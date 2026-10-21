import json
import logging
import threading

import lark_oapi as lark
from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

log = logging.getLogger("feishu")


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

    def _build_handler(self, on_message):
        return (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )

    def start_ws(self, on_message):
        def wrapper(data):
            self._dispatch(data, on_message)

        handler = self._build_handler(wrapper)
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

    def stop(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
