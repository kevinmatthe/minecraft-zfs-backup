from pathlib import Path
import tempfile
import unittest
from minecraft_zfs_agent.config import Config,secret
from minecraft_zfs_agent.daemon import InstanceLock
from minecraft_zfs_agent.protocol import atomic_json,read_json,parse_json,APIError


class ConfigTests(unittest.TestCase):
    def test_http_config_has_no_mailbox_or_polling(self):
        config=Config('tank/world',Path('/world'),Path('/state'),Path('/token'))
        self.assertEqual(config.listen_port,8765);self.assertFalse(hasattr(config,'mailbox_path'));self.assertFalse(hasattr(config,'poll_seconds'))
    def test_private_root_configuration_and_strict_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json';value=dict(source_dataset='tank/world',world_path='/world',state_dir='/state',api_token_file='/token')
            atomic_json(path,value,mode=0o600);self.assertEqual(Config.load(path).keep,24)
            atomic_json(path,value|dict(mailbox_path='/old'),mode=0o600)
            with self.assertRaises(ValueError):Config.load(path)
            atomic_json(path,value,mode=0o644)
            with self.assertRaises(ValueError):Config.load(path)
    def test_secret_is_private_regular_and_at_least32_chars(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'token';path.write_text('a'*32);path.chmod(0o600);self.assertEqual(secret(path),'a'*32)
            path.write_text('short')
            with self.assertRaises(ValueError):secret(path)
            path.unlink();path.symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):secret(path)
    def test_state_and_secrets_cannot_be_restored_world_entries(self):
        for state,token in [('/world/state','/token'),('/state','/world/token')]:
            with self.assertRaises(ValueError):Config('tank/world',Path('/world'),Path(state),Path(token))
    def test_flock_prevents_duplicate_companions(self):
        with tempfile.TemporaryDirectory() as tmp:
            with InstanceLock(Path(tmp)):
                with self.assertRaises(RuntimeError):
                    with InstanceLock(Path(tmp)):pass
    def test_file_and_http_json_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'json'
            for raw in [b' '*16385,b'{"x":1,"x":2}',b'[]',b'\xff']:
                path.write_bytes(raw)
                with self.assertRaises(ValueError):read_json(path)
                with self.assertRaises(APIError):parse_json(raw)
