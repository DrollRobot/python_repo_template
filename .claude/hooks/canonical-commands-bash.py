#!/usr/bin/env python3
r"""Keep bash command invocations consistent so the permission allowlist matches.

PreToolUse hook for the Bash tool. Enforces a single invocation form so the same
logical command does not reach the permission allowlist in a dozen shapes:

  * Run `.sh` scripts as `bash script.sh` -- not `sh script.sh` or `./script.sh`.
    `bash <script>` is the one form that always works: it needs no execute bit on
    the file, and it always uses bash (not whatever `sh` happens to point at).
    `bash -n` (syntax check), `bash -c '...'` and `sh -c '...'` stay allowed.
  * Do not run a `.ps1` from the Bash tool (via `pwsh`/`powershell`, or `./x.ps1`).
    Use the PowerShell tool instead.
  * Do not prepend `cd`; the working directory is already the project root.
  * Do not pass a redundant `git -C <cwd>` for the directory you are already in.

Blocking contract: exit code 2 blocks the call and feeds stderr back to Claude
so it retries in the canonical form.
"""

import contextlib
import datetime
import json
import os
import re
import sys

# --- OPTIONAL DEBUG LOGGING ---
# Off by default. Set CLAUDE_HOOK_DEBUG_LOG to a truthy value (1/true/yes/on) to
# append each invocation to hook-debug.log beside this script; the log
# self-rotates so it never grows unbounded. When disabled, _log() short-circuits
# before any filesystem access, so there is zero overhead in normal operation.
_DEBUG = os.environ.get("CLAUDE_HOOK_DEBUG_LOG", "").strip().lower() in {"1", "true", "yes", "on"}
_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook-debug.log")
_LOG_MAX_BYTES = 1 * 1024 * 1024  # rotate once the log grows past ~1 MB
_LOG_DROP_FRACTION = 0.20  # ...by dropping the oldest 20% of lines


def _rotate_if_needed() -> None:
    """Trim the debug log when it grows past _LOG_MAX_BYTES.

    Discards the oldest _LOG_DROP_FRACTION of lines, keeping the newest.
    Best-effort only; any error leaves the log untouched.
    """
    with contextlib.suppress(Exception):
        if os.path.getsize(_LOG) <= _LOG_MAX_BYTES:
            return
        with open(_LOG, encoding="utf-8") as fh:
            lines = fh.readlines()
        keep = lines[int(len(lines) * _LOG_DROP_FRACTION) :]
        with open(_LOG, "w", encoding="utf-8") as fh:
            fh.writelines(keep)


def _log(msg: str) -> None:
    """Append msg to the debug log when CLAUDE_HOOK_DEBUG_LOG is enabled.

    A no-op otherwise. Never raises: logging must not interfere with the
    tool call.
    """
    if not _DEBUG:
        return
    with contextlib.suppress(Exception):
        _rotate_if_needed()
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


# --- END OPTIONAL DEBUG LOGGING ---

# `sh <script>.sh`: run it as `bash <script>.sh` instead. The negative lookahead
# skips a flag, so inline `sh -c '...'` is left alone.
_SH_WRAPPER_RE = re.compile(r"^sh\s+(?!-)\S*\.sh(?:\s|$)")
# `./<script>.sh` (or `./dir/script.sh`): run it as `bash <script>.sh` instead.
_DOTSLASH_SH_RE = re.compile(r"^\./\S*\.sh(?:\s|$)")
# Leading `cd ...` (or a bare `cd`): the working directory is already the root.
_CD_PREFIX_RE = re.compile(r"^\s*cd(?:\s|$)")
# A `pwsh`/`powershell` executable, used together with a `.ps1` presence check.
_PS_EXE_RE = re.compile(r"(?i)\b(?:pwsh|powershell)(?:\.exe)?\b")
# `./<script>.ps1`: trying to execute a PowerShell script from the Bash tool.
_DOTSLASH_PS1_RE = re.compile(r"^\./\S*\.ps1(?:\s|$)")

# `git -C <path>` for the directory we are already in -- redundant. Kept byte for
# byte in sync with the pwsh hook: same option semantics (uppercase -C only, so a
# `-c key=val` config option that may legally precede it is never confused).
_GIT_C_RE = re.compile(r"^\s*git\s+(?:-c\s+\S+\s+)*-C\s+('[^']*'|\"[^\"]*\"|\S+)")


def _first_token(command: str) -> str:
    parts = command.strip().split()
    return parts[0].lower() if parts else ""


def _redundant_git_c(command: str, cwd: str) -> str | None:
    r"""Return the ``-C <path>`` argument when it is redundant, else None.

    "Redundant" means the path resolves to cwd, so the shell is already in
    that directory. Relative paths (e.g. ``.``) resolve against cwd; the
    compare is case- and separator-insensitive on the running platform.
    """
    if not cwd:
        return None
    match = _GIT_C_RE.match(command)
    if not match:
        return None
    raw = match.group(1).strip().strip('"').strip("'")
    if not raw:
        return None
    target = raw if os.path.isabs(raw) else os.path.join(cwd, raw)
    same = os.path.normcase(os.path.normpath(target)) == os.path.normcase(os.path.normpath(cwd))
    return raw if same else None


def main() -> int:
    """Block (exit 2) a non-canonical Bash command form."""
    raw = sys.stdin.read()
    _log(f"RAW_STDIN={raw!r}")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):  # fmt: skip
        _log("DECISION=allow (json parse failed)")
        return 0  # never block on a parsing problem

    tool = data.get("tool_name", "")
    if tool != "Bash":
        _log(f"DECISION=allow (tool={tool!r})")
        return 0
    command = data.get("tool_input", {}).get("command", "")
    if not isinstance(command, str) or not command.strip():
        _log("DECISION=allow (empty/non-str command)")
        return 0

    stripped = command.strip()
    has_ps1 = ".ps1" in command.lower()
    _log(f"command={command!r} has_ps1={has_ps1}")

    # Running a .ps1 from the Bash tool -- via pwsh/powershell, or `./x.ps1` --
    # is the wrong tool. Steer to the PowerShell tool.
    if (has_ps1 and _PS_EXE_RE.search(command)) or _DOTSLASH_PS1_RE.match(stripped):
        _log("DECISION=block (ps1 from Bash tool)")
        print(
            "Blocked: do not run a .ps1 script from the Bash tool. Use the "
            "PowerShell tool instead.",
            file=sys.stderr,
        )
        return 2

    problems = []
    if _CD_PREFIX_RE.match(stripped):
        problems.append("do not prepend `cd`; the working directory is already the project root")
    if _SH_WRAPPER_RE.match(stripped):
        problems.append("run `.sh` scripts as `bash script.sh`, not `sh script.sh`")
    if _DOTSLASH_SH_RE.match(stripped):
        problems.append("run `.sh` scripts as `bash script.sh`, not `./script.sh`")
    if problems:
        _log("DECISION=block (non-canonical form)")
        print(
            "Blocked (non-canonical command form): " + "; ".join(problems) + ".",
            file=sys.stderr,
        )
        return 2

    # Redundant `git -C <cwd>`: the shell is already in that directory. Nudge to
    # the plain `git ...` form so one allowlist entry covers it.
    if _first_token(command) == "git":
        here = _redundant_git_c(command, data.get("cwd", ""))
        if here:
            _log("DECISION=block (redundant git -C)")
            print(
                f"Blocked: already in '{here}' -- drop the `-C {here}` argument "
                "and run the plain `git ...` form. `git -C <path>` is only for "
                "operating on a DIFFERENT repo than the working directory.",
                file=sys.stderr,
            )
            return 2

    _log("DECISION=allow (fell through)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
