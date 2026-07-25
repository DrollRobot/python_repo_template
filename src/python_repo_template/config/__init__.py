"""Configuration and secrets for python_repo_template.

Public API::

    from python_repo_template.config import Settings, load_settings

    settings = load_settings(profile="contoso")
    settings.api_url

Non-secret values live in a per-user ``config.toml`` (see ``paths.py`` for
the OS-specific location); secrets live in the OS keyring or Azure Key Vault
and are never written to the file. The config CLI creates and edits the file;
no hand-editing is required.

Module map (each module's docstring carries its own removal instructions
where applicable):

- ``schema.py``  — the ``Settings`` dataclass: single source of truth for
  option names, types, defaults, secret classification, and help text.
- ``paths.py``   — config-directory resolution.
- ``file.py``    — config.toml reading and validation.
- ``resolve.py`` — the precedence engine behind :func:`load_settings`.
"""

from __future__ import annotations

from python_repo_template.config.resolve import load_settings, resolve_settings
from python_repo_template.config.schema import APP_NAME, CLI_NAME, ConfigError, Settings

__all__ = [
    "APP_NAME",
    "CLI_NAME",
    "ConfigError",
    "Settings",
    "load_settings",
    "resolve_settings",
]
