"""Durable event-driven checkpoint service and rollback maintenance gate."""
from datetime import datetime,timezone
import copy
import logging
import os
import threading
import time
from .protocol import APIError,atomic_json,read_json,object_fields,identifier,timestamp
from .zfs import OWNED,PENDING
from .offline_lock import OfflineWorldLock


RETENTION_RETRY_SECONDS=60


class CheckpointService:
    def __init__(self,config,backend,clock=None):
        self.config,self.backend,self.clock=config,backend,clock or (lambda:time.time_ns()//1_000_000)
        self.lock=threading.RLock();self.maintenance_owner=None;self.rollback=None
        self.stop=threading.Event();self._timer=None;self._retention_timer=None
        config.state_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        info=config.state_dir.lstat()
        if config.state_dir.is_symlink() or info.st_uid!=os.geteuid() or info.st_mode&0o077:raise ValueError('state must be agent-owned mode0700')
        self.path=config.state_dir/'ledger.json'
        try:self.ledger=read_json(self.path,limit=2*1024*1024)
        except FileNotFoundError:self.ledger=dict(version=2,active=None,operations={},lifecycle={},recovery={},last_accepted_ms=None,last_success=None,last_error=None)
        if self.ledger.get('version')!=2:raise ValueError('use a new HTTP state directory')
        self._reconcile()
        self.retention_tick()
    def save(self):atomic_json(self.path,self.ledger,mode=0o600)
    def close(self):
        with self.lock:
            self.stop.set()
            if self._timer:self._timer.cancel()
            if self._retention_timer:self._retention_timer.cancel()
    @property
    def active_checkpoint(self):
        return self.ledger['operations'].get(self.ledger['active'])
    def _copy(self,value):return copy.deepcopy(value)
    def _public(self,op):
        return self._copy({k:v for k,v in op.items() if k!='publish_intent'})
    def _bounded(self,collection):
        while len(collection)>256:
            oldest=next(iter(collection))
            if collection is self.ledger['operations'] and oldest==self.ledger['active']:break
            del collection[oldest]
    def _preflight(self):
        try:self.backend.preflight()
        except Exception:raise APIError(503,'source_unavailable','Configured world dataset is unavailable') from None
    def _schedule(self):
        if self._timer:self._timer.cancel()
        active=self.active_checkpoint
        if not active or self.stop.is_set():return
        grace=60000 if active['state']=='SNAPSHOT_READY' else 0
        delay=max(.001,(active['deadline_ms']+grace-self.clock()+1)/1000)
        self._timer=threading.Timer(delay,self._timer_expire);self._timer.daemon=True;self._timer.start()
    def _timer_expire(self):
        try:self.expire()
        except Exception:logging.exception('checkpoint deadline cleanup failed')
    def _members(self,op):
        # Old version2 single-snapshot journals remain valid.
        return op.get('snapshots') or ([op] if op.get('snapshot') else [])
    def _cleanup(self,op,state):
        members=self._members(op);names=dict(self.backend.snapshots())
        for member in members:
            actual=names.get('pending-'+member['snapshot'])
            if actual is not None and member.get('guid') and actual!=member['guid']:
                raise APIError(409,'guid_conflict','Pending snapshot GUID changed')
        for member in members:self.backend.destroy_pending('pending-'+member['snapshot'])
        op.update(state=state,updated_ms=self.clock())
        if self.active_checkpoint is op:self.ledger['active']=None
        self.save();self._schedule()
    def expire(self):
        with self.lock:
            if self.stop.is_set():return
            active=self.active_checkpoint
            if active and not active.get('publish_intent'):
                grace=60000 if active['state']=='SNAPSHOT_READY' else 0
                if self.clock()>active['deadline_ms']+grace:self._cleanup(active,'EXPIRED')
            self._schedule()
    def _reconcile(self):
        with self.lock:
            active=self.active_checkpoint
            if active and active.get('publish_intent'):self._publish(active)
            for record in self.ledger['lifecycle'].values():
                if record.get('publish_intent'):self._publish(record)
            for record in self.ledger['operations'].values():
                if record.get('state')=='COMMITTED' and record.get('event')=='periodic_save' and any(m['snapshot'].startswith('mc-hourly-') for m in self._members(record)):
                    self.ledger['periodic_hour']=max(self.ledger.get('periodic_hour',-1),record.get('snapshot_hour',record['created_ms']//3600000))
            self.save()
            self.expire()
            active=self.active_checkpoint;protected={'pending-'+m['snapshot'] for m in self._members(active)} if active else set()
            for name,_ in self.backend.snapshots():
                if PENDING.fullmatch(name) and name not in protected:self.backend.destroy_pending(name)
            if active and active['state']=='SNAPSHOTTING':
                # No acknowledged frozen barrier survives an interrupted snapshot request.
                self._cleanup(active,'EXPIRED')
            self._schedule()
    def _window(self,body):
        object_fields(body,{'id','event','created_ms','deadline_ms'});identifier(body['id'])
        if body['event'] not in ('manual_save','periodic_save'):raise APIError(400,'invalid_event','Unsupported checkpoint event')
        created,deadline=timestamp(body['created_ms']),timestamp(body['deadline_ms'])
        if created>self.clock()+1000 or not 0<deadline-created<=60000:raise APIError(400,'invalid_window','Invalid freeze window')
    def _get(self,id):
        identifier(id);op=self.ledger['operations'].get(id)
        if not op:raise APIError(404,'unknown_checkpoint','Unknown checkpoint ID')
        return op
    def _phase_time(self,op,body,key,commit=False):
        object_fields(body,{key});value=timestamp(body[key])
        if key in op and value!=op[key]:raise APIError(409,'identity_conflict','Operation timing cannot change')
        if not op['created_ms']<=value<=op['deadline_ms'] or value>self.clock()+1000:raise APIError(400,'invalid_timestamp','Timestamp outside original window')
        if commit and self.clock()>op['deadline_ms']+60000:raise APIError(410,'expired','Commit receipt window expired')
        return value
    def prepare(self,body):
        with self.lock:
            self._window(body);self.expire()
            existing=self.ledger['operations'].get(body['id'])
            if existing:
                if any(existing[k]!=v for k,v in body.items()):raise APIError(409,'identity_conflict','Checkpoint identity cannot change')
                return self._public(existing)
            if self.maintenance_owner or self.active_checkpoint:raise APIError(409,'busy','Checkpoint or rollback already active')
            if self.clock()>=body['deadline_ms']:raise APIError(410,'expired','Freeze budget expired')
            self._preflight()
            if self.clock()>=body['deadline_ms']:raise APIError(410,'expired','Source preparation exceeded freeze budget')
            previous=self.ledger['last_accepted_ms']
            if previous is not None and self.clock()-previous<self.config.min_interval_seconds*1000:raise APIError(409,'rate_limited','Minimum checkpoint interval has not elapsed')
            op=body|dict(state='PREPARED',updated_ms=self.clock())
            self.ledger['operations'][body['id']]=op;self.ledger['active']=body['id'];self.ledger['last_accepted_ms']=self.clock()
            self._bounded(self.ledger['operations']);self.save();self._schedule();return self._public(op)
    def _name(self,created,family='hourly'):
        names=dict(self.backend.snapshots());stamp=created//1000
        for _ in range(300):
            name=datetime.fromtimestamp(stamp,timezone.utc).strftime('mc-'+family+'-%Y%m%dT%H%M%SZ')
            if name not in names and 'pending-'+name not in names:return name
            stamp+=1
        raise APIError(409,'name_conflict','No checkpoint name available')
    def snapshot(self,id,body):
        with self.lock:
            self.expire();op=self._get(id);frozen=self._phase_time(op,body,'frozen_ms')
            if op['state'] in ('COMMITTED','SNAPSHOT_READY'):return self._public(op)
            if op['state']=='EXPIRED':raise APIError(410,'expired','Checkpoint expired')
            if op['state']!='PREPARED' or self.ledger['active']!=id:raise APIError(409,'invalid_state','Checkpoint is not prepared')
            if self.clock()>=op['deadline_ms']:self._cleanup(op,'EXPIRED');raise APIError(410,'expired','Freeze budget expired')
            self._preflight()
            families=['live'] if op['event']=='periodic_save' else ['hourly']
            # The UTC family bucket is the frozen capture/barrier start, not a later rename.
            captured=self.clock();hour=captured//3600000
            if op['event']=='periodic_save' and hour>self.ledger.get('periodic_hour',-1):families.append('hourly')
            members=[dict(snapshot=self._name(captured,family),family=family) for family in families]
            op.update(state='SNAPSHOTTING',snapshot=members[0]['snapshot'],snapshot_hour=hour,snapshots=members,frozen_ms=frozen,updated_ms=self.clock());self.save()
            for member in members:
                if self.clock()>=op['deadline_ms']:self._cleanup(op,'EXPIRED');raise APIError(410,'expired','Snapshot completed after deadline')
                self.backend.snapshot('pending-'+member['snapshot']);member['guid']=self.backend.guid('pending-'+member['snapshot'])
                if hasattr(self.backend,'metadata'):member.update(self.backend.metadata('pending-'+member['snapshot']))
                op['guid']=members[0]['guid'];self.save()
            if self.clock()>=op['deadline_ms']:self._cleanup(op,'EXPIRED');raise APIError(410,'expired','Snapshot completed after deadline')
            op.update(state='SNAPSHOT_READY',updated_ms=self.clock());self.save()
            if self.clock()>=op['deadline_ms']:self._cleanup(op,'EXPIRED');raise APIError(410,'expired','Snapshot acknowledgement budget expired')
            self._schedule();return self._public(op)
    def _validate_members(self,op,published=False):
        names=dict(self.backend.snapshots())
        for member in self._members(op):
            name=member['snapshot'];pending='pending-'+name;guid=member['guid']
            if published and names.get(name)==guid:
                if pending in names:raise APIError(409,'guid_conflict','Ambiguous pending and published names')
            elif name in names or names.get(pending)!=guid:
                raise APIError(409,'guid_conflict','Recorded snapshot missing or changed')
        return names
    def _publish(self,op):
        self._validate_members(op,published=True)
        for member in self._members(op):
            name=member['snapshot'];names=dict(self.backend.snapshots())
            if name not in names:
                if names.get('pending-'+name)!=member['guid']:raise APIError(409,'guid_conflict','Pending GUID changed before rename')
                self.backend.rename('pending-'+name,name)
            if self.backend.guid(name)!=member['guid']:raise APIError(409,'guid_conflict','Published GUID changed')
        op.update(state='COMMITTED',updated_ms=self.clock());op.pop('publish_intent',None)
        if op.get('event')=='periodic_save' and any(m['snapshot'].startswith('mc-hourly-') for m in self._members(op)):
            self.ledger['periodic_hour']=max(self.ledger.get('periodic_hour',-1),op.get('snapshot_hour',op['created_ms']//3600000))
        if self.active_checkpoint is op:self.ledger['active']=None
        self.ledger['last_success']=self._public(op);self.save();self._schedule();self._prune()
    def commit(self,id,body):
        with self.lock:
            self.expire();op=self._get(id)
            if op['state']=='COMMITTED' or op.get('publish_intent'):
                object_fields(body,{'committed_ms'})
                if timestamp(body['committed_ms'])!=op['committed_ms']:raise APIError(409,'identity_conflict','Commit timing cannot change')
                if op.get('publish_intent'):self._publish(op)
                return self._public(op)
            committed=self._phase_time(op,body,'committed_ms',commit=True)
            if op['state']=='EXPIRED':raise APIError(410,'expired','Checkpoint expired')
            if op['state']!='SNAPSHOT_READY' or self.ledger['active']!=id:raise APIError(409,'invalid_state','No ready pending checkpoint')
            self._preflight()
            self._validate_members(op)
            # Blocking mount/GUID checks must not turn a late receipt into a new intent.
            self._phase_time(op,body,'committed_ms',commit=True)
            op.update(committed_ms=committed,publish_intent=True,state='PUBLISHING',updated_ms=self.clock());self.save();self._publish(op);return self._public(op)
    def cancel(self,id,body):
        with self.lock:
            self.expire();op=self._get(id);object_fields(body,{'cancelled_ms'});cancelled=timestamp(body['cancelled_ms'])
            if cancelled<op['created_ms'] or cancelled>self.clock()+1000:raise APIError(400,'invalid_timestamp','Invalid cancellation timestamp')
            if 'cancelled_ms' in op and op['cancelled_ms']!=cancelled:raise APIError(409,'identity_conflict','Cancellation timing cannot change')
            if op['state'] in ('COMMITTED','CANCELLED','EXPIRED'):return self._public(op)
            if op.get('publish_intent'):self._publish(op);return self._public(op)
            op['cancelled_ms']=cancelled;self._cleanup(op,'CANCELLED');return self._public(op)
    def enter_maintenance(self,job_id):
        with self.lock:
            identifier(job_id);self.expire()
            if self.maintenance_owner==job_id:return
            if self.maintenance_owner or self.active_checkpoint:raise APIError(409,'busy','Checkpoint or rollback already active')
            self.maintenance_owner=job_id
    def leave_maintenance(self,job_id):
        with self.lock:
            if self.maintenance_owner!=job_id:raise APIError(409,'maintenance_conflict','Maintenance belongs to another job')
            self.maintenance_owner=None
            self._trim_recovery()
    def _trim_recovery(self):
        collection=self.ledger['recovery']
        if len(collection)<=256:return
        names=dict(self.backend.snapshots())
        for key,record in list(collection.items()):
            if len(collection)<=256:break
            if record['id']==self.maintenance_owner or record['state']!='RECORDED':continue
            if record['snapshot'] in names and (not hasattr(self.backend,'holds') or self.backend.holds(record['snapshot'])):continue
            del collection[key]
        self.save()
    def record_recovery(self,event,job_id):
        with self.lock:
            if self.maintenance_owner!=job_id or event not in ('pre_rollback','rollback_completed'):raise APIError(409,'maintenance_required','Recovery requires matching maintenance')
            key=job_id+':'+event;record=self.ledger['recovery'].get(key)
            if record is None:
                record=dict(snapshot=self._name(self.clock()),event=event,id=job_id,updated_ms=self.clock(),state='CREATING')
                self.ledger['recovery'][key]=record;self.save()
            names=dict(self.backend.snapshots())
            if record['snapshot'] not in names:
                if record.get('guid'):raise APIError(409,'guid_conflict','Recorded recovery point is missing')
                self.backend.snapshot(record['snapshot']);names=dict(self.backend.snapshots())
            guid=names[record['snapshot']]
            if record.get('guid') and record['guid']!=guid:raise APIError(409,'guid_conflict','Recovery GUID changed')
            self.backend.hold(record['snapshot'],'minecraft-zfs-rollback-'+job_id)
            if self.backend.guid(record['snapshot'])!=guid:raise APIError(409,'guid_conflict','Recovery GUID changed while acquiring hold')
            record.update(guid=guid,state='RECORDED');self._trim_recovery();self.save();return self._copy(record)
    def lifecycle(self,body):
        with self.lock:
            object_fields(body,{'id','event','created_ms','backup_requested'});identifier(body['id']);created=timestamp(body['created_ms'])
            if body['event'] not in ('server_started','server_stopped') or type(body['backup_requested']) is not bool:raise APIError(400,'invalid_event','Invalid lifecycle event')
            if created>self.clock()+1000 or self.clock()-created>300000:raise APIError(400,'invalid_timestamp','Lifecycle event is stale or future')
            if body['event']=='server_started' and body['backup_requested']:raise APIError(400,'invalid_event','Started event cannot request backup')
            old=self.ledger['lifecycle'].get(body['id'])
            if old:
                if any(old[k]!=v for k,v in body.items()):raise APIError(409,'identity_conflict','Lifecycle identity cannot change')
                if old['state']!='CREATING':return self._public(old)
            record=old or body|dict(state='RECORDED',updated_ms=self.clock())
            self.ledger['lifecycle'][body['id']]=record;self._bounded(self.ledger['lifecycle']);self.save()
            if not body['backup_requested']:return self._public(record)
            self.expire()
            if self.maintenance_owner or self.active_checkpoint:
                record.update(state='SKIPPED',reason='maintenance_or_checkpoint_active');self.save();return self._public(record)
            self._preflight()
            try:world_lock=OfflineWorldLock(self.config.world_path);world_lock.__enter__()
            except OSError:
                record.update(state='SKIPPED',reason='world_not_confirmed_stopped',updated_ms=self.clock());self.save();return self._public(record)
            try:
                if not record.get('snapshot'):record.update(state='CREATING',snapshot=self._name(created));self.save()
                names=dict(self.backend.snapshots());name=record['snapshot'];pending='pending-'+name
                if pending not in names and name not in names:self.backend.snapshot(pending)
                names=dict(self.backend.snapshots());guid=names.get(pending,names.get(name))
                if record.get('guid') and record['guid']!=guid:raise APIError(409,'guid_conflict','Lifecycle snapshot GUID changed')
                world_lock.check()
                record.update(guid=guid,publish_intent=True,state='PUBLISHING',updated_ms=self.clock());self.save();self._publish(record)
                return self._public(record)
            finally:world_lock.__exit__(None,None,None)

    def retention_tick(self):
        with self.lock:
            if self.stop.is_set():return
            if self._retention_timer:self._retention_timer.cancel()
            delay=RETENTION_RETRY_SECONDS
            try:
                result=self._prune()
                next_expiry=result.get('next_expiry') if result else None
                if next_expiry is not None:delay=min(delay,max(.05,next_expiry-self.clock()/1000))
            except Exception:logging.exception('autonomous local TTL cleanup failed; retry scheduled')
            finally:
                if not self.stop.is_set():
                    self._retention_timer=threading.Timer(delay,self.retention_tick)
                    self._retention_timer.daemon=True;self._retention_timer.start()
    def _prune(self):
        if self.maintenance_owner:return
        protected={record['snapshot'] for record in self.ledger['recovery'].values() if record.get('state')=='CREATING'}
        protected.update(member['snapshot'] for collection in ('operations','lifecycle') for record in self.ledger[collection].values() if record.get('publish_intent') for member in self._members(record))
        try:result=self.backend.prune(protected=protected);result.update(success=True,updated_ms=self.clock())
        except Exception:result=dict(success=False,updated_ms=self.clock(),error='Local retention failed');logging.exception('local retention failed')
        atomic_json(self.config.state_dir/'retention.json',result,mode=0o600)
        return result
    def status(self):
        with self.lock:
            self.expire()
            try:self._preflight();ready=not bool(self.maintenance_owner)
            except APIError:ready=False
            result=dict(ready=ready,checkpoint=self._copy(self.active_checkpoint),last_success=self._copy(self.ledger['last_success']),last_error=self._copy(self.ledger['last_error']),rollback=None,updated_ms=self.clock())
        if self.rollback:result['rollback']=self.rollback.current()
        return result
    def list_snapshots(self):
        with self.lock:
            metadata={member['snapshot']:record.get('event','unknown') for collection in ('operations','lifecycle','recovery') for record in self.ledger[collection].values() for member in self._members(record)}
            entries=[]
            for name,guid in self.backend.snapshots():
                if not OWNED.fullmatch(name):continue
                row=dict(name=name,guid=guid,event=metadata.get(name,'unknown'),family='live' if name.startswith('mc-live-') else 'hourly')
                if hasattr(self.backend,'metadata'):row.update(self.backend.metadata(name))
                entries.append(row)
            return dict(snapshots=entries,updated_ms=self.clock())

Coordinator=CheckpointService
