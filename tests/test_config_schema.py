"""Unit tests for the Settings schema conventions in config/schema.py.

These tests are generic over the schema: they iterate dataclasses.fields()
rather than naming specific options, so they keep passing when a downstream
repo replaces the FIXME example fields with its own.
"""

from __future__ import annotations

from dataclasses import fields
from typing import get_type_hints

import pytest

from python_repo_template.config import file as config_file
from python_repo_template.config import paths, resolve
from python_repo_template.config.schema import (
    Settings,
    field_default,
    field_help,
    is_required,
    is_secret,
)

# Version of this test module. It ships to projects generated from this
# template (cleanup.py keeps it: no script or hook shares its name), so bump
# on every change to let scripts/compare_to_template.py flag stale copies.
__version__ = "1.1.0"

pytestmark = pytest.mark.unit

_LEAK_SENTINEL = "SENTINEL-LEAK-VALUE"


def _dummy_settings() -> Settings:
    """Build a Settings instance with dummy values, secrets set to a sentinel."""
    hints = get_type_hints(Settings)
    dummies: dict[type, object] = {str: "x", int: 1, float: 1.0, bool: True}
    values: dict[str, object] = {}
    for f in fields(Settings):
        if is_secret(f):
            values[f.name] = _LEAK_SENTINEL
        elif is_required(f):
            values[f.name] = dummies.get(hints[f.name], "x")
    return Settings(**values)  # type: ignore[arg-type]


def test_field_names_avoid_reserved_keys() -> None:
    """Option names must not collide with reserved config.toml or env-var names."""
    reserved = (
        config_file.RESERVED_TOP_LEVEL_KEYS
        | config_file.secret_reserved_keys()  # credential_backend + backend keys
        | {"profile", "config_dir"}  # PROFILE_ENV / CONFIG_DIR_ENV suffixes
    )
    collisions = {f.name for f in fields(Settings)} & reserved
    assert not collisions


def test_secret_fields_disable_repr() -> None:
    for f in fields(Settings):
        if is_secret(f):
            assert f.repr is False, f"secret field {f.name!r} must set repr=False"


def test_repr_does_not_leak_secret_values() -> None:
    settings = _dummy_settings()
    assert _LEAK_SENTINEL not in repr(settings)
    assert _LEAK_SENTINEL not in str(settings)


def test_every_field_has_help_text() -> None:
    for f in fields(Settings):
        assert field_help(f), f"field {f.name!r} is missing help metadata"


def test_field_default_materializes_defaults_and_rejects_required() -> None:
    for f in fields(Settings):
        if is_required(f):
            with pytest.raises(ValueError, match="required"):
                field_default(f)
        else:
            assert field_default(f) is not None


def test_env_names_are_derived_from_one_prefix() -> None:
    """The profile and config-dir env vars share the schema's prefix."""
    assert resolve.PROFILE_ENV.endswith("_PROFILE")
    assert paths.CONFIG_DIR_ENV.endswith("_CONFIG_DIR")
    prefix = resolve.PROFILE_ENV.removesuffix("PROFILE")
    assert paths.CONFIG_DIR_ENV.removesuffix("CONFIG_DIR") == prefix
