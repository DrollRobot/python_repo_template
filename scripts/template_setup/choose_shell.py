"""Pick a primary shell and wire its command hooks into Claude Code.

The template ships two flavors of two ``PreToolUse`` hooks under
``.claude/hooks/``:

    canonical-commands-pwsh.py   no-chained-commands-pwsh.py   (PowerShell)
    canonical-commands-bash.py   no-chained-commands-bash.py   (Bash)

Each hook keeps shell invocations consistent so a permission allowlist keeps
matching. This script asks which shell you primarily use, writes the matching
pair into ``.claude/settings.json`` (merging with anything already there), and
deletes the unused pair so the project ships only the hooks it uses.

Usage:
    uv run scripts/template_setup/choose_shell.py
    uv run scripts/template_setup/choose_shell.py --shell bash
    uv run scripts/template_setup/choose_shell.py --shell powershell --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _common

# Per-shell wiring. ``hooks`` are the hook files to keep (and reference from
# settings); ``drop`` are the other shell's files to delete; ``matcher`` is the
# tool-name regex Claude Code uses to decide when to run them; ``python`` is the
# interpreter the hook command invokes (stdlib-only hooks, so no venv needed).
SHELLS = {
    "powershell": {
        "matcher": "Bash|PowerShell",
        "python": "python",
        "hooks": ["no-chained-commands-pwsh.py", "canonical-commands-pwsh.py"],
        "drop": ["no-chained-commands-bash.py", "canonical-commands-bash.py"],
    },
    "bash": {
        "matcher": "Bash",
        "python": "python3",
        "hooks": ["no-chained-commands-bash.py", "canonical-commands-bash.py"],
        "drop": ["no-chained-commands-pwsh.py", "canonical-commands-pwsh.py"],
    },
}

# Directory (relative to the project root) holding the hook scripts, and the
# committed settings file the hooks are wired into.
HOOKS_DIR = Path(".claude") / "hooks"
SETTINGS_PATH = Path(".claude") / "settings.json"

# Every hook file the template ships, used to strip stale entries from an
# existing settings file so re-running this step is idempotent.
_ALL_HOOK_FILES = {name for spec in SHELLS.values() for name in spec["hooks"]}


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


def _references_our_hook(entry: dict) -> bool:
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


def _build_entry(spec: dict) -> dict:
    """Build the ``PreToolUse`` matcher entry for the chosen shell.

    Args:
        spec: One value from :data:`SHELLS`.

    Returns:
        A matcher entry ready to append to ``hooks.PreToolUse``.
    """
    return {
        "matcher": spec["matcher"],
        "hooks": [
            {"type": "command", "command": _hook_command(spec["python"], name)}
            for name in spec["hooks"]
        ],
    }


def _merge_settings(existing: dict, entry: dict) -> dict:
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


def run(
    root: Path,
    shell: str | None = None,
    *,
    assume_yes: bool = False,
    dry_run: bool = False,
) -> int:
    """Wire the chosen shell's hooks into settings and drop the other shell's.

    Args:
        root: Project root directory.
        shell: ``powershell`` or ``bash``; prompt if ``None``.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.

    Returns:
        Process exit code (0 on success, 1 if aborted or invalid).
    """
    _common.section("Choose a primary shell")

    if shell is None:
        shell = _prompt_choice()
    shell = shell.lower()
    if shell not in SHELLS:
        print(f"  '{shell}' is not valid. Choose from: {', '.join(sorted(SHELLS))}.")
        return 1
    spec = SHELLS[shell]

    hooks_dir = root / HOOKS_DIR
    missing = [name for name in spec["hooks"] if not (hooks_dir / name).exists()]
    if missing:
        print(f"  Missing hook file(s): {', '.join(missing)}. Has setup already run?")
        return 1

    settings_path = root / SETTINGS_PATH
    drop = [name for name in spec["drop"] if (hooks_dir / name).exists()]

    _common.info("Shell", shell)
    _common.info("Wire into", str(SETTINGS_PATH))
    print(f"  Keep: {', '.join(spec['hooks'])}")
    if drop:
        print(f"  Delete: {', '.join(drop)}")

    if dry_run:
        print("\n  (dry run -- nothing changed)")
        return 0

    print()
    if not _common.confirm("Apply shell choice?", assume_yes=assume_yes):
        print("  Aborted; nothing changed.")
        return 1

    existing = _read_settings(settings_path)
    settings = _merge_settings(existing, _build_entry(spec))
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    for name in drop:
        (hooks_dir / name).unlink()

    print(f"\n  Wrote {SETTINGS_PATH}.")
    print("  Restart Claude Code (or run /hooks) so it picks up the new hooks.")
    return 0


def _read_settings(path: Path) -> dict:
    """Read and parse ``settings.json``, returning ``{}`` if absent or invalid.

    Args:
        path: Path to the settings file.

    Returns:
        The parsed mapping, or an empty mapping when the file is missing, empty,
        or not a JSON object.
    """
    text = _common.read_text(path)
    if not text or not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print(f"  WARNING: {path.name} is not valid JSON; starting fresh.")
        return {}
    return data if isinstance(data, dict) else {}


def _prompt_choice() -> str:
    """Prompt the user to pick a primary shell by number.

    Returns:
        The chosen shell key.
    """
    keys = sorted(SHELLS)
    print("  Available shells:")
    for index, key in enumerate(keys, start=1):
        print(f"    {index}) {key}")
    while True:
        answer = input("  Choose your primary shell [number]: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        print("  Enter the number of one of the listed shells.")


def main() -> None:
    """Parse arguments and run the shell chooser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shell", choices=sorted(SHELLS), help="Primary shell to wire in.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing."
    )
    args = parser.parse_args()

    root = _common.find_root()
    sys.exit(run(root, args.shell, assume_yes=args.yes, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
