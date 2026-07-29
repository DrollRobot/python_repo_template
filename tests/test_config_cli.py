"""Tests for the config CLI (init / show / set / unset / profiles / secrets).

Commands run in-process via ``cli.main([...])`` with the config directory
pointed at ``tmp_path`` and the schema swapped for the fixed test object, so
nothing here touches the real user config, a real credential store (an
in-memory fake backend stands in), or the repo's FIXME example fields.

The fake is a module registered in ``sys.modules`` under the name the
dispatcher imports, so the CLI reaches it through the real lookup in
``secrets.py``. Nothing here imports a concrete backend or its third-party
dependency, so this file keeps working in a project that deleted one.

There is no default backend, so tests that store secrets configure
``credential_backend`` explicitly (in the config file, or through init's
backend prompt); the fake registered as ``fake`` stands in for a real store.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import tomlkit

from python_repo_template.config import cli
from python_repo_template.config.paths import CONFIG_DIR_ENV
from python_repo_template.config.schema import APP_NAME, ENV_PREFIX, ConfigError
from tests._config_test_object import ConfigTestObject, block_secrets_module

# Version of this test module. It ships to projects generated from this
# template, so bump on every change to let scripts/compare_to_template.py
# flag stale copies.
__version__ = "2.0.0"

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


def _install_backend(monkeypatch: pytest.MonkeyPatch, name: str, **functions: Any) -> None:
    """Register a fake ``<name>_backend`` module the dispatcher can import."""
    module = types.ModuleType(f"python_repo_template.config.{name}_backend")
    for attr, value in functions.items():
        setattr(module, attr, value)
    monkeypatch.setitem(sys.modules, module.__name__, module)


def _memory_backend(monkeypatch: pytest.MonkeyPatch, name: str) -> dict[tuple[str, str], str]:
    """Install a read-write in-memory backend and return its ``(service, key)`` store."""
    store: dict[tuple[str, str], str] = {}

    def delete(key: str, service: str, config: Any) -> None:
        # Mirrors the real backends' contract: deleting what is not there is
        # an error, never a silent no-op.
        if (service, key) not in store:
            raise ConfigError(f"No {key!r} stored for {service!r}; nothing deleted.")
        del store[(service, key)]

    _install_backend(
        monkeypatch,
        name,
        get=lambda key, service, config: store.get((service, key)),
        set=lambda key, value, service, config: store.__setitem__((service, key), value),
        delete=delete,
    )
    return store


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    """In-memory backend named 'fake', so no test touches a real store.

    Registered in sys.modules, it shows up in available_backends() like a
    real backend file would, so init's backend prompt and the
    credential_backend validation accept it.
    """
    return _memory_backend(monkeypatch, "fake")


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
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # name, count, ratio, flag, tags -- empty keeps the default -- then the
    # backend choice.
    _feed_input(monkeypatch, ["n", "7", "", "", "", "fake"])
    _feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init"]) == 0

    document = tomlkit.parse(_config_text(config_dir))
    assert document["name"] == "n"
    assert document["count"] == 7
    assert document["credential_backend"] == "fake"  # the user's choice is recorded
    assert "ratio" not in document  # empty input keeps the schema default
    assert "token" not in document  # secrets never reach the file
    assert fake_backend == {(APP_NAME, "token"): "tok"}
    assert "Stored token" in capsys.readouterr().out


@pytest.mark.integration
def test_init_required_field_reprompts(monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> None:
    # First response empty for required 'name'; it must re-prompt. The last
    # empty response declines the backend choice.
    _feed_input(monkeypatch, ["", "n", "", "", "", "", ""])
    assert cli.main(["init"]) == 0
    assert tomlkit.parse(_config_text(config_dir))["name"] == "n"


@pytest.mark.integration
def test_init_profile_scopes_secret_service(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    _feed_input(monkeypatch, ["n", "", "", "", "", "fake"])
    _feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init", "--profile", "p"]) == 0
    document = tomlkit.parse(_config_text(config_dir))
    assert document["profiles"]["p"]["name"] == "n"
    assert document["profiles"]["p"]["credential_backend"] == "fake"
    assert fake_backend == {(f"{APP_NAME}:p", "token"): "tok"}


@pytest.mark.integration
def test_init_rejects_unknown_backend_and_reprompts(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _feed_input(monkeypatch, ["n", "", "", "", "", "nope", "fake"])
    _feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init"]) == 0
    assert "Unknown backend 'nope'" in capsys.readouterr().out
    assert tomlkit.parse(_config_text(config_dir))["credential_backend"] == "fake"


@pytest.mark.integration
def test_init_backend_skipped_prints_guidance_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _feed_input(monkeypatch, ["n", "", "", "", "", ""])
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "No credential_backend chosen" in out
    assert "set credential_backend" in out
    assert fake_backend == {}
    assert "credential_backend" not in tomlkit.parse(_config_text(config_dir))


@pytest.mark.integration
def test_init_skips_backend_prompt_when_already_configured(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    # Only the schema fields are prompted; no backend answer is queued.
    _feed_input(monkeypatch, ["n", "", "", "", ""])
    _feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init"]) == 0
    assert fake_backend == {(APP_NAME, "token"): "tok"}


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
    err = capsys.readouterr().err
    assert "Unknown option 'nope'" in err
    assert "credential_backend" in err  # reserved keys join the listing


def test_set_credential_backend_rejects_unknown_backend(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["set", "credential_backend", "nope"]) == 1
    err = capsys.readouterr().err
    assert "Unknown credential backend 'nope'" in err
    assert "fake" in err  # the available backends are listed


def test_set_credential_backend_writes_choice(config_dir: Path) -> None:
    assert cli.main(["set", "credential_backend", "fake"]) == 0
    assert tomlkit.parse(_config_text(config_dir))["credential_backend"] == "fake"


def test_set_backend_declared_key_writes(monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> None:
    """Keys a backend declares in RESERVED_KEYS are settable while it exists."""
    _install_backend(
        monkeypatch,
        "vaulted",
        get=lambda key, service, config: None,
        RESERVED_KEYS={"vaulted_url": "Vault URL"},
    )
    assert cli.main(["set", "vaulted_url", "https://x.invalid/"]) == 0
    assert tomlkit.parse(_config_text(config_dir))["vaulted_url"] == "https://x.invalid/"


def test_unset_credential_backend_removes_choice(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    assert cli.main(["unset", "credential_backend"]) == 0
    out = capsys.readouterr().out
    assert "is required" not in out  # reserved keys are never schema-required
    assert "credential_backend" not in tomlkit.parse(_config_text(config_dir))


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
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (config_dir / "config.toml").write_text(
        'name = "n"\ncredential_backend = "fake"\n', encoding="utf-8"
    )
    fake_backend[(APP_NAME, "token")] = "sekrit-value-xyz"
    monkeypatch.setenv(ENV_PREFIX + "COUNT", "9")
    assert cli.main(["show"]) == 0
    out = capsys.readouterr().out
    assert "sekrit-value-xyz" not in out  # masked
    assert "********" in out
    assert "<- fake" in out  # provenance names the user's backend
    assert "<- file:top-level" in out
    assert f"<- env:{ENV_PREFIX}COUNT" in out
    assert "<- default" in out


def test_show_missing_required_is_actionable(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["show"]) == 1
    assert "Missing required configuration value(s)" in capsys.readouterr().err


# --- secrets -------------------------------------------------------------------------


def test_set_secret_stores_via_backend(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    _feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 0
    assert fake_backend == {(APP_NAME, "token"): "s3"}
    out = capsys.readouterr().out
    assert "s3" not in out  # value never echoed


def test_set_secret_without_backend_is_actionable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No credential_backend configured: the error names the choice to make."""
    _feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 1
    err = capsys.readouterr().err
    assert "no credential_backend is configured" in err
    assert "set credential_backend" in err


def test_set_secret_uses_default_profile_service(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    (config_dir / "config.toml").write_text(
        'credential_backend = "fake"\ndefault_profile = "p"\n[profiles.p]\n', encoding="utf-8"
    )
    _feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 0
    assert fake_backend == {(f"{APP_NAME}:p", "token"): "s3"}


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
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    fake_backend[(APP_NAME, "token")] = "s3"
    assert cli.main(["delete-secret", "token"]) == 0
    assert fake_backend == {}


def test_delete_secret_missing_fails_loudly(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    assert cli.main(["delete-secret", "token"]) == 1
    assert "nothing deleted" in capsys.readouterr().err


@pytest.mark.integration
def test_set_secret_on_read_only_backend_names_the_manual_route(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A backend with no set() is read-only; the CLI must surface its own
    # READ_ONLY_HINT rather than a bare failure. Which backends are read-only
    # is the backends' business, tested with them.
    _install_backend(
        monkeypatch,
        "vaulted",
        get=lambda key, service, config: None,
        READ_ONLY_HINT="update the secret in the vault console",
    )
    (config_dir / "config.toml").write_text('credential_backend = "vaulted"\n', encoding="utf-8")
    _feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 1
    err = capsys.readouterr().err
    assert "read-only" in err
    assert "update the secret in the vault console" in err


# --- optional secret machinery -------------------------------------------------------


def test_secret_commands_absent_without_secret_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A schema with no secret fields gets no set-secret/delete-secret commands."""
    monkeypatch.setattr(cli, "_HAS_SECRET_FIELDS", False)
    with pytest.raises(SystemExit):
        cli.main(["set-secret", "token"])
    assert "invalid choice: 'set-secret'" in capsys.readouterr().err


def test_set_secret_with_machinery_removed_is_actionable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Secret fields still in the schema after removal fail loudly, not weirdly."""
    block_secrets_module(monkeypatch)
    _feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 1
    assert "secret-storage machinery" in capsys.readouterr().err


def test_backend_keys_unknown_when_machinery_removed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With secrets.py gone there are no reserved keys for set to accept."""
    block_secrets_module(monkeypatch)
    assert cli.main(["set", "credential_backend", "fake"]) == 1
    assert "Unknown option 'credential_backend'" in capsys.readouterr().err


# --- file permissions ----------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_saved_config_is_owner_only(config_dir: Path) -> None:
    assert cli.main(["set", "count", "7"]) == 0
    mode = (config_dir / "config.toml").stat().st_mode & 0o777
    assert mode == 0o600
