import importlib.util
from pathlib import Path
import shlex
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('install', Path(__file__).parents[1]/'install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


class InstallerTests(unittest.TestCase):
    def test_service_runs_persistent_agent_and_quotes_paths(self):
        text = install.render_unit(Path('/opt/my mods/zfs%backup'), Path('/etc/my config.json'))
        self.assertIn('Restart=always', text)
        self.assertIn('"PYTHONPATH=/opt/my mods/zfs%%backup/agent"', text)
        self.assertIn('"--config" "/etc/my config.json" "run"', text)
        self.assertNotIn('cron', text.lower())

    def test_newlines_and_unrelated_service_names_are_rejected(self):
        with self.assertRaises(ValueError): install.render_unit(Path('/opt/a\nb'),Path('/etc/config'))
        for name in ('docker', '../minecraft-zfs-agent', 'minecraft-zfs-agent\nExecStart=evil'):
            with self.assertRaises(ValueError): install.service_name(name)
        self.assertEqual(install.service_name('minecraft-zfs-agent'),'minecraft-zfs-agent.service')

    def test_reboot_hook_round_trips_arguments_without_shell_expansion(self):
        command = install.postinit_command(Path('/opt/a b'),Path('/etc/a $x.json'),'minecraft-zfs-agent',1000,0)
        values = shlex.split(command)
        self.assertEqual(values[:2],['/usr/bin/python3','/opt/a b/deploy/install.py'])
        self.assertEqual(values[values.index('--config')+1],'/etc/a $x.json')
        self.assertNotIn('--truenas',values)


if __name__ == '__main__': unittest.main()
