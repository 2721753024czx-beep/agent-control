import json
import os
import shlex
import shutil
import subprocess
import time
import base64
import urllib.parse
import urllib.request


def build_cli_command(command, prompt, platform=None):
    """Build argv without parsing user-controlled prompt as shell syntax.

    OpenCode's official Windows installer provides ``opencode.exe``. Batch-file
    shims are intentionally rejected because cmd.exe would parse prompt text as
    command syntax. This keeps Feishu messages data-only on every platform.
    """
    if isinstance(command, list):
        parts = [str(p) for p in command]
    else:
        parts = shlex.split(str(command), posix=True)
    if not parts:
        raise ValueError("empty CLI command")
    if not any("{prompt}" in part for part in parts):
        raise ValueError("CLI command must contain {prompt}")
    parts = [part.replace("{prompt}", prompt) for part in parts]

    current_platform = os.name if platform is None else platform
    executable = shutil.which(parts[0]) or parts[0]
    if current_platform == "nt" and executable.lower().endswith((".cmd", ".bat")):
        raise RuntimeError(
            "OpenCode resolved to a .cmd/.bat shim. Install the official "
            "opencode.exe and place it on PATH to keep prompts shell-safe."
        )
    parts[0] = executable
    return parts


def run_cli(engine, prompt, timeout):
    parts = build_cli_command(engine["command"], prompt)
    proc = subprocess.run(
        parts,
        cwd=engine.get("work_dir"),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "exit=%s stderr=%s" % (proc.returncode, (proc.stderr or "")[:2000]))
    return (proc.stdout or "").strip() or (proc.stderr or "").strip()


def _extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    out.append(item.get("text", ""))
                elif item.get("text"):
                    out.append(item["text"])
        return "".join(out)
    return ""


SAFE_REMOTE_TOOLS = frozenset({"read", "glob", "grep", "webfetch", "websearch", "skill", "todowrite"})


def run_serve(engine, prompt, timeout, on_permission=None):
    """Drive OpenCode 1.19 server with a per-prompt tool allowlist.

    Unknown/custom and mutating tools are disabled before the model runs.
    Pending permissions are resolved once; no `always` approval is sent.
    """
    base = engine["base_url"].rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("OpenCode server must be bound to loopback")
    directory = engine.get("work_dir")
    if not directory:
        raise ValueError("OpenCode serve requires a workspace")
    query = "?" + urllib.parse.urlencode({"directory": directory})
    password = engine.get("serve_password") or os.environ.get("OPENCODE_SERVER_PASSWORD", "")
    if not password:
        raise ValueError("protected OpenCode serve requires a password")
    headers = {"Content-Type": "application/json"}
    if password:
        credentials = base64.b64encode(("opencode:" + password).encode()).decode()
        headers["Authorization"] = "Basic " + credentials

    def request(method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base + path + query, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    tool_ids = request("GET", "/experimental/tool/ids")
    if not isinstance(tool_ids, list) or not tool_ids or "read" not in tool_ids:
        raise RuntimeError("cannot verify OpenCode tool list; remote task stopped")
    config = request("GET", "/config") or {}
    permissions = config.get("permission") or {}
    if not isinstance(permissions, dict) or any(
        permissions.get(name) != "deny"
        for name in ("*", "bash", "shell", "edit", "write", "apply_patch",
                     "task", "external_directory")
    ) or permissions.get("read") != "ask":
        raise RuntimeError("OpenCode server lacks mandatory remote safety permissions")
    agents = config.get("agent") or {}
    if not isinstance(agents, dict) or any(
        isinstance(agent, dict) and "permission" in agent
        for agent in agents.values()
    ):
        raise RuntimeError("OpenCode agent-specific permissions are not allowed for remote tasks")
    # Explicit `True` overrides OpenCode's permission rules (including
    # `read: ask` and even `read: deny`). Only disable disallowed tools here;
    # omitted tools still obey the server's permission policy.
    tools = {tool_id: False for tool_id in tool_ids if tool_id not in SAFE_REMOTE_TOOLS}
    created = request("POST", "/session", {})
    session_id = created.get("id") if isinstance(created, dict) else None
    if not session_id:
        raise RuntimeError("cannot create OpenCode session")
    request("POST", "/session/%s/prompt_async" % session_id,
            {"parts": [{"type": "text", "text": prompt}], "tools": tools})

    deadline = time.monotonic() + timeout
    handled = set()
    completed = False
    try:
        while time.monotonic() < deadline:
            pending = request("GET", "/permission") or []
            for item in pending:
                if item.get("sessionID") != session_id:
                    continue
                permission_id = item.get("id") or item.get("requestID")
                if not permission_id or permission_id in handled:
                    continue
                handled.add(permission_id)
                permission = str(item.get("permission") or "").lower()
                allowed = False
                if permission in SAFE_REMOTE_TOOLS and on_permission:
                    allowed = bool(on_permission(session_id, permission_id, permission, item))
                request("POST", "/permission/%s/reply" % permission_id,
                        {"reply": "once" if allowed else "reject"})
                if not allowed:
                    raise PermissionError("手机端审批已拒绝：%s" % permission)

            statuses = request("GET", "/session/status") or {}
            messages = request("GET", "/session/%s/message" % session_id) or []
            assistants = [entry for entry in messages
                          if entry.get("info", {}).get("role") == "assistant"]
            if assistants and session_id not in statuses:
                latest = assistants[-1]
                if latest.get("info", {}).get("error"):
                    raise RuntimeError("OpenCode session failed: %s" % latest["info"]["error"])
                result = _extract_text(latest.get("parts", [])).strip()
                if result:
                    completed = True
                    return result, session_id
            time.sleep(1)
    finally:
        if not completed:
            try:
                request("POST", "/session/%s/abort" % session_id, {})
            except Exception:
                pass
    raise TimeoutError("OpenCode serve task timed out")
