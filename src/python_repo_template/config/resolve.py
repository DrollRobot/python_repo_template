"""Precedence engine that resolves settings values from every layer.

Precedence, highest wins:

1. Explicit overrides passed by calling code (e.g. from CLI flags).
2. Environment variables: ``<ENV_PREFIX><UPPER_FIELD_NAME>`` - the
   CI/headless path; works with zero files and zero secret storage.
3. Credential backend - secret fields only, and only when a backend is
   selected: the profile's ``credential_backend`` key, else the schema's
   ``CREDENTIAL_BACKEND`` default (never under the ``"none"``/``"prompt"``
   policies). Secrets are looked up under the profile's
   ``<field>_secret_name`` from config.toml, written by the config CLI; a
   missing name fails loudly for a required secret, and leaves an optional
   one unset.
4. config.toml: the selected profile table, then bare top-level keys.
5. Schema field defaults - non-secret fields only. Secret values never
   live in source; a schema giving a secret field a default value is
   rejected before resolution starts. An optional secret
   (``secret(required=False)``) that no layer provides is None.

The secret layer is optional: ``secrets.py`` is imported lazily and only
when the schema marks at least one field ``secret``, so a schema without
secret fields runs with the secret-storage machinery deleted entirely.

Profile selection, first match wins: the explicit ``profile`` argument, the
``<ENV_PREFIX>PROFILE`` environment variable, the ``default_profile`` key in
config.toml, else bare top-level keys act as the single unnamed profile.

``ENV_PREFIX`` is defined in ``schema.py`` as the app name upper-cased plus
an underscore; the two variables named above are :data:`PROFILE_ENV` here
and ``paths.CONFIG_DIR_ENV``.

Missing required values raise ``ConfigError`` with an actionable message; the
resolver never auto-creates config and never silently defaults.
:func:`inspect_settings` is the one exception to raising: it reports every
problem alongside the values that did resolve, for the config CLI's ``show``.
"""

from __future__ import annotations

import importlib
import os
import types
import typing
from collections.abc import Iterable, Mapping
from dataclasses import Field, dataclass, fields
from pathlib import Path
from typing import Any, cast, get_args, get_origin, get_type_hints

from python_repo_template.config import file as config_file
from python_repo_template.config import paths
from python_repo_template.config.schema import (
    CLI_NAME,
    ENV_PREFIX,
    ConfigError,
    Settings,
    default_secret_name,
    field_default,
    is_optional_secret,
    is_required,
    is_secret,
)

# Environment variable selecting the active profile.
PROFILE_ENV = ENV_PREFIX + "PROFILE"

# Provenance labels for fields that did not resolve (see SettingsReport).
MISSING = "missing"
ERROR = "error"


@dataclass(frozen=True)
class SettingsReport:
    """What resolution found, including what it could not resolve.

    Returned by :func:`inspect_settings`, which never raises for a missing
    or invalid value, so a partial config can still be shown in full.

    Attributes:
        values: Field name to resolved value, for every field that resolved,
            defaults and an unset optional secret's None included. A field
            that did not resolve is absent.
        sources: Field name to provenance label, for every field:
            ``override``, ``env:<VAR>``, the backend name,
            ``file:profiles.<name>``, ``file:top-level``, ``default``,
            ``not set`` (an optional secret no layer provides), or, for a
            field absent from *values*, :data:`MISSING` (a required value no
            layer provides) or :data:`ERROR` (its value failed a check).
        profile_name: The active profile, or None for bare top-level mode.
        problems: Every problem found, in order, each naming its fix. Empty
            when :func:`load_settings` would succeed.
    """

    values: dict[str, Any]
    sources: dict[str, str]
    profile_name: str | None
    problems: list[str]


def load_settings(
    profile: str | None = None,
    overrides: Mapping[str, object] | None = None,
) -> Settings:
    """Resolve and return the effective :class:`Settings`.

    This is the public entry point application code should use::

        settings = load_settings(profile=args.profile)

    Args:
        profile: Profile name to resolve, or None to fall back to
            :data:`PROFILE_ENV` / ``default_profile`` / bare top-level keys.
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
    values, _, _ = _resolve(schema, profile, overrides, config_path, None)
    return schema(**values)


def inspect_settings(
    schema: type[Any],
    *,
    profile: str | None = None,
    overrides: Mapping[str, object] | None = None,
    config_path: Path | None = None,
) -> SettingsReport:
    """Resolve *schema* as far as possible and report every problem found.

    Used by the config CLI's ``show`` command, so the user sees the whole
    config, and where each value came from, even when some values are
    missing or invalid. Each problem is reported with the message
    :func:`resolve_settings` would raise for it.

    Args:
        schema: Frozen dataclass defining the options.
        profile: Explicit profile name, or None to auto-select.
        overrides: Highest-precedence values keyed by field name.
        config_path: Config file location; defaults to the standard path.

    Returns:
        The report: resolved values, every field's provenance, the active
        profile, and the problems.

    Raises:
        ConfigError: Only when there is nothing to resolve against: the
            schema itself is invalid, the file is not valid TOML, its
            ``default_profile`` or ``profiles`` is malformed, or the selected
            profile does not exist.
    """
    problems: list[str] = []
    values, sources, profile_name = _resolve(schema, profile, overrides, config_path, problems)
    return SettingsReport(values, sources, profile_name, problems)


def _resolve(
    schema: type[Any],
    profile: str | None,
    overrides: Mapping[str, object] | None,
    config_path: Path | None,
    problems: list[str] | None,
) -> tuple[dict[str, Any], dict[str, str], str | None]:
    """Shared resolution engine returning the values, sources, and profile.

    Args:
        schema: Frozen dataclass defining the options.
        profile: Explicit profile name, or None to auto-select.
        overrides: Highest-precedence values keyed by field name.
        config_path: Config file location, or None for the standard path.
        problems: None to raise at the first problem, as loading does. A list
            to append each problem's message to instead and keep going, as
            :func:`inspect_settings` does; a field with a problem is then
            left out of the values.

    Returns:
        ``(values, sources, profile_name)``, as the matching
        :class:`SettingsReport` attributes. With *problems* None, *values*
        holds every field, ready to build *schema*.

    Raises:
        ConfigError: With *problems* None, when a required value is missing
            from every layer, the config file is malformed, or a value has
            the wrong type. Otherwise only as :func:`inspect_settings`
            documents.
    """
    _validate_schema_secrets(schema)
    path = config_path if config_path is not None else paths.config_path()
    overrides = overrides or {}

    document = config_file.read_config(path)
    config = document if document is not None else {}
    config_file.validate_config(config, schema, path, problems)

    profile_name = select_profile_name(profile, config, path)
    profile_values = config_file.profile_table(config, profile_name, path)

    hints = get_type_hints(schema)
    secret_names = {f.name for f in fields(schema) if is_secret(f)}
    secrets_module: Any = _secrets_module(secret_names) if secret_names else None
    backend_config: dict[str, Any] = (
        secrets_module.reserved_config(config, profile_values) if secrets_module else {}
    )
    layers = _Layers(path, config, profile_name, profile_values, secrets_module, backend_config)
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    missing: list[str] = []
    for f in fields(schema):
        # An optional secret is annotated 'str | None' (checked above): any
        # value a layer provides is a str, and None only means "not set".
        tp = str if is_optional_secret(f) else hints[f.name]
        try:
            found = _resolve_field(f, tp, overrides, layers)
        except ConfigError as exc:
            if problems is None:
                raise
            if str(exc) not in problems:  # e.g. one unusable backend, many secrets
                problems.append(str(exc))
            sources[f.name] = ERROR
            continue
        if found is not None:
            values[f.name], sources[f.name] = found
        elif is_required(f):
            missing.append(f.name)
            sources[f.name] = MISSING
        else:
            values[f.name] = field_default(f)
            sources[f.name] = "not set" if is_optional_secret(f) else "default"

    if missing:
        missing_secrets = [name for name in missing if name in secret_names]
        backend_hint = None
        if missing_secrets and secrets_module.schema_backend_policy() == "none":
            backend_hint = (
                "schema.CREDENTIAL_BACKEND is 'none': secrets come from environment variables only."
            )
            missing_secrets = []  # there is no set-secret command to point at
        elif missing_secrets and secrets_module.backend_name(backend_config) is None:
            available = ", ".join(secrets_module.available_backends()) or "none"
            backend_hint = (
                f"No credential_backend is configured; choose one with "
                f"'{CLI_NAME} set credential_backend <name>' (available: {available})."
            )
        if profile_name is None and config.get("profiles"):
            message = _no_profile_message(path, sorted(config["profiles"]))
        else:
            message = _missing_message(
                missing, missing_secrets, document is not None, path, profile_name, backend_hint
            )
        if problems is None:
            raise ConfigError(message)
        problems.append(message)
    return values, sources, profile_name


@dataclass(frozen=True)
class _Layers:
    """The per-run inputs every field is resolved against.

    Attributes:
        path: Config file path, for error messages.
        config: Parsed config document (empty when there is no file).
        profile_name: The active profile, or None for bare top-level mode.
        profile_values: The active profile's table (empty for None).
        secrets_module: The secret machinery, or None when the schema has
            no secret fields.
        backend_config: Merged reserved-key table for the active profile.
    """

    path: Path
    config: dict[str, Any]
    profile_name: str | None
    profile_values: dict[str, Any]
    secrets_module: Any
    backend_config: dict[str, Any]


def _resolve_field(
    f: Field[Any], tp: Any, overrides: Mapping[str, object], layers: _Layers
) -> tuple[Any, str] | None:
    """Return field *f*'s value and provenance from the first layer that has it.

    Covers the overrides, environment, credential backend, and file layers;
    the caller applies the default layer.

    Args:
        f: The schema field.
        tp: The type its value must have.
        overrides: Highest-precedence values keyed by field name.
        layers: The run's config file, profile, and backend inputs.

    Returns:
        ``(value, source)``, or None when no layer provides the field.

    Raises:
        ConfigError: When a layer's value has the wrong type, or the
            credential backend fails or is misconfigured.
    """
    if f.name in overrides:
        return _check_type(f.name, overrides[f.name], tp, "overrides"), "override"
    env_name = ENV_PREFIX + f.name.upper()
    env_raw = os.environ.get(env_name)
    if env_raw is not None:
        return coerce_string(env_name, env_raw, tp), f"env:{env_name}"
    if is_secret(f):
        secrets_module = layers.secrets_module
        secret = secrets_module.get_secret(
            f.name, layers.profile_name, layers.backend_config, optional=is_optional_secret(f)
        )
        if secret is None:
            return None
        return secret, secrets_module.backend_name(layers.backend_config)
    if f.name in layers.profile_values:
        where = f"profile 'profiles.{layers.profile_name}' in {layers.path}"
        value = _check_type(f.name, layers.profile_values[f.name], tp, where)
        return value, f"file:profiles.{layers.profile_name}"
    if f.name in layers.config:
        where = f"the top level of {layers.path}"
        return _check_type(f.name, layers.config[f.name], tp, where), "file:top-level"
    return None


def _validate_schema_secrets(schema: type[Any]) -> None:
    """Reject schemas that mishandle secret fields, before any resolution.

    A default on a secret field is a secret value in source, silently used
    whenever the backend lookup misses. ``schema.secret(...)`` cannot
    express one; this guard catches hand-written ``field(...)``
    declarations that can. The one exception is an optional secret's None,
    which means "not set": it must be exactly None, on a field annotated
    ``str | None``.

    Args:
        schema: Frozen dataclass defining the options.

    Raises:
        ConfigError: When a secret field has a default value or leaves
            ``repr`` enabled, an optional secret's default is not None or
            its annotation is not ``str | None``, or a non-secret field
            declares a ``default_secret_name``.
    """
    hints = get_type_hints(schema)
    for f in fields(schema):
        if not is_secret(f):
            if default_secret_name(f) is not None:
                raise ConfigError(
                    f"Field {f.name!r} declares default_secret_name but is not secret. "
                    "Storage names apply only to secret fields."
                )
            continue
        if is_optional_secret(f):
            # The default's value is never echoed: it may be a real secret.
            if f.default is not None:
                raise ConfigError(
                    f"Optional secret field {f.name!r} must have the default None and no "
                    "other: any other default is a secret value in source. Declare it with "
                    "schema.secret(..., required=False)."
                )
            if not _is_optional_str(hints[f.name]):
                raise ConfigError(
                    f"Optional secret field {f.name!r} must be annotated 'str | None': it is "
                    "None when no layer provides it."
                )
        elif not is_required(f):
            raise ConfigError(
                f"Secret field {f.name!r} has a default value: a secret value in source. "
                "Secrets never live in source. Remove the default; declare the field with "
                f"schema.secret(...) and store the value with '{CLI_NAME} set-secret "
                f"{f.name}'."
            )
        if f.repr:
            raise ConfigError(
                f"Secret field {f.name!r} must set repr=False so its value cannot leak "
                "through repr()/tracebacks. Declare it with schema.secret(...)."
            )


def _is_optional_str(tp: Any) -> bool:
    """Return True if the type annotation *tp* is ``str | None``.

    Accepts both spellings, ``str | None`` and ``Optional[str]``, which older
    Python versions build as two distinct union types.

    Args:
        tp: A resolved type annotation.

    Returns:
        True when *tp* is a union of exactly ``str`` and ``None``.
    """
    return get_origin(tp) in (types.UnionType, typing.Union) and set(get_args(tp)) == {
        str,
        type(None),
    }


def _secrets_module(secret_fields: Iterable[str]) -> Any:
    """Import and return the secret-machinery module (``secrets.py``), lazily.

    Called only when the schema marks at least one field ``secret``, so a
    schema without secret fields never touches -- and can be shipped
    without -- the secret-storage machinery.

    Args:
        secret_fields: The schema's secret field names, for the error message.

    Returns:
        The imported ``secrets`` module.

    Raises:
        ConfigError: When ``secrets.py`` has been removed from the package
            while the schema still declares secret fields.
    """
    module_name = f"{__package__}.secrets"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        raise ConfigError(
            f"The settings schema marks {', '.join(sorted(secret_fields))} as secret, but "
            "the secret-storage machinery (secrets.py) has been removed from this package. "
            "Restore it (plus at least one *_backend.py module), or remove the "
            '"secret": True fields from the schema.'
        ) from exc


def select_profile_name(
    explicit: str | None,
    config: dict[str, Any],
    path: Path,
) -> str | None:
    """Pick the active profile name, or None for bare top-level mode.

    Selection order: *explicit* argument, then the :data:`PROFILE_ENV`
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


def _no_profile_message(path: Path, profile_names: list[str]) -> str:
    """Build the error message for a file with profiles when none is selected.

    Reporting missing values misleads here: the values usually sit in a
    profile, and the real fault is that no profile was picked.

    Args:
        path: Config file path.
        profile_names: The profiles the file defines, sorted.

    Returns:
        A single-string message listing the profiles and every way to select one.
    """
    return (
        f"No profile selected. Profiles defined in {path}: {', '.join(profile_names)}. "
        "Select one with --profile NAME or "
        f"the {PROFILE_ENV} environment variable, or set a default with "
        f"'{CLI_NAME} use NAME'."
    )


def _missing_message(
    missing: list[str],
    missing_secrets: list[str],
    file_exists: bool,
    path: Path,
    profile_name: str | None,
    backend_hint: str | None,
) -> str:
    """Build the actionable error message for missing required values.

    Args:
        missing: Field names that no layer provided.
        missing_secrets: The subset of *missing* that are secret fields.
        file_exists: Whether the config file was found at all.
        path: Config file path.
        profile_name: The active profile, or None for bare top-level mode.
        backend_hint: Extra sentence naming how to configure a credential
            backend, when secrets are missing and none is configured.

    Returns:
        A single-string message naming every missing field, its env var, and
        the CLI command that provides it.
    """
    env_vars = ", ".join(ENV_PREFIX + name.upper() for name in missing)
    if file_exists:
        source = f"config file {path} (profile: {profile_name or 'none - top-level keys'})"
    else:
        source = f"no config file found at {path}"
    message = (
        f"Missing required configuration value(s): {', '.join(missing)}. "
        f"Searched overrides, environment ({env_vars}), credential backend (secret fields), "
        f"and {source}. "
        f"Run '{CLI_NAME} init' to create the config interactively, or set the "
        "environment variable(s) above."
    )
    if backend_hint:
        message += f" {backend_hint}"
    if missing_secrets:
        stores = " ".join(f"'{CLI_NAME} set-secret {name}'" for name in missing_secrets)
        message += f" Store missing secret(s) with: {stores}."
    return message
