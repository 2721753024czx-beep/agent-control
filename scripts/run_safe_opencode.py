#!/usr/bin/env python3
"""Launch a loopback-only OpenCode server for Feishu remote tasks."""
import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("AGENT_CONTROL_NODE_CONFIG") or ROOT / "node" / "config.json")


def main():
    with open(CONFIG, encoding="utf-8") as handle:
        config = json.load(handle)
    password = config.get("serve_password") or ""
    if len(password) < 32:
        raise SystemExit("node/config.json 需要至少 32 字符的 serve_password")
    argv = config.get("opencode_argv") or [config.get("opencode_executable") or shutil.which("opencode")]
    if not argv or not argv[0]:
        raise SystemExit("未找到 opencode")
    port = int(config.get("serve_port", 4096))
    if port < 1024 or port > 65535:
        raise SystemExit("serve_port must be 1024..65535")
    env = os.environ.copy()
    env["OPENCODE_SERVER_PASSWORD"] = password
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"permission": {
        "bash": "deny", "edit": "deny", "task": "deny",
        "external_directory": "deny", "read": "ask",
        "glob": "allow", "grep": "allow", "list": "allow",
        "webfetch": "ask", "websearch": "ask", "skill": "allow",
        "todowrite": "allow",
    }})
    command = list(argv) + ["serve", "--pure", "--hostname", "127.0.0.1", "--port", str(port)]
    raise SystemExit(subprocess.call(command, cwd=config.get("default_work_dir") or ROOT, env=env))


if __name__ == "__main__":
    main()
