"""Shared toy settings schema for the config unit tests.

The resolver engine is generic over any dataclass following the schema
conventions (see src/python_repo_template/config/schema.py). Tests run it
against this fixed toy schema instead of the real ``Settings`` so they stay
green when a downstream repo replaces the FIXME example fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToySettings:
    """Fixed schema exercising every supported field type and classification."""

    name: str = field(metadata={"help": "Required plain string"})
    token: str = field(repr=False, metadata={"secret": True, "help": "Required secret"})
    count: int = field(default=3, metadata={"help": "Defaulted int"})
    ratio: float = field(default=0.5, metadata={"help": "Defaulted float"})
    flag: bool = field(default=False, metadata={"help": "Defaulted bool"})
    tags: list[str] = field(default_factory=list, metadata={"help": "Defaulted list"})
