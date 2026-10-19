from pathlib import Path
import tempfile
import unittest
from minecraft_zfs_agent.config import Config
from minecraft_zfs_agent.zfs import ZFS, ZFSError


def name(n): return 'mc-hourly-20260101T%06dZ' % n


class MemoryZFS(ZFS):
    def __init__(self,config):
        super().__init__(config)
        self.data={};self.held=set();self.busy=set();self.calls=[]
    def snapshots(self,dataset=None,missing_ok=False):return list(self.data.items())
    def run(self,*args,**kwargs):
        self.calls.append(args)
        snapshot=args[-1].split('@')[-1]
        if args[0]=='holds':return 'replication\ttimestamp' if snapshot in self.held else ''
        if args[0]=='destroy':
            if snapshot in self.busy:raise ZFSError(args,'cannot destroy snapshot: dataset is busy')
            del self.data[snapshot]
        return ''


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.config=Config('tank/world',Path('/world'),Path('/mail'),Path('/state'),keep=2)
        self.z=MemoryZFS(self.config)
    def test_prune_only_old_owned_published_snapshots(self):
        self.z.data.update({name(n):str(n) for n in range(1,5)})
        self.z.data.update({'foreign':'f','pending-'+name(0):'p'})
        result=self.z.prune()
        self.assertEqual(set(self.z.data),{name(3),name(4),'foreign','pending-'+name(0)})
        self.assertEqual(result['deleted'],[name(1),name(2)])
        self.assertFalse(any('-r' in call or '-R' in call or '-f' in call for call in self.z.calls))
    def test_external_replication_holds_are_preserved(self):
        self.z.data.update({name(n):str(n) for n in range(1,5)})
        self.z.held.add(name(1))
        result=self.z.prune()
        self.assertIn(name(1),self.z.data)
        self.assertEqual(result['held'],[name(1)])
        self.assertNotIn(('destroy','tank/world@'+name(1)),self.z.calls)
    def test_hold_race_or_busy_snapshot_is_skipped_without_force(self):
        self.z.data.update({name(n):str(n) for n in range(1,5)})
        self.z.busy.add(name(1))
        result=self.z.prune()
        self.assertIn(name(1),self.z.data)
        self.assertNotIn(name(2),self.z.data)
        self.assertEqual(result['held'],[name(1)])
    def test_default_keep_is_24(self):
        self.z=MemoryZFS(Config('tank/world',Path('/world'),Path('/mail'),Path('/state')))
        self.z.data.update({name(n):str(n) for n in range(1,30)})
        self.z.prune()
        self.assertEqual(len(self.z.data),24)
        self.assertEqual(list(self.z.data)[0],name(6))
    def test_pending_destroy_is_strictly_scoped(self):
        for invalid in ['manual','pending-other','pending-mc-hourly-20260101T000000Z/child','-r']:
            with self.assertRaises(ValueError):self.z.destroy_pending(invalid)
        self.assertEqual(self.z.calls,[])


class BackendPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name);(root/'level.dat').touch()
        self.config=Config('tank/world',root,root/'mail',root/'state')
        self.z=ZFS(self.config)
    def tearDown(self):self.tmp.cleanup()
    def test_preflight_checks_exact_mount_and_world_marker(self):
        from unittest.mock import patch
        import json
        import subprocess
        mount={'filesystems':[dict(source='tank/world',target=str(self.config.world_path),fstype='zfs')]}
        with patch('os.path.ismount',return_value=True),patch.object(self.z,'run',return_value='yes'),patch('subprocess.run',side_effect=lambda *a,**kw:subprocess.CompletedProcess([],0,json.dumps(mount))):
            self.z.preflight()
            mount['filesystems'][0]['target']='/ancestor'
            with self.assertRaises(RuntimeError):self.z.preflight()
    def test_stopped_backend_does_not_start_another_zfs_process(self):
        from unittest.mock import patch
        self.z.stop.set()
        with patch('subprocess.run') as run:
            with self.assertRaises(InterruptedError):self.z.run('list')
            run.assert_not_called()
