"""Optionally install Claude Code command hooks, wired to your primary shell.

The template ships two independent ``PreToolUse`` hooks under
``.claude/hooks/``, each in a powershell/bash flavor:

    no-chained-commands-pwsh.py   no-chained-commands-bash.py
    canonical-commands-pwsh.py    canonical-commands-bash.py

``no-chained-commands`` requires one shell command per tool call, so a
permission allowlist keeps matching. ``canonical-commands`` keeps shell
invocation consistent, so you don't need to allow multiple equivalent
commands. Each hook is independently optional. If at least one is wanted,
this script asks which shell you primarily use, then hands the choice to
``wire_hook.py``: the matching pair is merged into ``.claude/settings.json``
and every hook file that isn't wanted -- the unused shell's files, and either
hook kind you declined -- is deleted.

This script owns the *choice* (which kinds, which shell); ``wire_hook.py``
owns the settings file. The four hook files are described there too, in its
:data:`HOOKS` registry.

Usage:
    uv run scripts/template_setup/choose_shell.py
    uv run scripts/template_setup/choose_shell.py --shell bash
    uv run scripts/template_setup/choose_shell.py --shell powershell --dry-run
    uv run scripts/template_setup/choose_shell.py --shell bash --no-canonical-commands
    uv run scripts/template_setup/choose_shell.py --no-hooks
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _common
import wire_hook

# Supported shells, mapped to the suffix their hook files and spec keys use.
_SHELL_META = {
    "powershell": "pwsh",
    "bash": "bash",
}

# The two independently-toggleable hook kinds, in display/wiring order.
_KINDS = ("no_chained_commands", "canonical_commands")

# Every shell hook the template ships, in wiring order per shell. Anything not
# chosen below is deleted, so this is also the "remove them all" set.
_ALL_SHELL_SPECS = tuple(
    wire_hook.by_key(f"{kind}_{suffix}") for suffix in _SHELL_META.values() for kind in _KINDS
)

SECTION = "Claude Code command hooks"


def _specs_for(shell: str, kinds: frozenset[str]) -> tuple[wire_hook.HookSpec, ...]:
    """Return the hook specs implementing the wanted kinds for one shell.

    Args:
        shell: ``powershell`` or ``bash``.
        kinds: Which hook kinds to include (subset of :data:`_KINDS`).

    Returns:
        The matching specs, in :data:`_KINDS` order.
    """
    suffix = _SHELL_META[shell]
    return tuple(wire_hook.by_key(f"{kind}_{suffix}") for kind in _KINDS if kind in kinds)


def run(
    root: Path,
    shell: str | None = None,
    *,
    no_chained_commands: bool | None = None,
    canonical_commands: bool | None = None,
    assume_yes: bool = False,
    dry_run: bool = False,
) -> int:
    """Install the chosen hooks into settings, or remove all of them on decline.

    Args:
        root: Project root directory.
        shell: ``powershell`` or ``bash``; prompt if ``None`` (only when at
            least one hook kind is wanted).
        no_chained_commands: Whether to install the no-chained-commands hook;
            prompt if ``None``.
        canonical_commands: Whether to install the canonical-commands hook;
            prompt if ``None``.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.

    Returns:
        Process exit code (0 on success, 1 if aborted, the shell is invalid, or
        an existing ``settings.json`` cannot be parsed).
    """
    if no_chained_commands is None:
        no_chained_commands = _prompt_kind(
            "no-chained-commands",
            "Requires one shell command per tool call, so a permission allowlist keeps matching.",
        )
    if canonical_commands is None:
        canonical_commands = _prompt_kind(
            "canonical-commands",
            "Keeps shell invocation consistent, so you don't need to allow multiple equivalent"
            " commands.",
        )

    kinds = frozenset(
        kind
        for kind, wanted in (
            ("no_chained_commands", no_chained_commands),
            ("canonical_commands", canonical_commands),
        )
        if wanted
    )
    if not kinds:
        # Nothing wanted: delete every flavor of both kinds, and strip any
        # wiring left over from an earlier run.
        return wire_hook.run(
            root,
            title=SECTION,
            delete=_ALL_SHELL_SPECS,
            assume_yes=assume_yes,
            dry_run=dry_run,
        )

    if shell is None:
        shell = _prompt_choice()
    shell = shell.lower()
    if shell not in _SHELL_META:
        print(f"  '{shell}' is not valid. Choose from: {', '.join(sorted(_SHELL_META))}.")
        return 1

    keep = _specs_for(shell, kinds)
    drop = tuple(spec for spec in _ALL_SHELL_SPECS if spec not in keep)
    return wire_hook.run(
        root,
        title=SECTION,
        enable=keep,
        delete=drop,
        info=(("Shell", shell),),
        confirm_prompt="Apply hook choice?",
        assume_yes=assume_yes,
        dry_run=dry_run,
    )


def _prompt_kind(label: str, description: str) -> bool:
    """Ask whether to install one hook kind.

    Args:
        label: Short hook name shown in the prompt (e.g. ``no-chained-commands``).
        description: One-line explanation of what the hook does.

    Returns:
        ``True`` to install this hook kind, ``False`` to decline it.
    """
    print(f"{label}: {description}")
    return _common.confirm(f"  Install the {label} hook?")


def _prompt_choice() -> str:
    """Prompt the user to pick a primary shell by number.

    Returns:
        The chosen shell key.
    """
    keys = sorted(_SHELL_META)
    print("  Available shells:")
    for index, key in enumerate(keys, start=1):
        print(f"    {index}) {key}")
    while True:
        answer = input("  Choose your primary shell [number]: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        print("  Enter the number of one of the listed shells.")


def main() -> None:
    """Parse arguments and run the hook installer."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--shell", choices=sorted(_SHELL_META), help="Primary shell to wire in.")
    group.add_argument(
        "--no-hooks", action="store_true", help="Skip both hooks and remove all hook files."
    )
    parser.add_argument(
        "--no-chained-commands",
        action="store_true",
        help="With --shell, skip the no-chained-commands hook.",
    )
    parser.add_argument(
        "--no-canonical-commands",
        action="store_true",
        help="With --shell, skip the canonical-commands hook.",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing."
    )
    args = parser.parse_args()

    # --no-hooks declines both; --shell implies wanting hooks (each kind can be
    # negated); otherwise prompt for each kind individually.
    no_chained_commands: bool | None
    canonical_commands: bool | None
    if args.no_hooks:
        no_chained_commands = False
        canonical_commands = False
    elif args.shell:
        no_chained_commands = not args.no_chained_commands
        canonical_commands = not args.no_canonical_commands
    else:
        no_chained_commands = None
        canonical_commands = None

    root = _common.find_root()
    sys.exit(
        run(
            root,
            args.shell,
            no_chained_commands=no_chained_commands,
            canonical_commands=canonical_commands,
            assume_yes=args.yes,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
