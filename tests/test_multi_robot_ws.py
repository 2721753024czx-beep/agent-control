import asyncio
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hub'))
from feishu_client import FeishuClient


class MultiRobotWebsocketTests(unittest.TestCase):
    def test_two_robots_share_runner_and_disconnect(self):
        class FakeSocket:
            def __init__(self, **kwargs):
                self._auto_reconnect = False
                self.connected_thread = None
                self.disconnected = threading.Event()

            async def _connect(self):
                self.connected_thread = threading.get_ident()

            async def _ping_loop(self):
                await asyncio.sleep(3600)

            async def _disconnect(self):
                self.disconnected.set()

        with patch('feishu_client.lark.ws.Client', FakeSocket):
            clients = [FeishuClient('test-first', 'not-a-secret'),
                       FeishuClient('test-second', 'not-a-secret')]
            for client in clients:
                client.start_ws(lambda data: None)
                client._future.result(timeout=3)
            self.assertEqual(clients[0]._ws.connected_thread, clients[1]._ws.connected_thread)
            self.assertNotEqual(clients[0]._ws.connected_thread, threading.get_ident())
            for client in clients:
                client.stop()
                self.assertTrue(client._ws.disconnected.wait(3))
