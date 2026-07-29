"""Unit tests for the credential-backend dispatcher.

Fake backends are injected through the same importlib-by-name mechanism the
dispatcher uses in production (a module registered in sys.modules), so these
tests also prove the convention contract.

Deliberately free of any concrete backend: the dispatcher works with any
``*_backend.py`` module, and this file is deleted together with the whole
secret-storage machinery (``[features].secret_storage``), never with an
individual backend. The backends have their own test modules, deleted with
them.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from python_repo_template.config import secrets
from python_repo_template.config.resolve import resolve_settings
from python_repo_template.config.schema import APP_NAME, CLI_NAME, ENV_PREFIX, ConfigError
from tests._config_test_object import ConfigTestObject

# Version of this test module. It ships to projects generated from this
# template, so bump on every change to let scripts/compare_to_template.py
# flag stale copies.
__version__ = "2.0.0"

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip this package's env vars so the env layer cannot shadow backends."""
    import os

    for key in list(os.environ):
        if key.startswith(ENV_PREFIX):
            monkeypatch.delenv(key)


def _install_fake_backend(
    monkeypatch: pytest.MonkeyPatch, name: str = "fake", **attributes: Any
) -> types.ModuleType:
    """Register a fake ``<name>_backend`` module the dispatcher can import."""
    module = types.ModuleType(f"python_repo_template.config.{name}_backend")
    for attr, value in attributes.items():
        setattr(module, attr, value)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


# --- dispatcher ----------------------------------------------------------------------


def test_get_secret_routes_to_selected_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_get(key: str, service: str, config: dict[str, Any]) -> str:
        calls.append((key, service, config))
        return "value"

    _install_fake_backend(monkeypatch, get=fake_get)
    config = {"credential_backend": "fake", "extra": 1}
    assert secrets.get_secret("token", "prof", config) == "value"
    assert calls == [("token", f"{APP_NAME}:prof", config)]


def test_service_name_scopes_by_profile() -> None:
    assert secrets.service_name(None) == APP_NAME
    assert secrets.service_name("contoso") == f"{APP_NAME}:contoso"


def test_backend_name_has_no_default() -> None:
    """Which backend stores secrets is the user's choice; the code names none."""
    assert secrets.backend_name({}) is None
    assert secrets.backend_name({"credential_backend": "keyvault"}) == "keyvault"


def test_backend_name_rejects_non_string() -> None:
    with pytest.raises(ConfigError, match="must be a non-empty string"):
        secrets.backend_name({"credential_backend": 3})


def test_get_secret_without_backend_skips_the_layer() -> None:
    """No credential_backend configured: nothing stored, nothing imported."""
    assert secrets.get_secret("token", None, {}) is None


@pytest.mark.parametrize("operation", ["set", "delete"])
def test_writes_without_backend_fail_loudly(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Secret writes need an explicit user backend choice; the error names it."""
    _install_fake_backend(monkeypatch, get=lambda key, service, config: None)

    def write() -> None:
        if operation == "set":
            secrets.set_secret("token", "s", None, {})
        else:
            secrets.delete_secret("token", None, {})

    with pytest.raises(ConfigError) as excinfo:
        write()
    message = str(excinfo.value)
    assert "no credential_backend is configured" in message
    assert f"{CLI_NAME} set credential_backend" in message
    assert "fake" in message  # the available backends are listed


def test_missing_backend_module_is_actionable() -> None:
    with pytest.raises(ConfigError, match=r"no such backend exists.*Available backends"):
        secrets.get_secret("token", None, {"credential_backend": "nope"})


def test_set_secret_routes_to_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, str] = {}
    _install_fake_backend(
        monkeypatch,
        get=lambda key, service, config: stored.get(key),
        set=lambda key, value, service, config: stored.__setitem__(key, value),
        delete=lambda key, service, config: stored.__delitem__(key),
    )
    config = {"credential_backend": "fake"}
    secrets.set_secret("token", "s", None, config)
    assert secrets.get_secret("token", None, config) == "s"
    secrets.delete_secret("token", None, config)
    assert secrets.get_secret("token", None, config) is None


def test_read_only_backend_rejects_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_backend(
        monkeypatch,
        get=lambda key, service, config: None,
        READ_ONLY_HINT="update it at the source",
    )
    config = {"credential_backend": "fake"}
    with pytest.raises(ConfigError, match=r"read-only.*update it at the source"):
        secrets.set_secret("token", "s", None, config)
    with pytest.raises(ConfigError, match="read-only"):
        secrets.delete_secret("token", None, config)


# --- backend discovery and reserved keys ---------------------------------------------


def test_available_backends_sees_injected_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """A module registered under the naming convention is a usable backend."""
    _install_fake_backend(monkeypatch, get=lambda key, service, config: None)
    assert "fake" in secrets.available_backends()


def test_reserved_keys_come_from_the_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each backend's RESERVED_KEYS joins the reserved set while it exists."""
    _install_fake_backend(
        monkeypatch,
        get=lambda key, service, config: None,
        RESERVED_KEYS={"fake_url": "Where the fake backend lives"},
    )
    reserved = secrets.reserved_profile_keys()
    assert "credential_backend" in reserved
    assert "fake_url" in reserved
    assert secrets.reserved_key_help()["fake_url"] == "Where the fake backend lives"
    assert secrets.backend_reserved_keys("fake") == {"fake_url": "Where the fake backend lives"}


def test_backend_without_reserved_keys_declares_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_backend(monkeypatch, get=lambda key, service, config: None)
    assert secrets.backend_reserved_keys("fake") == {}


def test_reserved_config_merges_profile_over_top_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Top-level reserved keys are shared fallbacks; the profile table wins."""
    _install_fake_backend(
        monkeypatch,
        get=lambda key, service, config: None,
        RESERVED_KEYS={"fake_url": "help"},
    )
    config = {"credential_backend": "fake", "fake_url": "top", "name": "not-reserved"}
    profile_values = {"fake_url": "prof"}
    merged = secrets.reserved_config(config, profile_values)
    assert merged == {"credential_backend": "fake", "fake_url": "prof"}


# --- resolver integration ------------------------------------------------------------


@pytest.mark.integration
def test_resolver_pulls_secret_from_profile_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Full stack: config file selects the backend, dispatcher imports it."""
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def fake_get(key: str, service: str, config: dict[str, Any]) -> str:
        seen.append((key, service, config))
        return "from-backend"

    _install_fake_backend(monkeypatch, get=fake_get)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        credential_backend = "keyring"
        default_profile = "a"

        [profiles.a]
        name = "n"
        credential_backend = "fake"
        """,
        encoding="utf-8",
    )
    settings: ConfigTestObject = resolve_settings(ConfigTestObject, config_path=config_path)
    assert settings.token == "from-backend"  # noqa: S105
    # Profile-scoped service; profile's credential_backend overrode the top level.
    assert seen == [("token", f"{APP_NAME}:a", {"credential_backend": "fake"})]


@pytest.mark.integration
def test_resolver_accepts_backend_declared_keys_in_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A backend's RESERVED_KEYS are legal config.toml keys while it exists."""
    _install_fake_backend(
        monkeypatch,
        get=lambda key, service, config: config["fake_url"],
        RESERVED_KEYS={"fake_url": "help"},
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'name = "n"\ncredential_backend = "fake"\nfake_url = "https://x.invalid/"\n',
        encoding="utf-8",
    )
    settings: ConfigTestObject = resolve_settings(ConfigTestObject, config_path=config_path)
    assert settings.token == "https://x.invalid/"  # noqa: S105
