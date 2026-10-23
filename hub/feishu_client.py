import json
import logging
import threading

import lark_oapi as lark
from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

log = logging.getLogger("feishu")


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
        self._thread = threading.Thread(target=self._ws.start, daemon=True)
        self._thread.start()

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
            try:
                self._ws.close()
            except Exception:
                pass
