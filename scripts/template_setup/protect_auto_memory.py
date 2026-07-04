"""Optionally enable Claude Code's auto-memory write guard for this project.

The template ships a ``PreToolUse`` hook at
``.claude/hooks/protect-auto-memory.py`` that asks for explicit approval before
Claude writes to its auto-memory directory
(``~/.claude/projects/<project>/memory/``). The hook is off until you opt in.

Accept and this wires the hook into ``.claude/settings.json`` at project scope
(using ``$CLAUDE_PROJECT_DIR`` so it resolves from any working directory),
keeping the hook file. Decline (or pass ``--no-guard``) and it removes the hook
file and any stale wiring so nothing lingers.

See the hook file's header for how to run it globally (for every project)
instead of per-project.

Usage:
    uv run scripts/template_setup/protect_auto_memory.py
    uv run scripts/template_setup/protect_auto_memory.py --enable
    uv run scripts/template_setup/protect_auto_memory.py --no-guard
    uv run scripts/template_setup/protect_auto_memory.py --enable --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _common

# The hook file the template ships, the directory it lives in, and the committed
# settings file it is wired into. The command runs a stdlib-only hook, so no
# virtualenv interpreter is needed; POSIX-only users may prefer "python3".
HOOK_FILE = "protect-auto-memory.py"
HOOKS_DIR = Path(".claude") / "hooks"
SETTINGS_PATH = Path(".claude") / "settings.json"
MATCHER = "Write|Edit"
PYTHON = "python"


def _hook_command() -> str:
    """Build the shell command Claude Code runs for the memory-guard hook.

    Returns:
        The command string, using ``$CLAUDE_PROJECT_DIR`` so it resolves from
        any working directory.
    """
    target = f"$CLAUDE_PROJECT_DIR/{HOOKS_DIR.as_posix()}/{HOOK_FILE}"
    return f'{PYTHON} "{target}"'


def _references_our_hook(entry: dict) -> bool:
    """Return whether a ``PreToolUse`` entry points at the memory-guard hook.

    Args:
        entry: A single matcher entry from ``hooks.PreToolUse``.

    Returns:
        ``True`` if any of the entry's commands reference the hook file.
    """
    return any(HOOK_FILE in hook.get("command", "") for hook in entry.get("hooks", []))


def _build_entry() -> dict:
    """Build the ``PreToolUse`` matcher entry that wires the memory-guard hook.

    Returns:
        A matcher entry ready to append to ``hooks.PreToolUse``.
    """
    return {
        "matcher": MATCHER,
        "hooks": [{"type": "command", "command": _hook_command()}],
    }


def _without_our_entry(existing: dict) -> dict:
    """Return a copy of settings with any memory-guard entry removed.

    Preserves unrelated keys and any ``PreToolUse`` entries that do not
    reference our hook file, and drops an emptied ``PreToolUse``/``hooks`` so no
    empty scaffolding is left behind.

    Args:
        existing: Parsed contents of ``settings.json``.

    Returns:
        The settings mapping without our entry.
    """
    settings = dict(existing)
    hooks = dict(settings.get("hooks", {}))
    pre = [e for e in hooks.get("PreToolUse", []) if not _references_our_hook(e)]
    if pre:
        hooks["PreToolUse"] = pre
    else:
        hooks.pop("PreToolUse", None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return settings


def _merge_settings(existing: dict) -> dict:
    """Merge the memory-guard entry into an existing settings mapping.

    Preserves unrelated keys and ``PreToolUse`` entries; replaces any prior
    memory-guard entry so re-running is idempotent.

    Args:
        existing: Parsed contents of ``settings.json`` (``{}`` if absent).

    Returns:
        The updated settings mapping.
    """
    settings = _without_our_entry(existing)
    hooks = dict(settings.get("hooks", {}))
    pre = list(hooks.get("PreToolUse", []))
    pre.append(_build_entry())
    hooks["PreToolUse"] = pre
    settings["hooks"] = hooks
    return settings


def _is_wired(settings: dict) -> bool:
    """Return whether the parsed settings already wire the memory-guard hook.

    Args:
        settings: Parsed contents of ``settings.json``.

    Returns:
        ``True`` if any ``PreToolUse`` entry references our hook file.
    """
    return any(
        _references_our_hook(entry) for entry in settings.get("hooks", {}).get("PreToolUse", [])
    )


def _read_settings(path: Path) -> dict | None:
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


def _write_settings(path: Path, settings: dict) -> None:
    """Write settings as pretty JSON, or delete the file when it would be empty.

    Args:
        path: Path to ``settings.json``.
        settings: The settings mapping to persist.
    """
    if settings:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def _disable(hook_path: Path, settings_path: Path, *, dry_run: bool = False) -> int:
    """Remove the memory-guard hook file and any wiring that points at it.

    Args:
        hook_path: Path to the hook file under ``.claude/hooks``.
        settings_path: Path to ``.claude/settings.json``.
        dry_run: Show what would be removed without changing anything.

    Returns:
        Process exit code (0 on success, 1 when an existing ``settings.json``
        cannot be parsed).
    """
    settings = _read_settings(settings_path)
    if settings is None:
        return 1
    present = hook_path.exists()
    wired = _is_wired(settings)

    _common.info("Memory guard", "off")
    if present:
        print(f"  Delete: {HOOK_FILE}")
    if wired:
        print(f"  Unwire from: {SETTINGS_PATH}")
    if not present and not wired:
        print("  Nothing to remove.")

    if dry_run:
        print("\n  (dry run -- nothing changed)")
        return 0

    if present:
        hook_path.unlink()
        if hook_path.parent.exists() and not any(hook_path.parent.iterdir()):
            hook_path.parent.rmdir()
    if wired:
        _write_settings(settings_path, _without_our_entry(settings))

    if present or wired:
        print("\n  Removed the auto-memory guard.")
    return 0


def run(
    root: Path,
    *,
    install: bool | None = None,
    assume_yes: bool = False,
    dry_run: bool = False,
) -> int:
    """Enable the auto-memory write guard, or remove it on decline.

    Args:
        root: Project root directory.
        install: Whether to enable the guard; prompt if ``None``.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.

    Returns:
        Process exit code (0 on success, 1 if aborted, the hook is missing, or
        an existing ``settings.json`` cannot be parsed).
    """
    _common.section("Claude Code auto-memory guard")

    hook_path = root / HOOKS_DIR / HOOK_FILE
    settings_path = root / SETTINGS_PATH

    if install is None:
        install = _prompt_install()
    if not install:
        return _disable(hook_path, settings_path, dry_run=dry_run)

    if not hook_path.exists():
        print(f"  Missing hook file: {HOOK_FILE}. Has setup already run?")
        return 1

    settings = _read_settings(settings_path)
    if settings is None:
        return 1

    _common.info("Wire into", str(SETTINGS_PATH))
    print(f"  Keep: {HOOK_FILE}")
    print("  Prompts before Claude writes to its auto-memory directory.")

    if dry_run:
        print("\n  (dry run -- nothing changed)")
        return 0

    print()
    if not _common.confirm("Enable the auto-memory guard?", assume_yes=assume_yes):
        print("  Aborted; nothing changed.")
        return 1

    _write_settings(settings_path, _merge_settings(settings))
    print(f"\n  Wrote {SETTINGS_PATH}.")
    print("  Restart Claude Code (or run /hooks) so it picks up the new hook.")
    return 0


def _prompt_install() -> bool:
    """Ask whether to enable the auto-memory write guard.

    Returns:
        ``True`` to enable (wire the hook), ``False`` to remove the hook file.
    """
    print("Claude auto-memory guard:")
    print("  Prompts for your approval before Claude writes to its auto-memory")
    print("  directory (~/.claude/projects/<project>/memory/).")
    print("Declining removes the hook script entirely.")
    print()
    return _common.confirm("  Enable the auto-memory guard?")


def main() -> None:
    """Parse arguments and run the memory-guard installer."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--enable", action="store_true", help="Enable the guard (wire the hook).")
    group.add_argument(
        "--no-guard", action="store_true", help="Remove the guard and its hook file."
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing."
    )
    args = parser.parse_args()

    # --no-guard removes; --enable installs; otherwise prompt.
    install: bool | None = True if args.enable else False if args.no_guard else None

    root = _common.find_root()
    sys.exit(run(root, install=install, assume_yes=args.yes, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
