"""Read-only POSIX session lock check for already-stopped lifecycle checkpoints."""
import fcntl
import os
import stat


class OfflineWorldLock:
    def __init__(self,world):self.path=world/'session.lock';self.fd=None
    def __enter__(self):
        self.fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(self.fd).st_mode):raise OSError('session lock is not regular')
            fcntl.lockf(self.fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
            self.check()
            return self
        except BaseException:os.close(self.fd);self.fd=None;raise
    def check(self):
        opened=os.fstat(self.fd);current=self.path.lstat()
        if (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino) or not stat.S_ISREG(current.st_mode):raise OSError('session lock inode changed')
    def __exit__(self,*_):
        if self.fd is not None:os.close(self.fd);self.fd=None
