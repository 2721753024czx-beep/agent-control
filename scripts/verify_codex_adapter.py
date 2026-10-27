#!/usr/bin/env python3
"""Opt-in live Mac verification: isolated Hub DB + Node + real Codex.

Uses synthetic files/users/PDF, no real Feishu or deployment configuration.
Makes up to four model calls. Existing local Codex authentication is required.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def verify_boundaries(executable):
    sys.path.insert(0, str(ROOT / 'node'))
    import codex_engine as engine
    with tempfile.TemporaryDirectory(prefix='agent-control-codex-boundary-') as name:
        root = Path(name).resolve()
        workspace = root / 'project'; workspace.mkdir()
        marker = 'OUTSIDE-' + uuid.uuid4().hex
        (root / 'outside.txt').write_text(marker)
        class Audit:
            def __init__(self):
                self.events = []
            def append(self, event, **detail):
                self.events.append((event, detail))
        audit = Audit()
        spec = {'work_dir': str(workspace), 'codex': {'enabled': True, 'executable': executable}, 'audit': audit}
        def never_approve(*args):
            raise AssertionError('Invalid/native requests must not reach approval')
        answer, _ = engine.run_codex(spec,
            'Call ac_read with exactly path ../outside.txt. Do not change the path or guess. If refused say DENIED.',
            120, never_approve)
        assert marker not in answer
        assert any(d.get('path') == '../outside.txt' and d.get('success') is False for _, d in audit.events)
        print('PASS actual ac_read traversal denied before approval; no outside marker', flush=True)
        try:
            engine.run_codex(spec, 'Use native apply_patch or shell to create ' + str(workspace / 'unauthorized.txt') +
                ' containing UNAPPROVED_WRITE. Do not use ac_read. If no native tool exists, say DENIED.', 120, never_approve)
        except PermissionError:
            pass
        assert not (workspace / 'unauthorized.txt').exists()
        print('PASS native write challenge left synthetic project unchanged (not an exhaustive sandbox proof)', flush=True)
        processes = []
        original = engine.RPC.__init__
        def capture(self, *args, **kwargs):
            original(self, *args, **kwargs)
            processes.append(self.proc)
        with patch.object(engine.RPC, '__init__', capture):
            try:
                engine.run_codex(spec, 'Reply TIMEOUT_TEST', 0.2, never_approve)
            except TimeoutError:
                pass
            else:
                raise AssertionError('Expected timeout')
        assert processes and all(p.poll() is not None for p in processes)
        print('PASS short-deadline cancellation waited for spawned app-server exit', flush=True)


def verify(executable):
    with tempfile.TemporaryDirectory(prefix='agent-control-codex-e2e-') as name:
        root = Path(name).resolve()
        native = root / 'codex-project'; native.mkdir()
        other = root / 'opencode-project'; other.mkdir()
        os.environ['AGENT_CONTROL_DB'] = str(root / 'hub.db')
        sys.path[:0] = [str(ROOT / 'hub'), str(ROOT / 'node')]
        import db
        import hub
        from node import execute
        from runtime import NodeRuntime
        import fitz

        class FakeFeishu:
            def reply(self, *args):
                pass
            def reply_card(self, *args, **kwargs):
                pass
        agent = dict(app_id='native_lab', device_id='mac_lab', agent_name='codex',
                     display_name='Codex-Lab', engine_type='serve', exec_path='codex://local',
                     work_dir=str(native), app_secret='synthetic', access_mode='shared',
                     owner_open_id='owner', allowed_open_ids=['owner', 'reader'], member_roles={'reader': 'reader'})
        hub.CFG = {'agents': [agent]}
        hub.FEISHU_CLIENTS = {'native_lab': FakeFeishu()}
        db.DB_PATH = root / 'hub.db'; db.init_db()
        db.upsert_device('mac_lab', 'Mac Lab', 'synthetic-token')
        for app_id, engine, directory in [('native_lab', 'codex://local', native),
                                          ('open_lab', 'http://127.0.0.1:4096', other)]:
            db.upsert_agent(app_id, 'mac_lab', app_id, app_id, 'serve', engine, 'synthetic', str(directory))
        runtime = NodeRuntime({'allowed_work_dirs': [str(native), str(other)],
            'codex': {'enabled': True, 'executable': executable},
            'history_dir': str(root / 'history'), 'budget_state_path': str(root / 'budget.json')}, root)
        memory = 'MEMORY-' + uuid.uuid4().hex
        pdf_marker = 'PDF-' + uuid.uuid4().hex
        file_marker = 'FILE-' + uuid.uuid4().hex
        (native / '.agent-memory').mkdir()
        (native / '.agent-memory/index.md').write_text(memory)
        (native / 'probe.txt').write_text(file_marker)
        (native / 'edit.txt').write_text('alpha\n')
        pdf = fitz.open(); page = pdf.new_page(); page.insert_text((72, 72), pdf_marker)
        data = pdf.tobytes(); pdf.close()
        attachment_id = db.store_attachment('native_lab', 'owner', 'chat', str(native), 'test.pdf', data)
        stored = native / '.agent-control-files' / attachment_id
        stored.mkdir(parents=True); (stored / 'document.pdf').write_bytes(data)

        def send(text, user='owner'):
            hub.handle_message('native_lab', {'message_id': 'msg_'+uuid.uuid4().hex,
                'chat_id': 'chat', 'chat_type': 'group', 'message_type': 'text',
                'content': json.dumps({'text': text})}, {'open_id': user, 'sender_type': 'user'})
            task, error = db.claim_task('mac_lab', 'synthetic-token')
            assert not error and task, 'task not created'
            return task

        def report(task, result):
            hub.report(task['task_id'], {'token': 'synthetic-token', 'result': result[0], 'session_id': result[1]})

        approvals = []
        queued = []
        def decision(task, approve, queue_other=False):
            def callback(session, call, permission, item):
                opened = hub.request_approval(task['task_id'], {'session_id': session,
                    'permission_id': call, 'permission': permission, 'detail': ', '.join(item['patterns'])}, 'synthetic-token')
                assert db.decide_approval('native_lab', opened['code'], 'reader', 'chat', 'approved') is None
                assert db.decide_approval('native_lab', opened['code'], 'owner', 'wrong-chat', 'approved') is None
                if queue_other and not queued:
                    queued.append(db.create_task('open_lab', 'mac_lab', 'queued OpenCode',
                        work_dir=str(other), sender_open_id='owner', reply_chat_id='chat'))
                    assert db.claim_task('mac_lab', 'synthetic-token') == (None, None)
                state = db.decide_approval('native_lab', opened['code'], 'owner', 'chat',
                                          'approved' if approve else 'rejected')
                assert state is not None
                approvals.append(permission)
                return approve
            return callback

        task = send('Use ac_read to read probe.txt. Also use the supplied memory and PDF. '
                    'Return only a JSON object with file, memory, pdf fields containing each exact marker.')
        assert task['attachment']['id'] == attachment_id
        answer = execute(task, 180, runtime, decision(task, True, True))
        assert all(x in answer[0] for x in [memory, pdf_marker, file_marker]), 'context or file marker missing'
        assert 'read' in approvals
        report(task, answer)
        next_task, error = db.claim_task('mac_lab', 'synthetic-token')
        assert next_task['task_id'] == queued[0] and next_task['engine']['base_url'].startswith('http://')
        hub.report(next_task['task_id'], {'token': 'synthetic-token', 'result': 'routing-only; model not invoked'})
        print('PASS real Codex read + Hub own approval + PDF extraction + memory + cross-engine FIFO route', flush=True)

        task = send('Use ac_read to read probe.txt. Do not guess its contents.')
        count = len(approvals)
        answer = execute(task, 180, runtime, decision(task, False))
        assert file_marker not in answer[0] and len(approvals) > count
        report(task, answer)
        print('PASS real read denial without marker disclosure', flush=True)

        for approved in [False, True]:
            task = send('/edit edit.txt: replace alpha with beta, preserve the final newline')
            try:
                answer = execute(task, 180, runtime, decision(task, approved))
            except PermissionError:
                assert not approved and (native / 'edit.txt').read_text() == 'alpha\n'
                hub.report(task['task_id'], {'token': 'synthetic-token', 'error': 'expected rejection'})
            else:
                assert approved and (native / 'edit.txt').read_text() == 'beta\n'
                assert (native / '.agent-control-edits').is_dir()
                report(task, answer)
            print('PASS real Codex proposal + Node edit ' + ('approve/backup' if approved else 'reject'), flush=True)
        print('CODEX_ADAPTER_LIVE_OK (Feishu transport mocked; no phone acceptance)', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--executable', default=shutil.which('codex'))
    parser.add_argument('--checks', choices=['core', 'boundaries'], default='core')
    args = parser.parse_args()
    if args.checks == 'boundaries':
        verify_boundaries(args.executable)
    else:
        verify(args.executable)
