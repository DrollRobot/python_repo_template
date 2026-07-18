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

The marker mechanism is project-specific (a cloud resource tag, a database
marker row, a custom field on an API tenant, a file at a well-known path on a
host reachable over SSH, ...) because it depends entirely on what kind of
system this project's destructive_remote tests target. This stub always
refuses until it is implemented.

FIXME: replace the body of check() so it:
  1. Identifies which remote target this project is currently pointed at
     (read whatever configuration already names it -- environment variables,
     a settings file, IaC state, a URL, a resource ID, a tenant name; there is
     no guarantee this project even uses a .env file).
  2. Queries THAT SPECIFIC target for its disposability marker. Do not check
     "does a marker exist somewhere" -- it must be the live target, so that
     repointing the project's configuration at a different, unmarked target
     fails closed on its own.
  3. Confirms the marker has not expired. mark_remote_disposable.py should
     write an expiry alongside the marker; a marker set once during initial
     setup and never revisited should not still be trusted years later.

Usage:
    uv run tests/verify_remote_disposable.py
"""

from __future__ import annotations

import sys


def check() -> tuple[bool, str]:
    """Return whether the remote target this project points at is disposable.

    Returns:
        A tuple of (is_disposable, message). ``message`` is printed for a
        human to read; it is not parsed by the caller.
    """
    # FIXME: implement the case-by-case check described in the module docstring.
    return False, (
        "verify_remote_disposable.py has not been implemented for this project yet "
        "(see the FIXME in its docstring and AGENTS.TESTING.md). destructive_remote "
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
