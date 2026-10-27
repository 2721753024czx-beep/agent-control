#!/usr/bin/env bash
# Fresh installation only. Does not stop any existing process.
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$TASK_ROOT"
if [[ -e hub/config.json || -e node/config.json || -e hub/hub.db ]]; then
  echo 'Existing deployment found; use docs/deploy-maintenance.md. No changes made.' >&2
  exit 1
fi
case "$TASK_ROOT" in
  "$HOME/Desktop"/*|"$HOME/Documents"/*|"$HOME/Downloads"/*|/tmp/*|/private/tmp/*)
    echo 'Move the checkout to a persistent location such as ~/agent-control first.' >&2; exit 1;;
esac
TASK_PYTHON="${AGENT_CONTROL_PYTHON:-python3}"
"$TASK_PYTHON" -c 'import sys; assert sys.version_info >= (3,9)'
command -v opencode >/dev/null || { echo 'Install OpenCode and configure a working model first.' >&2; exit 1; }
opencode --version | grep -Eq '(^|[^0-9])1\.19\.0([^0-9]|$)' || { echo 'This release is tested with OpenCode 1.19.0; do not replace V2 automatically.' >&2; exit 1; }
"$TASK_PYTHON" -m venv .venv
.venv/bin/python -m pip install -r hub/requirements.txt -r node/requirements.txt
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python e2e_test.py
.venv/bin/python scripts/configure_local.py "$@"
.venv/bin/python scripts/doctor.py
echo 'Configuration ready. Next: .venv/bin/python scripts/macos/services.py install --confirm-idle'
