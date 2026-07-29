"""Credential-backend dispatcher.

Routes secret reads/writes to whichever backend a profile selects with its
``credential_backend`` key. Dispatch is by naming convention --
``credential_backend = "<name>"`` lazily imports
``python_repo_template.config.<name>_backend`` -- so this module never names
a concrete backend and deleting a backend file removes it completely without
touching this file.

There is deliberately no default backend: whether secrets are stored at all,
and where, is the user's choice, made by setting ``credential_backend`` in
config.toml (via the config CLI). With no backend configured, secret reads
simply skip the backend layer (environment variables still work); secret
writes fail loudly naming the available backends.

This module is itself optional. Nothing else in the config system imports it
at module scope, so when the settings schema marks no field ``secret`` the
whole secret-storage machinery -- this file plus every ``*_backend.py`` file
and their tests -- can be deleted without touching the rest of the package.

Backend module contract (structural, checked at call time):

- ``get(key, service, config) -> str | None``  -- required. Return the secret
  value, or None when the key is not stored.
- ``set(key, value, service, config) -> None`` -- optional. A backend without
  it is read-only; ``set_secret`` reports how to update the secret instead.
- ``delete(key, service, config) -> None``     -- optional, as above.
- ``RESERVED_KEYS: dict[str, str]``            -- optional. config.toml keys
  the backend consumes (beyond ``credential_backend``), mapped to help text;
  they become legal profile keys while the backend file exists.
- Third-party imports must live inside the functions that use them, so the
  module itself always imports (backend discovery imports every backend).

Where ``key`` is the schema field name, ``service`` is the profile-scoped
namespace (``python_repo_template:<profile>``), and ``config`` is the merged
reserved-key table for the active profile.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from collections.abc import Mapping
from typing import Any, Protocol, cast

from python_repo_template.config.schema import APP_NAME, CLI_NAME, ConfigError

# Version of this module. It ships to projects generated from this template,
# so bump on every change to let scripts/compare_to_template.py flag stale
# copies: patch = bugfix, minor = new behavior, major = breaking change.
__version__ = "2.0.0"

# The reserved profile key selecting the backend. No default backend exists:
# storing secrets is opt-in, and the user picks the backend, not the code.
BACKEND_KEY = "credential_backend"

# Module-name suffix that marks a file in this package as a backend.
_BACKEND_SUFFIX = "_backend"


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


def available_backends() -> list[str]:
    """Return the names of every backend module importable in this package.

    Discovered by naming convention: ``<name>_backend.py`` in this package is
    the backend ``<name>``. Deleting a backend file removes it from this
    list; adding one adds it, with no registration step. Modules already in
    ``sys.modules`` under the convention count too -- they are exactly as
    importable as files (tests inject in-memory backends this way).

    Returns:
        Sorted backend names (e.g. ``["keyring", "keyvault"]``).
    """
    package = importlib.import_module(__package__)
    names = {
        module.name
        for module in pkgutil.iter_modules(package.__path__)
        if module.name.endswith(_BACKEND_SUFFIX)
    }
    prefix = f"{__package__}."
    names.update(
        tail
        for qualified, module in sys.modules.items()
        if module is not None
        and qualified.startswith(prefix)
        and (tail := qualified.removeprefix(prefix)).endswith(_BACKEND_SUFFIX)
        and "." not in tail
    )
    return sorted(name[: -len(_BACKEND_SUFFIX)] for name in names)


def backend_reserved_keys(name: str) -> dict[str, str]:
    """Return backend *name*'s config keys mapped to their help text.

    Args:
        name: Backend name (e.g. ``"keyvault"``).

    Returns:
        The backend's ``RESERVED_KEYS`` mapping, or ``{}`` when it declares
        none.

    Raises:
        ConfigError: If no such backend module exists.
    """
    keys = getattr(_load_backend(name), "RESERVED_KEYS", {})
    return dict(cast("Mapping[str, str]", keys))


def reserved_key_help() -> dict[str, str]:
    """Return every reserved profile key mapped to its help text.

    Covers :data:`BACKEND_KEY` itself plus each available backend's
    ``RESERVED_KEYS``. The config CLI uses this to prompt for and validate
    backend configuration without naming any backend.

    Returns:
        ``{key: help text}`` for every reserved profile key.
    """
    available = ", ".join(available_backends()) or "none"
    help_map = {
        BACKEND_KEY: f"Secret-storage backend for this profile (available: {available})",
    }
    for name in available_backends():
        help_map.update(backend_reserved_keys(name))
    return help_map


def reserved_profile_keys() -> frozenset[str]:
    """Return every config.toml key reserved for secret-backend machinery.

    The union of :data:`BACKEND_KEY` and each available backend's
    ``RESERVED_KEYS``. ``file.py`` treats these as legal profile keys during
    validation; when this module (or a backend file) is deleted, its keys
    disappear from the legal set and validation rejects them loudly.

    Returns:
        The reserved key names.
    """
    return frozenset(reserved_key_help())


def reserved_config(config: dict[str, Any], profile_values: dict[str, Any]) -> dict[str, Any]:
    """Return the merged reserved-key table for the active profile.

    Reserved keys (see :func:`reserved_profile_keys`) select and configure the
    secret backend. Top-level values act as shared fallbacks; the profile
    table wins where both define a key.

    Args:
        config: Parsed TOML document.
        profile_values: The active profile's table (may be empty).

    Returns:
        The merged reserved-key mapping.
    """
    reserved = reserved_profile_keys()
    merged = {key: value for key, value in config.items() if key in reserved}
    merged.update({key: value for key, value in profile_values.items() if key in reserved})
    return merged


def backend_name(config: Mapping[str, Any]) -> str | None:
    """Return the backend selected by *config*, or None when none is set.

    Args:
        config: Merged reserved-key table for the active profile.

    Returns:
        The backend name, or None when the user has not configured one.

    Raises:
        ConfigError: If ``credential_backend`` is present but not a non-empty
            string.
    """
    name = config.get(BACKEND_KEY)
    if name is None:
        return None
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
    module_name = f"{__package__}.{name}{_BACKEND_SUFFIX}"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise  # the backend exists but something it imports is missing
        available = ", ".join(available_backends()) or "none"
        raise ConfigError(
            f"{BACKEND_KEY}={name!r} but no such backend exists. Available backends: {available}."
        ) from exc


def _require_backend(config: Mapping[str, Any], action: str) -> tuple[str, Any]:
    """Return the configured backend for a write-path *action*, loudly.

    Args:
        config: Merged reserved-key table for the active profile.
        action: Human description of the attempted operation, for the error.

    Returns:
        ``(name, module)`` of the configured backend.

    Raises:
        ConfigError: When no backend is configured, naming the available ones.
    """
    name = backend_name(config)
    if name is None:
        available = ", ".join(available_backends()) or "none"
        raise ConfigError(
            f"Cannot {action}: no {BACKEND_KEY} is configured for this profile. "
            f"Choose one with '{CLI_NAME} set {BACKEND_KEY} <name>' "
            f"(available backends: {available})."
        )
    return name, _load_backend(name)


def get_secret(key: str, profile: str | None, config: Mapping[str, Any]) -> str | None:
    """Read secret *key* from the profile's backend.

    Args:
        key: Schema field name of the secret.
        profile: Active profile name, or None.
        config: Merged reserved-key table for the active profile.

    Returns:
        The secret value, or None when not stored -- including when no
        backend is configured at all (the user may be supplying secrets via
        environment variables instead).

    Raises:
        ConfigError: If a configured backend is missing or unusable.
    """
    name = backend_name(config)
    if name is None:
        return None
    backend = cast("_CredentialBackend", _load_backend(name))
    return backend.get(key, service_name(profile), config)


def set_secret(key: str, value: str, profile: str | None, config: Mapping[str, Any]) -> None:
    """Store secret *key* in the profile's backend.

    Args:
        key: Schema field name of the secret.
        value: The secret value.
        profile: Active profile name, or None.
        config: Merged reserved-key table for the active profile.

    Raises:
        ConfigError: If no backend is configured, or the backend is missing,
            unusable, or read-only.
    """
    name, backend = _require_backend(config, f"store secret {key!r}")
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
        ConfigError: If no backend is configured, or the backend is missing,
            unusable, or read-only.
    """
    name, backend = _require_backend(config, f"delete secret {key!r}")
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
