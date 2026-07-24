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
from python_repo_template.config import paths
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
    path = config_path if config_path is not None else paths.config_path()
    overrides = overrides or {}

    document = config_file.read_config(path)
    config = document if document is not None else {}
    config_file.validate_config(config, schema, path)

    profile_name = _select_profile_name(profile, config, path)
    profile_values = config_file.profile_table(config, profile_name, path)

    hints = get_type_hints(schema)
    values: dict[str, Any] = {}
    missing: list[str] = []
    for f in fields(schema):
        if f.name in overrides:
            values[f.name] = _check_type(f.name, overrides[f.name], hints[f.name], "overrides")
            continue
        env_name = ENV_PREFIX + f.name.upper()
        env_raw = os.environ.get(env_name)
        if env_raw is not None:
            values[f.name] = _coerce_env(env_name, env_raw, hints[f.name])
            continue
        # --- secret backend layer (keyring / Key Vault) inserted here in a
        # --- later phase; secret fields below this line resolve from the
        # --- config file only via validate_config's rejection, i.e. never.
        if not is_secret(f):
            if f.name in profile_values:
                where = f"profile 'profiles.{profile_name}' in {path}"
                values[f.name] = _check_type(f.name, profile_values[f.name], hints[f.name], where)
                continue
            if f.name in config:
                where = f"the top level of {path}"
                values[f.name] = _check_type(f.name, config[f.name], hints[f.name], where)
                continue
        if is_required(f):
            missing.append(f.name)

    if missing:
        raise ConfigError(_missing_message(missing, document is not None, path, profile_name))
    return schema(**values)


def _select_profile_name(
    explicit: str | None,
    config: dict[str, Any],
    path: Path,
) -> str | None:
    """Pick the active profile name, or None for bare top-level mode.

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


def _coerce_env(env_name: str, raw: str, tp: Any) -> Any:
    """Convert the env-var string *raw* to the field type *tp*.

    Environment variables are the only untyped layer (TOML values arrive
    typed; overrides come from typed code), so this is the only place string
    conversion happens. Supported types: ``str``, ``int``, ``float``,
    ``bool`` (``true``/``false``, case-insensitive), and ``list[str]``
    (comma-separated).

    Args:
        env_name: Full environment-variable name, for error messages.
        raw: The raw string value.
        tp: The field's resolved type annotation.

    Returns:
        The converted value.

    Raises:
        ConfigError: When the string cannot be converted to *tp*, or *tp* is
            not supported for env-var overrides.
    """
    if tp is str:
        return raw
    if tp is bool:
        lowered = raw.strip().lower()
        if lowered in ("true", "false"):
            return lowered == "true"
        raise ConfigError(f"{env_name} must be 'true' or 'false', got {raw!r}.")
    if tp is int:
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise ConfigError(f"{env_name} must be an integer, got {raw!r}.") from exc
    if tp is float:
        try:
            return float(raw.strip())
        except ValueError as exc:
            raise ConfigError(f"{env_name} must be a number, got {raw!r}.") from exc
    if get_origin(tp) is list and get_args(tp) == (str,):
        return [item.strip() for item in raw.split(",") if item.strip()]
    raise ConfigError(f"{env_name}: type {tp!r} does not support environment-variable overrides.")


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
    file_exists: bool,
    path: Path,
    profile_name: str | None,
) -> str:
    """Build the actionable error message for missing required values.

    Args:
        missing: Field names that no layer provided.
        file_exists: Whether the config file was found at all.
        path: Config file path.
        profile_name: The active profile, or None for bare top-level mode.

    Returns:
        A single-string message naming every missing field, its env var, and
        the ``init`` command that creates the config interactively.
    """
    env_vars = ", ".join(ENV_PREFIX + name.upper() for name in missing)
    if file_exists:
        source = f"config file {path} (profile: {profile_name or 'none — top-level keys'})"
    else:
        source = f"no config file found at {path}"
    return (
        f"Missing required configuration value(s): {', '.join(missing)}. "
        f"Searched overrides, environment ({env_vars}), and {source}. "
        f"Run '{CLI_NAME} init' to create the config interactively, or set the "
        "environment variable(s) above."
    )
