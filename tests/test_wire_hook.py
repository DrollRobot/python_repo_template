"""Unit tests for scripts/template_setup/wire_hook.py.

wire_hook is the single engine behind every hook step: it owns the HOOKS
registry and .claude/settings.json. These cover the registry's integrity, the
pure settings helpers (build/merge/strip), and run()/toggle() end to end
against a temporary project.

The template_setup folder is not a package, so the module is imported by
adding the folder to sys.path, mirroring how the setup scripts import their
shared _common module.

This file is itself a dev-script test: cleanup.py matches it to
scripts/template_setup/wire_hook.py and deletes it along with the rest of the
scaffolding, so it never lingers in a project started from the template.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "template_setup"))

import wire_hook

REPO_ROOT = Path(__file__).resolve().parent.parent

MEMORY = wire_hook.by_key("auto_memory_guard")
SECRETS = wire_hook.by_key("no_inline_secrets")
CHAINED_PWSH = wire_hook.by_key("no_chained_commands_pwsh")
CANONICAL_PWSH = wire_hook.by_key("canonical_commands_pwsh")
CHAINED_BASH = wire_hook.by_key("no_chained_commands_bash")


def _make_project(tmp_path: Path, *specs: wire_hook.HookSpec) -> Path:
    """Create a project root with the given hook files present.

    Args:
        tmp_path: pytest temporary directory.
        specs: Hooks whose files should exist; all of them when none is given.

    Returns:
        The project root path.
    """
    hooks = tmp_path / wire_hook.HOOKS_DIR
    hooks.mkdir(parents=True)
    for spec in specs or wire_hook.HOOKS:
        (hooks / spec.file).write_text("", encoding="utf-8")
    return tmp_path


def _settings(root: Path) -> dict[str, Any]:
    """Read and parse the project's settings.json."""
    data: dict[str, Any] = json.loads((root / wire_hook.SETTINGS_PATH).read_text(encoding="utf-8"))
    return data


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_registry_keys_and_files_are_unique() -> None:
    assert len({spec.key for spec in wire_hook.HOOKS}) == len(wire_hook.HOOKS)
    assert len({spec.file for spec in wire_hook.HOOKS}) == len(wire_hook.HOOKS)


@pytest.mark.integration
def test_every_registered_hook_file_ships_with_the_template() -> None:
    # A typo in a spec's file name would wire a command pointing at nothing.
    for spec in wire_hook.HOOKS:
        assert (REPO_ROOT / wire_hook.HOOKS_DIR / spec.file).is_file(), spec.key


@pytest.mark.unit
def test_by_key_raises_for_unknown_hook() -> None:
    with pytest.raises(KeyError):
        wire_hook.by_key("no-such-hook")


@pytest.mark.unit
def test_every_hook_is_launched_the_same_portable_way() -> None:
    # settings.json is committed and read on every teammate's OS, so no hook
    # may carry a platform-specific interpreter name.
    for spec in wire_hook.HOOKS:
        command = wire_hook.hook_command(spec)
        assert command["command"] == "uv"
        assert command["args"][:2] == ["run", "--no-project"]


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hook_target_uses_the_placeholder_and_a_posix_path() -> None:
    # ${CLAUDE_PROJECT_DIR} is substituted by Claude Code, not by a shell, so
    # it must be the braced placeholder rather than a $VAR reference.
    assert wire_hook.hook_target(MEMORY) == (
        "${CLAUDE_PROJECT_DIR}/.claude/hooks/protect-auto-memory.py"
    )


@pytest.mark.unit
def test_hook_command_is_exec_form() -> None:
    # Exec form means no shell parses this, so nothing depends on whether the
    # host resolves the command through sh, Git Bash, or PowerShell.
    assert wire_hook.hook_command(MEMORY) == {
        "type": "command",
        "command": "uv",
        "args": ["run", "--no-project", wire_hook.hook_target(MEMORY)],
    }


@pytest.mark.unit
def test_entries_group_specs_sharing_a_matcher() -> None:
    # The shell pair has always been written as one entry with two commands.
    entries = wire_hook.entries_for([CHAINED_PWSH, CANONICAL_PWSH])
    assert len(entries) == 1
    assert entries[0]["matcher"] == "Bash|PowerShell"
    targets = [hook["args"][-1] for hook in entries[0]["hooks"]]
    assert len(targets) == 2
    assert "no-chained-commands-pwsh.py" in targets[0]
    assert "canonical-commands-pwsh.py" in targets[1]


@pytest.mark.unit
def test_entries_split_specs_with_different_matchers() -> None:
    entries = wire_hook.entries_for([MEMORY, SECRETS])
    assert [e["matcher"] for e in entries] == [MEMORY.matcher, SECRETS.matcher]


@pytest.mark.unit
def test_entries_for_nothing_is_empty() -> None:
    assert wire_hook.entries_for([]) == []


@pytest.mark.unit
def test_references_any_matches_only_named_files() -> None:
    entry = wire_hook.entries_for([MEMORY])[0]
    assert wire_hook.references_any(entry, frozenset({MEMORY.file})) is True
    assert wire_hook.references_any(entry, frozenset({SECRETS.file})) is False


@pytest.mark.unit
def test_references_any_recognizes_legacy_shell_form() -> None:
    # Earlier templates wrote the path into `command` with no `args`. An
    # upgraded project's old entry must still be found, or re-wiring would
    # leave a duplicate pointing at the same hook.
    legacy = {
        "matcher": "Write|Edit",
        "hooks": [
            {
                "type": "command",
                "command": f'python "$CLAUDE_PROJECT_DIR/.claude/hooks/{MEMORY.file}"',
            }
        ],
    }
    assert wire_hook.references_any(legacy, frozenset({MEMORY.file})) is True


@pytest.mark.unit
def test_merge_replaces_legacy_shell_form_entry() -> None:
    legacy = {
        "matcher": "Write|Edit",
        "hooks": [{"type": "command", "command": f"python .claude/hooks/{MEMORY.file}"}],
    }
    merged = wire_hook.merge_settings(
        {"hooks": {"PreToolUse": [legacy]}},
        wire_hook.entries_for([MEMORY]),
        replacing=frozenset({MEMORY.file}),
    )
    assert merged["hooks"]["PreToolUse"] == wire_hook.entries_for([MEMORY])


@pytest.mark.unit
def test_references_any_tolerates_a_malformed_args_value() -> None:
    entry = {"matcher": "Write", "hooks": [{"command": "uv", "args": "not-a-list"}]}
    assert wire_hook.references_any(entry, frozenset({MEMORY.file})) is False


@pytest.mark.unit
def test_merge_preserves_unrelated_keys_and_entries() -> None:
    existing = {
        "permissions": {"allow": ["Bash(git status)"]},
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "python other.py"}]}
            ]
        },
    }
    merged = wire_hook.merge_settings(
        existing, wire_hook.entries_for([MEMORY]), replacing=frozenset({MEMORY.file})
    )
    assert merged["permissions"] == existing["permissions"]
    assert [e["matcher"] for e in merged["hooks"]["PreToolUse"]] == ["Bash", MEMORY.matcher]


@pytest.mark.unit
def test_merge_is_idempotent() -> None:
    entries = wire_hook.entries_for([MEMORY])
    files = frozenset({MEMORY.file})
    once = wire_hook.merge_settings({}, entries, replacing=files)
    twice = wire_hook.merge_settings(once, entries, replacing=files)
    assert twice["hooks"]["PreToolUse"] == entries


@pytest.mark.unit
def test_merge_replaces_only_the_hooks_named_in_this_call() -> None:
    # Re-wiring one guard must not disturb another's entry.
    settings = wire_hook.merge_settings(
        {}, wire_hook.entries_for([MEMORY]), replacing=frozenset({MEMORY.file})
    )
    settings = wire_hook.merge_settings(
        settings, wire_hook.entries_for([SECRETS]), replacing=frozenset({SECRETS.file})
    )
    assert wire_hook.is_wired(settings, MEMORY) is True
    assert wire_hook.is_wired(settings, SECRETS) is True


@pytest.mark.unit
def test_merge_switching_shells_replaces_the_entry() -> None:
    pwsh = [CHAINED_PWSH, CANONICAL_PWSH]
    bash = [CHAINED_BASH, wire_hook.by_key("canonical_commands_bash")]
    all_files = frozenset(spec.file for spec in (*pwsh, *bash))
    settings = wire_hook.merge_settings({}, wire_hook.entries_for(pwsh), replacing=all_files)
    settings = wire_hook.merge_settings(settings, wire_hook.entries_for(bash), replacing=all_files)
    assert settings["hooks"]["PreToolUse"] == wire_hook.entries_for(bash)


@pytest.mark.unit
def test_merge_with_no_entries_strips_and_drops_empty_scaffolding() -> None:
    wired = wire_hook.merge_settings(
        {}, wire_hook.entries_for([MEMORY]), replacing=frozenset({MEMORY.file})
    )
    assert wire_hook.merge_settings(wired, [], replacing=frozenset({MEMORY.file})) == {}


@pytest.mark.unit
def test_is_wired_reflects_presence_of_entry() -> None:
    assert wire_hook.is_wired({}, MEMORY) is False
    wired = wire_hook.merge_settings(
        {}, wire_hook.entries_for([MEMORY]), replacing=frozenset({MEMORY.file})
    )
    assert wire_hook.is_wired(wired, MEMORY) is True


@pytest.mark.unit
def test_read_settings_missing_returns_empty(tmp_path: Path) -> None:
    assert wire_hook.read_settings(tmp_path / "settings.json") == {}


@pytest.mark.unit
def test_read_settings_empty_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("", encoding="utf-8")
    assert wire_hook.read_settings(path) == {}


@pytest.mark.unit
@pytest.mark.parametrize("body", ["{not json", "[1, 2, 3]"])
def test_read_settings_invalid_content_returns_none(tmp_path: Path, body: str) -> None:
    path = tmp_path / "settings.json"
    path.write_text(body, encoding="utf-8")
    assert wire_hook.read_settings(path) is None


@pytest.mark.unit
def test_read_settings_unreadable_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(b"\x80\x81")
    assert wire_hook.read_settings(path) is None


@pytest.mark.unit
def test_write_settings_deletes_file_when_empty(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    wire_hook.write_settings(path, {})
    assert not path.exists()


# ---------------------------------------------------------------------------
# toggle: enable
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.functional
def test_toggle_enable_writes_settings_and_keeps_hook(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    assert wire_hook.toggle(root, MEMORY, install=True, assume_yes=True) == 0
    assert wire_hook.is_wired(_settings(root), MEMORY)
    assert (root / wire_hook.HOOKS_DIR / MEMORY.file).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_toggle_enable_missing_hook_returns_one(tmp_path: Path) -> None:
    (tmp_path / wire_hook.HOOKS_DIR).mkdir(parents=True)
    assert wire_hook.toggle(tmp_path, MEMORY, install=True, assume_yes=True) == 1
    assert not (tmp_path / wire_hook.SETTINGS_PATH).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_toggle_enable_invalid_settings_aborts_without_changes(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    settings_path = root / wire_hook.SETTINGS_PATH
    settings_path.write_text("{not json", encoding="utf-8")

    assert wire_hook.toggle(root, MEMORY, install=True, assume_yes=True) == 1
    assert settings_path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.integration
@pytest.mark.functional
def test_toggle_enable_dry_run_changes_nothing(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    assert wire_hook.toggle(root, MEMORY, install=True, assume_yes=True, dry_run=True) == 0
    assert not (root / wire_hook.SETTINGS_PATH).exists()
    assert (root / wire_hook.HOOKS_DIR / MEMORY.file).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_two_guards_coexist(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    assert wire_hook.toggle(root, MEMORY, install=True, assume_yes=True) == 0
    assert wire_hook.toggle(root, SECRETS, install=True, assume_yes=True) == 0

    settings = _settings(root)
    assert wire_hook.is_wired(settings, MEMORY)
    assert wire_hook.is_wired(settings, SECRETS)


# ---------------------------------------------------------------------------
# toggle: disable
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.functional
def test_toggle_disable_removes_hook_and_writes_no_settings(tmp_path: Path) -> None:
    root = _make_project(tmp_path, MEMORY)
    assert wire_hook.toggle(root, MEMORY, install=False, assume_yes=True) == 0
    assert not (root / wire_hook.HOOKS_DIR / MEMORY.file).exists()
    assert not (root / wire_hook.SETTINGS_PATH).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_toggle_disable_unwires_existing_entry_and_spares_the_other(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    wire_hook.toggle(root, MEMORY, install=True, assume_yes=True)
    wire_hook.toggle(root, SECRETS, install=True, assume_yes=True)
    assert wire_hook.toggle(root, MEMORY, install=False, assume_yes=True) == 0

    settings = _settings(root)
    assert wire_hook.is_wired(settings, MEMORY) is False
    assert wire_hook.is_wired(settings, SECRETS) is True
    assert not (root / wire_hook.HOOKS_DIR / MEMORY.file).exists()
    assert (root / wire_hook.HOOKS_DIR / SECRETS.file).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_toggle_disable_invalid_settings_aborts_without_changes(tmp_path: Path) -> None:
    root = _make_project(tmp_path, MEMORY)
    settings_path = root / wire_hook.SETTINGS_PATH
    settings_path.write_text("{not json", encoding="utf-8")

    assert wire_hook.toggle(root, MEMORY, install=False, assume_yes=True) == 1
    assert settings_path.read_text(encoding="utf-8") == "{not json"
    assert (root / wire_hook.HOOKS_DIR / MEMORY.file).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_toggle_disable_dry_run_keeps_everything(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    wire_hook.toggle(root, MEMORY, install=True, assume_yes=True)
    assert wire_hook.toggle(root, MEMORY, install=False, assume_yes=True, dry_run=True) == 0
    assert (root / wire_hook.HOOKS_DIR / MEMORY.file).exists()
    assert (root / wire_hook.SETTINGS_PATH).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_disable_leaves_unrelated_settings_untouched(tmp_path: Path) -> None:
    root = _make_project(tmp_path, MEMORY)
    settings_path = root / wire_hook.SETTINGS_PATH
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    original = '{\n  "permissions": {\n    "deny": [\n      "Edit(uv.lock)"\n    ]\n  }\n}\n'
    settings_path.write_text(original, encoding="utf-8")

    assert wire_hook.toggle(root, MEMORY, install=False, assume_yes=True) == 0
    # Nothing to strip, so the file is not even rewritten.
    assert settings_path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# run: mixed enable/delete, as choose_shell uses it
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.functional
def test_run_enables_and_deletes_in_one_pass(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    assert (
        wire_hook.run(
            root,
            title="hooks",
            enable=(CHAINED_PWSH, CANONICAL_PWSH),
            delete=(CHAINED_BASH, wire_hook.by_key("canonical_commands_bash")),
            confirm_prompt="Apply?",
            assume_yes=True,
        )
        == 0
    )

    hooks = root / wire_hook.HOOKS_DIR
    assert (hooks / CHAINED_PWSH.file).exists()
    assert not (hooks / CHAINED_BASH.file).exists()
    entry = _settings(root)["hooks"]["PreToolUse"][-1]
    assert entry["matcher"] == "Bash|PowerShell"
    assert len(entry["hooks"]) == 2


@pytest.mark.integration
@pytest.mark.functional
def test_run_removes_emptied_hooks_directory(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    assert wire_hook.run(root, title="hooks", delete=wire_hook.HOOKS, assume_yes=True) == 0
    assert not (root / wire_hook.HOOKS_DIR).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_run_declining_strips_stale_wiring(tmp_path: Path) -> None:
    # Enabling then declining must not leave an entry pointing at a deleted file.
    root = _make_project(tmp_path)
    wire_hook.toggle(root, CHAINED_PWSH, install=True, assume_yes=True)
    assert wire_hook.run(root, title="hooks", delete=(CHAINED_PWSH,), assume_yes=True) == 0
    assert not (root / wire_hook.SETTINGS_PATH).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_run_declined_confirmation_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_project(tmp_path, MEMORY)
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert (
        wire_hook.run(
            root,
            title="hooks",
            enable=(MEMORY,),
            confirm_prompt="Apply?",
            assume_yes=False,
        )
        == 1
    )
    assert not (root / wire_hook.SETTINGS_PATH).exists()
    assert (root / wire_hook.HOOKS_DIR / MEMORY.file).exists()
