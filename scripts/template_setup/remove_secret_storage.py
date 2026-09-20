"""Remove the secret-storage machinery from the config package.

For projects whose configuration holds no secrets. The machinery is the
backend dispatcher, every credential backend, and their test modules:

    src/<package>/config/secrets.py       the backend dispatcher
    src/<package>/config/*_backend.py     every credential backend
    tests/test_config_secrets.py          the dispatcher's unit tests
    tests/test_keyring_backend.py         the keyring backend's unit tests
    tests/test_keyvault_backend.py        the Key Vault backend's unit tests

Those files are deleted and one is edited: ``config/__init__.py``, whose
package docstring describes the machinery and points users at
``credential_backend``, a config.toml key that validation rejects once the
dispatcher is gone. The rest of the config system imports the machinery
lazily and only when the settings schema marks a field ``secret``, so with no
secret fields it keeps working untouched: the resolver skips the secret
layer, config.toml validation stops accepting ``credential_backend`` (and
backend-declared keys), and the config CLI stops offering
``set-secret``/``delete-secret``.

"With no secret fields" is a precondition, not a hope: before deleting
anything this script parses ``src/<package>/config/schema.py`` and refuses
when ``Settings`` still declares a secret field, naming the fields. Removing
the machinery under them leaves a package that fails on every run, so the
schema edit comes first. ``--force`` deletes anyway, with a warning.

Manual follow-ups:

- Delete the ``keyring`` and ``keyvault`` extras in ``pyproject.toml``'s
  ``[project.optional-dependencies]``, then run ``uv lock`` and ``uv sync``.
- Set ``CREDENTIAL_BACKEND = "none"`` in ``config/schema.py`` and prune its
  comment block: with the machinery gone, the backend names it offers are no
  longer choices. Nothing breaks while it says ``"prompt"``; it is just
  wrong.
- Expect ``config/cli.py`` coverage to drop. Its ``set-secret`` /
  ``delete-secret`` / backend-prompt code (roughly a third of the module)
  can never run in a project with no secret fields, and the tests for it
  skip. Lower ``--cov-fail-under`` to match rather than keeping dead code
  for the number.

Usage:
    uv run scripts/template_setup/remove_secret_storage.py
    uv run scripts/template_setup/remove_secret_storage.py --dry-run
    uv run scripts/template_setup/remove_secret_storage.py --force
"""

from __future__ import annotations

import argparse
import ast
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

# The settings schema, located the same way, and the class the precondition
# check inspects inside it.
_SCHEMA_GLOB = "src/*/config/schema.py"
_SETTINGS_CLASS = "Settings"

# The config package docstring, located the same way. It describes the
# machinery this script deletes, so it is rewritten rather than left lying.
_CONFIG_INIT_GLOB = "src/*/config/__init__.py"

# The module title: the package no longer handles secrets. A fragment, not a
# whole line, because the package name follows it.
_INIT_TITLE_OLD = '"""Configuration and secrets for '
_INIT_TITLE_NEW = '"""Configuration for '

# The paragraph describing where values live. The replacement drops the
# credential-backend sentence, which now names an illegal config.toml key.
_INIT_STORAGE_OLD = """\
Non-secret values live in a per-user ``config.toml`` (see ``paths.py`` for
the OS-specific location); secrets live in whichever credential backend the
user selects (``credential_backend`` in config.toml) and are never written
to the file. The config CLI creates and edits the file; no hand-editing is
required.
"""

_INIT_STORAGE_NEW = """\
Values live in a per-user ``config.toml`` (see ``paths.py`` for the
OS-specific location). This package stores no secrets: the secret-storage
machinery has been removed, so ``credential_backend`` and the other reserved
backend keys are no longer legal in the file. The config CLI creates and
edits it; no hand-editing is required.
"""

# The module-map entry for the deleted files, dropped outright.
_INIT_MODULE_MAP_ENTRY = """\
- ``secrets.py`` + ``*_backend.py`` — optional secret-storage machinery:
  the backend dispatcher and the individual credential backends. Only
  consulted when the schema marks fields ``secret``; deletable as a unit
  when it marks none.
"""


def _replace_block(text: str, old: str, new: str) -> tuple[str, bool]:
    """Replace a run of whole lines in *text*, whatever its line endings.

    The blocks above are written with LF endings; a project checked out with
    CRLF must be edited too, so both sides are re-joined with the file's own
    ending before matching.

    Args:
        text: The file contents.
        old: The block to find, as newline-separated lines.
        new: Its replacement, or ``""`` to delete the block.

    Returns:
        A ``(new_text, replaced)`` tuple.
    """
    eol = "\r\n" if "\r\n" in text else "\n"
    old_block = eol.join(old.splitlines()) + eol
    if old_block not in text:
        return text, False
    new_block = eol.join(new.splitlines()) + eol if new else ""
    return text.replace(old_block, new_block, 1), True


def rewrite_config_init(text: str) -> tuple[str, list[str]]:
    """Rewrite the config package docstring for a project with no secrets.

    Retitles the module, replaces the storage paragraph, and drops the
    module-map entry for the deleted files. Each part is independent, so a
    project that has already edited some of them keeps its wording.

    Args:
        text: Contents of ``config/__init__.py``.

    Returns:
        A ``(new_text, changed)`` tuple, where *changed* names the parts that
        were rewritten. An empty list means the docstring needs no edit.
    """
    changed: list[str] = []
    if _INIT_TITLE_OLD in text:
        text = text.replace(_INIT_TITLE_OLD, _INIT_TITLE_NEW, 1)
        changed.append("module title")
    text, replaced = _replace_block(text, _INIT_STORAGE_OLD, _INIT_STORAGE_NEW)
    if replaced:
        changed.append("config.toml paragraph")
    text, replaced = _replace_block(text, _INIT_MODULE_MAP_ENTRY, "")
    if replaced:
        changed.append("module-map entry")
    return text, changed


def plan_edits(root: Path) -> list[tuple[Path, str, list[str]]]:
    """Compute the rewritten contents of every file that describes the machinery.

    Args:
        root: Project root directory.

    Returns:
        A list of ``(path, new_text, changed)`` tuples for files that
        actually change; empty when the docstring is already correct.
    """
    edits: list[tuple[Path, str, list[str]]] = []
    for path in sorted(root.glob(_CONFIG_INIT_GLOB)):
        text = _common.read_text(path)
        if text is None:
            continue
        new_text, changed = rewrite_config_init(text)
        if changed:
            edits.append((path, new_text, changed))
    return edits


def find_schema(root: Path) -> Path | None:
    """Return the project's settings schema module, or None when absent.

    Args:
        root: Project root directory.

    Returns:
        ``src/<package>/config/schema.py``, or None when the config system
        is not present.
    """
    return next(iter(sorted(root.glob(_SCHEMA_GLOB))), None)


def _declares_secret(value: ast.expr | None) -> bool:
    """Return True when a field declaration marks the field as a secret.

    Recognizes both supported spellings: the schema's ``secret(...)`` helper
    and a bare ``field(metadata={"secret": True})``.

    Args:
        value: The right-hand side of an annotated class attribute.

    Returns:
        True when the declaration classifies the field as a secret.
    """
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if called == "secret":
        return True
    if called != "field":
        return False
    for keyword in value.keywords:
        if keyword.arg != "metadata" or not isinstance(keyword.value, ast.Dict):
            continue
        for key, item in zip(keyword.value.keys, keyword.value.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "secret":
                return isinstance(item, ast.Constant) and bool(item.value)
    return False


def find_secret_fields(schema_path: Path) -> list[str]:
    """Return the ``Settings`` fields still marked secret, in declaration order.

    The module is parsed, never imported, so this runs before any dependency
    is installed and never executes project code. An unreadable or
    unparseable schema yields no names: this check exists to stop a known-bad
    removal, not to become a second syntax checker.

    Args:
        schema_path: Location of the project's ``config/schema.py``.

    Returns:
        The names of every secret-classified field of ``Settings``.
    """
    try:
        tree = ast.parse(schema_path.read_text(encoding="utf-8"))
    except OSError, SyntaxError, ValueError:
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != _SETTINGS_CLASS:
            continue
        for statement in node.body:
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and _declares_secret(statement.value)
            ):
                names.append(statement.target.id)
    return names


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


def run(root: Path, *, assume_yes: bool = False, dry_run: bool = False, force: bool = False) -> int:
    """Delete the secret-storage machinery and rewrite what described it.

    Refuses before deleting anything when the settings schema still declares
    secret fields, unless *force* is set.

    Args:
        root: Project root directory.
        assume_yes: Skip the confirmation prompt.
        dry_run: Show the plan without changing anything.
        force: Delete even while the schema still declares secret fields.

    Returns:
        Process exit code (0 on success or when nothing matched, 1 if
        aborted or refused).
    """
    _common.section("Remove the secret-storage machinery")

    deletions = plan_deletions(root)
    edits = plan_edits(root)
    if not deletions and not edits:
        print("\n  No secret-storage machinery found; nothing to remove.")
        return 0

    schema_path = find_schema(root)
    secret_fields = find_secret_fields(schema_path) if schema_path is not None else []
    if deletions and secret_fields and schema_path is not None:
        where = schema_path.relative_to(root)
        listing = ", ".join(secret_fields)
        if not force:
            print(f"\n  {where} still declares {len(secret_fields)} secret field(s):")
            for name in secret_fields:
                print(f"    {name}")
            print(
                "\n  Removing the machinery under them leaves a package that cannot\n"
                "  resolve its own configuration: every run fails with 'the settings\n"
                "  schema marks <field> as secret, but the secret-storage machinery\n"
                "  (secrets.py) has been removed'. Edit the schema first -- replace\n"
                "  those fields with option(...) or drop them -- then re-run.\n"
                "\n  Pass --force to delete the machinery anyway."
            )
            print("\n  Refused; nothing changed.")
            return 1
        print(f"\n  WARNING: --force: {where} still declares secret field(s): {listing}.")
        print("  Configuration will not resolve until they are removed from the schema.")

    if deletions:
        print(f"\n  Files to delete ({len(deletions)}):")
        for path in deletions:
            print(f"    {path.relative_to(root)}")
    if edits:
        print(f"\n  Files to edit ({len(edits)}):")
        for path, _, changed in edits:
            print(f"    {path.relative_to(root)}  ({', '.join(changed)})")

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
    for path, new_text, _ in edits:
        _common.write_text(path, new_text)
        print(f"  Edited {path.relative_to(root)}")

    print(
        f"\n  Removed the secret-storage machinery: {len(deletions)} path(s) deleted, "
        f"{len(edits)} edited."
    )
    print("  Reminders (not done automatically):")
    print("    - Delete the 'keyring' and 'keyvault' extras in pyproject.toml's")
    print("      [project.optional-dependencies], then run 'uv lock' and 'uv sync'.")
    print('    - Set CREDENTIAL_BACKEND = "none" in config/schema.py and prune the')
    print("      backend names its comment block offers; they are no longer choices.")
    print("    - Expect config/cli.py coverage to drop: its secret commands can never")
    print("      run here and their tests skip. Lower --cov-fail-under to match rather")
    print("      than keeping dead code for the number.")
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete even while the settings schema still declares secret fields.",
    )
    args = parser.parse_args()

    root = _common.find_root()
    sys.exit(run(root, assume_yes=args.yes, dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
