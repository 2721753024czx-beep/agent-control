import asyncio
import importlib
import json
import logging
import threading
import time
from urllib.parse import quote
import requests

import lark_oapi as lark
from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

log = logging.getLogger("feishu")

# The SDK stores its event loop at module scope. All clients must share one
# runner; calling Client.start() in multiple threads races run_until_complete.
_sdk_runner_lock = threading.Lock()
_sdk_runner = None


def _ws_loop():
    global _sdk_runner
    sdk = importlib.import_module("lark_oapi.ws.client")
    with _sdk_runner_lock:
        if _sdk_runner is None or not _sdk_runner.is_alive():
            _sdk_runner = threading.Thread(target=sdk.loop.run_forever, daemon=True)
            _sdk_runner.start()
    return sdk.loop


def approval_card(permission, detail, code):
    """One-time approval card; the complete request stays visible as plain text."""
    label = "单文件修改" if permission == "safe_change" else "只读访问"
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default", "enable_forward": False},
        "header": {
            "template": "yellow",
            "title": {"tag": "plain_text", "content": "待审批 · " + label},
            "subtitle": {"tag": "plain_text", "content": "仅本次有效 · 5 分钟后过期"},
            "icon": {"tag": "standard_icon", "token": "approval_colorful"},
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px",
            "vertical_spacing": "12px",
            "elements": [
                {"tag": "markdown", "content": "**%s**：请核对下方完整请求后选择。" % label},
                {"tag": "collapsible_panel", "expanded": True,
                 "header": {"title": {"tag": "plain_text", "content": "完整请求内容"}},
                 "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": detail}}]},
                {"tag": "column_set", "flex_mode": "bisect", "columns": [
                    {"tag": "column", "elements": [{
                        "tag": "button", "type": "primary_filled",
                        "text": {"tag": "plain_text", "content": "批准一次"},
                        "behaviors": [{"type": "callback", "value": {
                            "kind": "approval", "code": code, "decision": "approved"}}],
                    }]},
                    {"tag": "column", "elements": [{
                        "tag": "button", "type": "danger",
                        "text": {"tag": "plain_text", "content": "拒绝"},
                        "behaviors": [{"type": "callback", "value": {
                            "kind": "approval", "code": code, "decision": "rejected"}}],
                    }]},
                ]},
                {"tag": "markdown", "text_size": "notation",
                 "content": "<font color='grey'>按钮不可用时仍可发送 `/approve %s` 或 `/reject %s`。</font>" %
                            (code, code)},
            ],
        },
    }


class FeishuClient:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.api = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .build()
        self._ws = None
        self._thread = None
        self._file_slots = threading.BoundedSemaphore(2)

    def _build_handler(self, on_message, on_card_action=None):
        builder = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(on_message))
        if on_card_action is not None:
            builder = builder.register_p2_card_action_trigger(on_card_action)
        return builder.build()

    def start_ws(self, on_message, on_card_action=None):
        def wrapper(data):
            self._dispatch(data, on_message)

        def card_wrapper(data):
            return self._dispatch_card_action(data, on_card_action)

        handler = self._build_handler(wrapper, card_wrapper if on_card_action else None)
        self._ws = lark.ws.Client(
            app_id=self.app_id,
            app_secret=self.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )
        async def connect():
            try:
                await self._ws._connect()
            except Exception:
                await self._ws._disconnect()
                if not self._ws._auto_reconnect:
                    raise
                await self._ws._reconnect()
            self._ping_task = asyncio.create_task(self._ws._ping_loop())

        self._future = asyncio.run_coroutine_threadsafe(connect(), _ws_loop())

        def connected(future):
            if not future.cancelled() and future.exception() is not None:
                log.error("robot websocket startup failed for app %s", self.app_id)
        self._future.add_done_callback(connected)

    def _dispatch(self, data, callback):
        try:
            message = {
                "message_id": data.event.message.message_id,
                "chat_id": data.event.message.chat_id,
                "chat_type": data.event.message.chat_type,
                "message_type": data.event.message.message_type,
                "content": data.event.message.content,
            }
            sender = {
                "open_id": data.event.sender.sender_id.open_id,
                "sender_type": data.event.sender.sender_type,
            }
            if message["message_type"] == "file":
                if not self._file_slots.acquire(blocking=False):
                    self.reply(message["message_id"], "附件接收繁忙，请稍后重发 PDF。")
                    return
                def receive():
                    try:
                        callback(message, sender)
                    except Exception:
                        log.exception("file dispatch error")
                    finally:
                        self._file_slots.release()
                threading.Thread(target=receive, daemon=True).start()
            else:
                callback(message, sender)
        except Exception:
            log.exception("dispatch error")

    def _dispatch_card_action(self, data, callback):
        try:
            event = data.event
            action = event.action
            context = event.context
            result = callback({
                "value": action.value,
                "tag": action.tag,
                "open_id": event.operator.open_id,
                "chat_id": context.open_chat_id,
                "message_id": context.open_message_id,
            })
            return P2CardActionTriggerResponse({"toast": {
                "type": "success" if result.get("ok") else "error",
                "content": str(result.get("text") or "操作无效"),
            }})
        except Exception:
            log.exception("card action dispatch error")
            return P2CardActionTriggerResponse({"toast": {
                "type": "error", "content": "审批处理失败，请稍后重试。",
            }})

    def reply(self, message_id, text):
        body = ReplyMessageRequestBody()
        body.content = json.dumps({"text": text})
        body.msg_type = "text"
        req = (ReplyMessageRequest.builder()
               .message_id(message_id)
               .request_body(body)
               .build())
        resp = self.api.im.v1.message.reply(req)
        if not resp.success():
            raise RuntimeError(f"reply failed code={resp.code} msg={resp.msg}")

    def download_pdf(self, message_id, file_key):
        # Stream rather than SDK's fully buffered resource response: enforce the
        # application limit even if the message's declared size is incorrect.
        with requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                           json={"app_id": self.app_id, "app_secret": self.app_secret},
                           timeout=15, allow_redirects=False) as response:
            auth = response.json()
        token = auth.get("tenant_access_token")
        if auth.get("code") != 0 or not isinstance(token, str) or not token:
            raise RuntimeError("飞书附件认证失败，请检查应用配置")
        url = "https://open.feishu.cn/open-apis/im/v1/messages/%s/resources/%s" % (
            quote(message_id, safe=""), quote(file_key, safe=""))
        data = bytearray()
        deadline = time.monotonic() + 45
        with requests.get(url, params={"type": "file"}, headers={"Authorization": "Bearer " + token},
                          stream=True, timeout=15, allow_redirects=False) as response:
            if response.status_code != 200:
                raise RuntimeError("飞书附件下载失败 HTTP %d；请检查消息资源权限与文件有效性" % response.status_code)
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > 10 * 1024 * 1024 or time.monotonic() > deadline:
                    raise ValueError("PDF 超过 10 MiB 或下载超时，请拆分后重发")
        if not data.startswith(b"%PDF-") or len(data) > 10 * 1024 * 1024:
            raise ValueError("仅支持不超过 10 MiB 的 PDF 文件")
        return bytes(data)

    def reply_card(self, message_id, title, text, template="blue"):
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": text}}
            ],
        }
        body = ReplyMessageRequestBody()
        body.content = json.dumps(card, ensure_ascii=False)
        body.msg_type = "interactive"
        req = (ReplyMessageRequest.builder()
               .message_id(message_id)
               .request_body(body)
               .build())
        resp = self.api.im.v1.message.reply(req)
        if not resp.success():
            raise RuntimeError(f"reply card failed code={resp.code} msg={resp.msg}")

    def reply_approval_card(self, message_id, permission, detail, code):
        body = ReplyMessageRequestBody()
        body.content = json.dumps(approval_card(permission, detail, code), ensure_ascii=False)
        body.msg_type = "interactive"
        req = (ReplyMessageRequest.builder()
               .message_id(message_id)
               .request_body(body)
               .build())
        resp = self.api.im.v1.message.reply(req)
        if not resp.success() or not resp.data or not resp.data.message_id:
            raise RuntimeError("approval card reply failed")
        return resp.data.message_id

    def stop(self):
        if self._ws:
            async def disconnect():
                self._ws._auto_reconnect = False
                if getattr(self, "_ping_task", None):
                    self._ping_task.cancel()
                await self._ws._disconnect()
            asyncio.run_coroutine_threadsafe(disconnect(), _ws_loop())
