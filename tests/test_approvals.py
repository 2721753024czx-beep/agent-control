import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))
import db  # noqa: E402
import hub  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class ApprovalTests(unittest.TestCase):
    def test_once_only_same_sender_same_chat_and_device_token(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(db, "DB_PATH", Path(temp) / "test.db"):
            db.init_db()
            db.upsert_device("pc", "PC", "token")
            db.upsert_agent("app", "pc", "agent", "Agent", "cli", "echo {prompt}", "secret", temp)
            task = db.create_task("app", "pc", "hello", "msg", "chat", temp, "alice")
            db.claim_task("pc", "token")
            approval = db.create_approval(task, "token", "session", "permission", "read", "/file")
            self.assertTrue(approval["created"])
            self.assertFalse(db.create_approval(task, "token", "session", "permission", "read", "/file")["created"])
            self.assertIsNone(db.get_approval(approval["code"], "wrong"))
            self.assertIsNone(db.decide_approval("app", approval["code"], "bob", "chat", "approved"))
            self.assertIsNone(db.decide_approval("app", approval["code"], "alice", "other", "approved"))
            self.assertEqual(db.decide_approval("app", approval["code"], "alice", "chat", "approved"), "approved")
            self.assertIsNone(db.decide_approval("app", approval["code"], "alice", "chat", "approved"))
            self.assertEqual(db.get_approval(approval["code"], "token")["status"], "approved")

    def test_mutating_permission_cannot_be_approved_remotely(self):
        for permission in ("bash", "shell", "edit", "task", "external_directory"):
            with self.subTest(permission=permission), self.assertRaises(HTTPException) as caught:
                hub.request_approval("missing", {"permission": permission}, "token")
            self.assertEqual(caught.exception.status_code, 403)

    def test_safe_change_requires_edit_task_and_allowlisted_sender(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(db, "DB_PATH", Path(temp) / "test.db"):
            db.init_db()
            db.upsert_device("pc", "PC", "token")
            db.upsert_agent("app", "pc", "agent", "Agent", "serve", "http://127.0.0.1:4096", "secret", temp)
            task = db.create_task("app", "pc", "/edit note.txt: revise", "msg", "chat", temp, "alice")
            db.claim_task("pc", "token")
            diff = "--- a/note.txt\n+++ b/note.txt\n@@ -1 +1 @@\n-old\n+new\n"
            args = (task, "token", "session", "safe-edit-" + task, "safe_change", diff)
            self.assertIsNone(db.create_approval(*args, safe_change_senders={"app": []}))
            self.assertIsNone(db.create_approval(*args, safe_change_senders=None))
            self.assertIsNone(db.create_approval(*args[:-1], "unrelated diff",
                                                  safe_change_senders={"app": ["alice"]}))
            approval = db.create_approval(*args, safe_change_senders={"app": ["alice"]})
            self.assertTrue(approval["created"])
            self.assertIsNone(db.decide_approval("app", approval["code"], "bob", "chat", "approved"))
            self.assertEqual(db.decide_approval("app", approval["code"], "alice", "chat", "approved"), "approved")

    def test_hub_safe_change_gate_requires_configured_allowlist(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(db, "DB_PATH", Path(temp) / "test.db"):
            db.init_db()
            db.upsert_device("pc", "PC", "token")
            db.upsert_agent("app", "pc", "agent", "Agent", "serve", "http://127.0.0.1:4096", "secret", temp)
            task = db.create_task("app", "pc", "/edit note.txt: revise", "msg", "chat", temp, "alice")
            db.claim_task("pc", "token")
            payload = {"permission": "safe_change", "session_id": "session",
                       "permission_id": "safe-edit-" + task,
                       "detail": "--- a/note.txt\n+++ b/note.txt\n@@ -1 +1 @@\n-old\n+new\n"}
            with patch.object(hub, "CFG", {"agents": [{"app_id": "app"}]}):
                with self.assertRaises(HTTPException) as caught:
                    hub.request_approval(task, payload, "token")
                self.assertEqual(caught.exception.status_code, 404)
            with patch.object(hub, "CFG", {"agents": [{"app_id": "app", "allowed_open_ids": ["alice"]}]}), \
                 patch.object(hub, "reply") as reply:
                response = hub.request_approval(task, payload, "token")
                self.assertIn("待审批 [safe_change]", reply.call_args.args[2])
                self.assertEqual(db.get_approval(response["code"], "token")["detail"], payload["detail"])


if __name__ == "__main__":
    unittest.main()
