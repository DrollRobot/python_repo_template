"""Unit tests for the no-inline-secret-suppressions PreToolUse hook.

Covers the hook's runtime behavior only; wiring it into settings belongs to
wire_hook.py (see test_wire_hook.py). This drives real tool payloads through
the hook's main() and checks the exit code: 2 when a write adds a
detect-secrets allowlist pragma, 0 for everything else -- including shell
commands and any payload the hook cannot read (it fails open).

The hook lives at .claude/hooks/no-inline-secret-suppressions.py. Its filename is
not a valid module name (hyphens, and it is outside any package), so it is
loaded from its path with importlib rather than imported by name.

Every pragma literal below is assembled from fragments at runtime. If the intact
string appeared in this file, enabling the hook would make the file unwritable
by any agent -- the hook would block edits to its own tests.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from typing import Any

import pytest

_HOOK_PATH = (
    Path(__file__).resolve().parent.parent
    / ".claude"
    / "hooks"
    / "no-inline-secret-suppressions.py"
)
_spec = importlib.util.spec_from_file_location("no_inline_secret_suppressions_hook", _HOOK_PATH)
assert _spec is not None
assert _spec.loader is not None
mod: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

# Split so this source file never contains an intact suppression comment.
PRAGMA = "# pragma: allow" + "list secret"
PRAGMA_NEXTLINE = "# pragma: allow" + "list nextline secret"
PRAGMA_WHITELIST = "# pragma: white" + "list secret"
PRAGMA_UPPER = "# PRAGMA: ALLOW" + "LIST SECRET"
PRAGMA_NO_SPACE = "# pragma:allow" + "list secret"

CLEAN = 'TOKEN = os.environ["TOKEN"]\n'


def _invoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: object
) -> tuple[int, str]:
    """Feed payload to main() over stdin; return (exit_code, stderr_text)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO(text))
    rc = mod.main()
    return rc, capsys.readouterr().err


def _write(content: object) -> dict[str, object]:
    """A Write payload carrying content."""
    return {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": content}}


# ---------------------------------------------------------------------------
# suppressions in written content are blocked
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.functional
def test_write_content_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _write(f'TOKEN = "abc123"  {PRAGMA}\n'))
    assert rc == 2
    assert "detect-secrets allowlist pragma" in err
    assert ".secrets.baseline" in err


@pytest.mark.e2e
@pytest.mark.functional
@pytest.mark.parametrize(
    "pragma", [PRAGMA, PRAGMA_NEXTLINE, PRAGMA_WHITELIST, PRAGMA_UPPER, PRAGMA_NO_SPACE]
)
def test_pragma_variants_block(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], pragma: str
) -> None:
    rc, _ = _invoke(monkeypatch, capsys, _write(f"x = 1  {pragma}\n"))
    assert rc == 2


@pytest.mark.e2e
@pytest.mark.functional
def test_edit_new_string_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "a.py", "old_string": "x = 1", "new_string": f"x = 1 {PRAGMA}"},
    }
    rc, _ = _invoke(monkeypatch, capsys, payload)
    assert rc == 2


@pytest.mark.e2e
@pytest.mark.functional
def test_multiedit_edits_block(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "edits": [
                {"old_string": "a", "new_string": "b"},
                {"old_string": "c", "new_string": f"d  {PRAGMA}"},
            ]
        },
    }
    rc, _ = _invoke(monkeypatch, capsys, payload)
    assert rc == 2


@pytest.mark.e2e
@pytest.mark.functional
def test_notebook_new_source_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"tool_name": "NotebookEdit", "tool_input": {"new_source": f"k = 'v'  {PRAGMA}"}}
    rc, _ = _invoke(monkeypatch, capsys, payload)
    assert rc == 2


# ---------------------------------------------------------------------------
# shell commands are out of scope
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.functional
def test_bash_heredoc_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Shell text is never scanned: a heredoc can smuggle a pragma past this
    # hook by design (pre-commit and CI are the real gate).
    command = f"cat > a.py <<'EOF'\nTOKEN = 'x'  {PRAGMA}\nEOF"
    rc, err = _invoke(
        monkeypatch, capsys, {"tool_name": "Bash", "tool_input": {"command": command}}
    )
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_powershell_command_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"tool_name": "PowerShell", "tool_input": {"command": f"Select-String '{PRAGMA}'"}}
    rc, err = _invoke(monkeypatch, capsys, payload)
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_grepping_for_existing_pragmas_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The motivating case for excluding shell text: auditing what is already
    # suppressed must not be blocked.
    payload = {"tool_name": "Bash", "tool_input": {"command": f"rg '{PRAGMA}' src/"}}
    rc, err = _invoke(monkeypatch, capsys, payload)
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# removals and clean content pass
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.functional
def test_removing_a_pragma_is_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # old_string is never scanned: deleting an existing suppression must work.
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "a.py",
            "old_string": f"x = 1  {PRAGMA}",
            "new_string": "x = 1",
        },
    }
    rc, err = _invoke(monkeypatch, capsys, payload)
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_clean_write_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _write(CLEAN))
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_unrelated_noqa_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Only secret-scanner suppressions are in scope; a lint noqa is not.
    rc, err = _invoke(monkeypatch, capsys, _write("import os  # noqa: F401\n"))
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_word_secret_alone_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _invoke(monkeypatch, capsys, _write("# this module handles secret rotation\n"))
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# unreadable payloads fail open
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.functional
@pytest.mark.parametrize("payload", ["{not json", "[1, 2, 3]", '"a string"', ""])
def test_unreadable_payload_fails_open(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: str
) -> None:
    rc, err = _invoke(monkeypatch, capsys, payload)
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
@pytest.mark.parametrize("tool_input", [None, "not-a-dict", 42])
def test_missing_or_bad_tool_input_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tool_input: object
) -> None:
    rc, err = _invoke(monkeypatch, capsys, {"tool_name": "Write", "tool_input": tool_input})
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_non_string_content_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A non-string value must be skipped, not fed to re.search (TypeError).
    rc, err = _invoke(monkeypatch, capsys, _write(123))
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_malformed_edits_entries_ignored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {"edits": ["not-a-dict", {"new_string": 5}, None]},
    }
    rc, err = _invoke(monkeypatch, capsys, payload)
    assert rc == 0
    assert err == ""


@pytest.mark.e2e
@pytest.mark.functional
def test_non_list_edits_ignored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"tool_name": "MultiEdit", "tool_input": {"edits": "nope"}}
    rc, err = _invoke(monkeypatch, capsys, payload)
    assert rc == 0
    assert err == ""


# ---------------------------------------------------------------------------
# _candidates
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_candidates_yields_written_content_only() -> None:
    tool_input = {
        "content": "w",
        "new_string": "n",
        "new_source": "s",
        "command": "SHOULD-NOT-APPEAR",
        "old_string": "SHOULD-NOT-APPEAR",
        "edits": [{"old_string": "SHOULD-NOT-APPEAR", "new_string": "e"}],
    }
    assert list(mod._candidates(tool_input)) == ["w", "n", "s", "e"]


@pytest.mark.unit
def test_candidates_empty_for_empty_input() -> None:
    assert list(mod._candidates({})) == []


@pytest.mark.unit
def test_hook_declares_a_version() -> None:
    # compare_to_template.py tracks this file by __version__.
    assert mod.__version__


@pytest.mark.unit
def test_hook_source_contains_no_intact_pragma() -> None:
    # Guards the hook file itself: with the hook wired, an intact literal in its
    # own source would make the file unwritable by any agent.
    source = _HOOK_PATH.read_text(encoding="utf-8")
    for pattern, _label in mod._PATTERNS:
        assert pattern.search(source) is None
