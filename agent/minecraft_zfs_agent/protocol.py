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


def validate_request(value, now):
    common = {'protocol', 'id', 'phase', 'created_ms', 'deadline_ms'}
    if not isinstance(value, dict):
        raise ValueError('request must be an object')
    phase = value.get('phase')
    extras = {'FROZEN':set(), 'COMMIT':{'committed_ms'}, 'CANCEL':{'cancelled_ms'}}
    if not isinstance(phase, str) or phase not in extras or set(value) != common | extras[phase]:
        raise ValueError('unknown phase or request fields')
    if type(value['protocol']) is not int or value['protocol'] != 1:
        raise ValueError('unsupported protocol')
    identifier = value['id']
    if not isinstance(identifier, str): raise ValueError('id must be a UUID')
    try:
        if str(uuid.UUID(identifier)) != identifier: raise ValueError('id must be canonical lowercase UUID')
    except (ValueError, AttributeError) as error:
        raise ValueError('invalid UUID') from error
    for name in {'created_ms', 'deadline_ms'} | extras[phase]:
        if type(value[name]) is not int or value[name] < 0 or value[name] > 2**63-1:
            raise ValueError('timestamp must be a nonnegative integer')
    created, deadline = value['created_ms'], value['deadline_ms']
    if created > now+1000 or not 0 < deadline-created <= MAX_FREEZE_MS:
        raise ValueError('invalid request window')
    if phase == 'FROZEN' and now >= deadline:
        raise ValueError('FROZEN request expired')
    if phase == 'COMMIT':
        committed = value['committed_ms']
        if not created <= committed <= deadline or committed > now+1000 or now > deadline+COMMIT_GRACE_MS:
            raise ValueError('COMMIT outside permitted window')
    if phase == 'CANCEL' and (value['cancelled_ms'] < created or value['cancelled_ms'] > now+1000 or now > deadline+COMMIT_GRACE_MS):
        raise ValueError('invalid cancellation time')
    return value
