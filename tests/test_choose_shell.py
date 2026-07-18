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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "template_setup"))

import choose_shell

_BOTH_KINDS = frozenset({"no_chained_commands", "canonical_commands"})


def _make_project(tmp_path: Path) -> Path:
    """Create a project root with all four hook files present.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        The project root path.
    """
    hooks = tmp_path / choose_shell.HOOKS_DIR
    hooks.mkdir(parents=True)
    for name in choose_shell._ALL_HOOK_FILES:
        (hooks / name).write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.unit
def test_hook_command_uses_project_dir_and_posix_path() -> None:
    """The command resolves from $CLAUDE_PROJECT_DIR with a forward-slash path."""
    command = choose_shell._hook_command("python", "no-chained-commands-pwsh.py")
    assert command == 'python "$CLAUDE_PROJECT_DIR/.claude/hooks/no-chained-commands-pwsh.py"'


@pytest.mark.unit
def test_references_our_hook_detects_template_hooks() -> None:
    """An entry whose command names one of our hook files is recognized."""
    name = next(iter(choose_shell._ALL_HOOK_FILES))
    entry = {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": choose_shell._hook_command("python3", name)}],
    }
    assert choose_shell._references_our_hook(entry) is True


@pytest.mark.unit
def test_references_our_hook_ignores_unrelated_entries() -> None:
    """An unrelated PreToolUse entry is left alone."""
    entry = {"matcher": "Write", "hooks": [{"type": "command", "command": "python other.py"}]}
    assert choose_shell._references_our_hook(entry) is False


@pytest.mark.unit
def test_build_entry_has_one_command_per_kind() -> None:
    """Both kinds enabled builds an entry with one command for each."""
    entry = choose_shell._build_entry("powershell", _BOTH_KINDS)
    assert entry["matcher"] == choose_shell._SHELL_META["powershell"]["matcher"]
    commands = [hook["command"] for hook in entry["hooks"]]
    assert len(commands) == 2
    assert any("no-chained-commands-pwsh.py" in c for c in commands)
    assert any("canonical-commands-pwsh.py" in c for c in commands)


@pytest.mark.unit
def test_build_entry_with_single_kind_has_one_command() -> None:
    """Only the requested kind's file is referenced when the other is declined."""
    entry = choose_shell._build_entry("bash", frozenset({"no_chained_commands"}))
    commands = [hook["command"] for hook in entry["hooks"]]
    assert len(commands) == 1
    assert "no-chained-commands-bash.py" in commands[0]


@pytest.mark.unit
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
    entry = choose_shell._build_entry("bash", _BOTH_KINDS)
    merged = choose_shell._merge_settings(existing, entry)

    assert merged["permissions"] == existing["permissions"]
    matchers = [e["matcher"] for e in merged["hooks"]["PreToolUse"]]
    assert matchers == ["Write", "Bash"]


@pytest.mark.unit
def test_merge_is_idempotent() -> None:
    """Re-merging replaces our prior entry rather than appending a duplicate."""
    entry = choose_shell._build_entry("powershell", _BOTH_KINDS)
    once = choose_shell._merge_settings({}, entry)
    twice = choose_shell._merge_settings(once, entry)
    assert twice["hooks"]["PreToolUse"] == [entry]


@pytest.mark.unit
def test_merge_switching_shells_replaces_entry() -> None:
    """Choosing the other shell drops the previous shell's entry."""
    ps_entry = choose_shell._build_entry("powershell", _BOTH_KINDS)
    bash_entry = choose_shell._build_entry("bash", _BOTH_KINDS)
    merged = choose_shell._merge_settings(choose_shell._merge_settings({}, ps_entry), bash_entry)
    assert merged["hooks"]["PreToolUse"] == [bash_entry]


@pytest.mark.unit
def test_read_settings_missing_returns_empty(tmp_path: Path) -> None:
    """A missing settings file reads as an empty mapping."""
    assert choose_shell._read_settings(tmp_path / "settings.json") == {}


@pytest.mark.unit
def test_read_settings_empty_file_returns_empty(tmp_path: Path) -> None:
    """An empty settings file reads as an empty mapping."""
    path = tmp_path / "settings.json"
    path.write_text("", encoding="utf-8")
    assert choose_shell._read_settings(path) == {}


@pytest.mark.unit
def test_read_settings_invalid_json_returns_none(tmp_path: Path) -> None:
    """Invalid JSON is refused rather than silently discarded."""
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert choose_shell._read_settings(path) is None


@pytest.mark.unit
def test_read_settings_non_object_returns_none(tmp_path: Path) -> None:
    """A JSON array (not an object) is refused."""
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert choose_shell._read_settings(path) is None


@pytest.mark.unit
def test_read_settings_unreadable_returns_none(tmp_path: Path) -> None:
    """A file that is not UTF-8 text is refused."""
    path = tmp_path / "settings.json"
    path.write_bytes(b"\x80\x81")
    assert choose_shell._read_settings(path) is None


@pytest.mark.integration
@pytest.mark.functional
def test_run_invalid_shell_returns_one(tmp_path: Path) -> None:
    """An unrecognized shell name is rejected without writing anything."""
    root = _make_project(tmp_path)
    assert (
        choose_shell.run(
            root, "fish", no_chained_commands=True, canonical_commands=True, assume_yes=True
        )
        == 1
    )
    assert not (root / choose_shell.SETTINGS_PATH).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_run_missing_hook_files_returns_one(tmp_path: Path) -> None:
    """A shell whose hook files are absent is rejected."""
    root = tmp_path
    (root / choose_shell.HOOKS_DIR).mkdir(parents=True)
    assert (
        choose_shell.run(
            root, "bash", no_chained_commands=True, canonical_commands=True, assume_yes=True
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.functional
def test_run_invalid_settings_aborts_without_changes(tmp_path: Path) -> None:
    """An unparseable settings.json aborts the step before any file changes."""
    root = _make_project(tmp_path)
    settings_path = root / choose_shell.SETTINGS_PATH
    settings_path.write_text("{not json", encoding="utf-8")

    assert (
        choose_shell.run(
            root, "powershell", no_chained_commands=True, canonical_commands=True, assume_yes=True
        )
        == 1
    )

    assert settings_path.read_text(encoding="utf-8") == "{not json"
    hooks = root / choose_shell.HOOKS_DIR
    for name in choose_shell._ALL_HOOK_FILES:
        assert (hooks / name).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_run_dry_run_changes_nothing(tmp_path: Path) -> None:
    """A dry run neither writes settings nor deletes the other shell's hooks."""
    root = _make_project(tmp_path)
    assert (
        choose_shell.run(
            root,
            "powershell",
            no_chained_commands=True,
            canonical_commands=True,
            assume_yes=True,
            dry_run=True,
        )
        == 0
    )
    assert not (root / choose_shell.SETTINGS_PATH).exists()
    hooks = root / choose_shell.HOOKS_DIR
    assert (hooks / "canonical-commands-bash.py").exists()


@pytest.mark.integration
@pytest.mark.functional
def test_run_writes_settings_and_deletes_unused(tmp_path: Path) -> None:
    """A real run wires the chosen hooks and removes the other shell's files."""
    root = _make_project(tmp_path)
    assert (
        choose_shell.run(
            root, "powershell", no_chained_commands=True, canonical_commands=True, assume_yes=True
        )
        == 0
    )

    settings = json.loads((root / choose_shell.SETTINGS_PATH).read_text(encoding="utf-8"))
    entry = settings["hooks"]["PreToolUse"][-1]
    assert entry["matcher"] == "Bash|PowerShell"

    hooks = root / choose_shell.HOOKS_DIR
    assert (hooks / "canonical-commands-pwsh.py").exists()
    assert not (hooks / "canonical-commands-bash.py").exists()
    assert not (hooks / "no-chained-commands-bash.py").exists()


@pytest.mark.integration
@pytest.mark.functional
def test_run_one_kind_only_keeps_only_that_file(tmp_path: Path) -> None:
    """Declining one kind removes it even for the chosen shell; the other stays."""
    root = _make_project(tmp_path)
    assert (
        choose_shell.run(
            root, "bash", no_chained_commands=True, canonical_commands=False, assume_yes=True
        )
        == 0
    )

    hooks = root / choose_shell.HOOKS_DIR
    assert (hooks / "no-chained-commands-bash.py").exists()
    assert not (hooks / "canonical-commands-bash.py").exists()
    assert not (hooks / "no-chained-commands-pwsh.py").exists()
    assert not (hooks / "canonical-commands-pwsh.py").exists()

    settings = json.loads((root / choose_shell.SETTINGS_PATH).read_text(encoding="utf-8"))
    entry = settings["hooks"]["PreToolUse"][-1]
    commands = [hook["command"] for hook in entry["hooks"]]
    assert len(commands) == 1
    assert "no-chained-commands-bash.py" in commands[0]


@pytest.mark.integration
@pytest.mark.functional
def test_run_decline_removes_all_hooks(tmp_path: Path) -> None:
    """Declining both kinds deletes every hook file and writes no settings."""
    root = _make_project(tmp_path)
    assert (
        choose_shell.run(root, no_chained_commands=False, canonical_commands=False, assume_yes=True)
        == 0
    )

    assert not (root / choose_shell.SETTINGS_PATH).exists()
    hooks = root / choose_shell.HOOKS_DIR
    assert not hooks.exists()  # the emptied directory is removed too


@pytest.mark.integration
@pytest.mark.functional
def test_run_decline_dry_run_keeps_hooks(tmp_path: Path) -> None:
    """A declined dry run reports the removal but deletes nothing."""
    root = _make_project(tmp_path)
    assert (
        choose_shell.run(
            root,
            no_chained_commands=False,
            canonical_commands=False,
            assume_yes=True,
            dry_run=True,
        )
        == 0
    )

    hooks = root / choose_shell.HOOKS_DIR
    for name in choose_shell._ALL_HOOK_FILES:
        assert (hooks / name).exists()


@pytest.mark.unit
def test_remove_all_hooks_returns_removed_names(tmp_path: Path) -> None:
    """The remover deletes present hook files and reports them sorted."""
    root = _make_project(tmp_path)
    hooks = root / choose_shell.HOOKS_DIR
    removed = choose_shell._remove_all_hooks(hooks)
    assert removed == sorted(choose_shell._ALL_HOOK_FILES)
    assert not hooks.exists()
