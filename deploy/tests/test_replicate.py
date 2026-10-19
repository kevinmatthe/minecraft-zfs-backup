"""Optional sync task tests: no ZFS, source changes, or server required."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'replicate.py'
SPEC = importlib.util.spec_from_file_location('minecraft_optional_replicate', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def name(n): return f'mc-hourly-20260101T{n:06d}Z'


class MemoryZFS:
    def __init__(self, config):
        self.config = config
        self.data = {config.source_dataset: {}, config.target_dataset: {}}
        self.tags = {}
        self.events = []
        self.fail = False
        self.bad_guid = False
        self.after_hold = lambda: None
        self.busy = set()
    def snapshots(self, dataset, missing_ok=False): return list(self.data[dataset].items())
    def check_source(self): pass
    def check_target(self): pass
    def holds(self, dataset, snapshot): return set(self.tags.get((dataset, snapshot), set()))
    def hold(self, dataset, snapshot, tag):
        if snapshot not in self.data[dataset]: raise RuntimeError('snapshot no longer exists')
        self.tags.setdefault((dataset, snapshot), set()).add(tag)
        self.events.append(('hold', dataset, snapshot, tag))
        self.after_hold()
    def release(self, dataset, snapshot, tag):
        self.tags.setdefault((dataset, snapshot), set()).discard(tag)
        self.events.append(('release', dataset, snapshot, tag))
    def transfer(self, snapshot, base):
        self.events.append(('transfer', snapshot, base))
        if self.fail: raise RuntimeError('replica unavailable')
        self.data[self.config.target_dataset][snapshot] = 'wrong' if self.bad_guid else self.data[self.config.source_dataset][snapshot]
    def destroy_destination(self, snapshot):
        if snapshot in self.busy: return False
        self.events.append(('destroy', self.config.target_dataset, snapshot))
        del self.data[self.config.target_dataset][snapshot]
        return True


class SyncFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = MODULE.Config('tank/world', 'backup/world', Path(self.tmp.name), keep=2)
        self.zfs = MemoryZFS(self.config)
        self.task = MODULE.Synchronizer(self.config, self.zfs)
        self.source = self.zfs.data[self.config.source_dataset]
        self.target = self.zfs.data[self.config.target_dataset]
    def tearDown(self): self.tmp.cleanup()


class SyncTests(SyncFixture):
    def test_full_then_incremental_and_persistent_source_hold(self):
        self.source[name(1)] = '1'; first = self.task.sync()
        self.assertEqual(first['mode'], 'full')
        self.assertIn(self.task.tag, self.zfs.holds(self.config.source_dataset, name(1)))
        self.source[name(2)] = '2'; second = self.task.sync()
        self.assertEqual(second['base'], name(1))
        self.assertNotIn(self.task.tag, self.zfs.holds(self.config.source_dataset, name(1)))
        self.assertIn(self.task.tag, self.zfs.holds(self.config.source_dataset, name(2)))
        transfer = self.zfs.events.index(('transfer', name(2), name(1)))
        hold = self.zfs.events.index(('hold', self.config.source_dataset, name(2), self.task.tag))
        self.assertLess(hold, transfer)
    def test_failed_transfer_keeps_previous_base_hold_and_never_prunes(self):
        self.source[name(1)] = '1'; self.task.sync()
        self.source.update({name(n):str(n) for n in range(2,5)})
        self.zfs.fail = True
        with self.assertRaises(RuntimeError): self.task.sync()
        self.assertIn(self.task.tag, self.zfs.holds(self.config.source_dataset, name(1)))
        self.assertEqual(len(self.source),4)
        self.assertFalse(any(event[0]=='destroy' for event in self.zfs.events))
    def test_destination_latest_guid_mismatch_refuses_without_transfer(self):
        self.source[name(1)]='1';self.target[name(1)]='forged'
        with self.assertRaises(RuntimeError):self.task.sync()
        self.assertEqual(self.zfs.events,[])
    def test_foreign_latest_destination_without_source_match_refuses(self):
        self.source[name(1)]='1';self.target['foreign']='f'
        with self.assertRaises(RuntimeError):self.task.sync()
        self.assertEqual(self.zfs.events,[])
    def test_only_published_managed_source_snapshots_can_send(self):
        self.source.update({'foreign':'f','pending-'+name(1):'p'})
        with self.assertRaises(RuntimeError):self.task.sync()
        self.assertEqual(self.zfs.events,[])
    def test_receive_guid_mismatch_keeps_old_base_hold_and_disables_prune(self):
        self.source[name(1)]='1';self.task.sync()
        self.source.update({name(n):str(n) for n in range(2,5)})
        self.zfs.bad_guid=True
        with self.assertRaises(RuntimeError):self.task.sync()
        self.assertIn(self.task.tag,self.zfs.holds(self.config.source_dataset,name(1)))
        self.assertFalse(any(event[0]=='destroy' for event in self.zfs.events))
    def test_destination_retention_preserves_foreign_and_other_holds(self):
        self.source.update({name(n):str(n) for n in range(1,5)})
        self.target['foreign']='f';self.target.update({name(n):str(n) for n in range(1,4)})
        self.zfs.tags[(self.config.target_dataset,name(1))]={'external'}
        self.task.sync()
        self.assertEqual(set(self.target),{'foreign',name(1),name(3),name(4)})
        self.assertEqual(len(self.source),4)
    def test_busy_destination_snapshot_is_skipped(self):
        self.source.update({name(n):str(n) for n in range(1,5)})
        self.target.update({name(n):str(n) for n in range(1,4)})
        self.zfs.busy.add(name(1));self.task.sync()
        self.assertIn(name(1),self.target)
        self.assertNotIn(name(2),self.target)
    def test_repeated_run_is_idempotent_no_extra_transfer(self):
        self.source[name(1)]='1';self.task.sync();self.task.sync()
        self.assertEqual(len([e for e in self.zfs.events if e[0]=='transfer']),1)
    def test_source_guid_change_after_hold_prevents_transfer(self):
        self.source[name(1)]='1'
        self.zfs.after_hold=lambda:self.source.update({name(1):'changed'})
        with self.assertRaises(RuntimeError):self.task.sync()
        self.assertFalse(any(e[0]=='transfer' for e in self.zfs.events))
    def test_reserved_hold_tag_is_unique_per_dataset_pair(self):
        other=MODULE.Synchronizer(MODULE.Config('tank/world','other/world',Path(self.tmp.name)),self.zfs)
        self.assertNotEqual(self.task.tag,other.tag)
    def test_missing_incremental_base_fails_closed_after_source_prune(self):
        self.target[name(1)]='1';self.source[name(30)]='30'
        with self.assertRaises(RuntimeError):self.task.sync()
        self.assertEqual(self.zfs.events,[])


class ProcessTests(unittest.TestCase):
    def test_flock_excludes_overlapping_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            with MODULE.InstanceLock(Path(tmp)):
                with self.assertRaises(RuntimeError):
                    with MODULE.InstanceLock(Path(tmp)):pass
    def test_status_on_new_config_has_no_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=MODULE.Config('tank/world','backup/world',Path(tmp))
            self.assertIsNone(MODULE.status(config))


if __name__=='__main__':unittest.main()

class FailureAfterVerificationTests(SyncFixture):
    def test_destination_prune_failure_keeps_new_verified_base_hold(self):
        self.source.update({name(n):str(n) for n in range(1,5)})
        self.target.update({name(n):str(n) for n in range(1,4)})
        def fail(_):raise RuntimeError('retention unavailable')
        self.zfs.destroy_destination=fail
        with self.assertRaises(RuntimeError):self.task.sync()
        self.assertIn(self.task.tag,self.zfs.holds(self.config.source_dataset,name(4)))
    def test_hold_rotation_failure_keeps_new_verified_base_hold(self):
        self.source[name(1)]='1';self.task.sync()
        self.source[name(2)]='2'
        original=self.zfs.release
        def fail(dataset,snapshot,tag):
            if snapshot==name(1):raise RuntimeError('cannot release old hold')
            original(dataset,snapshot,tag)
        self.zfs.release=fail
        with self.assertRaises(RuntimeError):self.task.sync()
        self.assertIn(self.task.tag,self.zfs.holds(self.config.source_dataset,name(2)))


class BackendCommandTests(unittest.TestCase):
    def test_stream_is_compressed_nonforced_unmounted_readonly(self):
        from unittest.mock import MagicMock,patch
        with tempfile.TemporaryDirectory() as tmp:
            config=MODULE.Config('tank/world','backup/world',Path(tmp))
            backend=MODULE.ZFSBackend(config)
            sender=MagicMock();sender.returncode=0;sender.poll.return_value=0
            receiver=MagicMock();receiver.returncode=0;receiver.poll.return_value=0
            with patch.object(MODULE.subprocess,'Popen',side_effect=[sender,receiver]) as popen:
                backend.transfer(name(2),name(1))
            self.assertEqual(popen.call_args_list[0].args[0],['zfs','send','-c','-i','tank/world@'+name(1),'tank/world@'+name(2)])
            self.assertEqual(popen.call_args_list[1].args[0],['zfs','receive','-u','-o','mountpoint=none','-o','canmount=off','-o','readonly=on','backup/world'])
    def test_unsafe_existing_target_refuses_before_stream(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            backend=MODULE.ZFSBackend(MODULE.Config('tank/world','backup/world',Path(tmp)))
            with patch.object(backend,'exists',return_value=True),patch.object(backend,'run',return_value='mounted\tno\nreadonly\toff\ncanmount\toff\nmountpoint\tnone'):
                with self.assertRaises(RuntimeError):backend.check_target()
    def test_no_target_destroy_of_foreign_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend=MODULE.ZFSBackend(MODULE.Config('tank/world','backup/world',Path(tmp)))
            with self.assertRaises(ValueError):backend.destroy_destination('foreign')

class AmbiguousReceiveTests(SyncFixture):
    def test_receive_success_then_verification_failure_retains_both_source_holds(self):
        self.source[name(1)]='1';self.task.sync();self.source[name(2)]='2'
        calls=0
        def check():
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('transient property query failure after receive')
        self.zfs.check_target=check
        with self.assertRaises(RuntimeError):self.task.sync()
        self.assertEqual(self.target[name(2)],'2')
        self.assertIn(self.task.tag,self.zfs.holds(self.config.source_dataset,name(1)))
        self.assertIn(self.task.tag,self.zfs.holds(self.config.source_dataset,name(2)))

class RestartAndStatusTests(SyncFixture):
    def test_restart_reconciles_only_own_obsolete_holds(self):
        self.source.update({name(n):str(n) for n in range(1,4)})
        self.target.update({name(n):str(n) for n in range(1,3)})
        self.zfs.tags[(self.config.source_dataset,name(1))]={self.task.tag,'foreign-owner'}
        self.zfs.tags[(self.config.source_dataset,name(2))]={self.task.tag}
        restarted=MODULE.Synchronizer(self.config,self.zfs)
        restarted.sync()
        self.assertEqual(self.zfs.holds(self.config.source_dataset,name(1)),{'foreign-owner'})
        self.assertFalse(self.zfs.holds(self.config.source_dataset,name(2)))
        self.assertIn(restarted.tag,self.zfs.holds(self.config.source_dataset,name(3)))
    def test_failed_run_records_private_status_and_keeps_holds(self):
        self.source[name(1)]='1';self.zfs.fail=True
        with self.assertRaises(RuntimeError):MODULE.execute(self.config,self.zfs)
        status=MODULE.status(self.config)
        self.assertFalse(status['success'])
        self.assertIn('replica unavailable',status['error'])
        self.assertEqual((self.config.state_dir/'replication.json').stat().st_mode&0o777,0o600)
        self.assertIn(self.task.tag,self.zfs.holds(self.config.source_dataset,name(1)))
