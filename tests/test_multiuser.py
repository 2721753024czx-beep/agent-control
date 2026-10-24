import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hub"), str(ROOT / "node")]
import db
import hub
from access import policy, validate_agents
from node import execute
from runtime import NodeRuntime


class MultiuserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(db, "DB_PATH", self.root / "test.db")
        self.db_patch.start()
        db.init_db()
        db.upsert_device("dev", "PC", "token")
        db.upsert_agent("app", "dev", "opencode", "Agent", "serve", "http://127.0.0.1:4096", "secret", str(self.root))
        self.agent = {"app_id": "app", "device_id": "dev", "work_dir": str(self.root),
                      "allowed_open_ids": ["alice", "bob"], "owner_open_id": "alice",
                      "access_mode": "shared"}

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def send(self, mid, text, sender="alice", chat_type="p2p"):
        hub.handle_message("app", {"message_id": mid, "chat_id": "chat", "chat_type": chat_type,
                                   "message_type": "text", "content": json.dumps({"text": text})}, {"open_id": sender})

    def test_personal_default_no_first_sender_takeover_or_implicit_sharing(self):
        self.assertEqual(policy({"allowed_open_ids": ["alice"]})["members"], {"alice": "editor"})
        for cfg in ({}, {"allowed_open_ids": ["alice", "bob"]}):
            with self.assertRaises(ValueError):
                policy(cfg)
        self.agent["access_mode"] = "personal"
        with patch.object(hub, "CFG", {"agents": [self.agent]}), patch.object(hub, "reply"):
            self.send("bob", "read", "bob")
            self.send("group", "private", chat_type="group")
            self.send("owner", "read")
        with db._connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)
        task, _ = db.claim_task("dev", "token")
        self.assertEqual(task["sender_open_id"], "alice")

    def test_team_readers_cannot_edit_and_editors_cannot_approve_others(self):
        with patch.object(hub, "CFG", {"agents": [self.agent]}), patch.object(hub, "reply") as reply:
            self.send("read", "read VERSION", "bob")
            self.send("edit", "/edit a.txt: revise", "bob")
            self.assertIn("只读", reply.call_args.args[2])
            self.send("trash", "/trash a.txt", "bob")
            self.send("stop", "/stop")
        task, _ = db.claim_task("dev", "token")
        self.assertEqual(task["actor_role"], "reader")
        opened = db.create_approval(task["task_id"], "token", "session", "permission", "read", "VERSION")
        self.assertIsNone(db.decide_approval("app", opened["code"], "alice", "chat", "approved"))
        self.assertEqual(db.decide_approval("app", opened["code"], "bob", "chat", "approved"), "approved")
        db.report_task(task["task_id"], "token", result="done")
        self.agent["member_roles"] = {"bob": "editor"}
        with patch.object(hub, "CFG", {"agents": [self.agent]}), patch.object(hub, "reply"):
            self.send("editor", "/edit a.txt: revise", "bob")
        task, _ = db.claim_task("dev", "token")
        self.assertEqual(task["actor_role"], "editor")

    def test_concurrent_poll_is_fifo_and_survives_hub_init_and_timeout(self):
        first = db.create_task("app", "dev", "first", "m1", "chat", str(self.root), "alice")
        second = db.create_task("app", "dev", "second", "m2", "chat", str(self.root), "bob")
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: db.claim_task("dev", "token")[0], range(2)))
        self.assertEqual([task["task_id"] for task in claims if task], [first])
        approval = db.create_approval(first, "token", "s", "p", "read", "VERSION")
        self.assertEqual(db.claim_task("dev", "token"), (None, None))
        db.init_db()  # A Hub restart must not release the live reservation.
        with db._connect() as conn:
            conn.execute("UPDATE tasks SET started_at='2000-01-01' WHERE task_id=?", (first,))
        db.mark_timeout_tasks(1)
        self.assertEqual(db.claim_task("dev", "token"), (None, None))
        self.assertIsNone(db.decide_approval("app", approval["code"], "alice", "chat", "approved"))
        self.assertIsNone(db.report_task(first, "wrong", result="late"))
        db.report_task(first, "token", result="late")
        with db._connect() as conn:
            self.assertEqual(conn.execute("SELECT status,result FROM tasks WHERE task_id=?", (first,)).fetchone()[0], "timeout")
        self.assertEqual(db.claim_task("dev", "token")[0]["task_id"], second)

    def test_multiple_agents_one_device_and_local_crash_recovery(self):
        db.upsert_agent("other", "dev", "opencode", "Other", "serve", "http://127.0.0.1:4096", "secret", str(self.root / "other"))
        a = db.create_task("app", "dev", "a")
        b = db.create_task("other", "dev", "b")
        self.assertEqual(db.claim_task("dev", "token")[0]["task_id"], a)
        self.assertIsNone(db.claim_task("dev", "token")[0])
        self.assertEqual(db.release_device_execution("dev"), 1)
        self.assertEqual(db.claim_task("dev", "token")[0]["task_id"], b)

    def test_queue_limit_history_and_queued_revocation(self):
        with patch.object(hub, "CFG", {"agents": [self.agent]}), patch.object(hub, "reply") as reply:
            for number in range(6):
                self.send(str(number), "alice task")
            self.assertIn("上限", reply.call_args.args[2])
            self.send("bob", "bob task", "bob")
            self.send("history", "/history", "bob")
            own_history = reply.call_args.args[2]
            with db._connect() as conn:
                alice = conn.execute("SELECT task_id FROM tasks WHERE sender_open_id='alice' LIMIT 1").fetchone()[0]
            self.assertNotIn(alice, own_history)
            self.send("queue", "/queue", "bob")
            self.assertIn("不会", reply.call_args.args[2])
            self.agent["allowed_open_ids"] = ["bob"]
            self.agent["owner_open_id"] = "bob"
            self.assertIsNone(hub.pull_tasks("dev", x_device_token="token")["task"])
        with db._connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE task_id=?", (alice,)).fetchone()[0], "failed")

    def test_workspace_overlap_and_node_role_boundary(self):
        other = {**self.agent, "app_id": "other", "work_dir": str(self.root / "nested")}
        with self.assertRaises(ValueError):
            validate_agents([self.agent, other])
        win = {**self.agent, "work_dir": "C:/Projects/A"}
        other = {**win, "app_id": "other", "work_dir": "c:\\projects\\a\\nested"}
        with self.assertRaises(ValueError):
            validate_agents([win, other])
        runtime = NodeRuntime({"allowed_work_dirs": [str(self.root)]}, self.root)
        task = {"task_id": "t", "prompt": "/trash a.txt", "actor_role": "reader",
                "engine": {"type": "serve", "work_dir": str(self.root)}}
        with self.assertRaisesRegex(PermissionError, "只读"):
            execute(task, 30, runtime)
        runtime.config["agent_work_dirs"] = {"other": [str(self.root)]}
        task["actor_role"] = "editor"
        task["app_id"] = "app"
        with self.assertRaisesRegex(PermissionError, "无权"):
            execute(task, 30, runtime)


if __name__ == "__main__":
    unittest.main()
