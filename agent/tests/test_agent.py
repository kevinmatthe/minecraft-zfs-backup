import tempfile
from pathlib import Path
import unittest
import uuid
from minecraft_zfs_agent.config import Config
from minecraft_zfs_agent.coordinator import CheckpointService
from minecraft_zfs_agent.protocol import APIError, read_json


class FakeZFS:
    def __init__(self):
        self.items={};self.tags={};self.events=[];self.after_snapshot=lambda:None;self.ready=True
    def preflight(self):
        if not self.ready:raise RuntimeError('wrong mount')
    def snapshots(self,dataset=None):return list(self.items.items())
    def snapshot(self,name):
        self.events.append(('snapshot',name));self.items[name]='guid-'+str(len(self.events));self.after_snapshot()
    def rename(self,old,new):self.events.append(('rename',old,new));self.items[new]=self.items.pop(old)
    def destroy_pending(self,name):self.events.append(('destroy',name));self.items.pop(name,None)
    def guid(self,name):return self.items[name]
    def hold(self,name,tag):self.tags.setdefault(name,set()).add(tag)
    def release(self,name,tag):self.tags.setdefault(name,set()).discard(tag)
    def prune(self):self.events.append(('prune',));return {'deleted':[],'held':[]}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.config=Config('tank/world',root/'world',root/'state',root/'token',min_interval_seconds=0)
        self.config.world_path.mkdir();(self.config.world_path/'session.lock').write_bytes(b'lock')
        self.now=1800000000000;self.zfs=FakeZFS();self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.body=dict(id=str(uuid.uuid4()),event='manual_save',created_ms=self.now,deadline_ms=self.now+15000)
    def tearDown(self):self.service.close();self.tmp.cleanup()
    def prepare(self):return self.service.prepare(self.body)
    def freeze(self):self.prepare();return self.service.snapshot(self.body['id'],{'frozen_ms':self.now})
    def commit(self):return self.service.commit(self.body['id'],{'committed_ms':self.now})


class CheckpointTests(Fixture):
    def test_prepare_does_not_snapshot(self):
        self.assertEqual(self.prepare()['state'],'PREPARED');self.assertEqual(self.zfs.items,{})
    def test_only_commit_publishes_matching_recorded_snapshot(self):
        ready=self.freeze();name=ready['snapshot'];guid=self.zfs.items['pending-'+name]
        self.assertEqual(ready['state'],'SNAPSHOT_READY');self.assertNotIn(name,self.zfs.items)
        result=self.commit();self.assertEqual(result['state'],'COMMITTED');self.assertEqual(self.zfs.items[name],guid)
    def test_intent_exists_before_snapshot(self):
        def check():
            ledger=read_json(self.config.state_dir/'ledger.json')
            self.assertEqual(ledger['operations'][self.body['id']]['state'],'SNAPSHOTTING')
        self.zfs.after_snapshot=check;self.freeze()
    def test_snapshot_completed_after_deadline_cannot_publish(self):
        self.zfs.after_snapshot=lambda:setattr(self,'now',self.now+15001)
        with self.assertRaises(APIError) as error:self.freeze()
        self.assertEqual(error.exception.status,410);self.assertEqual(self.zfs.items,{})
    def test_other_id_cannot_commit_or_cancel_pending(self):
        ready=self.freeze();other=str(uuid.uuid4())
        for method,key in [(self.service.commit,'committed_ms'),(self.service.cancel,'cancelled_ms')]:
            with self.assertRaises(APIError):method(other,{key:self.now})
        self.assertIn('pending-'+ready['snapshot'],self.zfs.items)
    def test_invalid_modified_prepare_or_phase_identity_is_conflict(self):
        self.freeze()
        with self.assertRaises(APIError):self.service.prepare(self.body|dict(event='periodic_save'))
        with self.assertRaises(APIError):self.service.snapshot(self.body['id'],{'frozen_ms':self.now+1})
    def test_commit_without_snapshot_fails(self):
        self.prepare()
        with self.assertRaises(APIError):self.commit()
        self.assertEqual(self.zfs.items,{})
    def test_late_received_timely_commit_survives_expiry_timer(self):
        self.freeze();committed=self.now+14000;self.now+=16000;self.service.expire()
        result=self.service.commit(self.body['id'],{'committed_ms':committed})
        self.assertEqual(result['state'],'COMMITTED')
    def test_commit_beyond_grace_or_future_or_forged_is_rejected(self):
        self.freeze()
        for value in [self.now-1,self.now+1001,self.now+15001,True]:
            with self.assertRaises(APIError):self.service.commit(self.body['id'],{'committed_ms':value})
        self.now+=75001
        with self.assertRaises(APIError):self.service.commit(self.body['id'],{'committed_ms':self.body['created_ms']})
        self.assertFalse(self.zfs.items)
    def test_cancel_removes_only_pending_and_committed_never_deleted(self):
        ready=self.freeze();self.service.cancel(self.body['id'],{'cancelled_ms':self.now})
        self.assertFalse(self.zfs.items)
        self.now+=2000;self.body.update(id=str(uuid.uuid4()),created_ms=self.now,deadline_ms=self.now+15000)
        ready=self.freeze();self.commit();self.service.cancel(self.body['id'],{'cancelled_ms':self.now})
        self.assertIn(ready['snapshot'],self.zfs.items)
    def test_duplicate_commit_restart_and_crash_after_rename(self):
        ready=self.freeze();original=self.zfs.rename
        def crash(old,new):original(old,new);raise RuntimeError('simulated crash')
        self.zfs.rename=crash
        with self.assertRaises(RuntimeError):self.commit()
        self.zfs.rename=original;self.service.close()
        self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.assertEqual(self.commit()['state'],'COMMITTED')
        self.assertEqual(len([e for e in self.zfs.events if e[0]=='rename']),1)
        self.assertIn(ready['snapshot'],self.zfs.items)
    def test_replaced_guid_cannot_publish_or_destroy(self):
        ready=self.freeze();self.zfs.items['pending-'+ready['snapshot']]='replaced'
        with self.assertRaises(APIError):self.commit()
        with self.assertRaises(APIError):self.service.cancel(self.body['id'],{'cancelled_ms':self.now})
        self.assertIn('pending-'+ready['snapshot'],self.zfs.items)
    def test_preflight_unavailable_or_concurrent_checkpoint_rejected(self):
        self.zfs.ready=False
        with self.assertRaises(APIError) as err:self.prepare()
        self.assertEqual(err.exception.status,503)
        self.zfs.ready=True;self.prepare()
        with self.assertRaises(APIError) as err:self.service.prepare(self.body|dict(id=str(uuid.uuid4())))
        self.assertEqual(err.exception.status,409)
    def test_prepare_validation_strict_fields_integer_time_and_uuid(self):
        for change in [{'event':'unknown'},{'path':'evil'},{'created_ms':True},{'id':'bad'},{'deadline_ms':self.now+60001},{'created_ms':self.now+1001}]:
            with self.subTest(change=change),self.assertRaises(APIError):self.service.prepare(self.body|change)
    def test_maintenance_gate_blocks_checkpoints_and_recovery_is_idempotent(self):
        job=str(uuid.uuid4());self.service.enter_maintenance(job)
        with self.assertRaises(APIError):self.prepare()
        first=self.service.record_recovery('pre_rollback',job);second=self.service.record_recovery('pre_rollback',job)
        self.assertEqual(first,second)
        self.assertIn('minecraft-zfs-rollback-'+job,self.zfs.tags[first['snapshot']])
        self.service.leave_maintenance(job);self.assertEqual(self.prepare()['state'],'PREPARED')
    def test_active_checkpoint_blocks_maintenance(self):
        self.prepare()
        with self.assertRaises(APIError):self.service.enter_maintenance(str(uuid.uuid4()))
    def test_lifecycle_dedup_shutdown_and_maintenance_skip(self):
        event=dict(id=str(uuid.uuid4()),event='server_started',created_ms=self.now,backup_requested=False)
        self.assertEqual(self.service.lifecycle(event)['state'],'RECORDED');self.assertFalse(self.zfs.items)
        stop=event|dict(id=str(uuid.uuid4()),event='server_stopped',backup_requested=True)
        result=self.service.lifecycle(stop);self.assertEqual(result['state'],'COMMITTED')
        self.assertEqual(self.service.lifecycle(stop),result)
        self.service.enter_maintenance(str(uuid.uuid4()));self.now+=2000
        skipped=self.service.lifecycle(stop|dict(id=str(uuid.uuid4()),created_ms=self.now))
        self.assertEqual(skipped['state'],'SKIPPED')
    def test_status_and_list_include_event_and_exclude_pending(self):
        ready=self.freeze();self.assertEqual(self.service.list_snapshots()['snapshots'],[])
        self.commit();listed=self.service.list_snapshots()['snapshots']
        self.assertEqual(listed[0]['name'],ready['snapshot']);self.assertEqual(listed[0]['event'],'manual_save')
        self.assertTrue(self.service.status()['ready'])

if __name__=='__main__':unittest.main()

class ExtraSafetyTests(Fixture):
    def test_same_process_commit_retry_recovers_publish_intent(self):
        self.freeze();original=self.zfs.rename
        def crash(old,new):original(old,new);raise RuntimeError('ambiguous rename')
        self.zfs.rename=crash
        with self.assertRaises(RuntimeError):self.commit()
        self.zfs.rename=original
        self.assertEqual(self.commit()['state'],'COMMITTED')
    def test_shutdown_snapshot_skips_world_locked_by_another_process(self):
        import subprocess,sys
        child=subprocess.Popen([sys.executable,'-c',"import fcntl,sys,time; f=open(sys.argv[1],'r+b'); fcntl.lockf(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(30)",str(self.config.world_path/'session.lock')],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'ready')
            result=self.service.lifecycle(dict(id=str(uuid.uuid4()),event='server_stopped',created_ms=self.now,backup_requested=True))
            self.assertEqual(result['state'],'SKIPPED');self.assertFalse(self.zfs.items)
        finally:child.terminate();child.wait();child.stdout.close()

class PrepareBudgetTests(Fixture):
    def test_slow_preflight_does_not_prepare_after_freeze_deadline(self):
        def slow():self.now+=15001
        self.zfs.preflight=slow
        with self.assertRaises(APIError) as error:self.prepare()
        self.assertEqual(error.exception.status,410)
        self.assertIsNone(self.service.active_checkpoint)

class ReconcileIsolationTests(Fixture):
    def test_lifecycle_publish_reconciliation_preserves_new_ready_checkpoint(self):
        original=self.zfs.rename
        self.zfs.rename=lambda old,new:(_ for _ in ()).throw(RuntimeError('rename unavailable'))
        with self.assertRaises(RuntimeError):self.service.lifecycle(dict(id=str(uuid.uuid4()),event='server_stopped',created_ms=self.now,backup_requested=True))
        self.zfs.rename=original
        ready=self.freeze();self.service.close()
        self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.assertIsNotNone(self.service.active_checkpoint)
        self.assertEqual(self.service.active_checkpoint['id'],self.body['id'])
        self.assertIn('pending-'+ready['snapshot'],self.zfs.items)
        self.assertEqual(self.commit()['state'],'COMMITTED')

class SameIDReconcileTests(Fixture):
    def test_lifecycle_publish_does_not_clear_checkpoint_even_when_ids_match(self):
        original=self.zfs.rename
        self.zfs.rename=lambda old,new:(_ for _ in ()).throw(RuntimeError('rename unavailable'))
        with self.assertRaises(RuntimeError):self.service.lifecycle(dict(id=self.body['id'],event='server_stopped',created_ms=self.now,backup_requested=True))
        self.zfs.rename=original
        ready=self.freeze();self.service.close();self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.assertIsNotNone(self.service.active_checkpoint)
        self.assertIn('pending-'+ready['snapshot'],self.zfs.items)
        self.assertEqual(self.commit()['state'],'COMMITTED')
