"""Administrator configuration, never derived from mailbox messages."""
from dataclasses import dataclass
from pathlib import Path
import math
import re
from .protocol import read_json


@dataclass(frozen=True)
class Config:
    source_dataset: str
    world_path: Path
    mailbox_path: Path
    state_dir: Path
    keep: int = 24
    poll_seconds: float = 0.2
    min_interval_seconds: float = 60

    def __post_init__(self):
        for name in ('source_dataset',):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*(?:/[A-Za-z0-9_.:-]+)+', value):
                raise ValueError('invalid dataset: '+name)
        for name in ('world_path', 'mailbox_path', 'state_dir'):
            value = Path(getattr(self, name))
            if not value.is_absolute() or '..' in value.parts:
                raise ValueError(name+' must be an absolute normalized path')
            object.__setattr__(self, name, value)
        if self.state_dir == self.mailbox_path or self.mailbox_path in self.state_dir.parents:
            raise ValueError('state_dir must be outside the game-writable mailbox')
        if type(self.keep) is not int or self.keep < 1:
            raise ValueError('keep must be a positive integer')
        for name, upper in [('poll_seconds', 1), ('min_interval_seconds', 86400*365)]:
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or value > upper or (name == 'poll_seconds' and value == 0):
                raise ValueError('invalid '+name)

    @classmethod
    def load(cls, path):
        path = Path(path)
        stat = path.stat(follow_symlinks=False)
        if stat.st_uid != 0 or stat.st_mode & 0o022:
            raise ValueError('configuration must be root-owned and not group/world writable')
        try: return cls(**read_json(path))
        except TypeError as error: raise ValueError('unknown or missing configuration fields: '+str(error)) from error
