"""Remove the shared credentials dispatcher.

The template's credentials system has three independent pieces:

    tests/_bootstrap.py the backend-agnostic dispatcher (this file)
    tests/_keyring.py   the keyring backend implementation
    tests/_keyvault.py  the Azure KeyVault backend implementation

This removes the dispatcher itself, plus the generic ``.env.example``
"Credentials backend" block and the commented example fixture in
``tests/conftest.py`` -- the pieces that are shared by both backends rather
than specific to either one. Only run this once *neither* backend is wanted
(see ``remove_keyring.py`` and ``remove_keyvault.py``): with no backend left
to dispatch to, the dispatcher has nothing to do.

Usage:
    uv run scripts/template_setup/remove_credentials.py
    uv run scripts/template_setup/remove_credentials.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import _common

# Files deleted wholesale (relative to the project root).
_DELETE = [
    "tests/_bootstrap.py",
]

# The exact header text of the generic "Credentials backend" block in
# .env.example -- shared by both backends (e.g. tests/_keyvault.py also reads
# settings["USERNAME_KEY"]), so it is scoped to this script, not either
# backend's own remover.
_ENV_CREDENTIALS_HEADER = (
    "# --- Credentials backend ---------------------------------------------------"
)

# The first line of the commented example fixture in tests/conftest.py.
_CONFTEST_EXAMPLE_START = (
    "# Credentials via the configured backend (CREDENTIAL_BACKEND in .env: keyring by"
)


def _strip_env_example(text: str) -> tuple[str, list[str]]:
    """Remove the generic "Credentials backend" block from ``.env.example``."""
    return _common.remove_region(
        text, _ENV_CREDENTIALS_HEADER, "CLIENT_SECRET_KEY", blank="trailing"
    )


def _strip_conftest(text: str) -> tuple[str, list[str]]:
    """Remove the commented credentials-example fixture from ``tests/conftest.py``.

    Leaves the earlier generic ``client`` fixture example untouched. The
    credentials example is preceded by a lone ``#`` separator line (not a
    genuine blank line), which is consumed too so no dangling comment line is
    left behind. Runs to end of file -- this is the last thing in the file.

    Args:
        text: Contents of ``tests/conftest.py``.

    Returns:
        A ``(new_text, removed_lines)`` tuple.
    """
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    removed: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == _CONFTEST_EXAMPLE_START:
            if kept and kept[-1].strip() == "#":
                removed.append(kept.pop().strip())
            removed.extend(line.strip() for line in lines[index:] if line.strip())
            index = len(lines)
            continue
        kept.append(lines[index])
        index += 1
    return "".join(kept), removed


def _strip_readme(text: str) -> tuple[str, list[str]]:
    """Remove the credentials-dispatcher optional-features table row."""
    return _common.remove_matching(
        text, lambda line: line.startswith("| **Credentials dispatcher**")
    )


# Fixed-path files and the transform that strips their dispatcher references.
_TRANSFORMS: list[tuple[str, Callable[[str], tuple[str, list[str]]]]] = [
    (".env.example", _strip_env_example),
    ("tests/conftest.py", _strip_conftest),
    ("README.md", _strip_readme),
]


def plan_deletions(root: Path) -> list[Path]:
    """Return the dispatcher files that exist and should be deleted.

    Args:
        root: Project root directory.

    Returns:
        Existing paths from :data:`_DELETE`, in declaration order.
    """
    return [root / relpath for relpath in _DELETE if (root / relpath).exists()]


def plan_edits(root: Path) -> list[tuple[Path, str, list[str]]]:
    """Compute the rewritten contents for every file that references the dispatcher.

    Files that are missing, unreadable, or already free of these references
    are skipped.

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
    """Delete the credentials dispatcher and strip every reference to it.

    Args:
        root: Project root directory.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.

    Returns:
        Process exit code (0 on success or when nothing matched, 1 if aborted).
    """
    _common.section("Remove credentials dispatcher")

    deletions = plan_deletions(root)
    edits = plan_edits(root)
    if not deletions and not edits:
        print("\n  No credentials dispatcher artifacts found; nothing to remove.")
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
    if not _common.confirm("Remove the credentials dispatcher?", assume_yes=assume_yes):
        print("  Aborted; nothing changed.")
        return 1

    for path in deletions:
        path.unlink()
        print(f"  Deleted {path.relative_to(root)}")

    for path, new_text, _removed in edits:
        _common.write_text(path, new_text)
        print(f"  Updated {path.relative_to(root)}")

    print(
        f"\n  Removed credentials dispatcher: {len(deletions)} path(s) deleted, "
        f"{len(edits)} file(s) updated."
    )
    return 0


def main() -> None:
    """Parse arguments and run the credentials-dispatcher removal."""
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
