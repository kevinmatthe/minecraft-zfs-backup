"""Durable one-click rollback coordinator. Failed jobs retain maintenance and holds."""
from copy import deepcopy
from pathlib import Path
import logging
import threading
import time
import uuid

from .audit import AuditLog, AuditUnavailable, normalize_actor
from .crafty import CraftyClient
from .protocol import APIError, atomic_json, read_json
from .restore import WorldLock, restore_world, validate_world
from .zfs import OWNED

_DISABLED = {'auto_start':False,'crash_detection':False}


class RollbackManager:
    def __init__(self, config, backend, checkpoint_service, clock=None, *, crafty=None,
                 restorer=restore_world, validator=validate_world, background=True):
        self.config, self.backend, self.service = config,backend,checkpoint_service
        self.crafty = crafty if crafty is not None else CraftyClient(config.crafty)
        self.clock = clock or (lambda:int(time.time()*1000))
        self.restorer,self.validator,self.background = restorer,validator,background
        self.path = Path(config.state_dir)/'rollback.json'
        self.audit = AuditLog(config.state_dir,config.world_path,config.source_dataset,self.clock)
        self._lock = threading.RLock()
        self._thread = None
        self._closing = threading.Event()
        self._state = {'version':1,'current':None,'jobs':{}}
        if self.path.exists():
            self._state = read_json(self.path,limit=1024*1024)
            self._validate_state()
        for job in self._state['jobs'].values():self._flush_pending(job)
        current = self._state['current']
        if current:
            job = self._state['jobs'][current]
            if job['phase'] != 'COMPLETED':
                self.service.enter_maintenance(current)
                # A recorded failure requires explicit administrative resumption. Only an
                # interrupted operation continues automatically on agent restart.
                if job['phase'] != 'FAILED':
                    self._audit('RESTART_RESUME',job,actor=normalize_actor())
                    self._launch(current)

    def _validate_state(self):
        if self._state.get('version') != 1 or not isinstance(self._state.get('jobs'),dict):
            raise ValueError('invalid rollback state')
        if self._state.get('current') not in (None,*self._state['jobs']):
            raise ValueError('invalid rollback current job')
        for identifier,job in self._state['jobs'].items():
            if str(uuid.UUID(identifier)) != identifier or not OWNED.fullmatch(job['snapshot']):
                raise ValueError('invalid rollback state identity')
            if not isinstance(job.get('guid'),str) or not job['guid'].isdigit():
                raise ValueError('invalid rollback source GUID')
            if job.get('original_policy') is not None:
                policy=job['original_policy']
                if set(policy) != set(_DISABLED) or any(type(v) is not bool for v in policy.values()):
                    raise ValueError('invalid persisted Crafty policy')

    @property
    def busy(self):
        with self._lock:
            current=self._state['current']
            return current is not None and self._state['jobs'][current]['phase'] != 'COMPLETED'

    def _save(self):
        atomic_json(self.path,self._state,mode=0o600)

    def _audit(self,event,job,actor=None,**fields):
        return self.audit.append(event,actor=actor or job.get('actor') or normalize_actor(),
                                 job_id=job['id'],snapshot=job['snapshot'],guid=job.get('guid'),**fields)

    def _update(self, identifier, phase=None, **fields):
        with self._lock:
            job=self._state['jobs'][identifier]
            event=phase if phase in ('COMPLETED','FAILED','INTERRUPTED') else 'PHASE_INTENT'
            self._audit(event,job,phase=phase or job['phase'],
                        **({'error':fields['error']} if fields.get('error') else {}))
            for key in ('pre_recovery','post_recovery'):
                if fields.get(key):self._audit('RECOVERY_POINT',job,point=fields[key])
            if phase is not None: job['phase']=phase
            job.update(fields);job['updated_ms']=self.clock()
            self._save()
            return deepcopy(job)

    def get(self, identifier):
        with self._lock:
            if identifier not in self._state['jobs']:
                raise APIError(404,'unknown_rollback','Rollback job was not found')
            return deepcopy(self._state['jobs'][identifier])

    def current(self):
        with self._lock:
            identifier=self._state['current']
            return deepcopy(self._state['jobs'][identifier]) if identifier else None

    def submit(self, identifier, snapshot, actor=None):
        return self._request('submit',identifier,snapshot,actor)

    def flush_audit(self):
        with self._lock:
            for job in self._state['jobs'].values():self._flush_pending(job)

    def _request(self,action,identifier,snapshot=None,actor=None):
        self.flush_audit()
        # Do not retain arbitrary invalid IDs, snapshot strings or raw request bodies.
        context={}
        try:
            if isinstance(identifier,str) and str(uuid.UUID(identifier))==identifier:context['job_id']=identifier
        except (ValueError,AttributeError):pass
        if isinstance(snapshot,str) and OWNED.fullmatch(snapshot):
            context['snapshot']=snapshot
            # Include an observed identity even for busy/conflicting requests.
            # Read failure never hides the original rejection or exposes backend text.
            try:
                guid=dict(self.backend.snapshots()).get(snapshot)
                if isinstance(guid,str) and guid.isdigit():context['guid']=guid
            except Exception:pass
        elif action=='resume' and 'job_id' in context:
            with self._lock:
                job=self._state['jobs'].get(identifier)
                if job:context.update(snapshot=job['snapshot'],guid=job['guid'])
        normalized=normalize_actor()
        try:
            normalized=normalize_actor(actor)
        except APIError as error:
            self.audit.append('REQUEST',action=action,**context)
            self.audit.append('REJECT',action=action,error=dict(code=error.code,message=error.message),**context)
            raise
        self.audit.append('REQUEST',actor=normalized,action=action,**context)
        try:
            if action=='submit':return self._submit(identifier,snapshot,normalized)
            return self._resume(identifier,normalized)
        except AuditUnavailable:raise
        except APIError as error:
            self.audit.append('REJECT',actor=normalized,action=action,
                              error=dict(code=error.code,message=error.message),**context)
            raise
        except Exception:
            self.audit.append('REJECT',actor=normalized,action=action,
                              error=dict(code='internal_error',message='Operation failed; inspect administrator state'),**context)
            raise

    def _submit(self, identifier, snapshot, actor):
        try:
            if not isinstance(identifier,str) or str(uuid.UUID(identifier)) != identifier:
                raise ValueError()
        except (ValueError,AttributeError):
            raise APIError(400,'invalid_id','Rollback ID must be a canonical UUID') from None
        if not isinstance(snapshot,str) or not OWNED.fullmatch(snapshot):
            raise APIError(400,'invalid_snapshot','A managed published snapshot is required')
        with self._lock:
            if identifier in self._state['jobs']:
                previous=self._state['jobs'][identifier]
                if previous['snapshot'] != snapshot or previous.get('actor',normalize_actor()) != actor:
                    raise APIError(409,'identity_conflict','Rollback ID already selects another snapshot or actor')
                self._audit('ACCEPT',previous,actor=actor,duplicate=True)
                return deepcopy(previous)
            if self.busy: raise APIError(409,'maintenance','A rollback already requires maintenance')
            self.service.enter_maintenance(identifier)
            try:
                self.backend.preflight()
                available=dict(self.backend.snapshots())
                if snapshot not in available:
                    raise APIError(404,'unknown_snapshot','Snapshot was not found')
                guid=available[snapshot]
                if not isinstance(guid,str) or not guid.isdigit():
                    raise RuntimeError('invalid snapshot GUID')
                job={'id':identifier,'snapshot':snapshot,'guid':guid,'actor':actor,'phase':'INTENT',
                     'created_ms':self.clock(),'updated_ms':self.clock(),'original_policy':None,
                     'pre_recovery':None,'post_recovery':None,'stop_baseline':None,'error':None,
                     'offline_confirmed':False,'policy_disabled_verified':False}
                self._audit('ACCEPT',job)
                self._state['jobs'][identifier]=job;self._state['current']=identifier
                # Bound completed history; failed/active work remains available for recovery.
                for old in list(self._state['jobs']):
                    if len(self._state['jobs']) <= 64: break
                    if old != identifier and self._state['jobs'][old]['phase']=='COMPLETED':
                        del self._state['jobs'][old]
                self._save()
            except BaseException:
                self.service.leave_maintenance(identifier)
                raise
            self._launch(identifier)
            return self.get(identifier)

    def _launch(self, identifier):
        if self.background:
            self._thread=threading.Thread(target=self._run,args=(identifier,),name='zfs-rollback',daemon=True)
            self._thread.start()
        else: self._run(identifier)

    def resume(self, identifier, actor=None):
        """Explicit administrative retry; original requester remains immutable."""
        return self._request('resume',identifier,actor=actor)

    def _resume(self, identifier, actor):
        with self._lock:
            job=self.get(identifier)
            if self._state['current'] != identifier or job['phase'] != 'FAILED':
                raise APIError(409,'not_resumable','Only the current failed rollback can resume')
            if self._thread and self._thread.is_alive():
                raise APIError(409,'busy','Rollback worker is still finishing')
            self._flush_pending(self._state['jobs'][identifier])
            self._audit('ACCEPT',job,actor=actor,action='resume')
            self._audit('EXPLICIT_RESUME',job,actor=actor)
            self.service.enter_maintenance(identifier)
            self._update(identifier,job.get('failed_phase','INTENT'),error=None)
            self._launch(identifier)
            return self.get(identifier)

    def _check_guid(self, point):
        if self.backend.guid(point['snapshot']) != point['guid']:
            raise RuntimeError('snapshot identity changed')

    def _offline(self, identifier):
        job=self.get(identifier)
        if job['original_policy'] is None:
            self._update(identifier,'DISABLING_AUTOSTART',original_policy=self.crafty.settings())
        else: self._update(identifier,'DISABLING_AUTOSTART')
        self._update(identifier,policy_disabled_verified=False,offline_confirmed=False)
        self.crafty.set_policy(_DISABLED)
        self._update(identifier,policy_disabled_verified=True)
        baseline=self.crafty.stats()
        # Persist the original running baseline across agent restarts. Crafty does not
        # emit new statistics when stop is requested for an already stopped process.
        original=job.get('stop_baseline')
        if original is None:
            original=baseline
            self._update(identifier,stop_baseline=original)
        self._update(identifier,'STOPPING')
        self.crafty.action('stop_server')
        if baseline.get('running') is True:
            stopped=self.crafty.wait_stopped(baseline)
        elif original.get('running') is True:
            stopped=self.crafty.wait_stopped(original)
        else:
            # Approved already-stopped exception: verified restart policy plus cached
            # false is only sufficient once the authoritative POSIX lock is acquired.
            stopped=self.crafty.stats()
            if stopped.get('running') is not False:
                raise RuntimeError('Crafty no longer reports the server stopped')
        self._update(identifier,'OFFLINE',stopped_stats=stopped)
        return stopped

    def _complete(self, identifier):
        tag='minecraft-zfs-rollback-'+identifier
        job=self.get(identifier)
        available=dict(self.backend.snapshots())
        for point in (job,job['pre_recovery'],job['post_recovery']):
            if point is None: raise RuntimeError('missing finalization recovery point')
            # A previously released point may already have been pruned. Never release
            # a hold from a replacement with the same name but another GUID.
            if point['snapshot'] not in available: continue
            self._check_guid(point)
            self.backend.release(point['snapshot'],tag)
        self._update(identifier,'COMPLETED',error=None)
        self.service.leave_maintenance(identifier)

    def _finish_start(self, identifier):
        if self._closing.is_set(): raise InterruptedError('agent is stopping')
        job=self.get(identifier)
        tag='minecraft-zfs-rollback-'+identifier
        for point in (job,job['pre_recovery'],job['post_recovery']):
            self._check_guid(point);self.backend.hold(point['snapshot'],tag)
        self._update(identifier,policy_disabled_verified=False,offline_confirmed=False)
        self.crafty.set_policy(_DISABLED)
        self._update(identifier,policy_disabled_verified=True)
        baseline=self.crafty.stats()
        if baseline.get('running') is False:
            self._update(identifier,'STARTING',start_baseline=baseline)
            self.crafty.action('start_server')
            self.crafty.wait_started(baseline)
        else:
            # The prior start may already have run gameplay. Never reapply restored
            # data here: post_recovery permanently marks the end of destructive work.
            self._update(identifier,'STARTING')
            self.crafty.wait_started(job.get('start_baseline') or baseline)
        self._update(identifier,'START_VERIFIED')
        self._update(identifier,'RESTORING_POLICY')
        self.crafty.set_policy(job['original_policy'])
        self._update(identifier,'FINALIZING',policy_disabled_verified=job['original_policy']==_DISABLED)
        # FINALIZING is durable before any hold release. Resumption only finishes
        # releasing remaining holds, without stopping or rewriting the running world.
        self._complete(identifier)

    def _flush_pending(self,job):
        while job.get('audit_pending'):
            record=job['audit_pending'][0]
            self._audit(record['event'],job,**record['fields'])
            job['audit_pending'].pop(0);self._save()

    def _audit_failure(self,identifier):
        # Pause and enqueue evidence in ONE state write: a crash must never leave
        # a runnable old phase alongside a durable audit-failure outbox.
        with self._lock:
            job=self._state['jobs'][identifier];phase=job['phase']
            error={'code':'audit_unavailable','message':'Durable rollback audit is unavailable; operation paused'}
            warned=False
            def unavailable():
                nonlocal warned
                if not warned:
                    logging.error('Rollback audit/state unavailable; evidence may be incomplete; holds retained')
                    warned=True
            def persist():
                try:self._save()
                except Exception:
                    job['audit_state_unavailable']=True;unavailable()
            def flush():
                try:self._flush_pending(job)
                except Exception:unavailable()
            def enqueue(event,**fields):
                job.setdefault('audit_pending',[]).append(dict(event=event,fields=dict(
                    intent_id=str(uuid.uuid4()),occurred_ms=self.clock(),**fields)))
            def evidence(event,**fields):
                enqueue(event,**fields);persist();flush()
            job.update(phase='FAILED',failed_phase=phase,error=error,updated_ms=self.clock())
            enqueue('AUDIT_GAP',phase=phase,error=error)
            enqueue('FAILED',phase='FAILED',error=error)
            try:
                persist();flush()
            finally:
                # The already-audited STARTING intent authorizes ONLY safety stop
                # and disabling restart here, never restoration or another start.
                # Neither audit nor state-filesystem failure may prevent the stop.
                if job.get('post_recovery') is not None and phase!='FINALIZING':
                    evidence('SAFETY_COMPENSATION_INTENT',phase=phase)
                    job.update(offline_confirmed=False,policy_disabled_verified=False);persist()
                    try:
                        self.crafty.set_policy(_DISABLED)
                        job['policy_disabled_verified']=True;persist()
                    except Exception:pass
                    try:baseline=self.crafty.stats()
                    except Exception:baseline=None
                    try:
                        self.crafty.action('stop_server')
                        if baseline and baseline.get('running') is True:self.crafty.wait_stopped(baseline)
                        with WorldLock(self.config.world_path):
                            job['offline_confirmed']=True;persist()
                    except Exception:pass
                    evidence('SAFETY_COMPENSATION',offline_confirmed=job['offline_confirmed'],
                             policy_disabled_verified=job['policy_disabled_verified'])

    def _run(self,identifier):
        try:self._run_audited(identifier)
        except AuditUnavailable:self._audit_failure(identifier)

    def _run_audited(self, identifier):
        tag='minecraft-zfs-rollback-'+identifier
        may_be_running=False
        try:
            job=self.get(identifier)
            if job['phase']=='FINALIZING' or (job['phase']=='INTERRUPTED' and job.get('interrupted_phase')=='FINALIZING'):
                self._complete(identifier);return
            self.backend.preflight()
            if job['post_recovery'] is not None:
                may_be_running=True
                self._finish_start(identifier);return
            self._check_guid(job)
            self.backend.hold(job['snapshot'],tag)
            self._check_guid(job)
            self._offline(identifier)
            with WorldLock(self.config.world_path,cancel=self._closing) as guard:
                self._update(identifier,offline_confirmed=True)
                self._check_guid(job)
                if self.crafty.settings() != _DISABLED:
                    self._update(identifier,policy_disabled_verified=False)
                    raise RuntimeError('automatic restart policy changed before restore')
                job=self.get(identifier)
                if job['pre_recovery'] is None:
                    self._update(identifier,'PRE_RECOVERY')
                    point=self.service.record_recovery('pre_rollback',identifier)
                    self._update(identifier,pre_recovery=point)
                else:
                    point=job['pre_recovery']
                    self._check_guid(point);self.backend.hold(point['snapshot'],tag)
                guard.check()
                source=self.backend.snapshot_path(job['snapshot'])
                if job['phase'] != 'POST_RECOVERY':
                    self._update(identifier,'RESTORING')
                    self.restorer(source,self.config.world_path,guard)
                    self._update(identifier,'VALIDATING')
                self.validator(source,self.config.world_path,guard)
                self._check_guid(job);guard.check()
                self._update(identifier,'POST_RECOVERY')
                point=self.service.record_recovery('rollback_completed',identifier)
                self._update(identifier,post_recovery=point)
                guard.check()
            may_be_running=True
            self._finish_start(identifier)
        except AuditUnavailable:
            self._audit_failure(identifier)
        except InterruptedError:
            interrupted=self.get(identifier)['phase']
            self._update(identifier,'INTERRUPTED',interrupted_phase=interrupted,error={'code':'agent_stopping',
                         'message':'Agent shutdown interrupted the job; restart will resume safely'})
        except Exception as error:
            phase=self.get(identifier)['phase']
            # A start may already have launched a JVM. Stop it on failure; never
            # restore data here. Finalization failures leave a verified successful
            # start alone and may resume only their non-destructive hold cleanup.
            if may_be_running and phase != 'FINALIZING':
                self._update(identifier,offline_confirmed=False,policy_disabled_verified=False)
                try:
                    self.crafty.set_policy(_DISABLED)
                    self._update(identifier,policy_disabled_verified=True)
                    baseline=self.crafty.stats();self.crafty.action('stop_server')
                    if baseline.get('running') is True: self.crafty.wait_stopped(baseline)
                    with WorldLock(self.config.world_path):
                        self._update(identifier,offline_confirmed=True)
                except AuditUnavailable:return self._audit_failure(identifier)
                except Exception: pass
            status=self.get(identifier)
            policy=('automatic restart is verified disabled' if status.get('policy_disabled_verified')
                    else 'automatic restart policy is not confirmed disabled')
            self._update(identifier,'FAILED',failed_phase=phase,
                         error={'code':'rollback_failed','message':'Rollback failed during '+phase+
                                ' ('+type(error).__name__+'); '+policy})

    def close(self, timeout=5):
        """Bound shutdown; checks between clone chunks unwind while holding the lock."""
        self._closing.set()
        self.join(timeout)

    def join(self, timeout=None):
        """For orderly agent shutdown/testing; never called by the HTTP request thread."""
        if self._thread: self._thread.join(timeout)
