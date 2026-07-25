"""Unit tests for the OS-keyring credential backend.

Keyring is exercised against monkeypatched keyring functions -- never the
host's real credential store; the real-store roundtrip is a live-marked test.

This module imports the keyring backend and the ``keyring`` package at module
scope, so it is deleted along with the backend when a project drops it.
"""

from __future__ import annotations

import keyring
import keyring.backends.fail
import pytest

from python_repo_template.config import keyring_backend
from python_repo_template.config.schema import APP_NAME, ConfigError

# Version of this test module. It ships to projects generated from this
# template, so bump on every change to let scripts/compare_to_template.py
# flag stale copies.
__version__ = "1.0.0"

pytestmark = pytest.mark.unit


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
