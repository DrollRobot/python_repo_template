#!/usr/bin/env python3
r"""Keep bash command invocations consistent so the permission allowlist matches.

PreToolUse hook. Enforces a single invocation form for the Bash tool:

  * Run scripts directly and relatively: `./build.sh [args]` -- not
    `bash build.sh`, `sh build.sh`, or `bash ./build.sh`.
    (`bash -n file.sh` for a syntax check and `bash -c '...'` stay allowed.)
  * Do not prepend `cd`; the working directory is already set.

Each variation (script wrapper, cd-prefix) is a distinct string to the
permission allowlist, so inconsistency forces re-approving the same logical
command repeatedly.

Blocking contract: exit code 2 blocks the call and feeds stderr back to Claude
so it retries in the canonical form.
"""

import json
import re
import sys

# `bash foo.sh` / `sh ./foo.sh`: a plain script wrapper, not `bash -n`/`bash -c`
# (the negative lookahead skips anything starting with a flag).
_WRAPPER_RE = re.compile(r"^(?:bash|sh)\s+(?!-)\S*\.sh(?:\s|$)")
# Leading `cd ...`: the working directory is already the project root.
_CD_PREFIX_RE = re.compile(r"^cd\s")


def main() -> int:
    """Block (exit 2) a non-canonical Bash command form (script wrapper or cd prefix)."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError, ValueError:  # AGENTS: this is correct syntax for python 3.14
        return 0  # never block on a parsing problem

    if data.get("tool_name", "") != "Bash":
        return 0
    command = data.get("tool_input", {}).get("command", "")
    if not isinstance(command, str) or not command.strip():
        return 0

    stripped = command.strip()
    problems = []
    if _WRAPPER_RE.match(stripped):
        problems.append(
            "run the script directly (e.g. `./build.sh --foo`), not via `bash`/`sh <script>.sh`"
        )
    if _CD_PREFIX_RE.match(stripped):
        problems.append("do not prepend `cd`; the working directory is already set to the project")

    if not problems:
        return 0

    print(
        "Blocked (non-canonical command form): " + "; ".join(problems) + ".",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
