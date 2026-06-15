#!/usr/bin/env python3
r"""Keep powershell invocations consistent so the permission allowlist can match.

PreToolUse hook. Convention (also stated in ~/.claude/CLAUDE.md):
  * Default to the PowerShell tool for commands that have a PowerShell home
    (git, uv, python, ruff, cd, project .ps1 scripts). Reserve the Bash tool
    for genuinely POSIX-only tools (shellcheck, `bash -n`, ...).
  * Run .ps1 scripts directly and relatively: `.\Tests.ps1 [args]` -- never
    wrapped in `powershell -Command` / `pwsh -File`, never double-backslashed.

Blocking contract: exit code 2 blocks the tool call and feeds this script's
stderr back to Claude so it retries in the canonical form.
"""

import json
import re
import sys

# Bash-tool commands whose first token belongs on the PowerShell tool instead.
_BASH_REROUTE = {"git", "uv", "python", "python3", "ruff", "cd", "pwsh", "powershell"}

# A .ps1 invoked through a wrapper rather than run directly.
_WRAPPER_RE = re.compile(r"(?i)\b(?:powershell|pwsh)\b.*\.ps1")
# Over-escaped path: two consecutive backslashes.
_DOUBLE_BS = "\\\\"


def _first_token(command: str) -> str:
    parts = command.strip().split()
    return parts[0].lower() if parts else ""


def main() -> int:
    """Block (exit 2) a command that is on the wrong tool or in a non-canonical form."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # never block on a parsing problem

    tool = data.get("tool_name", "")
    command = data.get("tool_input", {}).get("command", "")
    if not isinstance(command, str) or not command.strip():
        return 0

    has_ps1 = ".ps1" in command.lower()

    if tool == "Bash":
        token = _first_token(command)
        if token in _BASH_REROUTE or has_ps1:
            print(
                f"Blocked: run `{token or 'this command'}` through the PowerShell "
                "tool, not the Bash tool. Reserve the Bash tool for POSIX-only "
                "tools (shellcheck, `bash -n`).",
                file=sys.stderr,
            )
            return 2
        return 0

    if tool == "PowerShell" and has_ps1:
        problems = []
        if _WRAPPER_RE.search(command):
            problems.append(
                r"run the script directly (e.g. `.\Tests.ps1 -Foo`), not via "
                r"`powershell -Command` / `pwsh -File`"
            )
        if _DOUBLE_BS in command:
            problems.append(r"use single backslashes (`.\Tests\Test-Foo.ps1`), not doubled")
        if problems:
            print(
                "Blocked (non-canonical .ps1 form): " + "; ".join(problems) + ".",
                file=sys.stderr,
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
