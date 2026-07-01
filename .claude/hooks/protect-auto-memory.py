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
# scripts/template_setup/protect_auto_memory.py (also run by the guided
# setup_new_project.py) asks whether to enable it. Accept and it wires the hook
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
#                 "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/protect-auto-memory.py\""
#               }
#             ]
#           }
#         ]
#       }
#     }
#
# Claude Code expands $CLAUDE_PROJECT_DIR to this project's root, so the command
# resolves regardless of the current working directory. (Declining the setup
# step deletes this file instead.)
#
# To make it GLOBAL instead (run for every project, not just this repo):
#
#   1. Copy this file into your user hooks directory, e.g.
#        ~/.claude/hooks/protect-auto-memory.py
#      (Windows: C:/Users/<you>/.claude/hooks/protect-auto-memory.py)
#   2. Add the same PreToolUse entry to your USER settings file
#        ~/.claude/settings.json
#      but point "command" at that absolute path instead of $CLAUDE_PROJECT_DIR:
#
#        "command": "python \"C:/Users/<you>/.claude/hooks/protect-auto-memory.py\""
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
