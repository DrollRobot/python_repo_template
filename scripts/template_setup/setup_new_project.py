"""Run the whole template-to-project transition from one toggle checklist.

Shows a numbered checklist of every setup step, all checked by default:

     1. strip template headers
     2. rename the project
     3. set the GitHub username
     4. set the Python version
     5. set the project version    (resets to 0.1.0 for a new project)
     6. reset the changelog        (drops the template's history)
     7. install command hooks      (wires Claude Code hooks to your shell)
     8. protect auto-memory        (gate Claude's memory writes)
     9. choose a license
    10. remove mkdocs              (if you don't want a docs site)
    11. report remaining FIXMEs
    12. re-initialize git          (destructive: deletes history)
    13. remove this scaffolding    (destructive: deletes these scripts)

Toggle steps by number ("3", "1 4", "5-8"), or with "all"/"none". Type "run"
to execute the checked steps -- that is the single point of confirmation:
each step then prompts for its inputs right before it runs and applies its
changes without asking again. Unchecked steps are skipped entirely, leaving
the repo untouched. Type "q" to quit without changing anything.

Each step is also runnable on its own with per-step previews and prompts;
this orchestrator just chains them. Steps always execute in the listed
order (cleanup last), no matter the order they were toggled in.

Usage:
    uv run scripts/template_setup/setup_new_project.py
"""

from __future__ import annotations

import datetime
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import _common
import choose_license
import choose_shell
import cleanup
import find_fixmes
import protect_auto_memory
import reinit_git
import remove_mkdocs
import rename_project
import reset_changelog
import set_github_user
import set_python_version
import set_version
import strip_template_headers

MenuAction = Literal["toggle", "run", "quit", "all", "none", "help", "error"]

_HINT = (
    "  Toggle steps by number ('3', '1 4', '5-8'), 'all'/'none' to check or clear\n"
    "  everything, 'run' to execute the checked steps, 'q' to quit."
)


@dataclass(frozen=True)
class MenuCommand:
    """Parsed result of one line of menu input.

    Attributes:
        action: What the input asks for.
        indices: Step numbers to toggle (``toggle`` action only).
        message: Text to show the user (``help`` and ``error`` actions only).
    """

    action: MenuAction
    indices: frozenset[int] = field(default_factory=frozenset)
    message: str = ""


@dataclass(frozen=True)
class Step:
    """One orchestrated setup step, in canonical execution order.

    Attributes:
        key: The step's module name, e.g. ``rename_project``.
        label: One-line description shown in the menu.
        gather: Prompts for the step's inputs and returns them as kwargs.
        execute: Runs the step with the gathered kwargs; returns an exit code.
        destructive: Whether the step deletes things that cannot be restored.
    """

    key: str
    label: str
    gather: Callable[[Path], dict[str, Any]]
    execute: Callable[[Path, dict[str, Any]], int]
    destructive: bool = False


def parse_menu_input(raw: str, step_count: int) -> MenuCommand:
    """Parse one line of menu input into a command.

    Empty input (and ``help``/``?``) shows the usage hint -- it never starts
    execution; only an explicit ``run`` does.

    Args:
        raw: The line as typed.
        step_count: Number of steps; valid step numbers are ``1..step_count``.

    Returns:
        The parsed :class:`MenuCommand`. Any invalid token rejects the whole
        line with an ``error`` command naming the offending token.
    """
    text = raw.strip().lower()
    if text in ("", "help", "?"):
        return MenuCommand("help", message=_HINT)
    if text == "run":
        return MenuCommand("run")
    if text in ("q", "quit"):
        return MenuCommand("quit")
    if text == "all":
        return MenuCommand("all")
    if text == "none":
        return MenuCommand("none")

    toggles: set[int] = set()
    for token in text.replace(",", " ").split():
        first, dash, last = token.partition("-")
        if not first.isdigit() or (dash and not last.isdigit()):
            return MenuCommand("error", message=f"  Not a step number or range: '{token}'.")
        low = int(first)
        high = int(last) if dash else low
        if low > high:
            return MenuCommand("error", message=f"  Range is reversed: '{token}'.")
        if low < 1 or high > step_count:
            return MenuCommand("error", message=f"  Out of range (1-{step_count}): '{token}'.")
        toggles.update(range(low, high + 1))
    return MenuCommand("toggle", indices=frozenset(toggles))


def apply_toggles(selected: frozenset[int], toggles: frozenset[int]) -> frozenset[int]:
    """Flip each toggled step number in or out of the selection.

    Args:
        selected: Currently checked step numbers.
        toggles: Step numbers to flip.

    Returns:
        The updated selection (symmetric difference).
    """
    return selected ^ toggles


def render_menu(steps: Sequence[Step], selected: frozenset[int]) -> str:
    """Render the checklist with ``[x]``/``[ ]`` markers and the usage hint.

    Args:
        steps: All steps, in canonical order.
        selected: Step numbers currently checked.

    Returns:
        The menu as a single printable string.
    """
    width = len(str(len(steps)))
    lines = ["", "Setup steps (checked steps run without further confirmation):", ""]
    for index, step in enumerate(steps, start=1):
        mark = "x" if index in selected else " "
        tag = "  [DESTRUCTIVE]" if step.destructive else ""
        lines.append(f"  [{mark}] {index:>{width}}. {step.label}{tag}")
    lines.append("")
    lines.append(_HINT)
    return "\n".join(lines)


def menu_loop(
    steps: Sequence[Step],
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> frozenset[int] | None:
    """Show the toggle menu and collect a final selection.

    Args:
        steps: All steps, in canonical order.
        input_fn: Reads one line of input (injectable for tests).
        print_fn: Writes one block of output (injectable for tests).

    Returns:
        The checked step numbers once the user types ``run``, or ``None`` when
        the user quits (or input ends).
    """
    selected = frozenset(range(1, len(steps) + 1))
    print_fn(render_menu(steps, selected))
    while True:
        try:
            raw = input_fn("> ")
        except EOFError:
            return None
        command = parse_menu_input(raw, len(steps))
        if command.action in ("help", "error"):
            print_fn(command.message)
        elif command.action == "quit":
            return None
        elif command.action == "run":
            if selected:
                return selected
            print_fn("  No steps selected; toggle some on, or type 'q' to quit.")
        else:
            if command.action == "all":
                selected = frozenset(range(1, len(steps) + 1))
            elif command.action == "none":
                selected = frozenset()
            else:
                selected = apply_toggles(selected, command.indices)
            print_fn(render_menu(steps, selected))


def _prompt_valid(
    prompt: str,
    validator: Callable[[str], object],
    *,
    default: str = "",
    prompt_fn: Callable[..., str] = _common.prompt_value,
) -> str:
    """Prompt until ``validator`` accepts the value.

    Args:
        prompt: Text to display before the input cursor.
        validator: Callable that raises :class:`ValueError` on invalid input.
        default: Value used when the user presses Enter without typing.
        prompt_fn: Reads one value (injectable for tests).

    Returns:
        The first value the validator accepts.
    """
    while True:
        value = prompt_fn(prompt, default=default)
        try:
            validator(value)
        except ValueError as exc:
            print(f"  {exc}")
            continue
        return value


def _require_value(value: str) -> str:
    """Reject empty input.

    Args:
        value: User-supplied value.

    Returns:
        The value unchanged.

    Raises:
        ValueError: If the value is empty or whitespace.
    """
    if not value.strip():
        raise ValueError("A value is required.")
    return value


def _gather_nothing(root: Path) -> dict[str, Any]:
    """Gather nothing, for steps that take no parameters.

    Args:
        root: Project root directory (unused).

    Returns:
        An empty kwargs mapping.
    """
    return {}


def _gather_rename(root: Path) -> dict[str, Any]:
    """Prompt for the new project name.

    Args:
        root: Project root directory (unused).

    Returns:
        Kwargs for :func:`rename_project.run`.
    """
    name = _prompt_valid("New project name (e.g. my-project)", rename_project.derive_names)
    return {"name": name}


def _gather_github_user(root: Path) -> dict[str, Any]:
    """Prompt for the GitHub username.

    Args:
        root: Project root directory (unused).

    Returns:
        Kwargs for :func:`set_github_user.run`.
    """
    return {"username": _prompt_valid("Your GitHub username", _require_value)}


def _gather_python_version(root: Path) -> dict[str, Any]:
    """Prompt for the Python version.

    Args:
        root: Project root directory (unused).

    Returns:
        Kwargs for :func:`set_python_version.run`.
    """
    version = _prompt_valid(
        "Python version",
        set_python_version.version_forms,
        default=set_python_version.DEFAULT_VERSION,
    )
    return {"version": version}


def _gather_version(root: Path) -> dict[str, Any]:
    """Prompt for the project version.

    Args:
        root: Project root directory (unused).

    Returns:
        Kwargs for :func:`set_version.run`.
    """
    version = _prompt_valid(
        "Project version", set_version.validate, default=set_version.DEFAULT_VERSION
    )
    return {"version": version}


def _gather_shell(root: Path) -> dict[str, Any]:
    """Ask whether to install the command hooks and, if so, for which shell.

    Declining is a real choice, not a confirmation: it makes the step remove
    the hook files entirely (that script's standalone behavior).

    Args:
        root: Project root directory (unused).

    Returns:
        Kwargs for :func:`choose_shell.run`.
    """
    install = choose_shell._prompt_install()
    shell = choose_shell._prompt_choice() if install else None
    return {"install": install, "shell": shell}


def _gather_memory_guard(root: Path) -> dict[str, Any]:
    """Ask whether to enable the auto-memory guard (declining removes it).

    Args:
        root: Project root directory (unused).

    Returns:
        Kwargs for :func:`protect_auto_memory.run`.
    """
    return {"install": protect_auto_memory._prompt_install()}


def _gather_license(root: Path) -> dict[str, Any]:
    """Prompt for the license choice and its copyright details.

    Passing every field the chosen license needs means the step itself never
    prompts. When no license candidates remain (already chosen), nothing is
    asked and the step reports that on its own.

    Args:
        root: Project root directory.

    Returns:
        Kwargs for :func:`choose_license.run`.
    """
    available = choose_license._available(root)
    if not available:
        return {}
    key = choose_license._prompt_choice(available)
    params: dict[str, Any] = {"key": key}
    if key in choose_license._NEEDS_HOLDER:
        params["year"] = _common.prompt_value(
            "Copyright year", default=str(datetime.date.today().year)
        )
        params["name"] = _common.prompt_value("Copyright holder name")
    if key in choose_license._NEEDS_COMPANY:
        params["company"] = _common.prompt_value("Company name")
    return params


def _gather_branch(root: Path) -> dict[str, Any]:
    """Prompt for the initial branch name of the re-initialized repository.

    Args:
        root: Project root directory (unused).

    Returns:
        Kwargs for :func:`reinit_git.run`.
    """
    return {"branch": _common.prompt_value("Initial branch name", default="main")}


def _execute_strip(root: Path, params: dict[str, Any]) -> int:
    """Run the template-header strip step.

    Args:
        root: Project root directory.
        params: Gathered kwargs (empty).

    Returns:
        The step's exit code.
    """
    return strip_template_headers.run(root, assume_yes=True)


def _execute_rename(root: Path, params: dict[str, Any]) -> int:
    """Run the project-rename step.

    Args:
        root: Project root directory.
        params: Gathered kwargs (``name``).

    Returns:
        The step's exit code.
    """
    return rename_project.run(root, params["name"], assume_yes=True)


def _execute_github_user(root: Path, params: dict[str, Any]) -> int:
    """Run the GitHub-username step.

    Args:
        root: Project root directory.
        params: Gathered kwargs (``username``).

    Returns:
        The step's exit code.
    """
    return set_github_user.run(root, params["username"], assume_yes=True)


def _execute_python_version(root: Path, params: dict[str, Any]) -> int:
    """Run the Python-version step.

    Args:
        root: Project root directory.
        params: Gathered kwargs (``version``).

    Returns:
        The step's exit code.
    """
    return set_python_version.run(root, params["version"], assume_yes=True)


def _execute_version(root: Path, params: dict[str, Any]) -> int:
    """Run the project-version step.

    Args:
        root: Project root directory.
        params: Gathered kwargs (``version``).

    Returns:
        The step's exit code.
    """
    return set_version.run(root, params["version"], assume_yes=True)


def _execute_reset_changelog(root: Path, params: dict[str, Any]) -> int:
    """Run the changelog-reset step.

    Args:
        root: Project root directory.
        params: Gathered kwargs (empty).

    Returns:
        The step's exit code.
    """
    return reset_changelog.run(root, assume_yes=True)


def _execute_shell(root: Path, params: dict[str, Any]) -> int:
    """Run the command-hooks step.

    Args:
        root: Project root directory.
        params: Gathered kwargs (``install``, ``shell``).

    Returns:
        The step's exit code.
    """
    return choose_shell.run(root, params["shell"], install=params["install"], assume_yes=True)


def _execute_memory_guard(root: Path, params: dict[str, Any]) -> int:
    """Run the auto-memory-guard step.

    Args:
        root: Project root directory.
        params: Gathered kwargs (``install``).

    Returns:
        The step's exit code.
    """
    return protect_auto_memory.run(root, install=params["install"], assume_yes=True)


def _execute_license(root: Path, params: dict[str, Any]) -> int:
    """Run the license step.

    Args:
        root: Project root directory.
        params: Gathered kwargs (``key``/``year``/``name``/``company``, or
            empty when no candidates remain).

    Returns:
        The step's exit code.
    """
    return choose_license.run(root, assume_yes=True, **params)


def _execute_remove_mkdocs(root: Path, params: dict[str, Any]) -> int:
    """Run the mkdocs-removal step.

    Args:
        root: Project root directory.
        params: Gathered kwargs (empty).

    Returns:
        The step's exit code.
    """
    return remove_mkdocs.run(root, assume_yes=True)


def _execute_find_fixmes(root: Path, params: dict[str, Any]) -> int:
    """Run the FIXME report (read-only).

    Args:
        root: Project root directory.
        params: Gathered kwargs (empty).

    Returns:
        The step's exit code (always 0).
    """
    return find_fixmes.run(root)


def _execute_reinit_git(root: Path, params: dict[str, Any]) -> int:
    """Run the git re-initialization step (destructive).

    Args:
        root: Project root directory.
        params: Gathered kwargs (``branch``).

    Returns:
        The step's exit code.
    """
    return reinit_git.run(root, branch=params["branch"], assume_yes=True)


def _execute_cleanup(root: Path, params: dict[str, Any]) -> int:
    """Run the scaffolding-removal step (destructive).

    Args:
        root: Project root directory.
        params: Gathered kwargs (empty).

    Returns:
        The step's exit code.
    """
    return cleanup.run(root, assume_yes=True)


STEPS: tuple[Step, ...] = (
    Step("strip_template_headers", "Strip template headers", _gather_nothing, _execute_strip),
    Step("rename_project", "Rename the project", _gather_rename, _execute_rename),
    Step("set_github_user", "Set the GitHub username", _gather_github_user, _execute_github_user),
    Step(
        "set_python_version",
        "Set the Python version",
        _gather_python_version,
        _execute_python_version,
    ),
    Step("set_version", "Set the project version", _gather_version, _execute_version),
    Step("reset_changelog", "Reset the changelog", _gather_nothing, _execute_reset_changelog),
    Step(
        "choose_shell",
        "Claude command hooks (choose shell, or remove)",
        _gather_shell,
        _execute_shell,
    ),
    Step(
        "protect_auto_memory",
        "Claude auto-memory guard (enable, or remove)",
        _gather_memory_guard,
        _execute_memory_guard,
    ),
    Step("choose_license", "Choose a license", _gather_license, _execute_license),
    Step(
        "remove_mkdocs",
        "Remove mkdocs (documentation site)",
        _gather_nothing,
        _execute_remove_mkdocs,
    ),
    Step(
        "find_fixmes", "Report remaining FIXMEs (read-only)", _gather_nothing, _execute_find_fixmes
    ),
    Step(
        "reinit_git",
        "Re-initialize git (deletes history)",
        _gather_branch,
        _execute_reinit_git,
        destructive=True,
    ),
    Step(
        "cleanup",
        "Remove the template-setup scaffolding",
        _gather_nothing,
        _execute_cleanup,
        destructive=True,
    ),
)


def execute_steps(root: Path, steps: Sequence[Step], selected: frozenset[int]) -> list[str]:
    """Run the selected steps in canonical order, gathering inputs just in time.

    Each step's inputs are prompted for immediately before it executes, so a
    long run never front-loads every question. Failures are collected rather
    than fatal: the remaining steps still run.

    Args:
        root: Project root directory.
        steps: All steps, in canonical order.
        selected: Step numbers to run.

    Returns:
        Keys of the steps that returned a nonzero exit code.
    """
    failed: list[str] = []
    for index, step in enumerate(steps, start=1):
        if index not in selected:
            continue
        params = step.gather(root)
        if step.execute(root, params) != 0:
            failed.append(step.key)
    return failed


def main() -> None:
    """Run the checklist-driven, end-to-end project setup."""
    root = _common.find_root()

    _common.section("New project setup")
    print("  This converts the cloned template into your own project.")
    _common.info("Project root", str(root))
    print()
    print("  Checked steps run in the listed order, prompting for their inputs")
    print("  right before each one executes. Typing 'run' is the only")
    print("  confirmation -- steps apply their changes without asking again.")

    selected = menu_loop(STEPS)
    if selected is None:
        sys.exit("Aborted; nothing changed.")

    failed = execute_steps(root, STEPS, selected)

    _common.section("Setup complete")
    if failed:
        print("  Steps that reported a problem: " + ", ".join(failed))
        print("  Review their output above; each can be re-run on its own.")
    else:
        print("  Review the changes, then write some code!")


if __name__ == "__main__":
    main()
