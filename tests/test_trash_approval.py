import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'node'))
from node import execute
from runtime import NodeRuntime
from safe_trash import prepare_trash, move_to_trash


class TrashApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'note.txt'
        self.target.write_text('keep')
        self.runtime = NodeRuntime({'allowed_work_dirs': [str(self.root)]}, self.root)
        self.task = {'task_id': 't_trash', 'prompt': '/trash note.txt',
                     'actor_role': 'editor', 'engine': {'type': 'serve', 'work_dir': str(self.root)}}

    def test_rejected_expired_or_no_approval_never_moves(self):
        for callback in (None, lambda *args: False):
            with self.assertRaises(PermissionError):
                execute(self.task, 10, self.runtime, callback)
            self.assertEqual(self.target.read_text(), 'keep')
            self.assertFalse((self.root / '.agent-control-trash').exists())

    def test_approved_move_without_model_and_restore(self):
        def approve(session, pid, permission, item):
            self.assertEqual(permission, 'safe_change')
            self.assertIn('note.txt', item['patterns'][0])
            self.assertTrue(self.target.exists())
            return True
        with patch('node.run_serve', side_effect=AssertionError('no model required')):
            result, _ = execute(self.task, 10, self.runtime, approve)
        self.assertIn('已批准', result)
        stored = next((self.root / '.agent-control-trash').glob('*/note.txt'))
        self.assertEqual(stored.read_text(), 'keep')
        stored.rename(self.target)
        self.assertEqual(self.target.read_text(), 'keep')

    def test_change_after_approval_fails_closed(self):
        def approve(*args):
            self.target.write_text('changed')
            return True
        with self.assertRaisesRegex(ValueError, '已变化'):
            execute(self.task, 10, self.runtime, approve)
        self.assertEqual(self.target.read_text(), 'changed')

    def test_reader_and_direct_runtime_bypass_denied(self):
        self.task['actor_role'] = 'reader'
        with self.assertRaises(PermissionError):
            execute(self.task, 10, self.runtime, lambda *args: True)
        with self.assertRaises(PermissionError):
            self.runtime.control('/trash note.txt', str(self.root))
        self.assertTrue(self.target.exists())

    def test_directory_snapshot_changes_and_links(self):
        folder = self.root / 'folder'
        folder.mkdir()
        (folder / 'a').write_text('a')
        change = prepare_trash(self.root, 'folder')
        (folder / 'b').write_text('b')
        with self.assertRaises(ValueError):
            move_to_trash(self.root, 'folder', expected=change)
        try:
            (folder / 'link').symlink_to(self.target)
        except OSError:
            return
        with self.assertRaises(ValueError):
            prepare_trash(self.root, 'folder')
