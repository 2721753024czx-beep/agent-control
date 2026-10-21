import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "node"))
from engines import run_serve  # noqa: E402


class FakeResponse:
    def __init__(self, value):
        self.body = io.BytesIO(json.dumps(value).encode() if value is not None else b"")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.body.close()

    def read(self):
        return self.body.read()


class ServeApprovalTests(unittest.TestCase):
    def test_read_approval_once_with_mutating_tools_disabled(self):
        requests = []
        rounds = {"count": 0}

        def urlopen(req, timeout):
            path = req.full_url.split("?", 1)[0].removeprefix("http://127.0.0.1:4096")
            body = json.loads(req.data) if req.data else None
            requests.append((req.get_method(), path, body))
            if path == "/experimental/tool/ids":
                return FakeResponse(["read", "glob", "grep", "webfetch", "websearch",
                                     "skill", "todowrite", "bash", "edit", "write",
                                     "apply_patch", "task", "custom_delete"])
            if path == "/config":
                return FakeResponse({"permission": {
                    "*": "deny", "bash": "deny", "shell": "deny", "edit": "deny",
                    "write": "deny", "apply_patch": "deny", "task": "deny",
                    "external_directory": "deny", "read": "ask"}})
            if path == "/session":
                return FakeResponse({"id": "ses_test"})
            if path.endswith("/prompt_async"):
                return FakeResponse(None)
            if path == "/permission":
                if rounds["count"] == 0:
                    return FakeResponse([{"id": "per_test", "sessionID": "ses_test",
                                          "permission": "read", "patterns": ["file.txt"]}])
                return FakeResponse([])
            if path.endswith("/reply"):
                return FakeResponse(True)
            if path == "/session/status":
                rounds["count"] += 1
                return FakeResponse({} if rounds["count"] >= 2 else {"ses_test": {"type": "busy"}})
            if path.endswith("/message"):
                return FakeResponse([{"info": {"role": "assistant"},
                                      "parts": [{"type": "text", "text": "done"}]}])
            raise AssertionError(path)

        approvals = []
        with patch("urllib.request.urlopen", side_effect=urlopen), \
             patch("time.sleep"), \
             patch.dict("os.environ", {"OPENCODE_SERVER_PASSWORD": "test-password"}):
            result, session = run_serve(
                {"base_url": "http://127.0.0.1:4096", "work_dir": str(ROOT)},
                "test", 30,
                lambda sid, pid, kind, item: approvals.append((sid, pid, kind)) or True,
            )
        self.assertEqual((result, session), ("done", "ses_test"))
        self.assertEqual(approvals, [("ses_test", "per_test", "read")])
        prompt = next(body for _, path, body in requests if path.endswith("/prompt_async"))
        self.assertEqual(prompt["tools"], {"bash": False, "edit": False,
                                          "write": False, "apply_patch": False,
                                          "task": False, "custom_delete": False})
        self.assertFalse(any(prompt["tools"].values()))
        self.assertIn(("POST", "/permission/per_test/reply", {"reply": "once"}), requests)

    def test_rejects_remote_server(self):
        with self.assertRaises(ValueError):
            run_serve({"base_url": "http://example.com:4096", "work_dir": str(ROOT)}, "test", 1)

    def test_rejects_agent_permission_override(self):
        def urlopen(req, timeout):
            path = req.full_url.split("?", 1)[0].removeprefix("http://127.0.0.1:4096")
            if path == "/experimental/tool/ids":
                return FakeResponse(["read", "bash"])
            if path == "/config":
                return FakeResponse({"permission": {
                    "*": "deny", "bash": "deny", "shell": "deny", "edit": "deny",
                    "write": "deny", "apply_patch": "deny", "task": "deny",
                    "external_directory": "deny", "read": "ask"},
                    "agent": {"build": {"permission": {"bash": "allow"}}}})
            raise AssertionError("session must not be created: " + path)

        with patch("urllib.request.urlopen", side_effect=urlopen), \
             patch.dict("os.environ", {"OPENCODE_SERVER_PASSWORD": "test-password"}):
            with self.assertRaisesRegex(RuntimeError, "agent-specific permissions"):
                run_serve({"base_url": "http://127.0.0.1:4096", "work_dir": str(ROOT)},
                          "test", 30)

    def test_rejected_permission_aborts_session_immediately(self):
        requests = []

        def urlopen(req, timeout):
            path = req.full_url.split("?", 1)[0].removeprefix("http://127.0.0.1:4096")
            body = json.loads(req.data) if req.data else None
            requests.append((req.get_method(), path, body))
            if path == "/experimental/tool/ids":
                return FakeResponse(["read", "bash"])
            if path == "/config":
                return FakeResponse({"permission": {
                    "*": "deny", "bash": "deny", "shell": "deny", "edit": "deny",
                    "write": "deny", "apply_patch": "deny", "task": "deny",
                    "external_directory": "deny", "read": "ask"}})
            if path == "/session":
                return FakeResponse({"id": "ses_test"})
            if path.endswith("/prompt_async"):
                return FakeResponse(None)
            if path == "/permission":
                return FakeResponse([{"id": "per_test", "sessionID": "ses_test",
                                      "permission": "read", "patterns": ["VERSION"]}])
            if path.endswith("/reply") or path.endswith("/abort"):
                return FakeResponse(True)
            raise AssertionError(path)

        with patch("urllib.request.urlopen", side_effect=urlopen), \
             patch.dict("os.environ", {"OPENCODE_SERVER_PASSWORD": "test-password"}):
            with self.assertRaises(PermissionError):
                run_serve({"base_url": "http://127.0.0.1:4096", "work_dir": str(ROOT)},
                          "test", 30, lambda *_: False)
        self.assertIn(("POST", "/permission/per_test/reply", {"reply": "reject"}), requests)
        self.assertIn(("POST", "/session/ses_test/abort", {}), requests)


if __name__ == "__main__":
    unittest.main()
