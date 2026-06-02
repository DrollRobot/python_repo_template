"""
Shared credential and client bootstrap utilities.

Used by both pytest fixtures (tests/conftest.py) and dev scripts
(scripts/_common.py).  Has no pytest dependency -- errors raise plain
exceptions so each caller can handle them appropriately.

Credential types (CREDENTIAL_TYPE)
------------------------------------
- ``user_pass``: authenticate with username and password.
  Required env vars: ``USERNAME_KEY``, ``PASSWORD_KEY``.
  Returns: ``tuple[str, str]`` -- (username, password)

- ``cert_thumbprint``: authenticate with a certificate thumbprint.
  Required env vars: ``CERT_THUMBPRINT_KEY``.
  Returns: ``str`` -- thumbprint

- ``service_principal``: authenticate with tenant ID, client ID, and client secret.
  Required env vars: ``TENANT_ID_KEY``, ``CLIENT_ID_KEY``, ``CLIENT_SECRET_KEY``.
  Returns: ``tuple[str, str, str]`` -- (tenant_id, client_id, client_secret)

Credential backends (CREDENTIAL_BACKEND)
-----------------------------------------
- ``keyvault`` (default): fetch secrets from Azure KeyVault.
  Requires ``KEYVAULT_TENANT_ID``, ``KEYVAULT_URL``, and a valid
  ``DefaultAzureCredential`` (e.g., ``az login``).

- ``keyring``: fetch secrets from the OS native keyring (Windows Credential
  Manager, macOS Keychain, Linux Secret Service).  Run
  ``uv run scripts/setup_credentials.py --type <type>`` once to store them.
  ``KEYVAULT_TENANT_ID`` and ``KEYVAULT_URL`` are not needed.

The ``*_KEY`` env vars hold the secret identifier used with both backends.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from azure.keyvault.secrets import SecretClient

_ENV_FILE = Path(__file__).parent.parent / ".env.testing"
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


def build_kv_client(settings: dict[str, str]) -> SecretClient:
    """Create and return an Azure KeyVault SecretClient.

    Only needed when ``CREDENTIAL_BACKEND=keyvault`` (the default).

    Raises:
        ImportError: if azure-identity or azure-keyvault-secrets are missing.
        Exception: if DefaultAzureCredential cannot authenticate.
    """
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    os.environ.setdefault("AZURE_TENANT_ID", settings["KEYVAULT_TENANT_ID"])
    credential = DefaultAzureCredential()
    return SecretClient(vault_url=settings["KEYVAULT_URL"], credential=credential)


# ---------------------------------------------------------------------------
# Credential fetchers -- user_pass
# ---------------------------------------------------------------------------


def _fetch_user_pass_from_keyvault(
    settings: dict[str, str], kv: SecretClient
) -> tuple[str, str]:
    username = kv.get_secret(settings["USERNAME_KEY"]).value
    password = kv.get_secret(settings["PASSWORD_KEY"]).value
    if not username or not password:
        raise ValueError("One or more KeyVault secrets returned no value.")
    return username, password


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


def get_user_pass(
    settings: dict[str, str], kv: SecretClient | None = None
) -> tuple[str, str]:
    """Retrieve username and password from the configured backend.

    Returns:
        ``(username, password)``
    """
    backend = settings.get("CREDENTIAL_BACKEND", "keyvault").lower()
    if backend == "keyring":
        return _fetch_user_pass_from_keyring(settings)
    if kv is None:
        raise ValueError(
            "kv_client is required when CREDENTIAL_BACKEND is 'keyvault'. "
            "Set CREDENTIAL_BACKEND=keyring in .env.testing to use the OS keyring instead."
        )
    return _fetch_user_pass_from_keyvault(settings, kv)


# ---------------------------------------------------------------------------
# Credential fetchers -- cert_thumbprint
# ---------------------------------------------------------------------------


def _fetch_cert_thumbprint_from_keyvault(
    settings: dict[str, str], kv: SecretClient
) -> str:
    thumbprint = kv.get_secret(settings["CERT_THUMBPRINT_KEY"]).value
    if not thumbprint:
        raise ValueError("Certificate thumbprint KeyVault secret returned no value.")
    return thumbprint


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


def get_cert_thumbprint(
    settings: dict[str, str], kv: SecretClient | None = None
) -> str:
    """Retrieve the certificate thumbprint from the configured backend.

    Returns:
        Certificate thumbprint string.
    """
    backend = settings.get("CREDENTIAL_BACKEND", "keyvault").lower()
    if backend == "keyring":
        return _fetch_cert_thumbprint_from_keyring(settings)
    if kv is None:
        raise ValueError(
            "kv_client is required when CREDENTIAL_BACKEND is 'keyvault'. "
            "Set CREDENTIAL_BACKEND=keyring in .env.testing to use the OS keyring instead."
        )
    return _fetch_cert_thumbprint_from_keyvault(settings, kv)


# ---------------------------------------------------------------------------
# Credential fetchers -- service_principal
# ---------------------------------------------------------------------------


def _fetch_service_principal_from_keyvault(
    settings: dict[str, str], kv: SecretClient
) -> tuple[str, str, str]:
    tenant_id = kv.get_secret(settings["TENANT_ID_KEY"]).value
    client_id = kv.get_secret(settings["CLIENT_ID_KEY"]).value
    client_secret = kv.get_secret(settings["CLIENT_SECRET_KEY"]).value
    if not tenant_id or not client_id or not client_secret:
        raise ValueError("One or more KeyVault secrets returned no value.")
    return tenant_id, client_id, client_secret


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


def get_service_principal(
    settings: dict[str, str], kv: SecretClient | None = None
) -> tuple[str, str, str]:
    """Retrieve service principal credentials from the configured backend.

    Returns:
        ``(tenant_id, client_id, client_secret)``
    """
    backend = settings.get("CREDENTIAL_BACKEND", "keyvault").lower()
    if backend == "keyring":
        return _fetch_service_principal_from_keyring(settings)
    if kv is None:
        raise ValueError(
            "kv_client is required when CREDENTIAL_BACKEND is 'keyvault'. "
            "Set CREDENTIAL_BACKEND=keyring in .env.testing to use the OS keyring instead."
        )
    return _fetch_service_principal_from_keyvault(settings, kv)



