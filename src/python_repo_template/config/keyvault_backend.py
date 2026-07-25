"""Azure Key Vault credential backend (opt-in, read-only).

Selected by ``credential_backend = "keyvault"`` in a profile, which must also
set ``keyvault_url``. Reads secrets with ``DefaultAzureCredential`` (``az
login``, managed identity, service principal env vars, ...); values are held
in memory only and never cached to disk.

Read-only by design: writing to Key Vault involves RBAC, soft-delete, and
content-type concerns that belong with the vault's owner. Update secrets via
the Azure portal or ``az keyvault secret set``; the config CLI repeats that
hint when asked to write to a keyvault profile.

Key Vault secret names cannot contain underscores, so schema field names are
mapped ``client_secret`` -> ``client-secret`` for lookup.

This is the only module in the package that imports ``azure-*``. To remove
the Key Vault backend entirely: delete this file and the two azure lines in
``pyproject.toml`` (in ``[project] dependencies`` and the ``dev`` group),
then run ``uv lock`` and ``uv sync``. With this file gone there are no
``azure`` imports anywhere.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from python_repo_template.config.schema import ConfigError

# secrets.py includes this hint when a write is attempted against this backend.
READ_ONLY_HINT = (
    "update the secret in Azure instead (portal, or "
    "'az keyvault secret set --vault-name <vault> --name <name> --value <value>')"
)


def get(key: str, service: str, config: Mapping[str, Any]) -> str | None:
    """Read secret *key* from the profile's Key Vault.

    Args:
        key: Schema field name of the secret; underscores map to hyphens in
            the vault secret name.
        service: Profile-scoped namespace (unused; the vault itself scopes
            the profile).
        config: Merged reserved-key table; must contain ``keyvault_url``.

    Returns:
        The secret value, or None when the vault has no such secret.

    Raises:
        ConfigError: When ``keyvault_url`` is missing/invalid or the azure
            packages are not installed.
    """
    vault_url = config.get("keyvault_url")
    if not isinstance(vault_url, str) or not vault_url:
        raise ConfigError(
            "credential_backend is 'keyvault' but 'keyvault_url' is not set for this "
            'profile. Add keyvault_url = "https://<vault>.vault.azure.net/" to it.'
        )
    try:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ModuleNotFoundError as exc:
        raise ConfigError(
            "The Key Vault backend needs the azure-identity and azure-keyvault-secrets "
            "packages; run 'uv sync' (they are regular dependencies)."
        ) from exc

    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    try:
        return client.get_secret(key.replace("_", "-")).value
    except ResourceNotFoundError:
        return None
