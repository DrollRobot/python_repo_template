"""Unit tests for the pure helpers in scripts/complete_worktree.py.

The scripts folder is not a package, so the module is imported by adding the
folder to sys.path, mirroring how the scripts import their shared _cli module.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# mypy cannot see the sys.path insertion above, so it cannot resolve the module.
from complete_worktree import (  # type: ignore[import-not-found]
    dirty_status_lines,
    notes_ref,
    parse_pr_note,
)


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


def test_parse_pr_note_with_front_matter() -> None:
    """base/title front-matter is recovered and the body excludes the separator."""
    note = "base: develop\ntitle: feat: add SSO\n---\nBody line 1\nBody line 2"
    assert parse_pr_note(note) == ("develop", "feat: add SSO", "Body line 1\nBody line 2")


def test_parse_pr_note_without_separator_is_all_body() -> None:
    """A note with no '---' separator is treated entirely as the body."""
    note = "just a plain body\nwith two lines"
    assert parse_pr_note(note) == (None, None, note)


def test_parse_pr_note_missing_fields_default_to_none() -> None:
    """Front-matter present but partial: absent fields come back as None."""
    assert parse_pr_note("base: develop\n---\nbody") == ("develop", None, "body")
    assert parse_pr_note("title: just a title\n---\nbody") == (None, "just a title", "body")


def test_parse_pr_note_empty_body_after_separator() -> None:
    """A separator with nothing after it yields an empty body, not an error."""
    assert parse_pr_note("base: develop\ntitle: x\n---") == ("develop", "x", "")


def test_parse_pr_note_strips_field_whitespace() -> None:
    """Extra spaces after the field colon are trimmed."""
    assert parse_pr_note("base:   develop  \n---\nbody") == ("develop", None, "body")


def test_notes_ref_uses_per_slug_suffix() -> None:
    """The note ref is namespaced per slug so concurrent PRs never collide."""
    assert notes_ref("issue-42") == "pr-body-issue-42"


def test_notes_ref_flattens_slashes() -> None:
    """A slug with slashes stays a single ref path segment."""
    assert notes_ref("fix/login") == "pr-body-fix-login"
