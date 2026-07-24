"""Precedence engine that resolves settings values from every layer.

Precedence, highest wins:

1. Explicit overrides passed by calling code (e.g. from CLI flags).
2. Environment variables: ``PYTHON_REPO_TEMPLATE_<UPPER_FIELD_NAME>`` — the
   CI/headless path; works with zero files and zero keyring.
3. Secret backend (keyring / Key Vault) — secret fields only.
4. config.toml: the selected profile table, then bare top-level keys.
5. Schema field defaults.

Profile selection, first match wins: the explicit ``profile`` argument, the
``PYTHON_REPO_TEMPLATE_PROFILE`` environment variable, the ``default_profile``
key in config.toml, else bare top-level keys act as the single unnamed
profile.

Missing required values raise ``ConfigError`` with an actionable message; the
resolver never auto-creates config and never silently defaults.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any, cast, get_args, get_origin, get_type_hints

from python_repo_template.config import file as config_file
from python_repo_template.config import paths, secrets
from python_repo_template.config.schema import (
    CLI_NAME,
    ENV_PREFIX,
    ConfigError,
    Settings,
    is_required,
    is_secret,
)

# Environment variable selecting the active profile.
PROFILE_ENV = ENV_PREFIX + "PROFILE"


def load_settings(
    profile: str | None = None,
    overrides: Mapping[str, object] | None = None,
) -> Settings:
    """Resolve and return the effective :class:`Settings`.

    This is the public entry point application code should use::

        settings = load_settings(profile=args.profile)

    Args:
        profile: Profile name to resolve, or None to fall back to
            ``PYTHON_REPO_TEMPLATE_PROFILE`` / ``default_profile`` / bare
            top-level keys.
        overrides: Highest-precedence values (e.g. parsed CLI flags), keyed by
            field name.

    Returns:
        A frozen ``Settings`` instance with every field populated.

    Raises:
        ConfigError: When a required value is missing from every layer, the
            config file is malformed, or a value has the wrong type.
    """
    return cast(
        "Settings",
        resolve_settings(Settings, profile=profile, overrides=overrides),
    )


def resolve_settings(
    schema: type[Any],
    *,
    profile: str | None = None,
    overrides: Mapping[str, object] | None = None,
    config_path: Path | None = None,
) -> Any:
    """Resolve an instance of *schema* through the full precedence chain.

    Generic engine behind :func:`load_settings`. Tests (and downstream repos
    with several settings groups) can run it against any frozen dataclass that
    follows the schema conventions documented in ``schema.py``.

    Args:
        schema: Frozen dataclass defining the options.
        profile: Explicit profile name, or None to auto-select.
        overrides: Highest-precedence values keyed by field name.
        config_path: Config file location; defaults to the standard path (see
            ``paths.py``).

    Returns:
        An instance of *schema* with every field populated.

    Raises:
        ConfigError: When a required value is missing from every layer, the
            config file is malformed, or a value has the wrong type.
    """
    instance, _, _ = _resolve(schema, profile, overrides, config_path)
    return instance


def resolve_with_sources(
    schema: type[Any],
    *,
    profile: str | None = None,
    overrides: Mapping[str, object] | None = None,
    config_path: Path | None = None,
) -> tuple[Any, dict[str, str], str | None]:
    """Resolve *schema* and report where each value came from.

    Used by the config CLI's ``show`` command so "which tenant am I about to
    hit, and why" is answerable in one step.

    Args:
        schema: Frozen dataclass defining the options.
        profile: Explicit profile name, or None to auto-select.
        overrides: Highest-precedence values keyed by field name.
        config_path: Config file location; defaults to the standard path.

    Returns:
        ``(instance, sources, profile_name)`` where ``sources`` maps each
        field name to a provenance label (``override``, ``env:<VAR>``, the
        backend name, ``file:profiles.<name>``, ``file:top-level``, or
        ``default``) and ``profile_name`` is the active profile, if any.

    Raises:
        ConfigError: As for :func:`resolve_settings`.
    """
    return _resolve(schema, profile, overrides, config_path)


def _resolve(
    schema: type[Any],
    profile: str | None,
    overrides: Mapping[str, object] | None,
    config_path: Path | None,
) -> tuple[Any, dict[str, str], str | None]:
    """Shared resolution engine returning the instance, sources, and profile.

    Args:
        schema: Frozen dataclass defining the options.
        profile: Explicit profile name, or None to auto-select.
        overrides: Highest-precedence values keyed by field name.
        config_path: Config file location, or None for the standard path.

    Returns:
        ``(instance, sources, profile_name)`` as documented on
        :func:`resolve_with_sources`.

    Raises:
        ConfigError: When a required value is missing from every layer, the
            config file is malformed, or a value has the wrong type.
    """
    path = config_path if config_path is not None else paths.config_path()
    overrides = overrides or {}

    document = config_file.read_config(path)
    config = document if document is not None else {}
    config_file.validate_config(config, schema, path)

    profile_name = select_profile_name(profile, config, path)
    profile_values = config_file.profile_table(config, profile_name, path)
    backend_config = config_file.reserved_config(config, profile_values)

    hints = get_type_hints(schema)
    secret_names = {f.name for f in fields(schema) if is_secret(f)}
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    missing: list[str] = []
    for f in fields(schema):
        if f.name in overrides:
            values[f.name] = _check_type(f.name, overrides[f.name], hints[f.name], "overrides")
            sources[f.name] = "override"
            continue
        env_name = ENV_PREFIX + f.name.upper()
        env_raw = os.environ.get(env_name)
        if env_raw is not None:
            values[f.name] = coerce_string(env_name, env_raw, hints[f.name])
            sources[f.name] = f"env:{env_name}"
            continue
        if is_secret(f):
            secret = secrets.get_secret(f.name, profile_name, backend_config)
            if secret is not None:
                values[f.name] = secret
                sources[f.name] = secrets.backend_name(backend_config)
                continue
        else:
            if f.name in profile_values:
                where = f"profile 'profiles.{profile_name}' in {path}"
                values[f.name] = _check_type(f.name, profile_values[f.name], hints[f.name], where)
                sources[f.name] = f"file:profiles.{profile_name}"
                continue
            if f.name in config:
                where = f"the top level of {path}"
                values[f.name] = _check_type(f.name, config[f.name], hints[f.name], where)
                sources[f.name] = "file:top-level"
                continue
        if is_required(f):
            missing.append(f.name)
        else:
            sources[f.name] = "default"

    if missing:
        missing_secrets = [name for name in missing if name in secret_names]
        raise ConfigError(
            _missing_message(missing, missing_secrets, document is not None, path, profile_name)
        )
    return schema(**values), sources, profile_name


def select_profile_name(
    explicit: str | None,
    config: dict[str, Any],
    path: Path,
) -> str | None:
    """Pick the active profile name, or None for bare top-level mode.

    Selection order: *explicit* argument, then the ``PYTHON_REPO_TEMPLATE_PROFILE``
    environment variable, then ``default_profile`` in the config file.

    Args:
        explicit: Profile name passed by the caller, if any.
        config: Parsed config document (may be empty).
        path: Config file path, for error messages.

    Returns:
        The selected profile name, or None when no profile applies.

    Raises:
        ConfigError: If a profile was requested but the config file is absent
            or does not define it (raised by ``profile_table`` later for the
            not-defined case; here for the no-file case).
    """
    name = explicit or os.environ.get(PROFILE_ENV) or config.get("default_profile")
    if name is not None and not config:
        raise ConfigError(
            f"Profile {name!r} requested but no config file exists at {path}. "
            f"Run '{CLI_NAME} init --profile {name}' to create it."
        )
    return cast("str | None", name)


def coerce_string(name: str, raw: str, tp: Any) -> Any:
    """Convert the string *raw* to the field type *tp*.

    Environment variables and CLI arguments are the only untyped layers (TOML
    values arrive typed; overrides come from typed code), so this is the only
    place string conversion happens. Supported types: ``str``, ``int``,
    ``float``, ``bool`` (``true``/``false``, case-insensitive), and
    ``list[str]`` (comma-separated).

    Args:
        name: Where the string came from (env-var name or CLI argument), for
            error messages.
        raw: The raw string value.
        tp: The field's resolved type annotation.

    Returns:
        The converted value.

    Raises:
        ConfigError: When the string cannot be converted to *tp*, or *tp* is
            not supported for string input.
    """
    if tp is str:
        return raw
    if tp is bool:
        lowered = raw.strip().lower()
        if lowered in ("true", "false"):
            return lowered == "true"
        raise ConfigError(f"{name} must be 'true' or 'false', got {raw!r}.")
    if tp is int:
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer, got {raw!r}.") from exc
    if tp is float:
        try:
            return float(raw.strip())
        except ValueError as exc:
            raise ConfigError(f"{name} must be a number, got {raw!r}.") from exc
    if get_origin(tp) is list and get_args(tp) == (str,):
        return [item.strip() for item in raw.split(",") if item.strip()]
    raise ConfigError(f"{name}: type {tp!r} does not support string input.")


def _check_type(name: str, value: Any, tp: Any, where: str) -> Any:
    """Verify that *value* matches the field type *tp*; fail loudly otherwise.

    ``bool`` is checked before ``int`` because ``bool`` subclasses ``int`` in
    Python. An ``int`` is accepted for a ``float`` field (TOML users write
    ``1`` for ``1.0``) and converted.

    Args:
        name: Field name, for error messages.
        value: The value found in the layer.
        tp: The field's resolved type annotation.
        where: Human description of the layer, for error messages.

    Returns:
        The value, converted to ``float`` when an int fills a float field.

    Raises:
        ConfigError: When the value's type does not match *tp*.
    """
    ok: bool
    if tp is bool:
        ok = isinstance(value, bool)
    elif tp is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif tp is float:
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        if ok:
            value = float(value)
    elif tp is str:
        ok = isinstance(value, str)
    elif get_origin(tp) is list and get_args(tp) == (str,):
        ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
    else:
        raise ConfigError(f"Field {name!r} has unsupported type {tp!r}.")
    if not ok:
        raise ConfigError(
            f"{name!r} in {where} must be {tp!r}, got {type(value).__name__} ({value!r})."
        )
    return value


def _missing_message(
    missing: list[str],
    missing_secrets: list[str],
    file_exists: bool,
    path: Path,
    profile_name: str | None,
) -> str:
    """Build the actionable error message for missing required values.

    Args:
        missing: Field names that no layer provided.
        missing_secrets: The subset of *missing* that are secret fields.
        file_exists: Whether the config file was found at all.
        path: Config file path.
        profile_name: The active profile, or None for bare top-level mode.

    Returns:
        A single-string message naming every missing field, its env var, and
        the CLI command that provides it.
    """
    env_vars = ", ".join(ENV_PREFIX + name.upper() for name in missing)
    if file_exists:
        source = f"config file {path} (profile: {profile_name or 'none — top-level keys'})"
    else:
        source = f"no config file found at {path}"
    message = (
        f"Missing required configuration value(s): {', '.join(missing)}. "
        f"Searched overrides, environment ({env_vars}), secret backend (secret fields), "
        f"and {source}. "
        f"Run '{CLI_NAME} init' to create the config interactively, or set the "
        "environment variable(s) above."
    )
    if missing_secrets:
        stores = " ".join(f"'{CLI_NAME} set-secret {name}'" for name in missing_secrets)
        message += f" Store missing secret(s) with: {stores}."
    return message
