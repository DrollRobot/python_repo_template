"""Unit tests for tests/verify_remote_disposable.py.

These tests cover the run()/check() exit-code contract that
tests/conftest.py's destructive_remote gate depends on -- they monkeypatch
check() rather than asserting on its message, so they hold for whatever
check() this project uses. test_check_fails_closed_by_default pins the
shipped refuse-unless-confirmed default.
"""

# =============================================================================
# REMOTE-DISPOSABILITY SETUP  (shared note -- an identical copy of this block
# lives in all three of scripts/mark_remote_disposable.py,
# tests/verify_remote_disposable.py and tests/test_verify_remote_disposable.py.
# Delete the whole block from all three once the FIXMEs below are done.)
#
# These three files are the gate on @pytest.mark.destructive_remote tests,
# which mutate or destroy a remote target. The gate asks the target itself
# whether it is marked disposable -- never a local flag, so that repointing
# this project's configuration at an unmarked or production target fails
# closed on its own.
#
# The marker mechanism is project-specific (a cloud resource tag, a database
# marker row, a custom field on an API tenant, a file at a well-known path on
# a host reachable over SSH, ...) because it depends entirely on what kind of
# system this project's destructive_remote tests target. Until the FIXMEs are
# done, destructive_remote tests fail closed.
#
# FIXME 1 -- tests/verify_remote_disposable.py, check():
#   a. Identify which remote target this project is currently pointed at
#      (read whatever configuration already names it -- environment
#      variables, a settings file, IaC state, a URL, a resource ID, a tenant
#      name; there is no guarantee this project even uses a .env file).
#   b. Query THAT SPECIFIC target for its disposability marker. Do not check
#      "does a marker exist somewhere" -- it must be the live target.
#   c. Confirm the marker has not expired. A marker set once during initial
#      setup and never revisited should not still be trusted years later.
#
# FIXME 2 -- scripts/mark_remote_disposable.py, main():
#   a. Identify the same target FIXME 1 reads, and print it clearly before
#      asking for confirmation.
#   b. Write a marker onto that target using whatever mechanism it supports,
#      including an expiry so a marker set once does not silently outlive the
#      review that justified it.
#
# FIXME 3 -- tests/test_verify_remote_disposable.py:
#   Replace test_check_fails_closed_by_default. It calls check() directly and
#   asserts a refusal, which a real implementation pointed at a marked target
#   contradicts. The other two tests monkeypatch check() and stay as-is.
#
# See the "Remote destructive tests" section of AGENTS.TESTING.md.
# =============================================================================

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


def test_check_fails_closed_by_default() -> None:
    """check() refuses unless it confirms a live marker, never silently passes."""
    # FIXME 3: see REMOTE-DISPOSABILITY SETUP at the top of this file.
    is_disposable, _message = verify_remote_disposable.check()
    assert is_disposable is False
