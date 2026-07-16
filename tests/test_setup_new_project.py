"""Unit tests for the checklist orchestrator in scripts/template_setup/setup_new_project.py.

The template_setup folder is not a package, so the module is imported by
adding the folder to sys.path, mirroring how the setup scripts import their
shared _common module.

This file is itself a dev-script test: cleanup.py matches it to
scripts/template_setup/setup_new_project.py and deletes it along with the rest
of the scaffolding, so it never lingers in a project started from the template.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "template_setup"))

import choose_license
import choose_shell
import rename_project
import setup_new_project

CANONICAL_KEYS = [
    "strip_template_headers",
    "rename_project",
    "set_github_user",
    "set_python_version",
    "set_version",
    "reset_changelog",
    "choose_shell",
    "protect_auto_memory",
    "choose_license",
    "remove_mkdocs",
    "find_fixmes",
    "reinit_git",
    "cleanup",
]

ALL_SELECTED = frozenset(range(1, len(CANONICAL_KEYS) + 1))


def _scripted_input(lines: list[str]) -> Callable[[str], str]:
    """Return an input_fn that replays the given lines in order."""
    iterator: Iterator[str] = iter(lines)

    def fake_input(prompt: str) -> str:
        return next(iterator)

    return fake_input


def _fake_step(key: str, log: list[str], *, exit_code: int = 0) -> setup_new_project.Step:
    """Build a Step whose gather/execute record their calls in ``log``."""

    def gather(root: Path) -> dict[str, Any]:
        log.append(f"gather:{key}")
        return {"key": key}

    def execute(root: Path, params: dict[str, Any]) -> int:
        log.append(f"execute:{params['key']}")
        return exit_code

    return setup_new_project.Step(key, key, gather, execute)


def _recording_run(calls: list[dict[str, Any]], exit_code: int = 0) -> Callable[..., int]:
    """Return a stand-in for a sub-script run() that records its arguments."""

    def run(*args: Any, **kwargs: Any) -> int:
        calls.append({"args": args, "kwargs": kwargs})
        return exit_code

    return run


# ---------------------------------------------------------------------------
# parse_menu_input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "help", "?"])
def test_parse_blank_and_help_input_is_help(raw: str) -> None:
    command = setup_new_project.parse_menu_input(raw, 13)
    assert command.action == "help"
    assert command.message


@pytest.mark.parametrize("raw", ["run", " RUN ", "Run"])
def test_parse_run_command(raw: str) -> None:
    assert setup_new_project.parse_menu_input(raw, 13).action == "run"


@pytest.mark.parametrize("raw", ["q", "quit", "Q", " QUIT "])
def test_parse_quit_commands(raw: str) -> None:
    assert setup_new_project.parse_menu_input(raw, 13).action == "quit"


def test_parse_all_and_none() -> None:
    assert setup_new_project.parse_menu_input("all", 13).action == "all"
    assert setup_new_project.parse_menu_input("none", 13).action == "none"


def test_parse_single_number_toggles() -> None:
    command = setup_new_project.parse_menu_input("3", 13)
    assert command.action == "toggle"
    assert command.indices == frozenset({3})


def test_parse_space_and_comma_separated() -> None:
    command = setup_new_project.parse_menu_input("1 3,5", 13)
    assert command.action == "toggle"
    assert command.indices == frozenset({1, 3, 5})


def test_parse_range_expands() -> None:
    command = setup_new_project.parse_menu_input("5-8", 13)
    assert command.action == "toggle"
    assert command.indices == frozenset({5, 6, 7, 8})


def test_parse_mixed_numbers_and_ranges() -> None:
    command = setup_new_project.parse_menu_input("1, 3-4 13", 13)
    assert command.action == "toggle"
    assert command.indices == frozenset({1, 3, 4, 13})


def test_parse_duplicates_collapse() -> None:
    command = setup_new_project.parse_menu_input("2 2,1-2", 13)
    assert command.action == "toggle"
    assert command.indices == frozenset({1, 2})


@pytest.mark.parametrize("raw", ["0", "14", "1-14", "0-3"])
def test_parse_rejects_out_of_range(raw: str) -> None:
    command = setup_new_project.parse_menu_input(raw, 13)
    assert command.action == "error"
    assert raw.strip() in command.message


def test_parse_rejects_reversed_range() -> None:
    command = setup_new_project.parse_menu_input("5-2", 13)
    assert command.action == "error"
    assert "5-2" in command.message


@pytest.mark.parametrize("raw", ["banana", "1-", "-3", "1--3", "run 3", "1 banana 3"])
def test_parse_rejects_garbage_and_whole_line(raw: str) -> None:
    command = setup_new_project.parse_menu_input(raw, 13)
    assert command.action == "error"
    assert command.indices == frozenset()


# ---------------------------------------------------------------------------
# apply_toggles / render_menu
# ---------------------------------------------------------------------------


def test_apply_toggles_unchecks_and_rechecks() -> None:
    selected = frozenset({1, 2, 3})
    unchecked = setup_new_project.apply_toggles(selected, frozenset({2, 4}))
    assert unchecked == frozenset({1, 3, 4})
    rechecked = setup_new_project.apply_toggles(unchecked, frozenset({2, 4}))
    assert rechecked == selected


def test_render_menu_marks_checked_and_unchecked() -> None:
    menu = setup_new_project.render_menu(setup_new_project.STEPS, frozenset({1}))
    assert "[x]  1." in menu
    assert "[ ]  2." in menu
    assert "13." in menu


def test_render_menu_flags_destructive_steps() -> None:
    menu = setup_new_project.render_menu(setup_new_project.STEPS, ALL_SELECTED)
    destructive_rows = [line for line in menu.splitlines() if "[DESTRUCTIVE]" in line]
    assert len(destructive_rows) == 2
    assert any("Re-initialize git" in line for line in destructive_rows)
    assert any("scaffolding" in line for line in destructive_rows)


# ---------------------------------------------------------------------------
# menu_loop
# ---------------------------------------------------------------------------


def test_menu_loop_starts_all_selected() -> None:
    result = setup_new_project.menu_loop(
        setup_new_project.STEPS, input_fn=_scripted_input(["run"]), print_fn=lambda _: None
    )
    assert result == ALL_SELECTED


def test_menu_loop_toggle_then_run() -> None:
    result = setup_new_project.menu_loop(
        setup_new_project.STEPS, input_fn=_scripted_input(["3", "run"]), print_fn=lambda _: None
    )
    assert result == ALL_SELECTED - {3}


def test_menu_loop_blank_enter_shows_hint_not_run() -> None:
    printed: list[str] = []
    result = setup_new_project.menu_loop(
        setup_new_project.STEPS, input_fn=_scripted_input(["", "q"]), print_fn=printed.append
    )
    assert result is None
    assert any("'run' to execute" in text for text in printed)


def test_menu_loop_quit_returns_none() -> None:
    result = setup_new_project.menu_loop(
        setup_new_project.STEPS, input_fn=_scripted_input(["q"]), print_fn=lambda _: None
    )
    assert result is None


def test_menu_loop_run_with_empty_selection_reprompts() -> None:
    printed: list[str] = []
    result = setup_new_project.menu_loop(
        setup_new_project.STEPS,
        input_fn=_scripted_input(["none", "run", "q"]),
        print_fn=printed.append,
    )
    assert result is None
    assert any("No steps selected" in text for text in printed)


def test_menu_loop_invalid_input_reprompts() -> None:
    printed: list[str] = []
    result = setup_new_project.menu_loop(
        setup_new_project.STEPS,
        input_fn=_scripted_input(["banana", "run"]),
        print_fn=printed.append,
    )
    assert result == ALL_SELECTED
    assert any("banana" in text for text in printed)


def test_menu_loop_none_then_all_restores() -> None:
    result = setup_new_project.menu_loop(
        setup_new_project.STEPS,
        input_fn=_scripted_input(["none", "all", "run"]),
        print_fn=lambda _: None,
    )
    assert result == ALL_SELECTED


def test_menu_loop_eof_aborts() -> None:
    def raising_input(prompt: str) -> str:
        raise EOFError

    result = setup_new_project.menu_loop(
        setup_new_project.STEPS, input_fn=raising_input, print_fn=lambda _: None
    )
    assert result is None


# ---------------------------------------------------------------------------
# step registry
# ---------------------------------------------------------------------------


def test_registry_order_is_canonical() -> None:
    assert [step.key for step in setup_new_project.STEPS] == CANONICAL_KEYS


def test_registry_cleanup_last_and_destructive_flags() -> None:
    assert setup_new_project.STEPS[-1].key == "cleanup"
    destructive = {step.key for step in setup_new_project.STEPS if step.destructive}
    assert destructive == {"reinit_git", "cleanup"}


def test_paramless_gathers_return_empty(tmp_path: Path) -> None:
    paramless = {
        "strip_template_headers",
        "reset_changelog",
        "remove_mkdocs",
        "find_fixmes",
        "cleanup",
    }
    for step in setup_new_project.STEPS:
        if step.key in paramless:
            assert step.gather(tmp_path) == {}


# ---------------------------------------------------------------------------
# execute_steps
# ---------------------------------------------------------------------------


def test_execute_steps_runs_only_selected_in_registry_order(tmp_path: Path) -> None:
    log: list[str] = []
    steps = [_fake_step("a", log), _fake_step("b", log), _fake_step("c", log)]
    failed = setup_new_project.execute_steps(tmp_path, steps, frozenset({1, 3}))
    assert failed == []
    assert log == ["gather:a", "execute:a", "gather:c", "execute:c"]


def test_execute_steps_gathers_just_in_time(tmp_path: Path) -> None:
    log: list[str] = []
    steps = [_fake_step("a", log), _fake_step("b", log)]
    setup_new_project.execute_steps(tmp_path, steps, frozenset({1, 2}))
    assert log == ["gather:a", "execute:a", "gather:b", "execute:b"]


def test_execute_steps_collects_failures(tmp_path: Path) -> None:
    log: list[str] = []
    steps = [
        _fake_step("a", log),
        _fake_step("b", log, exit_code=1),
        _fake_step("c", log),
    ]
    failed = setup_new_project.execute_steps(tmp_path, steps, frozenset({1, 2, 3}))
    assert failed == ["b"]
    assert "execute:c" in log


def test_execute_passes_assume_yes_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample_params: dict[str, dict[str, Any]] = {
        "strip_template_headers": {},
        "rename_project": {"name": "my-project"},
        "set_github_user": {"username": "someone"},
        "set_python_version": {"version": "3.14"},
        "set_version": {"version": "0.1.0"},
        "reset_changelog": {},
        "choose_shell": {"install": True, "shell": "bash"},
        "protect_auto_memory": {"install": True},
        "choose_license": {"key": "mit", "year": "2026", "name": "Ada"},
        "remove_mkdocs": {},
        "reinit_git": {"branch": "main"},
        "cleanup": {},
    }
    for step in setup_new_project.STEPS:
        if step.key == "find_fixmes":
            continue  # read-only report; takes no assume_yes
        calls: list[dict[str, Any]] = []
        module = getattr(setup_new_project, step.key)
        monkeypatch.setattr(module, "run", _recording_run(calls))
        assert step.execute(tmp_path, sample_params[step.key]) == 0
        assert calls[0]["kwargs"].get("assume_yes") is True, step.key


def test_execute_license_forwards_gathered_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(choose_license, "run", _recording_run(calls))
    step = setup_new_project.STEPS[CANONICAL_KEYS.index("choose_license")]
    step.execute(tmp_path, {"key": "gnu"})
    assert calls[0]["kwargs"]["key"] == "gnu"
    assert calls[0]["kwargs"]["assume_yes"] is True


def test_execute_shell_forwards_install_and_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(choose_shell, "run", _recording_run(calls))
    step = setup_new_project.STEPS[CANONICAL_KEYS.index("choose_shell")]
    step.execute(tmp_path, {"install": False, "shell": None})
    assert calls[0]["args"] == (tmp_path, None)
    assert calls[0]["kwargs"]["install"] is False


# ---------------------------------------------------------------------------
# param gathering
# ---------------------------------------------------------------------------


def test_prompt_valid_reprompts_until_valid() -> None:
    answers = iter(["???", "my-project"])

    def fake_prompt(prompt: str, *, default: str = "") -> str:
        return next(answers)

    value = setup_new_project._prompt_valid(
        "New project name", rename_project.derive_names, prompt_fn=fake_prompt
    )
    assert value == "my-project"


def test_require_value_rejects_empty() -> None:
    with pytest.raises(ValueError, match="required"):
        setup_new_project._require_value("   ")
    assert setup_new_project._require_value("someone") == "someone"


def test_gather_license_no_candidates_prompts_nothing(tmp_path: Path) -> None:
    assert setup_new_project._gather_license(tmp_path) == {}


def test_gather_license_gnu_skips_holder_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / choose_license.CANDIDATES["gnu"]).write_text("GPL text", encoding="utf-8")
    monkeypatch.setattr(choose_license, "_prompt_choice", lambda available: "gnu")
    assert setup_new_project._gather_license(tmp_path) == {"key": "gnu"}
