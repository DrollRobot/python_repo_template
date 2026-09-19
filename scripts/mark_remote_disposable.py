"""Mark the remote target this project points at as safe to destroy.

Write half of a pair with tests/verify_remote_disposable.py, which the test
suite runs automatically before any @pytest.mark.destructive_remote test.
This script is the opposite: run it manually, rarely (once per remote
target, or to renew an expiring marker), never automatically.

Marking a target disposable is a promise that destructive_remote tests may
mutate or destroy it. Get the target identity right before confirming --
there is no "no" once a destructive test has run.

Usage:
    uv run scripts/mark_remote_disposable.py
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

import argparse
import sys

import _cli as cli

# Version of this helper script itself. Bump on every change so copies in other
# repos can be compared: patch = bugfix, minor = new flag/behavior, major =
# breaking CLI change.
__version__ = "1.0.1"


def main() -> None:
    """Confirm with the user and write the disposability marker."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt.")
    args = parser.parse_args()
    cli.set_assume_yes(args.yes)

    cli.info("Script version", __version__)
    print()

    cli.section("Mark remote target disposable")
    cli.warn(
        "  This asserts the remote target is safe for destructive_remote tests to "
        "mutate or destroy. There is no 'no' once one has run."
    )

    # FIXME 2: identify and print the actual target here so the user confirms
    # the right thing, e.g.:
    #   cli.info("Target", os.environ["SOME_TARGET_URL"])
    # then replace the cli.die() below with a cli.step() confirmation and the
    # marker-writing logic. See REMOTE-DISPOSABILITY SETUP at the top of this file.
    cli.die(
        "mark_remote_disposable.py has not been implemented for this project yet "
        "(see REMOTE-DISPOSABILITY SETUP at the top of this file)."
    )


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):  # fmt: skip
        print()
        sys.exit(130)
