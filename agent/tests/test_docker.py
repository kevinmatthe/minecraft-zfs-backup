"""Validate the public Docker contract without starting production services."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PackagingTests(unittest.TestCase):
    def test_dockerfile_uses_compatible_userspace_and_copies_only_agent(self):
        dockerfile=(ROOT/'Dockerfile').read_text()
        self.assertIn('FROM debian:trixie-slim',dockerfile)
        self.assertIn('zfsutils-linux',dockerfile)
        self.assertIn('--no-install-recommends',dockerfile)
        self.assertIn('COPY agent/minecraft_zfs_agent/',dockerfile)
        self.assertNotIn('COPY . ',dockerfile)
        self.assertIn('"-m", "minecraft_zfs_agent"',dockerfile)
    def test_build_context_excludes_local_state_and_worlds(self):
        ignore=(ROOT/'.dockerignore').read_text().splitlines()
        self.assertIn('**',ignore)
        self.assertFalse(any(line.startswith('!run') for line in ignore))
        self.assertIn('!agent/minecraft_zfs_agent/*.py',ignore)
    @unittest.skipUnless(shutil.which('docker'), 'Docker Compose CLI is required for this packaging check')
    def test_compose_requires_exact_host_paths_and_scoped_permissions(self):
        env=os.environ|dict(AGENT_CONFIG='/example/config.json',WORLD_PATH='/example/world',MAILBOX_PATH='/example/mailbox',STATE_DIR='/example/state')
        result=subprocess.run(['docker','compose','-f',str(ROOT/'deploy/compose.example.yaml'),'config','--format','json'],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        service=json.loads(result.stdout)['services']['agent']
        self.assertTrue(service['read_only'])
        self.assertNotEqual(service.get('privileged'),True)
        self.assertNotIn('pid',service)
        self.assertEqual(service['network_mode'],'none')
        self.assertEqual(set(service['cap_add']),{'SYS_ADMIN','DAC_OVERRIDE'})
        self.assertEqual(service['cap_drop'],['ALL'])
        volumes={v['source']:v for v in service['volumes']}
        self.assertEqual(volumes['/example/world']['target'],'/example/world')
        self.assertTrue(volumes['/example/world']['read_only'])
        self.assertTrue(volumes['/example/config.json']['read_only'])
        self.assertEqual(volumes['/example/mailbox']['target'],'/example/mailbox')
        self.assertEqual(volumes['/example/state']['target'],'/example/state')
        self.assertFalse(any('docker.sock' in value for value in volumes))
        self.assertTrue(all(v['bind'].get('create_host_path',False) is False for v in volumes.values()))


if __name__=='__main__':unittest.main()
