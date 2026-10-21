import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(ROOT / "node"))
import hub  # noqa: E402
import db  # noqa: E402
from runtime import NodeRuntime  # noqa: E402


class WorkspaceTests(unittest.TestCase):
    def test_sender_selection_isolated_and_task_directory_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            with patch.object(db, "DB_PATH", root / "test.db"):
                db.init_db()
                db.upsert_device("dev", "device", "secret")
                db.upsert_agent("app", "dev", "opencode", "Agent", "cli", "echo {prompt}",
                                "secret", str(first))
                with patch.object(hub, "CFG", {"agents": [{"app_id": "app", "workspaces": {
                        "first": str(first), "second": str(second)}}]}), \
                     patch.object(hub, "reply"):
                    def send(mid, sender, text):
                        hub.handle_message("app", {"message_id": mid, "message_type": "text",
                                            "content": '{"text": "%s"}' % text}, {"open_id": sender})
                    send("m1", "alice", "/workspace use second")
                    send("m2", "alice", "first task")
                    send("m3", "alice", "/workspace use first")
                    send("m4", "bob", "bob task")
                self.assertEqual(db.get_workspace_selection("app", "alice"), "first")
                self.assertIsNone(db.get_workspace_selection("app", "bob"))
                task, error = db.claim_task("dev", "secret")
                self.assertIsNone(error)
                self.assertEqual(task["engine"]["work_dir"], str(second.resolve()))
                with sqlite3.connect(root / "test.db") as conn:
                    row = conn.execute("SELECT work_dir FROM tasks WHERE reply_message_id='m4'").fetchone()
                self.assertEqual(row[0], str(first.resolve()))

    def test_node_rejects_outside_allowlist_and_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            allowed = root / "allowed"
            outside = root / "outside"
            allowed.mkdir()
            outside.mkdir()
            runtime = NodeRuntime({"allowed_work_dirs": [str(allowed)]}, root)
            self.assertEqual(runtime.validate_work_dir(str(allowed)), str(allowed.resolve()))
            with self.assertRaises(PermissionError):
                runtime.validate_work_dir(str(outside))
            link = root / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(PermissionError):
                runtime.validate_work_dir(str(link))


if __name__ == "__main__":
    unittest.main()
