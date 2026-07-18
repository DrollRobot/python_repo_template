"""Unit tests for the pure helpers in scripts/template_setup/remove_keyvault.py.

The template_setup folder is not a package, so the module is imported by
adding the folder to sys.path, mirroring how the setup scripts import their
shared _common module.

This file is itself a dev-script test: cleanup.py matches it to
scripts/template_setup/remove_keyvault.py and deletes it along with the rest
of the scaffolding, so it never lingers in a project started from the template.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "template_setup"))

import remove_keyvault

PYPROJECT = (
    "[dependency-groups]\n"
    "docs = [\n"
    '    "mkdocs>=1.6,<2",\n'
    "]\n"
    "keyvault = [\n"
    "    # Azure KeyVault backend (opt-in). To remove: delete this group, and the\n"
    "    # dev include-group below\n"
    '    "azure-identity>=1.17,<2",\n'
    '    "azure-keyvault-secrets>=4.8,<5",\n'
    "]\n"
    "test = [\n"
    '    "pytest>=9.0,<10",\n'
    "]\n"
    "dev = [\n"
    '    {include-group = "docs"},\n'
    '    {include-group = "test"},\n'
    '    {include-group = "keyvault"},\n'
    '    "debugpy>=1.8,<2",\n'
    '    "keyring>=25.0,<26",  # credentials feature (keyring backend)\n'
    "]\n"
)

ENV_EXAMPLE = (
    "# .env.example\n"
    "\n"
    "# --- Credentials backend ---------------------------------------------------\n"
    "# CREDENTIAL_BACKEND=keyring\n"
    "\n"
    "# --- Azure KeyVault only -- delete this block if CREDENTIAL_BACKEND=keyring ---\n"
    "# KEYVAULT_TENANT_ID=\n"
    "# KEYVAULT_URL=\n"
)

README_FULL = (
    "| Feature | Delete | Remove from config |\n"
    "| --- | --- | --- |\n"
    "| **Keyring backend** | `tests/_keyring.py` | the `keyring` dep |\n"
    "| **Azure KeyVault backend** | `tests/_keyvault.py` | the `keyvault` group |\n"
    "| **Release script** | `push_new_tag_to_main.py` | -- |\n"
)


@pytest.mark.unit
def test_strip_pyproject_removes_group_and_include() -> None:
    """The keyvault group and its dev include are removed; siblings stay."""
    new_text, removed = remove_keyvault._strip_pyproject(PYPROJECT)
    assert "keyvault" not in new_text
    assert "azure" not in new_text
    assert "keyvault = [" not in new_text
    assert '{include-group = "keyvault"}' not in new_text
    # The other groups, includes, and the unrelated keyring line are intact.
    assert "docs = [" in new_text
    assert '{include-group = "docs"},' in new_text
    assert '{include-group = "test"},' in new_text
    assert '"keyring>=25.0,<26",' in new_text
    assert removed


@pytest.mark.unit
def test_strip_env_example_removes_keyvault_block_leaves_credentials_block() -> None:
    """The KeyVault-only block is removed; the shared credentials block stays."""
    new_text, _removed = remove_keyvault._strip_env_example(ENV_EXAMPLE)
    assert "KEYVAULT" not in new_text
    assert "Azure KeyVault only" not in new_text
    assert "# --- Credentials backend ---------------------------------------------------" in (
        new_text
    )
    assert "CREDENTIAL_BACKEND=keyring" in new_text


@pytest.mark.unit
def test_strip_readme_removes_table_row() -> None:
    """Only the Azure KeyVault backend row is removed; the others stay."""
    new_text, _removed = remove_keyvault._strip_readme(README_FULL)
    assert "Azure KeyVault backend" not in new_text
    assert "| **Keyring backend**" in new_text
    assert "| **Release script**" in new_text


@pytest.mark.unit
def test_plan_deletions_lists_only_existing_paths(tmp_path: Path) -> None:
    """Only the KeyVault backend path is scheduled for deletion when it exists."""
    assert remove_keyvault.plan_deletions(tmp_path) == []
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "_keyvault.py").write_text("", encoding="utf-8")
    deletions = remove_keyvault.plan_deletions(tmp_path)
    assert {path.name for path in deletions} == {"_keyvault.py"}


@pytest.mark.integration
@pytest.mark.functional
def test_run_returns_zero_when_nothing_matches(tmp_path: Path) -> None:
    """An empty project has no KeyVault artifacts and is a no-op success."""
    assert remove_keyvault.run(tmp_path, assume_yes=True) == 0


@pytest.mark.integration
@pytest.mark.functional
def test_run_dry_run_changes_nothing(tmp_path: Path) -> None:
    """A dry run neither deletes files nor rewrites them."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    keyvault_file = tmp_path / "tests" / "_keyvault.py"
    keyvault_file.write_text("", encoding="utf-8")

    assert remove_keyvault.run(tmp_path, assume_yes=True, dry_run=True) == 0
    assert keyvault_file.exists()
    assert pyproject.read_text(encoding="utf-8") == PYPROJECT


@pytest.mark.integration
@pytest.mark.functional
def test_run_deletes_and_rewrites(tmp_path: Path) -> None:
    """A real run deletes the artifacts and strips the references."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    keyvault_file = tmp_path / "tests" / "_keyvault.py"
    keyvault_file.write_text("", encoding="utf-8")

    assert remove_keyvault.run(tmp_path, assume_yes=True) == 0
    assert not keyvault_file.exists()
    assert "keyvault" not in pyproject.read_text(encoding="utf-8")
