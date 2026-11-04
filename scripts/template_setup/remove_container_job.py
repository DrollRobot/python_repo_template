"""Remove the Azure Container Apps job files.

A project that runs as a scheduled Azure Container Apps job keeps these: the
``Dockerfile`` and ``.dockerignore`` that build its image, the Image workflow
that builds, publishes and deploys it, and ``scripts/container_job.py`` with
its example config, which set the job up in Azure. Every other project has no
use for them, so this deletes them all. The feature is off unless
``[features].container_job`` turns it on.

Usage:
    uv run scripts/template_setup/remove_container_job.py
    uv run scripts/template_setup/remove_container_job.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _common

# Files deleted wholesale (relative to the project root). The script's unit
# tests go with it.
_DELETE = [
    "Dockerfile",
    ".dockerignore",
    ".github/workflows/image.yml",
    "scripts/container_job.py",
    "scripts/container_job.example.toml",
    "tests/test_container_job.py",
]


def plan_deletions(root: Path) -> list[Path]:
    """Return the container job files that exist and should be deleted.

    Args:
        root: Project root directory.

    Returns:
        Existing paths from :data:`_DELETE`, in declaration order.
    """
    return [root / relpath for relpath in _DELETE if (root / relpath).exists()]


def run(root: Path, *, assume_yes: bool = False, dry_run: bool = False) -> int:
    """Delete the container job files.

    Args:
        root: Project root directory.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.

    Returns:
        Process exit code (0 on success or when nothing matched, 1 if aborted).
    """
    _common.section("Remove the container job files")

    deletions = plan_deletions(root)
    if not deletions:
        print("\n  No container job files found; nothing to remove.")
        return 0

    print(f"\n  Files to delete ({len(deletions)}):")
    for path in deletions:
        print(f"    {path.relative_to(root)}")

    if dry_run:
        print("\n  (dry run -- nothing changed)")
        return 0

    print()
    if not _common.confirm("Remove the container job files?", assume_yes=assume_yes):
        print("  Aborted; nothing changed.")
        return 1

    for path in deletions:
        path.unlink()
        print(f"  Deleted {path.relative_to(root)}")

    print(f"\n  Removed the container job files: {len(deletions)} path(s) deleted.")
    return 0


def main() -> None:
    """Parse arguments and run the container job removal."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing."
    )
    args = parser.parse_args()

    root = _common.find_root()
    sys.exit(run(root, assume_yes=args.yes, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
