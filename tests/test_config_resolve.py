"""Unit tests for the precedence engine in config/resolve.py.

All tests pass an explicit config_path under tmp_path and run against the
fixed toy schema (tests/_toy_config.py), so nothing here touches the real
user config directory or depends on the repo's FIXME example fields.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from python_repo_template.config.resolve import PROFILE_ENV, resolve_settings
from python_repo_template.config.schema import CLI_NAME, ENV_PREFIX, ConfigError
from tests._toy_config import ToySettings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all of this package's env vars so each test controls every layer."""
    for key in list(os.environ):
        if key.startswith(ENV_PREFIX):
            monkeypatch.delenv(key)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


def _resolve(config_path: Path, **kwargs: Any) -> ToySettings:
    result: ToySettings = resolve_settings(ToySettings, config_path=config_path, **kwargs)
    return result


def _write(config_path: Path, text: str) -> None:
    config_path.write_text(text, encoding="utf-8")


# --- zero-file paths -----------------------------------------------------------------


def test_env_only_run_with_no_file(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
    """CI/headless: env vars alone produce a working run; defaults fill the rest."""
    monkeypatch.setenv(ENV_PREFIX + "NAME", "from-env")
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    settings = _resolve(config_path)
    assert settings.name == "from-env"
    assert settings.token == "tok"  # noqa: S105
    assert settings.count == 3
    assert settings.ratio == 0.5
    assert settings.flag is False
    assert settings.tags == []


def test_missing_required_is_actionable(config_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        _resolve(config_path)
    message = str(excinfo.value)
    assert "name" in message
    assert "token" in message
    assert ENV_PREFIX + "NAME" in message
    assert ENV_PREFIX + "TOKEN" in message
    assert f"{CLI_NAME} init" in message
    assert str(config_path) in message


# --- precedence ----------------------------------------------------------------------


def test_precedence_across_all_layers(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
    """Peel layers off one by one; the next one down must win each time."""
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    _write(
        config_path,
        """
        name = "top"
        default_profile = "a"

        [profiles.a]
        name = "prof"
        """,
    )

    monkeypatch.setenv(ENV_PREFIX + "NAME", "env")
    assert _resolve(config_path, overrides={"name": "ovr"}).name == "ovr"
    assert _resolve(config_path).name == "env"

    monkeypatch.delenv(ENV_PREFIX + "NAME")
    assert _resolve(config_path).name == "prof"

    _write(config_path, 'name = "top"\n')
    assert _resolve(config_path).name == "top"


def test_profile_values_override_top_level_fallbacks(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    _write(
        config_path,
        """
        name = "top"
        count = 1

        [profiles.a]
        count = 2
        """,
    )
    settings = _resolve(config_path, profile="a")
    assert settings.name == "top"  # top-level fallback still applies
    assert settings.count == 2  # profile wins where both define a value


def test_override_type_is_checked(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
    monkeypatch.setenv(ENV_PREFIX + "NAME", "n")
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    with pytest.raises(ConfigError, match="'count' in overrides"):
        _resolve(config_path, overrides={"count": "not-an-int"})


# --- profile selection ---------------------------------------------------------------


def test_profile_selection_order(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    _write(
        config_path,
        """
        default_profile = "c"

        [profiles.a]
        name = "from-a"
        [profiles.b]
        name = "from-b"
        [profiles.c]
        name = "from-c"
        """,
    )
    monkeypatch.setenv(PROFILE_ENV, "b")
    assert _resolve(config_path, profile="a").name == "from-a"  # arg beats env
    assert _resolve(config_path).name == "from-b"  # env beats default_profile
    monkeypatch.delenv(PROFILE_ENV)
    assert _resolve(config_path).name == "from-c"  # default_profile last


def test_unknown_profile_lists_available(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    _write(config_path, '[profiles.a]\nname = "x"\n')
    with pytest.raises(ConfigError, match=r"Profile 'zz' not found.*a"):
        _resolve(config_path, profile="zz")


def test_profile_requested_but_no_file(config_path: Path) -> None:
    with pytest.raises(ConfigError, match="no config file exists"):
        _resolve(config_path, profile="a")


# --- env-var coercion ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_name", "raw", "field_name", "expected"),
    [
        ("COUNT", "7", "count", 7),
        ("RATIO", "0.25", "ratio", 0.25),
        ("FLAG", "TRUE", "flag", True),
        ("FLAG", "false", "flag", False),
        ("TAGS", "a, b ,c", "tags", ["a", "b", "c"]),
        ("TAGS", "", "tags", []),
    ],
)
def test_env_coercion(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
    env_name: str,
    raw: str,
    field_name: str,
    expected: object,
) -> None:
    monkeypatch.setenv(ENV_PREFIX + "NAME", "n")
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    monkeypatch.setenv(ENV_PREFIX + env_name, raw)
    assert getattr(_resolve(config_path), field_name) == expected


@pytest.mark.parametrize(
    ("env_name", "raw", "match"),
    [
        ("COUNT", "seven", "must be an integer"),
        ("RATIO", "fast", "must be a number"),
        ("FLAG", "yes", "must be 'true' or 'false'"),
    ],
)
def test_env_coercion_failures(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
    env_name: str,
    raw: str,
    match: str,
) -> None:
    monkeypatch.setenv(ENV_PREFIX + "NAME", "n")
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    monkeypatch.setenv(ENV_PREFIX + env_name, raw)
    with pytest.raises(ConfigError, match=match):
        _resolve(config_path)


# --- file value typing ---------------------------------------------------------------


def test_file_type_mismatch_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    monkeypatch.setenv(ENV_PREFIX + "NAME", "n")
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    _write(config_path, 'count = "seven"\n')
    with pytest.raises(ConfigError, match="'count' in the top level"):
        _resolve(config_path)


def test_file_bool_does_not_satisfy_int(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
    monkeypatch.setenv(ENV_PREFIX + "NAME", "n")
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    _write(config_path, "count = true\n")
    with pytest.raises(ConfigError, match="'count' in the top level"):
        _resolve(config_path)


def test_file_int_fills_float_field(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
    monkeypatch.setenv(ENV_PREFIX + "NAME", "n")
    monkeypatch.setenv(ENV_PREFIX + "TOKEN", "tok")
    _write(config_path, "ratio = 1\n")
    settings = _resolve(config_path)
    assert settings.ratio == 1.0
    assert isinstance(settings.ratio, float)


def test_secret_in_file_is_rejected(config_path: Path) -> None:
    _write(config_path, 'token = "leaked"\n')
    with pytest.raises(ConfigError, match=r"Secrets must never be stored"):
        _resolve(config_path)


# --- public entry point --------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.smoke
def test_load_settings_env_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """load_settings() works against the real schema with env vars alone.

    Generic over the schema so it keeps passing when downstream repos replace
    the FIXME example fields.
    """
    from dataclasses import fields
    from typing import get_type_hints

    from python_repo_template.config import Settings, load_settings
    from python_repo_template.config.paths import CONFIG_DIR_ENV
    from python_repo_template.config.schema import is_required

    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    dummies = {str: "x", int: "1", float: "1.0", bool: "true"}
    hints = get_type_hints(Settings)
    for f in fields(Settings):
        if is_required(f):
            monkeypatch.setenv(ENV_PREFIX + f.name.upper(), dummies.get(hints[f.name], "x"))
    assert isinstance(load_settings(), Settings)
