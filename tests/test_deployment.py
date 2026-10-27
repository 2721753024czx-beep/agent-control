import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from configure_local import create_configs
from doctor import check
from build_macos_bundle import build as build_mac


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'install'
        self.workspace = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.workspace.mkdir()

    def configure(self, readers=()):
        return create_configs(self.root, self.workspace, 'cli_TEST', 'ou_OWNER',
                              'synthetic-test-secret', sys.executable, readers)

    def test_fresh_personal_configuration_and_private_credentials(self):
        hub, node = self.configure()
        self.assertEqual(check(self.root), 1)
        self.assertEqual(hub['agents'][0]['access_mode'], 'personal')
        self.assertEqual(node['token'], hub['devices'][0]['token'])
        self.assertEqual(len({node['token'], hub['admin_token'], node['serve_password']}), 3)
        self.assertTrue(all(len(x) >= 32 for x in [node['token'], hub['admin_token'], node['serve_password']]))
        if os.name != 'nt':
            self.assertEqual((self.root / 'hub/config.json').stat().st_mode & 0o777, 0o600)

    def test_existing_configuration_and_workspace_instructions_preserved(self):
        instructions = self.workspace / 'AGENTS.md'
        instructions.write_text('Existing project rules', encoding='utf-8')
        self.configure()
        original = (self.root / 'hub/config.json').read_bytes()
        with self.assertRaises(ValueError):
            self.configure()
        self.assertEqual(original, (self.root / 'hub/config.json').read_bytes())
        self.assertEqual(instructions.read_text(), 'Existing project rules')

    def test_existing_database_blocks_setup_without_writing_configuration(self):
        (self.root / 'hub').mkdir()
        (self.root / 'hub/hub.db').write_bytes(b'existing-data')
        with self.assertRaises(ValueError):
            self.configure()
        self.assertFalse((self.root / 'node/config.json').exists())
        self.assertEqual((self.root / 'hub/hub.db').read_bytes(), b'existing-data')

    def test_explicit_readers_enable_shared_without_promoting_owner(self):
        hub, _ = self.configure(['ou_READER', 'ou_OWNER'])
        agent = hub['agents'][0]
        self.assertEqual(agent['access_mode'], 'shared')
        self.assertEqual(agent['member_roles'], {'ou_READER': 'reader'})
        self.assertEqual(check(self.root), 1)

    def test_doctor_rejects_mismatched_token_and_missing_workspace(self):
        _, node = self.configure()
        path = self.root / 'node/config.json'
        node['token'] = 'wrong-token'
        path.write_text(json.dumps(node))
        with self.assertRaises(AssertionError):
            check(self.root)
        node['token'] = json.loads((self.root / 'hub/config.json').read_text())['devices'][0]['token']
        path.write_text(json.dumps(node))
        # Keep memory elsewhere while simulating a missing configured workspace.
        self.workspace.rename(self.workspace.with_name('moved-project'))
        with self.assertRaises(AssertionError):
            check(self.root)

    def test_doctor_rejects_workspace_removed_from_node_allowlist(self):
        _, node = self.configure()
        node['allowed_work_dirs'] = []
        (self.root / 'node/config.json').write_text(json.dumps(node))
        with self.assertRaises(AssertionError):
            check(self.root)

    @unittest.skipUnless((ROOT / 'scripts/macos/services.py').exists(), 'not a macOS/source bundle')
    def test_macos_service_ownership_paths_and_busy_guard(self):
        spec = importlib.util.spec_from_file_location('deployment_services', ROOT / 'scripts/macos/services.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        job = module.plist(self.root, 'node', Path(sys.executable))
        self.assertEqual(job['WorkingDirectory'], str(self.root))
        self.assertEqual(job['EnvironmentVariables']['AGENT_CONTROL_HOST'], '127.0.0.1')
        (self.root / 'hub').mkdir()
        with sqlite3.connect(self.root / 'hub/hub.db') as conn:
            conn.execute('CREATE TABLE tasks(status TEXT, execution_active INTEGER)')
            conn.execute("INSERT INTO tasks VALUES ('failed',1)")
        with self.assertRaises(SystemExit):
            module.require_idle(self.root)

    @unittest.skipUnless((ROOT / 'scripts/macos/install.sh').exists(), 'not a macOS/source bundle')
    def test_macos_bundle_manifest_and_secret_exclusions(self):
        output = self.root / 'mac.zip'
        build_mac(output)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            self.assertIn('agent-control/scripts/macos/install.sh', names)
            self.assertIn('agent-control/docs/deploy-acceptance.md', names)
            self.assertNotIn('agent-control/scripts/windows/install.ps1', names)
            for line in archive.read('agent-control/MANIFEST.sha256').decode().splitlines():
                digest, relative = line.split('  ', 1)
                self.assertEqual(hashlib.sha256(archive.read('agent-control/' + relative)).hexdigest(), digest)
            for forbidden in ['hub/config.json', 'node/config.json', 'mobile-control-smoke.txt']:
                self.assertNotIn('agent-control/' + forbidden, names)
            self.assertFalse(any('/runtime/' in n or '/.agent-memory/' in n for n in names))
