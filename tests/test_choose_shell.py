"""Unit tests for the pure helpers in scripts/template_setup/choose_shell.py.

The template_setup folder is not a package, so the module is imported by
adding the folder to sys.path, mirroring how the setup scripts import their
shared _common module.

This file is itself a dev-script test: cleanup.py matches it to
scripts/template_setup/choose_shell.py and deletes it along with the rest of
the scaffolding, so it never lingers in a project started from the template.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "template_setup"))

# mypy cannot see the sys.path insertion above, so it cannot resolve the module.
import choose_shell  # type: ignore[import-not-found]


def _make_project(tmp_path: Path) -> Path:
    """Create a project root with all four hook files present.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        The project root path.
    """
    hooks = tmp_path / choose_shell.HOOKS_DIR
    hooks.mkdir(parents=True)
    for spec in choose_shell.SHELLS.values():
        for name in spec["hooks"]:
            (hooks / name).write_text("", encoding="utf-8")
    return tmp_path


def test_hook_command_uses_project_dir_and_posix_path() -> None:
    """The command resolves from $CLAUDE_PROJECT_DIR with a forward-slash path."""
    command = choose_shell._hook_command("python", "no-chained-commands-pwsh.py")
    assert command == 'python "$CLAUDE_PROJECT_DIR/.claude/hooks/no-chained-commands-pwsh.py"'


def test_references_our_hook_detects_template_hooks() -> None:
    """An entry whose command names one of our hook files is recognized."""
    name = next(iter(choose_shell._ALL_HOOK_FILES))
    entry = {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": choose_shell._hook_command("python3", name)}],
    }
    assert choose_shell._references_our_hook(entry) is True


def test_references_our_hook_ignores_unrelated_entries() -> None:
    """An unrelated PreToolUse entry is left alone."""
    entry = {"matcher": "Write", "hooks": [{"type": "command", "command": "python other.py"}]}
    assert choose_shell._references_our_hook(entry) is False


def test_build_entry_has_one_command_per_hook() -> None:
    """The built entry carries the matcher and one command per chosen hook."""
    spec = choose_shell.SHELLS["powershell"]
    entry = choose_shell._build_entry(spec)
    assert entry["matcher"] == spec["matcher"]
    commands = [hook["command"] for hook in entry["hooks"]]
    assert len(commands) == len(spec["hooks"])
    assert all(name in command for name, command in zip(spec["hooks"], commands, strict=True))


def test_merge_preserves_unrelated_keys_and_entries() -> None:
    """Merging keeps permissions and unrelated PreToolUse entries intact."""
    existing = {
        "permissions": {"allow": ["Bash(git status)"]},
        "hooks": {
            "PreToolUse": [
                {"matcher": "Write", "hooks": [{"type": "command", "command": "python other.py"}]}
            ]
        },
    }
    entry = choose_shell._build_entry(choose_shell.SHELLS["bash"])
    merged = choose_shell._merge_settings(existing, entry)

    assert merged["permissions"] == existing["permissions"]
    matchers = [e["matcher"] for e in merged["hooks"]["PreToolUse"]]
    assert matchers == ["Write", "Bash"]


def test_merge_is_idempotent() -> None:
    """Re-merging replaces our prior entry rather than appending a duplicate."""
    entry = choose_shell._build_entry(choose_shell.SHELLS["powershell"])
    once = choose_shell._merge_settings({}, entry)
    twice = choose_shell._merge_settings(once, entry)
    assert twice["hooks"]["PreToolUse"] == [entry]


def test_merge_switching_shells_replaces_entry() -> None:
    """Choosing the other shell drops the previous shell's entry."""
    ps_entry = choose_shell._build_entry(choose_shell.SHELLS["powershell"])
    bash_entry = choose_shell._build_entry(choose_shell.SHELLS["bash"])
    merged = choose_shell._merge_settings(choose_shell._merge_settings({}, ps_entry), bash_entry)
    assert merged["hooks"]["PreToolUse"] == [bash_entry]


def test_read_settings_missing_returns_empty(tmp_path: Path) -> None:
    """A missing settings file reads as an empty mapping."""
    assert choose_shell._read_settings(tmp_path / "settings.json") == {}


def test_read_settings_invalid_json_returns_empty(tmp_path: Path) -> None:
    """Invalid JSON reads as empty rather than raising."""
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert choose_shell._read_settings(path) == {}


def test_read_settings_non_object_returns_empty(tmp_path: Path) -> None:
    """A JSON array (not an object) reads as empty."""
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert choose_shell._read_settings(path) == {}


def test_run_invalid_shell_returns_one(tmp_path: Path) -> None:
    """An unrecognized shell name is rejected without writing anything."""
    root = _make_project(tmp_path)
    assert choose_shell.run(root, "fish", install=True, assume_yes=True) == 1
    assert not (root / choose_shell.SETTINGS_PATH).exists()


def test_run_missing_hook_files_returns_one(tmp_path: Path) -> None:
    """A shell whose hook files are absent is rejected."""
    root = tmp_path
    (root / choose_shell.HOOKS_DIR).mkdir(parents=True)
    assert choose_shell.run(root, "bash", install=True, assume_yes=True) == 1


def test_run_dry_run_changes_nothing(tmp_path: Path) -> None:
    """A dry run neither writes settings nor deletes the other shell's hooks."""
    root = _make_project(tmp_path)
    assert choose_shell.run(root, "powershell", install=True, assume_yes=True, dry_run=True) == 0
    assert not (root / choose_shell.SETTINGS_PATH).exists()
    hooks = root / choose_shell.HOOKS_DIR
    assert (hooks / "canonical-commands-bash.py").exists()


def test_run_writes_settings_and_deletes_unused(tmp_path: Path) -> None:
    """A real run wires the chosen hooks and removes the other shell's files."""
    root = _make_project(tmp_path)
    assert choose_shell.run(root, "powershell", install=True, assume_yes=True) == 0

    settings = json.loads((root / choose_shell.SETTINGS_PATH).read_text(encoding="utf-8"))
    entry = settings["hooks"]["PreToolUse"][-1]
    assert entry["matcher"] == "Bash|PowerShell"

    hooks = root / choose_shell.HOOKS_DIR
    assert (hooks / "canonical-commands-pwsh.py").exists()
    assert not (hooks / "canonical-commands-bash.py").exists()
    assert not (hooks / "no-chained-commands-bash.py").exists()


def test_run_decline_removes_all_hooks(tmp_path: Path) -> None:
    """Declining the hooks deletes every hook file and writes no settings."""
    root = _make_project(tmp_path)
    assert choose_shell.run(root, install=False, assume_yes=True) == 0

    assert not (root / choose_shell.SETTINGS_PATH).exists()
    hooks = root / choose_shell.HOOKS_DIR
    assert not hooks.exists()  # the emptied directory is removed too


def test_run_decline_dry_run_keeps_hooks(tmp_path: Path) -> None:
    """A declined dry run reports the removal but deletes nothing."""
    root = _make_project(tmp_path)
    assert choose_shell.run(root, install=False, assume_yes=True, dry_run=True) == 0

    hooks = root / choose_shell.HOOKS_DIR
    for name in choose_shell._ALL_HOOK_FILES:
        assert (hooks / name).exists()


def test_remove_all_hooks_returns_removed_names(tmp_path: Path) -> None:
    """The remover deletes present hook files and reports them sorted."""
    root = _make_project(tmp_path)
    hooks = root / choose_shell.HOOKS_DIR
    removed = choose_shell._remove_all_hooks(hooks)
    assert removed == sorted(choose_shell._ALL_HOOK_FILES)
    assert not hooks.exists()
