"""Shared settings test object for the config unit tests.

The resolver engine is generic over any dataclass following the schema
conventions (see src/python_repo_template/config/schema.py). Tests run it
against this fixed test object instead of the real ``Settings`` so they stay
green when a downstream repo replaces the FIXME example fields.

Also hosts :func:`block_secrets_module`, which simulates a project that
deleted the secret-storage machinery (``config/secrets.py``), so the tests
proving the rest of the config system survives that removal can run without
actually deleting the file.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

# Version of this test helper. It ships to projects generated from this
# template (cleanup.py keeps it: no script or hook shares its name), so bump
# on every change to let scripts/compare_to_template.py flag stale copies.
__version__ = "1.1.0"

_SECRETS_MODULE = "python_repo_template.config.secrets"


@dataclass(frozen=True)
class ConfigTestObject:
    """Fixed schema exercising every supported field type and classification."""

    name: str = field(metadata={"help": "Required plain string"})
    token: str = field(repr=False, metadata={"secret": True, "help": "Required secret"})
    count: int = field(default=3, metadata={"help": "Defaulted int"})
    ratio: float = field(default=0.5, metadata={"help": "Defaulted float"})
    flag: bool = field(default=False, metadata={"help": "Defaulted bool"})
    tags: list[str] = field(default_factory=list, metadata={"help": "Defaulted list"})


@dataclass(frozen=True)
class NoSecretsTestObject:
    """Schema with no secret fields, for the machinery-is-optional tests."""

    name: str = field(metadata={"help": "Required plain string"})
    count: int = field(default=3, metadata={"help": "Defaulted int"})


class _BlockFinder:
    """Meta-path finder that refuses to import the secrets module."""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> None:
        if fullname == _SECRETS_MODULE:
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None


def block_secrets_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make importing ``config/secrets.py`` fail as if the file were deleted.

    Drops any cached import (from ``sys.modules`` and the parent package's
    attribute) and installs a meta-path finder that raises
    ``ModuleNotFoundError`` for it, so every lazy-import path in the config
    system sees the module as absent. ``monkeypatch`` restores everything.

    Args:
        monkeypatch: The test's monkeypatch fixture.
    """
    import python_repo_template.config as config_package

    monkeypatch.delitem(sys.modules, _SECRETS_MODULE, raising=False)
    if hasattr(config_package, "secrets"):
        monkeypatch.delattr(config_package, "secrets")
    monkeypatch.setattr(sys, "meta_path", [_BlockFinder(), *sys.meta_path])
