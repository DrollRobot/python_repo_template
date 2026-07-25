#!/usr/bin/env python3
"""PreToolUse hook: block secret-scanner suppression comments in written content.

Steering, not enforcement. This nudges an agent away from silencing
detect-secrets inline; the pre-commit hook and CI are what actually gate the
repo. That balance decides every judgement call below -- see "Fails open".

Scans only the content a direct write tool is about to commit to a file: a
Write body, an Edit's ``new_string``, a MultiEdit's ``edits``, or a notebook's
``new_source``. Two deliberate exclusions:

* ``old_string`` is never scanned, so removing an existing suppression is
  always allowed.
* Shell commands (Bash/PowerShell) are never scanned. A heredoc could smuggle
  the same comment past this hook, which is fine: agents write files with the
  write tools, and scanning shell text would block ordinary work like grepping
  the repo for existing pragmas.

Blocking contract: exit code 2 blocks the tool call and feeds this script's
stderr back to Claude so it records the finding in the baseline instead.

Fails open: an unreadable payload allows the call. A hook that blocks every
write because the payload shape changed would be a much bigger problem than a
pragma that reaches the pre-commit hook, which is the real gate.
"""

import json
import re
import sys
from collections.abc import Iterator
from typing import Any

# Bump on every change: compare_to_template.py tracks this file by version and
# offers to copy a newer template copy into a project.
__version__ = "1.0.0"

# Each entry is (compiled pattern, human label used in the block message).
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"pragma:\s*(?:allow|white)list[\s-]+(?:nextline[\s-]+)?secret", re.I),
        "detect-secrets allowlist pragma",
    )
]

# Payload keys holding content about to be written. `old_string` is deliberately
# absent (deleting a pragma must not be blocked), and so is `command`: shell
# text is out of scope for this hook.
_CONTENT_KEYS = ("content", "new_string", "new_source")

_GUIDANCE = (
    "Inline suppressions are prohibited in this repo — they silence the line "
    "permanently, survive edits that change the value, and leave no audit trail.\n"
    "Record the finding in the baseline and ask the user to audit it instead:\n"
    "uvx detect-secrets scan --baseline .secrets.baseline\n"
    "AGENTS: DO NOT AUDIT. AUDITING IS FOR USERS ONLY."
)


def _candidates(tool_input: dict[str, Any]) -> Iterator[str]:
    """Yield each string in tool_input that is content being written.

    Args:
        tool_input: The tool call's ``tool_input`` object.

    Yields:
        Every candidate string to scan: the top-level content keys plus each
        ``new_string`` inside a MultiEdit ``edits`` list. ``old_string`` and
        ``command`` are never yielded (see the module docstring).
    """
    for key in _CONTENT_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str):
            yield value

    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            new_string = edit.get("new_string")
            if isinstance(new_string, str):
                yield new_string


def main() -> int:
    """Block (exit 2) a write whose content adds a secret-scanner suppression.

    Returns:
        2 when a suppression pattern matches in written content; 0 otherwise,
        including for any payload this hook cannot read (see "Fails open" in
        the module docstring).
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):  # fmt: skip
        return 0  # Unreadable payload: never block on a parsing problem.

    if not isinstance(payload, dict):
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0  # No input object: nothing is being written.

    for text in _candidates(tool_input):
        for pattern, label in _PATTERNS:
            match = pattern.search(text)
            if match:
                print(f"Blocked: {label} ({match.group(0)!r}).\n{_GUIDANCE}", file=sys.stderr)
                return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
