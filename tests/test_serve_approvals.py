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
                return FakeResponse(["read", "glob", "bash", "edit", "apply_patch", "custom_delete"])
            if path == "/config":
                return FakeResponse({"permission": {"bash": "deny", "edit": "deny",
                                                    "task": "deny", "external_directory": "deny"}})
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
        self.assertEqual(prompt["tools"], {"read": True, "glob": True,
                                          "bash": False, "edit": False,
                                          "apply_patch": False, "custom_delete": False})
        self.assertIn(("POST", "/permission/per_test/reply", {"reply": "once"}), requests)

    def test_rejects_remote_server(self):
        with self.assertRaises(ValueError):
            run_serve({"base_url": "http://example.com:4096", "work_dir": str(ROOT)}, "test", 1)


if __name__ == "__main__":
    unittest.main()
