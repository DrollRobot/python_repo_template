"""
Shared credential bootstrap utilities for tests and dev scripts.

This module is the public entry point and the *keyring* backend. It has no
Azure dependency: the optional KeyVault backend lives in ``tests/_keyvault.py``
and is loaded lazily, so deleting that file (plus the ``keyvault`` dependency
group in ``pyproject.toml``) removes Azure entirely without touching this file.

Backend selection (CREDENTIAL_BACKEND)
--------------------------------------
- ``keyring`` (default): fetch secrets from the OS native keyring (Windows
  Credential Manager, macOS Keychain, Linux Secret Service). Run
  ``uv run scripts/setup_credentials.py --type <type>`` once to store them.
- ``keyvault``: fetch secrets from Azure KeyVault (requires ``tests/_keyvault.py``
  and the ``keyvault`` dependency group). Set ``CREDENTIAL_BACKEND=keyvault`` in
  ``.env`` to switch -- no code change needed.

Calling code stays backend-agnostic: use ``get_user_pass(settings)`` etc. and
the backend is chosen at runtime from ``.env``.

Credential types (CREDENTIAL_TYPE)
----------------------------------
- ``user_pass``: env vars ``USERNAME_KEY``, ``PASSWORD_KEY``; returns
  ``(username, password)``.
- ``cert_thumbprint``: env var ``CERT_THUMBPRINT_KEY``; returns the thumbprint.
- ``service_principal``: env vars ``TENANT_ID_KEY``, ``CLIENT_ID_KEY``,
  ``CLIENT_SECRET_KEY``; returns ``(tenant_id, client_id, client_secret)``.

The ``*_KEY`` env vars hold the secret identifier used by both backends.
Has no pytest dependency -- errors raise plain exceptions so each caller can
handle them appropriately.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast

_ENV_FILE = Path(__file__).parent.parent / ".env"
_KEYRING_SERVICE = "python_repo_template"


def load_settings(env_file: Path = _ENV_FILE) -> dict[str, str]:
    """Load key/value pairs from *env_file* and return as a plain dict.

    Raises:
        FileNotFoundError: if the file does not exist.
    """
    from dotenv import dotenv_values

    if not env_file.exists():
        raise FileNotFoundError(env_file)
    values = dotenv_values(env_file)
    return {k: v for k, v in values.items() if v is not None}


# ---------------------------------------------------------------------------
# Optional KeyVault backend -- loaded lazily by name so this module has no
# static dependency on tests/_keyvault.py or on azure-*. Deleting that file
# leaves the keyring path (and mypy) untouched.
# ---------------------------------------------------------------------------


class _KeyVaultBackend(Protocol):
    """Structural contract implemented by ``tests/_keyvault.py``."""

    def get_user_pass(self, settings: dict[str, str]) -> tuple[str, str]: ...
    def get_cert_thumbprint(self, settings: dict[str, str]) -> str: ...
    def get_service_principal(self, settings: dict[str, str]) -> tuple[str, str, str]: ...


def _keyvault() -> _KeyVaultBackend:
    """Import and return the KeyVault backend module, or raise a clear error."""
    import importlib

    try:
        return cast("_KeyVaultBackend", importlib.import_module("tests._keyvault"))
    except ModuleNotFoundError as exc:  # KeyVault feature was removed
        raise RuntimeError(
            "CREDENTIAL_BACKEND=keyvault but tests/_keyvault.py is missing. "
            "Restore it and the `keyvault` dependency group, or set "
            "CREDENTIAL_BACKEND=keyring in .env."
        ) from exc


def _backend(settings: dict[str, str]) -> str:
    return settings.get("CREDENTIAL_BACKEND", "keyring").lower()


# ---------------------------------------------------------------------------
# Credential fetchers -- keyring backend
# ---------------------------------------------------------------------------


def _fetch_user_pass_from_keyring(settings: dict[str, str]) -> tuple[str, str]:
    import keyring

    username_key = settings["USERNAME_KEY"]
    password_key = settings["PASSWORD_KEY"]
    username = keyring.get_password(_KEYRING_SERVICE, username_key)
    password = keyring.get_password(_KEYRING_SERVICE, password_key)
    if not username or not password:
        raise ValueError(
            f"Credentials not found in keyring (service='{_KEYRING_SERVICE}', "
            f"keys='{username_key}', '{password_key}'). "
            "Run 'uv run scripts/setup_credentials.py --type user_pass' to store them."
        )
    return username, password


def _fetch_cert_thumbprint_from_keyring(settings: dict[str, str]) -> str:
    import keyring

    thumbprint_key = settings["CERT_THUMBPRINT_KEY"]
    thumbprint = keyring.get_password(_KEYRING_SERVICE, thumbprint_key)
    if not thumbprint:
        raise ValueError(
            f"Certificate thumbprint not found in keyring "
            f"(service='{_KEYRING_SERVICE}', key='{thumbprint_key}'). "
            "Run 'uv run scripts/setup_credentials.py --type cert_thumbprint' to store it."
        )
    return thumbprint


def _fetch_service_principal_from_keyring(settings: dict[str, str]) -> tuple[str, str, str]:
    import keyring

    tenant_id_key = settings["TENANT_ID_KEY"]
    client_id_key = settings["CLIENT_ID_KEY"]
    client_secret_key = settings["CLIENT_SECRET_KEY"]
    tenant_id = keyring.get_password(_KEYRING_SERVICE, tenant_id_key)
    client_id = keyring.get_password(_KEYRING_SERVICE, client_id_key)
    client_secret = keyring.get_password(_KEYRING_SERVICE, client_secret_key)
    if not tenant_id or not client_id or not client_secret:
        raise ValueError(
            f"Service principal credentials not found in keyring "
            f"(service='{_KEYRING_SERVICE}', "
            f"keys='{tenant_id_key}', '{client_id_key}', '{client_secret_key}'). "
            "Run 'uv run scripts/setup_credentials.py --type service_principal' to store them."
        )
    return tenant_id, client_id, client_secret


# ---------------------------------------------------------------------------
# Public dispatchers -- pick the backend at runtime from CREDENTIAL_BACKEND
# ---------------------------------------------------------------------------


def get_user_pass(settings: dict[str, str]) -> tuple[str, str]:
    """Retrieve username and password from the configured backend.

    Returns:
        ``(username, password)``
    """
    backend = _backend(settings)
    if backend == "keyring":
        return _fetch_user_pass_from_keyring(settings)
    if backend == "keyvault":
        return _keyvault().get_user_pass(settings)
    raise ValueError(f"Unknown CREDENTIAL_BACKEND={backend!r}; expected 'keyring' or 'keyvault'.")


def get_cert_thumbprint(settings: dict[str, str]) -> str:
    """Retrieve the certificate thumbprint from the configured backend.

    Returns:
        Certificate thumbprint string.
    """
    backend = _backend(settings)
    if backend == "keyring":
        return _fetch_cert_thumbprint_from_keyring(settings)
    if backend == "keyvault":
        return _keyvault().get_cert_thumbprint(settings)
    raise ValueError(f"Unknown CREDENTIAL_BACKEND={backend!r}; expected 'keyring' or 'keyvault'.")


def get_service_principal(settings: dict[str, str]) -> tuple[str, str, str]:
    """Retrieve service principal credentials from the configured backend.

    Returns:
        ``(tenant_id, client_id, client_secret)``
    """
    backend = _backend(settings)
    if backend == "keyring":
        return _fetch_service_principal_from_keyring(settings)
    if backend == "keyvault":
        return _keyvault().get_service_principal(settings)
    raise ValueError(f"Unknown CREDENTIAL_BACKEND={backend!r}; expected 'keyring' or 'keyvault'.")
