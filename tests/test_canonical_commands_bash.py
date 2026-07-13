"""Unit tests for the canonical-commands-bash PreToolUse hook.

The hook lives at .claude/hooks/canonical-commands-bash.py. Its filename is not a
valid module name (hyphens, and it is outside any package), so it is loaded from
its path with importlib rather than imported by name.

Covers every branch of main(): the .ps1-from-Bash guard, the `bash script.sh`
canonical script form (blocking `sh`/`./`), the no-`cd` rule, and the shared
`git -C <cwd>` redundancy detection, plus the optional debug-logging helpers.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from typing import Any

import pytest

_HOOK_PATH = (
    Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "canonical-commands-bash.py"
)
_spec = importlib.util.spec_from_file_location("canonical_commands_bash", _HOOK_PATH)
assert _spec is not None
assert _spec.loader is not None
mod: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

# Keep tests hermetic: never touch the real hook-debug.log even if the ambient
# CLAUDE_HOOK_DEBUG_LOG env var was set when pytest launched.
mod._DEBUG = False


def _invoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: object
) -> tuple[int, str]:
    """Feed payload to main() over stdin; return (exit_code, stderr_text)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO(text))
    rc = mod.main()
    return rc, capsys.readouterr().err


def _bash(command: str, cwd: str = "/repo") -> dict[str, object]:
    """A Bash-tool payload for the given command."""
    return {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command}}


# ---------------------------------------------------------------------------
# early exits
# ---------------------------------------------------------------------------


def test_malformed_json_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, "{not json")
    assert rc == 0
    assert err == ""


def test_non_bash_tool_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(
        monkeypatch, capsys, {"tool_name": "PowerShell", "tool_input": {"command": "sh x.sh"}}
    )
    assert rc == 0
    assert err == ""


def test_empty_command_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, _ = _invoke(monkeypatch, capsys, _bash("   "))
    assert rc == 0


def test_non_string_command_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": 123}}
    rc, _ = _invoke(monkeypatch, capsys, payload)
    assert rc == 0


# ---------------------------------------------------------------------------
# .ps1 from the Bash tool -> use the PowerShell tool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "pwsh -File deploy.ps1",
        "pwsh deploy.ps1",
        'powershell -Command "& ./deploy.ps1"',
        "powershell.exe -File deploy.ps1",
        "./deploy.ps1",
    ],
)
def test_ps1_from_bash_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash(command))
    assert rc == 2
    assert "PowerShell tool" in err


def test_ps1_referenced_without_execution_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Reading/moving a .ps1 is fine; only executing it from Bash is blocked.
    rc, err = _invoke(monkeypatch, capsys, _bash("cat deploy.ps1"))
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# canonical script invocation: bash script.sh (block sh / ./)
# ---------------------------------------------------------------------------


def test_sh_wrapper_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("sh build.sh"))
    assert rc == 2
    assert "bash script.sh" in err


def test_dotslash_script_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("./build.sh --foo"))
    assert rc == 2
    assert "bash script.sh" in err


def test_dotslash_nested_script_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("./scripts/build.sh"))
    assert rc == 2
    assert "bash script.sh" in err


def test_bash_script_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("bash build.sh --foo"))
    assert rc == 0
    assert err == ""


def test_bash_dotslash_script_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `bash ./build.sh` still uses the bash method, so it is allowed.
    rc, err = _invoke(monkeypatch, capsys, _bash("bash ./build.sh"))
    assert rc == 0
    assert err == ""


def test_bash_syntax_check_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("bash -n build.sh"))
    assert rc == 0
    assert err == ""


def test_sh_inline_c_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("sh -c 'echo hi'"))
    assert rc == 0
    assert err == ""


def test_shellcheck_not_confused_with_sh(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("shellcheck build.sh"))
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# cd prefix
# ---------------------------------------------------------------------------


def test_cd_prefix_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("cd /tmp"))
    assert rc == 2
    assert "prepend" in err


def test_bare_cd_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("cd"))
    assert rc == 2
    assert "prepend" in err


def test_cd_substring_command_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("cdk deploy"))
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# git -C redundancy
# ---------------------------------------------------------------------------


def test_git_c_redundant_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("git -C /repo status", cwd="/repo"))
    assert rc == 2
    assert "DIFFERENT repo" in err


def test_git_c_dot_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("git -C . status", cwd="/repo"))
    assert rc == 2
    assert "drop the" in err


def test_git_c_other_repo_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("git -C /other status", cwd="/repo"))
    assert rc == 0
    assert err == ""


def test_plain_git_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("git status", cwd="/repo"))
    assert rc == 0
    assert err == ""


def test_ordinary_command_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("shellcheck -x lib/foo.sh", cwd="/repo"))
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# _first_token / _redundant_git_c (shared with the pwsh hook)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [("git status", "git"), ("  BASH  x.sh ", "bash"), ("", ""), ("   ", "")],
)
def test_first_token(command: str, expected: str) -> None:
    assert mod._first_token(command) == expected


def test_git_c_empty_cwd_returns_none() -> None:
    assert mod._redundant_git_c("git -C /repo status", "") is None


def test_git_c_no_dash_c_returns_none() -> None:
    assert mod._redundant_git_c("git status", "/repo") is None


def test_git_c_empty_quoted_path_returns_none() -> None:
    assert mod._redundant_git_c('git -C "" status', "/repo") is None


def test_git_c_abs_equal_to_cwd_via_tmp(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    assert mod._redundant_git_c(f"git -C {cwd} status", cwd) == cwd


def test_git_c_abs_different_from_cwd_via_tmp(tmp_path: Path) -> None:
    other = str(tmp_path / "sub")
    assert mod._redundant_git_c(f"git -C {other} status", str(tmp_path)) is None


def test_git_c_dot_resolves_to_cwd(tmp_path: Path) -> None:
    assert mod._redundant_git_c("git -C . status", str(tmp_path)) == "."


def test_git_c_quoted_path_with_space(tmp_path: Path) -> None:
    spaced = tmp_path / "my dir"
    spaced.mkdir()
    cwd = str(spaced)
    assert mod._redundant_git_c(f'git -C "{cwd}" status', cwd) == cwd


def test_git_c_config_options_before_dash_c(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    assert mod._redundant_git_c(f"git -c core.pager=cat -C {cwd} log", cwd) == cwd


def test_git_c_subcommand_reuse_flag_not_matched() -> None:
    assert mod._redundant_git_c("git commit -C HEAD", "/repo") is None


# ---------------------------------------------------------------------------
# optional debug logging
# ---------------------------------------------------------------------------


def test_log_noop_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "hook-debug.log"
    monkeypatch.setattr(mod, "_DEBUG", False)
    monkeypatch.setattr(mod, "_LOG", str(log))
    mod._log("nothing should be written")
    assert not log.exists()


def test_log_writes_when_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "hook-debug.log"
    monkeypatch.setattr(mod, "_DEBUG", True)
    monkeypatch.setattr(mod, "_LOG", str(log))
    mod._log("hello")
    assert "hello" in log.read_text(encoding="utf-8")


def test_log_never_raises_on_bad_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mod, "_DEBUG", True)
    monkeypatch.setattr(mod, "_LOG", str(tmp_path))  # a directory: open() will fail
    mod._log("boom")  # must not raise


def test_rotate_keeps_small_log_untouched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "hook-debug.log"
    log.write_text("one line\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_LOG", str(log))
    monkeypatch.setattr(mod, "_LOG_MAX_BYTES", 1_000_000)
    mod._rotate_if_needed()
    assert log.read_text(encoding="utf-8") == "one line\n"


def test_rotate_trims_oversized_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "hook-debug.log"
    log.write_text("".join(f"line {i}\n" for i in range(100)), encoding="utf-8")
    monkeypatch.setattr(mod, "_LOG", str(log))
    monkeypatch.setattr(mod, "_LOG_MAX_BYTES", 10)  # force rotation
    monkeypatch.setattr(mod, "_LOG_DROP_FRACTION", 0.20)
    mod._rotate_if_needed()
    remaining = log.read_text(encoding="utf-8").splitlines()
    assert len(remaining) == 80
    assert remaining[0] == "line 20"
    assert remaining[-1] == "line 99"
