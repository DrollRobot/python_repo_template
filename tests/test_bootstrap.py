"""Unit tests for the backend-agnostic credential dispatcher in tests/_bootstrap.py.

This is a permanent project test (not a dev-script test) -- tests/_bootstrap.py
ships in the final project, so this file is not deleted by
scripts/template_setup/cleanup.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import _bootstrap, _keyring, _keyvault

pytestmark = pytest.mark.unit


def test_backend_name_defaults_to_keyring() -> None:
    """An empty settings mapping selects the keyring backend."""
    assert _bootstrap._backend_name({}) == "keyring"


def test_backend_name_is_case_insensitive() -> None:
    """CREDENTIAL_BACKEND is normalized to lowercase."""
    assert _bootstrap._backend_name({"CREDENTIAL_BACKEND": "KeyVault"}) == "keyvault"


def test_load_backend_missing_module_raises_runtime_error() -> None:
    """A backend name with no matching tests/_<name>.py fails loudly, naming the file."""
    with pytest.raises(RuntimeError, match=re.escape("tests/_nonexistent_backend.py")):
        _bootstrap._load_backend("nonexistent_backend")


def test_load_backend_existing_module_is_returned() -> None:
    """An existing backend module is imported and returned as-is."""
    assert _bootstrap._load_backend("keyring") is _keyring
    assert _bootstrap._load_backend("keyvault") is _keyvault


def test_get_user_pass_routes_to_keyring_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no CREDENTIAL_BACKEND set, calls are routed to tests/_keyring.py."""
    monkeypatch.setattr(_keyring, "get_user_pass", lambda settings: ("user", "pass"))
    assert _bootstrap.get_user_pass({}) == ("user", "pass")


def test_get_cert_thumbprint_routes_to_keyring_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no CREDENTIAL_BACKEND set, calls are routed to tests/_keyring.py."""
    monkeypatch.setattr(_keyring, "get_cert_thumbprint", lambda settings: "thumb")
    assert _bootstrap.get_cert_thumbprint({}) == "thumb"


def test_get_service_principal_routes_to_keyring_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no CREDENTIAL_BACKEND set, calls are routed to tests/_keyring.py."""
    monkeypatch.setattr(_keyring, "get_service_principal", lambda settings: ("t", "c", "s"))
    assert _bootstrap.get_service_principal({}) == ("t", "c", "s")


def test_get_user_pass_routes_to_keyvault_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    """CREDENTIAL_BACKEND=keyvault routes to tests/_keyvault.py."""
    monkeypatch.setattr(_keyvault, "get_user_pass", lambda settings: ("kv-user", "kv-pass"))
    settings = {"CREDENTIAL_BACKEND": "keyvault"}
    assert _bootstrap.get_user_pass(settings) == ("kv-user", "kv-pass")


def test_get_user_pass_unknown_backend_raises_runtime_error() -> None:
    """An unrecognized CREDENTIAL_BACKEND value fails loudly, not silently."""
    settings = {"CREDENTIAL_BACKEND": "does_not_exist"}
    with pytest.raises(RuntimeError, match=re.escape("tests/_does_not_exist.py")):
        _bootstrap.get_user_pass(settings)


def test_load_settings_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    """A missing .env file raises FileNotFoundError rather than returning an empty dict."""
    with pytest.raises(FileNotFoundError):
        _bootstrap.load_settings(tmp_path / "missing.env")


def test_load_settings_reads_key_value_pairs(tmp_path: Path) -> None:
    """Values are read from the given env file and None entries are dropped."""
    env_file = tmp_path / ".env"
    env_file.write_text("CREDENTIAL_BACKEND=keyring\nUSERNAME_KEY=my_username\n", encoding="utf-8")
    settings = _bootstrap.load_settings(env_file)
    assert settings["CREDENTIAL_BACKEND"] == "keyring"
    assert settings["USERNAME_KEY"] == "my_username"
