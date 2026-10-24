import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hub"), str(ROOT / "node")]
import db
import hub
import node
from pdf_attachments import prepare, context
from pdf_worker import extract, tessdata
from runtime import NodeRuntime
from feishu_client import FeishuClient


def pdf_bytes(scanned=False):
    import fitz
    with fitz.open() as doc:
        page = doc.new_page()
        if scanned:
            # Render text into an image; the resulting PDF has no text layer.
            with fitz.open() as original:
                p = original.new_page()
                p.insert_text((60, 80), "SCANNED DOCUMENT EXAMPLE", fontsize=22)
                p.insert_text((60, 140), "中文识别测试", fontsize=28, fontname="china-s")
                page.insert_image(page.rect, pixmap=p.get_pixmap())
        else:
            page.insert_text((60, 80), "PDF attachment integration example for testing.")
        return doc.tobytes()


class PdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(db, "DB_PATH", self.root / "test.db")
        self.db_patch.start()
        db.init_db()
        db.upsert_device("dev", "device", "secret")
        db.upsert_agent("app", "dev", "opencode", "Agent", "serve", "http://127.0.0.1:4096", "secret", str(self.root))
        self.config = {"hub_url": "http://127.0.0.1:8000", "token": "secret",
                       "allowed_work_dirs": [str(self.root)], "pdf": {"ocr": False}}

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def send(self, mid, kind, content, sender="alice", chat="chat"):
        hub.handle_message("app", {"message_id": mid, "chat_id": chat,
                                  "message_type": kind, "content": json.dumps(content)}, {"open_id": sender})

    def test_hub_receive_followup_scope_freeze_clear_and_node_processing(self):
        data = pdf_bytes()
        client = Mock()
        client.download_pdf.return_value = data
        cfg = {"agents": [{"app_id": "app", "allowed_open_ids": ["alice", "bob"]}]}
        with patch.object(hub, "CFG", cfg), patch.object(hub, "FEISHU_CLIENTS", {"app": client}), patch.object(hub, "reply"):
            self.send("file1", "file", {"file_name": "paper.pdf", "file_key": "file1"})
            self.send("file1", "file", {"file_name": "paper.pdf", "file_key": "file1"})
            self.send("text1", "text", {"text": "summarize"})
            self.send("bob", "text", {"text": "unrelated"}, sender="bob")
            self.send("otherchat", "text", {"text": "unrelated"}, chat="other")
            self.send("file2", "file", {"file_name": "next.pdf", "file_key": "file2"})
            self.send("clear", "text", {"text": "/pdf clear"})
            self.send("afterclear", "text", {"text": "unrelated"})
        receive, _ = db.claim_task("dev", "secret")
        self.assertTrue(receive["attachment_receive"])
        self.assertIsNone(db.task_attachment(receive["task_id"], "wrong", True))
        self.assertEqual(hub.download_attachment(receive["task_id"], "secret").body, data)
        self.assertEqual(db.task_attachment(receive["task_id"], "secret", True)["data"], data)
        runtime = NodeRuntime(self.config, self.root)
        def download(request, timeout):
            self.assertEqual(request.get_header("X-device-token"), "secret")
            return io.BytesIO(data)
        with patch("urllib.request.urlopen", side_effect=download), patch.object(node, "run_serve") as run:
            result, _ = node.execute(receive, 30, runtime)
            self.assertIn("文字提取完成", result)
            run.assert_not_called()
        db.report_task(receive["task_id"], "secret", result)
        self.assertIsNone(db.task_attachment(receive["task_id"], "secret", True))
        followup, _ = db.claim_task("dev", "secret")
        self.assertEqual(followup["attachment"]["id"], receive["attachment"]["id"])
        with patch.object(node, "run_serve", return_value=("summary", "session")) as run:
            node.execute(followup, 30, runtime)
            prompt = run.call_args.args[1]
            self.assertIn("PDF attachment integration", prompt)
            self.assertIn("不可信资料", prompt)
        with sqlite3.connect(self.root / "test.db") as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM tasks WHERE reply_message_id IN ('bob','otherchat','afterclear') AND attachment_id IS NULL").fetchone()[0], 3)
        self.assertEqual(len(list(self.root.glob(".agent-control-files/*/document.pdf"))), 1)

    def test_reject_size_signature_unauthorized_and_symlink(self):
        for data in (b"not pdf", b"%PDF-" + b"x" * (10 * 1024 * 1024)):
            with self.assertRaises(ValueError):
                db.store_attachment("app", "alice", "chat", str(self.root), "x.pdf", data)
        with patch.object(hub, "CFG", {"agents": [{"app_id": "app"}]}), patch.object(hub, "reply") as reply:
            self.send("file", "file", {"file_name": "x.pdf", "file_key": "key"})
            self.assertIn("allowed_open_ids", reply.call_args.args[2])
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.root / ".agent-control-files").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink not available")
        with self.assertRaisesRegex(ValueError, "链接"):
            prepare({"task_id": "t_123456789abc", "attachment": {"id": "a" * 32, "sha256": "b" * 64}}, str(self.root), self.config)

    def test_scanned_missing_ocr_encrypted_and_page_limit(self):
        import fitz
        path = self.root / "scan.pdf"
        path.write_bytes(pdf_bytes(scanned=True))
        parsed = extract(str(path), {"ocr": False})
        self.assertIn("OCR 未启用", parsed["warnings"][0])
        parsed = extract(str(path), {"tessdata": str(self.root / "missing")})
        self.assertTrue(parsed["warnings"])
        self.assertIn("未识别", parsed["warnings"][0])
        with fitz.open() as doc:
            for _ in range(51):
                doc.new_page()
            doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="password")
        with self.assertRaisesRegex(ValueError, "加密"):
            extract(str(path), {})
        with fitz.open() as doc:
            for _ in range(51):
                doc.new_page()
            path.write_bytes(doc.tobytes())
        with self.assertRaisesRegex(ValueError, "页数"):
            extract(str(path), {})

    def test_stream_download_limit_and_message_resource_pair(self):
        client = FeishuClient("app", "secret")
        token = Mock()
        token.__enter__ = Mock(return_value=token)
        token.__exit__ = Mock(return_value=False)
        token.json.return_value = {"code": 0, "tenant_access_token": "test"}
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status_code = 200
        response.iter_content.return_value = [b"%PDF-", b"data"]
        with patch("feishu_client.requests.post", return_value=token), patch("feishu_client.requests.get", return_value=response) as get:
            self.assertEqual(client.download_pdf("msg", "key"), b"%PDF-data")
            self.assertTrue(get.call_args.args[0].endswith("/messages/msg/resources/key"))
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
            response.iter_content.return_value = [b"%PDF-", b"x" * (10 * 1024 * 1024)]
            with self.assertRaises(ValueError):
                client.download_pdf("msg", "key")

    def test_real_scanned_ocr_when_language_data_available(self):
        try:
            folder = tessdata({})
        except ValueError:
            self.skipTest("optional eng+chi_sim data not installed")
        path = self.root / "scanned.pdf"
        path.write_bytes(pdf_bytes(scanned=True))
        result = extract(str(path), {"tessdata": folder})
        self.assertFalse(result["warnings"])
        self.assertIn("SCANNED", result["text"].upper())
        self.assertIn("中文", result["text"])
        self.assertEqual(result["ocr_pages"], 1)

    def test_workdir_ttl_isolation_and_worker_timeout(self):
        import subprocess
        ident = db.store_attachment("app", "alice", "chat", str(self.root), "x.pdf", pdf_bytes())
        self.assertIsNone(db.current_attachment("app", "alice", "chat", str(self.root / "other")))
        with patch("db.time.time", return_value=20000000000):
            self.assertIsNone(db.current_attachment("app", "alice", "chat", str(self.root)))
        taskid = db.create_task("app", "dev", "receive", "m", "chat", str(self.root), "alice", ident, True)
        task, _ = db.claim_task("dev", "secret")
        with patch("urllib.request.urlopen", return_value=io.BytesIO(pdf_bytes())), \
             patch("pdf_attachments.subprocess.run", side_effect=subprocess.TimeoutExpired("worker", 120)):
            # Use exact stored bytes because PDF metadata IDs vary between builds.
            data = db.task_attachment(taskid, "secret", True)["data"]
            with patch("urllib.request.urlopen", return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(ValueError, "120 秒"):
                    prepare(task, str(self.root), self.config)


if __name__ == "__main__":
    unittest.main()
