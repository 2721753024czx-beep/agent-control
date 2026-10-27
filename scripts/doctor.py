#!/usr/bin/env python3
"""Read-only diagnostics; do not print credentials or make model calls."""
import argparse
import base64
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'hub'))
from access import identity, validate_agents


def check(root, live=False):
    root = Path(root)
    hub = json.loads((root / 'hub/config.json').read_text(encoding='utf-8-sig'))
    node = json.loads((root / 'node/config.json').read_text(encoding='utf-8-sig'))
    agents = [a for a in hub['agents'] if a.get('enabled', 1)]
    validate_agents(agents)
    assert len(node.get('serve_password', '')) >= 32, 'serve password too short'
    devices = {d['device_id']: d for d in hub['devices']}
    assert devices[node['device_id']]['token'] == node['token'], 'device token mismatch'
    assert node['hub_url'] == 'http://127.0.0.1:8000', 'this diagnostic targets local all-in-one mode'
    roots = {identity(p) for p in node.get('allowed_work_dirs', [])}
    for agent in agents:
        assert agent['engine_type'] == 'serve', 'CLI engine forbidden'
        assert Path(agent['work_dir']).is_dir(), 'workspace missing'
        if agent['device_id'] == node['device_id']:
            for path in [agent['work_dir'], *(agent.get('workspaces') or {}).values()]:
                assert identity(path) in roots, 'workspace missing from Node allowlist'
                if 'agent_work_dirs' in node:
                    assert identity(path) in {identity(p) for p in node['agent_work_dirs'].get(agent['app_id'], [])}, 'per-agent allowlist mismatch'
            assert agent['exec_path'] == 'http://127.0.0.1:' + str(node.get('serve_port', 4096)), 'serve endpoint mismatch'
    if live:
        with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5) as response:
            status = json.load(response)
        assert status.get('ok') and status.get('agents') == len(agents), 'Hub health mismatch'
        auth = base64.b64encode(('opencode:' + node['serve_password']).encode()).decode()
        endpoint = 'http://127.0.0.1:' + str(node.get('serve_port', 4096))
        def get(path):
            req = urllib.request.Request(endpoint + path, headers={'Authorization': 'Basic ' + auth})
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        assert get('/global/health').get('healthy'), 'OpenCode unhealthy'
        permissions = get('/config').get('permission', {})
        assert all(permissions.get(k) == 'deny' for k in ['*', 'bash', 'shell', 'edit', 'write', 'apply_patch', 'task', 'external_directory']), 'unsafe OpenCode permissions'
        assert permissions.get('read') == 'ask', 'read approval missing'
    return len(agents)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    try:
        count = check(ROOT, args.live)
    except Exception as exc:
        # Avoid printing arbitrary library messages that may contain credentials.
        print('DOCTOR_FAIL type=' + type(exc).__name__ + '; inspect configuration locally, do not paste secrets')
        raise SystemExit(1)
    print('DOCTOR_OK agents=%d live=%s; phone checks and websocket readiness still required' % (count, args.live))
