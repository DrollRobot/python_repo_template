"""Unit tests for scripts/template_setup/remove_secret_storage.py.

The template_setup folder is not a package, so the module is imported by
adding the folder to sys.path, mirroring how the setup scripts import their
shared _common module.

It also holds the template's own end-to-end guard for the state the script
creates: a throwaway copy of this package with a secret-free schema and the
machinery actually deleted, running the config test modules that ship with
it. The template's normal CI only ever exercises the secret-bearing FIXME
schema, so without this the "works with secret storage removed" promise would
regress silently.

This file is itself a dev-script test: cleanup.py matches it to
scripts/template_setup/remove_secret_storage.py and deletes it along with the
rest of the scaffolding, so it never lingers in a project started from the
template.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "template_setup"))

import remove_secret_storage

_DISPATCHER = "src/my_project/config/secrets.py"
_KEYRING = "src/my_project/config/keyring_backend.py"
_KEYVAULT = "src/my_project/config/keyvault_backend.py"
_SCHEMA = "src/my_project/config/schema.py"
_TESTS = [
    "tests/test_config_secrets.py",
    "tests/test_keyring_backend.py",
    "tests/test_keyvault_backend.py",
]

# This repo's own root, the source for the end-to-end copy at the bottom.
_TEMPLATE_ROOT = Path(__file__).resolve().parent.parent

# The config test modules that ship with the config system, minus the
# dispatcher's own (which the script deletes). Copied into the throwaway
# project together with the helpers they import.
_SHIPPED_TESTS = [
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/_config_test_object.py",
    "tests/test_config_cli.py",
    "tests/test_config_file.py",
    "tests/test_config_paths.py",
    "tests/test_config_resolve.py",
    "tests/test_config_schema.py",
]

# Minimal pytest configuration for the throwaway project: the copied package
# and its tests, the marker taxonomy they use, and nothing else (no coverage,
# no strict flags -- this run is about pass/fail of the copied modules).
_E2E_PYPROJECT = """\
[tool.pytest.ini_options]
pythonpath = [".", "src"]
testpaths = ["tests"]
markers = [
    "unit: single function/class in isolation",
    "integration: multiple real components wired together",
    "e2e: whole application end to end",
    "smoke: fast 'is it fundamentally broken' check",
    "regression: guards against reintroduction of a previously fixed bug",
    "acceptance: verifies behavior against a requirement",
    "functional: tests behavior/output of a feature",
    "live: requires a real external resource",
    "destructive_local: mutates the host/device",
    "destructive_remote: mutates a remote/external system",
    "slow: long-running",
]
"""

_SCHEMA_VIA_CONSTRUCTOR = """\
from dataclasses import dataclass, field


def secret(*, help, default_secret_name=None):
    return field(repr=False, kw_only=True, metadata={"secret": True, "help": help})


def option(*, help, default_value=None):
    return field(default=default_value, kw_only=True, metadata={"help": help})


@dataclass(frozen=True)
class Settings:
    tenant_id: str = option(help="Entra tenant ID")
    client_secret: str = secret(help="App registration client secret")
"""

_SCHEMA_VIA_METADATA = """\
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    name: str = field(metadata={"help": "h"})
    token: str = field(repr=False, metadata={"secret": True, "help": "h"})
    inert: str = field(default="x", metadata={"secret": False, "help": "h"})
"""

_SCHEMA_WITHOUT_SECRETS = """\
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    name: str = field(metadata={"help": "h"})
    count: int = field(default=3, metadata={"help": "h"})
"""


def _make_project(root: Path, *relpaths: str) -> list[Path]:
    """Create the named files under ``root`` and return their paths."""
    paths: list[Path] = []
    for relpath in relpaths:
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub\n", encoding="utf-8")
        paths.append(path)
    return paths


@pytest.mark.unit
def test_plan_deletions_is_empty_when_the_machinery_is_gone(tmp_path: Path) -> None:
    """A project that already removed the machinery plans nothing."""
    assert remove_secret_storage.plan_deletions(tmp_path) == []


@pytest.mark.unit
def test_plan_deletions_finds_dispatcher_backends_and_tests(tmp_path: Path) -> None:
    """Dispatcher, every backend, and their tests are planned together."""
    _make_project(tmp_path, _DISPATCHER, _KEYRING, _KEYVAULT, *_TESTS)

    deletions = remove_secret_storage.plan_deletions(tmp_path)
    assert [path.name for path in deletions] == [
        "secrets.py",
        "keyring_backend.py",
        "keyvault_backend.py",
        "test_config_secrets.py",
        "test_keyring_backend.py",
        "test_keyvault_backend.py",
    ]


@pytest.mark.unit
def test_plan_deletions_catches_project_added_backends(tmp_path: Path) -> None:
    """A backend added downstream matches the *_backend.py glob and goes too."""
    _make_project(tmp_path, _DISPATCHER, "src/my_project/config/custom_backend.py")

    deletions = remove_secret_storage.plan_deletions(tmp_path)
    assert [path.name for path in deletions] == ["secrets.py", "custom_backend.py"]


@pytest.mark.unit
def test_plan_deletions_keeps_the_rest_of_the_config_package(tmp_path: Path) -> None:
    """The core config modules and their tests are never planned."""
    _make_project(
        tmp_path,
        _DISPATCHER,
        "src/my_project/config/resolve.py",
        "src/my_project/config/file.py",
        "tests/test_config_resolve.py",
    )

    deletions = remove_secret_storage.plan_deletions(tmp_path)
    assert [path.name for path in deletions] == ["secrets.py"]


@pytest.mark.integration
@pytest.mark.functional
def test_run_returns_zero_when_nothing_matches(tmp_path: Path) -> None:
    """A project that never had the machinery is a no-op success."""
    assert remove_secret_storage.run(tmp_path, assume_yes=True) == 0


@pytest.mark.integration
@pytest.mark.functional
def test_run_dry_run_deletes_nothing(tmp_path: Path) -> None:
    """--dry-run reports the plan and leaves every file in place."""
    paths = _make_project(tmp_path, _DISPATCHER, _KEYRING, *_TESTS)

    assert remove_secret_storage.run(tmp_path, assume_yes=True, dry_run=True) == 0
    assert all(path.exists() for path in paths)


@pytest.mark.integration
@pytest.mark.functional
def test_run_deletes_the_machinery_and_nothing_else(tmp_path: Path) -> None:
    """A real run deletes the machinery files and leaves the core modules."""
    doomed = _make_project(tmp_path, _DISPATCHER, _KEYRING, _KEYVAULT, *_TESTS)
    (keep,) = _make_project(tmp_path, "src/my_project/config/resolve.py")

    assert remove_secret_storage.run(tmp_path, assume_yes=True) == 0
    assert not any(path.exists() for path in doomed)
    assert keep.exists()


# --- schema precondition -------------------------------------------------------------


def _write_schema(root: Path, source: str) -> Path:
    """Write *source* as the throwaway project's config schema module."""
    path = root / _SCHEMA
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.unit
def test_find_schema_returns_none_without_a_config_package(tmp_path: Path) -> None:
    """No config system in the project: nothing to inspect, nothing to refuse."""
    assert remove_secret_storage.find_schema(tmp_path) is None


@pytest.mark.unit
def test_find_secret_fields_reads_the_secret_constructor(tmp_path: Path) -> None:
    """Fields declared with the schema's secret() helper are found by name."""
    path = _write_schema(tmp_path, _SCHEMA_VIA_CONSTRUCTOR)
    assert remove_secret_storage.find_secret_fields(path) == ["client_secret"]


@pytest.mark.unit
def test_find_secret_fields_reads_the_metadata_spelling(tmp_path: Path) -> None:
    """A bare field(metadata={"secret": True}) counts; a False value does not."""
    path = _write_schema(tmp_path, _SCHEMA_VIA_METADATA)
    assert remove_secret_storage.find_secret_fields(path) == ["token"]


@pytest.mark.unit
def test_find_secret_fields_empty_for_a_secret_free_schema(tmp_path: Path) -> None:
    """The state the script requires: Settings declares no secret at all."""
    path = _write_schema(tmp_path, _SCHEMA_WITHOUT_SECRETS)
    assert remove_secret_storage.find_secret_fields(path) == []


@pytest.mark.unit
def test_find_secret_fields_tolerates_an_unparseable_schema(tmp_path: Path) -> None:
    """A broken schema is the project's problem to fix, not this check's."""
    path = _write_schema(tmp_path, "class Settings(:\n")
    assert remove_secret_storage.find_secret_fields(path) == []


@pytest.mark.integration
@pytest.mark.functional
def test_run_refuses_while_the_schema_declares_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario C is refused up front: the fields are named and nothing is deleted."""
    paths = _make_project(tmp_path, _DISPATCHER, _KEYRING, *_TESTS)
    _write_schema(tmp_path, _SCHEMA_VIA_CONSTRUCTOR)

    assert remove_secret_storage.run(tmp_path, assume_yes=True) == 1
    out = capsys.readouterr().out
    assert "client_secret" in out
    assert "Refused; nothing changed." in out
    assert all(path.exists() for path in paths)


@pytest.mark.integration
@pytest.mark.functional
def test_run_force_deletes_and_warns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--force is an escape hatch, never a silent one."""
    paths = _make_project(tmp_path, _DISPATCHER, _KEYRING, *_TESTS)
    _write_schema(tmp_path, _SCHEMA_VIA_CONSTRUCTOR)

    assert remove_secret_storage.run(tmp_path, assume_yes=True, force=True) == 0
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "client_secret" in out
    assert not any(path.exists() for path in paths)


@pytest.mark.integration
@pytest.mark.functional
def test_run_proceeds_for_a_secret_free_schema(tmp_path: Path) -> None:
    """The documented order -- edit the schema, then remove -- just works."""
    paths = _make_project(tmp_path, _DISPATCHER, _KEYRING, *_TESTS)
    _write_schema(tmp_path, _SCHEMA_WITHOUT_SECRETS)

    assert remove_secret_storage.run(tmp_path, assume_yes=True) == 0
    assert not any(path.exists() for path in paths)


# --- end-to-end: the config tests with the machinery actually gone -------------------


def _strip_secret_fields(schema_path: Path) -> None:
    """Delete every secret() field declaration from a copy of the real schema.

    The manual edit the script now demands, done mechanically so this test
    tracks whatever the FIXME schema happens to declare.
    """
    source = schema_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    doomed: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "Settings":
            continue
        for statement in node.body:
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.value, ast.Call)
                and getattr(statement.value.func, "id", "") == "secret"
            ):
                doomed.update(range(statement.lineno, (statement.end_lineno or 0) + 1))
    kept = [
        line
        for number, line in enumerate(source.splitlines(keepends=True), start=1)
        if number not in doomed
    ]
    schema_path.write_text("".join(kept), encoding="utf-8")


def _build_secret_free_project(root: Path) -> Path:
    """Copy this package into *root* with a secret-free schema and no machinery.

    Returns:
        The copied package's schema module.
    """
    package = _TEMPLATE_ROOT / "src" / "python_repo_template"
    shutil.copytree(
        package,
        root / "src" / "python_repo_template",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (root / "tests").mkdir()
    for relpath in _SHIPPED_TESTS:
        shutil.copy2(_TEMPLATE_ROOT / relpath, root / relpath)
    (root / "pyproject.toml").write_text(_E2E_PYPROJECT, encoding="utf-8")

    schema_path = root / "src" / "python_repo_template" / "config" / "schema.py"
    _strip_secret_fields(schema_path)
    return schema_path


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.functional
def test_config_tests_pass_with_the_machinery_removed(tmp_path: Path) -> None:
    """The shipped config tests stay green in a secret-free project.

    Builds the state remove_secret_storage.py is for -- no secret fields, no
    secrets.py, no backends -- and runs the config test modules that ship
    with the config system against it. Guards the promise in
    tests/_config_test_object.py's docstring, which this repo's own CI (always
    the secret-bearing FIXME schema) cannot check.
    """
    project = tmp_path / "project"
    project.mkdir()
    schema_path = _build_secret_free_project(project)
    assert remove_secret_storage.find_secret_fields(schema_path) == []

    assert remove_secret_storage.run(project, assume_yes=True) == 0
    assert not (project / "src" / "python_repo_template" / "config" / "secrets.py").exists()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Skips prove the run imported the stripped copy, not the installed
    # package: requires_secret_storage only fires when secrets.py is absent.
    assert " skipped" in result.stdout, result.stdout
