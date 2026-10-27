#!/usr/bin/env python3
"""Bounded identity capture. Never grants access or executes Agent tasks."""
import argparse
import getpass
import secrets
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hub'))
from feishu_client import FeishuClient
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-id', required=True)
    args = parser.parse_args()
    challenge = 'identity-' + secrets.token_hex(8)
    done = threading.Event()
    client = FeishuClient(args.app_id, getpass.getpass('App Secret (hidden): '))
    def receive(message, sender):
        if (message.get('chat_type') == 'p2p' and sender.get('sender_type') == 'user'
                and json.loads(message.get('content') or '{}').get('text', '').strip() == challenge):
            print('Candidate Open ID: ' + sender['open_id'], flush=True)
            print('Confirm this is the intended account; no permissions have been granted.', flush=True)
            done.set()
    print('In the intended user account, privately send this exact text to the new bot:', challenge, flush=True)
    client.start_ws(receive)
    try:
        if not done.wait(300):
            raise SystemExit('No matching identity received in 5 minutes; check events and publication')
    finally:
        client.stop()


if __name__ == '__main__':
    main()
