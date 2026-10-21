#!/usr/bin/env python3
"""Isolated Hub -> Node -> reply test. Never touches real config or database."""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


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

        echo_agent = temp / "echo_agent.py"
        echo_agent.write_text("import sys\nprint(sys.argv[1])\n", encoding="utf-8")
        command = '"%s" "%s" "{prompt}"' % (
            sys.executable.replace("\\", "/"), str(echo_agent).replace("\\", "/")
        )
        config = {
            "admin_token": "adm",
            "task_timeout_seconds": 900,
            "devices": [{"device_id": "win", "name": "Windows", "token": "tok"}],
            "agents": [{
                "app_id": "cli_test", "device_id": "win", "agent_name": "opencode",
                "display_name": "Windows-OpenCode", "engine_type": "cli",
                "exec_path": command, "work_dir": str(temp), "app_secret": "sec",
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
            "cli_test", "win", "opencode", "Windows-OpenCode", "cli",
            command, "sec", str(temp), None,
        )
        fake = FakeClient()
        hubmod.FEISHU_CLIENTS = {"cli_test": fake}
        hubmod.CFG = config

        hubmod.handle_message("cli_test", {
            "message_id": "om_1", "chat_id": "oc_1", "chat_type": "p2p",
            "message_type": "text", "content": json.dumps({"text": "hello windows"}),
        }, {"open_id": "u1", "sender_type": "user"})

        task, error = db.claim_task("win", "tok")
        assert error is None and task["prompt"] == "hello windows"
        runtime = NodeRuntime({
            "history_dir": str(temp / "history"),
            "budget_state_path": str(temp / "runtime" / "budget.json"),
            "budget": {"max_tasks_per_day": 5, "max_runtime_seconds_per_day": 60},
            "allow_unsafe_cli": True,  # Isolated echo fixture only; production defaults to deny.
        }, temp)
        result, session_id = execute(task, 30, runtime)
        assert "hello windows" in result
        hubmod.report(task["task_id"], {"token": "tok", "result": result, "session_id": session_id})

        assert fake.replies[0][0] == "text" and "收到" in fake.replies[0][2]
        assert fake.replies[-1][0] == "card" and "hello windows" in fake.replies[-1][3]
        events = runtime.audit.recent(10)
        assert [item["event"] for item in events][-2:] == ["task_start", "task_done"]

    assert digest(REAL_CONFIG) == before, "real hub/config.json was modified"
    print("E2E HUB -> NODE -> ISOLATED CLI FIXTURE -> REPLY OK")


if __name__ == "__main__":
    main()
