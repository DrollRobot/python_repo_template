"""Unit tests for the no-chained-commands-pwsh PreToolUse hook.

The hook lives at .claude/hooks/no-chained-commands-pwsh.py. Its filename is not
a valid module name (hyphens, and it is outside any package), so it is loaded
from its path with importlib rather than imported by name.

Drives real commands through main(): chained commands (&&, ||, ;) block; a single
command or a real pipe passes; a joiner inside quotes is not a false positive.
This variant has no tool_name gate -- it fires on whatever it is wired to.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from typing import Any

import pytest

_HOOK_PATH = (
    Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "no-chained-commands-pwsh.py"
)
_spec = importlib.util.spec_from_file_location("no_chained_commands_pwsh", _HOOK_PATH)
assert _spec is not None
assert _spec.loader is not None
mod: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _invoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: object
) -> tuple[int, str]:
    """Feed payload to main() over stdin; return (exit_code, stderr_text)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO(text))
    rc = mod.main()
    return rc, capsys.readouterr().err


def _cmd(command: str, tool_name: str = "PowerShell") -> dict[str, object]:
    """A tool payload for the given command."""
    return {"tool_name": tool_name, "tool_input": {"command": command}}


# ---------------------------------------------------------------------------
# early exits
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
def test_empty_command_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, _ = _invoke(monkeypatch, capsys, _cmd("   "))
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
# chained commands are blocked
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.functional
def test_and_and_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _cmd("git add . && git commit -m x"))
    assert rc == 2
    assert "joined with '&&'" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_or_or_blocks(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    rc, err = _invoke(monkeypatch, capsys, _cmd("foo || bar"))
    assert rc == 2
    assert "joined with '||'" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_semicolon_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _cmd("foo; bar"))
    assert rc == 2
    assert "joined with ';'" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_multiple_joiners_all_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _cmd("a && b; c"))
    assert rc == 2
    assert "joined with '&&'" in err
    assert "joined with ';'" in err


@pytest.mark.e2e
@pytest.mark.functional
def test_fires_regardless_of_tool_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No tool gate: a chained command is blocked whatever tool it is wired to.
    rc, err = _invoke(monkeypatch, capsys, _cmd("a; b", tool_name="Bash"))
    assert rc == 2
    assert "joined with ';'" in err


# ---------------------------------------------------------------------------
# allowed forms
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.functional
def test_single_command_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _cmd("git status"))
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_pipe_allowed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    rc, err = _invoke(monkeypatch, capsys, _cmd("Get-ChildItem | Select-Object -First 5"))
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_semicolon_inside_single_quotes_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _cmd("git commit -m 'fix; the bug'"))
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_joiner_inside_double_quotes_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _cmd('echo "a && b"'))
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# _strip_quoted
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a 'b;c' d", "a  d"),
        ('x "y&&z" w', "x  w"),
        ("no quotes here", "no quotes here"),
    ],
)
def test_strip_quoted(text: str, expected: str) -> None:
    assert mod._strip_quoted(text) == expected
