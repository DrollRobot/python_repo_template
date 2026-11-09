"""Configuration for python_repo_template.

Public API::

    from python_repo_template.config import Settings, load_settings

    settings = load_settings(profile="contoso")
    settings.api_url

# <template-only feature="secret_storage">
Non-secret values live in a per-user ``config.toml`` (see ``paths.py`` for
the OS-specific location); secrets live in whichever credential backend the
user selects (``credential_backend`` in config.toml) and are never written
to the file. The config CLI creates and edits the file; no hand-editing is
required.
# </template-only>
# <template-only feature="!secret_storage">
Values live in a per-user ``config.toml`` (see ``paths.py`` for the
OS-specific location). This package stores no secrets: the secret-storage
machinery has been removed, so ``credential_backend`` and the other reserved
backend keys are no longer legal in the file. The config CLI creates and
edits it; no hand-editing is required.
# </template-only>

Module map (each module's docstring carries its own removal instructions
where applicable):

- ``schema.py``  — the ``Settings`` dataclass: single source of truth for
  option names, types, defaults, secret classification, and help text.
- ``paths.py``   — config-directory resolution.
- ``file.py``    — config.toml reading and validation.
- ``resolve.py`` — the precedence engine behind :func:`load_settings`.
# <template-only feature="secret_storage">
- ``secrets.py`` + ``*_backend.py`` — optional secret-storage machinery:
  the backend dispatcher and the individual credential backends. Only
  consulted when the schema marks fields ``secret``; deletable as a unit
  when it marks none.
# </template-only>
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
