import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "node"))

from runtime import AuditLog, BudgetGuard, NodeRuntime, list_skills, read_shared_memory  # noqa: E402
from node import execute  # noqa: E402


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_shared_memory_order_and_limit(self):
        memory = self.root / ".agent-memory"
        memory.mkdir()
        for name in ("index.md", "requirements.md", "context.md", "decisions.md"):
            (memory / name).write_text(name + " body", encoding="utf-8")
        text = read_shared_memory(self.root, 1000)
        self.assertLess(text.index("index.md"), text.index("requirements.md"))
        self.assertLess(text.index("requirements.md"), text.index("context.md"))

    def test_prompt_uses_injected_memory_without_requiring_duplicate_reads(self):
        memory = self.root / ".agent-memory"
        memory.mkdir()
        (memory / "index.md").write_text("project overview", encoding="utf-8")
        runtime = NodeRuntime({"history_dir": str(self.root / "history")}, self.root)
        prompt = runtime.prepare_prompt("读取 VERSION", str(self.root))
        self.assertIn("project overview", prompt)
        self.assertIn("不要为重复确认而再次读取这些文件", prompt)
        self.assertIn("若快照缺少任务必需的信息", prompt)
        self.assertIn("纯查询任务无需产生记忆更新", prompt)
        self.assertIn("不要声称已回写", prompt)
        self.assertIn("最终只答复用户问题", prompt)

    def test_skill_listing(self):
        skill = self.root / ".agents" / "skills" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: demo skill\n---\n", encoding="utf-8"
        )
        items = list_skills(self.root)
        self.assertTrue(any(
            item["name"] == "demo" and item["source"] == "project-agents"
            for item in items
        ))

    def test_opencode_skill_overrides_compatible_project_skill(self):
        compatible = self.root / ".agents" / "skills" / "demo"
        preferred = self.root / ".opencode" / "skills" / "demo"
        compatible.mkdir(parents=True)
        preferred.mkdir(parents=True)
        (compatible / "SKILL.md").write_text(
            "---\nname: demo\ndescription: compatible\n---\n", encoding="utf-8"
        )
        (preferred / "SKILL.md").write_text(
            "---\nname: demo\ndescription: preferred\n---\n", encoding="utf-8"
        )
        items = [item for item in list_skills(self.root) if item["name"] == "demo"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "project-opencode")
        self.assertEqual(items[0]["description"], "preferred")

    def test_budget_daily_limit_and_quota_cooldown(self):
        path = self.root / "budget.json"
        guard = BudgetGuard({
            "max_tasks_per_day": 1,
            "max_runtime_seconds_per_day": 100,
            "quota_error_cooldown_seconds": 60,
        }, path)
        self.assertEqual(guard.preflight(), (True, None))
        guard.start()
        allowed, reason = guard.preflight()
        self.assertFalse(allowed)
        self.assertIn("每日任务上限", reason)

        path.unlink()
        guard.finish(1.0, "This request would exceed your available credits")
        allowed, reason = guard.preflight()
        self.assertFalse(allowed)
        self.assertIn("冷却", reason)

    def test_control_commands_do_not_consume_budget(self):
        config = {
            "history_dir": str(self.root / "history"),
            "budget_state_path": str(self.root / "budget.json"),
            "budget": {"max_tasks_per_day": 2},
        }
        runtime = NodeRuntime(config, self.root)
        result = runtime.control("/status", str(self.root))
        self.assertIn("今日已启动任务 0/2", result)
        self.assertFalse((self.root / "budget.json").exists())

    def test_phone_task_rejects_unrestricted_cli_even_with_legacy_opt_in(self):
        runtime = NodeRuntime({"history_dir": str(self.root / "history"),
                               "allow_unsafe_cli": True}, self.root)
        task = {
            "task_id": "t_safe", "prompt": "delete a file",
            "engine": {"type": "cli", "command": "opencode run {prompt}",
                       "work_dir": str(self.root)},
        }
        with patch("node.run_cli") as run_cli, self.assertRaises(PermissionError):
            execute(task, 5, runtime)
        run_cli.assert_not_called()
        self.assertEqual(runtime.audit.recent(1)[0]["event"], "safety_block")

    def test_audit_jsonl(self):
        audit = AuditLog(self.root / "history")
        audit.append("task_start", task_id="t_1", prompt_sha256="abc")
        items = audit.recent(5)
        self.assertEqual(items[-1]["event"], "task_start")
        self.assertNotIn("prompt", items[-1])


if __name__ == "__main__":
    unittest.main()
