"""Wire Claude Code hooks into .claude/settings.json, or remove them.

Every hook the template ships is described once in :data:`HOOKS` and wired by
the same code path here. A hook is either kept (its file stays and an entry
pointing at it is merged into ``.claude/settings.json``) or declined (its file
is deleted and any entry pointing at it is stripped back out). Callers pass the
specs; this module owns the settings file.

Used by the setup steps rather than run directly:

* ``choose_shell.py`` resolves a shell and hook kinds, then hands over the
  files to keep and the files to delete.
* ``setup_new_project.py`` toggles the standalone guards straight from
  ``scripts/template_setup.toml`` via :func:`toggle`.

Enabled specs sharing a matcher are wired as one ``PreToolUse`` entry carrying
several commands, which is how the shell hook pair has always been written.

Every hook is launched the same way, and both halves of that are deliberate,
because ``.claude/settings.json`` is committed and read on whatever OS each
teammate uses:

* **Exec form** (``command`` + ``args``) means no shell is involved, so Claude
  Code substitutes ``${CLAUDE_PROJECT_DIR}`` itself. Shell form would be passed
  to ``sh -c`` on macOS/Linux, Git Bash on Windows, or *PowerShell* when Git
  Bash is absent -- where ``$CLAUDE_PROJECT_DIR`` expands to nothing.
* **``uv run --no-project``** because no interpreter name is portable:
  ``python3`` is a Store alias stub on Windows that fails with "Python was not
  found", and ``python`` is frequently absent on macOS/Linux. ``uv`` is one
  name on every platform, and this project already requires it for every
  documented command. ``--no-project`` skips the environment sync -- the hooks
  are stdlib-only -- costing roughly 45ms per invocation.

Only the entries naming the hooks in *this* call are replaced, so wiring one
hook never disturbs another's entry, and unrelated keys (permissions, model,
other tools' hooks) are preserved untouched. Settings are only rewritten when
the merge actually changes something.

Usage:
    uv run scripts/template_setup/wire_hook.py --hook auto_memory_guard --enable
    uv run scripts/template_setup/wire_hook.py --hook auto_memory_guard --disable
    uv run scripts/template_setup/wire_hook.py --hook no_inline_secrets --enable --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import _common

# Directory (relative to the project root) holding the hook scripts, and the
# committed settings file the hooks are wired into.
HOOKS_DIR = Path(".claude") / "hooks"
SETTINGS_PATH = Path(".claude") / "settings.json"

# How every hook is launched. See the module docstring for why it is uv and
# not a bare interpreter name.
LAUNCHER = "uv"
LAUNCHER_ARGS = ("run", "--no-project")

# Claude Code substitutes this itself in exec form; it is not a shell variable.
PROJECT_DIR_PLACEHOLDER = "${CLAUDE_PROJECT_DIR}"


@dataclass(frozen=True)
class HookSpec:
    """One hook the template ships, and everything needed to wire it.

    Attributes:
        key: Stable identifier, used by ``--hook`` and by the setup steps.
        file: The hook's file name under ``.claude/hooks/``.
        matcher: The ``PreToolUse`` matcher deciding which tool calls it sees.
        title: Human name for the section heading and prompts.
        summary: One line describing what the hook does, shown in the plan.
    """

    key: str
    file: str
    matcher: str
    title: str
    summary: str


# Every hook the template ships. The four shell hooks come in a
# powershell/bash pair per kind; choose_shell.py picks one flavor and deletes
# the rest.
HOOKS: tuple[HookSpec, ...] = (
    HookSpec(
        key="auto_memory_guard",
        file="protect-auto-memory.py",
        matcher="Write|Edit",
        title="auto-memory guard",
        summary="Prompts before Claude writes to its auto-memory directory.",
    ),
    HookSpec(
        key="no_inline_secrets",
        file="no-inline-secret-suppressions.py",
        matcher="Write|Edit|MultiEdit|NotebookEdit",
        title="inline-suppression guard",
        summary="Blocks writes that add a detect-secrets allowlist pragma.",
    ),
    HookSpec(
        key="no_chained_commands_pwsh",
        file="no-chained-commands-pwsh.py",
        matcher="Bash|PowerShell",
        title="no-chained-commands hook (powershell)",
        summary="Requires one shell command per tool call, so an allowlist keeps matching.",
    ),
    HookSpec(
        key="no_chained_commands_bash",
        file="no-chained-commands-bash.py",
        matcher="Bash",
        title="no-chained-commands hook (bash)",
        summary="Requires one shell command per tool call, so an allowlist keeps matching.",
    ),
    HookSpec(
        key="canonical_commands_pwsh",
        file="canonical-commands-pwsh.py",
        matcher="Bash|PowerShell",
        title="canonical-commands hook (powershell)",
        summary="Keeps shell invocation consistent, so equivalent commands need one rule.",
    ),
    HookSpec(
        key="canonical_commands_bash",
        file="canonical-commands-bash.py",
        matcher="Bash",
        title="canonical-commands hook (bash)",
        summary="Keeps shell invocation consistent, so equivalent commands need one rule.",
    ),
)

_BY_KEY = {spec.key: spec for spec in HOOKS}


def by_key(key: str) -> HookSpec:
    """Look up a hook spec by its :attr:`HookSpec.key`.

    Args:
        key: The spec's key.

    Returns:
        The matching spec.

    Raises:
        KeyError: If no hook has that key.
    """
    return _BY_KEY[key]


def hook_target(spec: HookSpec) -> str:
    """Build the path argument Claude Code passes to the launcher.

    Args:
        spec: The hook to build a path for.

    Returns:
        The hook's path, rooted at :data:`PROJECT_DIR_PLACEHOLDER` so it
        resolves from any working directory.
    """
    return f"{PROJECT_DIR_PLACEHOLDER}/{HOOKS_DIR.as_posix()}/{spec.file}"


def hook_command(spec: HookSpec) -> dict[str, Any]:
    """Build the exec-form command Claude Code runs for one hook.

    Args:
        spec: The hook to build a command for.

    Returns:
        The command mapping: the launcher plus its arguments, with no shell
        involved (see the module docstring).
    """
    return {
        "type": "command",
        "command": LAUNCHER,
        "args": [*LAUNCHER_ARGS, hook_target(spec)],
    }


def entries_for(specs: Sequence[HookSpec]) -> list[dict[str, Any]]:
    """Build the ``PreToolUse`` entries wiring every spec.

    Specs sharing a matcher are grouped into one entry with several commands,
    in the order given.

    Args:
        specs: The hooks to wire.

    Returns:
        One entry per distinct matcher.
    """
    grouped: dict[str, list[HookSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.matcher, []).append(spec)
    return [
        {"matcher": matcher, "hooks": [hook_command(s) for s in group]}
        for matcher, group in grouped.items()
    ]


def references_any(entry: dict[str, Any], files: frozenset[str]) -> bool:
    """Return whether a ``PreToolUse`` entry points at any of ``files``.

    Scans the command *and* its arguments: the file name lives in ``args`` in
    exec form, but in ``command`` in the shell form earlier templates wrote, and
    both must be recognized so an upgraded project's old wiring is replaced
    rather than duplicated.

    Args:
        entry: A single matcher entry from ``hooks.PreToolUse``.
        files: Hook file names to look for.

    Returns:
        ``True`` if any of the entry's commands names one of the files.
    """
    for hook in entry.get("hooks", []):
        args = hook.get("args")
        parts = [str(hook.get("command", ""))]
        if isinstance(args, list):
            parts.extend(str(arg) for arg in args)
        text = " ".join(parts)
        if any(name in text for name in files):
            return True
    return False


def merge_settings(
    existing: dict[str, Any], entries: Sequence[dict[str, Any]], *, replacing: frozenset[str]
) -> dict[str, Any]:
    """Merge hook entries into a settings mapping, replacing prior wiring.

    Any existing ``PreToolUse`` entry naming a file in ``replacing`` is dropped
    first, so re-running -- or switching shells -- never leaves a duplicate or a
    stale entry behind. Entries for hooks outside ``replacing``, and every
    unrelated settings key, are preserved. An emptied ``PreToolUse``/``hooks``
    is removed rather than left as empty scaffolding.

    Args:
        existing: Parsed contents of ``settings.json`` (``{}`` if absent).
        entries: Entries to install; may be empty to only strip.
        replacing: Hook file names whose existing entries should be dropped.

    Returns:
        The updated settings mapping.
    """
    settings = dict(existing)
    hooks = dict(settings.get("hooks", {}))
    pre = [e for e in hooks.get("PreToolUse", []) if not references_any(e, replacing)]
    pre.extend(entries)
    if pre:
        hooks["PreToolUse"] = pre
    else:
        hooks.pop("PreToolUse", None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return settings


def is_wired(settings: dict[str, Any], spec: HookSpec) -> bool:
    """Return whether a settings mapping already wires one hook.

    Args:
        settings: Parsed contents of ``settings.json``.
        spec: The hook to look for.

    Returns:
        ``True`` if any ``PreToolUse`` entry references the hook's file.
    """
    entries = settings.get("hooks", {}).get("PreToolUse", [])
    return any(references_any(entry, frozenset({spec.file})) for entry in entries)


def read_settings(path: Path) -> dict[str, Any] | None:
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


def write_settings(path: Path, settings: dict[str, Any]) -> None:
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


def run(
    root: Path,
    *,
    title: str,
    enable: Sequence[HookSpec] = (),
    delete: Sequence[HookSpec] = (),
    info: Sequence[tuple[str, str]] = (),
    confirm_prompt: str | None = None,
    assume_yes: bool = False,
    dry_run: bool = False,
) -> int:
    """Wire the hooks in ``enable`` and remove the ones in ``delete``.

    Reads settings first, so an unparseable ``settings.json`` aborts before any
    file is touched. A hook that should be wired but whose file is missing is
    also refused, rather than writing a command pointing at nothing.

    Args:
        root: Project root directory.
        title: Section heading for this step.
        enable: Hooks to keep and wire into settings.
        delete: Hooks to remove, both the file and any wiring naming it.
        info: Extra ``(label, value)`` lines to print above the plan.
        confirm_prompt: Prompt to confirm before applying; ``None`` applies
            without asking (used for removal-only steps, which the caller has
            already decided).
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.

    Returns:
        Process exit code (0 on success; 1 if aborted, a hook file to wire is
        missing, or an existing ``settings.json`` cannot be parsed).
    """
    _common.section(title)

    hooks_dir = root / HOOKS_DIR
    settings_path = root / SETTINGS_PATH

    existing = read_settings(settings_path)
    if existing is None:
        return 1

    missing = [spec.file for spec in enable if not (hooks_dir / spec.file).exists()]
    if missing:
        print(f"  Missing hook file(s): {', '.join(missing)}. Has setup already run?")
        return 1

    touched = frozenset(spec.file for spec in (*enable, *delete))
    merged = merge_settings(existing, entries_for(enable), replacing=touched)
    present_deletes = [spec.file for spec in delete if (hooks_dir / spec.file).exists()]

    for label, value in info:
        _common.info(label, value)
    if enable:
        _common.info("Wire into", str(SETTINGS_PATH))
        print(f"  Keep: {', '.join(spec.file for spec in enable)}")
        for spec in enable:
            print(f"  {spec.summary}")
    else:
        _common.info("Hooks", "off")
    if present_deletes:
        print(f"  Delete: {', '.join(present_deletes)}")
    if not enable and not present_deletes and merged == existing:
        print("  Nothing to change.")

    if dry_run:
        print("\n  (dry run -- nothing changed)")
        return 0

    if confirm_prompt is not None:
        print()
        if not _common.confirm(confirm_prompt, assume_yes=assume_yes):
            print("  Aborted; nothing changed.")
            return 1

    changed = merged != existing
    if changed:
        write_settings(settings_path, merged)
    for spec in delete:
        path = hooks_dir / spec.file
        if path.exists():
            path.unlink()
    if hooks_dir.exists() and not any(hooks_dir.iterdir()):
        hooks_dir.rmdir()

    if enable:
        print(f"\n  Wrote {SETTINGS_PATH}.")
        print("  Restart Claude Code (or run /hooks) so it picks up the new hooks.")
    elif present_deletes or changed:
        print(f"\n  Removed: {title}.")
    return 0


def toggle(
    root: Path,
    spec: HookSpec,
    *,
    install: bool,
    assume_yes: bool = False,
    dry_run: bool = False,
) -> int:
    """Keep and wire one hook, or delete it and strip its wiring.

    Args:
        root: Project root directory.
        spec: The hook to toggle.
        install: ``True`` to wire it in, ``False`` to remove it.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.

    Returns:
        Process exit code, as :func:`run` returns it.
    """
    title = f"Claude Code {spec.title}"
    if install:
        return run(
            root,
            title=title,
            enable=(spec,),
            confirm_prompt=f"Enable the {spec.title}?",
            assume_yes=assume_yes,
            dry_run=dry_run,
        )
    return run(root, title=title, delete=(spec,), assume_yes=assume_yes, dry_run=dry_run)


def main() -> None:
    """Parse arguments and toggle one hook."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--hook", required=True, choices=[spec.key for spec in HOOKS], help="Which hook to toggle."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true", help="Keep the hook and wire it in.")
    group.add_argument("--disable", action="store_true", help="Delete the hook and unwire it.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing."
    )
    args = parser.parse_args()

    root = _common.find_root()
    sys.exit(
        toggle(
            root,
            by_key(args.hook),
            install=args.enable,
            assume_yes=args.yes,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
