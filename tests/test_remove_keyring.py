"""Unit tests for the pure helpers in scripts/template_setup/remove_keyring.py.

The template_setup folder is not a package, so the module is imported by
adding the folder to sys.path, mirroring how the setup scripts import their
shared _common module.

This file is itself a dev-script test: cleanup.py matches it to
scripts/template_setup/remove_keyring.py and deletes it along with the rest
of the scaffolding, so it never lingers in a project started from the template.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "template_setup"))

import remove_keyring

PYPROJECT = (
    "dev = [\n"
    '    {include-group = "docs"},\n'
    '    {include-group = "test"},\n'
    '    {include-group = "keyvault"},\n'
    '    "debugpy>=1.8,<2",\n'
    '    "keyring>=25.0,<26",  # credentials feature (keyring backend)\n'
    '    "mypy>=2.1,<3",\n'
    "]\n"
)

README_FULL = (
    "- **ruff** for linting and formatting.\n"
    "- **keyring** for credential storage. Cross-platform. Allows never keeping secrets"
    " in the repo.\n"
    "- **pytest** for testing.\n"
    "\n"
    "| Feature | Delete | Remove from config |\n"
    "| --- | --- | --- |\n"
    "| **Keyring backend** | `tests/_keyring.py`, `scripts/setup_credentials.py`"
    " | the `keyring` dep |\n"
    "| **Release script** | `push_new_tag_to_main.py` | -- |\n"
)


@pytest.mark.unit
def test_strip_pyproject_removes_keyring_line() -> None:
    """The keyring dependency line is removed; siblings stay."""
    new_text, removed = remove_keyring._strip_pyproject(PYPROJECT)
    assert "keyring" not in new_text
    assert '"debugpy>=1.8,<2",' in new_text
    assert '"mypy>=2.1,<3",' in new_text
    assert '{include-group = "keyvault"},' in new_text
    assert removed


@pytest.mark.unit
def test_strip_readme_removes_bullet_and_table_row() -> None:
    """The keyring bullet and table row are removed; unrelated content stays."""
    new_text, _removed = remove_keyring._strip_readme(README_FULL)
    assert "keyring" not in new_text.lower()
    assert "- **ruff** for linting and formatting." in new_text
    assert "- **pytest** for testing." in new_text
    assert "| **Release script**" in new_text


@pytest.mark.unit
def test_plan_deletions_lists_only_existing_paths(tmp_path: Path) -> None:
    """Only keyring backend paths that exist on disk are scheduled for deletion."""
    assert remove_keyring.plan_deletions(tmp_path) == []
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "_keyring.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "setup_credentials.py").write_text("", encoding="utf-8")
    deletions = remove_keyring.plan_deletions(tmp_path)
    names = {path.name for path in deletions}
    assert names == {"_keyring.py", "setup_credentials.py"}


@pytest.mark.integration
@pytest.mark.functional
def test_run_returns_zero_when_nothing_matches(tmp_path: Path) -> None:
    """An empty project has no keyring artifacts and is a no-op success."""
    assert remove_keyring.run(tmp_path, assume_yes=True) == 0


@pytest.mark.integration
@pytest.mark.functional
def test_run_dry_run_changes_nothing(tmp_path: Path) -> None:
    """A dry run neither deletes files nor rewrites them."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    keyring_file = tmp_path / "tests" / "_keyring.py"
    keyring_file.write_text("", encoding="utf-8")

    assert remove_keyring.run(tmp_path, assume_yes=True, dry_run=True) == 0
    assert keyring_file.exists()
    assert pyproject.read_text(encoding="utf-8") == PYPROJECT


@pytest.mark.integration
@pytest.mark.functional
def test_run_deletes_and_rewrites(tmp_path: Path) -> None:
    """A real run deletes the artifacts and strips the references."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    keyring_file = tmp_path / "tests" / "_keyring.py"
    keyring_file.write_text("", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    creds_script = tmp_path / "scripts" / "setup_credentials.py"
    creds_script.write_text("", encoding="utf-8")

    assert remove_keyring.run(tmp_path, assume_yes=True) == 0
    assert not keyring_file.exists()
    assert not creds_script.exists()
    assert "keyring" not in pyproject.read_text(encoding="utf-8")
