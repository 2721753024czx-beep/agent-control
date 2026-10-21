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


if __name__ == "__main__":
    unittest.main()
