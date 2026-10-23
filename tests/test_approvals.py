import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))
import db  # noqa: E402
import hub  # noqa: E402
from feishu_client import FeishuClient, approval_card  # noqa: E402
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

    def test_card_decision_requires_original_card_and_running_task(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(db, "DB_PATH", Path(temp) / "test.db"):
            db.init_db()
            db.upsert_device("pc", "PC", "token")
            db.upsert_agent("app", "pc", "agent", "Agent", "serve",
                            "http://127.0.0.1:4096", "secret", temp)
            task = db.create_task("app", "pc", "read", "msg", "chat", temp, "alice")
            db.claim_task("pc", "token")
            approval = db.create_approval(task, "token", "session", "permission", "read", "file")
            code = approval["code"]
            self.assertIsNone(db.decide_approval("app", code, "alice", "chat", "approved", "card"))
            self.assertTrue(db.set_approval_card_message(code, "card"))
            self.assertFalse(db.set_approval_card_message(code, "other"))
            for sender, chat, card in (("bob", "chat", "card"), ("alice", "other", "card"),
                                       ("alice", "chat", "other")):
                self.assertIsNone(db.decide_approval("app", code, sender, chat, "approved", card))
            self.assertEqual(db.decide_approval("app", code, "alice", "chat", "rejected", "card"),
                             "rejected")
            self.assertIsNone(db.decide_approval("app", code, "alice", "chat", "approved", "card"))

            second = db.create_approval(task, "token", "session", "second", "read", "other")
            self.assertTrue(db.set_approval_card_message(second["code"], "card2"))
            db.report_task(task, "token", result="done")
            self.assertIsNone(db.decide_approval("app", second["code"], "alice", "chat",
                                                  "approved", "card2"))
            self.assertEqual(db.get_approval(second["code"], "token")["status"], "expired")

    def test_card_payload_and_callback_are_once_only(self):
        card = approval_card("safe_change", "--- a/note.txt\n+++ b/note.txt\n-old\n+new", "a123456789")
        self.assertEqual(card["schema"], "2.0")
        self.assertFalse(card["config"]["enable_forward"])
        elements = card["body"]["elements"]
        self.assertIn("+new", elements[1]["elements"][0]["text"]["content"])
        buttons = [column["elements"][0] for column in elements[2]["columns"]]
        self.assertEqual([button["behaviors"][0]["value"]["decision"] for button in buttons],
                         ["approved", "rejected"])

        with tempfile.TemporaryDirectory() as temp, patch.object(db, "DB_PATH", Path(temp) / "test.db"):
            db.init_db()
            db.upsert_device("pc", "PC", "token")
            db.upsert_agent("app", "pc", "agent", "Agent", "serve", "http://127.0.0.1:4096", "secret", temp)
            task = db.create_task("app", "pc", "read", "msg", "chat", temp, "alice")
            db.claim_task("pc", "token")
            approval = db.create_approval(task, "token", "session", "permission", "read", "file")
            code = approval["code"]
            self.assertTrue(db.set_approval_card_message(code, "card"))
            event = {"tag": "button", "value": {"kind": "approval", "code": code,
                                                  "decision": "approved"},
                     "open_id": "alice", "chat_id": "chat", "message_id": "card"}
            for key, wrong in (("open_id", "bob"), ("chat_id", "other"),
                               ("message_id", "other")):
                self.assertFalse(hub.handle_card_action("app", {**event, key: wrong})["ok"])
            self.assertFalse(hub.handle_card_action("other-app", event)["ok"])
            self.assertTrue(hub.handle_card_action("app", event)["ok"])
            self.assertFalse(hub.handle_card_action("app", event)["ok"])

    def test_card_dispatch_returns_toast(self):
        client = FeishuClient.__new__(FeishuClient)
        event = SimpleNamespace(event=SimpleNamespace(
            action=SimpleNamespace(tag="button", value={"code": "abc"}),
            operator=SimpleNamespace(open_id="alice"),
            context=SimpleNamespace(open_chat_id="chat", open_message_id="card")))
        seen = []
        response = client._dispatch_card_action(event, lambda value: (
            seen.append(value) or {"ok": True, "text": "已批准一次。"}))
        self.assertEqual(seen[0]["message_id"], "card")
        self.assertEqual(response.toast.type, "success")
        self.assertEqual(response.toast.content, "已批准一次。")

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

    def test_hub_sends_card_and_binds_message_id(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(db, "DB_PATH", Path(temp) / "test.db"):
            db.init_db()
            db.upsert_device("pc", "PC", "token")
            db.upsert_agent("app", "pc", "agent", "Agent", "serve", "http://127.0.0.1:4096", "secret", temp)
            task = db.create_task("app", "pc", "read", "msg", "chat", temp, "alice")
            db.claim_task("pc", "token")
            fake = SimpleNamespace(reply_approval_card=lambda *args: "card-id")
            with patch.dict(hub.FEISHU_CLIENTS, {"app": fake}), patch.object(hub, "reply") as fallback:
                result = hub.request_approval(task, {"permission": "read", "session_id": "session",
                                                     "permission_id": "permission", "detail": "file"}, "token")
            fallback.assert_not_called()
            self.assertEqual(db.get_approval(result["code"], "token")["card_message_id"], "card-id")


if __name__ == "__main__":
    unittest.main()
