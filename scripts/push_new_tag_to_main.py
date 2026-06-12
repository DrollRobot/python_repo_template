"""Interactively merge the current branch into main, bump the version, tag, and push.

Walks through the release process one step at a time. Before each action it
shows what is about to happen and prompts for confirmation (y/n); answering
'n' aborts without making any further changes. The output of every git and
uv command is shown so the process can be watched as it happens. Pass
-y/--yes to answer every prompt with 'y' for non-interactive use.

Along the way it reports the original branch, the working-tree status, and
the current and target project versions.

The new version can either be bumped semantically (patch/minor/major) or set
to an explicit version number with --version.

Usage:
    python scripts/push_new_tag_to_main.py patch
    python scripts/push_new_tag_to_main.py --version 2.0.0
    python scripts/push_new_tag_to_main.py patch -y

Bump levels:
    patch — bug fixes only           (1.4.2 -> 1.4.3)
    minor — new features, no breaks  (1.4.2 -> 1.5.0)
    major — breaking changes         (1.4.2 -> 2.0.0)

Requirements:
    - Run from inside the source branch.
    - `uv` installed and the project uses uv for version management.
    - Push access to origin for both main and the source branch.
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import _cli as cli


def parse_args() -> argparse.Namespace:
    """Parse and validate the command line.

    Returns:
        The parsed arguments; exactly one of ``bump`` or ``version`` is set.
    """
    parser = argparse.ArgumentParser(
        description="Merge the current branch into main, bump the version, tag, and push."
    )
    parser.add_argument(
        "bump",
        nargs="?",
        choices=("patch", "minor", "major"),
        help="semantic version bump level",
    )
    parser.add_argument(
        "--version",
        help="explicit version number to set (e.g. 1.5.0), instead of a bump",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="assume 'yes' to every confirmation prompt (non-interactive)",
    )
    args = parser.parse_args()

    if bool(args.bump) == bool(args.version):
        parser.error("specify exactly one of: a bump level (patch/minor/major) or --version")
    if args.version and not re.match(r"^\d+\.\d+\.\d+", args.version):
        parser.error(f"--version must look like X.Y.Z, got '{args.version}'")
    return args


def main() -> None:
    """Run the interactive release flow."""
    args = parse_args()
    cli.set_assume_yes(args.yes)
    use_bump = bool(args.bump)

    # --- gather state --------------------------------------------------------

    cli.section("Release setup")

    source = cli.capture_ok(["git", "symbolic-ref", "--short", "HEAD"])
    if not source:
        cli.die("Not on a branch (detached HEAD?)")
    if source == "main":
        cli.die("Already on main; switch to the source branch first.")

    cli.info("Original branch", source)
    cli.info("Target branch", "main")
    if use_bump:
        cli.info("Version change", f"bump '{args.bump}'")
    else:
        cli.info("Version change", f"set to '{args.version}'")

    # --- working tree status -------------------------------------------------

    cli.section("Working tree status")
    cli.run(["git", "status", "--short", "--branch"])

    if cli.exit_code(["git", "diff-index", "--quiet", "HEAD", "--"]) != 0:
        cli.die("Working tree is not clean; commit or stash changes first.")
    cli.success("  Working tree is clean.")

    # --- versions --------------------------------------------------------------

    cli.section("Versions")
    current_version = cli.capture(["uv", "version", "--short"])
    if not current_version:
        cli.die("Failed to read current version from uv.")
    cli.info("Current version", current_version)

    print(f"  {cli.GRAY}Preview of the version change:{cli.RESET}")
    if use_bump:
        cli.run(["uv", "version", "--dry-run", "--bump", args.bump])
    else:
        cli.run(["uv", "version", "--dry-run", args.version])

    # --- release steps ---------------------------------------------------------

    cli.section("Step: switch to main")
    cli.step(f"Switch from '{source}' to 'main'?")
    cli.run(["git", "switch", "main"])

    cli.section(f"Step: merge '{source}' into main")
    cli.step(f"Merge '{source}' into 'main'?")
    cli.run(["git", "merge", source])

    cli.section("Step: update version")
    cli.step("Apply the version change?")
    if use_bump:
        cli.run(["uv", "version", "--bump", args.bump])
    else:
        cli.run(["uv", "version", args.version])

    # Brief pause before reading back the version: writing pyproject.toml can
    # leave uv.exe momentarily busy on Windows, which causes errors on the
    # next call.
    time.sleep(1)

    version = cli.capture(["uv", "version", "--short"])
    if not version:
        cli.die("Failed to read new version from uv after update.")
    cli.info("New version", version)

    cli.section("Step: commit release")
    cli.step(f"Stage all changes and commit as 'Release v{version}'?")
    cli.run(["git", "add", "."])
    cli.run(["git", "commit", "-m", f"Release v{version}"])

    cli.section("Step: tag release")
    cli.step(f"Create annotated tag 'v{version}'?")
    cli.run(["git", "tag", "-a", f"v{version}", "-m", f"Release {version}"])

    cli.section("Step: push main")
    cli.step("Push 'main' to origin?")
    cli.run(["git", "push", "origin", "main"])

    cli.section("Step: push tags")
    cli.step("Push tags to origin?")
    cli.run(["git", "push", "origin", "--tags"])

    cli.section(f"Step: return to '{source}'")
    cli.step(f"Switch back to '{source}'?")
    cli.run(["git", "switch", source])

    cli.section(f"Step: merge main into '{source}'")
    cli.step(f"Merge 'main' into '{source}'?")
    cli.run(["git", "merge", "main"])

    cli.section(f"Step: push '{source}'")
    cli.step(f"Push '{source}' to origin?")
    cli.run(["git", "push", "origin", source])

    # --- done ------------------------------------------------------------------

    cli.section("Done")
    cli.success(f"  Released v{version}.")
    cli.info("Current branch", cli.capture(["git", "branch", "--show-current"]))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(130)
