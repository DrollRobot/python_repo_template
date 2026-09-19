"""Check whether the remote target this project points at is marked disposable.

Read half of a pair with scripts/mark_remote_disposable.py, which writes the
marker this script looks for. The mark/verify split matters: marking is a
rare, human-confirmed action; verifying runs automatically, once per test
session, the first time an @pytest.mark.destructive_remote test executes (see
conftest.py and the "Remote destructive tests" section of AGENTS.TESTING.md).
Only this script's exit code is read by pytest -- 0 means confirmed
disposable, anything else means refuse.

This lives in tests/, not scripts/, because its only caller is the test
suite's own gate: unlike scripts/mark_remote_disposable.py (a standalone
maintenance action a human runs directly, rarely), this script exists purely
to serve conftest.py.

Usage:
    uv run tests/verify_remote_disposable.py
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

import sys


def check() -> tuple[bool, str]:
    """Return whether the remote target this project points at is disposable.

    Returns:
        A tuple of (is_disposable, message). ``message`` is printed for a
        human to read; it is not parsed by the caller.
    """
    # FIXME 1: see REMOTE-DISPOSABILITY SETUP at the top of this file.
    return False, (
        "verify_remote_disposable.py has not been implemented for this project yet "
        "(see REMOTE-DISPOSABILITY SETUP at the top of this file). destructive_remote "
        "tests fail closed until it is."
    )


def run() -> int:
    """Print the check result and return the exit code pytest reads.

    Returns:
        0 if the remote target is confirmed disposable, 1 otherwise.
    """
    is_disposable, message = check()
    print(message)
    return 0 if is_disposable else 1


def main() -> None:
    """Run the check and exit with its result code."""
    sys.exit(run())


if __name__ == "__main__":
    main()
