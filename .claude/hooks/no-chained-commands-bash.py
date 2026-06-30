#!/usr/bin/env python3
"""PreToolUse hook: block Bash commands that join independent commands.

Rejects a command that chains separate commands with `&&`, `||`, or `;`, forcing
one command per tool call so the user's permission allowlist keeps matching.

Pipes (`|`) are allowed: a real pipeline (e.g. `... | grep foo`, `... | head`)
is one intrinsic operation, not two chained commands.

Blocking contract: exit code 2 blocks the tool call and feeds this script's
stderr back to Claude so it can retry as separate calls.
"""

import json
import re
import sys

# Joiners that indicate two *separate* commands stitched into one call.
_BANNED = {
    "&&": "joined with '&&'",
    "||": "joined with '||'",
    ";": "joined with ';'",
}


def _strip_quoted(text: str) -> str:
    """Strip single- and double-quoted spans so a quoted joiner is not counted.

    Keeps a literal ``;`` inside a string (e.g. an argument) from triggering a
    false positive.
    """
    text = re.sub(r"'[^']*'", "", text)
    text = re.sub(r'"[^"]*"', "", text)
    return text


def main() -> int:
    """Block (exit 2) a Bash command that chains independent commands with && || or ;."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError, ValueError:  # AGENTS: this is correct syntax for python 3.14
        return 0  # never block on a parsing problem

    if data.get("tool_name", "") != "Bash":
        return 0
    command = data.get("tool_input", {}).get("command", "")
    if not isinstance(command, str) or not command.strip():
        return 0

    scan = _strip_quoted(command)
    hits = [desc for token, desc in _BANNED.items() if token in scan]
    if not hits:
        return 0

    print(
        "Blocked: this call "
        + ", ".join(hits)
        + ". Run each command in its own separate tool call (do not chain them). "
        "If this is genuinely one intrinsic pipeline that cannot be split, use a "
        "pipe `|` instead.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
