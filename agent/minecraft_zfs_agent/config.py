"""Fixed administrator configuration for the authenticated HTTP companion."""
from dataclasses import dataclass
from pathlib import Path
import math
import re
import stat
from urllib.parse import urlsplit
from .protocol import read_json


def absolute(value):
    path=Path(value)
    if not path.is_absolute() or '..' in path.parts:raise ValueError('absolute normalized path required')
    return path


def secret(path):
    path=absolute(path);info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:
        raise ValueError('secret must be a root-owned private regular file')
    if info.st_size>16384:raise ValueError('secret is too large')
    value=path.read_text(encoding='utf-8').strip()
    if len(value)<32 or any(ch.isspace() for ch in value):raise ValueError('secret must be at least32 characters without whitespace')
    return value


@dataclass(frozen=True)
class CraftyConfig:
    base_url:str
    server_id:str
    token_file:Path
    verify_tls:bool=True
    ca_file:Path|None=None
    stop_timeout_seconds:float=180
    start_timeout_seconds:float=180
    def __post_init__(self):
        parsed=urlsplit(self.base_url)
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('invalid fixed Crafty URL')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',self.server_id):raise ValueError('invalid Crafty server ID')
        object.__setattr__(self,'base_url',self.base_url.rstrip('/'))
        object.__setattr__(self,'token_file',absolute(self.token_file))
        if self.ca_file is not None:object.__setattr__(self,'ca_file',absolute(self.ca_file))
        if type(self.verify_tls) is not bool:raise ValueError('verify_tls must be boolean')
        for name in ('stop_timeout_seconds','start_timeout_seconds'):
            value=getattr(self,name)
            if type(value) not in (int,float) or not math.isfinite(value) or not 1<=value<=3600:raise ValueError('invalid Crafty timeout')


@dataclass(frozen=True)
class Config:
    source_dataset:str
    world_path:Path
    state_dir:Path
    api_token_file:Path
    keep:int=24
    min_interval_seconds:float=60
    listen_host:str='0.0.0.0'
    listen_port:int=8765
    crafty:CraftyConfig|dict|None=None
    def __post_init__(self):
        if not isinstance(self.source_dataset,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*(?:/[A-Za-z0-9_.:-]+)+',self.source_dataset):raise ValueError('invalid source dataset')
        for name in ('world_path','state_dir','api_token_file'):object.__setattr__(self,name,absolute(getattr(self,name)))
        if self.state_dir==self.world_path or self.world_path in self.state_dir.parents:raise ValueError('state must be outside restored world')
        if self.api_token_file==self.world_path or self.world_path in self.api_token_file.parents:raise ValueError('API secret must be outside restored world')
        if type(self.keep) is not int or self.keep<1:raise ValueError('keep must be positive')
        if type(self.min_interval_seconds) not in (int,float) or not math.isfinite(self.min_interval_seconds) or not 0<=self.min_interval_seconds<=31536000:raise ValueError('invalid minimum interval')
        if not isinstance(self.listen_host,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,255}',self.listen_host):raise ValueError('invalid listen host')
        if type(self.listen_port) is not int or not 1<=self.listen_port<=65535:raise ValueError('invalid listen port')
        if isinstance(self.crafty,dict):object.__setattr__(self,'crafty',CraftyConfig(**self.crafty))
        elif self.crafty is not None and not isinstance(self.crafty,CraftyConfig):raise ValueError('invalid crafty configuration')
    @classmethod
    def load(cls,path):
        path=absolute(path);info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:raise ValueError('configuration must be a root-owned private regular file')
        try:return cls(**read_json(path))
        except TypeError as error:raise ValueError('unknown or missing configuration field') from error
