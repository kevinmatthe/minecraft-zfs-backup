import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from minecraft_zfs_agent.config import Config
from minecraft_zfs_agent.protocol import validate_request, read_json, atomic_json
from minecraft_zfs_agent.coordinator import Coordinator


class FakeZFS:
    def __init__(self):
        self.items = {}
        self.events = []
        self.after_snapshot = lambda: None
        self.ready = True
    def prune(self):
        self.events.append(('prune',))
        return {'deleted':[], 'held':[]}
    def preflight(self):
        if not self.ready:
            raise RuntimeError('wrong source mount')
    def snapshots(self, dataset, missing_ok=False):
        return list(self.items.get(dataset, {}).items())
    def snapshot(self, name):
        self.events.append(('snapshot', name))
        self.items.setdefault('tank/world', {})[name] = '123'
        self.after_snapshot()
    def rename(self, old, new):
        self.events.append(('rename', old, new))
        self.items['tank/world'][new] = self.items['tank/world'].pop(old)
    def destroy_pending(self, name):
        self.events.append(('destroy', name))
        self.items.setdefault('tank/world', {}).pop(name, None)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000000
        self.msg = dict(protocol=1, id=str(uuid.uuid4()), phase='FROZEN',
                        created_ms=self.now, deadline_ms=self.now + 15000)
    def test_valid(self):
        self.assertEqual(validate_request(self.msg, self.now), self.msg)
    def test_strict_invalid_messages(self):
        mutations = [{'protocol': True}, {'protocol': 2}, {'created_ms': 1.2},
                     {'id': self.msg['id'].upper()}, {'id': 'bad'}, {'phase':'OTHER'},
                     {'deadline_ms': self.now}, {'created_ms': self.now + 1001},
                     {'deadline_ms': self.now + 60001}, {'path': '/evil'},
                     {'created_ms': True}, {'committed_ms':self.now}]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_request(self.msg | mutation, self.now)
    def test_expired_frozen_and_forged_commit(self):
        for msg, now in [(self.msg, self.now+15000),
                         (self.msg | dict(phase='COMMIT', committed_ms=self.now-1), self.now),
                         (self.msg | dict(phase='COMMIT', committed_ms=self.now+1001), self.now),
                         (self.msg | dict(phase='COMMIT', committed_ms=self.now+15001), self.now+15001),
                         (self.msg | dict(phase='COMMIT', committed_ms=self.now), self.now+75001)]:
            with self.subTest(msg=msg, now=now), self.assertRaises(ValueError):
                validate_request(msg, now)
    def test_file_bounds_duplicates_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'message.json'
            for raw in [b' ' * 16385, b'{"protocol":1,"protocol":1}', b'[]', b'\xff']:
                path.write_bytes(raw)
                with self.assertRaises(ValueError): read_json(path)
            path.unlink(); path.symlink_to('/etc/passwd')
            with self.assertRaises((ValueError, OSError)): read_json(path)


class CoordinatorFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = Config('tank/world', root/'world', root/'mailbox', root/'state', min_interval_seconds=0)
        self.config.mailbox_path.mkdir()
        self.now = 1800000000000
        self.backend = FakeZFS()
        self.agent = Coordinator(self.config, self.backend, lambda: self.now)
        self.msg = dict(protocol=1, id=str(uuid.uuid4()), phase='FROZEN', created_ms=self.now, deadline_ms=self.now+15000)
    def tearDown(self): self.tmp.cleanup()
    def request(self, msg=None): atomic_json(self.config.mailbox_path/'request.json', msg or self.msg)
    def response(self): return read_json(self.config.mailbox_path/'response.json')
    def tick(self): self.agent.tick()
    def freeze(self): self.request(); self.tick(); return self.response()['snapshot']
    def commit(self): self.request(self.msg | dict(phase='COMMIT', committed_ms=self.now)); self.tick()


class CoordinatorTests(CoordinatorFixture):
    def test_only_matching_commit_publishes(self):
        name = self.freeze()
        self.assertEqual(self.response()['phase'], 'SNAPSHOT_READY')
        self.assertIn('pending-'+name, self.backend.items['tank/world'])
        self.request(self.msg | dict(id=str(uuid.uuid4()), phase='COMMIT', committed_ms=self.now)); self.tick()
        self.assertNotIn(name, self.backend.items['tank/world'])
        self.commit()
        self.assertEqual(self.response()['phase'], 'COMMITTED')
    def test_intent_is_durable_before_snapshot(self):
        self.backend.after_snapshot = lambda: self.assertIsNotNone(read_json(self.config.state_dir/'ledger.json')['pending'])
        self.freeze()
        self.assertEqual((self.config.state_dir/'ledger.json').stat().st_mode & 0o777, 0o600)
    def test_snapshot_rechecks_current_request(self):
        self.backend.after_snapshot = lambda: self.request(self.msg | dict(phase='CANCEL', cancelled_ms=self.now))
        self.request(); self.tick()
        self.assertFalse(self.backend.items['tank/world'])
    def test_snapshot_that_finishes_after_deadline_is_destroyed(self):
        self.backend.after_snapshot = lambda: setattr(self, 'now', self.now+15001)
        self.request(); self.tick()
        self.assertFalse(self.backend.items['tank/world'])
    def test_cancel_timeout_and_superseding_cleanup(self):
        for mode in ['cancel','timeout','supersede']:
            with self.subTest(mode=mode):
                self.msg['id'] = str(uuid.uuid4()); self.msg['created_ms']=self.now; self.msg['deadline_ms']=self.now+15000
                name=self.freeze()
                if mode=='cancel': self.request(self.msg | dict(phase='CANCEL', cancelled_ms=self.now))
                elif mode=='timeout': self.now += 75001
                else: self.request(self.msg | dict(id=str(uuid.uuid4()), created_ms=self.now+1000)); self.now += 1000
                self.tick()
                self.assertNotIn('pending-'+name, self.backend.items['tank/world'])
    def test_duplicate_commit_and_restart_are_idempotent(self):
        name=self.freeze(); self.commit(); self.commit()
        agent=Coordinator(self.config,self.backend,lambda:self.now)
        agent.tick()
        self.assertEqual(len([e for e in self.backend.events if e[0]=='rename']),1)
        self.assertEqual(self.response()['snapshot'],name)
    def test_restart_completes_commit_from_pending(self):
        name=self.freeze()
        self.request(self.msg | dict(phase='COMMIT', committed_ms=self.now))
        self.agent=Coordinator(self.config,self.backend,lambda:self.now)
        self.tick()
        self.assertIn(name,self.backend.items['tank/world'])
    def test_changed_window_cannot_commit(self):
        name=self.freeze()
        self.request(self.msg | dict(phase='COMMIT', deadline_ms=self.now+20000, committed_ms=self.now))
        self.tick()
        self.assertNotIn(name,self.backend.items['tank/world'])
    def test_guid_mismatch_prevents_publish(self):
        name=self.freeze(); self.backend.items['tank/world']['pending-'+name]='forged'
        with self.assertLogs(level='ERROR'): self.commit()
        self.assertNotIn(name,self.backend.items['tank/world'])
    def test_preflight_prevents_heartbeat_readiness_and_snapshot(self):
        self.backend.ready=False; self.request()
        with self.assertLogs(level='ERROR'): self.tick()
        self.assertFalse(read_json(self.config.mailbox_path/'heartbeat.json')['ready'])
        self.assertEqual(self.backend.events,[])
    def test_rate_limit_persists_after_restart(self):
        config = Config(self.config.source_dataset,self.config.world_path,self.config.mailbox_path,self.config.state_dir)
        self.agent=Coordinator(config,self.backend,lambda:self.now)
        self.freeze(); self.commit()
        self.now+=2000; self.msg.update(id=str(uuid.uuid4()),created_ms=self.now,deadline_ms=self.now+15000)
        self.agent=Coordinator(config,self.backend,lambda:self.now)
        self.request();self.tick()
        self.assertEqual(len([e for e in self.backend.events if e[0]=='snapshot']),1)

if __name__=='__main__': unittest.main()

class RecoveryTests(CoordinatorFixture):
    def test_crash_after_rename_before_completed_ledger_is_recovered(self):
        name=self.freeze()
        original=self.backend.rename
        def renamed_then_crash(old,new):
            original(old,new)
            raise RuntimeError('simulated crash after rename')
        self.backend.rename=renamed_then_crash
        with self.assertLogs(level='ERROR'):self.commit()
        self.assertIn(name,self.backend.items['tank/world'])
        self.backend.rename=original
        self.agent=Coordinator(self.config,self.backend,lambda:self.now)
        self.tick()
        self.assertEqual(self.response()['phase'],'COMMITTED')
        self.assertIsNone(read_json(self.config.state_dir/'ledger.json')['pending'])
    def test_restart_does_not_republish_foreign_guid_at_published_name(self):
        name=self.freeze()
        ledger=read_json(self.config.state_dir/'ledger.json')
        ledger['pending']['publish_intent']=dict(committed_ms=self.now,received_ms=self.now)
        atomic_json(self.config.state_dir/'ledger.json',ledger,mode=0o600)
        self.backend.items['tank/world'][name]='foreign'
        self.agent=Coordinator(self.config,self.backend,lambda:self.now)
        with self.assertLogs(level='ERROR'):self.tick()
        self.assertEqual(self.response()['phase'],'ERROR')
        self.assertEqual(self.backend.items['tank/world'][name],'foreign')
    def test_cancel_other_id_cannot_destroy_pending(self):
        name=self.freeze()
        self.request(self.msg|dict(id=str(uuid.uuid4()),phase='CANCEL',cancelled_ms=self.now))
        self.tick()
        self.assertIn('pending-'+name,self.backend.items['tank/world'])
    def test_commit_without_ledger_cannot_publish_existing_pending(self):
        name='pending-mc-hourly-20270115T080000Z'
        self.backend.items['tank/world']={name:'123'}
        self.commit()
        self.assertEqual(self.response()['phase'],'ERROR')
        self.assertEqual(self.backend.items['tank/world'],{})
    def test_successive_local_snapshots_are_independent(self):
        first=self.freeze();self.commit()
        self.now+=2000;self.msg.update(id=str(uuid.uuid4()),created_ms=self.now,deadline_ms=self.now+15000)
        second=self.freeze();self.commit()
        self.assertNotEqual(first,second)
        self.assertIn(first,self.backend.items['tank/world'])
        self.assertIn(second,self.backend.items['tank/world'])
    def test_timely_commit_received_after_deadline_can_publish(self):
        name=self.freeze()
        commit=self.msg|dict(phase='COMMIT',committed_ms=self.now+14000)
        self.now+=20000;self.request(commit);self.tick()
        self.assertIn(name,self.backend.items['tank/world'])
    def test_commit_received_beyond_grace_cannot_publish(self):
        name=self.freeze();commit=self.msg|dict(phase='COMMIT',committed_ms=self.now+14000)
        self.now+=75001;self.request(commit);self.tick()
        self.assertNotIn(name,self.backend.items['tank/world'])
    def test_repeated_frozen_does_not_extend_or_duplicate_snapshot(self):
        self.freeze();self.tick();self.tick()
        self.assertEqual(len([e for e in self.backend.events if e[0]=='snapshot']),1)
    def test_expired_duplicate_commit_does_not_repeat_publish(self):
        self.freeze();self.commit();self.now+=75001;self.commit()
        self.assertEqual(len([e for e in self.backend.events if e[0]=='rename']),1)
    def test_same_id_different_window_cannot_relabel_pending_snapshot(self):
        name=self.freeze();self.now+=1000
        self.msg.update(created_ms=self.now,deadline_ms=self.now+15000)
        self.request();self.tick()
        self.assertIn('pending-'+name,self.backend.items['tank/world'])
        self.assertEqual(len([e for e in self.backend.events if e[0]=='snapshot']),1)

class AdditionalSafetyTests(CoordinatorFixture):
    def test_restart_removes_orphaned_owned_pending_only(self):
        self.backend.items['tank/world']={'pending-mc-hourly-20260101T000000Z':'1','pending-foreign':'2','manual':'3'}
        self.tick()
        self.assertEqual(self.backend.items['tank/world'],{'pending-foreign':'2','manual':'3'})
    def test_replaced_pending_guid_is_not_destroyed_on_cancel(self):
        name=self.freeze();self.backend.items['tank/world']['pending-'+name]='replaced'
        self.request(self.msg|dict(phase='CANCEL',cancelled_ms=self.now))
        with self.assertLogs(level='ERROR'):self.tick()
        self.assertEqual(self.backend.items['tank/world']['pending-'+name],'replaced')

class InterleavingTests(CoordinatorFixture):
    def test_matching_commit_during_repeated_frozen_tick_is_preserved(self):
        name=self.freeze()
        original=self.backend.snapshots
        calls=0
        def interleaved(dataset,missing_ok=False):
            nonlocal calls
            calls+=1
            if calls==1:self.request(self.msg|dict(phase='COMMIT',committed_ms=self.now))
            return original(dataset,missing_ok)
        self.backend.snapshots=interleaved
        self.tick();self.tick()
        self.assertIn(name,self.backend.items['tank/world'])
        self.assertEqual(self.response()['phase'],'COMMITTED')
    def test_acknowledged_snapshot_survives_deadline_tick_for_late_commit(self):
        name=self.freeze()
        committed=self.now+14000
        self.now+=16000;self.tick()
        self.request(self.msg|dict(phase='COMMIT',committed_ms=committed));self.tick()
        self.assertIn(name,self.backend.items['tank/world'])
    def test_unrelated_cancel_during_repeated_frozen_cannot_destroy_pending(self):
        name=self.freeze();original=self.backend.snapshots
        def interleaved(dataset,missing_ok=False):
            self.request(self.msg|dict(id=str(uuid.uuid4()),phase='CANCEL',cancelled_ms=self.now))
            return original(dataset,missing_ok)
        self.backend.snapshots=interleaved
        self.tick()
        self.assertIn('pending-'+name,self.backend.items['tank/world'])

class ResponseFailureTests(CoordinatorFixture):
    def test_commit_response_failure_still_runs_local_retention(self):
        from unittest.mock import patch
        name=self.freeze()
        original=self.agent.respond
        def failed(*args,**kwargs):raise OSError('mailbox temporarily unavailable')
        with patch.object(self.agent,'respond',side_effect=failed),self.assertLogs(level='ERROR'):
            self.commit()
        self.assertTrue(read_json(self.config.state_dir/'retention.json')['success'])
        self.tick()
        self.assertEqual(self.response()['phase'],'COMMITTED')
