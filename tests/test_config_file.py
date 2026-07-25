"""Unit tests for config.toml reading and validation in config/file.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from python_repo_template.config import file as config_file
from python_repo_template.config.schema import ConfigError
from tests._config_test_object import ConfigTestObject

# Version of this test module. It ships to projects generated from this
# template (cleanup.py keeps it: no script or hook shares its name), so bump
# on every change to let scripts/compare_to_template.py flag stale copies.
__version__ = "1.0.0"

pytestmark = pytest.mark.unit


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _load(tmp_path: Path, text: str) -> tuple[dict[str, Any], Path]:
    path = _write(tmp_path, text)
    document = config_file.read_config(path)
    assert document is not None
    return document, path


# --- read_config ---------------------------------------------------------------------


def test_read_config_missing_file_returns_none(tmp_path: Path) -> None:
    assert config_file.read_config(tmp_path / "config.toml") is None


def test_read_config_malformed_toml_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "name = ")
    with pytest.raises(ConfigError, match="Malformed TOML"):
        config_file.read_config(path)


# --- validate_config -----------------------------------------------------------------


def test_validate_accepts_full_valid_document(tmp_path: Path) -> None:
    document, path = _load(
        tmp_path,
        """
        name = "top"
        credential_backend = "keyring"
        default_profile = "a"

        [profiles.a]
        name = "prof"
        count = 7
        credential_backend = "keyvault"
        keyvault_url = "https://kv.example.invalid/"
        """,
    )
    config_file.validate_config(document, ConfigTestObject, path)


def test_validate_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    document, path = _load(tmp_path, 'nmae = "typo"\n')
    with pytest.raises(ConfigError, match="Unknown key 'nmae'"):
        config_file.validate_config(document, ConfigTestObject, path)


def test_validate_rejects_unknown_profile_key(tmp_path: Path) -> None:
    document, path = _load(tmp_path, '[profiles.a]\nnmae = "typo"\n')
    with pytest.raises(ConfigError, match=r"Unknown key 'nmae' in profile 'profiles\.a'"):
        config_file.validate_config(document, ConfigTestObject, path)


@pytest.mark.parametrize(
    "text",
    [
        'token = "leaked"\n',
        '[profiles.a]\ntoken = "leaked"\n',
    ],
    ids=["top-level", "profile"],
)
def test_validate_rejects_secret_values_in_file(tmp_path: Path, text: str) -> None:
    document, path = _load(tmp_path, text)
    with pytest.raises(ConfigError, match=r"Secrets must never be stored in config.toml"):
        config_file.validate_config(document, ConfigTestObject, path)


def test_validate_rejects_non_string_default_profile(tmp_path: Path) -> None:
    document, path = _load(tmp_path, "default_profile = 3\n")
    with pytest.raises(ConfigError, match=r"'default_profile'.*must be a string"):
        config_file.validate_config(document, ConfigTestObject, path)


def test_validate_rejects_non_table_profiles(tmp_path: Path) -> None:
    document, path = _load(tmp_path, 'profiles = "oops"\n')
    with pytest.raises(ConfigError, match=r"'profiles'.*must be a table"):
        config_file.validate_config(document, ConfigTestObject, path)


def test_validate_rejects_non_table_profile_entry(tmp_path: Path) -> None:
    document, path = _load(tmp_path, '[profiles]\na = "oops"\n')
    with pytest.raises(ConfigError, match=r"Profile 'profiles\.a'.*must be a table"):
        config_file.validate_config(document, ConfigTestObject, path)


# --- profile_table -------------------------------------------------------------------


def test_profile_table_none_returns_empty(tmp_path: Path) -> None:
    document, path = _load(tmp_path, 'name = "top"\n')
    assert config_file.profile_table(document, None, path) == {}


def test_profile_table_returns_selected_profile(tmp_path: Path) -> None:
    document, path = _load(tmp_path, '[profiles.a]\nname = "prof"\n')
    assert config_file.profile_table(document, "a", path) == {"name": "prof"}


def test_profile_table_unknown_profile_lists_available(tmp_path: Path) -> None:
    document, path = _load(tmp_path, "[profiles.a]\n[profiles.b]\n")
    with pytest.raises(ConfigError, match=r"Profile 'c' not found.*a, b"):
        config_file.profile_table(document, "c", path)


def test_profile_table_no_profiles_at_all(tmp_path: Path) -> None:
    document, path = _load(tmp_path, 'name = "top"\n')
    with pytest.raises(ConfigError, match=r"Profile 'a' not found.*none defined"):
        config_file.profile_table(document, "a", path)
