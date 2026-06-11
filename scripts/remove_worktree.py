"""Interactively delete a finished git worktree created by new_worktree.py.

The inverse of new_worktree.py, and the final step of the worktree lifecycle:

    new_worktree  ->  (work, commit, complete_worktree)  ->  remove_worktree

Run this once the work is done: the PR opened from the worktree's 'wt/<slug>'
branch has been merged into develop and the worktree is no longer needed.

WARNING: this permanently deletes the worktree directory and its local
branch. It walks through the teardown one step at a time, showing what is
about to happen and prompting for confirmation (y/n) before each action;
answering 'n' stops without taking the remaining steps. The output of every
git command is shown.

Steps:
  1. Refresh develop  - git switch develop (if needed) + git pull --ff-only origin develop
  2. Remove worktree  - git worktree remove --force <path>
  3. Delete branch    - git branch -D wt/<slug>
  4. Prune stale refs - git fetch --prune

Paths and names are derived exactly as new_worktree.py derives them, so the
same slug that created a worktree will clean it up. Every step runs against
the main worktree, so this is safe to run even from inside the worktree
being removed (git refuses to remove the worktree you are standing in).

Pass -y/--yes to answer every prompt with 'y' for non-interactive use.

Usage:
    python scripts/remove_worktree.py issue-42
    python scripts/remove_worktree.py fix/login
    python scripts/remove_worktree.py issue-42 -y

Env overrides mirror new_worktree.py: WT_HOME (parent dir for worktrees),
WT_PREFIX (branch prefix, default "wt/").
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import _cli as cli


def slug_arg(value: str) -> str:
    """Validate the worktree slug (same rules as new_worktree.py).

    Args:
        value: Slug from the command line.

    Returns:
        The validated slug.

    Raises:
        argparse.ArgumentTypeError: If the slug contains disallowed characters.
    """
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", value):
        raise argparse.ArgumentTypeError(
            "slug may only contain letters, digits, and . _ / - characters"
        )
    if ".." in value:
        raise argparse.ArgumentTypeError("slug may not contain '..'")
    return value


def parse_args() -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        description="Delete a finished git worktree created by new_worktree.py."
    )
    parser.add_argument("slug", type=slug_arg, help="the same slug passed to new_worktree.py")
    parser.add_argument(
        "base",
        nargs="?",
        default=os.environ.get("WT_BASE") or "develop",
        help="integration branch to refresh, that the PR was merged into "
        "(default: $WT_BASE, then develop)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="assume 'yes' to every confirmation prompt (non-interactive)",
    )
    return parser.parse_args()


def same_path(a: str, b: str) -> bool:
    """Compare two filesystem paths without requiring either to exist.

    Normalises slash direction and trailing separators; case-insensitive on
    Windows (via ``os.path.normcase``).

    Args:
        a: First path.
        b: Second path.

    Returns:
        ``True`` if the paths refer to the same location.
    """
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def main() -> None:
    """Run the interactive worktree teardown flow."""
    args = parse_args()
    cli.set_assume_yes(args.yes)

    # --- what this does (shown up front, before anything is touched) ------------

    intro = (
        "  The inverse of new_worktree.py. Run it once the work is done: the PR",
        "  opened from this worktree's wt/<slug> branch has been merged into",
        "  develop, and the worktree is no longer needed.",
    )
    print()
    print(f"{cli.CYAN}remove_worktree - tear down a finished worktree{cli.RESET}")
    print()
    for line in intro:
        print(f"{cli.GRAY}{line}{cli.RESET}")
    print()
    cli.warn("  WARNING: this DELETES the worktree directory and its local branch.")
    cli.warn("  It runs one step at a time and asks before each; answer n to stop.")

    # --- resolve paths (mirrors new_worktree.py) -------------------------------

    cli.section("Cleanup setup")

    # Resolve the MAIN worktree, not whichever worktree we happen to be standing
    # in: `git worktree list` always reports the main worktree first. Deriving
    # names from it means this works even when run from inside the worktree we
    # remove.
    porcelain = cli.capture(["git", "worktree", "list", "--porcelain"])
    worktree_paths = [
        line.removeprefix("worktree ")
        for line in porcelain.splitlines()
        if line.startswith("worktree ")
    ]
    if not worktree_paths:
        cli.die("Could not determine the main worktree (are you inside a git repository?).")
    main_repo = worktree_paths[0]

    repo_name = Path(main_repo).name
    prefix = os.environ.get("WT_PREFIX") or "wt/"
    branch = f"{prefix}{args.slug}"
    dir_slug = args.slug.replace("/", "-")
    wt_home = Path(os.environ.get("WT_HOME") or Path(main_repo).parent / f"{repo_name}-wt")
    wt_path = wt_home / dir_slug

    cli.info("Slug", args.slug)
    cli.info("Branch", branch)
    cli.info("Worktree", str(wt_path))
    cli.info("Integration branch", args.base)
    cli.info("Main worktree", main_repo)

    # --- preflight: figure out what actually still exists -----------------------

    wt_registered = any(same_path(p, str(wt_path)) for p in worktree_paths)
    if not wt_registered:
        cli.warn(f"  Note: no registered worktree at '{wt_path}' - remove step will be skipped.")

    branch_exists = bool(cli.capture(["git", "branch", "--list", branch]))
    if not branch_exists:
        cli.warn(f"  Note: branch '{branch}' does not exist - delete-branch step will be skipped.")

    if not wt_registered and not branch_exists:
        print()
        cli.success(f"Nothing to clean up for slug '{args.slug}'.")
        sys.exit(0)

    # Operate from the main worktree for every step. This guarantees the
    # worktree being removed is never the "current" one (git refuses to remove
    # that) and moves us out of it if we happened to be inside it.
    cli.echo(f"cd {main_repo}")
    os.chdir(main_repo)

    # --- step 1: refresh the integration branch ---------------------------------

    cli.section(f"Step: refresh '{args.base}'")
    current = cli.capture(["git", "branch", "--show-current"])
    current_label = current or "(detached HEAD)"
    cli.info("Current branch", current_label)
    if current != args.base:
        cli.step(f"Switch from '{current_label}' to '{args.base}'?")
        cli.run(["git", "switch", args.base])
    cli.step(f"Pull '{args.base}' from origin (fast-forward only)?")
    cli.run(["git", "pull", "--ff-only", "origin", args.base])

    # --- step 2: remove the worktree --------------------------------------------

    if wt_registered:
        cli.section("Step: remove worktree")
        # --force: the worktree carries untracked/ignored files (generated
        # .code-workspace, .vscode and .env links, .venv) that a plain remove
        # would refuse to discard.
        cli.step(f"DELETE the worktree directory at '{wt_path}'?")
        cli.run(["git", "worktree", "remove", "--force", str(wt_path)])

    # --- step 3: delete the branch ----------------------------------------------

    if branch_exists:
        cli.section("Step: delete branch")
        # -D (force): a squash- or rebase-merged PR leaves the local branch
        # looking unmerged to git, so -d would refuse to delete it.
        cli.step(f"Force-delete local branch '{branch}'?")
        cli.run(["git", "branch", "-D", branch])

    # --- step 4: prune stale remote-tracking refs -------------------------------

    cli.section("Step: prune")
    cli.step("Fetch and prune deleted remote branches?")
    cli.run(["git", "fetch", "--prune"])

    # --- done -------------------------------------------------------------------

    cli.section("Done")
    cli.success(f"  Removed worktree '{args.slug}'.")
    cli.info("Current branch", cli.capture(["git", "branch", "--show-current"]))
    cli.info("Location", os.getcwd())


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(130)
