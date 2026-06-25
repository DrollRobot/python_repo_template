#!/usr/bin/env python3
"""PreToolUse hook: block shell commands that join independent commands.

Wired to the Bash and PowerShell tools, this inspects the command about to run
and rejects it when it chains separate commands with `&&`, `||`, or `;`. The
goal is to force one command per tool call so the user's permission allowlist
keeps matching.

Pipes (`|`) are deliberately allowed: a real pipeline (e.g. a PowerShell object
pipeline, or `... | head`) is one intrinsic operation, not two chained commands.

Blocking contract: exit code 2 tells Claude Code to block the tool call and
feed this script's stderr back to the model so it can retry as separate calls.
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

    Keeps a literal ``;`` inside a string (e.g. a commit message) from
    triggering a false positive.
    """
    text = re.sub(r"'[^']*'", "", text)
    text = re.sub(r'"[^"]*"', "", text)
    return text


def main() -> int:
    """Block (exit 2) a command that chains independent commands with && || or ;."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError, ValueError:
        return 0  # never block on a parsing problem

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
        "pipe `|` instead, or a dedicated tool.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
