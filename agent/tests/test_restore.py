import errno
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

try:
    from minecraft_zfs_agent.restore import WorldLock, restore_world, validate_world
except ImportError:
    WorldLock = restore_world = validate_world = None


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'snapshot'; self.source.mkdir()
        self.world = self.root/'world'; self.world.mkdir()
        (self.source/'level.dat').write_bytes(b'old world')
        (self.world/'level.dat').write_bytes(b'new world')
        (self.world/'session.lock').write_bytes(b'lock inode')

    def test_restore_replaces_tree_preserves_lock_and_symlinks_and_metadata(self):
        self.assertIsNotNone(restore_world, 'restore feature must exist')
        (self.source/'region').mkdir()
        (self.source/'region'/'r.0.0.mca').write_bytes(b'R'*5000)
        (self.source/'link').symlink_to('region/r.0.0.mca')
        (self.source/'level.dat').chmod(0o640)
        os.utime(self.source/'level.dat', ns=(123456789000,123456789000))
        (self.source/'session.lock').write_bytes(b'wrong lock')
        (self.world/'obsolete').mkdir(); (self.world/'obsolete'/'data').write_text('remove')
        inode = (self.world/'session.lock').stat().st_ino
        with WorldLock(self.world) as guard:
            restore_world(self.source,self.world,guard)
            validate_world(self.source,self.world,guard)
            self.assertEqual((self.world/'session.lock').stat().st_ino,inode)
            self.assertEqual((self.world/'session.lock').read_bytes(),b'lock inode')
            self.assertEqual((self.world/'level.dat').read_bytes(),b'old world')
            self.assertEqual((self.world/'level.dat').stat().st_mode & 0o777,0o640)
            self.assertEqual((self.world/'level.dat').stat().st_mtime_ns,123456789000)
            self.assertEqual(os.readlink(self.world/'link'),'region/r.0.0.mca')
            self.assertFalse((self.world/'obsolete').exists())
        # Reapplying an interrupted job is safe and keeps the same lock inode.
        with WorldLock(self.world) as guard: restore_world(self.source,self.world,guard)
        self.assertEqual((self.world/'session.lock').stat().st_ino,inode)

    def test_never_follows_destination_symlink_outside_world(self):
        self.assertIsNotNone(restore_world)
        outside=self.root/'outside';outside.mkdir();(outside/'keep').write_text('safe')
        (self.world/'region').symlink_to(outside)
        (self.source/'region').mkdir();(self.source/'region'/'data').write_text('restore')
        with WorldLock(self.world) as guard: restore_world(self.source,self.world,guard)
        self.assertEqual((outside/'keep').read_text(),'safe')
        self.assertFalse((outside/'data').exists())
        self.assertFalse((self.world/'region').is_symlink())

    def test_preserves_xattrs_and_validation_detects_corruption(self):
        self.assertIsNotNone(restore_world)
        os.setxattr(self.source/'level.dat','user.minecraft-test',b'value')
        with WorldLock(self.world) as guard:
            restore_world(self.source,self.world,guard)
            self.assertEqual(os.getxattr(self.world/'level.dat','user.minecraft-test'),b'value')
            os.setxattr(self.world/'level.dat','user.minecraft-test',b'corrupt')
            with self.assertRaises(RuntimeError): validate_world(self.source,self.world,guard)
            os.setxattr(self.world/'level.dat','user.minecraft-test',b'value')
            (self.world/'level.dat').write_bytes(b'tampered')
            with self.assertRaises(RuntimeError): validate_world(self.source,self.world,guard)

    def test_rejects_destination_special_file_before_writing(self):
        self.assertIsNotNone(restore_world)
        os.mkfifo(self.world/'pipe')
        with WorldLock(self.world) as guard:
            with self.assertRaises(ValueError): restore_world(self.source,self.world,guard)
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new world')

    def test_rejects_source_and_destination_root_symlinks(self):
        self.assertIsNotNone(restore_world)
        alias=self.root/'snapshot-alias';alias.symlink_to(self.source,target_is_directory=True)
        with WorldLock(self.world) as guard:
            with self.assertRaises(OSError):restore_world(alias,self.world,guard)
        world_alias=self.root/'world-alias';world_alias.symlink_to(self.world,target_is_directory=True)
        with self.assertRaises(OSError):
            with WorldLock(world_alias):pass
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new world')

    def test_preserves_protected_inode_in_directory_that_would_be_replaced(self):
        self.assertIsNotNone(restore_world)
        (self.source/'region').write_text('file replaces directory')
        (self.world/'region').mkdir();protected=self.world/'region'/'session.lock'
        protected.write_bytes(b'keep');inode=protected.stat().st_ino
        with WorldLock(self.world) as guard:
            with self.assertRaises(ValueError):restore_world(self.source,self.world,guard)
        self.assertEqual(protected.stat().st_ino,inode)
        self.assertEqual(protected.read_bytes(),b'keep')

    def test_sparse_file_copy_calls_are_bounded(self):
        self.assertIsNotNone(restore_world)
        with (self.source/'sparse').open('wb') as stream:
            stream.seek(20*1024*1024);stream.write(b'last block')
        original=os.copy_file_range;calls=[]
        def bounded(src,dst,count):
            calls.append(count);return original(src,dst,count)
        with WorldLock(self.world) as guard,patch('os.copy_file_range',side_effect=bounded):
            restore_world(self.source,self.world,guard)
            validate_world(self.source,self.world,guard)
        self.assertLessEqual(max(calls),8*1024*1024)
        self.assertEqual((self.world/'sparse').stat().st_size,20*1024*1024+10)

    @unittest.skipUnless(os.environ.get('ZFS_TEST_ZFS_POOL'), 'opt-in disposable ZFS snapshot restore')
    def test_real_zfs_snapshot_restore_preserves_lock_and_clone_contents(self):
        import uuid
        pool=os.environ['ZFS_TEST_ZFS_POOL']
        self.assertRegex(pool,r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
        dataset=pool+'/minecraft-zfs-restore-validation-'+uuid.uuid4().hex[:12]
        mount=self.root/'zfs-world';snapshot='mc-hourly-20261019T000001Z'
        altroot=subprocess.run(['zpool','get','-H','-o','value','altroot',pool],check=True,capture_output=True,text=True).stdout.strip()
        mount_property=str(mount)
        if altroot != '-':
            mount_property='/'+str(mount.relative_to(altroot))
        subprocess.run(['zfs','create','-o','copies=1','-o','mountpoint='+mount_property,dataset],check=True,capture_output=True)
        snap_created=False
        try:
            (mount/'session.lock').write_bytes(b'original inode')
            (mount/'level.dat').write_bytes(b'old level data')
            (mount/'region').mkdir();(mount/'region'/'r.0.0.mca').write_bytes(b'R'*1024*1024)
            subprocess.run(['zfs','snapshot',dataset+'@'+snapshot],check=True,capture_output=True);snap_created=True
            (mount/'level.dat').write_bytes(b'new level data')
            (mount/'extra').write_bytes(b'remove')
            inode=(mount/'session.lock').stat().st_ino
            with WorldLock(mount) as guard:
                source=mount/'.zfs'/'snapshot'/snapshot
                restore_world(source,mount,guard);validate_world(source,mount,guard)
            self.assertEqual((mount/'level.dat').read_bytes(),b'old level data')
            self.assertEqual((mount/'session.lock').stat().st_ino,inode)
            self.assertFalse((mount/'extra').exists())
        finally:
            if snap_created:subprocess.run(['zfs','destroy',dataset+'@'+snapshot],check=True,capture_output=True)
            subprocess.run(['zfs','destroy',dataset],check=True,capture_output=True)

    def test_copy_error_has_no_plain_copy_fallback(self):
        self.assertIsNotNone(restore_world)
        with WorldLock(self.world) as guard, patch('os.copy_file_range',side_effect=OSError(errno.EOPNOTSUPP,'unsupported')):
            with self.assertRaises(OSError): restore_world(self.source,self.world,guard)
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new world')

    def test_lock_required_and_inode_replacement_detected(self):
        self.assertIsNotNone(WorldLock)
        with WorldLock(self.world) as guard:
            (self.world/'session.lock').unlink();(self.world/'session.lock').write_bytes(b'replaced')
            with self.assertRaises(RuntimeError): restore_world(self.source,self.world,guard)
        (self.world/'session.lock').unlink()
        with self.assertRaises(FileNotFoundError):
            with WorldLock(self.world): pass

    def test_special_snapshot_files_rejected_before_data_change(self):
        self.assertIsNotNone(restore_world)
        os.mkfifo(self.source/'pipe')
        with WorldLock(self.world) as guard:
            with self.assertRaises(ValueError): restore_world(self.source,self.world,guard)
        self.assertEqual((self.world/'level.dat').read_bytes(),b'new world')

    @unittest.skipUnless(os.environ.get('ZFS_TEST_JAVA_DOCKER') == '1', 'opt-in isolated Java21 Docker lock test')
    def test_real_java_filechannel_lock_compatibility_both_directions(self):
        self.assertIsNotNone(WorldLock)
        (self.root/'LockProbe.java').write_text('''import java.nio.channels.*;
import java.nio.file.*;
class LockProbe { public static void main(String[] args) throws Exception {
  try (var c=FileChannel.open(Path.of(args[0]),StandardOpenOption.READ,StandardOpenOption.WRITE)) {
    var lock=c.tryLock(); System.out.println(lock==null?"BUSY":"LOCKED");System.out.flush();
    if(lock!=null) { if(args.length>1)System.in.read(); lock.release(); }
  }
}}''')
        command=['docker','run','--rm','--network','none','-i','--mount',
                 'type=bind,src='+str(self.root)+',dst=/test','--entrypoint',
                 '/usr/lib/jvm/java-21-openjdk-amd64/bin/java',
                 'arcadiatechnology/crafty-4:4.11.0','/test/LockProbe.java','/test/world/session.lock']
        with WorldLock(self.world):
            result=subprocess.run(command,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'BUSY')
        process=subprocess.Popen(command+['hold'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'LOCKED')
            with self.assertRaises(BlockingIOError):
                with WorldLock(self.world): pass
        finally:
            process.communicate('\n',timeout=30)
        self.assertEqual(process.returncode,0)

    def test_posix_lock_excludes_another_process(self):
        self.assertIsNotNone(WorldLock)
        code='import fcntl,sys; f=open(sys.argv[1],"r+b"); fcntl.lockf(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
        with WorldLock(self.world):
            result=subprocess.run(['python3','-c',code,str(self.world/'session.lock')],capture_output=True)
            self.assertNotEqual(result.returncode,0)


if __name__ == '__main__': unittest.main()
