#!/usr/bin/env python3
"""Scoped launchd lifecycle. Never kill processes by name or remove user data."""
import argparse
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('opencode', 'hub', 'node')


def plist(root, name, python):
    entry = {'opencode': 'scripts/run_safe_opencode.py', 'hub': 'hub/hub.py', 'node': 'node/node.py'}[name]
    return {'Label': 'com.agentcontrol.' + name, 'ProgramArguments': [str(python), str(root / entry)],
            'WorkingDirectory': str(root), 'RunAtLoad': True, 'KeepAlive': True,
            'StandardOutPath': str(root / ('logs/' + name + '.log')),
            'StandardErrorPath': str(root / ('logs/' + name + '.err.log')),
            'EnvironmentVariables': {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                     'AGENT_CONTROL_HOST': '127.0.0.1'}}


def require_idle(root):
    database = root / 'hub/hub.db'
    if database.exists():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
            if conn.execute("SELECT count(*) FROM tasks WHERE status IN ('pending','running') OR execution_active=1").fetchone()[0]:
                raise SystemExit('Device busy. Do not interrupt tasks; wait for completion.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'status', 'stop', 'uninstall'])
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--confirm-idle', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'darwin' and not args.dry_run:
        parser.error('macOS only')
    if args.dry_run:
        for name in NAMES:
            plistlib.loads(plistlib.dumps(plist(ROOT, name, ROOT / '.venv/bin/python')))
        print('DRY_RUN_OK: 3 launchd jobs; no files or services changed')
        return
    domain = 'gui/' + str(os.getuid())
    folder = Path.home() / 'Library/LaunchAgents'
    paths = [folder / ('com.agentcontrol.' + n + '.plist') for n in NAMES]
    if args.action == 'status':
        for name in NAMES:
            subprocess.run(['launchctl', 'print', domain + '/com.agentcontrol.' + name], check=False)
        return
    if not args.confirm_idle:
        parser.error('Pause mobile submissions, confirm no active work, then pass --confirm-idle')
    require_idle(ROOT)
    if args.action == 'install':
        if any(p.exists() for p in paths):
            raise SystemExit('Existing launchd registration found; do not overwrite. See maintenance guide.')
        python = ROOT / '.venv/bin/python'
        if not python.exists():
            raise SystemExit('Run scripts/macos/install.sh first')
        subprocess.run([str(python), str(ROOT / 'scripts/doctor.py')], check=True)
        folder.mkdir(parents=True, exist_ok=True)
        (ROOT / 'logs').mkdir(exist_ok=True)
        for name, path in zip(NAMES, paths):
            with path.open('xb') as stream:
                plistlib.dump(plist(ROOT, name, python), stream)
            subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True)
    else:
        # Verify ownership of all target definitions before stopping anything.
        for path in paths:
            if path.exists():
                job = plistlib.loads(path.read_bytes())
                if job.get('WorkingDirectory') != str(ROOT):
                    raise SystemExit('Registration belongs to another checkout; refusing to stop it')
        for name, path in reversed(list(zip(NAMES, paths))):
            if path.exists():
                subprocess.run(['launchctl', 'bootout', domain + '/com.agentcontrol.' + name], check=False)
                if args.action == 'uninstall':
                    path.unlink()
        print('Service definitions processed; configuration, databases, attachments and memory preserved')


if __name__ == '__main__':
    main()
