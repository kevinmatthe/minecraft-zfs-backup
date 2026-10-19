"""Independent local snapshot families and restart-safe group publication."""
import threading
import time
import uuid
from test_agent import Fixture
from minecraft_zfs_agent.coordinator import CheckpointService
from minecraft_zfs_agent.protocol import APIError

class FamilyTests(Fixture):
    def periodic(self):self.body['event']='periodic_save';return self.freeze()
    def next(self,seconds=60):
        self.now+=seconds*1000
        self.body.update(id=str(uuid.uuid4()),created_ms=self.now,deadline_ms=self.now+15000)
    def test_periodic_creates_two_independent_snapshots_then_live_only(self):
        ready=self.periodic()
        self.assertTrue(ready['snapshot'].startswith('mc-live-'))
        self.assertEqual([s['family'] for s in ready['snapshots']],['live','hourly'])
        self.assertEqual(len(self.zfs.items),2)
        result=self.commit();self.assertEqual(result['state'],'COMMITTED')
        self.assertEqual(len(set(self.zfs.items.values())),2)
        self.next();ready=self.periodic();self.assertEqual(len(ready['snapshots']),1)
        self.commit();self.next(3600);self.assertEqual(len(self.periodic()['snapshots']),2)
    def test_cancelled_group_does_not_consume_hour(self):
        self.periodic();self.service.cancel(self.body['id'],{'cancelled_ms':self.now})
        self.assertEqual(self.zfs.items,{})
        self.next();self.assertEqual(len(self.periodic()['snapshots']),2)
    def test_group_commit_recovers_after_first_rename(self):
        ready=self.periodic();original=self.zfs.rename
        def crash(old,new):original(old,new);raise RuntimeError('power loss')
        self.zfs.rename=crash
        with self.assertRaises(RuntimeError):self.commit()
        self.zfs.rename=original;self.service.close();self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        result=self.commit();self.assertEqual(result['state'],'COMMITTED')
        self.assertEqual(set(self.zfs.items),{s['snapshot'] for s in ready['snapshots']})
        self.assertEqual(len([e for e in self.zfs.events if e[0]=='rename']),2)
    def test_all_guids_checked_before_any_publication(self):
        ready=self.periodic();self.zfs.items['pending-'+ready['snapshots'][1]['snapshot']]='forged'
        with self.assertRaises(APIError):self.commit()
        self.assertFalse(any(not n.startswith('pending-') for n in self.zfs.items))
    def test_second_snapshot_timeout_cleans_entire_group(self):
        count=0
        def elapsed():
            nonlocal count
            count+=1
            if count==2:self.now+=15001
        self.zfs.after_snapshot=elapsed
        with self.assertRaises(APIError):self.periodic()
        self.assertFalse(self.zfs.items)
    def test_hour_marker_survives_bounded_operation_history_and_restart(self):
        self.periodic();self.commit();self.service.ledger['operations']={};self.service.save();self.service.close()
        self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.next();self.assertEqual(len(self.periodic()['snapshots']),1)
    def test_listing_includes_both_families_and_manual_hourly(self):
        self.periodic();self.commit();self.next();self.body['event']='manual_save';ready=self.freeze();self.commit()
        self.assertTrue(ready['snapshot'].startswith('mc-hourly-'))
        entries=self.service.list_snapshots()['snapshots']
        self.assertEqual({e['family'] for e in entries},{'live','hourly'})
    def test_retention_runs_without_new_checkpoint_and_pauses_in_maintenance(self):
        self.zfs.events.clear();self.service.retention_tick()
        self.assertIn(('prune',),self.zfs.events)
        self.zfs.events.clear();self.service.enter_maintenance(str(uuid.uuid4()));self.service.retention_tick()
        self.assertNotIn(('prune',),self.zfs.events)
    def test_legacy_committed_periodic_single_snapshot_seeds_hour_deduplication(self):
        self.freeze();self.commit()
        op=self.service.ledger['operations'][self.body['id']]
        op['event']='periodic_save';op.pop('snapshots',None)
        self.service.ledger.pop('periodic_hour',None);self.service.save();self.service.close()
        self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.next();self.assertEqual(len(self.periodic()['snapshots']),1)
    def test_retention_timer_fires_when_no_game_operations_occur(self):
        fired=threading.Event();calls=[]
        def prune(protected=()):
            calls.append(1)
            if len(calls)>1:fired.set()
            return {'deleted':[],'held':[],'next_expiry':self.now/1000+.05}
        self.zfs.prune=prune;self.service.retention_tick()
        self.assertTrue(fired.wait(1),'deadline scheduler did not execute retention')
    def test_complete_group_intent_is_durable_before_first_snapshot(self):
        from minecraft_zfs_agent.protocol import read_json
        def inspect():
            record=read_json(self.service.path)['operations'][self.body['id']]
            self.assertEqual(record['state'],'SNAPSHOTTING')
            self.assertEqual(len(record['snapshots']),2)
        self.zfs.after_snapshot=inspect;self.periodic()
    def test_restart_during_second_snapshot_cleans_entire_unacknowledged_group(self):
        calls=[]
        def crash():
            calls.append(1)
            if len(calls)==2:raise RuntimeError('power loss before GUID persisted')
        self.zfs.after_snapshot=crash
        with self.assertRaises(RuntimeError):self.periodic()
        self.zfs.after_snapshot=lambda:None;self.service.close()
        self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.assertFalse(self.zfs.items)
        self.assertEqual(self.service.ledger['operations'][self.body['id']]['state'],'EXPIRED')
        self.next();self.assertEqual(len(self.periodic()['snapshots']),2)
    def test_new_hour_uses_capture_time_when_prepare_crosses_boundary(self):
        self.now=(self.now//3600000+1)*3600000-1000
        self.body.update(event='periodic_save',created_ms=self.now,deadline_ms=self.now+15000)
        self.prepare();self.now+=2000
        ready=self.service.snapshot(self.body['id'],{'frozen_ms':self.now});self.commit()
        self.assertEqual(ready['snapshot_hour'],self.now//3600000)
        self.next();self.assertEqual(len(self.periodic()['snapshots']),1)
    def test_transient_retention_status_write_failure_keeps_autonomous_retry(self):
        from unittest.mock import patch
        import minecraft_zfs_agent.coordinator as coordinator
        original=coordinator.atomic_json;calls=[];succeeded=threading.Event()
        def write(path,value,**kwargs):
            if path.name=='retention.json':
                calls.append(1)
                if len(calls)==1:raise OSError('transient state write failure')
                original(path,value,**kwargs);succeeded.set()
            else:original(path,value,**kwargs)
        with patch.object(coordinator,'RETENTION_RETRY_SECONDS',.05,create=True),patch.object(coordinator,'atomic_json',write),self.assertLogs(level='ERROR'):
            self.service.retention_tick()
            self.assertTrue(succeeded.wait(1),'TTL worker never retried after status write failure')
    def test_slow_commit_preflight_cannot_create_publish_intent_after_receipt_deadline(self):
        self.periodic()
        self.zfs.preflight=lambda:setattr(self,'now',self.now+75001)
        with self.assertRaises(APIError):self.commit()
        self.assertFalse(self.service.active_checkpoint.get('publish_intent'))
        self.assertFalse(any(not n.startswith('pending-') for n in self.zfs.items))
    def test_slow_guid_inventory_cannot_create_late_publish_intent(self):
        self.periodic();original=self.zfs.snapshots
        def inventory(*args,**kwargs):
            self.now+=75001;return original(*args,**kwargs)
        self.zfs.snapshots=inventory
        with self.assertRaises(APIError):self.commit()
        self.assertFalse(self.service.active_checkpoint.get('publish_intent'))
    def test_second_create_failure_can_cancel_without_publishing_or_consuming_hour(self):
        original=self.zfs.snapshot;calls=[]
        def fail(name):
            calls.append(name)
            if len(calls)==2:raise OSError('snapshot command failed')
            original(name)
        self.zfs.snapshot=fail
        with self.assertRaises(OSError):self.periodic()
        self.assertEqual(len(self.zfs.items),1)
        self.service.cancel(self.body['id'],{'cancelled_ms':self.now})
        self.assertFalse(self.zfs.items)
        self.zfs.snapshot=original;self.next();self.assertEqual(len(self.periodic()['snapshots']),2)
    def test_partial_publish_runtime_retry_completes_group_exactly_once(self):
        ready=self.periodic();original=self.zfs.rename;calls=[]
        def fail(old,new):
            calls.append(new)
            if len(calls)==2:raise OSError('second rename failed')
            original(old,new)
        self.zfs.rename=fail
        with self.assertRaises(OSError):self.commit()
        self.zfs.rename=original
        self.assertEqual(self.commit()['state'],'COMMITTED')
        self.assertEqual(set(self.zfs.items),{s['snapshot'] for s in ready['snapshots']})
        self.assertEqual(len([e for e in self.zfs.events if e[0]=='rename']),2)
    def test_capture_hour_is_barrier_start_even_if_creation_crosses_hour(self):
        self.now=(self.now//3600000+1)*3600000-1000
        self.body.update(event='periodic_save',created_ms=self.now,deadline_ms=self.now+15000)
        captured_hour=self.now//3600000
        self.zfs.after_snapshot=lambda:setattr(self,'now',self.now+1001)
        ready=self.periodic();self.commit()
        self.assertGreater(self.now//3600000,captured_hour)
        self.assertEqual(ready['snapshot_hour'],captured_hour)
