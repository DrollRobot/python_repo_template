"""Unit tests for scripts/template_setup/choose_shell.py.

choose_shell owns the *choice* -- which hook kinds, which shell flavor -- and
hands the resulting keep/delete lists to wire_hook, which owns settings.json
(covered in test_wire_hook.py). These tests drive run() end to end and check
the outcome on disk: the chosen flavor wired, every other hook file gone.

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
import wire_hook

_BOTH_KINDS = frozenset({"no_chained_commands", "canonical_commands"})


def _make_project(tmp_path: Path) -> Path:
    """Create a project root with all four shell hook files present.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        The project root path.
    """
    hooks = tmp_path / wire_hook.HOOKS_DIR
    hooks.mkdir(parents=True)
    for spec in choose_shell._ALL_SHELL_SPECS:
        (hooks / spec.file).write_text("", encoding="utf-8")
    return tmp_path


def _entry(root: Path) -> dict[str, object]:
    """Return the last PreToolUse entry from the project's settings.json."""
    settings = json.loads((root / wire_hook.SETTINGS_PATH).read_text(encoding="utf-8"))
    entries = settings["hooks"]["PreToolUse"]
    assert isinstance(entries, list)
    return dict(entries[-1])


# ---------------------------------------------------------------------------
# spec selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_shell_specs_covers_both_kinds_in_both_flavors() -> None:
    files = {spec.file for spec in choose_shell._ALL_SHELL_SPECS}
    assert files == {
        "no-chained-commands-pwsh.py",
        "no-chained-commands-bash.py",
        "canonical-commands-pwsh.py",
        "canonical-commands-bash.py",
    }


@pytest.mark.unit
def test_specs_for_returns_chosen_flavor_in_kind_order() -> None:
    specs = choose_shell._specs_for("powershell", _BOTH_KINDS)
    assert [spec.file for spec in specs] == [
        "no-chained-commands-pwsh.py",
        "canonical-commands-pwsh.py",
    ]


@pytest.mark.unit
def test_specs_for_drops_the_declined_kind() -> None:
    specs = choose_shell._specs_for("bash", frozenset({"no_chained_commands"}))
    assert [spec.file for spec in specs] == ["no-chained-commands-bash.py"]


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


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
    assert not (root / wire_hook.SETTINGS_PATH).exists()


@pytest.mark.integration
@pytest.mark.functional
def test_run_missing_hook_files_returns_one(tmp_path: Path) -> None:
    """A shell whose hook files are absent is rejected."""
    (tmp_path / wire_hook.HOOKS_DIR).mkdir(parents=True)
    assert (
        choose_shell.run(
            tmp_path, "bash", no_chained_commands=True, canonical_commands=True, assume_yes=True
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.functional
def test_run_invalid_settings_aborts_without_changes(tmp_path: Path) -> None:
    """An unparseable settings.json aborts the step before any file changes."""
    root = _make_project(tmp_path)
    settings_path = root / wire_hook.SETTINGS_PATH
    settings_path.write_text("{not json", encoding="utf-8")

    assert (
        choose_shell.run(
            root, "powershell", no_chained_commands=True, canonical_commands=True, assume_yes=True
        )
        == 1
    )

    assert settings_path.read_text(encoding="utf-8") == "{not json"
    hooks = root / wire_hook.HOOKS_DIR
    for spec in choose_shell._ALL_SHELL_SPECS:
        assert (hooks / spec.file).exists()


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
    assert not (root / wire_hook.SETTINGS_PATH).exists()
    assert (root / wire_hook.HOOKS_DIR / "canonical-commands-bash.py").exists()


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

    assert _entry(root)["matcher"] == "Bash|PowerShell"

    hooks = root / wire_hook.HOOKS_DIR
    assert (hooks / "canonical-commands-pwsh.py").exists()
    assert not (hooks / "canonical-commands-bash.py").exists()
    assert not (hooks / "no-chained-commands-bash.py").exists()


@pytest.mark.integration
@pytest.mark.functional
def test_run_wires_both_kinds_as_one_entry(tmp_path: Path) -> None:
    """The pair shares a matcher, so it is written as one entry, two commands."""
    root = _make_project(tmp_path)
    choose_shell.run(
        root, "powershell", no_chained_commands=True, canonical_commands=True, assume_yes=True
    )

    hooks = _entry(root)["hooks"]
    assert isinstance(hooks, list)
    targets = [hook["args"][-1] for hook in hooks]
    assert len(targets) == 2
    assert any("no-chained-commands-pwsh.py" in t for t in targets)
    assert any("canonical-commands-pwsh.py" in t for t in targets)


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

    hooks = root / wire_hook.HOOKS_DIR
    assert (hooks / "no-chained-commands-bash.py").exists()
    assert not (hooks / "canonical-commands-bash.py").exists()
    assert not (hooks / "no-chained-commands-pwsh.py").exists()
    assert not (hooks / "canonical-commands-pwsh.py").exists()

    entry_hooks = _entry(root)["hooks"]
    assert isinstance(entry_hooks, list)
    targets = [hook["args"][-1] for hook in entry_hooks]
    assert len(targets) == 1
    assert "no-chained-commands-bash.py" in targets[0]


@pytest.mark.integration
@pytest.mark.functional
def test_run_bash_flavor_differs_only_by_matcher(tmp_path: Path) -> None:
    """Both flavors launch identically; only the matcher tells them apart.

    The interpreter used to differ per flavor, which broke the moment a
    committed settings.json was opened on another OS.
    """
    root = _make_project(tmp_path)
    choose_shell.run(
        root, "bash", no_chained_commands=True, canonical_commands=True, assume_yes=True
    )

    entry = _entry(root)
    assert entry["matcher"] == "Bash"
    hooks = entry["hooks"]
    assert isinstance(hooks, list)
    assert all(hook["command"] == "uv" for hook in hooks)


@pytest.mark.integration
@pytest.mark.functional
def test_run_switching_shells_replaces_the_entry(tmp_path: Path) -> None:
    """Re-running with the other shell leaves exactly one shell-hook entry."""
    root = _make_project(tmp_path)
    choose_shell.run(
        root, "powershell", no_chained_commands=True, canonical_commands=True, assume_yes=True
    )
    # The pwsh files are gone now, so restore all four before switching.
    _make_hooks_again(root)
    choose_shell.run(
        root, "bash", no_chained_commands=True, canonical_commands=True, assume_yes=True
    )

    settings = json.loads((root / wire_hook.SETTINGS_PATH).read_text(encoding="utf-8"))
    entries = settings["hooks"]["PreToolUse"]
    assert len(entries) == 1
    assert entries[0]["matcher"] == "Bash"


def _make_hooks_again(root: Path) -> None:
    """Recreate every shell hook file under an existing project root."""
    hooks = root / wire_hook.HOOKS_DIR
    hooks.mkdir(parents=True, exist_ok=True)
    for spec in choose_shell._ALL_SHELL_SPECS:
        (hooks / spec.file).write_text("", encoding="utf-8")


@pytest.mark.integration
@pytest.mark.functional
def test_run_decline_removes_all_hooks(tmp_path: Path) -> None:
    """Declining both kinds deletes every shell hook file and writes no settings."""
    root = _make_project(tmp_path)
    assert (
        choose_shell.run(root, no_chained_commands=False, canonical_commands=False, assume_yes=True)
        == 0
    )

    assert not (root / wire_hook.SETTINGS_PATH).exists()
    assert not (root / wire_hook.HOOKS_DIR).exists()  # the emptied directory goes too


@pytest.mark.integration
@pytest.mark.functional
def test_run_decline_strips_earlier_wiring(tmp_path: Path) -> None:
    """Declining after an earlier run removes the entry, not just the files."""
    root = _make_project(tmp_path)
    choose_shell.run(
        root, "powershell", no_chained_commands=True, canonical_commands=True, assume_yes=True
    )
    assert (
        choose_shell.run(root, no_chained_commands=False, canonical_commands=False, assume_yes=True)
        == 0
    )
    assert not (root / wire_hook.SETTINGS_PATH).exists()


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

    hooks = root / wire_hook.HOOKS_DIR
    for spec in choose_shell._ALL_SHELL_SPECS:
        assert (hooks / spec.file).exists()
