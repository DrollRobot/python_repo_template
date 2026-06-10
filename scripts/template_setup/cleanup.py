"""Remove the template-setup scaffolding once you are done with it.

After you have renamed the project, stripped the headers, chosen a license, and
worked through the FIXMEs, these setup scripts have served their purpose. This
script deletes them (the whole ``scripts/template_setup/`` folder, including
itself) and any leftover ``LICENSE.*.FIXME`` candidates -- but only once a real
``LICENSE`` file exists, so you are never left with no license.

It does NOT edit prose for you; it prints reminders for the manual bits (such as
removing the template instructions from README.md).

Usage:
    uv run scripts/template_setup/cleanup.py
    uv run scripts/template_setup/cleanup.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import _common

REMINDERS = [
    "Remove the 'Making a new repo from this template' section from README.md.",
    "Run find_fixmes (before deleting) to confirm no FIXMEs remain.",
]


def _gather_targets(root: Path) -> list[Path]:
    """Collect the scaffolding paths that are safe to delete.

    Args:
        root: Project root directory.

    Returns:
        Paths to delete: leftover license candidates (only when a real
        ``LICENSE`` already exists) and the setup-scripts folder itself.
    """
    targets: list[Path] = []
    if (root / "LICENSE").exists():
        targets.extend(sorted(root.glob("LICENSE.*.FIXME")))
    targets.append(_common.SETUP_DIR)
    return targets


def run(root: Path, *, assume_yes: bool = False, dry_run: bool = False) -> int:
    """Delete the template-setup scaffolding.

    Args:
        root: Project root directory.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without deleting anything.

    Returns:
        Process exit code (0 on success, 1 if aborted).
    """
    _common.section("Clean up template scaffolding")

    targets = _gather_targets(root)
    print("  Will delete:")
    for path in targets:
        suffix = "/" if path.is_dir() else ""
        print(f"    {path.relative_to(root)}{suffix}")

    if (root / "LICENSE").exists() is False and any(root.glob("LICENSE.*.FIXME")):
        print("  (Keeping LICENSE.*.FIXME: no LICENSE chosen yet -- run choose_license first.)")

    print("\n  Reminders (not done automatically):")
    for reminder in REMINDERS:
        print(f"    - {reminder}")

    if dry_run:
        print("\n  (dry run -- nothing deleted)")
        return 0

    print()
    if not _common.confirm("Delete the scaffolding listed above?", assume_yes=assume_yes):
        print("  Aborted; nothing deleted.")
        return 1

    setup_dir = _common.SETUP_DIR
    # Move out of the setup folder so it can be removed while this script runs.
    os.chdir(root)
    for path in targets:
        if path == setup_dir:
            continue
        path.unlink()
        print(f"  Deleted {path.relative_to(root)}")

    try:
        shutil.rmtree(setup_dir, onexc=_common.force_remove)
        print(f"  Deleted {setup_dir.relative_to(root)}/")
    except OSError as error:
        print(f"  Could not remove {setup_dir.relative_to(root)}/ ({error}).")
        print("  Delete it manually -- it is no longer needed.")

    print("\n  Cleanup complete.")
    return 0


def main() -> None:
    """Parse arguments and run cleanup."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be deleted without deleting."
    )
    args = parser.parse_args()

    root = _common.find_root()
    sys.exit(run(root, assume_yes=args.yes, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
