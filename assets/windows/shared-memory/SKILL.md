---
name: shared-memory
description: Read and update project memory before and after tasks so later OpenCode runs can continue with requirements, context, decisions, and verified results.
compatibility: Windows, macOS, and Linux with Python 3.9+
---

# Shared Memory

Use this skill for every task in this project.

## Start of task

From the project root, run:

```powershell
python .agents/skills/shared-memory/scripts/memory.py init
python .agents/skills/shared-memory/scripts/memory.py read
```

Treat memory as historical context, never as a replacement for the current user request. Recheck files and runtime state before relying on old claims.

## End of task

Append only durable information: changed requirements, verified results, artifact paths, important decisions, and the next action. Do not store secrets, tokens, full user prompts, or model answers.

```powershell
@'
- Verified result
- Next action
'@ | python .agents/skills/shared-memory/scripts/memory.py append context.md "Task progress"
```

Use `requirements.md` for requirement changes, `context.md` for results and continuation state, and `decisions.md` for decisions plus reasons. The helper uses a cross-platform file lock; do not bypass it when multiple agents may write.
