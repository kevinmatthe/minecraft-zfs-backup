"""Independent save triggers and restart compatibility with old version2 bundles."""
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
    def legacy_bundle(self,state='SNAPSHOT_READY',created_members=2,recorded_guids=None):
        """Load the durable shape left by the former two-member implementation."""
        self.body['event']='periodic_save'
        members=[dict(snapshot=self.service._name(self.now,family),family=family)
                 for family in ('live','hourly')]
        recorded_guids=created_members if recorded_guids is None else recorded_guids
        for index,member in enumerate(members[:created_members]):
            pending='pending-'+member['snapshot'];self.zfs.snapshot(pending)
            if index<recorded_guids:member['guid']=self.zfs.guid(pending)
        op=self.body|dict(state=state,snapshot=members[0]['snapshot'],snapshots=members,
                          frozen_ms=self.now,snapshot_hour=self.now//3600000,updated_ms=self.now)
        if members[0].get('guid'):op['guid']=members[0]['guid']
        self.service.ledger['operations'][op['id']]=op
        self.service.ledger['active']=op['id'];self.service.save()
        return self.service._public(op)
    def test_periodic_always_creates_only_live_and_hourly_trigger_is_separate(self):
        for delay in (0,60,3600):
            self.next(delay);ready=self.periodic()
            self.assertEqual([m['family'] for m in ready['snapshots']],['live'])
            self.commit()
        self.next();self.body['event']='hourly_save';ready=self.freeze();self.commit()
        self.assertEqual([m['family'] for m in ready['snapshots']],['hourly'])

    def test_legacy_cancelled_group_cleans_both_without_affecting_new_allocation(self):
        self.legacy_bundle();self.service.cancel(self.body['id'],{'cancelled_ms':self.now})
        self.assertEqual(self.zfs.items,{})
        self.next();self.assertEqual([m['family'] for m in self.periodic()['snapshots']],['live'])
        self.commit();self.next();self.body['event']='hourly_save'
        self.assertEqual([m['family'] for m in self.freeze()['snapshots']],['hourly'])

    def test_group_commit_recovers_after_first_rename(self):
        ready=self.legacy_bundle();original=self.zfs.rename
        def crash(old,new):original(old,new);raise RuntimeError('power loss')
        self.zfs.rename=crash
        with self.assertRaises(RuntimeError):self.commit()
        self.zfs.rename=original;self.service.close();self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        result=self.commit();self.assertEqual(result['state'],'COMMITTED')
        self.assertEqual(set(self.zfs.items),{s['snapshot'] for s in ready['snapshots']})
        self.assertEqual(len([e for e in self.zfs.events if e[0]=='rename']),2)
    def test_all_guids_checked_before_any_publication(self):
        ready=self.legacy_bundle();self.zfs.items['pending-'+ready['snapshots'][1]['snapshot']]='forged'
        with self.assertRaises(APIError):self.commit()
        self.assertFalse(any(not n.startswith('pending-') for n in self.zfs.items))
    def test_legacy_second_snapshot_timeout_cleans_entire_group(self):
        self.legacy_bundle(state='SNAPSHOTTING')
        self.now=self.body['deadline_ms']+1;self.service.expire()
        self.assertFalse(self.zfs.items)
        self.assertEqual(self.service.ledger['operations'][self.body['id']]['state'],'EXPIRED')

    def test_historic_hour_marker_cannot_change_new_event_mapping_after_restart(self):
        self.periodic();self.commit();self.service.ledger['operations']={}
        self.service.ledger['periodic_hour']=self.now//3600000+100
        self.service.save();self.service.close()
        self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.next();self.assertEqual([m['family'] for m in self.periodic()['snapshots']],['live'])
        self.commit();self.next();self.body['event']='hourly_save'
        self.assertEqual([m['family'] for m in self.freeze()['snapshots']],['hourly'])

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
    def test_legacy_committed_single_snapshot_preserves_historic_marker_only(self):
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
    def test_single_member_intent_is_durable_before_first_snapshot_for_both_events(self):
        from minecraft_zfs_agent.protocol import read_json
        def inspect():
            record=read_json(self.service.path)['operations'][self.body['id']]
            self.assertEqual(record['state'],'SNAPSHOTTING')
            self.assertEqual(len(record['snapshots']),1)
            expected='live' if self.body['event']=='periodic_save' else 'hourly'
            self.assertEqual(record['snapshots'][0]['family'],expected)
        self.zfs.after_snapshot=inspect;self.periodic();self.commit()
        self.next();self.body['event']='hourly_save';self.freeze()

    def test_legacy_restart_after_second_create_before_guid_persisted_cleans_group(self):
        self.legacy_bundle(state='SNAPSHOTTING',recorded_guids=1)
        self.service.close()
        self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.assertFalse(self.zfs.items)
        self.assertEqual(self.service.ledger['operations'][self.body['id']]['state'],'EXPIRED')
        self.next();self.assertEqual(len(self.periodic()['snapshots']),1)

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
    def test_legacy_failed_second_create_can_cancel_complete_manifest(self):
        self.legacy_bundle(state='SNAPSHOTTING',created_members=1)
        self.assertEqual(len(self.zfs.items),1)
        self.service.cancel(self.body['id'],{'cancelled_ms':self.now})
        self.assertFalse(self.zfs.items)
        self.next();self.assertEqual(len(self.periodic()['snapshots']),1)

    def test_partial_publish_runtime_retry_completes_group_exactly_once(self):
        ready=self.legacy_bundle();original=self.zfs.rename;calls=[]
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
    def test_guid_change_between_group_renames_cannot_publish_replacement(self):
        ready=self.legacy_bundle();second=ready['snapshots'][1]['snapshot'];original=self.zfs.rename
        def changed(old,new):
            original(old,new)
            self.zfs.items['pending-'+second]='replacement'
        self.zfs.rename=changed
        with self.assertRaises(APIError):self.commit()
        self.assertNotIn(second,self.zfs.items)
        self.assertEqual(self.zfs.items['pending-'+second],'replacement')

    def test_legacy_manifest_without_created_members_expires_without_creating_any(self):
        self.legacy_bundle(state='SNAPSHOTTING',created_members=0)
        self.zfs.events.clear();self.service.close()
        self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.assertFalse(self.zfs.items)
        self.assertFalse(any(event[0]=='snapshot' for event in self.zfs.events))
        self.assertEqual(self.service.ledger['operations'][self.body['id']]['state'],'EXPIRED')
    def test_legacy_partial_create_restart_cleans_first_member_and_missing_second(self):
        self.legacy_bundle(state='SNAPSHOTTING',created_members=1)
        self.service.close();self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.assertFalse(self.zfs.items)
        self.assertEqual(self.service.ledger['operations'][self.body['id']]['state'],'EXPIRED')
    def test_legacy_second_guid_conflict_cannot_delete_first_member_on_cancel(self):
        ready=self.legacy_bundle();second='pending-'+ready['snapshots'][1]['snapshot']
        self.zfs.items[second]='foreign-replacement'
        with self.assertRaises(APIError):self.service.cancel(self.body['id'],{'cancelled_ms':self.now})
        self.assertEqual(len(self.zfs.items),2)
        self.assertEqual(self.zfs.items[second],'foreign-replacement')

class IndependentHourlyTests(Fixture):
    def setUp(self):
        super().setUp()
        from dataclasses import replace
        self.service.close();self.config=replace(self.config,min_interval_seconds=60)
        self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
    def request(self,event,seconds=0):
        self.now+=seconds*1000
        self.body=dict(id=str(uuid.uuid4()),event=event,created_ms=self.now,deadline_ms=self.now+15000)
        return self.freeze()
    def test_every_periodic_save_has_exactly_one_live_in_every_hour(self):
        for event_time in (0,3600,3600):
            ready=self.request('periodic_save',event_time)
            self.assertEqual([m['family'] for m in ready['snapshots']],['live'])
            self.assertEqual(len(ready['snapshots']),1)
            self.commit()
        self.assertTrue(all(name.startswith('mc-live-') for name in self.zfs.items))
    def test_hourly_save_without_any_autosave_creates_one_hourly(self):
        ready=self.request('hourly_save')
        self.assertEqual([m['family'] for m in ready['snapshots']],['hourly'])
        self.commit()
        self.assertEqual({row['event'] for row in self.service.list_snapshots()['snapshots']},{'hourly_save'})
    def test_live_then_hourly_and_hourly_then_manual_have_independent_rate_limits(self):
        self.request('periodic_save');self.commit()
        self.request('hourly_save',1);self.commit()
        with self.assertRaises(APIError) as error:self.request('manual_save',1)
        self.assertEqual(error.exception.code,'rate_limited')
        self.request('manual_save',58);self.commit()
        self.request('hourly_save',1);self.commit()
    def test_hourly_does_not_rate_limit_first_live_or_manual(self):
        for event in ('periodic_save','manual_save'):
            with self.subTest(event=event):
                self.request('hourly_save',60);self.commit()
                self.request(event,1);self.commit()
    def test_repeated_hourly_is_rate_limited_across_restart(self):
        self.request('hourly_save');self.commit()
        accepted=self.now
        self.service.close();self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        self.assertEqual(self.service.ledger['last_hourly_accepted_ms'],accepted)
        with self.assertRaises(APIError) as error:self.request('hourly_save',1)
        self.assertEqual(error.exception.code,'rate_limited')
        self.request('hourly_save',59);self.commit()
    def test_scheduled_hourly_and_normal_checkpoints_share_global_exclusion(self):
        for first,second in (('periodic_save','hourly_save'),('hourly_save','periodic_save'),('manual_save','hourly_save')):
            with self.subTest(first=first,second=second):
                self.request(first,60)
                active=self.body.copy()
                with self.assertRaises(APIError) as error:self.request(second,1)
                self.assertEqual(error.exception.code,'busy')
                self.service.cancel(active['id'],{'cancelled_ms':self.now})
        owner=str(uuid.uuid4());self.service.enter_maintenance(owner)
        with self.assertRaises(APIError) as error:self.request('hourly_save',60)
        self.assertEqual(error.exception.code,'busy')
        self.service.leave_maintenance(owner)
    def test_legacy_normal_rate_limit_remains_and_old_ledger_allows_first_hourly(self):
        self.request('periodic_save');self.commit()
        self.service.ledger.pop('last_hourly_accepted_ms',None)
        self.service.save();self.service.close()
        self.service=CheckpointService(self.config,self.zfs,lambda:self.now)
        with self.assertRaises(APIError) as error:self.request('manual_save',1)
        self.assertEqual(error.exception.code,'rate_limited')
        self.request('hourly_save');self.commit()

    def test_rejected_hourly_does_not_consume_acceptance_and_identity_is_fixed(self):
        self.zfs.ready=False
        with self.assertRaises(APIError) as error:self.request('hourly_save')
        self.assertEqual(error.exception.code,'source_unavailable')
        self.assertIsNone(self.service.ledger.get('last_hourly_accepted_ms'))
        self.zfs.ready=True;ready=self.request('hourly_save')
        accepted=self.service.ledger['last_hourly_accepted_ms']
        self.assertEqual(self.service.prepare(self.body)['snapshot'],ready['snapshot'])
        with self.assertRaises(APIError) as error:self.service.prepare(self.body|dict(event='periodic_save'))
        self.assertEqual(error.exception.code,'identity_conflict')
        self.assertEqual(self.service.ledger['last_hourly_accepted_ms'],accepted)
        self.assertIsNone(self.service.ledger['last_accepted_ms'])
