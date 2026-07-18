"""Unit tests for the pure helpers in scripts/template_setup/remove_credentials.py.

The template_setup folder is not a package, so the module is imported by
adding the folder to sys.path, mirroring how the setup scripts import their
shared _common module.

This file is itself a dev-script test: cleanup.py matches it to
scripts/template_setup/remove_credentials.py and deletes it along with the
rest of the scaffolding, so it never lingers in a project started from the
template.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "template_setup"))

import remove_credentials

ENV_EXAMPLE = (
    "# =============================================================================\n"
    "# TEMPLATE SETUP NOTES -- remove this block - FIXME\n"
    "# =============================================================================\n"
    "\n"
    "# .env.example\n"
    "\n"
    "# --- Credentials backend ---------------------------------------------------\n"
    "# Default is keyring (OS credential store, no Azure needed). Flip to keyvault\n"
    "# to read secrets from Azure KeyVault instead -- no code change -- which also\n"
    "# needs the KEYVAULT_* vars below and the `keyvault` dependency group.\n"
    "# CREDENTIAL_BACKEND=keyring\n"
    "\n"
    "# Which credential type to use: user_pass | cert_thumbprint | service_principal\n"
    "# CREDENTIAL_TYPE=user_pass\n"
    "\n"
    "# Key *names* (not the secrets). For the keyring backend, run\n"
    "#   uv run scripts/setup_credentials.py --type <type>\n"
    "# once to store the actual secret values under these names.\n"
    "\n"
    "# --- user_pass ---\n"
    "# USERNAME_KEY=python_repo_template_username\n"
    "# PASSWORD_KEY=python_repo_template_password\n"
    "\n"
    "# --- cert_thumbprint ---\n"
    "# CERT_THUMBPRINT_KEY=python_repo_template_cert_thumbprint\n"
    "\n"
    "# --- service_principal ---\n"
    "# TENANT_ID_KEY=python_repo_template_tenant_id\n"
    "# CLIENT_ID_KEY=python_repo_template_client_id\n"
    "# CLIENT_SECRET_KEY=python_repo_template_client_secret\n"
    "\n"
    "# --- Azure KeyVault only -- delete this block if CREDENTIAL_BACKEND=keyring ---\n"
    "# KEYVAULT_TENANT_ID=\n"
    "# KEYVAULT_URL=\n"
)

CONFTEST = (
    "# ---------------------------------------------------------------------------\n"
    "# FIXME: add project-specific fixtures below\n"
    "# ---------------------------------------------------------------------------\n"
    "# Example:\n"
    "#\n"
    '# @pytest.fixture(scope="session")\n'
    "# def client(test_settings: dict[str, str]) -> MyClient:\n"
    '#     """Return a client configured for live tests."""\n'
    '#     return MyClient(api_key=test_settings["MY_API_KEY"])\n'
    "#\n"
    "# Credentials via the configured backend (CREDENTIAL_BACKEND in .env: keyring by\n"
    "# default, keyvault when you flip it). The call is backend-agnostic:\n"
    "#\n"
    "# from tests._bootstrap import get_user_pass\n"
    "#\n"
    '# @pytest.fixture(scope="session")\n'
    "# def credentials(test_settings: dict[str, str]) -> tuple[str, str]:\n"
    '#     """Username/password from keyring (dev) or KeyVault (prod) per .env."""\n'
    "#     return get_user_pass(test_settings)\n"
)

README_FULL = (
    "| Feature | Delete | Remove from config |\n"
    "| --- | --- | --- |\n"
    "| **Keyring backend** | `tests/_keyring.py` | the `keyring` dep |\n"
    "| **Azure KeyVault backend** | `tests/_keyvault.py` | the `keyvault` group |\n"
    "| **Credentials dispatcher** | `tests/_bootstrap.py` | the credentials block |\n"
    "| **Release script** | `push_new_tag_to_main.py` | -- |\n"
)


@pytest.mark.unit
def test_strip_env_example_removes_credentials_block_leaves_keyvault_block() -> None:
    """The shared credentials block is removed; the KeyVault-only block is untouched."""
    new_text, removed = remove_credentials._strip_env_example(ENV_EXAMPLE)
    assert "Credentials backend" not in new_text
    assert "CREDENTIAL_TYPE" not in new_text
    assert "USERNAME_KEY" not in new_text
    # Not this script's job -- remove_keyvault.py owns this block.
    assert "Azure KeyVault only" in new_text
    assert "KEYVAULT_URL=" in new_text
    assert removed


@pytest.mark.unit
def test_strip_conftest_removes_only_credentials_example_keeps_client_example() -> None:
    """Only the credentials-example fixture is removed; the generic example stays."""
    new_text, removed = remove_credentials._strip_conftest(CONFTEST)
    assert "get_user_pass" not in new_text
    assert "_bootstrap" not in new_text
    assert "Credentials via the configured backend" not in new_text
    # The generic client example is untouched.
    assert "def client(test_settings" in new_text
    assert 'return MyClient(api_key=test_settings["MY_API_KEY"])' in new_text
    # No dangling lone "#" separator left behind.
    assert not new_text.rstrip("\n").endswith("#")
    assert removed


@pytest.mark.unit
def test_strip_readme_removes_table_row() -> None:
    """Only the credentials-dispatcher row is removed; the others stay."""
    new_text, _removed = remove_credentials._strip_readme(README_FULL)
    assert "Credentials dispatcher" not in new_text
    assert "| **Keyring backend**" in new_text
    assert "| **Azure KeyVault backend**" in new_text
    assert "| **Release script**" in new_text


@pytest.mark.unit
def test_plan_deletions_lists_only_existing_paths(tmp_path: Path) -> None:
    """Only the dispatcher path is scheduled for deletion when it exists."""
    assert remove_credentials.plan_deletions(tmp_path) == []
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "_bootstrap.py").write_text("", encoding="utf-8")
    deletions = remove_credentials.plan_deletions(tmp_path)
    assert {path.name for path in deletions} == {"_bootstrap.py"}


@pytest.mark.integration
@pytest.mark.functional
def test_run_returns_zero_when_nothing_matches(tmp_path: Path) -> None:
    """An empty project has no dispatcher artifacts and is a no-op success."""
    assert remove_credentials.run(tmp_path, assume_yes=True) == 0


@pytest.mark.integration
@pytest.mark.functional
def test_run_dry_run_changes_nothing(tmp_path: Path) -> None:
    """A dry run neither deletes files nor rewrites them."""
    env_example = tmp_path / ".env.example"
    env_example.write_text(ENV_EXAMPLE, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    bootstrap = tmp_path / "tests" / "_bootstrap.py"
    bootstrap.write_text("", encoding="utf-8")

    assert remove_credentials.run(tmp_path, assume_yes=True, dry_run=True) == 0
    assert bootstrap.exists()
    assert env_example.read_text(encoding="utf-8") == ENV_EXAMPLE


@pytest.mark.integration
@pytest.mark.functional
def test_run_deletes_and_rewrites(tmp_path: Path) -> None:
    """A real run deletes the artifacts and strips the references."""
    env_example = tmp_path / ".env.example"
    env_example.write_text(ENV_EXAMPLE, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    bootstrap = tmp_path / "tests" / "_bootstrap.py"
    bootstrap.write_text("", encoding="utf-8")
    conftest = tmp_path / "tests" / "conftest.py"
    conftest.write_text(CONFTEST, encoding="utf-8")

    assert remove_credentials.run(tmp_path, assume_yes=True) == 0
    assert not bootstrap.exists()
    assert "Credentials backend" not in env_example.read_text(encoding="utf-8")
    assert "get_user_pass" not in conftest.read_text(encoding="utf-8")
