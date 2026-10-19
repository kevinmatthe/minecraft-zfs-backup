from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from minecraft_zfs_agent.config import Config
from minecraft_zfs_agent.zfs import ZFS, ZFSError


def name(n): return 'mc-hourly-20260101T%06dZ' % n


class MemoryZFS(ZFS):
    def __init__(self,config):
        super().__init__(config)
        self.data={};self.held=set();self.busy=set();self.calls=[];self.properties={};self.created=1000
    def snapshots(self,dataset=None,missing_ok=False):return list(self.data.items())
    def run(self,*args,**kwargs):
        self.calls.append(args)
        snapshot=args[-1].split('@')[-1]
        if args[0]=='get':
            props=args[-2].split(',')
            return '\t'.join(str(self.created) if prop=='creation' else self.properties.get(snapshot,{}).get(prop,'-') for prop in props)
        if args[0]=='set':
            self.properties.setdefault(snapshot,{}).update(item.split('=',1) for item in args[1:-1])
        if args[0]=='holds':return 'replication\ttimestamp' if snapshot in self.held else ''
        if args[0]=='destroy':
            if snapshot in self.busy:raise ZFSError(args,'cannot destroy snapshot: dataset is busy')
            del self.data[snapshot]
        return ''


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.config=Config('tank/world',Path('/world'),Path('/state'),Path('/token'),keep=2)
        self.z=MemoryZFS(self.config)
    def test_prune_only_old_owned_published_snapshots(self):
        self.z.data.update({name(n):str(n) for n in range(1,5)})
        self.z.data.update({'foreign':'f','pending-'+name(0):'p'})
        result=self.z.prune(now=90000)
        self.assertEqual(set(self.z.data),{'foreign','pending-'+name(0)})
        self.assertEqual(result['deleted'],[name(1),name(2),name(3),name(4)])
        self.assertFalse(any('-r' in call or '-R' in call or '-f' in call for call in self.z.calls))
    def test_external_replication_holds_are_preserved(self):
        self.z.data.update({name(n):str(n) for n in range(1,5)})
        self.z.held.add(name(1))
        result=self.z.prune(now=90000)
        self.assertIn(name(1),self.z.data)
        self.assertEqual(result['held'],[name(1)])
        self.assertNotIn(('destroy','tank/world@'+name(1)),self.z.calls)
    def test_hold_race_or_busy_snapshot_is_skipped_without_force(self):
        self.z.data.update({name(n):str(n) for n in range(1,5)})
        self.z.busy.add(name(1))
        result=self.z.prune(now=90000)
        self.assertIn(name(1),self.z.data)
        self.assertNotIn(name(2),self.z.data)
        self.assertEqual(result['held'],[name(1)])
    def test_independent_ttls_legacy_creation_and_keep_is_ignored(self):
        live='mc-live-20260101T000000Z'
        self.z.data={live:'l',name(1):'h','foreign':'f'}
        self.z.prune(now=4600)
        self.assertNotIn(live,self.z.data)
        self.assertIn(name(1),self.z.data)
        self.assertEqual(self.z.properties[name(1)]['org.minecraft-zfs-backup:local-expires-at'],'87400')
        self.z.prune(now=87400)
        self.assertEqual(self.z.data,{'foreign':'f'})
    def test_persisted_expiry_survives_restart_and_ttl_config_change(self):
        self.z.data={name(1):'h'}
        self.z.properties[name(1)]={'org.minecraft-zfs-backup:local-expires-at':'1200','org.minecraft-zfs-backup:family':'hourly'}
        self.z.prune(now=1200)
        self.assertEqual(self.z.data,{})
    def test_new_snapshots_have_atomic_user_properties(self):
        self.z.snapshot('pending-mc-live-20260101T000000Z')
        args=next(call for call in self.z.calls if call[0]=='snapshot')
        self.assertIn('org.minecraft-zfs-backup:family=live',args)
        self.assertTrue(any(a.startswith('org.minecraft-zfs-backup:local-expires-at=') for a in args))
    def test_recovery_intent_protects_unheld_snapshot_until_hold_reconciled(self):
        self.z.data={name(1):'h',name(2):'h2'}
        self.z.prune(now=90000,protected={name(1)})
        self.assertEqual(self.z.data,{name(1):'h'})
    def test_changed_guid_between_inventory_and_destroy_is_preserved(self):
        self.z.data={name(1):'old'}
        original=self.z.snapshots;calls=[]
        def snapshots(*args,**kwargs):
            calls.append(1)
            if len(calls)>1:self.z.data[name(1)]='replacement'
            return original(*args,**kwargs)
        self.z.snapshots=snapshots;self.z.prune(now=90000)
        self.assertEqual(self.z.data,{name(1):'replacement'})
    def test_config_ttls_are_positive_integers(self):
        self.assertEqual(self.config.live_ttl_seconds,3600)
        self.assertEqual(self.config.hourly_ttl_seconds,86400)
        for value in (0,-1,True,1.5):
            with self.assertRaises(ValueError):Config('tank/world',Path('/world'),Path('/state'),Path('/token'),live_ttl_seconds=value)
    def test_pending_destroy_is_strictly_scoped(self):
        for invalid in ['manual','pending-other','pending-mc-hourly-20260101T000000Z/child','-r']:
            with self.assertRaises(ValueError):self.z.destroy_pending(invalid)
        self.assertEqual(self.z.calls,[])


class BackendPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name);(root/'world').mkdir();(root/'world/level.dat').touch()
        self.config=Config('tank/world',root/'world',root/'state',root/'token')
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
