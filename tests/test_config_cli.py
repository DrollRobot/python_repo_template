"""Tests for the config CLI (init / open / show / set / unset / profiles / secrets).

Commands run in-process via ``cli.main([...])``. The setup in
tests/_config_cli_support.py points the config directory at ``tmp_path`` and
swaps the schema for a fixed test object, so nothing here touches the real
user config, a credential store, or the repo's FIXME example fields.

The default test object is secret-free, so every generic command test passes
in a project without the secret-storage machinery. Tests that exercise secret
fields ask for the ``secret_schema`` fixture, which swaps in the test object
declaring one. Tests that also need the machinery itself (a credential backend
or the reserved backend keys) are in tests/test_config_cli_secrets.py, which
ships only with the secret_storage feature.

The setup also pins the schema's CREDENTIAL_BACKEND policy to "prompt" (no
default backend), whatever the project picked. Policy-specific tests
monkeypatch the schema constant themselves.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import tomlkit

from python_repo_template.config import cli
from tests._config_cli_support import (
    config_text,
    feed_getpass,
    feed_input,
    forbid_getpass,
    isolate_config_cli,
    use_schema,
)
from tests._config_test_object import SecretTestObject, block_secrets_module

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run each test against a throwaway config directory and the secret-free test object."""
    return isolate_config_cli(monkeypatch, tmp_path)


@pytest.fixture
def secret_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a schema with a secret field.

    Requested by every test that exercises a secret. It runs after the
    autouse fixture above (autouse fixtures are set up first within a scope),
    so the secret-bearing object wins for those tests only.
    """
    use_schema(monkeypatch, SecretTestObject)


# --- path ----------------------------------------------------------------------------


def test_path_prints_config_location(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["path"]) == 0
    assert str(config_dir / "config.toml") in capsys.readouterr().out


# --- open ----------------------------------------------------------------------------


def _fake_run(calls: list[list[str]], returncode: int = 0) -> Any:
    """Stand-in for ``subprocess.run`` that records argv instead of launching."""

    def run(argv: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode)

    return run


def test_open_missing_file_points_at_init(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["open"]) == 1
    err = capsys.readouterr().err
    assert "No config file" in err
    assert "init" in err


def test_open_on_windows_uses_startfile(monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> None:
    path = config_dir / "config.toml"
    path.write_text('name = "n"\n', encoding="utf-8")
    opened: list[Path] = []
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("os.startfile", opened.append, raising=False)
    assert cli.main(["open"]) == 0
    assert opened == [path]


@pytest.mark.parametrize(("platform", "opener"), [("darwin", "open"), ("linux", "xdg-open")])
def test_open_on_posix_runs_the_platform_opener(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, platform: str, opener: str
) -> None:
    path = config_dir / "config.toml"
    path.write_text('name = "n"\n', encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr("sys.platform", platform)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("subprocess.run", _fake_run(calls))
    assert cli.main(["open"]) == 0
    assert calls == [[f"/usr/bin/{opener}", str(path)]]


def test_open_without_opener_installed_is_actionable(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text('name = "n"\n', encoding="utf-8")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert cli.main(["open"]) == 1
    assert "'xdg-open' not found" in capsys.readouterr().err


def test_open_opener_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text('name = "n"\n', encoding="utf-8")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("subprocess.run", _fake_run([], returncode=4))
    assert cli.main(["open"]) == 1
    assert "exited with status 4" in capsys.readouterr().err


# --- init ----------------------------------------------------------------------------


@pytest.mark.integration
def test_init_required_field_reprompts(monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> None:
    # First response empty for required 'name'; it must re-prompt. The rest
    # keep the defaults for count, ratio, flag, and tags.
    feed_input(monkeypatch, ["", "n", "", "", "", ""])
    assert cli.main(["init"]) == 0
    assert tomlkit.parse(config_text(config_dir))["name"] == "n"


def test_init_refuses_configured_target(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (config_dir / "config.toml").write_text('name = "n"\n', encoding="utf-8")
    assert cli.main(["init"]) == 1
    err = capsys.readouterr().err
    assert "already configured" in err
    assert "init --force" in err


@pytest.mark.integration
def test_init_force_replaces_whole_file(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text(
        '# old comment\nname = "old"\ncount = 9\ndefault_profile = "p"\n\n'
        '[profiles.p]\nname = "pn"\n',
        encoding="utf-8",
    )
    feed_input(monkeypatch, ["n", "", "", "", ""])
    assert cli.main(["init", "--force"]) == 0
    text = config_text(config_dir)
    assert tomlkit.parse(text).unwrap() == {"name": "n"}
    assert "# old comment" not in text
    assert "starting over" in capsys.readouterr().out


@pytest.mark.integration
def test_init_force_with_profile_still_replaces_whole_file(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    (config_dir / "config.toml").write_text(
        'name = "top"\n\n[profiles.p]\nname = "old"\n\n[profiles.q]\nname = "q"\n',
        encoding="utf-8",
    )
    feed_input(monkeypatch, ["n", "", "", "", ""])
    assert cli.main(["init", "--profile", "p", "--force"]) == 0
    assert tomlkit.parse(config_text(config_dir)).unwrap() == {"profiles": {"p": {"name": "n"}}}


@pytest.mark.integration
def test_init_force_recovers_malformed_file(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    (config_dir / "config.toml").write_text("name = [unclosed\n", encoding="utf-8")
    feed_input(monkeypatch, ["n", "", "", "", ""])
    assert cli.main(["init", "--force"]) == 0
    assert tomlkit.parse(config_text(config_dir))["name"] == "n"


def test_init_force_aborted_keeps_old_file(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    original = 'name = "old"\n'
    (config_dir / "config.toml").write_text(original, encoding="utf-8")

    def abort(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", abort)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["init", "--force"])
    assert config_text(config_dir) == original


# --- set / unset ---------------------------------------------------------------------


def test_set_writes_coerced_value(config_dir: Path) -> None:
    assert cli.main(["set", "count", "7"]) == 0
    assert tomlkit.parse(config_text(config_dir))["count"] == 7


@pytest.mark.usefixtures("secret_schema")
def test_set_rejects_secret_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["set", "token", "x"]) == 1
    err = capsys.readouterr().err
    assert "never be written to config.toml" in err
    assert "set-secret token" in err


def test_set_unknown_profile_is_actionable(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["set", "count", "7", "--profile", "p"]) == 1
    assert "Profile 'p' not found" in capsys.readouterr().err


def test_set_preserves_comments(config_dir: Path) -> None:
    (config_dir / "config.toml").write_text('# keep me\nname = "n"\n', encoding="utf-8")
    assert cli.main(["set", "count", "7"]) == 0
    text = config_text(config_dir)
    assert "# keep me" in text
    assert "count = 7" in text


def test_unset_removes_value_and_warns_when_required(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text('name = "n"\n', encoding="utf-8")
    assert cli.main(["unset", "name"]) == 0
    out = capsys.readouterr().out
    assert "name is required" in out
    assert "name" not in tomlkit.parse(config_text(config_dir))


def test_unset_missing_key_fails(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (config_dir / "config.toml").write_text('name = "n"\n', encoding="utf-8")
    assert cli.main(["unset", "count"]) == 1
    assert "nothing to unset" in capsys.readouterr().err


# --- profiles ------------------------------------------------------------------------


def test_list_profiles_bare_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["list-profiles"]) == 0
    assert "No profiles defined" in capsys.readouterr().out


def test_list_profiles_marks_default(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (config_dir / "config.toml").write_text(
        'default_profile = "b"\n[profiles.a]\n[profiles.b]\n', encoding="utf-8"
    )
    assert cli.main(["list-profiles"]) == 0
    out = capsys.readouterr().out
    assert "a" in out
    assert "b  (default)" in out


def test_use_sets_default_profile(config_dir: Path) -> None:
    (config_dir / "config.toml").write_text("[profiles.a]\n", encoding="utf-8")
    assert cli.main(["use", "a"]) == 0
    assert tomlkit.parse(config_text(config_dir))["default_profile"] == "a"


def test_use_unknown_profile_fails(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (config_dir / "config.toml").write_text("[profiles.a]\n", encoding="utf-8")
    assert cli.main(["use", "b"]) == 1
    assert "Profile 'b' not found" in capsys.readouterr().err


# --- show ----------------------------------------------------------------------------


def test_show_missing_required_is_actionable(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["show"]) == 1
    assert "Missing required configuration value(s)" in capsys.readouterr().err


def _show_line(out: str, name: str) -> str:
    """Return the line of ``show`` output for setting *name*."""
    return next(line for line in out.splitlines() if line.strip().startswith(f"{name} "))


@pytest.mark.regression
def test_show_prints_every_setting_when_one_is_missing(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text("count = 7\n", encoding="utf-8")
    assert cli.main(["show"]) == 1
    captured = capsys.readouterr()
    assert _show_line(captured.out, "name").endswith("= <missing>")
    assert _show_line(captured.out, "count").endswith("= 7  <- file:top-level")
    assert _show_line(captured.out, "ratio").endswith("= 0.5  <- default")
    assert "ERROR: Missing required configuration value(s): name" in captured.err


def test_show_marks_invalid_values_and_lists_every_problem(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text(
        'name = "n"\ncount = "seven"\nnmae = "typo"\n', encoding="utf-8"
    )
    assert cli.main(["show"]) == 1
    captured = capsys.readouterr()
    assert _show_line(captured.out, "name").endswith("= 'n'  <- file:top-level")
    assert _show_line(captured.out, "count").endswith("= <error>")
    errors = captured.err.splitlines()
    assert len(errors) == 2
    assert "Unknown key 'nmae'" in errors[0]
    assert "'count'" in errors[1]


def test_show_complete_config_exits_zero_with_no_errors(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config_dir / "config.toml").write_text('name = "n"\n', encoding="utf-8")
    assert cli.main(["show"]) == 0
    assert capsys.readouterr().err == ""


# --- secrets -------------------------------------------------------------------------


@pytest.mark.usefixtures("secret_schema")
def test_set_secret_rejects_non_secret_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["set-secret", "name"]) == 1
    assert "not a secret" in capsys.readouterr().err


# --- schema backend policy -----------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("secret_schema")
def test_init_none_policy_skips_secret_storage(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The 'none' policy prompts for nothing secret-related and stores nothing.

    Nothing can be stored without a value, and no hidden value prompt may run.
    """
    monkeypatch.setattr("python_repo_template.config.schema.CREDENTIAL_BACKEND", "none")
    feed_input(monkeypatch, ["n", "", "", "", ""])
    forbid_getpass(monkeypatch)
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "CREDENTIAL_BACKEND is 'none'" in out
    assert "environment variables" in out
    assert "credential_backend" not in tomlkit.parse(config_text(config_dir))


@pytest.mark.usefixtures("secret_schema")
def test_none_policy_unregisters_secret_commands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Secret fields but a 'none' policy: the secret commands stay unregistered."""
    monkeypatch.setattr("python_repo_template.config.schema.CREDENTIAL_BACKEND", "none")
    with pytest.raises(SystemExit):
        cli.main(["set-secret", "token"])
    assert "invalid choice: 'set-secret'" in capsys.readouterr().err


# --- optional secret machinery -------------------------------------------------------


def test_secret_commands_absent_without_secret_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A schema with no secret fields gets no set-secret/delete-secret commands."""
    with pytest.raises(SystemExit):
        cli.main(["set-secret", "token"])
    assert "invalid choice: 'set-secret'" in capsys.readouterr().err


@pytest.mark.usefixtures("secret_schema")
def test_set_secret_with_machinery_removed_is_actionable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Secret fields still in the schema after removal fail loudly, not weirdly."""
    block_secrets_module(monkeypatch)
    feed_getpass(monkeypatch, ["s3"])
    assert cli.main(["set-secret", "token"]) == 1
    assert "secret-storage machinery" in capsys.readouterr().err


def test_backend_keys_unknown_when_machinery_removed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With secrets.py gone there are no reserved keys for set to accept."""
    block_secrets_module(monkeypatch)
    assert cli.main(["set", "credential_backend", "fake"]) == 1
    assert "Unknown option 'credential_backend'" in capsys.readouterr().err


# --- call-time schema gates ----------------------------------------------------------


@pytest.mark.regression
def test_secret_command_registration_follows_the_live_schema(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Swapping the schema mid-process changes which commands exist.

    Guards against reintroducing an import-time snapshot of the schema: the
    parser used to be built from flags computed once, at import, so a
    downstream secret-free schema silently kept (or lost) the secret
    commands regardless of what the tests installed.
    """
    with pytest.raises(SystemExit):  # secret-free schema: no such command
        cli.main(["set-secret", "token"])
    assert "invalid choice: 'set-secret'" in capsys.readouterr().err

    monkeypatch.setattr(cli, "Settings", SecretTestObject)
    # Registered now: the command parses and reaches its own error path
    # (no backend configured) instead of argparse's "invalid choice".
    assert cli.main(["set-secret", "token"]) == 1
    assert "invalid choice" not in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.regression
def test_init_secret_handling_follows_the_live_schema(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """init reads the schema at call time, so a mid-process swap is honored."""
    monkeypatch.setattr("python_repo_template.config.schema.CREDENTIAL_BACKEND", "none")
    forbid_getpass(monkeypatch)
    feed_input(monkeypatch, ["n", "", "", "", ""])
    assert cli.main(["init"]) == 0
    assert "CREDENTIAL_BACKEND is 'none'" not in capsys.readouterr().out

    monkeypatch.setattr(cli, "Settings", SecretTestObject)
    feed_input(monkeypatch, ["n", "", "", "", ""])
    assert cli.main(["init", "--profile", "p"]) == 0
    assert "CREDENTIAL_BACKEND is 'none'" in capsys.readouterr().out


# --- file permissions ----------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_saved_config_is_owner_only(config_dir: Path) -> None:
    assert cli.main(["set", "count", "7"]) == 0
    mode = (config_dir / "config.toml").stat().st_mode & 0o777
    assert mode == 0o600
