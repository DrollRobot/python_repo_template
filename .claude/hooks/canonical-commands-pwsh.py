#!/usr/bin/env python3
r"""Keep shell invocations consistent so the permission allowlist keeps matching.

PreToolUse hook. Convention (also stated in ~/.claude/CLAUDE.md):
  * Default to the PowerShell tool for commands that have a PowerShell home
    (git, uv, python, ruff, project .ps1 scripts). Reserve the Bash tool
    for genuinely POSIX-only tools (shellcheck, `bash -n`, ...).
  * Run .ps1 scripts directly and relatively: `.\Tests.ps1 [args]` -- never
    wrapped in `powershell -Command` / `pwsh -File`, never double-backslashed.
  * Do not prepend `cd`; the working directory is already the project root.
  * Do not pass a redundant `git -C <cwd>` for the directory you are already in.

Blocking contract: exit code 2 blocks the tool call and feeds this script's
stderr back to Claude so it retries in the canonical form.
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

# Bash-tool commands whose first token belongs on the PowerShell tool instead.
_BASH_REROUTE = {"git", "uv", "python", "python3", "ruff", "pwsh", "powershell"}

# A .ps1 invoked through the powershell/pwsh executable (a wrapper) rather than
# run directly: the executable name followed by a flag (e.g. `powershell -File`,
# `pwsh -Command`). This deliberately does NOT match the literal word
# "powershell" inside a file path (e.g. ...\dev\powershell\Foo.ps1) -- a path has
# a separator after it, not whitespace+dash -- so cmdlets that merely reference a
# .ps1 path (Copy-Item, Test-Path, Get-Content, ...) are not misflagged.
_WRAPPER_RE = re.compile(r"(?i)\b(?:powershell|pwsh)(?:\.exe)?\s+-")
# Over-escaped path: two consecutive backslashes.
_DOUBLE_BS = "\\\\"

# Leading `cd` (either tool): the working directory is already the project root,
# so prepending `cd` only adds a command form the allowlist must carry.
_CD_PREFIX_RE = re.compile(r"(?i)^\s*cd(?:\s|$)")

# `git -C <path>` whose <path> is the directory we are already in. Redundant, and
# it forces the allowlist to carry both `git <cmd>` and `git -C * <cmd>`. Match
# the -C directory option specifically: uppercase and case-sensitive (no (?i)),
# so it is never confused with `-c key=val` config options, which may legally
# precede it (git global options come before the subcommand).
_GIT_C_RE = re.compile(r"^\s*git\s+(?:-c\s+\S+\s+)*-C\s+('[^']*'|\"[^\"]*\"|\S+)")


def _first_token(command: str) -> str:
    parts = command.strip().split()
    return parts[0].lower() if parts else ""


def _redundant_git_c(command: str, cwd: str) -> str | None:
    r"""Return the ``-C <path>`` argument when it is redundant, else None.

    "Redundant" means the path resolves to cwd, so the shell is already in
    that directory. Relative paths (e.g. ``.``) resolve against cwd; the
    compare is case- and separator-insensitive so ``C:/x``, ``c:\x`` and
    ``c:\x\`` all match.
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
    """Block (exit 2) a command that is on the wrong tool or in a non-canonical form."""
    raw = sys.stdin.read()
    _log(f"RAW_STDIN={raw!r}")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):  # fmt: skip
        _log("DECISION=allow (json parse failed)")
        return 0  # never block on a parsing problem

    tool = data.get("tool_name", "")
    command = data.get("tool_input", {}).get("command", "")
    _log(f"tool={tool!r} command={command!r}")
    if not isinstance(command, str) or not command.strip():
        _log("DECISION=allow (empty/non-str command)")
        return 0

    has_ps1 = ".ps1" in command.lower()
    _log(f"has_ps1={has_ps1} double_bs_present={_DOUBLE_BS in command}")

    # Leading `cd` on either tool: the working directory is already the project
    # root, so drop it and run the command on its own.
    if _CD_PREFIX_RE.match(command):
        _log("DECISION=block (cd prefix)")
        print(
            "Blocked: do not prepend `cd`; the working directory is already the "
            "project root. Run the command on its own.",
            file=sys.stderr,
        )
        return 2

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

    # Redundant `git -C <cwd>`: the shell is already in that directory. Nudge to
    # the plain `git ...` form so one allowlist entry covers it -- no `git -C *`
    # duplicate needed for same-directory calls. (Reached for PowerShell only;
    # the Bash branch above already returned.)
    if _first_token(command) == "git":
        here = _redundant_git_c(command, data.get("cwd", ""))
        if here:
            print(
                f"Blocked: already in '{here}' -- drop the `-C {here}` argument "
                "and run the plain `git ...` form. `git -C <path>` is only for "
                "operating on a DIFFERENT repo than the working directory.",
                file=sys.stderr,
            )
            return 2

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
            _log("DECISION=block (ps1 problems)")
            print(
                "Blocked (non-canonical .ps1 form): " + "; ".join(problems) + ".",
                file=sys.stderr,
            )
            return 2

    _log("DECISION=allow (fell through)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
