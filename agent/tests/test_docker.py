import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[2]


class PackagingTests(unittest.TestCase):
    def test_dockerfile_uses_compatible_userspace_and_copies_only_agent(self):
        value=(ROOT/'Dockerfile').read_text()
        self.assertIn('FROM debian:trixie-slim',value);self.assertIn('zfsutils-linux',value);self.assertIn('--no-install-recommends',value)
        self.assertIn('COPY agent/minecraft_zfs_agent/',value);self.assertNotIn('COPY . ',value)
    def test_build_context_excludes_local_state_and_worlds(self):
        value=(ROOT/'.dockerignore').read_text().splitlines();self.assertIn('**',value);self.assertFalse(any(v.startswith('!run') for v in value))
    @unittest.skipUnless(shutil.which('docker'),'Docker Compose CLI required')
    def test_private_http_companion_exposes_no_host_port_or_mailbox(self):
        env=os.environ|dict(AGENT_CONFIG='/example/config.json',WORLD_PATH='/example/world',STATE_DIR='/example/state',SECRETS_DIR='/example/secrets',AGENT_NETWORK='example-private')
        result=subprocess.run(['docker','compose','-f',str(ROOT/'deploy/compose.example.yaml'),'config','--format','json'],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);service=json.loads(result.stdout)['services']['agent']
        self.assertNotIn('ports',service);self.assertNotIn('network_mode',service);self.assertNotIn('pid',service);self.assertNotEqual(service.get('privileged'),True)
        self.assertEqual(service['tmpfs'],['/tmp:rw,noexec,nosuid,size=16m'])
        self.assertEqual(service['cap_drop'],['ALL']);self.assertTrue({'SYS_ADMIN','DAC_OVERRIDE','CHOWN','FOWNER','FSETID'}<=set(service['cap_add']))
        self.assertTrue(service['read_only'])
        volumes={v['source']:v for v in service['volumes']}
        self.assertEqual(set(volumes),{'/example/config.json','/example/world','/example/state','/example/secrets'})
        self.assertEqual(volumes['/example/world']['target'],'/example/world');self.assertFalse(volumes['/example/world'].get('read_only',False))
        self.assertTrue(volumes['/example/secrets']['read_only']);self.assertTrue(volumes['/example/config.json']['read_only'])
        self.assertTrue(all(v['bind'].get('create_host_path',False) is False for v in volumes.values()))
