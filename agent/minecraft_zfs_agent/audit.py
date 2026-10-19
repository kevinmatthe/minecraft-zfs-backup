"""Private, append-only rollback evidence; never restored or automatically trimmed.

Events are at-least-once intent/outcome evidence. Each append has a unique event_id;
a job_id groups retries, explicit resumption and process-restart resumption.
Actor claims come from an authenticated API client, not independent authentication.
"""
import fcntl
import json
import os
from pathlib import Path
import stat
import time
import uuid
from .protocol import APIError


class AuditUnavailable(APIError):
    def __init__(self):
        super().__init__(503,'audit_unavailable','Durable rollback audit is unavailable; operation paused')


def normalize_actor(value=None):
    if value is None:return {'kind':'api','name':'authenticated-api','provenance':'server_default'}
    if not isinstance(value,dict):raise APIError(400,'invalid_actor','Invalid rollback actor claim')
    kind=value.get('kind');fields={'kind','name','uuid'} if kind=='player' else {'kind','name'}
    name=value.get('name')
    if (kind not in ('player','console') or set(value)!=fields or not isinstance(name,str)
            or not 1<=len(name)<=128 or not name.isprintable()):
        raise APIError(400,'invalid_actor','Invalid rollback actor claim')
    result={'kind':kind,'name':name,'provenance':'authenticated_client_claim'}
    if kind=='player':
        try:
            if not isinstance(value['uuid'],str) or str(uuid.UUID(value['uuid']))!=value['uuid']:raise ValueError()
        except (ValueError,AttributeError):raise APIError(400,'invalid_actor','Canonical player UUID required') from None
        result['uuid']=value['uuid']
    return result


class AuditLog:
    def __init__(self,state_dir,world_path,source_dataset,clock=None):
        self.directory=Path(state_dir);self.path=self.directory/'rollback-audit.jsonl'
        self.source_dataset=source_dataset;self.clock=clock or (lambda:time.time_ns()//1_000_000)
        try:
            world=Path(world_path).resolve();resolved=self.directory.resolve()
            if resolved==world or world in resolved.parents or resolved!=self.directory:raise ValueError()
            fd=self._directory()
            os.close(fd)
        except Exception:raise AuditUnavailable() from None
    @staticmethod
    def _secure(info,directory=False):
        expected=0o700 if directory else 0o600
        if (info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=expected
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or (not directory and info.st_nlink!=1)):raise ValueError()
    def _directory(self):
        fd=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:self._secure(os.fstat(fd),True)
        except BaseException:os.close(fd);raise
        return fd
    def append(self,event,*,actor=None,**fields):
        record=dict(event_id=str(uuid.uuid4()),timestamp_ms=self.clock(),event=event,
                    actor=actor or normalize_actor(),source_dataset=self.source_dataset,**fields)
        try:
            raw=(json.dumps(record,ensure_ascii=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')
            directory=self._directory()
            try:
                fd=os.open(self.path.name,os.O_RDWR|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=directory)
                try:
                    self._secure(os.fstat(fd));fcntl.flock(fd,fcntl.LOCK_EX)
                    # A torn append is evidence requiring administrator attention. Never
                    # truncate historical bytes or append a record to an incomplete line.
                    size=os.fstat(fd).st_size
                    if size and os.pread(fd,1,size-1)!=b'\n':raise ValueError()
                    remaining=memoryview(raw)
                    while remaining:
                        count=os.write(fd,remaining)
                        if count<=0:raise OSError()
                        remaining=remaining[count:]
                    os.fsync(fd);os.fsync(directory)
                finally:os.close(fd)
            finally:os.close(directory)
            return record['event_id']
        except Exception:raise AuditUnavailable() from None
