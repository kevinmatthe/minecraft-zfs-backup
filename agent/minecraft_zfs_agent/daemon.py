"""Daemon process ownership and independent readiness heartbeat."""
import fcntl
import logging
import os
from pathlib import Path
import signal
import threading
import time

from .coordinator import Coordinator
from .protocol import atomic_json, read_json
from .zfs import ZFS


def now_ms(): return time.time_ns()//1_000_000


class InstanceLock:
    def __init__(self, state_dir): self.path=Path(state_dir)/'agent.lock'; self.fd=None
    def __enter__(self):
        self.fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try: fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd);self.fd=None
            raise RuntimeError('another host agent holds the instance lock') from None
        return self
    def __exit__(self,*_):
        if self.fd is not None:os.close(self.fd);self.fd=None


class Heartbeat(threading.Thread):
    """Publication cannot stall while a ZFS command or preflight is blocked."""
    def __init__(self, backend, mailbox, stop, interval=.5):
        super().__init__(name='heartbeat',daemon=True)
        self.backend,self.mailbox,self.stop,self.interval=backend,mailbox,stop,interval
        self._ready=False;self._checked=0
    def probe(self):
        while not self.stop.is_set():
            try:
                self.backend.preflight()
                self._ready=True
            except Exception:
                self._ready=False
                logging.debug('heartbeat preflight unavailable',exc_info=True)
            self._checked=time.monotonic()
            self.stop.wait(.5)
    def publish(self, ready):
        atomic_json(self.mailbox/'heartbeat.json',dict(protocol=1,updated_ms=now_ms(),ready=ready))
    def run(self):
        probe=threading.Thread(target=self.probe,name='source-preflight',daemon=True)
        probe.start()
        try:
            while not self.stop.is_set():
                try:self.publish(self._ready and time.monotonic()-self._checked < 2)
                except OSError:logging.exception('heartbeat publication failed')
                self.stop.wait(self.interval)
        finally:
            try:self.publish(False)
            except OSError:logging.exception('final unavailable heartbeat publication failed')


def run(config):
    if os.geteuid()!=0:raise RuntimeError('run must execute as root to protect the pending ledger')
    config.state_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=config.state_dir.lstat()
    if config.state_dir.is_symlink() or info.st_uid!=0 or info.st_mode&0o077:
        raise ValueError('state_dir must be root-owned and mode0700')
    if not config.mailbox_path.is_dir() or config.mailbox_path.is_symlink():
        raise ValueError('mailbox_path must be a pre-created shared directory')
    stop=threading.Event()
    def terminate(*_):stop.set()
    previous={number:signal.signal(number,terminate) for number in (signal.SIGTERM,signal.SIGINT)}
    try:
        with InstanceLock(config.state_dir):
            backend=ZFS(config,stop)
            coordinator=Coordinator(config,backend,now_ms,publish_heartbeat=False)
            heartbeat=Heartbeat(backend,config.mailbox_path,stop)
            heartbeat.start()
            logging.info('host coordinator started')
            try:
                while not stop.is_set():
                    coordinator.tick()
                    stop.wait(config.poll_seconds)
            finally:
                stop.set();heartbeat.join(2)
                logging.info('host coordinator stopped; pending lifecycle is durably recoverable')
    finally:
        for number,handler in previous.items():signal.signal(number,handler)


def status(config):
    result={}
    for key,path in [('heartbeat',config.mailbox_path/'heartbeat.json'),
                     ('ledger',config.state_dir/'ledger.json'),
                     ('retention',config.state_dir/'retention.json')]:
        try:result[key]=read_json(path,limit=1024*1024)
        except FileNotFoundError:result[key]=None
    heartbeat=result['heartbeat']
    result['ready']=bool(heartbeat and heartbeat.get('ready') is True and
                         type(heartbeat.get('updated_ms')) is int and
                         -1000 <= now_ms()-heartbeat['updated_ms'] <= 5000)
    return result
