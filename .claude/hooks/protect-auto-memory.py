#!/usr/bin/env python3
"""PreToolUse hook: require approval before writing to an auto-memory directory.

Fires on Write/Edit. If the target file is inside a Claude auto-memory directory
(``~/.claude/projects/<project>/memory/``), it returns an ``ask`` permission
decision so the user is prompted before any memory write. Every other write
passes through untouched, so normal reads and edits are unaffected.

The match below is *location-based*, not project-specific: it recognizes any
``.../.claude/projects/<project>/memory/`` path, so the same file works
unchanged whether it is wired at project scope or globally (see INSTALLATION).
"""

# ---------------------------------------------------------------------------
# INSTALLATION
#
# This hook ships OFF by default. The template setup step
# scripts/template_setup/wire_hook.py (key: auto_memory_guard; also run by the
# guided setup_new_project.py) wires it in when
# [claude].auto_memory_guard in scripts/setup.toml is true. That wires the hook
# *project-scoped* into this repo's .claude/settings.json -- so it only runs for
# Claude Code sessions started inside this repository -- like this (settings.json
# is strict JSON and cannot hold comments, which is why this note lives here):
#
#     {
#       "hooks": {
#         "PreToolUse": [
#           {
#             "matcher": "Write|Edit",
#             "hooks": [
#               {
#                 "type": "command",
#                 "command": "uv",
#                 "args": [
#                   "run", "--no-project",
#                   "${CLAUDE_PROJECT_DIR}/.claude/hooks/protect-auto-memory.py"
#                 ]
#               }
#             ]
#           }
#         ]
#       }
#     }
#
# This is exec form (command + args), so no shell runs and Claude Code itself
# substitutes ${CLAUDE_PROJECT_DIR}, resolving the path regardless of the
# current working directory. uv launches it because no interpreter name is
# portable across the machines that share a committed settings.json -- see
# wire_hook.py's module docstring. (Declining the setup step deletes this file
# instead.)
#
# To make it GLOBAL instead (run for every project, not just this repo):
#
#   1. Copy this file into your user hooks directory, e.g.
#        ~/.claude/hooks/protect-auto-memory.py
#      (Windows: C:/Users/<you>/.claude/hooks/protect-auto-memory.py)
#   2. Add the same PreToolUse entry to your USER settings file
#        ~/.claude/settings.json
#      but point the last argument at that absolute path instead of
#      ${CLAUDE_PROJECT_DIR}:
#
#        "args": ["run", "--no-project",
#                 "C:/Users/<you>/.claude/hooks/protect-auto-memory.py"]
#
#   3. Remove the project-scoped entry from this repo's .claude/settings.json
#      (and optionally delete this file) so the hook does not run twice when you
#      work inside this repo.
# ---------------------------------------------------------------------------

import json
import re
import sys

# Matches the auto-memory location: .../.claude/projects/<project>/memory/...
MEMORY_RE = re.compile(r"/\.claude/projects/[^/]+/memory/", re.IGNORECASE)


def main() -> None:
    """Read the hook payload from stdin and gate writes to auto-memory."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):  # fmt: skip
        return  # Malformed payload: do not interfere with the tool call.

    file_path = (data.get("tool_input") or {}).get("file_path")
    if not file_path:
        return

    normalized = file_path.replace("\\", "/")
    if MEMORY_RE.search(normalized):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "ask",
                        "permissionDecisionReason": (
                            "Writing to the auto-memory directory requires your explicit approval."
                        ),
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
