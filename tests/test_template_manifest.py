"""Tests for the real template: its template.toml and the projects it makes.

Each test works on a committed copy of the template's working tree, so it checks
uncommitted changes too, and neither the real index nor a git hook's environment
matters. Git identity and config are pinned through environment variables.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import template_sync as ts

GIT = shutil.which("git") or "git"

_TEST_GIT_ENV = {
    "GIT_AUTHOR_NAME": "template-sync-test",
    "GIT_AUTHOR_EMAIL": "template-sync-test@example.invalid",
    "GIT_COMMITTER_NAME": "template-sync-test",
    "GIT_COMMITTER_EMAIL": "template-sync-test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}
# Set when pytest runs inside a git hook; they would point git at this repo.
_GIT_HOOK_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
)


@pytest.fixture(autouse=True)
def _isolated_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    for key, value in _TEST_GIT_ENV.items():
        monkeypatch.setenv(key, value)
    for key in _GIT_HOOK_VARS:
        monkeypatch.delenv(key, raising=False)
    gitconfig = tmp_path_factory.mktemp("home") / "gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))


def sh(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` and return its stdout."""
    proc = subprocess.run(  # noqa: S603  (fixed argv list, no shell)
        [GIT, *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return proc.stdout


def commit_all(root: Path, msg: str = "c") -> str:
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", msg)
    return sh(root, "rev-parse", "HEAD").strip()


def run(*argv: object) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ts.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
# What a project may not contain anywhere but .template.toml: the template's
# names, its handle, its tooling, and placeholders that `new` was given values for.
TEMPLATE_TRACES = (
    "DrollRobot",
    "python_repo_template",
    "python-repo-template",
    "template_sync",
    "template.toml",
    "TEMPLATE SETUP",
    "TEMPLATE NOTES",
    "template-only",
    "<COPYRIGHT HOLDER>",
    "<COPYRIGHT YEAR>",
    "<COMPANY>",
)
PROJECT_VALUES = (
    "--set", "project=my-widget", "--set", "github_user=octo-dev",
    "--set", "copyright_holder=Jane Doe", "--set", "copyright_year=2026",
    "--set", "company=Widget Co",
)  # fmt: skip
# Tests that need the audited .secrets.baseline a user creates after `new`.
NEEDS_BASELINE = (
    "--deselect",
    "tests/test_secrets_baseline_audited.py::test_baseline_is_fully_audited",
    "--deselect",
    "tests/test_secrets_guards_under_hook_env.py::test_secrets_guards_pass_under_a_hook_environment",
)
SCRIPT_FEATURES = ("remote_disposable_scripts", "container_job")
# A Python version literal (3.10 .. 3.19) that isn't part of a longer version.
PYTHON_VERSION = re.compile(r"(?<![\d.])3\.1\d(?!\d)")


def _features(choice: str) -> list[str]:
    """``--feature`` flags for a named feature set of the real manifest."""
    if choice == "defaults":
        return []
    manifest = ts.load_manifest(ROOT)
    flags = []
    for name in manifest.features:
        on = choice == "all-on" and (name == "license_mit" or not name.startswith("license_"))
        flags += ["--feature", f"{name}={'true' if on else 'false'}"]
    return flags


@pytest.fixture(scope="module")
def real_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A committed copy of the real template's working tree, as a throwaway repo.

    The working tree rather than HEAD, so the tests check uncommitted changes
    too; a copy, so neither the real index nor a git hook's environment matters.
    """
    snapshot = tmp_path_factory.mktemp("real") / "python_repo_template"
    listed = sh(ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    with pytest.MonkeyPatch.context() as mp:
        for key, value in _TEST_GIT_ENV.items():
            mp.setenv(key, value)
        for key in _GIT_HOOK_VARS:
            mp.delenv(key, raising=False)
        mp.setenv("GIT_CONFIG_GLOBAL", str(tmp_path_factory.mktemp("home") / "gitconfig"))
        for rel in listed.split("\0"):
            src = ROOT / rel
            if rel and src.is_file():
                dst = snapshot / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        sh(snapshot, "init", "-q")
        commit_all(snapshot, "snapshot")
    return snapshot


@pytest.mark.integration
def test_real_template_validates_without_warnings(real_template: Path) -> None:
    code, out, err = run("--template", real_template, "validate")
    assert code == 0, err
    assert "warning:" not in out


@pytest.mark.integration
@pytest.mark.parametrize("choice", ["defaults", "all-off", "all-on"])
def test_real_template_new_project_is_clean(real_template: Path, choice: str) -> None:
    project = real_template.parent / f"my-widget-{choice}"
    code, _out, err = run(
        "--template", real_template, "new", project, "-y", *PROJECT_VALUES, *_features(choice)
    )
    assert code == 0, err
    cfg = tomllib.loads((project / ts.CHILD_CONFIG_NAME).read_text())
    on = {name for name, value in cfg["features"].items() if value}

    shipped = [p for p in sh(project, "ls-files").split() if p != ts.CHILD_CONFIG_NAME]
    hits = [f"{p}: path" for p in shipped for t in TEMPLATE_TRACES if t in p]
    for rel in shipped:
        data = (project / rel).read_bytes()
        if ts.is_binary(data):
            continue
        for n, line in enumerate(data.decode("utf-8").split("\n"), 1):
            hits += [f"{rel}:{n}: {t}" for t in TEMPLATE_TRACES if t in line]
            if ts.marker_on(line):
                hits.append(f"{rel}:{n}: leftover marker")
    assert hits == []
    assert not [p for p in shipped if p.startswith("child/")]

    pyproject = tomllib.loads((project / "pyproject.toml").read_text())
    assert pyproject["project"]["name"] == "my-widget"
    has_scripts = bool(on & set(SCRIPT_FEATURES))
    assert ("scripts" in pyproject["tool"]["vulture"]["paths"]) is has_scripts
    assert (project / "scripts").is_dir() is has_scripts
    if "container_job" in on:
        entrypoint = next(
            line for line in (project / "Dockerfile").read_text().splitlines()
            if line.startswith("ENTRYPOINT")
        )  # fmt: skip
        assert any(f'"{name}"' in entrypoint for name in pyproject["project"]["scripts"])


@pytest.mark.integration
@pytest.mark.parametrize(
    ("license_feature", "license_keys"),
    [
        ("license_mit", {"copyright_holder", "copyright_year"}),
        ("license_apache", {"copyright_holder", "copyright_year"}),
        ("license_gnu", set()),
        ("license_proprietary", {"copyright_holder", "copyright_year", "company"}),
    ],
)
def test_real_template_asks_only_for_the_values_its_license_uses(
    real_template: Path,
    monkeypatch: pytest.MonkeyPatch,
    license_feature: str,
    license_keys: set[str],
) -> None:
    flags = []
    for name in ts.load_manifest(real_template).features:
        flags += ["--feature", f"{name}={'true' if name == license_feature else 'false'}"]
    ask = mock.Mock(return_value="")
    monkeypatch.setattr("sys.stdin", mock.Mock(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", ask)
    code, _out, err = run(
        "--template", real_template, "new", real_template.parent / f"asks-{license_feature}",
        "--set", "project=my-widget", "--set", "github_user=octo-dev", *flags,
    )  # fmt: skip
    assert code == 0, err
    asked = {c.args[0].split(" ", 1)[0] for c in ask.call_args_list}
    assert asked == {"python", *license_keys}


@pytest.mark.integration
@pytest.mark.parametrize("python", [None, "3.12", "all-supported"])
def test_real_template_python_versions(real_template: Path, python: str | None) -> None:
    project = real_template.parent / f"python-{python}"
    extra = ("--set", f"python={python}") if python else ()
    code, _out, err = run(
        "--template", real_template, "new", project, "-y", *PROJECT_VALUES, *extra
    )
    assert code == 0, err
    newest = ts.load_manifest(real_template).placeholder_values()["python"]
    supported = ts.load_manifest(real_template).tokens[newest].aliases["all-supported"]
    if python == "all-supported":
        dev, requires, badge = supported, f">={supported}", f"python-{supported}%2B-blue"
        matrix = [f"3.{minor}" for minor in range(int(supported[2:]), int(newest[2:]) + 1)]
    else:
        dev = python or newest
        requires, badge, matrix = f"=={dev}.*", f"python-{dev}-blue", [dev]
    pyproject = tomllib.loads((project / "pyproject.toml").read_text())
    assert pyproject["project"]["requires-python"] == requires
    assert pyproject["tool"]["mypy"]["python_version"] == dev
    assert (project / ".python-version").read_text() == f"{dev}\n"
    assert (
        f"python: {matrix}".replace("'", '"') in (project / ".github/workflows/ci.yml").read_text()
    )
    assert badge in (project / "README.md").read_text()
    # Any other version the project names is a spelling the manifest doesn't
    # tokenise, left at the template's own version.
    stray: dict[str, set[str]] = {}
    for path in project.rglob("*"):
        relative = path.relative_to(project)
        if path.is_file() and ".git" not in relative.parts:
            for version in PYTHON_VERSION.findall(path.read_text("utf-8", "replace")):
                if version not in matrix:
                    stray.setdefault(version, set()).add(relative.as_posix())
    assert stray == {}


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.live  # uv sync fetches the project's dependencies
@pytest.mark.parametrize("choice", ["defaults", "all-off", "all-on", "library"])
def test_real_template_new_project_passes_its_gates(
    real_template: Path, choice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = real_template.parent / f"gates-{choice}"
    extra = ["--set", "python=all-supported"] if choice == "library" else _features(choice)
    code, _out, err = run(
        "--template", real_template, "new", project, "-y", *PROJECT_VALUES, *extra
    )
    assert code == 0, err
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    uv = shutil.which("uv") or "uv"
    for args in (
        ["sync", "--all-groups", "--all-extras"],
        ["run", "ruff", "check", "."],
        ["run", "ruff", "format", "--check", "."],
        ["run", "mypy"],
        ["run", "vulture"],
        ["run", "pytest", "-m", "not live", "-q", "-p", "no:cacheprovider", *NEEDS_BASELINE],
    ):
        proc = subprocess.run(  # noqa: S603  (fixed argv list, no shell)
            [uv, *args], cwd=project, capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, f"uv {' '.join(args)}:\n{proc.stdout}\n{proc.stderr}"
