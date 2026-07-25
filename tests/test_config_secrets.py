"""Unit tests for the credential-backend dispatcher.

Fake backends are injected through the same importlib-by-name mechanism the
dispatcher uses in production (a module registered in sys.modules), so these
tests also prove the convention contract.

Deliberately free of any concrete backend: ``secrets.py`` cannot be deleted
from a project (``resolve.py`` imports it), so its tests must not depend on
a backend file that can be. The backends have their own test modules, which
are deleted with them.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from python_repo_template.config import secrets
from python_repo_template.config.resolve import resolve_settings
from python_repo_template.config.schema import APP_NAME, ENV_PREFIX, ConfigError
from tests._config_test_object import ConfigTestObject

# Version of this test module. It ships to projects generated from this
# template, so bump on every change to let scripts/compare_to_template.py
# flag stale copies.
__version__ = "1.1.0"

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
