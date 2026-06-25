"""Unit tests for the pure helpers in scripts/remove_worktree.py.

The scripts folder is not a package, so the module is imported by adding the
folder to sys.path, mirroring how the scripts import their shared _cli module.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# mypy cannot see the sys.path insertion above, so it cannot resolve the module.
from remove_worktree import (  # type: ignore[import-not-found]
    open_worktree_slugs,
    parse_choice,
    parse_worktrees,
    slug_arg,
)

MAIN = ("C:/dev/repo", "refs/heads/develop")


def porcelain(*blocks: str) -> str:
    """Join porcelain blocks with the blank line git puts between them."""
    return "\n\n".join(blocks) + "\n"


def test_parse_worktrees_empty_output() -> None:
    """No output (not a repo, or stripped) yields no entries."""
    assert parse_worktrees("") == []


def test_parse_worktrees_main_only() -> None:
    """A single block yields one (path, branch) pair."""
    text = porcelain("worktree C:/dev/repo\nHEAD abc123\nbranch refs/heads/develop")
    assert parse_worktrees(text) == [MAIN]


def test_parse_worktrees_multiple_blocks() -> None:
    """Each block becomes one entry, in listing order (main worktree first)."""
    text = porcelain(
        "worktree C:/dev/repo\nHEAD abc123\nbranch refs/heads/develop",
        "worktree C:/dev/repo-wt/issue-42\nHEAD def456\nbranch refs/heads/wt/issue-42",
    )
    assert parse_worktrees(text) == [
        MAIN,
        ("C:/dev/repo-wt/issue-42", "refs/heads/wt/issue-42"),
    ]


def test_parse_worktrees_detached_head_has_no_branch() -> None:
    """A detached worktree is reported with branch ``None``."""
    text = porcelain(
        "worktree C:/dev/repo\nHEAD abc123\nbranch refs/heads/develop",
        "worktree C:/dev/repo-wt/pinned\nHEAD def456\ndetached",
    )
    assert parse_worktrees(text) == [MAIN, ("C:/dev/repo-wt/pinned", None)]


def test_parse_worktrees_ignores_extra_attribute_lines() -> None:
    """Lines like 'locked' or 'prunable' do not disturb the pairs."""
    text = porcelain(
        "worktree C:/dev/repo\nHEAD abc123\nbranch refs/heads/develop",
        "worktree C:/dev/repo-wt/issue-42\nHEAD def456\n"
        "branch refs/heads/wt/issue-42\nlocked reason",
    )
    assert parse_worktrees(text)[1] == ("C:/dev/repo-wt/issue-42", "refs/heads/wt/issue-42")


def test_open_worktree_slugs_skips_main_worktree() -> None:
    """The main worktree is never offered, even if its branch matched the prefix."""
    worktrees = [("C:/dev/repo", "refs/heads/wt/oops")]
    assert open_worktree_slugs(worktrees, "wt/") == []


def test_open_worktree_slugs_matches_prefix_only() -> None:
    """Only linked worktrees on a prefixed branch are offered; slug drops the prefix."""
    worktrees = [
        MAIN,
        ("C:/dev/repo-wt/issue-42", "refs/heads/wt/issue-42"),
        ("C:/dev/other", "refs/heads/feature/login"),
        ("C:/dev/repo-wt/pinned", None),
    ]
    assert open_worktree_slugs(worktrees, "wt/") == [("issue-42", "C:/dev/repo-wt/issue-42")]


def test_open_worktree_slugs_keeps_slashes_in_slug() -> None:
    """A slug that itself contains slashes survives the prefix strip."""
    worktrees = [MAIN, ("C:/dev/repo-wt/fix-login", "refs/heads/wt/fix/login")]
    assert open_worktree_slugs(worktrees, "wt/") == [("fix/login", "C:/dev/repo-wt/fix-login")]


def test_open_worktree_slugs_honours_custom_prefix() -> None:
    """A different branch prefix changes which branches are offered."""
    worktrees = [MAIN, ("C:/dev/repo-wt/issue-42", "refs/heads/wt/issue-42")]
    assert open_worktree_slugs(worktrees, "agent/") == []


def test_parse_choice_accepts_numbers_in_range() -> None:
    """Numbers from 1 to count are returned as ints."""
    assert parse_choice("1", 3) == 1
    assert parse_choice("3", 3) == 3


def test_parse_choice_rejects_out_of_range() -> None:
    """0, negatives, and numbers past the end are rejected."""
    assert parse_choice("0", 3) is None
    assert parse_choice("4", 3) is None
    assert parse_choice("-1", 3) is None


def test_parse_choice_rejects_non_numbers() -> None:
    """Anything that is not an integer is rejected."""
    assert parse_choice("", 3) is None
    assert parse_choice("x", 3) is None
    assert parse_choice("1.5", 3) is None


# slug_arg is duplicated byte-for-byte in new_worktree.py; these cover both.


def test_slug_arg_accepts_plain_slug() -> None:
    """A simple slug passes through unchanged."""
    assert slug_arg("issue-42") == "issue-42"


def test_slug_arg_accepts_internal_slash() -> None:
    """An internal slash is allowed (becomes a nested branch like wt/fix/login)."""
    assert slug_arg("fix/login") == "fix/login"


def test_slug_arg_rejects_disallowed_characters() -> None:
    """Characters outside the allowed set are rejected."""
    with pytest.raises(argparse.ArgumentTypeError):
        slug_arg("has space")


def test_slug_arg_rejects_dotdot() -> None:
    """A '..' sequence (path traversal) is rejected."""
    with pytest.raises(argparse.ArgumentTypeError):
        slug_arg("a..b")


@pytest.mark.parametrize("bad", ["/login", "login/", "fix//login"])
def test_slug_arg_rejects_edge_slashes(bad: str) -> None:
    """Leading, trailing, or doubled slashes would build malformed branch names."""
    with pytest.raises(argparse.ArgumentTypeError):
        slug_arg(bad)
