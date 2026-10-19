"""Durable rollback evidence, independent of the bounded operational journal."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
from minecraft_zfs_agent.protocol import APIError
import test_rollback as rollback_tests
NAME=rollback_tests.NAME


class AuditFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=self.root/'state';self.state.mkdir(mode=0o700)
        self.world=self.root/'world';self.world.mkdir()
    def log(self):
        from minecraft_zfs_agent.audit import AuditLog
        return AuditLog(self.state,self.world,'tank/world',clock=lambda:123)
    def test_append_survives_restart_and_fsyncs_each_record(self):
        log=self.log()
        with patch('os.fsync',wraps=os.fsync) as sync:
            log.append('REQUEST');log.append('REJECT',error={'code':'busy','message':'Busy'})
            self.assertGreaterEqual(sync.call_count,2)
        self.log().append('REQUEST')
        records=[json.loads(line) for line in log.path.read_text().splitlines()]
        self.assertEqual(len(records),3)
        self.assertEqual(len({r['event_id'] for r in records}),3)
        self.assertEqual(records[0]['timestamp_ms'],123)
        self.assertEqual(records[0]['source_dataset'],'tank/world')
        self.assertEqual(log.path.stat().st_mode&0o777,0o600)
    def test_unsafe_modes_symlinks_and_world_state_are_rejected(self):
        from minecraft_zfs_agent.audit import AuditLog,AuditUnavailable
        self.state.chmod(0o755)
        with self.assertRaises(AuditUnavailable):self.log()
        self.state.chmod(0o700)
        target=self.root/'target';target.write_text('unchanged')
        (self.state/'rollback-audit.jsonl').symlink_to(target)
        with self.assertRaises(AuditUnavailable):self.log().append('REQUEST')
        self.assertEqual(target.read_text(),'unchanged')
        with self.assertRaises(AuditUnavailable):AuditLog(self.world,self.world,'tank/world')
    def test_unsafe_file_owner_permissions_hardlink_and_torn_append_fail_closed(self):
        from minecraft_zfs_agent.audit import AuditUnavailable
        log=self.log();log.append('REQUEST')
        log.path.chmod(0o644)
        with self.assertRaises(AuditUnavailable):log.append('REQUEST')
        log.path.chmod(0o600)
        if os.geteuid()==0:
            os.chown(log.path,12345,0)
            with self.assertRaises(AuditUnavailable):log.append('REQUEST')
            os.chown(log.path,0,0)
        link=self.root/'hardlink';os.link(log.path,link)
        with self.assertRaises(AuditUnavailable):log.append('REQUEST')
        link.unlink()
        with log.path.open('ab') as out:out.write(b'{"torn":')
        previous=log.path.read_bytes()
        with self.assertRaises(AuditUnavailable):log.append('REQUEST')
        self.assertEqual(log.path.read_bytes(),previous)
    def test_actor_validation_and_json_injection(self):
        from minecraft_zfs_agent.audit import normalize_actor
        player={'kind':'player','name':'a"},{"secret":"fake','uuid':str(uuid.uuid4())}
        actor=normalize_actor(player)
        self.assertEqual(actor['provenance'],'authenticated_client_claim')
        log=self.log();log.append('REQUEST',actor=actor)
        self.assertEqual(len(log.path.read_text().splitlines()),1)
        for bad in [{'kind':'console','name':'a\nb'}, {'kind':'player','name':'x','uuid':'bad'}, {'kind':'api','name':'pretend'}]:
            with self.assertRaises(APIError):normalize_actor(bad)


class RollbackAuditTests(rollback_tests.RollbackTests):
    def setUp(self):
        super().setUp();self.config.source_dataset='tank/world'
    def records(self):return [json.loads(line) for line in (self.state/'rollback-audit.jsonl').read_text().splitlines()]
    def test_every_restore_has_durable_audit_before_write_and_fixed_actor(self):
        actor={'kind':'console','name':'administrator'}
        def restore(*_):
            records=self.records()
            self.assertTrue(any(r['event']=='PHASE_INTENT' and r.get('phase')=='RESTORING' for r in records))
            self.assertTrue(any(r['event']=='RECOVERY_POINT' and r.get('point',{}).get('event')=='pre_rollback' for r in records))
        m=self.manager(restorer=restore,validator=lambda *_:None)
        result=m.submit(self.identifier,NAME,actor=actor)
        self.assertEqual(result['phase'],'COMPLETED')
        self.assertEqual(result['actor']['name'],'administrator')
        self.assertEqual(self.records()[-1]['event'],'COMPLETED')
        with self.assertRaises(APIError):m.submit(self.identifier,NAME,actor={'kind':'console','name':'different'})
        self.assertEqual(self.records()[-1]['event'],'REJECT')
    def test_audit_failure_prevents_stop_and_restore(self):
        from minecraft_zfs_agent.audit import AuditUnavailable
        m=self.manager()
        original=m.audit.append
        def append(event,**fields):
            if fields.get('phase')=='STOPPING':raise AuditUnavailable()
            return original(event,**fields)
        m.audit.append=append
        job=m.submit(self.identifier,NAME)
        self.assertNotIn('stop_server',self.events)
        self.assertNotIn('pre_rollback',self.events)
        self.assertFalse(self.crafty.policy['auto_start'])
        self.assertEqual(job['phase'],'FAILED')
    def test_failure_explicit_resume_restart_and_sanitized_errors(self):
        def fail(*_):raise RuntimeError('SECRET token https://private/\nINJECT')
        m=self.manager(restorer=fail);m.submit(self.identifier,NAME)
        self.assertEqual(self.records()[-1]['event'],'FAILED')
        m.restorer=lambda *_:None;m.validator=lambda *_:None
        m.resume(self.identifier,actor={'kind':'console','name':'retry-admin'})
        records=self.records()
        self.assertTrue(any(r['event']=='EXPLICIT_RESUME' and r['actor']['name']=='retry-admin' for r in records))
        self.assertNotIn('SECRET',(self.state/'rollback-audit.jsonl').read_text())
        self.assertEqual(m.get(self.identifier)['actor']['kind'],'api')
        state=json.loads((self.state/'rollback.json').read_text());state['jobs'][self.identifier]['phase']='FINALIZING'
        from minecraft_zfs_agent.protocol import atomic_json
        atomic_json(self.state/'rollback.json',state,0o600)
        self.manager()
        self.assertTrue(any(r['event']=='RESTART_RESUME' for r in self.records()))
    def test_restore_audit_failure_blocks_world_writes(self):
        from minecraft_zfs_agent.audit import AuditUnavailable
        m=self.manager();original=m.audit.append
        def append(event,**fields):
            if fields.get('phase')=='RESTORING':raise AuditUnavailable()
            return original(event,**fields)
        m.audit.append=append
        job=m.submit(self.identifier,NAME)
        self.assertEqual(job['phase'],'FAILED')
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new')
        self.assertNotIn('start_server',self.events)
    def test_post_start_audit_failure_safely_stops_and_backfills_gap(self):
        from minecraft_zfs_agent.audit import AuditUnavailable
        m=self.manager();original=m.audit.append
        def append(event,**fields):
            if fields.get('phase')=='START_VERIFIED':raise AuditUnavailable()
            return original(event,**fields)
        m.audit.append=append
        job=m.submit(self.identifier,NAME)
        self.assertEqual(job['phase'],'FAILED')
        self.assertFalse(self.crafty.running)
        self.assertFalse(self.crafty.policy['auto_start'])
        self.assertTrue(any(r['event']=='AUDIT_GAP' for r in self.records()))
        self.assertTrue(any(r['event']=='SAFETY_COMPENSATION' for r in self.records()))
    def test_persistent_audit_failure_keeps_pending_evidence_for_restart(self):
        from minecraft_zfs_agent.audit import AuditUnavailable
        m=self.manager();original=m.audit.append
        def append(event,**fields):
            if fields.get('phase')=='RESTORING':
                m.audit.append=lambda *a,**kw:(_ for _ in ()).throw(AuditUnavailable())
                raise AuditUnavailable()
            return original(event,**fields)
        m.audit.append=append
        with self.assertLogs(level='ERROR'):
            job=m.submit(self.identifier,NAME)
        self.assertEqual(job['phase'],'FAILED');self.assertTrue(job['audit_pending'])
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new')
        m=self.manager()
        self.assertFalse(m.get(self.identifier).get('audit_pending'))
        self.assertTrue(any(r['event']=='AUDIT_GAP' for r in self.records()))
    def test_next_authenticated_request_flushes_pending_gap_without_restart(self):
        from minecraft_zfs_agent.audit import AuditUnavailable
        m=self.manager();original=m.audit.append
        def append(event,**fields):
            if fields.get('phase')=='RESTORING':
                m.audit.append=lambda *a,**kw:(_ for _ in ()).throw(AuditUnavailable())
                raise AuditUnavailable()
            return original(event,**fields)
        m.audit.append=append
        with self.assertLogs(level='ERROR'):
            m.submit(self.identifier,NAME)
        self.assertTrue(m.get(self.identifier)['audit_pending'])
        m.audit.append=original
        with self.assertRaises(APIError):m.resume(str(uuid.uuid4()))
        self.assertFalse(m.get(self.identifier)['audit_pending'])
        self.assertTrue(any(r['event']=='AUDIT_GAP' for r in self.records()))
    def test_busy_rejection_retains_requested_target_guid(self):
        m=self.manager(restorer=lambda *_:(_ for _ in ()).throw(RuntimeError('fail')))
        m.submit(self.identifier,NAME)
        with self.assertRaises(APIError):m.submit(str(uuid.uuid4()),NAME,actor={'kind':'console','name':'other'})
        rejected=self.records()[-1]
        self.assertEqual(rejected['event'],'REJECT')
        self.assertEqual(rejected['guid'],'111')
        self.assertEqual(rejected['snapshot'],NAME)
    def test_audit_and_state_failure_after_start_still_stops_without_restore_retry(self):
        from minecraft_zfs_agent.audit import AuditUnavailable
        calls=[]
        m=self.manager(restorer=lambda *_:calls.append('restore'),validator=lambda *_:None)
        original_append=m.audit.append;original_save=m._save;failed=False
        def append(event,**fields):
            nonlocal failed
            if fields.get('phase')=='START_VERIFIED':failed=True;raise AuditUnavailable()
            if failed:raise AuditUnavailable()
            return original_append(event,**fields)
        def save():
            if failed:raise OSError('ENOSPC SECRET')
            return original_save()
        m.audit.append=append;m._save=save
        with self.assertLogs(level='ERROR') as logs:
            job=m.submit(self.identifier,NAME)
        self.assertNotIn('SECRET',' '.join(logs.output))
        self.assertIn('evidence may be incomplete',' '.join(logs.output))
        self.assertEqual(job['phase'],'FAILED')
        self.assertFalse(self.crafty.running);self.assertFalse(self.crafty.policy['auto_start'])
        self.assertEqual(calls,['restore']);self.assertEqual(self.events.count('start_server'),1)
        self.assertTrue(self.backend.held)
    def test_first_audit_failure_outbox_save_is_already_failed_not_runnable(self):
        from minecraft_zfs_agent.audit import AuditUnavailable
        m=self.manager();original_append=m.audit.append;original_save=m._save;failed=False
        def append(event,**fields):
            nonlocal failed
            if fields.get('phase')=='RESTORING':failed=True;raise AuditUnavailable()
            return original_append(event,**fields)
        def save():
            original_save()
            if failed:raise SystemExit('crash after first audit failure state write')
        m.audit.append=append;m._save=save
        with self.assertRaises(SystemExit):m.submit(self.identifier,NAME)
        state=json.loads((self.state/'rollback.json').read_text())
        self.assertEqual(state['jobs'][self.identifier]['phase'],'FAILED')
        self.events.clear();m=self.manager()
        self.assertEqual(m.get(self.identifier)['phase'],'FAILED')
        self.assertNotIn('stop_server',self.events)
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new')
    def test_audit_history_outlives_64_completed_jobs(self):
        m=self.manager(restorer=lambda *_:None,validator=lambda *_:None)
        for _ in range(66):m.submit(str(uuid.uuid4()),NAME)
        self.assertEqual(len(m._state['jobs']),64)
        self.assertEqual(sum(r['event']=='COMPLETED' for r in self.records()),66)


class HTTPAuditTests(__import__('test_http').HTTPFixture):
    def test_authenticated_rejections_and_invalid_body_are_recorded(self):
        import json
        before=self.service.config.state_dir/'rollback-audit.jsonl'
        status,_=self.request('/v1/rollbacks','POST','{}',token=False)
        self.assertEqual(status,401);self.assertFalse(before.exists())
        status,_=self.request('/v1/rollbacks','POST','broken')
        self.assertEqual(status,400)
        status,_=self.request('/v1/rollbacks','POST','{}')
        self.assertEqual(status,503)
        records=[json.loads(line) for line in before.read_text().splitlines()]
        self.assertEqual(sum(r['event']=='HTTP_REQUEST' for r in records),2)
        self.assertEqual([r['error']['code'] for r in records if r['event']=='HTTP_REJECT'],['invalid_json','rollback_disabled'])
    def test_actor_must_be_object_and_audit_unavailable_blocks_manager(self):
        from minecraft_zfs_agent.audit import AuditUnavailable
        called=[]
        class Manager:
            def submit(self,*a,**kw):called.append(a);return {}
        self.server.rollback=Manager()
        body={'id':str(uuid.uuid4()),'snapshot':NAME,'actor':None}
        status,_=self.request('/v1/rollbacks','POST',json.dumps(body))
        self.assertEqual(status,400);self.assertFalse(called)
        self.server.audit.append=lambda *a,**kw:(_ for _ in ()).throw(AuditUnavailable())
        body.pop('actor')
        status,result=self.request('/v1/rollbacks','POST',json.dumps(body))
        self.assertEqual(status,503);self.assertEqual(result['error']['code'],'audit_unavailable');self.assertFalse(called)
    def test_actor_forwarding_and_busy_notfound_rejections(self):
        from minecraft_zfs_agent.rollback import RollbackManager
        m=RollbackManager(self.service.config,self.service.backend,self.service,crafty=object(),background=False)
        self.server.rollback=m
        actor={'kind':'console','name':'admin'}
        status,_=self.request('/v1/rollbacks','POST',json.dumps({'id':str(uuid.uuid4()),'snapshot':NAME,'actor':actor}))
        self.assertEqual(status,404)
        records=[json.loads(line) for line in m.audit.path.read_text().splitlines()]
        self.assertTrue(any(r['event']=='REJECT' and r['actor']['name']=='admin' for r in records))
        status,_=self.request('/v1/rollbacks/'+str(uuid.uuid4())+'/resume','POST','{}')
        self.assertEqual(status,404)
