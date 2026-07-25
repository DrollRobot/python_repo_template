"""Unit tests for the credential-backend dispatcher and backends.

Fake backends are injected through the same importlib-by-name mechanism the
dispatcher uses in production (a module registered in sys.modules), so these
tests also prove the convention contract. Keyring itself is exercised against
monkeypatched keyring functions -- never the host's real credential store;
the real-store roundtrip is a live-marked test.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import keyring
import keyring.backends.fail
import pytest

from python_repo_template.config import keyring_backend, secrets
from python_repo_template.config.resolve import resolve_settings
from python_repo_template.config.schema import APP_NAME, ENV_PREFIX, ConfigError
from tests._config_test_object import ConfigTestObject

# Version of this test module. It ships to projects generated from this
# template (cleanup.py keeps it: no script or hook shares its name), so bump
# on every change to let scripts/compare_to_template.py flag stale copies.
# It imports the keyring backend at module scope, so remove_keyring.py deletes
# it along with the backend.
__version__ = "1.0.0"

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip this package's env vars so the env layer cannot shadow backends."""
    import os

    for key in list(os.environ):
        if key.startswith(ENV_PREFIX):
            monkeypatch.delenv(key)


def _install_fake_backend(
    monkeypatch: pytest.MonkeyPatch, name: str = "fake", **functions: Any
) -> types.ModuleType:
    """Register a fake ``<name>_backend`` module the dispatcher can import."""
    module = types.ModuleType(f"python_repo_template.config.{name}_backend")
    for attr, value in functions.items():
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


def test_default_backend_is_keyring() -> None:
    assert secrets.backend_name({}) == "keyring"
    assert secrets.backend_name({"credential_backend": "keyvault"}) == "keyvault"


def test_backend_name_rejects_non_string() -> None:
    with pytest.raises(ConfigError, match="must be a non-empty string"):
        secrets.backend_name({"credential_backend": 3})


def test_missing_backend_module_is_actionable() -> None:
    with pytest.raises(ConfigError, match=r"nope_backend\.py is missing"):
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


# --- keyring backend -----------------------------------------------------------------


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    """Replace the keyring module functions with an in-memory store."""
    store: dict[tuple[str, str], str] = {}

    def delete(service: str, key: str) -> None:
        if (service, key) not in store:
            raise keyring.errors.PasswordDeleteError(key)
        del store[(service, key)]

    monkeypatch.setattr(keyring, "get_password", lambda service, key: store.get((service, key)))

    def set_password(service: str, key: str, value: str) -> None:
        store[(service, key)] = value

    monkeypatch.setattr(keyring, "set_password", set_password)
    monkeypatch.setattr(keyring, "delete_password", delete)
    return store


def test_keyring_roundtrip_with_fake_store(fake_keyring: dict[tuple[str, str], str]) -> None:
    keyring_backend.set("token", "s", "svc", {})
    assert fake_keyring == {("svc", "token"): "s"}
    assert keyring_backend.get("token", "svc", {}) == "s"
    keyring_backend.delete("token", "svc", {})
    assert keyring_backend.get("token", "svc", {}) is None


def test_keyring_delete_missing_fails_loudly(fake_keyring: dict[tuple[str, str], str]) -> None:
    with pytest.raises(ConfigError, match="nothing deleted"):
        keyring_backend.delete("token", "svc", {})


def test_keyring_headless_backend_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    fail_backend = keyring.backends.fail.Keyring()  # type: ignore[no-untyped-call]
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail_backend)
    with pytest.raises(ConfigError, match=r"No usable OS keyring.*environment variables"):
        keyring_backend.get("token", "svc", {})


# --- keyvault backend ----------------------------------------------------------------


def test_keyvault_requires_vault_url() -> None:
    from python_repo_template.config import keyvault_backend

    with pytest.raises(ConfigError, match="keyvault_url"):
        keyvault_backend.get("token", "svc", {})


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


# --- live ----------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.integration
def test_real_keyring_roundtrip() -> None:
    """Store, read, and delete a value in the host's real credential store."""
    service = f"{APP_NAME}:pytest-live"
    key = "live-roundtrip-probe"
    keyring_backend.set(key, "probe-value", service, {})
    try:
        assert keyring_backend.get(key, service, {}) == "probe-value"
    finally:
        keyring_backend.delete(key, service, {})
    assert keyring_backend.get(key, service, {}) is None
