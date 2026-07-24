"""Credential-backend dispatcher.

Routes secret reads/writes to whichever backend a profile selects with its
``credential_backend`` key (default ``"keyring"``). Dispatch is by naming
convention -- ``credential_backend = "<name>"`` lazily imports
``python_repo_template.config.<name>_backend`` -- so this module never names
a concrete backend and deleting a backend file removes it completely without
touching this file.

Backend module contract (structural, checked at call time):

- ``get(key, service, config) -> str | None``  -- required. Return the secret
  value, or None when the key is not stored.
- ``set(key, value, service, config) -> None`` -- optional. A backend without
  it is read-only; ``set_secret`` reports how to update the secret instead.
- ``delete(key, service, config) -> None``     -- optional, as above.

Where ``key`` is the schema field name, ``service`` is the profile-scoped
namespace (``python_repo_template:<profile>``), and ``config`` is the merged
reserved-key table (``credential_backend``, ``keyvault_url``, ...) for the
active profile.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any, Protocol, cast

from python_repo_template.config.schema import APP_NAME, ConfigError

# The reserved profile key selecting the backend, and its default.
BACKEND_KEY = "credential_backend"
DEFAULT_BACKEND = "keyring"


class _CredentialBackend(Protocol):
    """Structural contract implemented by every ``<name>_backend`` module."""

    def get(self, key: str, service: str, config: Mapping[str, Any]) -> str | None: ...


def service_name(profile: str | None) -> str:
    """Return the backend namespace for *profile*.

    Namespacing by profile keeps multi-tenant secrets apart (e.g. two tenants
    both storing ``client_secret``).

    Args:
        profile: Active profile name, or None for bare top-level mode.

    Returns:
        ``python_repo_template:<profile>``, or just ``python_repo_template``
        when no profile is active.
    """
    return f"{APP_NAME}:{profile}" if profile else APP_NAME


def backend_name(config: Mapping[str, Any]) -> str:
    """Return the backend selected by *config* (default ``keyring``).

    Args:
        config: Merged reserved-key table for the active profile.

    Returns:
        The backend name.

    Raises:
        ConfigError: If ``credential_backend`` is present but not a string.
    """
    name = config.get(BACKEND_KEY, DEFAULT_BACKEND)
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{BACKEND_KEY!r} must be a non-empty string, got {name!r}.")
    return name


def _load_backend(name: str) -> Any:
    """Import and return the ``<name>_backend`` module, by convention.

    Deliberately name-agnostic: any ``python_repo_template/config/<name>_backend.py``
    implementing the contract above is a valid backend.

    Args:
        name: Backend name from ``credential_backend``.

    Returns:
        The imported backend module.

    Raises:
        ConfigError: If the module does not exist -- the backend was removed,
            or the name is misspelled.
    """
    module_name = f"{__package__}.{name}_backend"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        path = module_name.replace(".", "/") + ".py"
        raise ConfigError(
            f"{BACKEND_KEY}={name!r} but {path} is missing. Restore it, or set "
            f"{BACKEND_KEY} to a backend that exists."
        ) from exc


def get_secret(key: str, profile: str | None, config: Mapping[str, Any]) -> str | None:
    """Read secret *key* from the profile's backend.

    Args:
        key: Schema field name of the secret.
        profile: Active profile name, or None.
        config: Merged reserved-key table for the active profile.

    Returns:
        The secret value, or None when not stored.

    Raises:
        ConfigError: If the backend is missing or unusable.
    """
    backend = cast("_CredentialBackend", _load_backend(backend_name(config)))
    return backend.get(key, service_name(profile), config)


def set_secret(key: str, value: str, profile: str | None, config: Mapping[str, Any]) -> None:
    """Store secret *key* in the profile's backend.

    Args:
        key: Schema field name of the secret.
        value: The secret value.
        profile: Active profile name, or None.
        config: Merged reserved-key table for the active profile.

    Raises:
        ConfigError: If the backend is missing, unusable, or read-only.
    """
    name = backend_name(config)
    backend = _load_backend(name)
    setter = getattr(backend, "set", None)
    if setter is None:
        raise ConfigError(_read_only_message(name, backend))
    setter(key, value, service_name(profile), config)


def delete_secret(key: str, profile: str | None, config: Mapping[str, Any]) -> None:
    """Delete secret *key* from the profile's backend.

    Args:
        key: Schema field name of the secret.
        profile: Active profile name, or None.
        config: Merged reserved-key table for the active profile.

    Raises:
        ConfigError: If the backend is missing, unusable, or read-only.
    """
    name = backend_name(config)
    backend = _load_backend(name)
    deleter = getattr(backend, "delete", None)
    if deleter is None:
        raise ConfigError(_read_only_message(name, backend))
    deleter(key, service_name(profile), config)


def _read_only_message(name: str, backend: Any) -> str:
    """Build the error for write attempts against a read-only backend.

    Args:
        name: Backend name.
        backend: The backend module (may document its own update route in a
            ``READ_ONLY_HINT`` string).

    Returns:
        An actionable message naming how to update the secret instead.
    """
    hint = getattr(backend, "READ_ONLY_HINT", "update the secret at its source instead")
    return f"The {name!r} credential backend is read-only: {hint}."
