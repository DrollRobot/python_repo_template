"""
Azure KeyVault credential backend (OPT-IN).

Implements the backend contract used by ``tests/_bootstrap.py`` when
``CREDENTIAL_BACKEND=keyvault``. This is the *only* module in the repo that
imports ``azure-*``.

To remove Azure entirely: delete this file, delete the ``keyvault`` dependency
group and its ``dev`` include in ``pyproject.toml``, and drop the ``KEYVAULT_*``
block from ``.env.example`` (leave ``CREDENTIAL_BACKEND=keyring``). With this
file gone there are no ``azure`` imports anywhere, so mypy passes without
``azure-identity`` / ``azure-keyvault-secrets`` installed.

Requires ``KEYVAULT_TENANT_ID``, ``KEYVAULT_URL``, and a valid
``DefaultAzureCredential`` (e.g. ``az login``). The ``*_KEY`` env vars hold the
KeyVault secret names.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from azure.keyvault.secrets import SecretClient


def build_kv_client(settings: dict[str, str]) -> SecretClient:
    """Create and return an Azure KeyVault SecretClient.

    Raises:
        ImportError: if azure-identity or azure-keyvault-secrets are missing.
        Exception: if DefaultAzureCredential cannot authenticate.
    """
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    os.environ.setdefault("AZURE_TENANT_ID", settings["KEYVAULT_TENANT_ID"])
    credential = DefaultAzureCredential()
    return SecretClient(vault_url=settings["KEYVAULT_URL"], credential=credential)


def get_user_pass(settings: dict[str, str]) -> tuple[str, str]:
    """Retrieve username and password from KeyVault.

    Returns:
        ``(username, password)``
    """
    kv = build_kv_client(settings)
    username = kv.get_secret(settings["USERNAME_KEY"]).value
    password = kv.get_secret(settings["PASSWORD_KEY"]).value
    if not username or not password:
        raise ValueError("One or more KeyVault secrets returned no value.")
    return username, password


def get_cert_thumbprint(settings: dict[str, str]) -> str:
    """Retrieve the certificate thumbprint from KeyVault.

    Returns:
        Certificate thumbprint string.
    """
    kv = build_kv_client(settings)
    thumbprint = kv.get_secret(settings["CERT_THUMBPRINT_KEY"]).value
    if not thumbprint:
        raise ValueError("Certificate thumbprint KeyVault secret returned no value.")
    return thumbprint


def get_service_principal(settings: dict[str, str]) -> tuple[str, str, str]:
    """Retrieve service principal credentials from KeyVault.

    Returns:
        ``(tenant_id, client_id, client_secret)``
    """
    kv = build_kv_client(settings)
    tenant_id = kv.get_secret(settings["TENANT_ID_KEY"]).value
    client_id = kv.get_secret(settings["CLIENT_ID_KEY"]).value
    client_secret = kv.get_secret(settings["CLIENT_SECRET_KEY"]).value
    if not tenant_id or not client_id or not client_secret:
        raise ValueError("One or more KeyVault secrets returned no value.")
    return tenant_id, client_id, client_secret
