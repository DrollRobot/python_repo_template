"""Single source of truth for this package's configuration options.

The :class:`Settings` dataclass below is THE one place where configuration
option names, types, defaults, secret classification, and help text are
defined. Everything else derives from it by iterating
``dataclasses.fields(Settings)``:

- ``resolve.py`` resolves each field through the precedence layers.
- ``file.py`` validates config.toml keys against the field names.
- The config CLI prompts and validates per field.
- Tests build fake configurations from the same field list.

To add, rename, or remove an option: edit :class:`Settings`; nothing else
needs changing.

Field conventions:

- Fields without a default are required; resolution fails loudly when one is
  missing from every layer.
- ``metadata={"help": ...}`` provides the human description used in prompts
  and generated comments.
- ``metadata={"secret": True}`` marks a secret: its value lives in the
  user-selected credential backend (see ``secrets.py``), never in
  config.toml, and it must also set ``repr=False`` so the value cannot leak
  through ``repr()``/tracebacks (enforced by a unit test). A schema with no
  secret fields never touches the secret machinery, which can then be
  removed from the package entirely.
"""

from __future__ import annotations

from dataclasses import MISSING, Field, dataclass, field
from typing import Any

# The application name, used for the config directory, env-var prefix, CLI
# name, and keyring service name. Kept as a literal (not derived from
# __package__) so a project-wide find-replace of the package name updates it.
APP_NAME = "python_repo_template"

# Prefix for every environment-variable override: the app name upper-cased,
# so the field 'tenant_id' is overridden by <ENV_PREFIX>TENANT_ID.
ENV_PREFIX = APP_NAME.upper() + "_"

# Name of the config CLI's console script, used in error messages.
CLI_NAME = APP_NAME.replace("_", "-") + "-config"


class ConfigError(Exception):
    """Raised for any configuration problem: missing, malformed, or mistyped values."""


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for one profile.

    Instances are produced by :func:`python_repo_template.config.load_settings`
    after applying the full precedence chain (explicit overrides > env vars >
    secret backend > config.toml > field defaults). Application code should
    treat instances as immutable value objects.
    """

    # FIXME: replace these example fields with your project's options.
    tenant_id: str = field(metadata={"help": "Entra tenant ID"})
    client_id: str = field(metadata={"help": "App registration client ID"})
    client_secret: str = field(
        repr=False, metadata={"secret": True, "help": "App registration client secret"}
    )
    api_url: str = field(
        default="https://example.invalid",
        metadata={"help": "Base URL of the API this tool calls"},
    )


def is_secret(f: Field[Any]) -> bool:
    """Return True if *f* is marked as a secret in its metadata.

    Args:
        f: A dataclass field of the settings schema.

    Returns:
        True when the field's value must come from a secret backend and never
        from config.toml.
    """
    return bool(f.metadata.get("secret", False))


def field_help(f: Field[Any]) -> str:
    """Return the human-readable description of *f* (empty string if unset).

    Args:
        f: A dataclass field of the settings schema.

    Returns:
        The ``help`` metadata string, or ``""`` when none was provided.
    """
    return str(f.metadata.get("help", ""))


def is_required(f: Field[Any]) -> bool:
    """Return True if *f* has no default and must be provided by some layer.

    Args:
        f: A dataclass field of the settings schema.

    Returns:
        True when the field has neither a default value nor a default factory.
    """
    return f.default is MISSING and f.default_factory is MISSING


def field_default(f: Field[Any]) -> Any:
    """Return the field's default, materializing a default factory if needed.

    Args:
        f: A dataclass field of the settings schema (must not be required).

    Returns:
        The default value.

    Raises:
        ValueError: If the field is required and therefore has no default.
    """
    if f.default is not MISSING:
        return f.default
    if f.default_factory is not MISSING:
        return f.default_factory()
    raise ValueError(f"Field {f.name!r} is required and has no default.")
