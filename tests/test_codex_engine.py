import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from collections import deque
from types import SimpleNamespace
import queue
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'node'))
from codex_engine import DISABLED, RPC, VERSION, read_project, run_codex, toml_value


@unittest.skipUnless(sys.platform == 'darwin', 'Mac adapter')
class CodexReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'note.txt').write_text('alpha')

    def test_read_requires_approval(self):
        with self.assertRaises(PermissionError):
            read_project(self.root, 'note.txt', lambda: False)
        self.assertEqual(read_project(self.root, 'note.txt', lambda: True), 'alpha')

    def test_escape_and_hidden_paths_rejected_before_approval(self):
        for name in ['../note.txt', str(self.root / 'note.txt'), '.env', 'a/../note.txt', 'a\\b', 'a:stream']:
            with self.subTest(name=name), patch('codex_engine.os.open') as opened:
                with self.assertRaises(PermissionError):
                    read_project(self.root, name, lambda: self.fail('invalid path requested approval'))
                opened.assert_not_called()

    def test_symlink_and_hardlink_denied(self):
        (self.root / 'link.txt').symlink_to(self.root / 'note.txt')
        os.link(self.root / 'note.txt', self.root / 'hard.txt')
        for name in ['link.txt', 'hard.txt']:
            with self.subTest(name=name), self.assertRaises((PermissionError, OSError)):
                read_project(self.root, name, lambda: True)

    def test_symlink_parent_and_fifo_denied(self):
        (self.root / 'dir').mkdir()
        (self.root / 'alias').symlink_to(self.root / 'dir', target_is_directory=True)
        os.mkfifo(self.root / 'pipe')
        for name in ['alias/note.txt', 'pipe']:
            with self.subTest(name=name), self.assertRaises((PermissionError, OSError)):
                read_project(self.root, name, lambda: True)

    def test_file_changed_to_link_during_approval_is_denied(self):
        def approve():
            (self.root / 'note.txt').unlink()
            (self.root / 'note.txt').symlink_to('/etc/hosts')
            return True
        with self.assertRaises(OSError):
            read_project(self.root, 'note.txt', approve)

    def test_binary_and_large_files_denied(self):
        for data in [b'\x00private', b'a' * 32769]:
            (self.root / 'note.txt').write_bytes(data)
            with self.assertRaises(PermissionError):
                read_project(self.root, 'note.txt', lambda: True)


class CodexGateTests(unittest.TestCase):
    def test_opt_in_required_before_starting_process(self):
        with patch('codex_engine.subprocess.Popen') as process:
            with self.assertRaises(PermissionError):
                run_codex({'work_dir': '/nonexistent'}, 'hello', 5)
            process.assert_not_called()

    def test_toml_keys_with_dots_are_table_members_not_dotted_paths(self):
        value = toml_value({'plugins': {'package.id@source': {'enabled': False}}})
        self.assertIn('"package.id@source"={"enabled"=false}', value)

    def test_early_turn_notification_is_preserved(self):
        rpc = RPC.__new__(RPC)
        rpc.queue = queue.Queue(); rpc.deferred = deque(); rpc.sequence = 0
        rpc.deadline = time.monotonic() + 2
        rpc.queue.put({'method': 'turn/completed', 'params': {'threadId': 's'}})
        rpc.queue.put({'id': 1, 'result': {'turn': {'id': 't'}}})
        with patch.object(rpc, 'send'):
            self.assertEqual(rpc.request('turn/start', {})['turn']['id'], 't')
        self.assertEqual(rpc.receive()['method'], 'turn/completed')

    def test_native_requests_and_wrong_turn_never_reach_read_broker(self):
        for event in [
            {'id': 42, 'method': 'item/commandExecution/requestApproval', 'params': {}},
            {'id': 42, 'method': 'item/tool/call', 'params': {'threadId': 's', 'turnId': 'other',
             'tool': 'ac_read', 'callId': 'call', 'arguments': {'path': 'note.txt'}}}]:
            class FakeRPC:
                def __init__(self, *args):
                    pass
                def request(self, method, params):
                    if method == 'config/read':
                        return {'config': {'features': {k: False for k in DISABLED}}}
                    if method == 'thread/start':
                        return {'thread': {'id': 's'}, 'approvalPolicy': 'never',
                                'activePermissionProfile': {'id': params['permissions']}}
                    if method == 'turn/start':
                        return {'turn': {'id': 't'}}
                    return {}
                def send(self, payload):
                    pass
                def receive(self):
                    return event
                def close(self):
                    pass
            with tempfile.TemporaryDirectory() as folder:
                with patch('codex_engine.sys.platform', 'darwin'), patch('codex_engine.RPC', FakeRPC), \
                     patch('codex_engine.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=VERSION)), \
                     patch('codex_engine.read_project') as read:
                    with self.assertRaises(PermissionError):
                        run_codex({'work_dir': folder, 'codex': {'enabled': True, 'executable': sys.executable}}, 'hi', 10)
                    read.assert_not_called()
