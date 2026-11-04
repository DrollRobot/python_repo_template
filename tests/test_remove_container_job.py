"""Unit tests for scripts/template_setup/remove_container_job.py.

The template_setup folder is not a package, so the module is imported by
adding the folder to sys.path, mirroring how the setup scripts import their
shared _common module.

This file is itself a dev-script test: cleanup.py matches it to
scripts/template_setup/remove_container_job.py and deletes it along with the
rest of the scaffolding, so it never lingers in a project started from the
template.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "template_setup"))

import remove_container_job

_ROOT = Path(__file__).resolve().parent.parent

_FILES = (
    "Dockerfile",
    ".dockerignore",
    ".github/workflows/image.yml",
    "scripts/container_job.py",
    "scripts/container_job.example.toml",
    "tests/test_container_job.py",
)


def _make_project(root: Path, *relpaths: str) -> list[Path]:
    """Create the named files under ``root`` and return their paths."""
    paths: list[Path] = []
    for relpath in relpaths:
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub\n", encoding="utf-8")
        paths.append(path)
    return paths


@pytest.mark.unit
def test_plan_deletions_is_empty_when_the_files_are_gone(tmp_path: Path) -> None:
    assert remove_container_job.plan_deletions(tmp_path) == []


@pytest.mark.unit
def test_every_file_it_deletes_ships_in_the_template() -> None:
    """A name typo here would leave that file behind in every declining project."""
    assert list(_FILES) == remove_container_job._DELETE
    assert [relpath for relpath in _FILES if not (_ROOT / relpath).is_file()] == []


@pytest.mark.integration
@pytest.mark.functional
def test_run_dry_run_deletes_nothing(tmp_path: Path) -> None:
    paths = _make_project(tmp_path, *_FILES)

    assert remove_container_job.run(tmp_path, assume_yes=True, dry_run=True) == 0
    assert all(path.exists() for path in paths)


@pytest.mark.integration
@pytest.mark.functional
def test_run_deletes_every_container_job_file_and_nothing_else(tmp_path: Path) -> None:
    paths = _make_project(tmp_path, *_FILES)
    (keep,) = _make_project(tmp_path, ".github/workflows/ci.yml")

    assert remove_container_job.run(tmp_path, assume_yes=True) == 0
    assert not any(path.exists() for path in paths)
    assert keep.exists()
