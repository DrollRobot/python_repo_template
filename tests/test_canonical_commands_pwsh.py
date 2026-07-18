"""Unit tests for the canonical-commands-pwsh PreToolUse hook.

The hook lives at .claude/hooks/canonical-commands-pwsh.py. Its filename is not
a valid module name (hyphens, and it is outside any package), so it is loaded
from its path with importlib rather than imported by name.

Every branch of main() is exercised through stdin, plus focused coverage of the
`git -C <cwd>` redundancy detection, the .ps1 wrapper regex, and the optional
debug-logging helpers.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_HOOK_PATH = (
    Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "canonical-commands-pwsh.py"
)
_spec = importlib.util.spec_from_file_location("canonical_commands_pwsh", _HOOK_PATH)
assert _spec is not None
assert _spec.loader is not None
mod: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

# Keep tests hermetic: never touch the real hook-debug.log even if the ambient
# CLAUDE_HOOK_DEBUG_LOG env var was set when pytest launched. Tests that want
# logging on flip this back per-test with monkeypatch.
mod._DEBUG = False

_WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")


def _invoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: object
) -> tuple[int, str]:
    """Feed payload to main() over stdin; return (exit_code, stderr_text).

    payload may be a dict (JSON-encoded here) or a raw string for malformed
    input.
    """
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO(text))
    rc = mod.main()
    return rc, capsys.readouterr().err


def _ps(command: str, cwd: str = "C:/repo") -> dict[str, object]:
    """A PowerShell-tool payload for the given command."""
    return {"tool_name": "PowerShell", "cwd": cwd, "tool_input": {"command": command}}


def _bash(command: str, cwd: str = "C:/repo") -> dict[str, object]:
    """A Bash-tool payload for the given command."""
    return {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command}}


# ---------------------------------------------------------------------------
# main(): early exits (never block on junk)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.functional
def test_malformed_json_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, "{not json")
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_missing_command_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, {"tool_name": "PowerShell", "tool_input": {}})
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_empty_command_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, _ = _invoke(monkeypatch, capsys, _ps("   "))
    assert rc == 0


@pytest.mark.e2e
@pytest.mark.functional
def test_non_string_command_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"tool_name": "PowerShell", "tool_input": {"command": 123}}
    rc, _ = _invoke(monkeypatch, capsys, payload)
    assert rc == 0


# ---------------------------------------------------------------------------
# main(): Bash-tool routing
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.functional
@pytest.mark.parametrize(
    "token",
    ["git", "uv", "python", "python3", "ruff", "pwsh", "powershell"],
)
def test_bash_rerouted_first_tokens_block(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], token: str
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash(f"{token} --version"))
    assert rc == 2
    assert "PowerShell" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_cd_prefix_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _ps("cd C:/other"))
    assert rc == 2
    assert "prepend" in err
    assert "cd" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_bash_cd_prefix_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `cd` is no longer rerouted; it is blocked outright on either tool.
    rc, err = _invoke(monkeypatch, capsys, _bash("cd /tmp"))
    assert rc == 2
    assert "prepend" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_bare_cd_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _ps("cd"))
    assert rc == 2
    assert "prepend" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_cd_is_not_confused_with_other_commands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A command that merely starts with "cd" as a substring (code, cdk) is fine.
    rc, err = _invoke(monkeypatch, capsys, _ps("code ."))
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_bash_ps1_blocks_even_for_neutral_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # First token (cat) is not on the reroute list, but the .ps1 forces PowerShell.
    rc, err = _invoke(monkeypatch, capsys, _bash("cat Tests.ps1"))
    assert rc == 2
    assert "PowerShell" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_bash_posix_only_tool_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _bash("shellcheck build.sh"))
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# main(): git -C redundancy (PowerShell branch)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_redundant_git_c_abs_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _ps("git -C C:/repo status", cwd="C:/repo"))
    assert rc == 2
    assert "-C" in err
    assert "DIFFERENT repo" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_redundant_git_c_dot_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _ps("git -C . status", cwd="C:/repo"))
    assert rc == 2
    assert "drop the" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_git_c_other_repo_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _ps("git -C C:/other status", cwd="C:/repo"))
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_plain_git_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _ps("git status", cwd="C:/repo"))
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# main(): .ps1 canonical-form checks
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_wrapper_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _ps(r"pwsh -File .\Tests.ps1"))
    assert rc == 2
    assert "run the script directly" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_double_backslash_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _ps(r"Get-Content .\\Tests.ps1"))
    assert rc == 2
    assert "single backslashes" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_wrapper_and_double_backslash_report_both(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _ps(r"pwsh -File .\\Tests.ps1"))
    assert rc == 2
    assert "run the script directly" in err
    assert "single backslashes" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_ps1_path_in_cmdlet_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A cmdlet that merely references a .ps1 path containing the word "powershell"
    # must not be misflagged as a wrapper invocation.
    rc, err = _invoke(monkeypatch, capsys, _ps(r"Copy-Item C:\dev\powershell\Foo.ps1 bar"))
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_ps_ordinary_command_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _ps("uv run pytest -q"))
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_unmatched_tool_falls_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(
        monkeypatch,
        capsys,
        {"tool_name": "Write", "cwd": "C:/repo", "tool_input": {"command": "uv run pytest"}},
    )
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# _first_token
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("command", "expected"),
    [("git status", "git"), ("  UV  sync ", "uv"), ("", ""), ("   ", "")],
)
def test_first_token(command: str, expected: str) -> None:
    assert mod._first_token(command) == expected


# ---------------------------------------------------------------------------
# _redundant_git_c: thorough
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_git_c_empty_cwd_returns_none() -> None:
    assert mod._redundant_git_c("git -C C:/repo status", "") is None


@pytest.mark.unit
def test_git_c_no_dash_c_returns_none() -> None:
    assert mod._redundant_git_c("git status", "C:/repo") is None


@pytest.mark.unit
def test_git_c_empty_quoted_path_returns_none() -> None:
    assert mod._redundant_git_c('git -C "" status', "C:/repo") is None


@pytest.mark.unit
def test_git_c_abs_equal_to_cwd_via_tmp(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    assert mod._redundant_git_c(f"git -C {cwd} status", cwd) == cwd


@pytest.mark.unit
def test_git_c_abs_different_from_cwd_via_tmp(tmp_path: Path) -> None:
    other = str(tmp_path / "sub")
    assert mod._redundant_git_c(f"git -C {other} status", str(tmp_path)) is None


@pytest.mark.unit
def test_git_c_dot_resolves_to_cwd(tmp_path: Path) -> None:
    assert mod._redundant_git_c("git -C . status", str(tmp_path)) == "."


@pytest.mark.unit
def test_git_c_relative_subdir_not_redundant(tmp_path: Path) -> None:
    assert mod._redundant_git_c("git -C sub status", str(tmp_path)) is None


@pytest.mark.unit
def test_git_c_quoted_path_with_space(tmp_path: Path) -> None:
    spaced = tmp_path / "my dir"
    spaced.mkdir()
    cwd = str(spaced)
    assert mod._redundant_git_c(f'git -C "{cwd}" status', cwd) == cwd


@pytest.mark.unit
def test_git_c_config_options_before_dash_c(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    command = f"git -c core.pager=cat -C {cwd} log"
    assert mod._redundant_git_c(command, cwd) == cwd


@pytest.mark.unit
def test_git_c_subcommand_reuse_flag_not_matched() -> None:
    # `git commit -C HEAD` is --reuse-message, not the global -C directory option.
    assert mod._redundant_git_c("git commit -C HEAD", "C:/repo") is None


@pytest.mark.unit
@_WINDOWS_ONLY
def test_git_c_case_insensitive_on_windows() -> None:
    assert mod._redundant_git_c(r"git -C c:\repo status", r"C:\Repo") == r"c:\repo"


@pytest.mark.unit
@_WINDOWS_ONLY
def test_git_c_slash_style_insensitive_on_windows() -> None:
    assert mod._redundant_git_c("git -C C:/repo status", r"C:\repo") == "C:/repo"


# ---------------------------------------------------------------------------
# _WRAPPER_RE
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "command",
    [r"pwsh -File .\Tests.ps1", r"powershell -Command x", r"powershell.exe -File .\a.ps1"],
)
def test_wrapper_re_matches_executable_invocations(command: str) -> None:
    assert mod._WRAPPER_RE.search(command)


@pytest.mark.unit
@pytest.mark.parametrize(
    "command",
    [r"Copy-Item C:\dev\powershell\Foo.ps1 bar", r".\Tests.ps1 -Foo", "Get-Content x.ps1"],
)
def test_wrapper_re_ignores_paths_and_direct_calls(command: str) -> None:
    assert mod._WRAPPER_RE.search(command) is None


# ---------------------------------------------------------------------------
# optional debug logging
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_log_noop_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "hook-debug.log"
    monkeypatch.setattr(mod, "_DEBUG", False)
    monkeypatch.setattr(mod, "_LOG", str(log))
    mod._log("nothing should be written")
    assert not log.exists()


@pytest.mark.unit
def test_log_writes_when_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "hook-debug.log"
    monkeypatch.setattr(mod, "_DEBUG", True)
    monkeypatch.setattr(mod, "_LOG", str(log))
    mod._log("hello")
    assert "hello" in log.read_text(encoding="utf-8")


@pytest.mark.unit
def test_log_never_raises_on_bad_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Point the log at a directory: opening it for append raises, and _log must
    # swallow it rather than crash the tool call.
    monkeypatch.setattr(mod, "_DEBUG", True)
    monkeypatch.setattr(mod, "_LOG", str(tmp_path))
    mod._log("boom")  # must not raise


@pytest.mark.unit
def test_rotate_keeps_small_log_untouched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "hook-debug.log"
    log.write_text("one line\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_LOG", str(log))
    monkeypatch.setattr(mod, "_LOG_MAX_BYTES", 1_000_000)
    mod._rotate_if_needed()
    assert log.read_text(encoding="utf-8") == "one line\n"


@pytest.mark.unit
def test_rotate_trims_oversized_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "hook-debug.log"
    lines = [f"line {i}\n" for i in range(100)]
    log.write_text("".join(lines), encoding="utf-8")
    monkeypatch.setattr(mod, "_LOG", str(log))
    monkeypatch.setattr(mod, "_LOG_MAX_BYTES", 10)  # force rotation
    monkeypatch.setattr(mod, "_LOG_DROP_FRACTION", 0.20)
    mod._rotate_if_needed()
    remaining = log.read_text(encoding="utf-8").splitlines()
    assert len(remaining) == 80
    assert remaining[0] == "line 20"
    assert remaining[-1] == "line 99"
