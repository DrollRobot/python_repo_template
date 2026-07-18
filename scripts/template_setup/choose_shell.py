"""Optionally install Claude Code command hooks, wired to your primary shell.

The template ships two independent ``PreToolUse`` hooks under
``.claude/hooks/``, each in a powershell/bash flavor:

    no-chained-commands-pwsh.py   no-chained-commands-bash.py
    canonical-commands-pwsh.py    canonical-commands-bash.py

``no-chained-commands`` requires one shell command per tool call, so a
permission allowlist keeps matching. ``canonical-commands`` keeps shell
invocation consistent, so you don't need to allow multiple equivalent
commands. Each hook is independently optional. If at least one is wanted,
this script asks which shell you primarily use, writes the matching pair
into ``.claude/settings.json`` (merging with anything already there), and
deletes every hook file that isn't wanted -- the unused shell's files, and
either hook kind you declined.

Usage:
    uv run scripts/template_setup/choose_shell.py
    uv run scripts/template_setup/choose_shell.py --shell bash
    uv run scripts/template_setup/choose_shell.py --shell powershell --dry-run
    uv run scripts/template_setup/choose_shell.py --shell bash --no-canonical-commands
    uv run scripts/template_setup/choose_shell.py --no-hooks
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import _common

# Per-shell wiring shared by both hook kinds: the tool-name regex Claude Code
# uses to decide when to run them, and the interpreter the hook command
# invokes (stdlib-only hooks, so no venv needed).
_SHELL_META = {
    "powershell": {"matcher": "Bash|PowerShell", "python": "python"},
    "bash": {"matcher": "Bash", "python": "python3"},
}

# The two independently-toggleable hook kinds, in display/wiring order.
_KINDS = ("no_chained_commands", "canonical_commands")

# (kind, shell) -> the hook file that implements it.
_FILE_BY_KIND_AND_SHELL = {
    ("no_chained_commands", "powershell"): "no-chained-commands-pwsh.py",
    ("no_chained_commands", "bash"): "no-chained-commands-bash.py",
    ("canonical_commands", "powershell"): "canonical-commands-pwsh.py",
    ("canonical_commands", "bash"): "canonical-commands-bash.py",
}

# Every hook file the template ships, used to strip stale entries from an
# existing settings file so re-running this step is idempotent.
_ALL_HOOK_FILES = set(_FILE_BY_KIND_AND_SHELL.values())

# Directory (relative to the project root) holding the hook scripts, and the
# committed settings file the hooks are wired into.
HOOKS_DIR = Path(".claude") / "hooks"
SETTINGS_PATH = Path(".claude") / "settings.json"


def _hook_command(python: str, hook_file: str) -> str:
    """Build the shell command Claude Code runs for one hook.

    Args:
        python: Interpreter to invoke (e.g. ``python`` or ``python3``).
        hook_file: Hook script file name under ``.claude/hooks/``.

    Returns:
        The command string, using ``$CLAUDE_PROJECT_DIR`` so it resolves from
        any working directory.
    """
    target = f"$CLAUDE_PROJECT_DIR/{HOOKS_DIR.as_posix()}/{hook_file}"
    return f'{python} "{target}"'


def _references_our_hook(entry: dict[str, Any]) -> bool:
    """Return whether a ``PreToolUse`` entry points at one of our hook files.

    Args:
        entry: A single matcher entry from ``hooks.PreToolUse``.

    Returns:
        ``True`` if any of the entry's commands reference a template hook file.
    """
    for hook in entry.get("hooks", []):
        command = hook.get("command", "")
        if any(name in command for name in _ALL_HOOK_FILES):
            return True
    return False


def _build_entry(shell: str, kinds: frozenset[str]) -> dict[str, Any]:
    """Build the ``PreToolUse`` matcher entry for the chosen shell and hook kinds.

    Args:
        shell: ``powershell`` or ``bash``.
        kinds: Which hook kinds to include (subset of :data:`_KINDS`).

    Returns:
        A matcher entry ready to append to ``hooks.PreToolUse``.
    """
    meta = _SHELL_META[shell]
    files = [_FILE_BY_KIND_AND_SHELL[(kind, shell)] for kind in _KINDS if kind in kinds]
    return {
        "matcher": meta["matcher"],
        "hooks": [
            {"type": "command", "command": _hook_command(meta["python"], name)} for name in files
        ],
    }


def _merge_settings(existing: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Merge our hook entry into an existing settings mapping.

    Preserves unrelated keys and any ``PreToolUse`` entries that do not point at
    our hook files; replaces any that do (so switching shells is idempotent).

    Args:
        existing: Parsed contents of ``settings.json`` (``{}`` if absent).
        entry: The matcher entry to install.

    Returns:
        The updated settings mapping.
    """
    settings = dict(existing)
    hooks = dict(settings.get("hooks", {}))
    pre = [e for e in hooks.get("PreToolUse", []) if not _references_our_hook(e)]
    pre.append(entry)
    hooks["PreToolUse"] = pre
    settings["hooks"] = hooks
    return settings


def _remove_all_hooks(hooks_dir: Path) -> list[str]:
    """Delete every template hook file (and the dir if it empties out).

    Args:
        hooks_dir: The ``.claude/hooks`` directory.

    Returns:
        Sorted names of the hook files that were present and removed.
    """
    removed = []
    for name in sorted(_ALL_HOOK_FILES):
        path = hooks_dir / name
        if path.exists():
            path.unlink()
            removed.append(name)
    if hooks_dir.exists() and not any(hooks_dir.iterdir()):
        hooks_dir.rmdir()
    return removed


def _decline_hooks(hooks_dir: Path, *, dry_run: bool = False) -> int:
    """Remove all hook files when neither hook kind is wanted.

    Args:
        hooks_dir: The ``.claude/hooks`` directory.
        dry_run: Show what would be removed without deleting anything.

    Returns:
        Process exit code (always 0).
    """
    present = sorted(name for name in _ALL_HOOK_FILES if (hooks_dir / name).exists())
    _common.info("Hooks", "skipped")
    if present:
        print(f"  Delete: {', '.join(present)}")
    else:
        print("  No hook files to remove.")

    if dry_run:
        print("\n  (dry run -- nothing changed)")
        return 0

    _remove_all_hooks(hooks_dir)
    if present:
        print("\n  Removed the pre-tool hook scripts; no settings written.")
    return 0


def run(
    root: Path,
    shell: str | None = None,
    *,
    no_chained_commands: bool | None = None,
    canonical_commands: bool | None = None,
    assume_yes: bool = False,
    dry_run: bool = False,
) -> int:
    """Install the chosen hooks into settings, or remove all of them on decline.

    Args:
        root: Project root directory.
        shell: ``powershell`` or ``bash``; prompt if ``None`` (only when at
            least one hook kind is wanted).
        no_chained_commands: Whether to install the no-chained-commands hook;
            prompt if ``None``.
        canonical_commands: Whether to install the canonical-commands hook;
            prompt if ``None``.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.

    Returns:
        Process exit code (0 on success, 1 if aborted, the shell is invalid, or
        an existing ``settings.json`` cannot be parsed).
    """
    _common.section("Claude Code command hooks")

    hooks_dir = root / HOOKS_DIR

    if no_chained_commands is None:
        no_chained_commands = _prompt_kind(
            "no-chained-commands",
            "Requires one shell command per tool call, so a permission allowlist keeps matching.",
        )
    if canonical_commands is None:
        canonical_commands = _prompt_kind(
            "canonical-commands",
            "Keeps shell invocation consistent, so you don't need to allow multiple equivalent"
            " commands.",
        )

    kinds = frozenset(
        kind
        for kind, wanted in (
            ("no_chained_commands", no_chained_commands),
            ("canonical_commands", canonical_commands),
        )
        if wanted
    )
    if not kinds:
        return _decline_hooks(hooks_dir, dry_run=dry_run)

    if shell is None:
        shell = _prompt_choice()
    shell = shell.lower()
    if shell not in _SHELL_META:
        print(f"  '{shell}' is not valid. Choose from: {', '.join(sorted(_SHELL_META))}.")
        return 1

    keep = [_FILE_BY_KIND_AND_SHELL[(kind, shell)] for kind in _KINDS if kind in kinds]
    missing = [name for name in keep if not (hooks_dir / name).exists()]
    if missing:
        print(f"  Missing hook file(s): {', '.join(missing)}. Has setup already run?")
        return 1

    settings_path = root / SETTINGS_PATH
    existing = _read_settings(settings_path)
    if existing is None:
        return 1
    drop = sorted(name for name in _ALL_HOOK_FILES - set(keep) if (hooks_dir / name).exists())

    _common.info("Shell", shell)
    _common.info("Wire into", str(SETTINGS_PATH))
    print(f"  Keep: {', '.join(keep)}")
    if drop:
        print(f"  Delete: {', '.join(drop)}")

    if dry_run:
        print("\n  (dry run -- nothing changed)")
        return 0

    print()
    if not _common.confirm("Apply hook choice?", assume_yes=assume_yes):
        print("  Aborted; nothing changed.")
        return 1

    settings = _merge_settings(existing, _build_entry(shell, kinds))
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    for name in drop:
        (hooks_dir / name).unlink()

    print(f"\n  Wrote {SETTINGS_PATH}.")
    print("  Restart Claude Code (or run /hooks) so it picks up the new hooks.")
    return 0


def _read_settings(path: Path) -> dict[str, Any] | None:
    """Read and parse ``settings.json``, refusing to proceed on invalid content.

    A missing or empty file reads as ``{}`` (a fresh start). A file that exists
    but cannot be parsed returns ``None`` so callers abort instead of rewriting
    the file and silently discarding whatever it held (permission rules, other
    hooks, and so on).

    Args:
        path: Path to the settings file.

    Returns:
        The parsed mapping, ``{}`` when the file is missing or empty, or
        ``None`` when the file exists but is unreadable, not valid JSON, or not
        a JSON object.
    """
    if not path.exists():
        return {}
    text = _common.read_text(path)
    if text is None:
        print(f"  ERROR: {path} is not readable as UTF-8 text.")
        print("  Fix or delete it, then re-run this step.")
        return None
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"  ERROR: {path} is not valid JSON ({exc}).")
        print("  Fix or delete it, then re-run this step.")
        return None
    if not isinstance(data, dict):
        print(f"  ERROR: {path} does not contain a JSON object.")
        print("  Fix or delete it, then re-run this step.")
        return None
    return data


def _prompt_kind(label: str, description: str) -> bool:
    """Ask whether to install one hook kind.

    Args:
        label: Short hook name shown in the prompt (e.g. ``no-chained-commands``).
        description: One-line explanation of what the hook does.

    Returns:
        ``True`` to install this hook kind, ``False`` to decline it.
    """
    print(f"{label}: {description}")
    return _common.confirm(f"  Install the {label} hook?")


def _prompt_choice() -> str:
    """Prompt the user to pick a primary shell by number.

    Returns:
        The chosen shell key.
    """
    keys = sorted(_SHELL_META)
    print("  Available shells:")
    for index, key in enumerate(keys, start=1):
        print(f"    {index}) {key}")
    while True:
        answer = input("  Choose your primary shell [number]: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        print("  Enter the number of one of the listed shells.")


def main() -> None:
    """Parse arguments and run the hook installer."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--shell", choices=sorted(_SHELL_META), help="Primary shell to wire in.")
    group.add_argument(
        "--no-hooks", action="store_true", help="Skip both hooks and remove all hook files."
    )
    parser.add_argument(
        "--no-chained-commands",
        action="store_true",
        help="With --shell, skip the no-chained-commands hook.",
    )
    parser.add_argument(
        "--no-canonical-commands",
        action="store_true",
        help="With --shell, skip the canonical-commands hook.",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing."
    )
    args = parser.parse_args()

    # --no-hooks declines both; --shell implies wanting hooks (each kind can be
    # negated); otherwise prompt for each kind individually.
    no_chained_commands: bool | None
    canonical_commands: bool | None
    if args.no_hooks:
        no_chained_commands = False
        canonical_commands = False
    elif args.shell:
        no_chained_commands = not args.no_chained_commands
        canonical_commands = not args.no_canonical_commands
    else:
        no_chained_commands = None
        canonical_commands = None

    root = _common.find_root()
    sys.exit(
        run(
            root,
            args.shell,
            no_chained_commands=no_chained_commands,
            canonical_commands=canonical_commands,
            assume_yes=args.yes,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
