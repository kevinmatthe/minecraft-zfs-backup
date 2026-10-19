"""Offline, descriptor-relative world restoration without a plain-copy fallback."""
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import uuid

_EXCLUDED = frozenset({'.zfs', 'session.lock'})
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME


class WorldLock:
    """Minecraft FileChannel locks use POSIX record locks, not BSD flock."""
    def __init__(self, world, cancel=None):
        self.world = Path(world)
        self.cancel = cancel
        self.fd = self.root_fd = None

    def __enter__(self):
        self.root_fd = os.open(self.world, _DIRECTORY)
        try:
            self.fd = os.open('session.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                              dir_fd=self.root_fd)
            if not stat.S_ISREG(os.fstat(self.fd).st_mode):
                raise ValueError('session.lock must be an existing regular file')
            fcntl.lockf(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.check()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise InterruptedError('agent shutdown interrupted restoration')
        if self.fd is None or self.root_fd is None:
            raise RuntimeError('world session lock is not held')
        actual = os.stat('session.lock', dir_fd=self.root_fd, follow_symlinks=False)
        locked = os.fstat(self.fd)
        root = os.stat(self.world, follow_symlinks=False)
        opened = os.fstat(self.root_fd)
        if ((actual.st_dev,actual.st_ino) != (locked.st_dev,locked.st_ino)
                or (root.st_dev,root.st_ino) != (opened.st_dev,opened.st_ino)):
            raise RuntimeError('world or session.lock inode changed during rollback')

    def __exit__(self, *_):
        # Never open another fd for session.lock: closing it releases this process's locks.
        if self.fd is not None:
            os.close(self.fd); self.fd = None
        if self.root_fd is not None:
            os.close(self.root_fd); self.root_fd = None


def _names(fd):
    return set(os.listdir(fd)) - _EXCLUDED


def _info(fd, name):
    return os.stat(name, dir_fd=fd, follow_symlinks=False)


def _check_tree(fd, device):
    for name in _names(fd):
        info = _info(fd,name)
        if info.st_dev != device:
            raise ValueError('nested mounts are not supported during world restore')
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name,_DIRECTORY,dir_fd=fd)
            try: _check_tree(child,device)
            finally: os.close(child)
        elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            raise ValueError('snapshot/world contains a special file')


def _remove(parent, name, guard):
    guard.check()
    info = _info(parent,name)
    if stat.S_ISDIR(info.st_mode):
        child = os.open(name,_DIRECTORY,dir_fd=parent)
        try:
            # An excluded inode inside an extra directory must be preserved as well.
            for entry in _names(child): _remove(child,entry,guard)
            if os.listdir(child):
                raise ValueError('cannot remove a directory containing protected entries')
        finally: os.close(child)
        os.rmdir(name,dir_fd=parent)
    else:
        os.unlink(name,dir_fd=parent)


def _xattrs(source, destination, follow=True):
    wanted = set(os.listxattr(source,follow_symlinks=follow))
    for name in os.listxattr(destination,follow_symlinks=follow):
        if name not in wanted: os.removexattr(destination,name,follow_symlinks=follow)
    for name in wanted:
        os.setxattr(destination,name,os.getxattr(source,name,follow_symlinks=follow),follow_symlinks=follow)


def _metadata(source, destination, info):
    # chown may clear setuid/setgid and capabilities, so ownership comes first.
    os.fchown(destination,info.st_uid,info.st_gid)
    os.fchmod(destination,stat.S_IMODE(info.st_mode))
    _xattrs(source,destination)
    os.utime(destination,ns=(info.st_atime_ns,info.st_mtime_ns))


def _copy_tree(source, destination, guard):
    guard.check()
    wanted = _names(source)
    for name in _names(destination) - wanted: _remove(destination,name,guard)
    for name in sorted(wanted):
        guard.check()
        info = _info(source,name)
        try: existing = _info(destination,name)
        except FileNotFoundError: existing = None
        if stat.S_ISDIR(info.st_mode):
            if existing is not None and not stat.S_ISDIR(existing.st_mode):
                _remove(destination,name,guard); existing = None
            if existing is None: os.mkdir(name,0o700,dir_fd=destination)
            src = os.open(name,_DIRECTORY,dir_fd=source)
            dst = os.open(name,_DIRECTORY,dir_fd=destination)
            try:
                _copy_tree(src,dst,guard)
                _metadata(src,dst,info); os.fsync(dst)
            finally: os.close(src); os.close(dst)
        elif stat.S_ISLNK(info.st_mode):
            if existing is not None: _remove(destination,name,guard)
            os.symlink(os.readlink(name,dir_fd=source),name,dir_fd=destination)
            os.chown(name,info.st_uid,info.st_gid,dir_fd=destination,follow_symlinks=False)
            # proc fd paths retain the descriptor-relative parent and do not follow the link.
            _xattrs(f'/proc/self/fd/{source}/{name}',f'/proc/self/fd/{destination}/{name}',False)
            os.utime(name,ns=(info.st_atime_ns,info.st_mtime_ns),dir_fd=destination,follow_symlinks=False)
        else:
            src = os.open(name,os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME,dir_fd=source)
            temporary = '.rollback-'+uuid.uuid4().hex
            dst = os.open(temporary,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600,dir_fd=destination)
            try:
                remaining = info.st_size
                while remaining:
                    guard.check()
                    count = os.copy_file_range(src,dst,min(remaining,8*1024*1024))
                    if count <= 0: raise OSError(errno.EIO,'short block-clone copy')
                    remaining -= count
                _metadata(src,dst,info); os.fsync(dst)
                guard.check()
                if existing is not None and stat.S_ISDIR(existing.st_mode): _remove(destination,name,guard)
                os.replace(temporary,name,src_dir_fd=destination,dst_dir_fd=destination)
            finally:
                os.close(src); os.close(dst)
                try: os.unlink(temporary,dir_fd=destination)
                except FileNotFoundError: pass
    os.fsync(destination)


def _open_source(source):
    source = Path(source)
    fd = os.open(source,_DIRECTORY)
    try:
        level = _info(fd,'level.dat')
        if not stat.S_ISREG(level.st_mode): raise ValueError('snapshot level.dat must be regular')
        _check_tree(fd,os.fstat(fd).st_dev)
        return fd
    except BaseException:
        os.close(fd); raise


def restore_world(source, world, guard):
    """Reapply the immutable snapshot safely; errors retain the existing lock fd."""
    if Path(world) != guard.world: raise ValueError('lock is for a different world')
    guard.check()
    src = _open_source(source)
    try:
        if (os.fstat(src).st_dev,os.fstat(src).st_ino) == (os.fstat(guard.root_fd).st_dev,os.fstat(guard.root_fd).st_ino):
            raise ValueError('source must differ from destination')
        _check_tree(guard.root_fd,os.fstat(guard.root_fd).st_dev)
        info = os.fstat(src)
        _copy_tree(src,guard.root_fd,guard)
        _metadata(src,guard.root_fd,info); os.fsync(guard.root_fd)
        guard.check()
    finally: os.close(src)


def _digest(parent, name):
    fd = os.open(name,os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME,dir_fd=parent)
    try:
        digest = hashlib.sha256()
        while block := os.read(fd,1024*1024): digest.update(block)
        return digest.digest()
    finally: os.close(fd)


def _validate(source, destination, guard):
    guard.check()
    if _names(source) != _names(destination): raise RuntimeError('restored tree entries differ')
    for name in _names(source):
        src,dst = _info(source,name),_info(destination,name)
        if (stat.S_IFMT(src.st_mode),stat.S_IMODE(src.st_mode),src.st_uid,src.st_gid,src.st_mtime_ns) != (stat.S_IFMT(dst.st_mode),stat.S_IMODE(dst.st_mode),dst.st_uid,dst.st_gid,dst.st_mtime_ns):
            raise RuntimeError('restored metadata differs')
        a_path=f'/proc/self/fd/{source}/{name}';b_path=f'/proc/self/fd/{destination}/{name}'
        a_attrs={k:os.getxattr(a_path,k,follow_symlinks=False) for k in os.listxattr(a_path,follow_symlinks=False)}
        b_attrs={k:os.getxattr(b_path,k,follow_symlinks=False) for k in os.listxattr(b_path,follow_symlinks=False)}
        if a_attrs != b_attrs: raise RuntimeError('restored extended attributes differ')
        if stat.S_ISDIR(src.st_mode):
            a=os.open(name,_DIRECTORY,dir_fd=source);b=os.open(name,_DIRECTORY,dir_fd=destination)
            try: _validate(a,b,guard)
            finally: os.close(a);os.close(b)
        elif stat.S_ISLNK(src.st_mode):
            if os.readlink(name,dir_fd=source) != os.readlink(name,dir_fd=destination):
                raise RuntimeError('restored link differs')
        elif src.st_size != dst.st_size or _digest(source,name) != _digest(destination,name):
            raise RuntimeError('restored file content differs')


def validate_world(source, world, guard):
    if Path(world) != guard.world: raise ValueError('lock is for a different world')
    guard.check(); src = _open_source(source)
    try: _validate(src,guard.root_fd,guard)
    finally: os.close(src)
