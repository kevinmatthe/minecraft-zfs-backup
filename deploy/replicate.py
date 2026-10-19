#!/usr/bin/env python3
"""Optional standalone, non-forced ZFS synchronization. No Minecraft console access."""
from dataclasses import dataclass
import argparse
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time
import uuid

OWNED = re.compile(r'mc-hourly-\d{8}T\d{6}Z\Z')
DATASET = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]*(?:/[A-Za-z0-9_.:-]+)+\Z')


@dataclass(frozen=True)
class Config:
    source_dataset: str
    target_dataset: str
    state_dir: Path
    keep: int = 24

    def __post_init__(self):
        for value in (self.source_dataset, self.target_dataset):
            if not isinstance(value, str) or not DATASET.fullmatch(value):
                raise ValueError('invalid administrator dataset name')
        source, target = self.source_dataset, self.target_dataset
        if source == target or source.startswith(target+'/') or target.startswith(source+'/'):
            raise ValueError('source and target must be distinct unrelated datasets')
        object.__setattr__(self, 'state_dir', Path(self.state_dir))
        if not self.state_dir.is_absolute() or '..' in self.state_dir.parts:
            raise ValueError('state_dir must be an absolute normalized private path')
        if type(self.keep) is not int or self.keep < 1:
            raise ValueError('keep must be a positive integer')

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.is_absolute(): raise ValueError('configuration path must be absolute')
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('configuration must be a root-owned regular file, not group/world writable')
        try: return cls(**read_json(path))
        except TypeError as error: raise ValueError('invalid configuration fields: '+str(error)) from error


def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024*1024:
            raise ValueError('JSON state/config must be a regular file at most 1 MiB')
        with os.fdopen(fd, 'rb', closefd=False) as stream: value=json.load(stream)
        if not isinstance(value,dict): raise ValueError('JSON object required')
        return value
    finally: os.close(fd)


def atomic_json(path, value):
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:temporary.unlink(missing_ok=True)


class InstanceLock:
    def __init__(self,state_dir):self.path=state_dir/'replication.lock';self.fd=None
    def __enter__(self):
        self.fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd);self.fd=None
            raise RuntimeError('another synchronization task holds the lock') from None
        return self
    def __exit__(self,*_):
        if self.fd is not None:os.close(self.fd);self.fd=None


class ZFSError(RuntimeError):
    def __init__(self,args,stderr):
        self.stderr=stderr
        super().__init__('zfs '+str(args[:2])+': '+stderr)


class ZFSBackend:
    def __init__(self,config):self.config=config
    def run(self,*args,timeout=30):
        result=subprocess.run(['zfs',*args],capture_output=True,text=True,timeout=timeout)
        if result.returncode:raise ZFSError(args,result.stderr.strip())
        return result.stdout.strip()
    def exists(self,dataset):
        return dataset in self.run('list','-H','-o','name','-t','filesystem,volume').splitlines()
    def check_source(self):
        if self.run('get','-H','-o','value','type',self.config.source_dataset)!='filesystem':
            raise RuntimeError('source must be an existing filesystem dataset')
    def check_target(self):
        target=self.config.target_dataset
        if not self.exists(target):return
        properties=dict(line.split('\t',1) for line in self.run('get','-H','-o','property,value',
                        'mounted,readonly,canmount,mountpoint',target).splitlines())
        if properties!=dict(mounted='no',readonly='on',canmount='off',mountpoint='none'):
            raise RuntimeError('existing target must be unmounted, readonly=on, canmount=off, mountpoint=none')
    def snapshots(self,dataset,missing_ok=False):
        if dataset not in (self.config.source_dataset,self.config.target_dataset):raise ValueError('unconfigured dataset')
        if missing_ok and not self.exists(dataset):return []
        output=self.run('list','-H','-p','-t','snapshot','-d','1','-s','creation','-o','name,guid',dataset)
        return [(full.split('@',1)[1],guid) for full,guid in
                (line.split('\t') for line in output.splitlines()) if full.startswith(dataset+'@')]
    def holds(self,dataset,snapshot):
        output=self.run('holds','-H',dataset+'@'+snapshot)
        return {line.split('\t')[1] for line in output.splitlines()}
    def hold(self,dataset,snapshot,tag):
        if tag not in self.holds(dataset,snapshot):self.run('hold',tag,dataset+'@'+snapshot)
    def release(self,dataset,snapshot,tag):
        if tag in self.holds(dataset,snapshot):self.run('release',tag,dataset+'@'+snapshot)
    def transfer(self,snapshot,base):
        if not OWNED.fullmatch(snapshot):raise ValueError('only published managed checkpoints may be sent')
        send=['zfs','send','-c']
        if base:send+=['-i',self.config.source_dataset+'@'+base]
        send+=[self.config.source_dataset+'@'+snapshot]
        receive=['zfs','receive','-u','-o','mountpoint=none','-o','canmount=off','-o','readonly=on',self.config.target_dataset]
        with tempfile.TemporaryFile() as send_err,tempfile.TemporaryFile() as receive_err:
            sender=subprocess.Popen(send,stdout=subprocess.PIPE,stderr=send_err)
            receiver=None
            try:
                receiver=subprocess.Popen(receive,stdin=sender.stdout,stdout=subprocess.DEVNULL,stderr=receive_err)
                sender.stdout.close()
                receiver.wait(timeout=1800);sender.wait(timeout=30)
                if receiver.returncode or sender.returncode:
                    send_err.seek(0);receive_err.seek(0)
                    raise RuntimeError('ZFS stream failed: '+(send_err.read()+receive_err.read()).decode(errors='replace')[-4096:])
            finally:
                for process in (sender,receiver):
                    if process is not None and process.poll() is None:process.kill();process.wait()
                sender.stdout.close()
    def destroy_destination(self,snapshot):
        if not OWNED.fullmatch(snapshot):raise ValueError('refusing foreign snapshot destruction')
        try:self.run('destroy',self.config.target_dataset+'@'+snapshot)
        except ZFSError as error:
            busy=any(reason in error.stderr.lower() for reason in
                     ('dataset is busy','snapshot is held','has dependent clones'))
            if busy or self.holds(self.config.target_dataset,snapshot):return False
            raise
        return True


class Synchronizer:
    def __init__(self,config,backend):
        self.config,self.backend=config,backend
        identity=(config.source_dataset+'\0'+config.target_dataset).encode()
        self.tag='mc-zfs-sync-'+hashlib.sha256(identity).hexdigest()[:16]
    def hold_checked(self,snapshot,guid):
        source=self.config.source_dataset
        if dict(self.backend.snapshots(source)).get(snapshot)!=guid:
            raise RuntimeError('source snapshot disappeared or GUID changed before hold')
        existed=self.tag in self.backend.holds(source,snapshot)
        self.backend.hold(source,snapshot,self.tag)
        if dict(self.backend.snapshots(source)).get(snapshot)!=guid:
            raise RuntimeError('source snapshot GUID changed while acquiring hold')
        return not existed
    def sync(self):
        backend=self.backend;source=self.config.source_dataset;target=self.config.target_dataset
        backend.check_source();backend.check_target()
        source_snapshots=backend.snapshots(source)
        owned=[name for name,_ in source_snapshots if OWNED.fullmatch(name)]
        if not owned:raise RuntimeError('no published managed source checkpoint exists')
        snapshot=owned[-1];guid=dict(source_snapshots)[snapshot]
        destination=backend.snapshots(target,missing_ok=True)
        base=None
        if destination:
            base,base_guid=destination[-1]
            if dict(source_snapshots).get(base)!=base_guid:
                raise RuntimeError('latest target snapshot has no identical source GUID; refusing overwrite/rollback')
            if [name for name,_ in source_snapshots].index(base)>[name for name,_ in source_snapshots].index(snapshot):
                raise RuntimeError('target is ahead of latest managed source checkpoint; refusing rollback')
            self.hold_checked(base,base_guid)
        try:
            self.hold_checked(snapshot,guid)
            if base!=snapshot:
                backend.transfer(snapshot,base)
            backend.check_target()
            current_source=dict(backend.snapshots(source))
            destination=backend.snapshots(target)
            if current_source.get(snapshot)!=guid or dict(destination).get(snapshot)!=guid:
                raise RuntimeError('post-receive GUID mismatch; retention and hold rotation disabled')
            if not destination or destination[-1]!=(snapshot,guid):
                raise RuntimeError('target changed concurrently; retention and hold rotation disabled')
            # The new common base is held before any previous task-owned hold is released.
            self.hold_checked(snapshot,guid)
            for previous,_ in backend.snapshots(source):
                if previous!=snapshot and self.tag in backend.holds(source,previous):
                    backend.release(source,previous,self.tag)
            result=dict(snapshot=snapshot,guid=guid,base=base,
                        mode='already-present' if base==snapshot else ('incremental' if base else 'full'),
                        hold_tag=self.tag,deleted=[],held=[])
            managed=[name for name,_ in destination if OWNED.fullmatch(name)]
            for old in managed[:-self.config.keep]:
                if old==snapshot:continue
                if backend.holds(target,old) or not backend.destroy_destination(old):result['held'].append(old)
                else:result['deleted'].append(old)
            return result
        except BaseException:
            # Receive may have completed even when a command/verification failed.
            # Keep both holds until the next GUID-verified success reconciles them.
            logging.info('sync incomplete; source candidate and prior base holds retained for retry')
            raise


def status(config):
    try:return read_json(config.state_dir/'replication.json')
    except FileNotFoundError:return None


def execute(config,backend=None):
    if os.geteuid()!=0:raise RuntimeError('synchronization must run as root')
    config.state_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=config.state_dir.lstat()
    if config.state_dir.is_symlink() or info.st_uid!=0 or info.st_mode&0o077:
        raise ValueError('state_dir must be root-owned and mode0700')
    with InstanceLock(config.state_dir):
        result=dict(started_ms=time.time_ns()//1_000_000,success=False)
        try:
            result.update(Synchronizer(config,backend or ZFSBackend(config)).sync())
            result['success']=True
            return result
        except BaseException as error:
            result['error']=str(error)[:4096]
            raise
        finally:
            result['finished_ms']=time.time_ns()//1_000_000
            atomic_json(config.state_dir/'replication.json',result)


def main():
    os.environ['PATH']='/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('mode',choices=('run','status'))
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    def terminate(signum,_):raise InterruptedError('signal '+str(signum))
    signal.signal(signal.SIGTERM,terminate);signal.signal(signal.SIGINT,terminate)
    try:
        config=Config.load(args.config)
        print(json.dumps(execute(config) if args.mode=='run' else status(config),indent=2))
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        logging.error('%s',error);raise SystemExit(1) from None


if __name__=='__main__':main()
