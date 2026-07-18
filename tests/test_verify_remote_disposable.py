"""Unit tests for tests/verify_remote_disposable.py.

These tests cover the run()/check() exit-code contract that
tests/conftest.py's destructive_remote gate depends on -- they use
monkeypatch rather than asserting on the stub's specific message, so they
keep passing once a project replaces check() with real logic. The one test
that does pin down the stub's own behavior (test_stub_fails_closed) is
expected to be replaced alongside that implementation.
"""

from __future__ import annotations

import pytest

from tests import verify_remote_disposable

pytestmark = pytest.mark.unit


def test_run_returns_zero_when_disposable(monkeypatch: pytest.MonkeyPatch) -> None:
    """run() maps a disposable confirmation from check() to exit code 0."""
    monkeypatch.setattr(verify_remote_disposable, "check", lambda: (True, "ok"))
    assert verify_remote_disposable.run() == 0


def test_run_returns_one_when_not_disposable(monkeypatch: pytest.MonkeyPatch) -> None:
    """run() maps a refusal from check() to exit code 1, whatever the reason."""
    monkeypatch.setattr(verify_remote_disposable, "check", lambda: (False, "not today"))
    assert verify_remote_disposable.run() == 1


def test_stub_fails_closed() -> None:
    """Until a project implements check(), it must refuse, never silently pass."""
    is_disposable, _message = verify_remote_disposable.check()
    assert is_disposable is False
