import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response

from db import (
    claim_task, create_task, create_approval, decide_approval, get_approval,
    get_agent, init_db, list_agents,
    get_workspace_selection, set_workspace_selection,
    mark_message_processed, mark_timeout_tasks, message_seen,
    report_task, set_approval_card_message, set_display_name, upsert_agent, upsert_device,
    store_attachment, current_attachment, task_attachment,
)
from feishu_client import FeishuClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hub")

CFG = {}
FEISHU_CLIENTS = {}

app = FastAPI(title="Agent Control Hub")


def load_config():
    configured = os.environ.get("AGENT_CONTROL_HUB_CONFIG")
    path = Path(configured) if configured else Path(__file__).parent / "config.json"
    if not path.exists():
        path = Path(__file__).parent / "config.example.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def strip_mention(text):
    return re.sub(r"@_user_\S+", "", text).strip()


def reply(app_id, message_id, text):
    fc = FEISHU_CLIENTS.get(app_id)
    if fc is None:
        log.warning("no client for %s", app_id)
        return
    fc.reply(message_id, text)


def _chunks(text, limit=9000):
    if limit <= 0:
        raise ValueError("chunk limit must be positive")
    parts = text.split("\n\n")
    chunks, cur = [], ""
    for p in parts:
        while len(p) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            split_at = p.rfind("\n", 0, limit + 1)
            if split_at <= 0:
                split_at = limit
            chunks.append(p[:split_at])
            p = p[split_at:].lstrip("\n")
        if cur and len(cur) + len(p) + 2 > limit:
            chunks.append(cur)
            cur = p
        else:
            cur = p if not cur else cur + "\n\n" + p
    if cur:
        chunks.append(cur)
    return chunks or [text]


def reply_final(app_id, message_id, title, text, template="blue"):
    fc = FEISHU_CLIENTS.get(app_id)
    if fc is None:
        log.warning("no client for %s", app_id)
        return
    chunks = _chunks(text)
    for i, chunk in enumerate(chunks, 1):
        t = title if len(chunks) == 1 else "%s（%d/%d）" % (title, i, len(chunks))
        fc.reply_card(message_id, t, chunk, template)


def handle_message(app_id, message, sender):
    msg_id = message.get("message_id") or ""
    if not msg_id or not mark_message_processed(app_id, msg_id):
        return

    agent = get_agent(app_id)
    if agent is None or not agent["enabled"]:
        log.info("unknown or disabled app_id=%s", app_id)
        return

    configured = next((a for a in CFG.get("agents", []) if a.get("app_id") == app_id), {})
    allowed_senders = configured.get("allowed_open_ids")
    valid_allowlist = isinstance(allowed_senders, list) and all(
        isinstance(item, str) and item for item in allowed_senders)
    if allowed_senders is not None and (
            not valid_allowlist or sender.get("open_id") not in allowed_senders):
        log.warning("sender not allowed for app_id=%s", app_id)
        return

    is_file = message.get("message_type") == "file"
    if message.get("message_type") not in {"text", "file"}:
        reply(app_id, msg_id, "【%s】目前支持文本消息和 PDF 文件附件。" % agent["display_name"])
        return

    try:
        content = json.loads(message.get("content") or "{}")
        text = "" if is_file else str(content.get("text", "")).strip()
    except Exception:
        text = str(message.get("content", "")).strip()
    text = strip_mention(text)
    if not text and not is_file:
        return
    if text.lower().startswith(("/trash", "/edit")) and (not valid_allowlist or not allowed_senders):
        reply(app_id, msg_id, "电脑端尚未配置 allowed_open_ids，/trash 与 /edit 已拒绝。")
        return

    approval_match = re.fullmatch(r"/(approve|reject) ([0-9a-f]{10})", text)
    if approval_match:
        decision = "approved" if approval_match.group(1) == "approve" else "rejected"
        applied = decide_approval(app_id, approval_match.group(2),
                                  sender.get("open_id") or "",
                                  message.get("chat_id") or "", decision)
        reply(app_id, msg_id, "审批已记录：%s" % decision if applied else
              "审批无效、已处理或已过期；请检查代码和原对话。")
        return

    sender_id = sender.get("open_id") or ""
    raw_workspaces = configured.get("workspaces") or {}
    workspaces = {}
    for alias, path in raw_workspaces.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", alias):
            continue
        try:
            workspaces[alias] = str(Path(path).resolve(strict=True))
        except (OSError, ValueError):
            continue
    default_dir = agent.get("work_dir")
    if default_dir:
        try:
            default_dir = str(Path(default_dir).resolve(strict=True))
        except (OSError, ValueError):
            reply(app_id, msg_id, "当前默认工作目录不存在，请在电脑端修复配置。")
            return
    if text.startswith("/workspace"):
        words = text.split()
        if words == ["/workspace"] or words == ["/workspace", "current"]:
            alias = get_workspace_selection(app_id, sender_id) if sender_id else None
            path = workspaces.get(alias, default_dir)
            reply(app_id, msg_id, "当前工作目录：%s%s" % (path or "未配置", " (%s)" % alias if alias in workspaces else ""))
            return
        if words == ["/workspace", "list"]:
            lines = ["%s → %s" % item for item in sorted(workspaces.items())]
            reply(app_id, msg_id, "可选工作目录：\n" + ("\n".join(lines) or "尚未配置别名"))
            return
        if len(words) == 3 and words[1] == "use" and words[2] in workspaces and sender_id:
            set_workspace_selection(app_id, sender_id, words[2])
            reply(app_id, msg_id, "已切换到 %s：%s" % (words[2], workspaces[words[2]]))
            return
        reply(app_id, msg_id, "用法：/workspace current | list | use <别名>。别名须由电脑端预先配置。")
        return

    alias = get_workspace_selection(app_id, sender_id) if sender_id else None
    selected_dir = workspaces.get(alias, default_dir)
    if not selected_dir:
        reply(app_id, msg_id, "工作目录未配置，任务未启动。")
        return

    attachment_id = None
    if is_file:
        if not valid_allowlist or not allowed_senders or not sender_id or not message.get("chat_id"):
            reply(app_id, msg_id, "电脑端须配置 allowed_open_ids 后才能接收 PDF。")
            return
        try:
            name = content.get("file_name")
            key = content.get("file_key")
            if (not isinstance(name, str) or len(name) > 180 or not name.lower().endswith(".pdf")
                    or any(ord(c) < 32 for c in name) or not isinstance(key, str) or len(key) > 300):
                raise ValueError("请发送名称有效的 PDF 文件，单文件上限 10 MiB")
            if int(content.get("file_size") or 0) > 10 * 1024 * 1024:
                raise ValueError("PDF 超过 10 MiB 限制")
            data = FEISHU_CLIENTS[app_id].download_pdf(msg_id, key)
            attachment_id = store_attachment(app_id, sender_id, message["chat_id"], selected_dir, name, data)
            text = "接收并提取 PDF"
        except Exception as exc:
            log.warning("PDF receive failed: %s", type(exc).__name__)
            reply(app_id, msg_id, "PDF 接收失败：" + (str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "下载暂不可用，请稍后重发"))
            return
    elif text == "/pdf clear":
        current_attachment(app_id, sender_id, message.get("chat_id") or "", selected_dir, clear=True)
        reply(app_id, msg_id, "已取消当前 PDF 关联，文件与已有任务保留。")
        return
    elif not text.startswith("/"):
        attachment = current_attachment(app_id, sender_id, message.get("chat_id") or "", selected_dir)
        attachment_id = attachment["id"] if attachment else None

    task_id = create_task(app_id, agent["device_id"], text, msg_id,
                          message.get("chat_id"), work_dir=selected_dir,
                          sender_open_id=sender_id, attachment_id=attachment_id,
                          attachment_receive=is_file)
    log.info("task %s queued for %s", task_id, agent["device_id"])
    reply(app_id, msg_id,
          "【%s】收到，正在处理（task=%s）…" % (agent["display_name"], task_id))


def make_handler(app_id):
    def on_message(message, sender):
        handle_message(app_id, message, sender)
    return on_message


def handle_card_action(app_id, event):
    value = event.get("value")
    if (event.get("tag") != "button" or not isinstance(value, dict)
            or set(value) != {"kind", "code", "decision"}
            or value.get("kind") != "approval"
            or not isinstance(value.get("code"), str)
            or not re.fullmatch(r"[0-9a-f]{10}", value["code"])
            or value.get("decision") not in ("approved", "rejected")
            or not all(isinstance(event.get(key), str) and event[key]
                       for key in ("open_id", "chat_id", "message_id"))):
        return {"ok": False, "text": "审批操作无效。"}
    applied = decide_approval(
        app_id, value["code"], event["open_id"], event["chat_id"],
        value["decision"], card_message_id=event["message_id"])
    return ({"ok": True, "text": "已批准一次。" if applied == "approved" else "已拒绝。"}
            if applied else {"ok": False, "text": "审批无效、已处理或已过期。"})


def make_card_handler(app_id):
    def on_card_action(event):
        return handle_card_action(app_id, event)
    return on_card_action


def sweeper():
    while True:
        time.sleep(60)
        try:
            for t in mark_timeout_tasks(CFG.get("task_timeout_seconds", 900)):
                agent = get_agent(t["app_id"])
                if agent:
                    reply(t["app_id"], t["reply_message_id"],
                          "【%s】处理超时，已放弃。" % agent["display_name"])
        except Exception:
            log.exception("sweeper error")


@asynccontextmanager
async def lifespan(_app):
    global CFG
    CFG = load_config()
    init_db()
    for d in CFG.get("devices", []):
        upsert_device(d["device_id"], d["name"], d["token"])
    for a in CFG.get("agents", []):
        upsert_agent(
            app_id=a["app_id"], device_id=a["device_id"],
            agent_name=a["agent_name"], display_name=a["display_name"],
            engine_type=a["engine_type"], exec_path=a["exec_path"],
            app_secret=a["app_secret"], work_dir=a.get("work_dir"),
            session_id=a.get("session_id"),
        )
    for a in list_agents():
        if not a["enabled"]:
            continue
        fc = FeishuClient(a["app_id"], a["app_secret"])
        fc.start_ws(make_handler(a["app_id"]), make_card_handler(a["app_id"]))
        FEISHU_CLIENTS[a["app_id"]] = fc
        log.info("ws started for %s", a["display_name"])
    threading.Thread(target=sweeper, daemon=True).start()
    yield
    for fc in FEISHU_CLIENTS.values():
        fc.stop()


app.router.lifespan_context = lifespan


@app.get("/health")
def health():
    return {"ok": True, "agents": len(FEISHU_CLIENTS)}


@app.get("/tasks")
def pull_tasks(device_id: str, token: str = None,
               x_device_token: str = Header(default=None)):
    # Query token remains as a compatibility fallback. New Nodes use a header
    # so credentials do not appear in access logs or browser histories.
    task, err = claim_task(device_id, x_device_token or token)
    if err:
        raise HTTPException(status_code=401, detail=err)
    return {"task": task}


@app.get("/tasks/{task_id}/attachment")
def download_attachment(task_id: str, x_device_token: str = Header(default=None)):
    attachment = task_attachment(task_id, x_device_token, include_data=True)
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found or unauthorized")
    return Response(content=attachment["data"], media_type="application/pdf",
                    headers={"Cache-Control": "no-store"})


@app.post("/tasks/{task_id}/approvals")
def request_approval(task_id: str, payload: dict,
                     x_device_token: str = Header(default=None)):
    permission = str(payload.get("permission") or "").lower()
    # safe_change is a Node-mediated single-file proposal, not OpenCode's
    # unrestricted edit tool. Shell/edit/task permissions remain forbidden.
    if permission not in {"read", "glob", "grep", "list", "lsp", "skill", "webfetch", "websearch", "safe_change"}:
        raise HTTPException(status_code=403, detail="permission class not approvable remotely")
    session_id = str(payload.get("session_id") or "")
    permission_id = str(payload.get("permission_id") or "")
    if not session_id or not permission_id or len(session_id) > 128 or len(permission_id) > 128:
        raise HTTPException(status_code=400, detail="invalid session or permission id")
    safe_change_senders = {
        agent["app_id"]: allowed
        for agent in CFG.get("agents", [])
        if isinstance((allowed := agent.get("allowed_open_ids")), list)
        and all(isinstance(item, str) and item for item in allowed)
    }
    approval = create_approval(task_id, x_device_token, session_id, permission_id,
                               permission, str(payload.get("detail") or ""),
                               safe_change_senders=safe_change_senders)
    if approval is None:
        raise HTTPException(status_code=404, detail="task not found or unauthorized")
    if approval["status"] == "pending" and approval["created"]:
        fc = FEISHU_CLIENTS.get(approval["app_id"])
        sent = False
        if fc is not None and hasattr(fc, "reply_approval_card"):
            try:
                card_id = fc.reply_approval_card(
                    approval["reply_message_id"], approval["permission"],
                    approval["detail"], approval["code"])
                sent = set_approval_card_message(approval["code"], card_id)
                if not sent:
                    log.warning("approval card %s was sent but not bound", approval["code"])
            except Exception:
                log.exception("approval card send failed; using text fallback")
        if not sent:
            reply(approval["app_id"], approval["reply_message_id"],
                  "待审批 [%s]：%s\n只批准这一次：/approve %s\n拒绝：/reject %s\n5 分钟后过期。" %
                  (approval["permission"], approval["detail"], approval["code"], approval["code"]))
    return {"code": approval["code"], "status": approval["status"]}


@app.get("/approvals/{code}")
def approval_status(code: str, x_device_token: str = Header(default=None)):
    approval = get_approval(code, x_device_token)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    status = approval["status"]
    if status == "pending" and approval["expires_at"] <= int(time.time()):
        status = "expired"
    return {"status": status, "session_id": approval["session_id"],
            "permission_id": approval["permission_id"]}


@app.post("/tasks/{task_id}/report")
def report(task_id: str, payload: dict):
    token = payload.get("token")
    result = payload.get("result")
    error = payload.get("error")
    session_id = payload.get("session_id")
    task = report_task(task_id, token, result, error, session_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found or token invalid")
    agent = get_agent(task["app_id"])
    if agent is None:
        return {"ok": True}
    prefix = "【%s】" % agent["display_name"]
    if task["status"] == "done" and result:
        reply_final(task["app_id"], task["reply_message_id"], prefix, result)
    elif task["status"] == "failed":
        reply(task["app_id"], task["reply_message_id"],
              prefix + " 执行失败：" + (error or "未知错误"))
    return {"ok": True}


@app.post("/agents/{app_id}/display_name")
def update_display_name(app_id: str, payload: dict):
    if payload.get("admin_token") != CFG.get("admin_token"):
        raise HTTPException(status_code=401, detail="unauthorized")
    name = (payload.get("display_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="display_name required")
    if get_agent(app_id) is None:
        raise HTTPException(status_code=404, detail="agent not found")
    set_display_name(app_id, name)
    return {"ok": True, "app_id": app_id, "display_name": name}


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("AGENT_CONTROL_HOST", "0.0.0.0"), port=8000)
