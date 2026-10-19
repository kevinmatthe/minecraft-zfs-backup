"""Persistent, fenced local checkpoint lifecycle and hold-aware retention."""
from datetime import datetime, timezone
import logging
import os
from .protocol import atomic_json, read_json, validate_request, COMMIT_GRACE_MS


class Coordinator:
    def __init__(self, config, backend, clock, publish_heartbeat=True):
        self.config, self.backend, self.clock = config, backend, clock
        self.publish_heartbeat = publish_heartbeat
        config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = config.state_dir.lstat()
        if config.state_dir.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('state_dir must be agent-owned with permissions 0700')
        self.ledger_path = config.state_dir/'ledger.json'
        try: self.ledger = read_json(self.ledger_path, limit=1024*1024)
        except FileNotFoundError:
            self.ledger = {'version':1, 'pending':None, 'completed':{}, 'last_accepted_ms':None}
        if self.ledger.get('version') != 1 or set(self.ledger) != {'version','pending','completed','last_accepted_ms'}:
            raise ValueError('invalid ledger; manual review required')
        self._resume = True
        self._retention_due = True

    def expire(self):
        pending = self.ledger['pending']
        if pending:
            grace = COMMIT_GRACE_MS if pending.get('ready') else 0
            if self.clock() > pending['deadline_ms'] + grace:
                self.cleanup()

    def save(self): atomic_json(self.ledger_path, self.ledger, mode=0o600)

    def respond(self, identifier, phase, **extra):
        atomic_json(self.config.mailbox_path/'response.json',
                    dict(protocol=1, id=identifier, phase=phase, updated_ms=self.clock(), **extra))

    def request(self):
        return validate_request(read_json(self.config.mailbox_path/'request.json'), self.clock())

    @staticmethod
    def matches(pending, message):
        return all(pending[field] == message[field] for field in ('id','created_ms','deadline_ms'))

    def cleanup(self):
        pending = self.ledger['pending']
        if pending:
            name = 'pending-'+pending['snapshot']
            actual = dict(self.backend.snapshots(self.config.source_dataset)).get(name)
            if actual is not None and pending.get('guid') and actual != pending['guid']:
                raise RuntimeError('pending GUID changed; refusing to destroy replacement')
            self.backend.destroy_pending(name)
            self.ledger['pending'] = None
            self.save()

    def complete(self, pending):
        """A durable publish intent contains a previously validated COMMIT and GUID."""
        snapshots = dict(self.backend.snapshots(self.config.source_dataset))
        name = pending['snapshot']; temporary = 'pending-'+name
        if snapshots.get(name) == pending['guid']:
            if temporary in snapshots:
                raise RuntimeError('both pending and published snapshots exist; refusing ambiguity')
        elif name in snapshots:
            raise RuntimeError('published snapshot GUID conflict')
        elif snapshots.get(temporary) == pending['guid']:
            self.backend.rename(temporary, name)
        else:
            raise RuntimeError('recorded pending snapshot missing or GUID changed')
        record = {key:pending[key] for key in ('id','created_ms','deadline_ms','snapshot','guid')}
        self.ledger['completed'][pending['id']] = record
        # Keep idempotence records bounded. Ancient requests fail timestamp validation.
        if len(self.ledger['completed']) > 256:
            oldest = next(iter(self.ledger['completed']))
            del self.ledger['completed'][oldest]
        self.ledger['pending'] = None
        self.save()
        self._retention_due = True
        self.respond(pending['id'], 'COMMITTED', snapshot=name)

    def tick(self):
        ready = True
        try: self.backend.preflight()
        except Exception:
            ready = False
            logging.exception('source preflight failed')
        if self.publish_heartbeat:
            atomic_json(self.config.mailbox_path/'heartbeat.json', dict(protocol=1, updated_ms=self.clock(), ready=ready))
        if not ready: return
        try:
            if self._resume:
                from .zfs import PENDING
                active = self.ledger['pending']
                protected = 'pending-'+active['snapshot'] if active else None
                for name, _ in self.backend.snapshots(self.config.source_dataset):
                    if PENDING.fullmatch(name) and name != protected:
                        self.backend.destroy_pending(name)
                self._resume = False
            pending = self.ledger['pending']
            if pending and pending.get('publish_intent'):
                self.complete(pending)
            try: message = self.request()
            except (ValueError, OSError):
                self.expire()
                return
            completed = self.ledger['completed'].get(message['id'])
            if completed:
                if message['phase'] == 'COMMIT' and self.matches(completed, message):
                    self.respond(message['id'], 'COMMITTED', snapshot=completed['snapshot'])
                return
            pending = self.ledger['pending']
            if pending and not self.matches(pending, message):
                # Only a newer valid FROZEN can supersede; unrelated COMMIT/CANCEL cannot.
                if message['phase']=='FROZEN' and message['id'] != pending['id'] and message['created_ms'] >= pending['created_ms']:
                    self.cleanup(); pending=None
                else:
                    if self.clock() > pending['deadline_ms']+COMMIT_GRACE_MS: self.cleanup()
                    return
            if message['phase']=='CANCEL':
                if pending: self.cleanup()
                return
            if message['phase']=='COMMIT':
                if not pending or not pending.get('guid'):
                    self.respond(message['id'],'ERROR',error='no matching recorded pending snapshot')
                    return
                snapshots=dict(self.backend.snapshots(self.config.source_dataset))
                if snapshots.get('pending-'+pending['snapshot']) != pending['guid']:
                    raise RuntimeError('pending GUID mismatch; refusing publication')
                pending['publish_intent'] = dict(committed_ms=message['committed_ms'], received_ms=self.clock())
                self.save()
                self.complete(pending)
                return
            if pending is None:
                previous=self.ledger['last_accepted_ms']
                if previous is not None and self.clock()-previous < self.config.min_interval_seconds*1000:
                    self.respond(message['id'],'ERROR',error='host minimum snapshot interval has not elapsed')
                    return
                name=datetime.fromtimestamp(message['created_ms']/1000,timezone.utc).strftime('mc-hourly-%Y%m%dT%H%M%SZ')
                snapshots=dict(self.backend.snapshots(self.config.source_dataset))
                if name in snapshots or 'pending-'+name in snapshots:
                    self.respond(message['id'],'ERROR',error='snapshot name collision')
                    return
                pending={key:message[key] for key in ('id','created_ms','deadline_ms')}
                pending.update(snapshot=name, guid=None)
                self.ledger['pending']=pending
                self.ledger['last_accepted_ms']=self.clock()
                self.save()  # Intent exists durably before ZFS snapshot command.
            snapshots=dict(self.backend.snapshots(self.config.source_dataset))
            if 'pending-'+pending['snapshot'] not in snapshots:
                self.backend.snapshot('pending-'+pending['snapshot'])
                snapshots=dict(self.backend.snapshots(self.config.source_dataset))
            guid=snapshots.get('pending-'+pending['snapshot'])
            if not guid or (pending['guid'] and pending['guid'] != guid):
                raise RuntimeError('pending snapshot GUID mismatch')
            pending['guid']=guid; self.save()
            try: current=self.request()
            except (ValueError,OSError):
                if pending.get('ready'): self.expire()
                else: self.cleanup()
                return
            if current['phase']!='FROZEN' or current != message:
                if self.matches(pending,current) and current['phase']=='COMMIT':
                    return  # Process the valid commit on the next poll.
                if pending.get('ready') and not self.matches(pending,current):
                    self.expire()  # Unrelated messages cannot cancel an acknowledged checkpoint.
                    return
                self.cleanup()
                return
            pending['ready']=True
            self.save()
            self.respond(message['id'],'SNAPSHOT_READY',snapshot=pending['snapshot'])
        except Exception as error:
            logging.exception('checkpoint coordination failed')
            pending=self.ledger['pending']
            if pending: self.respond(pending['id'],'ERROR',error=str(error)[:512])
        finally:
            if self._retention_due and self.ledger['pending'] is None:
                result = dict(updated_ms=self.clock(), success=False)
                try:
                    result.update(self.backend.prune())
                    result['success'] = True
                    self._retention_due = False
                except Exception as error:
                    result['error'] = str(error)[:512]
                    logging.exception('local retention failed; published snapshots remain valid')
                try: atomic_json(self.config.state_dir/'retention.json', result, mode=0o600)
                except OSError: logging.exception('retention status persistence failed')

