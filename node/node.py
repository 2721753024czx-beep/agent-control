import json
import logging
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

from engines import run_cli, run_serve
from runtime import NodeRuntime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("node")


def load_config():
    configured = os.environ.get("AGENT_CONTROL_NODE_CONFIG")
    path = Path(configured) if configured else Path(__file__).parent / "config.json"
    if not path.exists():
        path = Path(__file__).parent / "config.example.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def http_json(method, url, payload=None, timeout=30, headers=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    req = urllib.request.Request(
        url, data=data, method=method,
        headers=request_headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return resp.status, json.loads(raw.decode("utf-8")) if raw else None


def execute(task, timeout, runtime=None, approval_client=None):
    engine = task["engine"]
    if runtime:
        engine = dict(engine)
        engine["work_dir"] = runtime.validate_work_dir(engine.get("work_dir"))
        engine["serve_password"] = runtime.config.get("serve_password")
        control_result = runtime.control(task["prompt"], engine.get("work_dir"), task["task_id"])
        if control_result is not None:
            runtime.audit.append("control", task_id=task["task_id"],
                                 command=task["prompt"].strip().split(maxsplit=1)[0].lower())
            return control_result, None
        if engine["type"] == "cli" and not runtime.config.get("allow_unsafe_cli", False):
            runtime.audit.append("safety_block", task_id=task["task_id"],
                                 reason="unrestricted CLI engine")
            raise PermissionError("手机普通任务需要受限 serve 模式；CLI 模式已拒绝执行")
        allowed, reason = runtime.budget.preflight()
        if not allowed:
            runtime.audit.append("budget_block", task_id=task["task_id"], reason=reason)
            raise RuntimeError("预算保护已阻止执行：" + reason)
        runtime.budget.start()
        prompt = runtime.prepare_prompt(task["prompt"], engine.get("work_dir"))
        runtime.audit.append(
            "task_start", task_id=task["task_id"],
            prompt_sha256=runtime.prompt_digest(task["prompt"]),
            work_dir=engine.get("work_dir"),
        )
    else:
        prompt = task["prompt"]
    started = time.monotonic()
    try:
        if engine["type"] == "cli":
            result = run_cli(engine, prompt, timeout), None
        else:
            result = run_serve(engine, prompt, timeout, approval_client)
    except Exception as exc:
        if runtime:
            duration = time.monotonic() - started
            runtime.budget.finish(duration, exc)
            runtime.audit.append(
                "task_failed", task_id=task["task_id"],
                duration_seconds=round(duration, 3), error=str(exc)[:500],
            )
        raise
    if runtime:
        duration = time.monotonic() - started
        runtime.budget.finish(duration)
        runtime.audit.append("task_done", task_id=task["task_id"], duration_seconds=round(duration, 3))
    return result


def main():
    cfg = load_config()
    hub = cfg["hub_url"].rstrip("/")
    device_id = cfg["device_id"]
    token = cfg["token"]
    poll = cfg.get("poll_interval", 5)
    timeout = cfg.get("task_timeout", 600)
    pull_url = "%s/tasks?device_id=%s" % (hub, device_id)
    auth_headers = {"X-Device-Token": token}
    runtime = NodeRuntime(cfg, Path(__file__).resolve().parent.parent)

    log.info("node %s polling %s", device_id, hub)
    while True:
        try:
            _, data = http_json("GET", pull_url, headers=auth_headers)
            task = (data or {}).get("task")
            if not task:
                time.sleep(poll)
                continue
            log.info("got task %s (%s)", task["task_id"], task["engine"]["type"])
            try:
                def approval_client(session_id, permission_id, permission, item):
                    detail = ", ".join(str(part) for part in item.get("patterns", []))[:2000]
                    try:
                        _, opened = http_json(
                            "POST", "%s/tasks/%s/approvals" % (hub, task["task_id"]),
                            {"session_id": session_id, "permission_id": permission_id,
                             "permission": permission, "detail": detail},
                            headers=auth_headers,
                        )
                    except urllib.error.HTTPError as exc:
                        if exc.code == 403:
                            return False
                        raise
                    code = opened["code"]
                    deadline = time.monotonic() + 305
                    while time.monotonic() < deadline:
                        _, state = http_json("GET", "%s/approvals/%s" % (hub, code),
                                             headers=auth_headers)
                        if state["session_id"] != session_id or state["permission_id"] != permission_id:
                            return False
                        if state["status"] != "pending":
                            runtime.audit.append("approval_decision", task_id=task["task_id"],
                                                 permission=permission, status=state["status"])
                            return state["status"] == "approved"
                        time.sleep(2)
                    return False

                result, session_id = execute(task, timeout, runtime, approval_client)
                report = {"token": token, "result": result, "session_id": session_id}
                http_json("POST", "%s/tasks/%s/report" % (hub, task["task_id"]), report)
                log.info("reported %s done", task["task_id"])
            except Exception as e:
                log.exception("exec failed")
                report = {"token": token, "error": str(e)[:2000]}
                http_json("POST", "%s/tasks/%s/report" % (hub, task["task_id"]), report)
        except Exception as e:
            log.error("poll failed: %s", e)
            time.sleep(poll)


if __name__ == "__main__":
    main()
