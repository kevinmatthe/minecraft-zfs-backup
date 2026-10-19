import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from minecraft_zfs_agent.config import Config
from minecraft_zfs_agent.protocol import atomic_json, read_json
from minecraft_zfs_agent.daemon import InstanceLock, Heartbeat


class WorkerTests(unittest.TestCase):
    def test_single_instance_flock(self):
        with tempfile.TemporaryDirectory() as tmp:
            with InstanceLock(Path(tmp)):
                with self.assertRaises(RuntimeError):
                    with InstanceLock(Path(tmp)):pass
            with InstanceLock(Path(tmp)):pass
    def test_atomic_shared_messages_are_readable_under_private_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=os.umask(0o077)
            try:atomic_json(Path(tmp)/'response.json',{'ok':True})
            finally:os.umask(old)
            self.assertEqual((Path(tmp)/'response.json').stat().st_mode&0o777,0o644)
    def test_heartbeat_keeps_refreshing_during_slow_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop=threading.Event()
            class Backend:
                def preflight(self):stop.wait(2)
            heartbeat=Heartbeat(Backend(),Path(tmp),stop,interval=.02)
            heartbeat.start();time.sleep(.07)
            first=read_json(Path(tmp)/'heartbeat.json')
            time.sleep(.05)
            second=read_json(Path(tmp)/'heartbeat.json')
            stop.set();heartbeat.join(2)
            self.assertFalse(first['ready']);self.assertGreater(second['updated_ms'],first['updated_ms'])

if __name__=='__main__':unittest.main()

class ConfigurationTests(unittest.TestCase):
    def test_rejects_untrusted_configuration_file_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'
            atomic_json(path,dict(source_dataset='tank/world',world_path='/world',mailbox_path='/mail',state_dir='/state'),mode=0o666)
            with self.assertRaises(ValueError):Config.load(path)
    def test_rejects_config_that_places_state_inside_game_mailbox(self):
        with self.assertRaises(ValueError):Config('tank/world',Path('/world'),Path('/mail'),Path('/mail/state'))
    def test_rejects_command_like_dataset_names(self):
        with self.assertRaises(ValueError):Config('tank/world;cmd',Path('/world'),Path('/mail'),Path('/state'))
    def test_no_target_dataset_is_required(self):
        config=Config('tank/world',Path('/world'),Path('/mail'),Path('/state'))
        self.assertEqual(config.keep,24)
        self.assertFalse(hasattr(config,'target_dataset'))
    def test_unknown_config_fields_fail_with_clear_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'
            atomic_json(path,dict(source_dataset='tank/world',world_path='/world',mailbox_path='/mail',state_dir='/state',command='bad'),mode=0o600)
            with self.assertRaises(ValueError):Config.load(path)

class CLITests(unittest.TestCase):
    def test_status_accepts_local_config_without_target(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=root/'config.json'
            atomic_json(config,dict(source_dataset='tank/world',world_path=str(root/'world'),mailbox_path=str(root/'mail'),state_dir=str(root/'state')),mode=0o600)
            result=subprocess.run([sys.executable,'-m','minecraft_zfs_agent','--config',str(config),'status'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            value=json.loads(result.stdout)
            self.assertFalse(value['ready'])
            self.assertIn('retention',value)
            self.assertNotIn('replication',value)
