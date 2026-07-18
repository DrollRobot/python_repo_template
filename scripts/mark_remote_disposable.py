"""Mark the remote target this project points at as safe to destroy.

Write half of a pair with tests/verify_remote_disposable.py, which the test
suite runs automatically before any @pytest.mark.destructive_remote test.
This script is the opposite: run it manually, rarely (once per remote
target, or to renew an expiring marker), never automatically.

Marking a target disposable is a promise that destructive_remote tests may
mutate or destroy it. Get the target identity right before confirming --
there is no "no" once a destructive test has run.

FIXME: replace the body of main() below so it:
  1. Identifies which remote target this project is currently pointed at
     (the same identity tests/verify_remote_disposable.py's FIXME reads --
     read whatever configuration already names it, whether that's
     environment variables, a settings file, IaC state, or something else;
     there is no guarantee this project even uses a .env file), and prints
     it clearly before asking for confirmation.
  2. Writes a marker onto that target using whatever mechanism it supports
     (a resource tag, a database marker row/table, a custom field on an API
     tenant, a file at a well-known path, ...), including an expiry so a
     marker set once does not silently outlive the review that justified it.
Until this is implemented, there is nothing for tests/verify_remote_disposable.py
to find, so destructive_remote tests keep failing closed.

Usage:
    uv run scripts/mark_remote_disposable.py
"""

from __future__ import annotations

import argparse
import sys

import _cli as cli

# Version of this helper script itself. Bump on every change so copies in other
# repos can be compared: patch = bugfix, minor = new flag/behavior, major =
# breaking CLI change.
__version__ = "1.0.0"


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

    # FIXME: identify and print the actual target here so the user confirms
    # the right thing, e.g.:
    #   cli.info("Target", os.environ["SOME_TARGET_URL"])
    # then replace the cli.die() below with a cli.step() confirmation and the
    # marker-writing logic described in the module docstring.
    cli.die(
        "mark_remote_disposable.py has not been implemented for this project yet "
        "(see the FIXME in its docstring and AGENTS.TESTING.md)."
    )


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):  # fmt: skip
        print()
        sys.exit(130)
