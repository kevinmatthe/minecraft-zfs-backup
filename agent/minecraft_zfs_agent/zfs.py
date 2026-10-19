"""Local ZFS snapshots and retention that respects external replication holds."""
import json
import logging
import os
import re
import subprocess
import threading

OWNED = re.compile(r'mc-hourly-\d{8}T\d{6}Z\Z')
PENDING = re.compile(r'pending-mc-hourly-\d{8}T\d{6}Z\Z')


class ZFSError(RuntimeError):
    def __init__(self, arguments, stderr):
        self.arguments, self.stderr = arguments, stderr
        super().__init__('zfs '+str(arguments[:2])+': '+stderr)


class ZFS:
    def __init__(self, config, stop=None):
        self.config = config
        self.stop = stop or threading.Event()

    def run(self, *args, timeout=30):
        if self.stop.is_set(): raise InterruptedError('agent shutdown requested')
        result = subprocess.run(['zfs', *args], capture_output=True, text=True, timeout=timeout)
        if result.returncode: raise ZFSError(args, result.stderr.strip())
        return result.stdout.strip()

    def preflight(self):
        world = self.config.world_path
        if str(world.resolve()) != str(world) or not os.path.ismount(world):
            raise RuntimeError('world_path must be an actual dedicated mount without symlink aliases')
        if self.run('get', '-H', '-o', 'value', 'mounted', self.config.source_dataset) != 'yes':
            raise RuntimeError('source dataset is not mounted')
        result = subprocess.run(['findmnt', '--json', '--target', str(world), '--output', 'SOURCE,TARGET,FSTYPE'],
                                capture_output=True, text=True, timeout=5, check=True)
        mounts = json.loads(result.stdout)['filesystems']
        if len(mounts)!=1 or mounts[0]['source'] != self.config.source_dataset or mounts[0]['target'] != str(world) or mounts[0]['fstype'] != 'zfs':
            raise RuntimeError('world_path is not the exact configured source ZFS mount')
        if not (world/'level.dat').is_file(): raise RuntimeError('source world level.dat is missing')

    def snapshots(self, dataset=None):
        if dataset is not None and dataset != self.config.source_dataset:
            raise ValueError('only the configured local source dataset is accessible')
        dataset=self.config.source_dataset
        output = self.run('list','-H','-p','-t','snapshot','-d','1','-s','creation','-o','name,guid',dataset)
        result=[]
        for line in output.splitlines():
            full, guid = line.split('\t')
            if full.startswith(dataset+'@'): result.append((full.split('@',1)[1],guid))
        return result

    def snapshot(self, name):
        if not (PENDING.fullmatch(name) or OWNED.fullmatch(name)): raise ValueError('invalid managed snapshot name')
        self.run('snapshot',self.config.source_dataset+'@'+name,timeout=10)

    def rename(self, old, new):
        if not PENDING.fullmatch(old) or not OWNED.fullmatch(new) or old != 'pending-'+new:
            raise ValueError('invalid publication rename')
        self.run('rename',self.config.source_dataset+'@'+old,self.config.source_dataset+'@'+new,timeout=10)

    def destroy_pending(self, name):
        if not PENDING.fullmatch(name): raise ValueError('refusing to destroy unrelated snapshot')
        if name in dict(self.snapshots()):
            self.run('destroy',self.config.source_dataset+'@'+name)

    def prune(self):
        owned=[name for name,_ in self.snapshots() if OWNED.fullmatch(name)]
        result={'deleted':[], 'held':[]}
        for name in owned[:-self.config.keep]:
            if self.stop.is_set(): raise InterruptedError('shutdown requested before retention')
            full=self.config.source_dataset+'@'+name
            if self.run('holds','-H',full):
                result['held'].append(name)
                logging.info('retained externally held snapshot %s',full)
                continue
            try:self.run('destroy',full)
            except ZFSError as error:
                # A native replication task may acquire its hold after the check.
                busy=any(reason in error.stderr.lower() for reason in
                         ('dataset is busy','snapshot is held','has dependent clones'))
                if busy or self.run('holds','-H',full):
                    result['held'].append(name)
                    logging.info('retained busy/held snapshot %s',full)
                    continue
                raise
            result['deleted'].append(name)
            logging.info('pruned owned local snapshot %s',full)
        return result

    def list_snapshots(self):return dict(self.snapshots())

    def guid(self,name):
        if not (OWNED.fullmatch(name) or PENDING.fullmatch(name)):raise ValueError('invalid managed snapshot name')
        value=dict(self.snapshots()).get(name)
        if value is None:
            from .protocol import APIError
            raise APIError(409,'snapshot_missing','Recorded snapshot is missing')
        return value

    def snapshot_path(self,name):
        if not OWNED.fullmatch(name):raise ValueError('invalid published snapshot')
        return self.config.world_path/'.zfs'/'snapshot'/name

    def hold(self,name,tag):
        self.guid(name)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',tag):raise ValueError('invalid hold tag')
        full=self.config.source_dataset+'@'+name
        tags={line.split('\t')[1] for line in self.run('holds','-H',full).splitlines()}
        if tag not in tags:self.run('hold',tag,full)

    def release(self,name,tag):
        self.guid(name)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',tag):raise ValueError('invalid hold tag')
        full=self.config.source_dataset+'@'+name
        tags={line.split('\t')[1] for line in self.run('holds','-H',full).splitlines()}
        if tag in tags:self.run('release',tag,full)

    def holds(self,name):
        self.guid(name)
        return {line.split('\t')[1] for line in self.run('holds','-H',self.config.source_dataset+'@'+name).splitlines()}
