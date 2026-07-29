"""Remove the secret-storage machinery from the config package.

For projects whose configuration holds no secrets. The machinery is the
backend dispatcher, every credential backend, and their test modules:

    src/<package>/config/secrets.py       the backend dispatcher
    src/<package>/config/*_backend.py     every credential backend
    tests/test_config_secrets.py          the dispatcher's unit tests
    tests/test_keyring_backend.py         the keyring backend's unit tests
    tests/test_keyvault_backend.py        the Key Vault backend's unit tests

Only those files are deleted; nothing else is edited. The rest of the config
system imports the machinery lazily and only when the settings schema marks a
field ``secret``, so with no secret fields it keeps working untouched: the
resolver skips the secret layer, config.toml validation stops accepting
``credential_backend`` (and backend-declared keys), and the config CLI stops
offering ``set-secret``/``delete-secret``.

Manual follow-ups (this script only deletes files):

- Remove any ``"secret": True`` fields from the settings schema
  (``src/<package>/config/schema.py``); resolution fails loudly while they
  remain.
- Delete the ``keyring`` and ``keyvault`` extras in ``pyproject.toml``'s
  ``[project.optional-dependencies]``, then run ``uv lock`` and ``uv sync``.

Usage:
    uv run scripts/template_setup/remove_secret_storage.py
    uv run scripts/template_setup/remove_secret_storage.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _common

# Fixed-path files deleted wholesale (relative to the project root).
_DELETE = [
    "tests/test_config_secrets.py",
    "tests/test_keyring_backend.py",
    "tests/test_keyvault_backend.py",
]

# The dispatcher and every backend, located by glob because the package
# directory carries the project's own (possibly already renamed) import name.
_DELETE_GLOBS = [
    "src/*/config/secrets.py",
    "src/*/config/*_backend.py",
]


def plan_deletions(root: Path) -> list[Path]:
    """Return the secret-storage files that exist and should be deleted.

    Args:
        root: Project root directory.

    Returns:
        The dispatcher and backend files (wherever the package lives under
        ``src/``) followed by the fixed-path files from :data:`_DELETE`,
        existing paths only.
    """
    paths: list[Path] = []
    for pattern in _DELETE_GLOBS:
        paths.extend(sorted(root.glob(pattern)))
    paths.extend(root / relpath for relpath in _DELETE if (root / relpath).exists())
    return paths


def run(root: Path, *, assume_yes: bool = False, dry_run: bool = False) -> int:
    """Delete the secret-storage machinery and its test suites.

    Args:
        root: Project root directory.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.

    Returns:
        Process exit code (0 on success or when nothing matched, 1 if aborted).
    """
    _common.section("Remove the secret-storage machinery")

    deletions = plan_deletions(root)
    if not deletions:
        print("\n  No secret-storage machinery found; nothing to remove.")
        return 0

    print(f"\n  Files to delete ({len(deletions)}):")
    for path in deletions:
        print(f"    {path.relative_to(root)}")

    if dry_run:
        print("\n  (dry run -- nothing changed)")
        return 0

    print()
    if not _common.confirm("Remove the secret-storage machinery?", assume_yes=assume_yes):
        print("  Aborted; nothing changed.")
        return 1

    for path in deletions:
        path.unlink()
        print(f"  Deleted {path.relative_to(root)}")

    print(f"\n  Removed the secret-storage machinery: {len(deletions)} path(s) deleted.")
    print("  Reminders (not done automatically):")
    print('    - Remove any "secret": True fields from the settings schema')
    print("      (src/<package>/config/schema.py); resolution fails loudly while")
    print("      they remain.")
    print("    - Delete the 'keyring' and 'keyvault' extras in pyproject.toml's")
    print("      [project.optional-dependencies], then run 'uv lock' and 'uv sync'.")
    return 0


def main() -> None:
    """Parse arguments and run the secret-storage removal."""
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
