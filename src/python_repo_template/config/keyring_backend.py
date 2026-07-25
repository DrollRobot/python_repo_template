"""OS-keyring credential backend (the default).

Stores secrets in the OS-native credential store: Windows Credential Manager,
macOS Keychain, or Linux Secret Service (GNOME Keyring / KWallet). Selected
by ``credential_backend = "keyring"`` in a profile, or by default when no
backend is named. Secrets are namespaced per profile via the keyring
*service* (``python_repo_template:<profile>``); the *username* slot holds the
schema field name.

Headless hosts (Linux without D-Bus/SecretService, containers, CI) have no
usable keyring; ``keyring`` then selects a fail/null backend. That is
detected on first use and raised as an actionable error naming the
alternatives -- this backend never falls back to writing plaintext anywhere.

To remove the keyring backend entirely: delete this file, its test module
``tests/test_keyring_backend.py``, and the marked ``keyring`` line in
``pyproject.toml``'s ``[project] dependencies`` (then run ``uv lock`` and
``uv sync``). With this file gone there are no ``keyring`` imports anywhere,
and profiles must select another backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import keyring
import keyring.backends.chainer
import keyring.backends.fail
import keyring.errors

from python_repo_template.config.schema import CLI_NAME, ConfigError

# Version of this module. It ships to projects generated from this template,
# so bump on every change to let scripts/compare_to_template.py flag stale
# copies: patch = bugfix, minor = new behavior, major = breaking change.
__version__ = "1.0.1"


def _require_usable_backend() -> None:
    """Raise when the active keyring backend cannot actually store secrets.

    Raises:
        ConfigError: On fail/null backends (headless Linux, containers), with
            the env-var and Key Vault alternatives named.
    """
    active = keyring.get_keyring()
    unusable = isinstance(active, keyring.backends.fail.Keyring) or (
        isinstance(active, keyring.backends.chainer.ChainerBackend) and not active.backends
    )
    if unusable:
        raise ConfigError(
            "No usable OS keyring backend is available on this host (common on headless "
            "Linux and containers). Provide secrets via environment variables instead, or "
            'use the Azure Key Vault backend (credential_backend = "keyvault"). Secrets '
            "are never written to disk as a fallback."
        )


def get(key: str, service: str, config: Mapping[str, Any]) -> str | None:
    """Read secret *key* from the OS keyring.

    Args:
        key: Schema field name of the secret.
        service: Profile-scoped keyring service name.
        config: Merged reserved-key table (unused by this backend).

    Returns:
        The stored value, or None when not present.

    Raises:
        ConfigError: When no usable keyring backend exists.
    """
    _require_usable_backend()
    return keyring.get_password(service, key)


def set(key: str, value: str, service: str, config: Mapping[str, Any]) -> None:
    """Store secret *key* in the OS keyring.

    Args:
        key: Schema field name of the secret.
        value: The secret value.
        service: Profile-scoped keyring service name.
        config: Merged reserved-key table (unused by this backend).

    Raises:
        ConfigError: When no usable keyring backend exists.
    """
    _require_usable_backend()
    keyring.set_password(service, key, value)


def delete(key: str, service: str, config: Mapping[str, Any]) -> None:
    """Delete secret *key* from the OS keyring.

    Args:
        key: Schema field name of the secret.
        service: Profile-scoped keyring service name.
        config: Merged reserved-key table (unused by this backend).

    Raises:
        ConfigError: When no usable keyring backend exists, or the key is not
            stored (so a typo'd delete fails loudly instead of silently).
    """
    _require_usable_backend()
    try:
        keyring.delete_password(service, key)
    except keyring.errors.PasswordDeleteError as exc:
        raise ConfigError(
            f"No secret named {key!r} is stored for service {service!r}; nothing deleted. "
            f"Run '{CLI_NAME} show' to see which secrets are set."
        ) from exc
