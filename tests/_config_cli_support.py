"""Shared setup for the config CLI tests.

tests/test_config_cli.py and, in projects with secret storage,
tests/test_config_cli_secrets.py run the CLI in-process via ``cli.main([...])``.
Both use these helpers, so both set the CLI up the same way: a throwaway
config directory, the fixed secret-free test object as the schema, the
"prompt" credential policy, and scripted answers in place of the interactive
prompts.

Plain functions rather than fixtures, like :func:`pin_prompt_policy`: each
test file calls them from its own fixtures, so nothing here runs for tests
that did not ask for it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import pytest

from python_repo_template.config.paths import CONFIG_DIR_ENV
from python_repo_template.config.schema import ENV_PREFIX
from tests._config_test_object import ConfigTestObject, pin_prompt_policy

_CLI_SETTINGS = "python_repo_template.config.cli.Settings"


def isolate_config_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the config CLI at a throwaway config directory and the test schema.

    Strips this package's env vars so each test controls every layer, sets
    the config directory to ``tmp_path``, swaps :class:`ConfigTestObject` in
    for the FIXME example fields, and pins the "prompt" credential policy.

    Args:
        monkeypatch: The test's monkeypatch fixture.
        tmp_path: The test's temporary directory.

    Returns:
        The config directory, ``tmp_path``.
    """
    for key in list(os.environ):
        if key.startswith(ENV_PREFIX):
            monkeypatch.delenv(key)
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    use_schema(monkeypatch, ConfigTestObject)
    pin_prompt_policy(monkeypatch)
    return tmp_path


def use_schema(monkeypatch: pytest.MonkeyPatch, schema: type) -> None:
    """Make the config CLI run against *schema* instead of ``Settings``.

    Args:
        monkeypatch: The test's monkeypatch fixture.
        schema: A dataclass following the schema conventions.
    """
    monkeypatch.setattr(_CLI_SETTINGS, schema)


def feed_input(monkeypatch: pytest.MonkeyPatch, responses: Iterable[str]) -> None:
    """Answer the CLI's visible prompts with *responses*, in order.

    Args:
        monkeypatch: The test's monkeypatch fixture.
        responses: One answer per ``input()`` call. Running out fails the test.
    """
    answers = iter(responses)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))


def feed_getpass(monkeypatch: pytest.MonkeyPatch, responses: Iterable[str]) -> None:
    """Answer the CLI's hidden value prompts with *responses*, in order.

    Args:
        monkeypatch: The test's monkeypatch fixture.
        responses: One answer per ``getpass()`` call. Running out fails the test.
    """
    answers = iter(responses)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))


def forbid_getpass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if any hidden value prompt is shown.

    Args:
        monkeypatch: The test's monkeypatch fixture.
    """

    def explode(prompt: str = "") -> str:
        raise AssertionError(f"unexpected hidden value prompt: {prompt!r}")

    monkeypatch.setattr("getpass.getpass", explode)


def config_text(config_dir: Path) -> str:
    """Return the text of ``config.toml`` in *config_dir*.

    Args:
        config_dir: The directory :func:`isolate_config_cli` returned.

    Returns:
        The file's contents.
    """
    return (config_dir / "config.toml").read_text(encoding="utf-8")
