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
    def test_trash_requires_sender_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.mkdir(exist_ok=True)
            with patch.object(db, "DB_PATH", root / "test.db"):
                db.init_db()
                db.upsert_device("dev", "device", "secret")
                db.upsert_agent("app", "dev", "opencode", "Agent", "serve",
                                "http://127.0.0.1:4096", "secret", str(root))
                with patch.object(hub, "CFG", {"agents": [{"app_id": "app"}]}), \
                     patch.object(hub, "reply") as reply:
                    hub.handle_message("app", {
                        "message_id": "trash-1", "message_type": "text",
                        "content": '{"text": "/trash file.txt"}',
                    }, {"open_id": "alice"})
                self.assertIn("已拒绝", reply.call_args.args[2])
                with sqlite3.connect(root / "test.db") as conn:
                    count = conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
                self.assertEqual(count, 0)

    def test_edit_requires_sender_allowlist(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(db, "DB_PATH", Path(temp) / "test.db"):
            db.init_db()
            db.upsert_device("dev", "device", "secret")
            db.upsert_agent("app", "dev", "opencode", "Agent", "serve",
                            "http://127.0.0.1:4096", "secret", temp)
            with patch.object(hub, "CFG", {"agents": [{"app_id": "app"}]}), patch.object(hub, "reply") as reply:
                hub.handle_message("app", {"message_id": "edit-1", "message_type": "text",
                                           "content": '{"text": "/edit notes.txt: revise"}'},
                                   {"open_id": "alice"})
            self.assertIn("已拒绝", reply.call_args.args[2])
            with sqlite3.connect(Path(temp) / "test.db") as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

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
