"""Unit tests for the pure helpers in scripts/complete_worktree.py.

The scripts folder is not a package, so the module is imported by adding the
folder to sys.path, mirroring how the scripts import their shared _cli module.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# mypy cannot see the sys.path insertion above, so it cannot resolve the module.
from complete_worktree import dirty_status_lines  # type: ignore[import-not-found]


def test_clean_tree_returns_no_lines() -> None:
    """An empty status means a clean tree regardless of the exempt path."""
    assert dirty_status_lines([], "PR.md") == []
    assert dirty_status_lines([], None) == []


def test_blank_lines_are_ignored() -> None:
    """Blank or whitespace-only lines do not count as dirty entries."""
    assert dirty_status_lines(["", "   "], None) == []


def test_untracked_exempt_file_is_filtered() -> None:
    """An untracked PR body file does not make the tree dirty."""
    assert dirty_status_lines(["?? PR.md"], "PR.md") == []


def test_modified_exempt_file_is_filtered() -> None:
    """A tracked-but-modified PR body file does not make the tree dirty."""
    assert dirty_status_lines([" M PR.md"], "PR.md") == []


def test_other_changes_are_reported() -> None:
    """Changes to any non-exempt file are returned verbatim."""
    lines = [" M src/app.py", "?? PR.md", "?? notes.txt"]
    assert dirty_status_lines(lines, "PR.md") == [" M src/app.py", "?? notes.txt"]


def test_no_exempt_path_keeps_everything() -> None:
    """With no exempt path (body file outside the worktree), nothing is filtered."""
    assert dirty_status_lines(["?? PR.md"], None) == ["?? PR.md"]


def test_exempt_path_is_compared_as_posix_relative_path() -> None:
    """The exempt path must match git's repo-relative POSIX path exactly."""
    assert dirty_status_lines(["?? docs/PR.md"], "PR.md") == ["?? docs/PR.md"]
    assert dirty_status_lines(["?? docs/PR.md"], "docs/PR.md") == []


def test_quoted_porcelain_path_matches_exempt() -> None:
    """Git quotes unusual paths; the quotes are stripped before comparison."""
    assert dirty_status_lines(['?? "PR.md"'], "PR.md") == []
