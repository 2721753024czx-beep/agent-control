#!/usr/bin/env python3
"""Isolated compatibility probes, never a remote task execution adapter.

Default: versions and Codex protocol handshake only. --live additionally makes
one small model request per installed engine using existing local credentials.
No current Hub/Node configuration is loaded or changed.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import tempfile
import time

MARKER = 'AGENT_CONTROL_NATIVE_PROBE_OK'


def command(engine, executable, workspace, claude_user_settings=False):
    if engine == 'claude':
        return [executable, '-p', 'Reply exactly ' + MARKER,
                '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--setting-sources', 'user' if claude_user_settings else '',
                '--settings', '{"disableAllHooks":true}',
                '--disable-slash-commands', '--no-chrome', '--no-session-persistence',
                '--system-prompt', 'Answer the supplied text. No tools are available.',
                '--output-format', 'json', '--permission-mode', 'dontAsk']
    return [executable, 'exec', '--ignore-user-config', '--ignore-rules',
            '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only',
            '--disable', 'shell_tool',
            '-c', 'features.multi_agent=false', '-c', 'web_search="disabled"',
            '-c', 'project_doc_max_bytes=0', '-c', 'approval_policy="never"',
            '--json', '-C', str(workspace), 'Reply exactly ' + MARKER + '. Do not use tools.']


def stop(process):
    if process.poll() is not None:
        return
    if os.name == 'posix':
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()


def text_result(engine, code, stdout, stderr):
    # Return classifications, never arbitrary CLI logs, account data or secrets.
    messages = []
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            messages.append(item)
    tool_events = 0
    final = ''
    error = False
    for item in messages:
        if engine == 'claude':
            if item.get('type') == 'result':
                final = item.get('result', '')
                error = bool(item.get('is_error'))
            if item.get('permission_denials'):
                tool_events += len(item['permission_denials'])
        else:
            event = item.get('item', {})
            if event.get('type') == 'agent_message':
                final = event.get('text', '')
            if event.get('type') in {'command_execution', 'file_change', 'mcp_tool_call', 'web_search'}:
                tool_events += 1
            error = error or item.get('type') in {'error', 'turn.failed'}
    text = (stdout + '\n' + stderr).lower()
    return dict(exit_code=code, marker_received=final.strip() == MARKER,
                passed=code == 0 and final.strip() == MARKER and not error and tool_events == 0,
                error_event=error, tool_events=tool_events,
                auth_error=any(s in text for s in ['not logged in', 'authentication_error', '401 unauthorized', 'invalid api key']),
                permission_error=any(s in text for s in ['operation not permitted', 'permission denied']),
                network_error=any(s in text for s in ['enotfound', 'econnrefused', 'connection error', 'connection refused', 'error sending request']))


def handshake(executable, workspace):
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen([executable, 'app-server', '--listen', 'stdio://'],
                                cwd=workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=err, start_new_session=os.name == 'posix')
        try:
            request = dict(id=1, method='initialize', params={
                'clientInfo': {'name': 'agent_control_probe', 'version': '0.1'},
                'capabilities': {'experimentalApi': True}})
            proc.stdin.write((json.dumps(request) + '\n').encode()); proc.stdin.flush()
            # stdio pipes are selectable on POSIX; Windows default is versions only.
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if selector.select(1):
                        raw = proc.stdout.readline()
                        if not raw:
                            break
                        response = json.loads(raw)
                        if response.get('id') == 1:
                            return 'ok' if 'result' in response else 'protocol_error'
            return 'no_response'
        finally:
            stop(proc)


def probe(engine, live, claude_user_settings=False):
    executable = shutil.which(engine)
    if not executable:
        return {'installed': False}
    if Path(executable).suffix.lower() in {'.cmd', '.bat', '.ps1'}:
        return {'installed': True, 'status': 'shim_not_supported_by_probe'}
    version = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=15)
    result = {'installed': True, 'version': version.stdout.strip()[:100]}
    with tempfile.TemporaryDirectory(prefix='agent-control-native-probe-') as workspace:
        if engine == 'codex':
            result['handshake'] = handshake(executable, workspace) if os.name == 'posix' else 'not_tested_on_windows'
        if live:
            proc = subprocess.Popen(command(engine, executable, workspace, claude_user_settings), cwd=workspace,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=os.name == 'posix')
            try:
                out, err = proc.communicate(timeout=90)
                result['smoke'] = text_result(engine, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                stop(proc)
                result['smoke'] = {'timeout': True}
            finally:
                stop(proc)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=['claude', 'codex', 'both'], default='both')
    parser.add_argument('--live', action='store_true', help='Make a model call; may consume account quota')
    parser.add_argument('--claude-user-settings', action='store_true',
                        help='Load Claude user settings for existing auth/provider configuration; hooks and tools remain disabled')
    args = parser.parse_args()
    engines = ['claude', 'codex'] if args.engine == 'both' else [args.engine]
    for engine in engines:
        try:
            result = probe(engine, args.live, args.claude_user_settings)
        except Exception as exc:
            result = {'probe_error': type(exc).__name__}
        print(json.dumps({'engine': engine, **result}, ensure_ascii=False), flush=True)
