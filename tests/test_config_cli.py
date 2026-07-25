"""Tests for the config CLI (init / show / set / unset / profiles / secrets).

Commands run in-process via ``cli.main([...])`` with the config directory
pointed at ``tmp_path`` and the schema swapped for the fixed test object, so
nothing here touches the real user config, the real keyring (an in-memory
fake stands in), or the repo's FIXME example fields.
"""

from __future__ import annotations

import os
from pathlib import Path

import keyring
import keyring.errors
import pytest
import tomlkit

from python_repo_template.config import cli
from python_repo_template.config.paths import CONFIG_DIR_ENV
from python_repo_template.config.schema import APP_NAME, ENV_PREFIX
from tests._config_test_object import ConfigTestObject

# Version of this test module. It ships to projects generated from this
# template (cleanup.py keeps it: no script or hook shares its name), so bump
# on every change to let scripts/compare_to_template.py flag stale copies.
__version__ = "1.0.0"

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(ENV_PREFIX):
            monkeypatch.delenv(key)


@pytest.fixture(autouse=True)
def config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _test_object_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the CLI against the fixed test object instead of the FIXME fields."""
    monkeypatch.setattr("python_repo_template.config.cli.Settings", ConfigTestObject)


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    """In-memory keyring so no test touches the host credential store."""
    store: dict[tuple[str, str], str] = {}

    def delete(service: str, key: str) -> None:
        if (service, key) not in store:
            raise keyring.errors.PasswordDeleteError(key)
        del store[(service, key)]

    def set_password(service: str, key: str, value: str) -> None:
        store[(service, key)] = value

    monkeypatch.setattr(keyring, "get_password", lambda service, key: store.get((service, key)))
    monkeypatch.setattr(keyring, "set_password", set_password)
    monkeypatch.setattr(keyring, "delete_password", delete)
    return store


def _feed_input(monkeypatch: pytest.MonkeyPatch, responses: list[str]) -> None:
    answers = iter(responses)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))


def _feed_getpass(monkeypatch: pytest.MonkeyPatch, responses: list[str]) -> None:
    answers = iter(responses)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))


def _config_text(config_dir: Path) -> str:
    return (config_dir / "config.toml").read_text(encoding="utf-8")


# --- path ----------------------------------------------------------------------------


def test_path_prints_config_location(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["path"]) == 0
    assert str(config_dir / "config.toml") in capsys.readouterr().out


# --- init ----------------------------------------------------------------------------


@pytest.mark.integration
def test_init_writes_config_and_stores_secret(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_keyring: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # name, count, ratio, flag, tags -- empty keeps the default.
    _feed_input(monkeypatch, ["n", "7", "", "", ""])
    _feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init"]) == 0

    document = tomlkit.parse(_config_text(config_dir))
    assert document["name"] == "n"
    assert document["count"] == 7
    assert "ratio" not in document  # empty input keeps the schema default
    assert "token" not in document  # secrets never reach the file
    assert fake_keyring == {(APP_NAME, "token"): "tok"}
    assert "Stored token" in capsys.readouterr().out


@pytest.mark.integration
def test_init_required_field_reprompts(monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> None:
    # First response empty for required 'name'; it must re-prompt.
    _feed_input(monkeypatch, ["", "n", "", "", "", ""])
    _feed_getpass(monkeypatch, [""])  # skip the secret
    assert cli.main(["init"]) == 0
    assert tomlkit.parse(_config_text(config_dir))["name"] == "n"


@pytest.mark.integration
def test_init_profile_scopes_secret_service(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_keyring: dict[tuple[str, str], str],
) -> None:
    _feed_input(monkeypatch, ["n", "", "", "", ""])
    _feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init", "--profile", "p"]) == 0
    document = tomlkit.parse(_config_text(config_dir))
    assert document["profiles"]["p"]["name"] == "n"
    assert fake_keyring == {(f"{APP_NAME}:p", "token"): "tok"}


def test_init_refuses_configured_target(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (config_dir / "config.toml").write_text('name = "n"\n', encoding="utf-8")
    assert cli.main(["init"]) == 1
    assert "already configured" in capsys.readouterr().err


# --- set / unset ---------------------------------------------------------------------


def test_set_writes_coerced_value(config_dir: Path) -> None:
    assert cli.main(["set", "count", "7"]) == 0
    assert tomlkit.parse(_config_text(config_dir))["count"] == 7


def test_set_rejects_secret_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["set", "token", "x"]) == 1
    err = capsys.readouterr().err
    assert "never be written to config.toml" in err
    assert "set-secret token" in err


def test_set_rejects_unknown_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["set", "nope", "x"]) == 1
    assert "Unknown option 'nope'" in capsys.readouterr().err


def test_set_unknown_profile_is_actionable(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["set", "count", "7", "--profile", "p"]) == 1
    assert "Profile 'p' not found" in capsys.readouterr().err


def test_set_preserves_comments(config_dir: Path) -> None:
    (config_dir / "config.toml").write_text('# keep me\nname = "n"\n', encoding="utf-8")
    assert cli.main(["set", "count", "7"]) == 0
    text = _config_text(config_dir)
    assert "# keep me" in text
    assert "count = 7" in text


def test_unset_removes_value_and_warns_when_required(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text('name = "n"\n', encoding="utf-8")
    assert cli.main(["unset", "name"]) == 0
    out = capsys.readouterr().out
    assert "name is required" in out
    assert "name" not in tomlkit.parse(_config_text(config_dir))


def test_unset_missing_key_fails(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (config_dir / "config.toml").write_text('name = "n"\n', encoding="utf-8")
    assert cli.main(["unset", "count"]) == 1
    assert "nothing to unset" in capsys.readouterr().err


# --- profiles ------------------------------------------------------------------------


def test_list_profiles_bare_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["list-profiles"]) == 0
    assert "No profiles defined" in capsys.readouterr().out


def test_list_profiles_marks_default(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (config_dir / "config.toml").write_text(
        'default_profile = "b"\n[profiles.a]\n[profiles.b]\n', encoding="utf-8"
    )
    assert cli.main(["list-profiles"]) == 0
    out = capsys.readouterr().out
    assert "a" in out
    assert "b  (default)" in out


def test_use_sets_default_profile(config_dir: Path) -> None:
    (config_dir / "config.toml").write_text("[profiles.a]\n", encoding="utf-8")
    assert cli.main(["use", "a"]) == 0
    assert tomlkit.parse(_config_text(config_dir))["default_profile"] == "a"


def test_use_unknown_profile_fails(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (config_dir / "config.toml").write_text("[profiles.a]\n", encoding="utf-8")
    assert cli.main(["use", "b"]) == 1
    assert "Profile 'b' not found" in capsys.readouterr().err


# --- show ----------------------------------------------------------------------------


@pytest.mark.integration
def test_show_masks_secrets_and_reports_sources(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_keyring: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (config_dir / "config.toml").write_text('name = "n"\n', encoding="utf-8")
    fake_keyring[(APP_NAME, "token")] = "sekrit-value-xyz"
    monkeypatch.setenv(ENV_PREFIX + "COUNT", "9")
    assert cli.main(["show"]) == 0
    out = capsys.readouterr().out
    assert "sekrit-value-xyz" not in out  # masked
    assert "********" in out
    assert "<- keyring" in out
    assert "<- file:top-level" in out
    assert f"<- env:{ENV_PREFIX}COUNT" in out
    assert "<- default" in out


def test_show_missing_required_is_actionable(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["show"]) == 1
    assert "Missing required configuration value(s)" in capsys.readouterr().err


# --- secrets -------------------------------------------------------------------------


def test_set_secret_stores_via_backend(
    monkeypatch: pytest.MonkeyPatch,
    fake_keyring: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 0
    assert fake_keyring == {(APP_NAME, "token"): "s3"}
    out = capsys.readouterr().out
    assert "s3" not in out  # value never echoed


def test_set_secret_uses_default_profile_service(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_keyring: dict[tuple[str, str], str],
) -> None:
    (config_dir / "config.toml").write_text(
        'default_profile = "p"\n[profiles.p]\n', encoding="utf-8"
    )
    _feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 0
    assert fake_keyring == {(f"{APP_NAME}:p", "token"): "s3"}


def test_set_secret_rejects_empty_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _feed_getpass(monkeypatch, [""])
    assert cli.main(["set-secret", "token"]) == 1
    assert "Empty value" in capsys.readouterr().err


def test_set_secret_rejects_non_secret_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["set-secret", "name"]) == 1
    assert "not a secret" in capsys.readouterr().err


def test_delete_secret_removes_stored_value(
    fake_keyring: dict[tuple[str, str], str],
) -> None:
    fake_keyring[(APP_NAME, "token")] = "s3"
    assert cli.main(["delete-secret", "token"]) == 0
    assert fake_keyring == {}


def test_delete_secret_missing_fails_loudly(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["delete-secret", "token"]) == 1
    assert "nothing deleted" in capsys.readouterr().err


@pytest.mark.integration
def test_set_secret_on_keyvault_profile_names_the_manual_route(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (config_dir / "config.toml").write_text(
        'credential_backend = "keyvault"\nkeyvault_url = "https://kv.example.invalid/"\n',
        encoding="utf-8",
    )
    _feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 1
    err = capsys.readouterr().err
    assert "read-only" in err
    assert "az keyvault secret set" in err


# --- file permissions ----------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_saved_config_is_owner_only(config_dir: Path) -> None:
    assert cli.main(["set", "count", "7"]) == 0
    mode = (config_dir / "config.toml").stat().st_mode & 0o777
    assert mode == 0o600
