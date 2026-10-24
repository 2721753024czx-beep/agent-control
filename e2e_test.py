#!/usr/bin/env python3
"""Isolated Hub -> Node -> reply test. Never touches real config or database."""
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
REAL_CONFIG = ROOT / "hub" / "config.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    before = digest(REAL_CONFIG)
    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        os.environ["AGENT_CONTROL_DB"] = str(temp / "hub.db")
        os.environ["AGENT_CONTROL_HUB_CONFIG"] = str(temp / "hub-config.json")
        os.environ["AGENT_CONTROL_NODE_CONFIG"] = str(temp / "node-config.json")
        sys.path.insert(0, str(ROOT / "hub"))
        sys.path.insert(0, str(ROOT / "node"))

        import db
        import hub as hubmod
        from node import execute
        from runtime import NodeRuntime

        serve_url = "http://127.0.0.1:4096"
        config = {
            "admin_token": "adm",
            "task_timeout_seconds": 900,
            "devices": [{"device_id": "win", "name": "Windows", "token": "tok"}],
            "agents": [{
                "app_id": "serve_test", "device_id": "win", "agent_name": "opencode",
                "display_name": "Windows-OpenCode", "engine_type": "serve",
                "exec_path": serve_url, "work_dir": str(temp), "app_secret": "sec",
                "allowed_open_ids": ["u1"],
            }],
        }
        (temp / "hub-config.json").write_text(json.dumps(config), encoding="utf-8")
        (temp / ".agent-memory").mkdir()
        (temp / ".agent-memory" / "index.md").write_text("isolated memory", encoding="utf-8")

        class FakeClient:
            def __init__(self):
                self.replies = []

            def reply(self, message_id, text):
                self.replies.append(("text", message_id, text))

            def reply_card(self, message_id, title, text, template="blue"):
                self.replies.append(("card", message_id, title, text))

        db.DB_PATH = temp / "hub.db"
        db.init_db()
        db.upsert_device("win", "Windows", "tok")
        db.upsert_agent(
            "serve_test", "win", "opencode", "Windows-OpenCode", "serve",
            serve_url, "sec", str(temp), None,
        )
        fake = FakeClient()
        hubmod.FEISHU_CLIENTS = {"serve_test": fake}
        hubmod.CFG = config

        hubmod.handle_message("serve_test", {
            "message_id": "om_1", "chat_id": "oc_1", "chat_type": "p2p",
            "message_type": "text", "content": json.dumps({"text": "hello windows"}),
        }, {"open_id": "u1", "sender_type": "user"})

        task, error = db.claim_task("win", "tok")
        assert error is None and task["prompt"] == "hello windows"
        runtime = NodeRuntime({
            "history_dir": str(temp / "history"),
            "budget_state_path": str(temp / "runtime" / "budget.json"),
            "budget": {"max_tasks_per_day": 10, "max_runtime_seconds_per_day": 60},
        }, temp)
        def fake_serve(engine, prompt, timeout, approval_client):
            assert engine["base_url"] == serve_url
            assert engine["work_dir"] == str(temp.resolve())
            assert "isolated memory" in prompt and "hello windows" in prompt
            return "hello windows", "ses_fixture"

        with patch("node.run_serve", side_effect=fake_serve):
            result, session_id = execute(task, 30, runtime)
        assert "hello windows" in result
        hubmod.report(task["task_id"], {"token": "tok", "result": result, "session_id": session_id})

        assert fake.replies[0][0] == "text" and "收到" in fake.replies[0][2]
        assert fake.replies[-1][0] == "card" and "hello windows" in fake.replies[-1][3]
        events = runtime.audit.recent(10)
        assert [item["event"] for item in events][-2:] == ["task_start", "task_done"]

        # Exercise a complete, isolated /edit proposal and the Feishu approval
        # command. No real message, credential, or workspace is touched.
        config["agents"][0]["allowed_open_ids"] = ["u1"]
        target = temp / "note.txt"
        target.write_text("before\n", encoding="utf-8")

        def send(message_id, text, sender="u1", chat="oc_1"):
            hubmod.handle_message("serve_test", {
                "message_id": message_id, "chat_id": chat, "chat_type": "p2p",
                "message_type": "text", "content": json.dumps({"text": text}),
            }, {"open_id": sender, "sender_type": "user"})

        def decide_once(task_id, decision):
            def approval_client(session_id, permission_id, permission, item):
                assert permission == "safe_change" and "+after" in item["patterns"][0]
                assert target.read_text(encoding="utf-8") == "before\n"
                opened = hubmod.request_approval(task_id, {
                    "session_id": session_id, "permission_id": permission_id,
                    "permission": permission, "detail": item["patterns"][0],
                }, "tok")
                code = opened["code"]
                assert db.decide_approval("serve_test", code, "other", "oc_1", "approved") is None
                assert db.decide_approval("serve_test", code, "u1", "other", "approved") is None
                send("decision-" + task_id, "/" + decision + " " + code)
                state = hubmod.approval_status(code, "tok")
                assert state["status"] == ("approved" if decision == "approve" else "rejected")
                return state["status"] == "approved"
            return approval_client

        send("edit-approve", "/edit note.txt: replace before with after")
        edit_task, error = db.claim_task("win", "tok")
        assert error is None and edit_task["prompt"].startswith("/edit ")
        with patch("node.run_serve", return_value=('{"content":"after\\n"}', "ses_edit_approve")):
            result, session_id = execute(edit_task, 30, runtime,
                                         decide_once(edit_task["task_id"], "approve"))
        assert target.read_text(encoding="utf-8") == "after\n"
        assert (temp / ".agent-control-edits").is_dir()
        hubmod.report(edit_task["task_id"], {"token": "tok", "result": result,
                                             "session_id": session_id})

        target.write_text("before\n", encoding="utf-8")
        send("edit-reject", "/edit note.txt: replace before with after")
        rejected_task, error = db.claim_task("win", "tok")
        assert error is None
        with patch("node.run_serve", return_value=('{"content":"after\\n"}', "ses_edit_reject")):
            try:
                execute(rejected_task, 30, runtime,
                        decide_once(rejected_task["task_id"], "reject"))
            except PermissionError as exc:
                hubmod.report(rejected_task["task_id"], {"token": "tok", "error": str(exc)})
            else:
                raise AssertionError("rejected edit was applied")
        assert target.read_text(encoding="utf-8") == "before\n"

        send("trash-recoverable", "/trash note.txt")
        trash_task, error = db.claim_task("win", "tok")
        assert error is None
        trash_result, _ = execute(trash_task, 30, runtime)
        assert "可恢复暂存目录" in trash_result and not target.exists()
        manifests = list((temp / ".agent-control-trash").glob("*/manifest.json"))
        assert len(manifests) == 1
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert manifest["original"] == "note.txt"
        assert (temp / manifest["stored"]).read_text(encoding="utf-8") == "before\n"
        hubmod.report(trash_task["task_id"], {"token": "tok", "result": trash_result})

        # Attachment receive -> task-scoped transfer -> real PDF extraction ->
        # subsequent model prompt. All bytes/config remain in the temp workspace.
        import fitz
        with fitz.open() as document:
            document.new_page().insert_text((60, 80), "ISOLATED PDF CONTENT FOR SUMMARY")
            pdf_data = document.tobytes()
        fake.download_pdf = lambda message_id, key: pdf_data
        hubmod.handle_message("serve_test", {
            "message_id": "pdf-upload", "chat_id": "oc_1", "message_type": "file",
            "content": json.dumps({"file_name": "fixture.pdf", "file_key": "fixture"}),
        }, {"open_id": "u1"})
        pdf_task, error = db.claim_task("win", "tok")
        assert not error and pdf_task["attachment_receive"]
        runtime.config.update({"hub_url": "http://127.0.0.1:8000", "token": "tok"})
        raw = hubmod.download_attachment(pdf_task["task_id"], "tok").body
        with patch("urllib.request.urlopen", return_value=io.BytesIO(raw)):
            result, _ = execute(pdf_task, 30, runtime)
        assert "文字提取完成" in result
        hubmod.report(pdf_task["task_id"], {"token": "tok", "result": result})
        send("pdf-summary", "summarize this document")
        pdf_followup, error = db.claim_task("win", "tok")
        assert not error and pdf_followup["attachment"]["id"] == pdf_task["attachment"]["id"]
        def summarize(engine, prompt, timeout, approval):
            assert "ISOLATED PDF CONTENT FOR SUMMARY" in prompt
            assert "不可信资料" in prompt
            return "PDF SUMMARY OK", "pdf-session"
        with patch("node.run_serve", side_effect=summarize):
            result, _ = execute(pdf_followup, 30, runtime)
        hubmod.report(pdf_followup["task_id"], {"token": "tok", "result": result})
        assert fake.replies[-1][-1] == "PDF SUMMARY OK"

        # Team opt-in: the second user's request cannot preempt the first.
        config["agents"][0].update({"access_mode": "shared", "owner_open_id": "u1",
                                    "allowed_open_ids": ["u1", "u2"]})
        send("team-first", "first team task")
        send("team-second", "second team task", sender="u2")
        first = hubmod.pull_tasks("win", x_device_token="tok")["task"]
        assert first["sender_open_id"] == "u1" and first["actor_role"] == "editor"
        assert hubmod.pull_tasks("win", x_device_token="tok")["task"] is None
        with patch("node.run_serve", return_value=("first complete", "team-first")):
            result, _ = execute(first, 30, runtime)
        hubmod.report(first["task_id"], {"token": "tok", "result": result})
        second = hubmod.pull_tasks("win", x_device_token="tok")["task"]
        assert second["sender_open_id"] == "u2" and second["actor_role"] == "reader"
        assert not second.get("attachment")  # u1's PDF never crosses actors.
        with patch("node.run_serve", return_value=("second complete", "team-second")):
            result, _ = execute(second, 30, runtime)
        hubmod.report(second["task_id"], {"token": "tok", "result": result})
        send("reader-edit", "/edit note.txt: revise", sender="u2")
        assert "只读" in fake.replies[-1][-1]
        assert hubmod.pull_tasks("win", x_device_token="tok")["task"] is None

    assert digest(REAL_CONFIG) == before, "real hub/config.json was modified"
    print("E2E HUB -> NODE -> SERVE -> APPROVE/REJECT -> RECOVERABLE TRASH -> PDF -> TEAM FIFO -> REPLY OK")


if __name__ == "__main__":
    main()
