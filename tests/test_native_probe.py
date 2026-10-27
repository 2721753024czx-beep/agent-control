import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from probe_native_engines import MARKER, command, text_result


class NativeProbeTests(unittest.TestCase):
    def test_claude_tools_mcp_hooks_and_project_discovery_disabled(self):
        args = command('claude', '/synthetic/claude', '/synthetic/work')
        self.assertEqual(args[args.index('--tools') + 1], '')
        self.assertEqual(json.loads(args[args.index('--mcp-config') + 1]), {'mcpServers': {}})
        self.assertTrue(json.loads(args[args.index('--settings') + 1])['disableAllHooks'])
        self.assertEqual(args[args.index('--setting-sources') + 1], '')
        self.assertNotIn('--dangerously-skip-permissions', args)

    def test_codex_is_read_only_without_user_config_or_rules(self):
        args = command('codex', '/synthetic/codex', '/synthetic/work')
        self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
        self.assertIn('--ignore-user-config', args)
        self.assertIn('--ignore-rules', args)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)

    def test_claude_result_requires_exact_final_marker(self):
        output = json.dumps({'type': 'result', 'result': MARKER, 'is_error': False})
        self.assertTrue(text_result('claude', 0, output, '')['marker_received'])
        output = json.dumps({'type': 'result', 'result': 'Not ' + MARKER, 'is_error': False})
        self.assertFalse(text_result('claude', 0, output, '')['marker_received'])

    def test_codex_reasoning_is_not_final_answer(self):
        output = json.dumps({'type': 'item.completed', 'item': {'type': 'reasoning', 'text': MARKER}})
        self.assertFalse(text_result('codex', 0, output, '')['marker_received'])
        output += '\n' + json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': MARKER}})
        self.assertTrue(text_result('codex', 0, output, '')['marker_received'])

    def test_errors_and_tool_events_are_not_hidden_by_success_marker(self):
        output = '\n'.join(json.dumps(i) for i in [
            {'type': 'item.completed', 'item': {'type': 'command_execution'}},
            {'type': 'turn.failed'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': MARKER}}])
        result = text_result('codex', 1, output, 'private error detail')
        self.assertEqual(result['tool_events'], 1)
        self.assertTrue(result['error_event'])
        self.assertNotIn('private error detail', json.dumps(result))
