"""HTTP process ownership and startup; no heartbeat or shared-file polling."""
import fcntl
import logging
import os
from pathlib import Path
import signal
import threading
import time
from .config import secret
from .coordinator import CheckpointService
from .protocol import read_json
from .zfs import ZFS


def now_ms():return time.time_ns()//1_000_000


class InstanceLock:
    def __init__(self,state_dir):self.path=Path(state_dir)/'agent.lock';self.fd=None
    def __enter__(self):
        self.fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd);self.fd=None;raise RuntimeError('another companion holds the state lock') from None
        return self
    def __exit__(self,*_):
        if self.fd is not None:os.close(self.fd);self.fd=None


def run(config):
    if os.geteuid()!=0:raise RuntimeError('run requires root for private durable state')
    config.state_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=config.state_dir.lstat()
    if config.state_dir.is_symlink() or info.st_uid!=0 or info.st_mode&0o077:raise ValueError('state must be root-owned mode0700')
    token=secret(config.api_token_file)
    from .http_service import AgentHTTPServer
    with InstanceLock(config.state_dir):
        backend=ZFS(config);service=CheckpointService(config,backend,now_ms)
        manager=None
        if config.crafty:
            from .rollback import RollbackManager
            manager=RollbackManager(config,backend,service,clock=now_ms)
            service.rollback=manager
        server=AgentHTTPServer((config.listen_host,config.listen_port),service,token,manager)
        stopping=threading.Event()
        def terminate(*_):
            if not stopping.is_set():
                stopping.set();threading.Thread(target=server.shutdown,daemon=True).start()
        previous={number:signal.signal(number,terminate) for number in (signal.SIGTERM,signal.SIGINT)}
        try:
            logging.info('HTTP companion listening on configured private interface')
            server.serve_forever(poll_interval=.2)
        finally:
            service.close();server.server_close()
            if manager and hasattr(manager,'close'):manager.close()
            for number,handler in previous.items():signal.signal(number,handler)


def status(config):
    result={}
    for name in ('ledger','retention','rollback'):
        try:result[name]=read_json(config.state_dir/(name+'.json'),limit=2*1024*1024)
        except FileNotFoundError:result[name]=None
    return result
