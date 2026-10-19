from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid

try:
    from minecraft_zfs_agent.rollback import RollbackManager
except ImportError:
    RollbackManager=None

NAME='mc-hourly-20261019T010101Z'

class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.world=self.root/'world';self.world.mkdir()
        (self.world/'session.lock').write_bytes(b'lock');(self.world/'level.dat').write_bytes(b'new')
        self.source=self.world/'.zfs'/'snapshot'/NAME;self.source.mkdir(parents=True)
        (self.source/'level.dat').write_bytes(b'old')
        self.state=self.root/'state';self.state.mkdir(mode=0o700)
        self.config=SimpleNamespace(world_path=self.world,state_dir=self.state,crafty=object(),source_dataset='tank/world')
        self.events=[]
        events=self.events;source=self.source
        class Backend:
            def __init__(s):s.data={NAME:'111'};s.held=set()
            def preflight(s):events.append('preflight')
            def snapshots(s):return list(s.data.items())
            def guid(s,n):return s.data[n]
            def snapshot_path(s,n):return source
            def hold(s,n,t):s.held.add((n,t));events.append('hold:'+n)
            def release(s,n,t):s.held.discard((n,t));events.append('release:'+n)
        self.backend=Backend()
        backend=self.backend
        class Gate:
            maintenance_owner=None
            active_checkpoint=None
            def enter_maintenance(s,i):
                if s.maintenance_owner not in (None,i):raise RuntimeError('busy')
                s.maintenance_owner=i;events.append('maintenance')
            def leave_maintenance(s,i):s.maintenance_owner=None;events.append('leave')
            def record_recovery(s,event,i):
                n='mc-hourly-20261019T'+('020202' if event=='pre_rollback' else '030303')+'Z'
                backend.data[n]='222' if event=='pre_rollback' else '333'
                backend.hold(n,'minecraft-zfs-rollback-'+i)
                events.append(event)
                return {'snapshot':n,'guid':backend.data[n],'event':event}
        self.gate=Gate()
        class Crafty:
            def __init__(s):s.policy={'auto_start':True,'crash_detection':True};s.i=1;s.running=True
            def settings(s):return deepcopy(s.policy)
            def set_policy(s,p):s.policy=deepcopy(p);events.append('policy:'+str(p['auto_start']))
            def stats(s):return {'stats_id':s.i,'created':str(s.i),'running':s.running}
            def action(s,a):
                events.append(a)
                s.running=a=='start_server';s.i+=1
            def wait_stopped(s,b):events.append('stopped');return s.stats()
            def wait_started(s,b):events.append('started');return s.stats()
        self.crafty=Crafty()
        self.identifier=str(uuid.uuid4())

    def manager(self,**extra):
        self.assertIsNotNone(RollbackManager,'rollback feature must exist')
        return RollbackManager(self.config,self.backend,self.gate,crafty=self.crafty,background=False,**extra)

    def test_success_orders_offline_lock_recovery_restore_postpoint_and_start(self):
        m=self.manager();job=m.submit(self.identifier,NAME)
        self.assertEqual(job['phase'],'COMPLETED')
        self.assertEqual((self.world/'level.dat').read_bytes(),b'old')
        for a,b in [('policy:False','stop_server'),('stopped','pre_rollback'),('pre_rollback','rollback_completed'),('rollback_completed','start_server'),('started','policy:True')]:
            self.assertLess(self.events.index(a),self.events.index(b),(a,b,self.events))
        self.assertFalse(self.backend.held);self.assertFalse(m.busy)
        count=len(self.events)
        self.assertEqual(m.submit(self.identifier,NAME)['phase'],'COMPLETED')
        self.assertEqual(len(self.events),count)
        with self.assertRaises(Exception):m.submit(self.identifier,'mc-hourly-20261019T000000Z')

    def test_local_live_snapshot_is_valid_for_rollback(self):
        live=NAME.replace('mc-hourly-','mc-live-')
        self.backend.data={live:'111'}
        manager=self.manager()
        self.assertEqual(manager.submit(self.identifier,live)['phase'],'COMPLETED')
        self.assertEqual((self.world/'level.dat').read_bytes(),b'old')

    def test_restore_failure_keeps_policy_disabled_holds_and_prepoint_and_never_starts(self):
        def fail(*_):raise OSError('disk exhausted')
        m=self.manager(restorer=fail);job=m.submit(self.identifier,NAME)
        self.assertEqual(job['phase'],'FAILED')
        self.assertIn('pre_rollback',self.events);self.assertNotIn('start_server',self.events)
        self.assertEqual(self.crafty.policy,{'auto_start':False,'crash_detection':False})
        self.assertTrue(self.backend.held);self.assertTrue(m.busy)
        self.assertTrue((self.state/'rollback.json').is_file())
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new')

    def test_settings_verification_failure_never_stops_or_restores(self):
        def fail(_):raise RuntimeError('settings rejected')
        self.crafty.set_policy=fail
        m=self.manager();job=m.submit(self.identifier,NAME)
        self.assertEqual(job['phase'],'FAILED');self.assertNotIn('stop_server',self.events)
        self.assertNotIn('pre_rollback',self.events)

    def test_unknown_or_guid_changed_source_fails_before_writes(self):
        m=self.manager()
        with self.assertRaises(Exception):m.submit(self.identifier,'foreign')
        self.assertNotIn('stop_server',self.events)
        original=self.backend.guid
        self.backend.guid=lambda n:'changed' if n==NAME else original(n)
        job=m.submit(self.identifier,NAME)
        self.assertEqual(job['phase'],'FAILED')
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new')

    def test_interrupted_restore_resumes_without_replacing_prepoint(self):
        m=self.manager();job=m.submit(self.identifier,NAME)
        state=__import__('json').loads((self.state/'rollback.json').read_text())
        state['jobs'][self.identifier]['phase']='RESTORING'
        state['jobs'][self.identifier]['post_recovery']=None
        __import__('minecraft_zfs_agent.protocol',fromlist=['atomic_json']).atomic_json(self.state/'rollback.json',state,0o600)
        self.events.clear();self.crafty.running=False
        m=self.manager()
        self.assertEqual(m.get(self.identifier)['phase'],'COMPLETED')
        self.assertNotIn('pre_rollback',self.events)
        self.assertIn('stop_server',self.events)


    def test_initially_stopped_uses_lock_without_impossible_new_stats(self):
        self.crafty.running=False
        self.crafty.wait_stopped=lambda _:self.fail('already stopped server emits no new stats')
        m=self.manager();job=m.submit(self.identifier,NAME)
        self.assertEqual(job['phase'],'COMPLETED')

    def test_pre_stop_failure_reports_unconfirmed_offline_and_policy(self):
        self.crafty.set_policy=lambda _:(_ for _ in ()).throw(RuntimeError('denied'))
        m=self.manager();job=m.submit(self.identifier,NAME)
        self.assertFalse(job['offline_confirmed'])
        self.assertFalse(job['policy_disabled_verified'])
        self.assertNotIn('remains disabled',job['error']['message'])

    def test_start_failure_attempts_orderly_stop_and_keeps_policy_disabled(self):
        self.crafty.wait_started=lambda _:(_ for _ in ()).throw(RuntimeError('not ready'))
        m=self.manager();job=m.submit(self.identifier,NAME)
        self.assertEqual(job['phase'],'FAILED')
        self.assertFalse(self.crafty.running)
        self.assertEqual(self.crafty.policy,{'auto_start':False,'crash_detection':False})
        self.assertTrue(self.backend.held)

    def test_resume_after_start_never_reapplies_snapshot_over_live_gameplay(self):
        m=self.manager();m.submit(self.identifier,NAME)
        (self.world/'level.dat').write_bytes(b'new gameplay after restart')
        state=__import__('json').loads((self.state/'rollback.json').read_text())
        state['jobs'][self.identifier]['phase']='STARTING'
        __import__('minecraft_zfs_agent.protocol',fromlist=['atomic_json']).atomic_json(self.state/'rollback.json',state,0o600)
        self.events.clear();self.crafty.running=True
        m=self.manager()
        self.assertEqual(m.get(self.identifier)['phase'],'COMPLETED')
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new gameplay after restart')
        self.assertNotIn('stop_server',self.events)
        self.assertNotIn('pre_rollback',self.events)

    def test_resume_during_policy_restore_never_reapplies_snapshot(self):
        m=self.manager();m.submit(self.identifier,NAME)
        (self.world/'level.dat').write_bytes(b'play after start')
        state=__import__('json').loads((self.state/'rollback.json').read_text())
        state['jobs'][self.identifier]['phase']='RESTORING_POLICY'
        __import__('minecraft_zfs_agent.protocol',fromlist=['atomic_json']).atomic_json(self.state/'rollback.json',state,0o600)
        self.events.clear();self.crafty.running=True
        m=self.manager()
        self.assertEqual(m.get(self.identifier)['phase'],'COMPLETED')
        self.assertEqual((self.world/'level.dat').read_bytes(),b'play after start')
        self.assertNotIn('stop_server',self.events)

    def test_failure_does_not_clear_original_prepoint_on_explicit_resume(self):
        def fail(*_):raise OSError('interrupted')
        m=self.manager(restorer=fail);failed=m.submit(self.identifier,NAME)
        pre=failed['pre_recovery'];self.events.clear()
        from minecraft_zfs_agent.restore import restore_world
        m.restorer=restore_world
        result=m.resume(self.identifier)
        self.assertEqual(result['phase'],'COMPLETED')
        self.assertEqual(result['pre_recovery'],pre)
        self.assertNotIn('pre_rollback',self.events)

    def test_finalization_restart_only_cleans_remaining_holds(self):
        m=self.manager();m.submit(self.identifier,NAME)
        state=__import__('json').loads((self.state/'rollback.json').read_text())
        state['jobs'][self.identifier]['phase']='FINALIZING'
        __import__('minecraft_zfs_agent.protocol',fromlist=['atomic_json']).atomic_json(self.state/'rollback.json',state,0o600)
        self.events.clear();self.backend.data.pop(NAME)
        m=self.manager()
        self.assertEqual(m.get(self.identifier)['phase'],'COMPLETED')
        self.assertNotIn('stop_server',self.events)
        self.assertNotIn('policy:False',self.events)

    def test_close_interrupts_restore_without_releasing_guard_early_or_losing_resume_intent(self):
        import threading
        entered=threading.Event();continue_restore=threading.Event()
        def blocked(source,world,guard):
            entered.set();continue_restore.wait(5);guard.check()
        self.assertIsNotNone(RollbackManager)
        m=RollbackManager(self.config,self.backend,self.gate,crafty=self.crafty,restorer=blocked,background=True)
        m.submit(self.identifier,NAME)
        self.assertTrue(entered.wait(3))
        threading.Timer(0.01,continue_restore.set).start();m.close(timeout=3)
        self.assertEqual(m.get(self.identifier)['phase'],'INTERRUPTED')
        self.assertTrue(self.backend.held)
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new')
        self.assertFalse(self.crafty.policy['auto_start'])

    def test_target_hold_failure_never_stops_or_writes(self):
        self.backend.hold=lambda *_:(_ for _ in ()).throw(RuntimeError('hold failed'))
        m=self.manager();job=m.submit(self.identifier,NAME)
        self.assertEqual(job['phase'],'FAILED')
        self.assertNotIn('stop_server',self.events)
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new')

if __name__ == '__main__':unittest.main()
