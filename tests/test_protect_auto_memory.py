"""Unit tests for the pure helpers in scripts/template_setup/protect_auto_memory.py.

The template_setup folder is not a package, so the module is imported by
adding the folder to sys.path, mirroring how the setup scripts import their
shared _common module.

This file is itself a dev-script test: cleanup.py matches it to
scripts/template_setup/protect_auto_memory.py and deletes it along with the rest
of the scaffolding, so it never lingers in a project started from the template.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "template_setup"))

# mypy cannot see the sys.path insertion above, so it cannot resolve the module.
import protect_auto_memory  # type: ignore[import-not-found]


def _make_project(tmp_path: Path) -> Path:
    """Create a project root with the memory-guard hook file present.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        The project root path.
    """
    hooks = tmp_path / protect_auto_memory.HOOKS_DIR
    hooks.mkdir(parents=True)
    (hooks / protect_auto_memory.HOOK_FILE).write_text("", encoding="utf-8")
    return tmp_path


def test_hook_command_uses_project_dir_and_posix_path() -> None:
    """The command resolves from $CLAUDE_PROJECT_DIR with a forward-slash path."""
    command = protect_auto_memory._hook_command()
    assert command == 'python "$CLAUDE_PROJECT_DIR/.claude/hooks/protect-auto-memory.py"'


def test_references_our_hook_detects_template_hook() -> None:
    """An entry whose command names the hook file is recognized."""
    entry = protect_auto_memory._build_entry()
    assert protect_auto_memory._references_our_hook(entry) is True


def test_references_our_hook_ignores_unrelated_entries() -> None:
    """An unrelated PreToolUse entry is left alone."""
    entry = {"matcher": "Write", "hooks": [{"type": "command", "command": "python other.py"}]}
    assert protect_auto_memory._references_our_hook(entry) is False


def test_build_entry_has_matcher_and_one_command() -> None:
    """The built entry carries the Write|Edit matcher and a single command."""
    entry = protect_auto_memory._build_entry()
    assert entry["matcher"] == protect_auto_memory.MATCHER == "Write|Edit"
    assert len(entry["hooks"]) == 1
    assert protect_auto_memory.HOOK_FILE in entry["hooks"][0]["command"]


def test_merge_preserves_unrelated_keys_and_entries() -> None:
    """Merging keeps permissions and unrelated PreToolUse entries intact."""
    existing = {
        "permissions": {"allow": ["Bash(git status)"]},
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "python other.py"}]}
            ]
        },
    }
    merged = protect_auto_memory._merge_settings(existing)

    assert merged["permissions"] == existing["permissions"]
    matchers = [e["matcher"] for e in merged["hooks"]["PreToolUse"]]
    assert matchers == ["Bash", "Write|Edit"]


def test_merge_is_idempotent() -> None:
    """Re-merging replaces our prior entry rather than appending a duplicate."""
    once = protect_auto_memory._merge_settings({})
    twice = protect_auto_memory._merge_settings(once)
    assert twice["hooks"]["PreToolUse"] == [protect_auto_memory._build_entry()]


def test_without_our_entry_strips_ours_and_drops_empty_hooks() -> None:
    """Removing the only entry leaves no empty hooks/PreToolUse scaffolding."""
    wired = protect_auto_memory._merge_settings({})
    cleaned = protect_auto_memory._without_our_entry(wired)
    assert cleaned == {}


def test_without_our_entry_keeps_unrelated_entries() -> None:
    """Stripping our entry preserves other PreToolUse entries and keys."""
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python other.py"}]}
    existing = {"model": "opus", "hooks": {"PreToolUse": [other]}}
    wired = protect_auto_memory._merge_settings(existing)
    cleaned = protect_auto_memory._without_our_entry(wired)
    assert cleaned == existing


def test_is_wired_reflects_presence_of_entry() -> None:
    """_is_wired is True only once our entry has been merged in."""
    assert protect_auto_memory._is_wired({}) is False
    assert protect_auto_memory._is_wired(protect_auto_memory._merge_settings({})) is True


def test_read_settings_missing_returns_empty(tmp_path: Path) -> None:
    """A missing settings file reads as an empty mapping."""
    assert protect_auto_memory._read_settings(tmp_path / "settings.json") == {}


def test_read_settings_empty_file_returns_empty(tmp_path: Path) -> None:
    """An empty settings file reads as an empty mapping."""
    path = tmp_path / "settings.json"
    path.write_text("", encoding="utf-8")
    assert protect_auto_memory._read_settings(path) == {}


def test_read_settings_invalid_json_returns_none(tmp_path: Path) -> None:
    """Invalid JSON is refused rather than silently discarded."""
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert protect_auto_memory._read_settings(path) is None


def test_read_settings_non_object_returns_none(tmp_path: Path) -> None:
    """A JSON array (not an object) is refused."""
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert protect_auto_memory._read_settings(path) is None


def test_read_settings_unreadable_returns_none(tmp_path: Path) -> None:
    """A file that is not UTF-8 text is refused."""
    path = tmp_path / "settings.json"
    path.write_bytes(b"\x80\x81")
    assert protect_auto_memory._read_settings(path) is None


def test_write_settings_deletes_file_when_empty(tmp_path: Path) -> None:
    """Writing an empty mapping removes the file rather than leaving '{}'."""
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    protect_auto_memory._write_settings(path, {})
    assert not path.exists()


def test_run_enable_writes_settings_and_keeps_hook(tmp_path: Path) -> None:
    """Enabling wires the hook into settings and leaves the hook file in place."""
    root = _make_project(tmp_path)
    assert protect_auto_memory.run(root, install=True, assume_yes=True) == 0

    settings_path = root / protect_auto_memory.SETTINGS_PATH
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert protect_auto_memory._is_wired(settings)
    assert (root / protect_auto_memory.HOOKS_DIR / protect_auto_memory.HOOK_FILE).exists()


def test_run_enable_missing_hook_returns_one(tmp_path: Path) -> None:
    """Enabling without the hook file present is rejected and writes nothing."""
    (tmp_path / protect_auto_memory.HOOKS_DIR).mkdir(parents=True)
    assert protect_auto_memory.run(tmp_path, install=True, assume_yes=True) == 1
    assert not (tmp_path / protect_auto_memory.SETTINGS_PATH).exists()


def test_run_enable_invalid_settings_aborts_without_changes(tmp_path: Path) -> None:
    """Enabling against an unparseable settings.json aborts and changes nothing."""
    root = _make_project(tmp_path)
    settings_path = root / protect_auto_memory.SETTINGS_PATH
    settings_path.write_text("{not json", encoding="utf-8")

    assert protect_auto_memory.run(root, install=True, assume_yes=True) == 1
    assert settings_path.read_text(encoding="utf-8") == "{not json"


def test_run_disable_invalid_settings_aborts_without_changes(tmp_path: Path) -> None:
    """Declining against an unparseable settings.json keeps the hook file too."""
    root = _make_project(tmp_path)
    settings_path = root / protect_auto_memory.SETTINGS_PATH
    settings_path.write_text("{not json", encoding="utf-8")

    assert protect_auto_memory.run(root, install=False, assume_yes=True) == 1
    assert settings_path.read_text(encoding="utf-8") == "{not json"
    assert (root / protect_auto_memory.HOOKS_DIR / protect_auto_memory.HOOK_FILE).exists()


def test_run_enable_dry_run_changes_nothing(tmp_path: Path) -> None:
    """A dry run neither writes settings nor removes the hook file."""
    root = _make_project(tmp_path)
    assert protect_auto_memory.run(root, install=True, assume_yes=True, dry_run=True) == 0
    assert not (root / protect_auto_memory.SETTINGS_PATH).exists()
    assert (root / protect_auto_memory.HOOKS_DIR / protect_auto_memory.HOOK_FILE).exists()


def test_run_disable_removes_hook_and_writes_no_settings(tmp_path: Path) -> None:
    """Declining deletes the hook file and writes no settings when none existed."""
    root = _make_project(tmp_path)
    assert protect_auto_memory.run(root, install=False, assume_yes=True) == 0

    assert not (root / protect_auto_memory.HOOKS_DIR / protect_auto_memory.HOOK_FILE).exists()
    assert not (root / protect_auto_memory.SETTINGS_PATH).exists()


def test_run_disable_unwires_existing_entry(tmp_path: Path) -> None:
    """Declining after a prior enable removes the wiring and the hook file."""
    root = _make_project(tmp_path)
    assert protect_auto_memory.run(root, install=True, assume_yes=True) == 0
    assert protect_auto_memory.run(root, install=False, assume_yes=True) == 0

    settings_path = root / protect_auto_memory.SETTINGS_PATH
    # The only entry was ours, so the now-empty settings file is removed entirely.
    assert not settings_path.exists()
    assert not (root / protect_auto_memory.HOOKS_DIR / protect_auto_memory.HOOK_FILE).exists()


def test_run_disable_dry_run_keeps_everything(tmp_path: Path) -> None:
    """A declined dry run reports the removal but deletes nothing."""
    root = _make_project(tmp_path)
    protect_auto_memory.run(root, install=True, assume_yes=True)
    assert protect_auto_memory.run(root, install=False, assume_yes=True, dry_run=True) == 0

    assert (root / protect_auto_memory.HOOKS_DIR / protect_auto_memory.HOOK_FILE).exists()
    assert (root / protect_auto_memory.SETTINGS_PATH).exists()
