"""Remove the keyring credential backend.

The template's credentials system has three independent pieces:

    tests/_keyring.py            the keyring backend implementation
    scripts/setup_credentials.py stores secrets in the OS keyring (keyring-only)
    tests/_bootstrap.py          the backend-agnostic dispatcher (shared with KeyVault)

This removes only the two keyring-specific files above, the ``keyring`` line
in ``pyproject.toml``, and the keyring bullet + table row in ``README.md``.
``tests/_bootstrap.py`` is untouched -- it never hardcodes "keyring" or
"keyvault" by name, so it stays functional as long as at least one backend
remains. See ``remove_credentials.py``, which removes the dispatcher itself
once neither backend is wanted.

Run ``uv lock`` (then ``uv sync``) afterwards to drop ``keyring`` from the
lockfile and the virtualenv -- this script does not touch the generated
``uv.lock``.

Usage:
    uv run scripts/template_setup/remove_keyring.py
    uv run scripts/template_setup/remove_keyring.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import _common

# Files deleted wholesale (relative to the project root).
_DELETE = [
    "tests/_keyring.py",
    "scripts/setup_credentials.py",
]


def _strip_pyproject(text: str) -> tuple[str, list[str]]:
    """Remove the ``keyring`` dependency line from the ``dev`` group."""
    return _common.remove_matching(text, lambda line: line.startswith('"keyring>=25.0,<26"'))


def _strip_readme(text: str) -> tuple[str, list[str]]:
    """Remove the keyring tool bullet and the optional-features table row."""
    removed: list[str] = []
    text, part = _common.remove_matching(text, lambda line: line.startswith("- **keyring**"))
    removed += part
    text, part = _common.remove_matching(
        text, lambda line: line.startswith("| **Keyring backend**")
    )
    removed += part
    return text, removed


# Fixed-path files and the transform that strips their keyring references.
_TRANSFORMS: list[tuple[str, Callable[[str], tuple[str, list[str]]]]] = [
    ("pyproject.toml", _strip_pyproject),
    ("README.md", _strip_readme),
]


def plan_deletions(root: Path) -> list[Path]:
    """Return the keyring backend files that exist and should be deleted.

    Args:
        root: Project root directory.

    Returns:
        Existing paths from :data:`_DELETE`, in declaration order.
    """
    return [root / relpath for relpath in _DELETE if (root / relpath).exists()]


def plan_edits(root: Path) -> list[tuple[Path, str, list[str]]]:
    """Compute the rewritten contents for every file that references keyring.

    Files that are missing, unreadable, or free of keyring references are skipped.

    Args:
        root: Project root directory.

    Returns:
        A list of ``(path, new_text, removed_lines)`` tuples for files that
        actually change.
    """
    edits: list[tuple[Path, str, list[str]]] = []
    for relpath, transform in _TRANSFORMS:
        path = root / relpath
        text = _common.read_text(path)
        if text is None:
            continue
        new_text, removed = transform(text)
        if removed:
            edits.append((path, new_text, removed))
    return edits


def run(root: Path, *, assume_yes: bool = False, dry_run: bool = False) -> int:
    """Delete the keyring backend and strip every reference to it.

    Args:
        root: Project root directory.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.

    Returns:
        Process exit code (0 on success or when nothing matched, 1 if aborted).
    """
    _common.section("Remove keyring backend")

    deletions = plan_deletions(root)
    edits = plan_edits(root)
    if not deletions and not edits:
        print("\n  No keyring backend artifacts found; nothing to remove.")
        return 0

    if deletions:
        print(f"\n  Files to delete ({len(deletions)}):")
        for path in deletions:
            print(f"    {path.relative_to(root)}")

    if edits:
        print(f"\n  Files to update ({len(edits)}):")
        for path, _new, removed in edits:
            print(f"    {path.relative_to(root)}")
            for line in removed:
                print(f"      - {line}")

    if dry_run:
        print("\n  (dry run -- nothing changed)")
        return 0

    print()
    if not _common.confirm("Remove the keyring backend?", assume_yes=assume_yes):
        print("  Aborted; nothing changed.")
        return 1

    for path in deletions:
        path.unlink()
        print(f"  Deleted {path.relative_to(root)}")

    for path, new_text, _removed in edits:
        _common.write_text(path, new_text)
        print(f"  Updated {path.relative_to(root)}")

    print(
        f"\n  Removed keyring backend: {len(deletions)} path(s) deleted, "
        f"{len(edits)} file(s) updated."
    )
    print("  Reminder: run 'uv lock' (then 'uv sync') to drop keyring from the lockfile and venv.")
    return 0


def main() -> None:
    """Parse arguments and run the keyring-backend removal."""
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
