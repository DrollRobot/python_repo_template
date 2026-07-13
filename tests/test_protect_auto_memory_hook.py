"""Unit tests for the protect-auto-memory PreToolUse hook itself.

Distinct from test_protect_auto_memory.py, which tests the *setup helper* that
wires the hook in and out of settings. This drives Write/Edit payloads through
the hook's main() and checks the runtime decision: an `ask` on stdout for a
path inside a Claude auto-memory directory, silence for anything else.

The hook lives at .claude/hooks/protect-auto-memory.py; its filename is not a
valid module name, so it is loaded from its path with importlib.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from typing import Any

import pytest

_HOOK_PATH = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "protect-auto-memory.py"
_spec = importlib.util.spec_from_file_location("protect_auto_memory_hook", _HOOK_PATH)
assert _spec is not None
assert _spec.loader is not None
mod: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _invoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: object
) -> str:
    """Feed payload to main() over stdin; return captured stdout text."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO(text))
    mod.main()
    return capsys.readouterr().out


def _write(file_path: object) -> dict[str, object]:
    """A Write payload targeting file_path."""
    return {"tool_name": "Write", "tool_input": {"file_path": file_path}}


def _assert_asks(out: str) -> None:
    """Assert stdout carries a PreToolUse `ask` permission decision."""
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "ask"
    assert decision["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# memory-directory writes require approval
# ---------------------------------------------------------------------------


def test_memory_write_asks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = "/home/u/.claude/projects/my-proj/memory/fact.md"
    out = _invoke(monkeypatch, capsys, _write(path))
    _assert_asks(out)


def test_memory_write_windows_backslashes_ask(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = r"C:\Users\me\.claude\projects\my-proj\memory\fact.md"
    out = _invoke(monkeypatch, capsys, _write(path))
    _assert_asks(out)


def test_memory_match_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = "/home/u/.Claude/Projects/My-Proj/Memory/Fact.md"
    out = _invoke(monkeypatch, capsys, _write(path))
    _assert_asks(out)


def test_memory_match_is_location_based_any_project(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Any <project> segment matches; the rule is location-based, not per-project.
    path = "/x/.claude/projects/some-other-repo/memory/notes.md"
    out = _invoke(monkeypatch, capsys, _write(path))
    _assert_asks(out)


def test_edit_tool_also_gated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "/x/.claude/projects/p/memory/a.md"},
    }
    out = _invoke(monkeypatch, capsys, payload)
    _assert_asks(out)


# ---------------------------------------------------------------------------
# everything else passes through untouched (no output)
# ---------------------------------------------------------------------------


def test_ordinary_write_passes_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _invoke(monkeypatch, capsys, _write("/home/u/project/src/main.py"))
    assert out == ""


def test_memory_word_outside_claude_path_passes_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # "memory" elsewhere must not trip the .claude/projects/<p>/memory/ pattern.
    out = _invoke(monkeypatch, capsys, _write("/home/u/memory/notes.txt"))
    assert out == ""


def test_claude_projects_without_memory_segment_passes_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _invoke(monkeypatch, capsys, _write("/x/.claude/projects/p/settings.json"))
    assert out == ""


def test_malformed_json_passes_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _invoke(monkeypatch, capsys, "{not json")
    assert out == ""


def test_missing_file_path_passes_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _invoke(monkeypatch, capsys, {"tool_name": "Write", "tool_input": {}})
    assert out == ""


def test_null_tool_input_passes_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _invoke(monkeypatch, capsys, {"tool_name": "Write", "tool_input": None})
    assert out == ""
