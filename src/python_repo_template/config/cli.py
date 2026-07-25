"""The config CLI: create, inspect, and edit config.toml and stored secrets.

One command group covers everything -- non-secret values and secrets alike --
so no hand-editing of config.toml is ever required:

===================  ========================================================
Command              Behavior
===================  ========================================================
``init``             Interactive first-time setup; prompts from the schema;
                     non-secrets go to config.toml, secrets to the backend.
``path``             Print the resolved config file path.
``show``             Effective config after full resolution, secrets masked,
                     with each value's provenance.
``set KEY VALUE``    Write a non-secret to config.toml (comment-preserving).
``unset KEY``        Remove a key so the schema default applies again.
``list-profiles``    Profile names, marking the default.
``use PROFILE``      Set ``default_profile``.
``set-secret KEY``   Prompt (hidden) and store in the profile's backend.
``delete-secret KEY``  Remove a secret from the profile's backend.
===================  ========================================================

Exposed as the ``python-repo-template-config`` console script and as
``python -m python_repo_template.config``. Writes go through ``tomlkit`` so
user comments in config.toml survive edits; secret values never touch the
file and are never accepted as command-line arguments (shell history).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from dataclasses import Field, fields
from pathlib import Path
from typing import Any, get_type_hints

import tomlkit
import tomlkit.exceptions

from python_repo_template.config import file as config_file
from python_repo_template.config import paths, secrets
from python_repo_template.config.resolve import (
    coerce_string,
    resolve_with_sources,
    select_profile_name,
)
from python_repo_template.config.schema import (
    CLI_NAME,
    ConfigError,
    Settings,
    field_default,
    field_help,
    is_required,
    is_secret,
)

# Version of this module. It ships to projects generated from this template,
# so bump on every change to let scripts/compare_to_template.py flag stale
# copies: patch = bugfix, minor = new behavior, major = breaking change.
__version__ = "1.0.0"

_SECRET_MASK = "********"  # noqa: S105  (display placeholder, not a credential)


# ---------------------------------------------------------------------------
# TOML document helpers (the only write path to config.toml)
# ---------------------------------------------------------------------------


def _load_document(path: Path) -> tomlkit.TOMLDocument:
    """Parse the config file, or return an empty document when absent.

    Args:
        path: Config file location.

    Returns:
        The parsed (comment-preserving) document.

    Raises:
        ConfigError: If the file exists but is not valid TOML.
    """
    if not path.exists():
        return tomlkit.document()
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except tomlkit.exceptions.ParseError as exc:
        raise ConfigError(f"Malformed TOML in {path}: {exc}") from exc


def _save_document(path: Path, document: tomlkit.TOMLDocument) -> None:
    """Write *document* to *path*, creating the config directory if needed.

    On POSIX the file is restricted to the owner (``0o600``); the directory
    permissions are handled by ``paths.ensure_config_dir``.

    Args:
        path: Config file location.
        document: The document to serialize.
    """
    paths.ensure_config_dir()
    path.write_text(document.as_string(), encoding="utf-8", newline="\n")
    if os.name == "posix":
        os.chmod(path, 0o600)


def _profile_table(
    document: tomlkit.TOMLDocument, profile: str | None, *, create: bool = False
) -> Any:
    """Return the table CLI edits target: a profile's table or the top level.

    Args:
        document: The parsed config document.
        profile: Profile name, or None for bare top-level mode.
        create: Create the ``[profiles.<name>]`` table when missing instead
            of failing (used by ``init``).

    Returns:
        A mutable tomlkit table.

    Raises:
        ConfigError: When the profile does not exist and *create* is False.
    """
    if profile is None:
        return document
    profiles = document.get("profiles")
    if profiles is None:
        if not create:
            raise ConfigError(
                f"Profile {profile!r} not found (no profiles defined). "
                f"Run '{CLI_NAME} init --profile {profile}' to create it."
            )
        profiles = tomlkit.table(True)
        document["profiles"] = profiles
    if profile not in profiles:
        if not create:
            available = ", ".join(sorted(profiles.keys())) or "none defined"
            raise ConfigError(
                f"Profile {profile!r} not found. Available profiles: {available}. "
                f"Run '{CLI_NAME} init --profile {profile}' to create it."
            )
        profiles[profile] = tomlkit.table()
    return profiles[profile]


def _schema_field(key: str) -> Field[Any]:
    """Look up schema field *key*, failing loudly on unknown names.

    Args:
        key: A field name from the command line.

    Returns:
        The matching dataclass field.

    Raises:
        ConfigError: When no such option exists, listing the valid names.
    """
    for f in fields(Settings):
        if f.name == key:
            return f
    valid = ", ".join(f.name for f in fields(Settings))
    raise ConfigError(f"Unknown option {key!r}. Valid options: {valid}.")


def _backend_config(document: tomlkit.TOMLDocument, profile: str | None) -> dict[str, Any]:
    """Return the merged reserved-key backend config for *profile*.

    Args:
        document: The parsed config document.
        profile: Active profile name, or None.

    Returns:
        The merged ``credential_backend`` / ``keyvault_url`` mapping.
    """
    config: dict[str, Any] = dict(document)
    profiles = config.get("profiles")
    profile_values: dict[str, Any] = {}
    if profile is not None and isinstance(profiles, dict) and profile in profiles:
        profile_values = dict(profiles[profile])
    return config_file.reserved_config(config, profile_values)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    """Interactively create the config file (and optionally a profile).

    Prompts for every schema field: non-secrets are written to config.toml
    (empty input keeps the default, or re-prompts when required); secrets are
    stored in the credential backend via a hidden prompt (empty input skips,
    with a reminder). Refuses to touch an already-configured target so a typo
    cannot silently overwrite a tenant's settings.

    Args:
        args: Parsed CLI arguments (``profile``).

    Returns:
        Process exit code.
    """
    path = paths.config_path()
    document = _load_document(path)
    table = _profile_table(document, args.profile, create=True)

    already = [f.name for f in fields(Settings) if f.name in table]
    if already:
        target = f"profile {args.profile!r}" if args.profile else f"the top level of {path}"
        raise ConfigError(
            f"{target} is already configured ({', '.join(already)}). "
            f"Use '{CLI_NAME} set' / '{CLI_NAME} set-secret' to change values."
        )

    hints = get_type_hints(Settings)
    print(f"Writing {path}" + (f" (profile {args.profile!r})" if args.profile else ""))
    for f in fields(Settings):
        if is_secret(f):
            continue
        label = f"{f.name} — {field_help(f)}"
        if not is_required(f):
            label += f" [default: {field_default(f)!r}]"
        while True:
            raw = input(f"{label}: ").strip()
            if raw:
                table[f.name] = coerce_string(f.name, raw, hints[f.name])
                break
            if not is_required(f):
                break  # keep the schema default; write nothing
            print(f"{f.name} is required.")
    _save_document(path, document)

    backend_config = _backend_config(document, args.profile)
    for f in fields(Settings):
        if not is_secret(f):
            continue
        value = getpass.getpass(f"{f.name} — {field_help(f)} (hidden, empty to skip): ")
        if value:
            secrets.set_secret(f.name, value, args.profile, backend_config)
            print(f"Stored {f.name} in the {secrets.backend_name(backend_config)!r} backend.")
        else:
            print(f"Skipped {f.name}; store it later with '{CLI_NAME} set-secret {f.name}'.")

    print(f"Done. Inspect with '{CLI_NAME} show'.")
    return 0


def _cmd_path(args: argparse.Namespace) -> int:
    """Print the resolved config file path.

    Args:
        args: Parsed CLI arguments (unused).

    Returns:
        Process exit code.
    """
    print(paths.config_path())
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """Print the effective configuration with provenance, secrets masked.

    Args:
        args: Parsed CLI arguments (``profile``).

    Returns:
        Process exit code.
    """
    settings, sources, profile_name = resolve_with_sources(Settings, profile=args.profile)
    print(f"config file: {paths.config_path()}")
    print(f"profile:     {profile_name or 'none (top-level keys)'}")
    width = max(len(f.name) for f in fields(Settings))
    for f in fields(Settings):
        value = _SECRET_MASK if is_secret(f) else getattr(settings, f.name)
        print(f"  {f.name:<{width}} = {value!r}  <- {sources[f.name]}")
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    """Write a non-secret option to config.toml, preserving comments.

    Args:
        args: Parsed CLI arguments (``key``, ``value``, ``profile``).

    Returns:
        Process exit code.

    Raises:
        ConfigError: For unknown or secret-classified keys.
    """
    f = _schema_field(args.key)
    if is_secret(f):
        raise ConfigError(
            f"{args.key!r} is a secret and must never be written to config.toml. "
            f"Use '{CLI_NAME} set-secret {args.key}' instead."
        )
    value = coerce_string(args.key, args.value, get_type_hints(Settings)[args.key])
    path = paths.config_path()
    document = _load_document(path)
    table = _profile_table(document, args.profile)
    table[args.key] = value
    _save_document(path, document)
    where = f"profile {args.profile!r}" if args.profile else "top level"
    print(f"Set {args.key} = {value!r} ({where}) in {path}")
    return 0


def _cmd_unset(args: argparse.Namespace) -> int:
    """Remove an option from config.toml so the schema default applies.

    Args:
        args: Parsed CLI arguments (``key``, ``profile``).

    Returns:
        Process exit code.

    Raises:
        ConfigError: When the key is not set in the targeted table.
    """
    f = _schema_field(args.key)
    path = paths.config_path()
    document = _load_document(path)
    table = _profile_table(document, args.profile)
    if args.key not in table:
        where = f"profile {args.profile!r}" if args.profile else f"the top level of {path}"
        raise ConfigError(f"{args.key!r} is not set in {where}; nothing to unset.")
    del table[args.key]
    _save_document(path, document)
    print(f"Unset {args.key} in {path}")
    if is_required(f):
        print(f"Note: {args.key} is required; resolution will fail until it is provided again.")
    return 0


def _cmd_list_profiles(args: argparse.Namespace) -> int:
    """List profile names, marking the default.

    Args:
        args: Parsed CLI arguments (unused).

    Returns:
        Process exit code.
    """
    document = _load_document(paths.config_path())
    profiles = document.get("profiles")
    default = document.get("default_profile")
    if not profiles:
        print("No profiles defined (bare top-level mode).")
        return 0
    for name in sorted(profiles.keys()):
        marker = "  (default)" if name == default else ""
        print(f"{name}{marker}")
    return 0


def _cmd_use(args: argparse.Namespace) -> int:
    """Set ``default_profile``.

    Args:
        args: Parsed CLI arguments (``profile``).

    Returns:
        Process exit code.

    Raises:
        ConfigError: When the named profile does not exist.
    """
    path = paths.config_path()
    document = _load_document(path)
    _profile_table(document, args.profile)  # existence check
    document["default_profile"] = args.profile
    _save_document(path, document)
    print(f"default_profile = {args.profile!r} in {path}")
    return 0


def _select_secret_profile(document: tomlkit.TOMLDocument, explicit: str | None) -> str | None:
    """Resolve which profile a secret command targets.

    Same order as settings resolution: explicit flag, then the profile env
    var, then ``default_profile``.

    Args:
        document: The parsed config document.
        explicit: The ``--profile`` value, if any.

    Returns:
        The profile name, or None for bare top-level mode.
    """
    return select_profile_name(explicit, dict(document), paths.config_path())


def _cmd_set_secret(args: argparse.Namespace) -> int:
    """Prompt for a secret value (hidden) and store it in the backend.

    Args:
        args: Parsed CLI arguments (``key``, ``profile``).

    Returns:
        Process exit code.

    Raises:
        ConfigError: For unknown or non-secret keys, an empty value, or a
            read-only backend.
    """
    f = _schema_field(args.key)
    if not is_secret(f):
        raise ConfigError(
            f"{args.key!r} is not a secret. Use '{CLI_NAME} set {args.key} <value>' instead."
        )
    document = _load_document(paths.config_path())
    profile = _select_secret_profile(document, args.profile)
    backend_config = _backend_config(document, profile)
    value = getpass.getpass(f"Value for {args.key} (hidden): ")
    if not value:
        raise ConfigError("Empty value; nothing stored.")
    secrets.set_secret(args.key, value, profile, backend_config)
    service = secrets.service_name(profile)
    backend = secrets.backend_name(backend_config)
    print(f"Stored {args.key} in the {backend!r} backend (service {service!r}).")
    return 0


def _cmd_delete_secret(args: argparse.Namespace) -> int:
    """Delete a secret from the backend.

    Args:
        args: Parsed CLI arguments (``key``, ``profile``).

    Returns:
        Process exit code.

    Raises:
        ConfigError: For unknown or non-secret keys, or a read-only backend.
    """
    f = _schema_field(args.key)
    if not is_secret(f):
        raise ConfigError(f"{args.key!r} is not a secret; use '{CLI_NAME} unset {args.key}'.")
    document = _load_document(paths.config_path())
    profile = _select_secret_profile(document, args.profile)
    backend_config = _backend_config(document, profile)
    secrets.delete_secret(args.key, profile, backend_config)
    print(f"Deleted {args.key} from the {secrets.backend_name(backend_config)!r} backend.")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse command tree.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog=CLI_NAME,
        description="Create, inspect, and edit config.toml and stored secrets.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, **kwargs: Any) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, description=help_text, **kwargs)

    p = add("init", "Interactive first-time setup.")
    p.add_argument("--profile", default=None, help="Create/populate this profile.")
    p.set_defaults(func=_cmd_init)

    p = add("path", "Print the config file path.")
    p.set_defaults(func=_cmd_path)

    p = add("show", "Show the effective config with provenance; secrets masked.")
    p.add_argument("--profile", default=None, help="Resolve this profile.")
    p.set_defaults(func=_cmd_show)

    p = add("set", "Set a non-secret option in config.toml.")
    p.add_argument("key")
    p.add_argument("value")
    p.add_argument("--profile", default=None, help="Target this profile's table.")
    p.set_defaults(func=_cmd_set)

    p = add("unset", "Remove an option so the schema default applies.")
    p.add_argument("key")
    p.add_argument("--profile", default=None, help="Target this profile's table.")
    p.set_defaults(func=_cmd_unset)

    p = add("list-profiles", "List profiles, marking the default.")
    p.set_defaults(func=_cmd_list_profiles)

    p = add("use", "Set default_profile.")
    p.add_argument("profile")
    p.set_defaults(func=_cmd_use)

    p = add("set-secret", "Prompt for a secret (hidden) and store it in the backend.")
    p.add_argument("key")
    p.add_argument("--profile", default=None, help="Target this profile's backend.")
    p.set_defaults(func=_cmd_set_secret)

    p = add("delete-secret", "Delete a secret from the backend.")
    p.add_argument("key")
    p.add_argument("--profile", default=None, help="Target this profile's backend.")
    p.set_defaults(func=_cmd_delete_secret)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the config CLI.

    Args:
        argv: Argument list, or None to use ``sys.argv``.

    Returns:
        Process exit code (0 on success, 1 on any configuration error).
    """
    args = _build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
