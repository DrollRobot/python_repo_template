"""
Shared credential bootstrap utilities for tests and dev scripts.

This module is the public, backend-agnostic entry point: it loads settings
from ``.env`` and routes to whichever backend ``CREDENTIAL_BACKEND`` selects,
by convention -- ``CREDENTIAL_BACKEND=<name>`` loads ``tests._<name>`` -- with
no static, source-level dependency on any specific backend. Both
``tests/_keyring.py`` and ``tests/_keyvault.py`` are loaded lazily by name, so
deleting either one (plus its own dependency lines in ``pyproject.toml``)
removes it entirely without touching this file.

Backend selection (CREDENTIAL_BACKEND)
--------------------------------------
- ``keyring`` (default, including when unset): ``tests/_keyring.py`` -- OS
  native keyring (Windows Credential Manager, macOS Keychain, Linux Secret
  Service).
- ``keyvault``: ``tests/_keyvault.py`` -- Azure KeyVault.

Calling code stays backend-agnostic: use ``get_user_pass(settings)`` etc. and
the backend is chosen at runtime from ``.env`` -- no code change needed to
switch.

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

import importlib
from pathlib import Path
from typing import Protocol, cast

_ENV_FILE = Path(__file__).parent.parent / ".env"


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


class _CredentialBackend(Protocol):
    """Structural contract implemented by every ``tests/_<backend>.py`` module."""

    def get_user_pass(self, settings: dict[str, str]) -> tuple[str, str]: ...
    def get_cert_thumbprint(self, settings: dict[str, str]) -> str: ...
    def get_service_principal(self, settings: dict[str, str]) -> tuple[str, str, str]: ...


def _backend_name(settings: dict[str, str]) -> str:
    return settings.get("CREDENTIAL_BACKEND", "keyring").lower()


def _load_backend(name: str) -> _CredentialBackend:
    """Import and return the ``tests._<name>`` backend module, by convention.

    Deliberately name-agnostic: this function has never heard of "keyring" or
    "keyvault" specifically. Any ``tests/_<name>.py`` implementing the three
    methods below is a valid backend, selected purely via ``CREDENTIAL_BACKEND``.

    Args:
        name: Backend name from ``CREDENTIAL_BACKEND`` (e.g. ``"keyring"``).

    Returns:
        The imported module, structurally matching ``_CredentialBackend``.

    Raises:
        RuntimeError: If ``tests/_<name>.py`` does not exist -- the backend
            was removed, or the name is misspelled.
    """
    module_name = f"tests._{name}"
    try:
        return cast("_CredentialBackend", importlib.import_module(module_name))
    except ModuleNotFoundError as exc:
        path = module_name.replace(".", "/") + ".py"
        raise RuntimeError(
            f"CREDENTIAL_BACKEND={name!r} but {path} is missing. "
            "Restore it, or set CREDENTIAL_BACKEND to a backend that exists."
        ) from exc


def get_user_pass(settings: dict[str, str]) -> tuple[str, str]:
    """Retrieve username and password from the configured backend.

    Returns:
        ``(username, password)``
    """
    return _load_backend(_backend_name(settings)).get_user_pass(settings)


def get_cert_thumbprint(settings: dict[str, str]) -> str:
    """Retrieve the certificate thumbprint from the configured backend.

    Returns:
        Certificate thumbprint string.
    """
    return _load_backend(_backend_name(settings)).get_cert_thumbprint(settings)


def get_service_principal(settings: dict[str, str]) -> tuple[str, str, str]:
    """Retrieve service principal credentials from the configured backend.

    Returns:
        ``(tenant_id, client_id, client_secret)``
    """
    return _load_backend(_backend_name(settings)).get_service_principal(settings)
