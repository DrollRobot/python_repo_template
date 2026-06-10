"""Start a fresh git history: delete .git and run ``git init``.

DESTRUCTIVE. This permanently removes the cloned template's git history,
branches, tags, and remote configuration by deleting the ``.git`` directory,
then initializes a brand-new empty repository in its place. Use this once, after
you have customized the template, to begin your project's own history.

Usage:
    uv run scripts/template_setup/reinit_git.py
    uv run scripts/template_setup/reinit_git.py --branch main
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import _common


def _git_output(git: str, args: list[str], cwd: Path) -> str | None:
    """Run a read-only git command and return its stripped stdout.

    Args:
        git: Full path to the git executable.
        args: Arguments to pass to git.
        cwd: Working directory.

    Returns:
        The command's stdout (stripped), or ``None`` if git exited non-zero.
    """
    result = subprocess.run(  # noqa: S603  (git path resolved via shutil.which)
        [git, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def run(root: Path, *, branch: str = "main", assume_yes: bool = False) -> int:
    """Delete ``.git`` and initialize a new repository.

    Args:
        root: Project root directory.
        branch: Name for the initial branch of the new repository.
        assume_yes: Skip the confirmation prompt.

    Returns:
        Process exit code (0 on success, 1 if aborted or git is missing).
    """
    _common.section("Re-initialize git")

    git = shutil.which("git")
    if git is None:
        print("  ERROR: git is not on PATH; cannot reinitialize.")
        return 1

    git_dir = root / ".git"
    if git_dir.is_dir():
        origin = _git_output(git, ["remote", "get-url", "origin"], root)
        head = _git_output(git, ["rev-parse", "--abbrev-ref", "HEAD"], root)
        print("  This will PERMANENTLY delete the current git history:")
        _common.info("Current branch", head or "(unknown)")
        _common.info("origin remote", origin or "(none)")
    else:
        print("  No .git directory present; will initialize a new repository.")

    print(f"  Then run: git init -b {branch}")

    print()
    if not _common.confirm("Delete .git and start a fresh history?", assume_yes=assume_yes):
        print("  Aborted; nothing changed.")
        return 1

    if git_dir.is_dir():
        shutil.rmtree(git_dir, onexc=_common.force_remove)
        print("  Removed .git/")

    subprocess.run([git, "init", "-b", branch], cwd=root, check=True)  # noqa: S603
    print("\n  New repository initialized.")
    print('  Next: git add -A && git commit -m "Initial commit"')
    return 0


def main() -> None:
    """Parse arguments and run the git re-initialization."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--branch", default="main", help="Initial branch name (default: main).")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt.")
    args = parser.parse_args()

    root = _common.find_root()
    sys.exit(run(root, branch=args.branch, assume_yes=args.yes))


if __name__ == "__main__":
    main()
