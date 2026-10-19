"""Strict v1 parsing and durable atomic publication."""
import json
import os
from pathlib import Path
import stat
import uuid

MAX_MESSAGE = 16 * 1024
MAX_FREEZE_MS = 60000
COMMIT_GRACE_MS = 60000


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def read_json(path, limit=MAX_MESSAGE):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('message must be a regular file no larger than 16 KiB')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit+1)
        if len(raw) > limit:
            raise ValueError('message too large')
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError('invalid UTF-8 JSON') from error
        if not isinstance(value, dict):
            raise ValueError('message must be an object')
        return value
    finally:
        os.close(fd)


def atomic_json(path, value, mode=0o644):
    path = Path(path)
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, separators=(',', ':'), allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class APIError(Exception):
    def __init__(self,status,code,message):
        self.status,self.code,self.message=status,code,message
        super().__init__(message)


def object_fields(value,fields):
    if not isinstance(value,dict) or set(value)!=set(fields):raise APIError(400,'invalid_fields','Unexpected or missing JSON fields')
    return value


def identifier(value):
    try:
        if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError()
    except (ValueError,AttributeError):raise APIError(400,'invalid_id','Canonical lowercase UUID required') from None
    return value


def timestamp(value):
    if type(value) is not int or not 0<=value<=2**63-1:raise APIError(400,'invalid_timestamp','Integer Unix milliseconds required')
    return value


def parse_json(raw):
    if len(raw)>MAX_MESSAGE:raise APIError(400,'body_too_large','JSON request exceeds16KiB')
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value,dict):raise ValueError()
    except (ValueError,UnicodeError,RecursionError):raise APIError(400,'invalid_json','Valid UTF-8 JSON object required') from None
    return value
