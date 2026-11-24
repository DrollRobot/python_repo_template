"""Tests for the config CLI's secret handling with the secret-storage machinery present.

Ships with the secret_storage feature and is removed with it. The CLI tests
that pass without the machinery are in tests/test_config_cli.py; both files
share their setup, tests/_config_cli_support.py.

The credential backend is an in-memory fake: a module registered in
``sys.modules`` under the name the dispatcher imports, so the CLI reaches it
through the real lookup in ``secrets.py``. Nothing here imports a concrete
backend or its third-party dependency, so this file keeps working in a project
that deleted one.

The setup pins the schema's CREDENTIAL_BACKEND policy to "prompt" (no default
backend), whatever the project picked, so tests that store secrets configure
``credential_backend`` explicitly (in the config file, or through init's
backend prompt); the fake registered as ``fake`` stands in for a real store.
Policy-specific tests monkeypatch the schema constant themselves.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest
import tomlkit

from python_repo_template.config import cli
from python_repo_template.config.schema import APP_NAME, ENV_PREFIX, ConfigError
from tests._config_cli_support import (
    config_text,
    feed_getpass,
    feed_input,
    forbid_getpass,
    isolate_config_cli,
    use_schema,
)
from tests._config_test_object import OptionalSecretTestObject, SecretTestObject

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run each test against a throwaway config directory and the secret-free test object."""
    return isolate_config_cli(monkeypatch, tmp_path)


@pytest.fixture
def secret_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a schema with a secret field.

    Requested by every test that exercises a secret. It runs after the
    autouse fixtures (autouse fixtures are set up first within a scope), so
    the secret-bearing object wins for those tests only.
    """
    use_schema(monkeypatch, SecretTestObject)


@pytest.fixture
def optional_secret_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a schema with a required secret, ``token``, and an optional one, ``extra``."""
    use_schema(monkeypatch, OptionalSecretTestObject)


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


# --- init ----------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_init_writes_config_and_stores_secret(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # name, count, ratio, flag, tags -- empty keeps the default -- then the
    # backend choice, then token's storage name (empty keeps the field name).
    feed_input(monkeypatch, ["n", "7", "", "", "", "fake", ""])
    feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init"]) == 0

    document = tomlkit.parse(config_text(config_dir))
    assert document["name"] == "n"
    assert document["count"] == 7
    assert document["credential_backend"] == "fake"  # the user's choice is recorded
    assert "ratio" not in document  # empty input keeps the schema default
    assert "token" not in document  # secrets never reach the file
    # The storage name is always written; runtime reads it from config.toml only.
    assert document["token_secret_name"] == "token"  # noqa: S105  (a name, not a secret)
    assert fake_backend == {(APP_NAME, "token"): "tok"}
    assert "Stored token" in capsys.readouterr().out


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_init_profile_scopes_secret_service(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    feed_input(monkeypatch, ["n", "", "", "", "", "fake", ""])
    feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init", "--profile", "p"]) == 0
    document = tomlkit.parse(config_text(config_dir))
    assert document["profiles"]["p"]["name"] == "n"
    assert document["profiles"]["p"]["credential_backend"] == "fake"
    assert fake_backend == {(f"{APP_NAME}:p", "token"): "tok"}


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_init_rejects_unknown_backend_and_reprompts(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    feed_input(monkeypatch, ["n", "", "", "", "", "nope", "fake", ""])
    feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init"]) == 0
    assert "Unknown backend 'nope'" in capsys.readouterr().out
    assert tomlkit.parse(config_text(config_dir))["credential_backend"] == "fake"


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_init_backend_skipped_prints_guidance_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    feed_input(monkeypatch, ["n", "", "", "", "", ""])
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "No credential_backend chosen" in out
    assert "set credential_backend" in out
    assert fake_backend == {}
    assert "credential_backend" not in tomlkit.parse(config_text(config_dir))


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_init_skips_backend_prompt_when_already_configured(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    # Only the schema fields and token's storage name are prompted; no
    # backend answer is queued.
    feed_input(monkeypatch, ["n", "", "", "", "", ""])
    feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init"]) == 0
    assert fake_backend == {(APP_NAME, "token"): "tok"}


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_init_force_restarts_secret_setup_and_leaves_stored_values(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    (config_dir / "config.toml").write_text(
        'name = "old"\ncredential_backend = "fake"\ntoken_secret_name = "old-name"\n',
        encoding="utf-8",
    )
    fake_backend[(APP_NAME, "old-name")] = "old-tok"
    # The backend choice is prompted again: nothing from the old file survives.
    feed_input(monkeypatch, ["n", "", "", "", "", "fake", ""])
    feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init", "--force"]) == 0
    document = tomlkit.parse(config_text(config_dir))
    assert document["credential_backend"] == "fake"
    assert document["token_secret_name"] == "token"  # noqa: S105  (a name, not a secret)
    assert fake_backend == {(APP_NAME, "old-name"): "old-tok", (APP_NAME, "token"): "tok"}


# --- set / unset ---------------------------------------------------------------------


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
    assert tomlkit.parse(config_text(config_dir))["credential_backend"] == "fake"


def test_set_backend_declared_key_writes(monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> None:
    """Keys a backend declares in RESERVED_KEYS are settable while it exists."""
    _install_backend(
        monkeypatch,
        "vaulted",
        get=lambda key, service, config: None,
        RESERVED_KEYS={"vaulted_url": "Vault URL"},
    )
    assert cli.main(["set", "vaulted_url", "https://x.invalid/"]) == 0
    assert tomlkit.parse(config_text(config_dir))["vaulted_url"] == "https://x.invalid/"


def test_unset_credential_backend_removes_choice(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    assert cli.main(["unset", "credential_backend"]) == 0
    out = capsys.readouterr().out
    assert "is required" not in out  # reserved keys are never schema-required
    assert "credential_backend" not in tomlkit.parse(config_text(config_dir))


# --- show ----------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_show_masks_secrets_and_reports_sources(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (config_dir / "config.toml").write_text(
        'name = "n"\ncredential_backend = "fake"\ntoken_secret_name = "token"\n', encoding="utf-8"
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


@pytest.mark.regression
@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_show_prints_settings_when_a_secret_name_is_missing(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text(
        'name = "n"\ncredential_backend = "fake"\n', encoding="utf-8"
    )
    assert cli.main(["show"]) == 1
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert next(line for line in lines if line.strip().startswith("name ")).endswith(
        "= 'n'  <- file:top-level"
    )
    assert next(line for line in lines if line.strip().startswith("token ")).endswith("= <error>")
    assert "'token_secret_name' is not set in config.toml" in captured.err


# --- secrets -------------------------------------------------------------------------


@pytest.mark.usefixtures("secret_schema")
def test_set_secret_stores_via_backend(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    feed_input(monkeypatch, [""])  # empty keeps the default storage name
    feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 0
    assert fake_backend == {(APP_NAME, "token"): "s3"}
    out = capsys.readouterr().out
    assert "s3" not in out  # value never echoed


@pytest.mark.usefixtures("secret_schema")
def test_set_secret_without_backend_is_actionable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No credential_backend configured: fail fast, before any prompt."""
    forbid_getpass(monkeypatch)
    assert cli.main(["set-secret", "token"]) == 1
    err = capsys.readouterr().err
    assert "no credential_backend is configured" in err
    assert "set credential_backend" in err


@pytest.mark.usefixtures("secret_schema")
def test_set_secret_uses_default_profile_service(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    (config_dir / "config.toml").write_text(
        'credential_backend = "fake"\ndefault_profile = "p"\n[profiles.p]\n', encoding="utf-8"
    )
    feed_input(monkeypatch, [""])
    feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 0
    assert fake_backend == {(f"{APP_NAME}:p", "token"): "s3"}


@pytest.mark.usefixtures("secret_schema")
def test_set_secret_rejects_empty_value(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    feed_input(monkeypatch, [""])
    feed_getpass(monkeypatch, [""])
    assert cli.main(["set-secret", "token"]) == 1
    assert "Empty value" in capsys.readouterr().err


@pytest.mark.usefixtures("secret_schema")
def test_delete_secret_removes_stored_value(
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    (config_dir / "config.toml").write_text(
        'credential_backend = "fake"\ntoken_secret_name = "token"\n', encoding="utf-8"
    )
    fake_backend[(APP_NAME, "token")] = "s3"
    assert cli.main(["delete-secret", "token"]) == 0
    assert fake_backend == {}


@pytest.mark.usefixtures("secret_schema")
def test_delete_secret_missing_fails_loudly(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text(
        'credential_backend = "fake"\ntoken_secret_name = "token"\n', encoding="utf-8"
    )
    assert cli.main(["delete-secret", "token"]) == 1
    assert "nothing deleted" in capsys.readouterr().err


@pytest.mark.usefixtures("secret_schema")
def test_delete_secret_without_storage_name_is_actionable(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    assert cli.main(["delete-secret", "token"]) == 1
    assert "'token_secret_name' is not set" in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_set_secret_on_read_only_backend_names_the_manual_route(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A backend with no set() is read-only: set-secret becomes a name-only
    # operation -- no hidden value prompt, ever -- and surfaces the backend's
    # own READ_ONLY_HINT. Which backends are read-only is the backends'
    # business, tested with them.
    _install_backend(
        monkeypatch,
        "vaulted",
        get=lambda key, service, config: None,
        READ_ONLY_HINT="update the secret in the vault console",
    )
    (config_dir / "config.toml").write_text('credential_backend = "vaulted"\n', encoding="utf-8")
    feed_input(monkeypatch, ["vault-token"])
    forbid_getpass(monkeypatch)
    assert cli.main(["set-secret", "token"]) == 0
    out = capsys.readouterr().out
    assert "read-only" in out
    assert "update the secret in the vault console" in out
    document = tomlkit.parse(config_text(config_dir))
    assert document["token_secret_name"] == "vault-token"  # noqa: S105  (a name, not a secret)


# --- secret storage names ------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_init_custom_secret_name_persists_and_scopes_storage(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    """A custom NAME lands in config.toml and the value is stored under it."""
    feed_input(monkeypatch, ["n", "", "", "", "", "fake", "kv-token"])
    feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init"]) == 0
    document = tomlkit.parse(config_text(config_dir))
    assert document["token_secret_name"] == "kv-token"  # noqa: S105  (a name, not a secret)
    assert fake_backend == {(APP_NAME, "kv-token"): "tok"}


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_set_secret_custom_name_persists_and_scopes_storage(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    feed_input(monkeypatch, ["kv-token"])
    feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 0
    document = tomlkit.parse(config_text(config_dir))
    assert document["token_secret_name"] == "kv-token"  # noqa: S105  (a name, not a secret)
    assert fake_backend == {(APP_NAME, "kv-token"): "s3"}
    assert "token_secret_name" in capsys.readouterr().out


@pytest.mark.usefixtures("secret_schema")
def test_delete_secret_uses_custom_name(
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    (config_dir / "config.toml").write_text(
        'credential_backend = "fake"\ntoken_secret_name = "kv-token"\n', encoding="utf-8"
    )
    fake_backend[(APP_NAME, "kv-token")] = "s3"
    assert cli.main(["delete-secret", "token"]) == 0
    assert fake_backend == {}


@pytest.mark.usefixtures("secret_schema")
def test_set_accepts_secret_name_key(config_dir: Path) -> None:
    """<field>_secret_name is a reserved key the set/unset commands accept."""
    assert cli.main(["set", "token_secret_name", "kv-token"]) == 0
    parsed = tomlkit.parse(config_text(config_dir))
    assert parsed["token_secret_name"] == "kv-token"  # noqa: S105  (a name, not a secret)
    assert cli.main(["unset", "token_secret_name"]) == 0
    assert "token_secret_name" not in tomlkit.parse(config_text(config_dir))


@pytest.mark.usefixtures("secret_schema")
def test_set_rejects_empty_secret_name(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["set", "token_secret_name", ""]) == 1
    assert "non-empty" in capsys.readouterr().err


@pytest.mark.integration
def test_set_secret_honors_schema_default_secret_name(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    """A schema default_secret_name scopes storage without any config.toml override."""
    from dataclasses import dataclass, field

    from python_repo_template.config.schema import secret

    @dataclass(frozen=True)
    class NamedSecretObject:
        name: str = field(metadata={"help": "Required plain string"})
        token: str = secret(
            help="Named secret",
            default_secret_name="kv-token",  # noqa: S106  (a name, not a secret)
        )

    monkeypatch.setattr("python_repo_template.config.cli.Settings", NamedSecretObject)
    (config_dir / "config.toml").write_text('credential_backend = "fake"\n', encoding="utf-8")
    feed_input(monkeypatch, [""])  # empty accepts the schema's offered name
    feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 0
    assert fake_backend == {(APP_NAME, "kv-token"): "s3"}
    # The chosen name is persisted; runtime reads config.toml only.
    parsed = tomlkit.parse(config_text(config_dir))
    assert parsed["token_secret_name"] == "kv-token"  # noqa: S105  (a name, not a secret)


# --- schema backend policy -----------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_init_schema_default_backend_skips_choice_prompt(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A backend-name policy is used without prompting and never written."""
    monkeypatch.setattr("python_repo_template.config.schema.CREDENTIAL_BACKEND", "fake")
    # Schema fields, then token's storage name; no backend answer is queued.
    feed_input(monkeypatch, ["n", "", "", "", "", ""])
    feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "Using credential backend 'fake' (schema default)" in out
    assert "credential_backend" not in tomlkit.parse(config_text(config_dir))
    assert fake_backend == {(APP_NAME, "token"): "tok"}


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_init_read_only_backend_prompts_name_only(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Read-only backend: storage-name prompt only, never a value prompt."""
    _install_backend(
        monkeypatch,
        "vaulted",
        get=lambda key, service, config: None,
        READ_ONLY_HINT="update the secret in the vault console",
    )
    (config_dir / "config.toml").write_text('credential_backend = "vaulted"\n', encoding="utf-8")
    feed_input(monkeypatch, ["n", "", "", "", "", "vault-token"])
    forbid_getpass(monkeypatch)
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "read-only" in out
    assert "update the secret in the vault console" in out
    parsed = tomlkit.parse(config_text(config_dir))
    assert parsed["token_secret_name"] == "vault-token"  # noqa: S105  (a name, not a secret)


# --- optional secrets ----------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("optional_secret_schema")
def test_init_declined_optional_secret_gets_no_name_and_no_value(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # name, count, ratio, flag, tags, backend, token's storage name, then
    # "set up extra?" left empty, which means no.
    feed_input(monkeypatch, ["n", "", "", "", "", "fake", "", ""])
    feed_getpass(monkeypatch, ["tok"])  # token's value only; no prompt for extra
    assert cli.main(["init"]) == 0
    document = tomlkit.parse(config_text(config_dir))
    assert document["token_secret_name"] == "token"  # noqa: S105  (a name, not a secret)
    assert "extra_secret_name" not in document  # declined: resolves as not set
    assert fake_backend == {(APP_NAME, "token"): "tok"}
    assert "Skipped extra; set it up later with" in capsys.readouterr().out


@pytest.mark.integration
@pytest.mark.usefixtures("optional_secret_schema")
def test_init_accepted_optional_secret_gets_name_and_value(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
) -> None:
    feed_input(monkeypatch, ["n", "", "", "", "", "fake", "", "y", "kv-extra"])
    feed_getpass(monkeypatch, ["tok", "ex"])
    assert cli.main(["init"]) == 0
    document = tomlkit.parse(config_text(config_dir))
    assert document["extra_secret_name"] == "kv-extra"  # noqa: S105  (a name, not a secret)
    assert fake_backend == {(APP_NAME, "token"): "tok", (APP_NAME, "kv-extra"): "ex"}


@pytest.mark.integration
@pytest.mark.usefixtures("optional_secret_schema")
def test_init_optional_secret_question_reprompts_on_other_answers(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    feed_input(monkeypatch, ["n", "", "", "", "", "fake", "", "maybe", "NO"])
    feed_getpass(monkeypatch, ["tok"])
    assert cli.main(["init"]) == 0
    assert "Answer y or n." in capsys.readouterr().out
    assert "extra_secret_name" not in tomlkit.parse(config_text(config_dir))


@pytest.mark.integration
@pytest.mark.usefixtures("optional_secret_schema")
def test_show_unset_optional_secret_is_none_and_set_one_is_masked(
    config_dir: Path,
    fake_backend: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = 'name = "n"\ncredential_backend = "fake"\ntoken_secret_name = "token"\n'
    (config_dir / "config.toml").write_text(base, encoding="utf-8")
    fake_backend[(APP_NAME, "token")] = "tok"
    assert cli.main(["show"]) == 0
    lines = capsys.readouterr().out.splitlines()
    extra_line = next(line for line in lines if line.strip().startswith("extra"))
    assert extra_line.endswith("= None  <- not set")

    (config_dir / "config.toml").write_text(
        base + 'extra_secret_name = "extra"\n', encoding="utf-8"
    )
    fake_backend[(APP_NAME, "extra")] = "extra-sekrit-xyz"
    assert cli.main(["show"]) == 0
    out = capsys.readouterr().out
    assert "extra-sekrit-xyz" not in out
    extra_line = next(line for line in out.splitlines() if line.strip().startswith("extra"))
    assert extra_line.endswith("= '********'  <- fake")
