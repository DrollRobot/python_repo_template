"""Tests for scripts/template_sync.py.

Builds throwaway template and child git repositories under pytest's tmp_path.
Git identity and config are pinned through environment variables for each
test, so the user's own config (signing, default branch, ...) can't interfere,
and nothing here reads or writes it.
"""

from __future__ import annotations

import contextlib
import io
import json
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
    # Local-path submodule URLs are refused by default (CVE-2022-39253).
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "protocol.file.allow",
    "GIT_CONFIG_VALUE_0": "always",
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


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8", newline="\n")


def commit_all(root: Path, msg: str = "c") -> str:
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", msg)
    return sh(root, "rev-parse", "HEAD").strip()


def run(*argv: object) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ts.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def statuses(out: str) -> dict[str, str]:
    """Map each file in a printed plan to its status."""
    result = {}
    for line in out.splitlines():
        parts = line.split()
        if (
            line.startswith("  ")
            and len(parts) >= 2
            and not line.startswith("  note:")
            and not parts[0].startswith("submodule")
        ):
            result[parts[1]] = parts[0]
    return result


def tty(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> mock.Mock:
    """Pretend stdin is a terminal that gives ``answers``; return the input() mock."""
    ask = mock.Mock(side_effect=answers)
    monkeypatch.setattr("sys.stdin", mock.Mock(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", ask)
    return ask


# ---------------------------------------------------------------------------
# Fixture templates
# ---------------------------------------------------------------------------

MANIFEST = """\
manifest_format = 2
exclude = ["template.toml", "tool/**", "GUIDE.md"]

[tokens]
"demo-template" = "project"
"<HOLDER>" = "holder"

[paths]
"child/" = ""

[base]
managed = [".gitignore", ".gitmodules", "ci.yml"]
merge = ["Makefile", "child/GUIDE.md", "NOTES.md"]
seed = ["README.md", "LICENSE", "src/*.py", "demo-template.txt", "logo.png"]

[features.docs]
description = "docs site"
managed = ["docs.yml"]
seed = ["docs/index.md"]

[features.docs_extra]
requires = ["docs"]
merge = ["docs/extra.md"]
"""

# The same template in format 1, before [paths]: without child/GUIDE.md, the only
# file that needs the mapping or a negated marker.
MANIFEST_V1 = (
    MANIFEST.replace("manifest_format = 2", "manifest_format = 1")
    .replace('[paths]\n"child/" = ""\n\n', "")
    .replace('"child/GUIDE.md", ', "")
)

# An option the template gains after a project was created from it.
LINT_FEATURE = (
    '\n[features.lint]\ndescription = "lint config"\ndefault = false\nmanaged = ["lint.yml"]\n'
)

FILES = {
    ".gitignore": "out/\n",
    "ci.yml": "name: ci\n# <template-only>\nvalidate: true\n# </template-only>\nrun: make\n",
    "Makefile": (
        "all:\n\techo demo-template\n# <template-only>\ntemplate-x:\n\techo x\n# </template-only>\n"
    ),
    "NOTES.md": "# Notes\n\nline one\n\nline two\n\nline three\n",
    "README.md": "# demo-template\n\nSee `<template-only>` for how markers work.\n",
    "LICENSE": "Copyright <HOLDER>\n",
    "src/part.py": "print(1)\n",
    "demo-template.txt": "{}\n",
    "docs.yml": (
        'docs: true\n# <template-only feature="docs_extra">\nextra: true\n# </template-only>\n'
    ),
    "docs/index.md": "# docs\n",
    "docs/extra.md": "extra\n",
    "tool/sync.py": "print('not shipped')\n",
    # The template's own guide stays here; children get child/GUIDE.md in its place.
    "GUIDE.md": "# the template's guide\n",
    "child/GUIDE.md": (
        "# guide\n"
        '<!-- <template-only feature="docs"> -->\nsee docs\n<!-- </template-only> -->\n'
        '<!-- <template-only feature="!docs"> -->\nall here\n<!-- </template-only> -->\n'
    ),
}
LOGO = b"\x89PNG\0\0binary"

# A format-3 template: one project name in two spellings, a required handle,
# mutually exclusive licenses, and a block kept by either of two features.
MANIFEST_V3 = """\
manifest_format = 3
exclude = ["template.toml"]

[tokens]
"demo_template" = { key = "project", transform = "snake" }
"demo-template" = { key = "project", transform = "kebab" }
"Octocat" = { key = "github_user", required = true }
"<HOLDER>" = "holder"

[base]
managed = ["pyproject.toml", "README.md"]
seed = ["LICENSE", "pkg/demo_template/__init__.py"]

[features.license_mit]
description = "MIT license"
conflicts = ["license_apache"]

[features.license_apache]
default = false

[features.alpha]
default = false
managed = ["scripts/alpha.py"]

[features.beta]
default = false
managed = ["scripts/beta.py"]
"""

FILES_V3 = {
    "pyproject.toml": (
        '[project]\nname = "demo-template"\n\n[tool.lint]\npaths = [\n  "src",\n'
        '  # <template-only feature="alpha|beta">\n  "scripts",\n  # </template-only>\n]\n'
    ),
    "README.md": "# demo-template\n\nby Octocat: https://github.com/Octocat/demo-template\n",
    "LICENSE": (
        '<template-only feature="license_mit">\nMIT, <HOLDER>\n</template-only>\n'
        '<template-only feature="license_apache">\nApache-2.0, <HOLDER>\n</template-only>\n'
    ),
    "pkg/demo_template/__init__.py": 'NAME = "demo-template"\n',
    "scripts/alpha.py": "print('alpha')\n",
    "scripts/beta.py": "print('beta')\n",
}


class Fixture:
    """A template repo, and the path a child is created at, under one directory."""

    def __init__(
        self,
        base: Path,
        manifest: str = MANIFEST,
        files: dict[str, str] | None = None,
        name: str = "demo-template",
    ) -> None:
        self.dir = base
        self.template = base / name
        self.template.mkdir()
        sh(self.template, "init", "-q")
        write(self.template, "template.toml", manifest)
        for rel, text in (FILES if files is None else files).items():
            write(self.template, rel, text)
        if files is None:
            (self.template / "logo.png").write_bytes(LOGO)
        self.head = commit_all(self.template, "template")
        self.child = base / "widget"

    def new(self, *extra: object) -> tuple[int, str, str]:
        return run(
            "--template", self.template, "new", self.child, "-y", "--set", "holder=Jane", *extra
        )

    def check(self, *extra: object) -> tuple[int, str, str]:
        return run("--template", self.template, "check", self.child, *extra)

    def update(self, *extra: object) -> tuple[int, str, str]:
        return run("--template", self.template, "update", self.child, *extra)

    def validate(self) -> tuple[int, str, str]:
        return run("--template", self.template, "validate")

    def template_commit(self, msg: str = "template") -> str:
        self.head = commit_all(self.template, msg)
        return self.head

    def child_commit(self, msg: str = "child") -> str:
        return commit_all(self.child, msg)

    def pin(self) -> str:
        config = next(rel for rel in ts.CHILD_CONFIG_PATHS if (self.child / rel).is_file())
        commit: str = tomllib.loads((self.child / config).read_text())["template"]["commit"]
        return commit

    def read(self, rel: str) -> str:
        return (self.child / rel).read_text("utf-8")

    def exists(self, rel: str) -> bool:
        return (self.child / rel).exists()

    def edit_child(self, rel: str, text: str, *, commit: bool = True) -> None:
        write(self.child, rel, text)
        if commit:
            self.child_commit()

    def edit_template(self, rel: str, text: str) -> None:
        write(self.template, rel, text)
        self.template_commit()

    def set_manifest(self, text: str) -> None:
        write(self.template, "template.toml", text)
        self.template_commit("manifest")

    def add_lint_feature(self) -> None:
        write(self.template, "template.toml", MANIFEST + LINT_FEATURE)
        write(self.template, "lint.yml", "lint: true\n")
        self.template_commit("add lint")


@pytest.fixture
def fx(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path)


@pytest.fixture
def synced(fx: Fixture) -> Fixture:
    """The fixture template with a child created from it and committed."""
    code, _out, err = fx.new()
    assert code == 0, err
    fx.child_commit("initial")
    return fx


@pytest.fixture
def fx3(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path, MANIFEST_V3, FILES_V3)


def assert_rejected(fx: Fixture, fragment: str) -> None:
    code, _out, err = fx.validate()
    assert code == 2, err
    assert fragment in err


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/x/a.py", False),
        ("**/*.md", "a.md", True),
        ("**/*.md", "x/y/a.md", True),
        ("tool/**", "tool/a/b", True),
        ("tool/**", "tools/a", False),
        ("?.txt", "a.txt", True),
        ("?.txt", "ab.txt", False),
        ("a.b", "aXb", False),
    ],
)
def test_glob_to_regex(pattern: str, path: str, expected: bool) -> None:
    assert (ts.glob_to_regex(pattern).match(path) is not None) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "line",
    [
        "# <template-only>",
        "// <template-only>",
        "<!-- <template-only> -->",
        "  #  <template-only>  ",
        "/* <template-only> */",
        "; <template-only>",
        "<template-only>",
    ],
)
def test_marker_on_comment_syntaxes(line: str) -> None:
    assert ts.marker_on(line) == ("open", None)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('# <template-only feature="docs">', ("open", "docs")),
        ('# <template-only feature="!docs">', ("open", "!docs")),
        ('# <template-only feature="a|!b|c">', ("open", "a|!b|c")),
        ("<!-- </template-only> -->", ("close", None)),
        ("See `<template-only>` for markers", None),
        ("- wrap it in <template-only> markers", None),
        ("`# <template-only>`", None),
        ("# <template-only> extra", None),
        ("# <template-only feature=docs>", None),
        ('# <template-only feature="!!docs">', None),
        ('# <template-only feature="! docs">', None),
        ('# <template-only feature="a|">', None),
        ('# <template-only feature="|a">', None),
        ('# <template-only feature="a||b">', None),
    ],
)
def test_marker_on(line: str, expected: tuple[str, str | None] | None) -> None:
    assert ts.marker_on(line) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("feature", "enabled", "kept"),
    [
        (None, set(), False),
        ("a", {"a"}, True),
        ("a", set(), False),
        ("!a", set(), True),
        ("!a", {"a"}, False),
        ("a|b", {"b"}, True),
        ("a|b", set(), False),
        ("!a|b", set(), True),
        ("!a|b", {"a"}, False),
        ("!a|b", {"a", "b"}, True),
    ],
)
def test_block_kept(feature: str | None, enabled: set[str], kept: bool) -> None:
    assert ts.block_kept(feature, frozenset(enabled)) is kept


@pytest.mark.unit
def test_apply_markers() -> None:
    text = (
        "a\n# <template-only>\nb\n# </template-only>\nc\n"
        '# <template-only feature="x">\nd\n# </template-only>\n'
    )
    assert ts.apply_markers(text, frozenset(), where="t")[0] == "a\nc\n"
    assert ts.apply_markers(text, frozenset({"x"}), where="t")[0] == "a\nc\nd\n"
    assert ts.apply_markers(text, None, where="t") == (text, {"x"})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "enabled", "expected"),
    [
        # A dropped block between blank lines leaves one blank line, not two; at
        # the top of a file it leaves none. A kept block keeps its spacing.
        ("a\n\n# <template-only>\nb\n# </template-only>\n\nc\n", set(), "a\n\nc\n"),
        ("# <template-only>\nb\n# </template-only>\n\nc\n", set(), "c\n"),
        ("a\n# <template-only>\nb\n# </template-only>\n\nc\n", set(), "a\n\nc\n"),
        (
            'a\n\n# <template-only feature="x">\nb\n# </template-only>\n\nc\n',
            {"x"},
            "a\n\nb\n\nc\n",
        ),
        # A negated block is kept exactly when the feature is declined.
        ('a\n# <template-only feature="!x">\nb\n# </template-only>\nc\n', set(), "a\nb\nc\n"),
        ('a\n# <template-only feature="!x">\nb\n# </template-only>\nc\n', {"x"}, "a\nc\n"),
        # An any-of block is kept when either feature is on. Indented markers count.
        (
            '[\n  # <template-only feature="x|y">\n  "s",\n  # </template-only>\n]\n',
            {"y"},
            '[\n  "s",\n]\n',
        ),
        (
            '[\n  # <template-only feature="x|y">\n  "s",\n  # </template-only>\n]\n',
            set(),
            "[\n]\n",
        ),
    ],
)
def test_apply_markers_blocks(text: str, enabled: set[str], expected: str) -> None:
    assert ts.apply_markers(text, frozenset(enabled), where="t")[0] == expected


@pytest.mark.unit
def test_apply_markers_reports_tags() -> None:
    text = (
        '# <template-only feature="!x">\nb\n# </template-only>\n'
        '# <template-only feature="y|z">\n# </template-only>\n'
    )
    assert ts.apply_markers(text, None, where="t") == (text, {"!x", "y|z"})


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        "# <template-only>\nx\n",
        "x\n# </template-only>\n",
        "# <template-only>\n# <template-only>\n# </template-only>\n",
    ],
)
def test_apply_markers_unbalanced(bad: str) -> None:
    with pytest.raises(ts.ConfigError):
        ts.apply_markers(bad, frozenset(), where="t")


@pytest.mark.unit
def test_substitute() -> None:
    assert ts.substitute("abc ab", {"ab": "X", "abc": "Y"}) == "Y X"
    # Single pass: a replacement is never itself re-substituted.
    assert ts.substitute("foo", {"foo": "bar", "bar": "baz"}) == "bar"
    assert ts.substitute("plain", {}) == "plain"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "transform", "expected"),
    [
        ("My Widget", "snake", "my_widget"),
        ("My Widget", "kebab", "my-widget"),
        ("My Widget", "upper", "MY_WIDGET"),
        ("my-widget", "snake", "my_widget"),
        ("my_widget", "kebab", "my-widget"),
        ("  spaced  out ", "kebab", "spaced-out"),
        ("As Typed", None, "As Typed"),
    ],
)
def test_transform_value(value: str, transform: str | None, expected: str) -> None:
    assert ts.transform_value(value, transform) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "transform"), [("9lives", "snake"), ("", "kebab"), ("a.b", "snake")]
)
def test_transform_value_rejects_invalid_names(value: str, transform: str) -> None:
    with pytest.raises(ValueError, match=f"{transform} name"):
        ts.transform_value(value, transform)


@pytest.mark.unit
def test_with_feature_choices() -> None:
    features = {
        "docs": ts.Feature("docs", "docs site", False, ()),
        "lint": ts.Feature("lint", "", True, ()),
    }
    # A config written when the template had no features: the placeholder comment goes.
    placeholder = (
        f'[values]\nproject = "x"\n\n[features]\n{ts.NO_FEATURES_COMMENT}\n\n'
        '[overrides]\n"a" = "ignore"\n'
    )
    out = ts.with_feature_choices(placeholder, features, {"docs": False})
    assert ts.NO_FEATURES_COMMENT not in out
    assert "# docs site\ndocs = false\n\n[overrides]\n" in out
    assert tomllib.loads(out) == {
        "values": {"project": "x"},
        "features": {"docs": False},
        "overrides": {"a": "ignore"},
    }
    # Appended after existing choices, or in a new table if there is none.
    out = ts.with_feature_choices("[features]\ndocs = true\n", features, {"lint": True})
    assert out == "[features]\ndocs = true\nlint = true\n"
    out = ts.with_feature_choices('[values]\nproject = "x"\n', features, {"docs": True})
    assert tomllib.loads(out)["features"] == {"docs": True}


def _decide(
    strategy: str, base: bytes | None, theirs: bytes | None, mine: bytes | None
) -> ts.FileResult:
    return ts.decide(ts.FileResult("p", "p", strategy), base, theirs, mine, ("a", "b", "c"))


@pytest.mark.unit
def test_decide_managed() -> None:
    assert _decide("managed", b"1", b"1", b"1").status == "ok"
    r = _decide("managed", b"1", b"2", b"1")
    assert (r.status, r.change, r.content) == ("update", True, b"2")
    r = _decide("managed", b"1", b"1", b"9")
    assert (r.status, r.error, r.content) == ("drift", True, b"1")
    assert "also changed" in _decide("managed", b"1", b"2", b"9").note
    assert _decide("managed", None, b"2", None).status == "new"
    r = _decide("managed", b"1", b"2", None)
    assert (r.status, r.error, r.content) == ("drift", True, b"2")
    assert _decide("managed", b"1", None, b"1").status == "delete"
    assert _decide("managed", b"1", None, b"9").status == "drift"
    assert _decide("managed", b"1", None, None).status == "ok"


@pytest.mark.unit
def test_decide_merge() -> None:
    assert _decide("merge", b"1\n", b"1\n", b"9\n").status == "local"
    assert _decide("merge", b"1\n", b"2\n", b"1\n").status == "update"
    assert _decide("merge", b"1\n", b"2\n", b"2\n").status == "ok"  # child already took it
    assert _decide("merge", b"1\n", None, b"9\n").status == "removed-upstream"
    assert _decide("merge", b"1\n", b"2\n", None).status == "local"
    assert _decide("merge", None, b"2\n", b"9\n").status == "local"
    base = b"a\n\nb\n\nc\n"
    r = _decide("merge", base, b"a\n\nb\n\nC\n", b"A\n\nb\n\nc\n")
    assert (r.status, r.content) == ("merged", b"A\n\nb\n\nC\n")
    r = _decide("merge", base, b"a\n\nB\n\nc\n", b"a\n\nX\n\nc\n")
    assert (r.status, r.error) == ("conflict", True)
    assert r.content is not None
    assert b"<<<<<<< a" in r.content
    r = _decide("merge", b"1\n", b"2\n", b"<<<<<<< a\n1\n=======\n2\n>>>>>>> c\n")
    assert r.status == "markers"
    r = _decide("merge", b"1\0", b"2\0", b"3\0")
    assert (r.status, r.error) == ("conflict", True)


@pytest.mark.unit
def test_decide_seed() -> None:
    assert _decide("seed", None, b"1", None).status == "seed-available"
    assert _decide("seed", b"1", b"2", None).status == "ok"
    assert _decide("seed", b"1", b"2", b"9").status == "ok"


_FEATURES_V3 = """\
manifest_format = 3
[features.mit]
conflicts = ["apache", "gnu"]
[features.apache]
default = false
[features.gnu]
default = false
[features.docs]
[features.docs_extra]
requires = ["docs"]
"""


@pytest.mark.unit
def test_conflicts_are_symmetric() -> None:
    manifest = ts.parse_manifest(_FEATURES_V3, Path("."), where="t")
    assert manifest.features["mit"].conflicts == ("apache", "gnu")
    assert manifest.features["apache"].conflicts == ("mit",)
    assert manifest.features["docs"].conflicts == ()


@pytest.mark.unit
def test_effective_features_explicit_choice_beats_conflicting_default() -> None:
    manifest = ts.parse_manifest(_FEATURES_V3, Path("."), where="t")
    on, notes = ts.effective_features(manifest, {"apache": True})
    assert on == {"apache", "docs", "docs_extra"}
    assert notes == ["feature mit turned off: it conflicts with apache"]
    on, notes = ts.effective_features(manifest, {"docs": False})
    assert on == {"mit"}
    assert notes == ["feature docs_extra turned off: it requires docs"]


@pytest.mark.unit
def test_effective_features_rejects_two_conflicting_choices() -> None:
    manifest = ts.parse_manifest(_FEATURES_V3, Path("."), where="t")
    with pytest.raises(ts.ConfigError, match="both on: apache and mit"):
        ts.effective_features(manifest, {"apache": True, "mit": True})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "message"),
    [
        (_FEATURES_V3.replace('"apache", "gnu"', '"nope"'), "conflicts with 'nope'"),
        (_FEATURES_V3.replace('"apache", "gnu"', '"mit"'), "conflicts with 'mit'"),
        (_FEATURES_V3.replace("manifest_format = 3", "manifest_format = 2"), "conflicts needs"),
        (
            'manifest_format = 3\n[tokens]\n"x" = { key = "k", transform = "camel" }\n',
            "transform must",
        ),
        ('manifest_format = 3\n[tokens]\n"x" = { key = "k", required = "yes" }\n', "required must"),
        ('manifest_format = 3\n[tokens]\n"x" = { key = "k", extra = 1 }\n', "unknown key(s) extra"),
        ('manifest_format = 3\n[tokens]\n"x" = { transform = "snake" }\n', "key must"),
        ('manifest_format = 3\n[tokens]\n"x" = 1\n', "a key name or a table"),
        ('manifest_format = 2\n[tokens]\n"x" = { key = "k" }\n', "tables need manifest_format = 3"),
    ],
)
def test_parse_manifest_rejects_format_3_mistakes(text: str, message: str) -> None:
    with pytest.raises(ts.ConfigError) as excinfo:
        ts.parse_manifest(text, Path("."), where="t")
    assert message in str(excinfo.value)


@pytest.mark.unit
def test_replacements_apply_transforms() -> None:
    manifest = ts.parse_manifest(MANIFEST_V3, Path("."), where="t")
    values = {"project": "My Widget", "github_user": "jane", "holder": "Jane Doe"}
    assert manifest.replacements(values) == {
        "demo_template": "my_widget",
        "demo-template": "my-widget",
        "Octocat": "jane",
        "<HOLDER>": "Jane Doe",
    }
    assert manifest.required_keys == {"github_user"}
    with pytest.raises(ts.ConfigError, match=r"\[values\] project: '1st' gives the snake name"):
        manifest.replacements({**values, "project": "1st"})


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_validate_ok(fx: Fixture) -> None:
    code, out, err = fx.validate()
    assert code == 0, err
    assert "classified" in out
    assert "features: docs (default on), docs_extra (default on)" in out


@pytest.mark.integration
def test_unclassified_file(fx: Fixture) -> None:
    write(fx.template, "stray.txt", "x\n")
    fx.template_commit()
    assert_rejected(fx, "stray.txt: not classified")


@pytest.mark.integration
def test_ambiguous_file(fx: Fixture) -> None:
    fx.set_manifest(MANIFEST.replace('"ci.yml"]', '"ci.yml", "*.yml"]'))
    assert_rejected(fx, "several rules")


@pytest.mark.integration
def test_merge_binary(fx: Fixture) -> None:
    fx.set_manifest(
        MANIFEST.replace(', "logo.png"]', "]").replace('"NOTES.md"]', '"NOTES.md", "logo.png"]')
    )
    assert_rejected(fx, "binary files cannot be `merge`")


@pytest.mark.integration
def test_unbalanced_marker(fx: Fixture) -> None:
    write(fx.template, "ci.yml", "# <template-only>\nx\n")
    fx.template_commit()
    assert_rejected(fx, "never closed")


@pytest.mark.integration
@pytest.mark.parametrize("tag", ["nope", "!nope", "docs|nope"])
def test_undeclared_feature_marker(fx: Fixture, tag: str) -> None:
    fx.set_manifest(MANIFEST.replace("manifest_format = 2", "manifest_format = 3"))
    write(fx.template, "ci.yml", f'# <template-only feature="{tag}">\nx\n# </template-only>\n')
    fx.template_commit()
    assert_rejected(fx, f'feature="{tag}"> names an undeclared feature: nope')


@pytest.mark.integration
def test_path_clash(fx: Fixture) -> None:
    # Ship the template's own GUIDE.md too, and it collides with child/GUIDE.md.
    fx.set_manifest(
        MANIFEST.replace(', "GUIDE.md"]', "]").replace('"ci.yml"]', '"ci.yml", "GUIDE.md"]')
    )
    assert_rejected(fx, "GUIDE.md: shipped from several template files: GUIDE.md, child/GUIDE.md")


@pytest.mark.integration
@pytest.mark.parametrize(
    "entry",
    ['"child" = ""', '"../child/" = ""', '"./child/" = ""', '"child/" = "/abs/"', '"child/" = 1'],
)
def test_bad_paths(fx: Fixture, entry: str) -> None:
    fx.set_manifest(MANIFEST.replace('"child/" = ""', entry))
    assert_rejected(fx, "[paths]")


@pytest.mark.integration
def test_stale_paths_warns(fx: Fixture) -> None:
    fx.set_manifest(MANIFEST.replace('"child/" = ""', '"child/" = ""\n"nowhere/" = "x/"'))
    code, out, err = fx.validate()
    assert code == 0, err
    assert "[paths] 'nowhere/' matches no shipped file" in out


@pytest.mark.integration
def test_requires_unknown(fx: Fixture) -> None:
    fx.set_manifest(MANIFEST.replace('requires = ["docs"]', 'requires = ["nope"]'))
    assert_rejected(fx, "unknown feature")


@pytest.mark.integration
def test_requires_cycle(fx: Fixture) -> None:
    fx.set_manifest(
        MANIFEST.replace(
            'description = "docs site"\n', 'description = "docs site"\nrequires = ["docs_extra"]\n'
        )
    )
    assert_rejected(fx, "cycle")


@pytest.mark.integration
def test_unknown_key(fx: Fixture) -> None:
    fx.set_manifest(MANIFEST + "\n[bogus]\nx = 1\n")
    assert_rejected(fx, "unknown key")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("4", "manifest_format = 4, but this tool reads formats up to 3"),
        ("0", "positive integer"),
        ("true", "positive integer"),
        ('"2"', "positive integer"),
    ],
)
def test_bad_format(fx: Fixture, value: str, message: str) -> None:
    fx.set_manifest(MANIFEST.replace("manifest_format = 2", f"manifest_format = {value}"))
    assert_rejected(fx, message)


@pytest.mark.integration
def test_newer_format_is_refused_before_its_keys(fx: Fixture) -> None:
    # A format-4 manifest with a key this tool has never heard of: the number is what's reported.
    fx.set_manifest(
        MANIFEST.replace("manifest_format = 2", "manifest_format = 4") + "\n[future]\nx = 1\n"
    )
    code, _out, err = fx.validate()
    assert code == 2, err
    assert "reads formats up to 3" in err
    assert "unknown key" not in err


@pytest.mark.integration
def test_format_1_manifest(fx: Fixture) -> None:
    write(fx.template, "template.toml", MANIFEST_V1)
    sh(fx.template, "rm", "-q", "child/GUIDE.md")
    fx.template_commit("format 1")
    code, _out, err = fx.validate()
    assert code == 0, err
    # What format 2 added is refused under format 1.
    fx.set_manifest(MANIFEST_V1 + '\n[paths]\n"child/" = ""\n')
    assert_rejected(fx, "[paths] needs manifest_format = 2")
    fx.set_manifest(MANIFEST_V1)
    write(fx.template, "ci.yml", '# <template-only feature="!docs">\nx\n# </template-only>\n')
    fx.template_commit()
    assert_rejected(fx, 'ci.yml: negated markers (feature="!name") need manifest_format = 2')


@pytest.mark.integration
def test_any_of_markers_need_format_3(fx: Fixture) -> None:
    write(
        fx.template,
        "ci.yml",
        '# <template-only feature="docs|docs_extra">\nx\n# </template-only>\n',
    )
    fx.template_commit()
    assert_rejected(fx, 'ci.yml: any-of markers (feature="a|b") need manifest_format = 3')
    fx.set_manifest(MANIFEST.replace("manifest_format = 2", "manifest_format = 3"))
    code, _out, err = fx.validate()
    assert code == 0, err


@pytest.mark.integration
def test_stale_pattern_warns(fx: Fixture) -> None:
    fx.set_manifest(MANIFEST.replace('"ci.yml"]', '"ci.yml", "nothing.txt"]'))
    code, out, err = fx.validate()
    assert code == 0, err
    assert "'nothing.txt' matches no tracked file" in out


@pytest.mark.integration
def test_check_on_template_itself(fx: Fixture) -> None:
    code, _out, err = run("--template", fx.template, "check", fx.template)
    assert code == 2
    assert "template itself" in err


@pytest.mark.integration
def test_validate_format_3(fx3: Fixture) -> None:
    code, out, err = fx3.validate()
    assert code == 0, err
    assert "license_mit (default on), license_apache (default off)" in out


@pytest.mark.integration
def test_validate_rejects_inconsistent_token_spellings(fx3: Fixture) -> None:
    fx3.set_manifest(MANIFEST_V3.replace('"demo-template" = {', '"Demo-Template" = {'))
    assert_rejected(fx3, "[tokens] 'Demo-Template': rendering the template's own project value")


# ---------------------------------------------------------------------------
# new
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_new_layout(synced: Fixture) -> None:
    fx = synced
    for rel in (
        ".gitignore", "ci.yml", "Makefile", "NOTES.md", "README.md", "LICENSE", "src/part.py",
        "widget.txt", "docs.yml", "docs/index.md", "docs/extra.md", "logo.png", ".template.toml",
        "GUIDE.md",
    ):  # fmt: skip
        assert fx.exists(rel), rel
    for rel in ("template.toml", "tool/sync.py", "tool", "demo-template.txt", "child"):
        assert not fx.exists(rel), rel
    assert fx.read("GUIDE.md") == "# guide\nsee docs\n"
    assert fx.read("ci.yml") == "name: ci\nrun: make\n"
    assert fx.read("Makefile") == "all:\n\techo widget\n"
    assert fx.read("README.md") == "# widget\n\nSee `<template-only>` for how markers work.\n"
    assert fx.read("LICENSE") == "Copyright Jane\n"
    assert fx.read("docs.yml") == "docs: true\nextra: true\n"
    assert (fx.child / "logo.png").read_bytes() == LOGO
    cfg = tomllib.loads(fx.read(".template.toml"))
    assert cfg["template"]["commit"] == fx.head
    assert cfg["values"] == {"project": "widget", "holder": "Jane"}
    assert cfg["features"] == {"docs": True, "docs_extra": True}
    assert sh(fx.child, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


@pytest.mark.integration
def test_new_stages_everything(fx: Fixture) -> None:
    code, out, err = fx.new()
    assert code == 0, err
    assert "nothing is committed" in out
    staged = sh(fx.child, "diff", "--cached", "--name-only").split()
    assert ".template.toml" in staged
    assert "widget.txt" in staged
    assert sh(fx.child, "rev-list", "--all").strip() == ""


@pytest.mark.integration
def test_new_refuses_nonempty_target(synced: Fixture) -> None:
    code, _out, err = synced.new()
    assert code == 2
    assert "not an empty directory" in err


@pytest.mark.integration
def test_new_into_empty_git_repo(fx: Fixture) -> None:
    # As left by cloning a repository just created, empty, on GitHub.
    fx.child.mkdir()
    sh(fx.child, "init", "-q")
    sh(fx.child, "remote", "add", "origin", "https://example.invalid/widget.git")
    code, _out, err = fx.new()
    assert code == 0, err
    assert fx.read("LICENSE") == "Copyright Jane\n"
    assert (
        sh(fx.child, "remote", "get-url", "origin").strip() == "https://example.invalid/widget.git"
    )


@pytest.mark.integration
def test_new_refuses_repo_with_history(fx: Fixture) -> None:
    fx.child.mkdir()
    sh(fx.child, "init", "-q")
    sh(fx.child, "commit", "-q", "--allow-empty", "-m", "first")
    code, _out, err = fx.new()
    assert code == 2
    assert "not an empty directory" in err


@pytest.mark.integration
def test_new_feature_off_and_defaults(fx: Fixture) -> None:
    other = fx.dir / "other"
    code, out, err = run("--template", fx.template, "new", other, "-y", "--feature", "docs=false")
    assert code == 0, err
    assert "feature docs_extra turned off" in out
    for rel in ("docs.yml", "docs/index.md", "docs/extra.md"):
        assert not (other / rel).exists(), rel
    assert (other / "GUIDE.md").read_text() == "# guide\nall here\n"
    cfg = tomllib.loads((other / ".template.toml").read_text())
    assert cfg["features"] == {"docs": False, "docs_extra": False}
    # Unset values: project defaults to the directory name, anything else to the placeholder.
    assert cfg["values"] == {"project": "other", "holder": "<HOLDER>"}
    assert (other / "LICENSE").read_text() == "Copyright <HOLDER>\n"


@pytest.mark.integration
def test_new_prompts_for_features(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    other = fx.dir / "other"
    # docs: Enter takes the default (on); docs_extra: an unclear answer is asked again.
    ask = tty(monkeypatch, ["", "maybe", "n"])
    code, _out, err = run(
        "--template", fx.template, "new", other, "--set", "project=other", "--set", "holder=Jane"
    )
    assert code == 0, err
    assert ask.call_count == 3
    assert ask.call_args_list[0].args[0].startswith("docs (docs site) [Y/n]")
    cfg = tomllib.loads((other / ".template.toml").read_text())
    assert cfg["features"] == {"docs": True, "docs_extra": False}


@pytest.mark.integration
def test_new_skips_a_feature_whose_requirement_is_declined(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = fx.dir / "other"
    # docs: no. docs_extra requires docs, so it is never asked.
    ask = tty(monkeypatch, ["n"])
    code, _out, err = run(
        "--template", fx.template, "new", other, "--set", "project=other", "--set", "holder=Jane"
    )
    assert code == 0, err
    assert ask.call_count == 1
    cfg = tomllib.loads((other / ".template.toml").read_text())
    assert cfg["features"] == {"docs": False, "docs_extra": False}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("answers", "asked", "chosen"),
    [
        # Declining config skips secrets, which requires it, and keyring, which
        # requires secrets.
        (["n", ""], ["config", "docs"], {"config": False, "secrets": False, "keyring": False}),
        (["", "", "", ""], ["config", "secrets", "keyring", "docs"], {}),
    ],
)
def test_resolve_features_asks_requirements_first_and_skips_their_dependents(
    monkeypatch: pytest.MonkeyPatch, answers: list[str], asked: list[str], chosen: dict[str, bool]
) -> None:
    # Declared dependents first, so the order asked in can't be declaration order.
    text = (
        'manifest_format = 3\n[features.keyring]\nrequires = ["secrets"]\n'
        '[features.secrets]\nrequires = ["config"]\n[features.config]\n[features.docs]\n'
    )
    manifest = ts.parse_manifest(text, Path("."), where="t")
    ask = tty(monkeypatch, answers)
    result = ts.resolve_features(manifest, {}, yes=False)
    assert [c.args[0].split(" ", 1)[0] for c in ask.call_args_list] == asked
    assert result == {name: chosen.get(name, True) for name in manifest.features}


@pytest.mark.integration
def test_new_refuses_dirty_template(fx: Fixture) -> None:
    write(fx.template, "ci.yml", "changed\n")
    code, _out, err = run("--template", fx.template, "new", fx.dir / "other", "-y")
    assert code == 2
    assert "uncommitted changes" in err


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="no executable bit on Windows")
def test_new_takes_executable_bit_from_the_index(fx: Fixture) -> None:
    (fx.template / "ci.yml").chmod(0o755)
    fx.template_commit("executable")
    assert sh(fx.template, "ls-files", "-s", "ci.yml").startswith("100755")
    code, _out, err = fx.new()
    assert code == 0, err
    assert (fx.child / "ci.yml").stat().st_mode & 0o100
    assert not (fx.child / "NOTES.md").stat().st_mode & 0o100


@pytest.mark.integration
def test_new_format_3(fx3: Fixture) -> None:
    child = fx3.dir / "widget"
    code, out, err = run(
        "--template", fx3.template, "new", child, "-y",
        "--set", "project=My Widget", "--set", "github_user=jane", "--set", "holder=Jane Doe",
    )  # fmt: skip
    assert code == 0, err
    assert (child / "pkg/my_widget/__init__.py").read_text() == 'NAME = "my-widget"\n'
    assert not (child / "pkg/demo_template").exists()
    assert (child / "README.md").read_text() == (
        "# my-widget\n\nby jane: https://github.com/jane/my-widget\n"
    )
    assert (child / "LICENSE").read_text() == "MIT, Jane Doe\n"
    # Neither alpha nor beta: the any-of block is gone.
    assert '"scripts"' not in (child / "pyproject.toml").read_text()
    cfg = tomllib.loads((child / ".template.toml").read_text())
    assert cfg["values"] == {"github_user": "jane", "holder": "Jane Doe", "project": "My Widget"}
    assert cfg["features"] == {
        "license_mit": True, "license_apache": False, "alpha": False, "beta": False
    }  # fmt: skip
    assert "features on: license_mit" in out


@pytest.mark.integration
def test_new_required_value_has_no_default(fx3: Fixture) -> None:
    code, _out, err = run("--template", fx3.template, "new", fx3.dir / "w", "-y")
    assert code == 2
    assert "github_user has no default: pass --set github_user=VALUE" in err
    assert not (fx3.dir / "w").exists()
    code, _out, err = run(
        "--template", fx3.template, "new", fx3.dir / "w", "-y", "--set", "github_user="
    )
    assert code == 2
    assert "github_user needs a non-empty value" in err


@pytest.mark.integration
def test_new_rejects_a_value_its_transform_cannot_spell(fx3: Fixture) -> None:
    code, _out, err = run(
        "--template", fx3.template, "new", fx3.dir / "w", "-y",
        "--set", "github_user=jane", "--set", "project=1st",
    )  # fmt: skip
    assert code == 2
    assert "project: '1st' gives the snake name '1st'" in err
    assert not (fx3.dir / "w").exists()


@pytest.mark.integration
def test_new_prompts_format_3(fx3: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    child = fx3.dir / "widget"
    # Features first. license_mit: Enter keeps it, so license_apache is never
    # asked. alpha no, beta yes. Then values: github_user is required, so Enter
    # asks again; holder and project take defaults.
    ask = tty(monkeypatch, ["", "n", "y", "", "jane", "", ""])
    code, _out, err = run("--template", fx3.template, "new", child)
    assert code == 0, err
    prompts = [c.args[0] for c in ask.call_args_list]
    assert prompts[0].startswith("license_mit (MIT license) [Y/n]")
    assert not [p for p in prompts if p.startswith("license_apache")]
    assert prompts[1].startswith("alpha [y/N]")
    assert prompts[3:5] == ["github_user (required): ", "github_user (required): "]
    assert '"scripts"' in (child / "pyproject.toml").read_text()
    cfg = tomllib.loads((child / ".template.toml").read_text())
    assert cfg["values"]["project"] == "widget"
    assert cfg["features"] == {
        "license_mit": True, "license_apache": False, "alpha": False, "beta": True
    }  # fmt: skip


@pytest.mark.integration
@pytest.mark.parametrize(
    ("answers", "company"),
    [
        # Features, then values (company, holder). license_mit kept, alpha and
        # beta declined: no file names <COMPANY>.
        (["", "", "", "Jane"], None),
        # alpha ships a file that names it.
        (["", "y", "", "Acme", "Jane"], "Acme"),
        # license_apache's block of LICENSE names it.
        (["n", "y", "", "", "Acme", "Jane"], "Acme"),
    ],
)
def test_new_asks_only_for_values_its_files_use(
    fx3: Fixture, monkeypatch: pytest.MonkeyPatch, answers: list[str], company: str | None
) -> None:
    holder = '"<HOLDER>" = "holder"\n'
    manifest = MANIFEST_V3.replace(holder, f'{holder}"<COMPANY>" = "company"\n')
    apache = "Apache-2.0, <HOLDER>"
    write(fx3.template, "template.toml", manifest)
    write(fx3.template, "LICENSE", FILES_V3["LICENSE"].replace(apache, f"{apache}, <COMPANY>"))
    write(fx3.template, "scripts/alpha.py", "print('<COMPANY>')\n")
    fx3.template_commit()
    ask = tty(monkeypatch, answers)
    code, _out, err = run(
        "--template", fx3.template, "new", fx3.child,
        "--set", "github_user=jane", "--set", "project=widget",
    )  # fmt: skip
    assert code == 0, err
    assert ask.call_count == len(answers)
    asked = [c.args[0] for c in ask.call_args_list]
    assert any(p.startswith("company") for p in asked) is (company is not None)
    # An unasked value keeps its placeholder, as Enter at its prompt would.
    values = tomllib.loads(fx3.read(".template.toml"))["values"]
    assert values["company"] == (company or "<COMPANY>")


@pytest.mark.unit
def test_resolve_values_asks_for_a_required_key_even_when_unused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = (
        'manifest_format = 3\n[tokens]\n"Octocat" = { key = "github_user", required = true }\n'
        '"<HOLDER>" = "holder"\n'
    )
    manifest = ts.parse_manifest(text, Path("."), where="t")
    ask = tty(monkeypatch, ["jane"])
    values = ts.resolve_values(manifest, {}, Path("widget"), yes=False, ask=set())
    assert ask.call_args_list == [mock.call("github_user (required): ")]
    assert values == {"github_user": "jane", "holder": "<HOLDER>"}


@pytest.mark.integration
def test_new_declining_the_default_license_asks_the_next(
    fx3: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = fx3.dir / "widget"
    ask = tty(monkeypatch, ["n", "y", "", ""])
    code, _out, err = run(
        "--template", fx3.template, "new", child, "--set", "github_user=jane",
        "--set", "project=widget", "--set", "holder=Jane",
    )  # fmt: skip
    assert code == 0, err
    assert ask.call_args_list[1].args[0].startswith("license_apache [y/N]")
    assert (child / "LICENSE").read_text() == "Apache-2.0, Jane\n"


@pytest.mark.integration
def test_new_explicit_feature_turns_off_conflicting_default(fx3: Fixture) -> None:
    child = fx3.dir / "widget"
    code, out, err = run(
        "--template", fx3.template, "new", child, "-y", "--set", "github_user=jane",
        "--feature", "license_apache=true",
    )  # fmt: skip
    assert code == 0, err
    assert "feature license_mit turned off: it conflicts with license_apache" in out
    assert (child / "LICENSE").read_text() == "Apache-2.0, <HOLDER>\n"
    assert tomllib.loads((child / ".template.toml").read_text())["features"]["license_mit"] is False


@pytest.mark.integration
def test_new_refuses_two_conflicting_features(fx3: Fixture) -> None:
    code, _out, err = run(
        "--template", fx3.template, "new", fx3.dir / "w", "-y", "--set", "github_user=jane",
        "--feature", "license_apache=true", "--feature", "license_mit=true",
    )  # fmt: skip
    assert code == 2
    assert "conflicting features are both on: license_apache and license_mit" in err


@pytest.mark.integration
@pytest.mark.parametrize(
    ("flags", "kept"), [((), False), (("alpha",), True), (("alpha", "beta"), True)]
)
def test_new_any_of_block(fx3: Fixture, flags: tuple[str, ...], kept: bool) -> None:
    child = fx3.dir / "widget"
    features = [arg for name in flags for arg in ("--feature", f"{name}=true")]
    code, _out, err = run(
        "--template", fx3.template, "new", child, "-y", "--set", "github_user=jane", *features
    )
    assert code == 0, err
    assert ('"scripts"' in (child / "pyproject.toml").read_text()) is kept


# ---------------------------------------------------------------------------
# check / update
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_check_clean(synced: Fixture) -> None:
    code, out, err = synced.check()
    assert code == 0, err
    assert "child pin is current" in out
    assert "0 pending change(s); 0 error(s)" in out


@pytest.mark.integration
def test_check_reads_crlf_child_files_as_lf(synced: Fixture) -> None:
    # A Windows checkout with core.autocrlf: line endings alone are not drift.
    (synced.child / "ci.yml").write_bytes(synced.read("ci.yml").replace("\n", "\r\n").encode())
    code, out, err = synced.check()
    assert code == 0, err
    assert statuses(out) == {}


@pytest.mark.integration
def test_update_managed_and_merge(synced: Fixture) -> None:
    fx = synced
    fx.edit_template(".gitignore", "out/\n*.log\n")
    fx.edit_template("NOTES.md", "# Notes\n\nline one\n\nline two\n\nline 3\n")
    code, out, err = fx.check()
    assert code == 0, err
    assert statuses(out) == {".gitignore": "update", "NOTES.md": "update"}
    assert "2 commit(s) behind" in out
    assert fx.check("--strict")[0] == 1

    code, out, err = fx.update()
    assert code == 0, err
    assert fx.read(".gitignore") == "out/\n*.log\n"
    assert "line 3" in fx.read("NOTES.md")
    assert fx.pin() == fx.head
    assert f"-> {fx.head[:7]}" in out
    fx.child_commit()
    code, out, err = fx.check()
    assert code == 0, err
    assert "child pin is current" in out


@pytest.mark.integration
def test_update_records_new_feature(synced: Fixture) -> None:
    fx = synced
    fx.add_lint_feature()
    code, out, err = fx.check()
    assert code == 0, err
    assert "[features] lint is unset, so its default (off) applies" in out
    code, out, err = fx.update()  # not a terminal: takes the default
    assert code == 0, err
    assert "recorded in .template.toml: lint = false" in out
    assert tomllib.loads(fx.read(".template.toml"))["features"]["lint"] is False
    assert not fx.exists("lint.yml")
    fx.child_commit()
    code, out, err = fx.check()
    assert code == 0, err
    assert "lint is unset" not in out


@pytest.mark.integration
def test_update_asks_about_new_feature(synced: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = synced
    fx.add_lint_feature()
    ask = tty(monkeypatch, ["y"])
    code, out, err = fx.update()
    assert code == 0, err
    assert ask.call_count == 1
    assert ask.call_args.args[0].startswith("lint (lint config) [y/N]")
    assert "hasn't chosen yet" in out
    assert tomllib.loads(fx.read(".template.toml"))["features"]["lint"] is True
    assert fx.read("lint.yml") == "lint: true\n"


@pytest.mark.integration
def test_update_records_new_feature_off_when_it_conflicts(fx3: Fixture) -> None:
    fx3.child = fx3.dir / "widget"
    code, _out, err = run(
        "--template", fx3.template, "new", fx3.child, "-y", "--set", "github_user=jane",
        "--feature", "license_apache=true",
    )  # fmt: skip
    assert code == 0, err
    fx3.child_commit()
    # The template gains a third license, on by default.
    fx3.set_manifest(
        MANIFEST_V3 + '\n[features.license_gnu]\nconflicts = ["license_mit", "license_apache"]\n'
    )
    code, out, err = fx3.update()
    assert code == 0, err
    assert "recorded in .template.toml: license_gnu = false" in out


@pytest.mark.integration
def test_update_records_new_feature_off_when_its_requirement_is_off(fx: Fixture) -> None:
    code, _out, err = fx.new("--feature", "docs=false")
    assert code == 0, err
    fx.child_commit()
    # The template gains a feature, on by default, that requires docs.
    fx.set_manifest(MANIFEST + '\n[features.docs_search]\nrequires = ["docs"]\n')
    code, out, err = fx.update()  # not a terminal: takes the default
    assert code == 0, err
    assert "recorded in .template.toml: docs_search = false" in out
    assert tomllib.loads(fx.read(".template.toml"))["features"]["docs_search"] is False


@pytest.mark.integration
def test_managed_drift_is_restored(synced: Fixture) -> None:
    synced.edit_child(".gitignore", "out/\nmine/\n")
    code, out, _err = synced.check()
    assert code == 1
    assert statuses(out)[".gitignore"] == "drift"
    code, _out, err = synced.update()
    assert code == 0, err
    assert synced.read(".gitignore") == "out/\n"


@pytest.mark.integration
def test_merge_keeps_local_edit(synced: Fixture) -> None:
    synced.edit_child("NOTES.md", "# Notes\n\nline ONE\n\nline two\n\nline three\n")
    synced.edit_template("NOTES.md", "# Notes\n\nline one\n\nline two\n\nline THREE\n")
    code, out, err = synced.check()
    assert code == 0, err
    assert statuses(out)["NOTES.md"] == "merged"
    code, _out, err = synced.update()
    assert code == 0, err
    assert synced.read("NOTES.md") == "# Notes\n\nline ONE\n\nline two\n\nline THREE\n"


@pytest.mark.integration
def test_merge_local_only_is_not_drift(synced: Fixture) -> None:
    synced.edit_child("NOTES.md", "# Notes\n\nmine\n")
    code, out, err = synced.check()
    assert code == 0, err
    assert statuses(out)["NOTES.md"] == "local"
    synced.update()
    assert synced.read("NOTES.md") == "# Notes\n\nmine\n"


@pytest.mark.integration
def test_format_bump_keeps_reading_the_pin(fx: Fixture) -> None:
    # A child pinned under format 1. The bump to 2 lands with ci.yml's removal, and only
    # the manifest at the pin knows ci.yml was managed -- so that manifest must stay readable.
    write(fx.template, "template.toml", MANIFEST_V1)
    sh(fx.template, "rm", "-q", "child/GUIDE.md")
    fx.template_commit("format 1")
    code, _out, err = fx.new()
    assert code == 0, err
    fx.child_commit()
    write(fx.template, "template.toml", MANIFEST.replace(', "ci.yml"]', "]"))
    write(fx.template, "child/GUIDE.md", FILES["child/GUIDE.md"])
    sh(fx.template, "rm", "-q", "ci.yml")
    fx.template_commit("format 2")
    code, out, err = fx.check()
    assert code == 0, err
    assert "no readable" not in out
    assert statuses(out) == {"ci.yml": "delete", "child/GUIDE.md": "new"}


@pytest.mark.integration
def test_mapped_path(synced: Fixture) -> None:
    fx = synced
    fx.edit_template("GUIDE.md", "# the template's guide, reworded\n")
    code, out, err = fx.check()
    assert code == 0, err
    assert statuses(out) == {}
    fx.edit_template("child/GUIDE.md", FILES["child/GUIDE.md"].replace("# guide", "# Guide"))
    code, out, err = fx.check()
    assert code == 0, err
    assert statuses(out) == {"child/GUIDE.md": "update"}
    assert "child/GUIDE.md -> GUIDE.md" in out
    code, _out, err = fx.update()
    assert code == 0, err
    assert fx.read("GUIDE.md") == "# Guide\nsee docs\n"
    assert not fx.exists("child")


@pytest.mark.integration
def test_template_only_edits_are_invisible(synced: Fixture) -> None:
    synced.edit_template(
        "Makefile",
        "all:\n\techo demo-template\n"
        "# <template-only>\ntemplate-y:\n\techo y\n# </template-only>\n",
    )
    code, out, err = synced.check()
    assert code == 0, err
    assert "Makefile" not in statuses(out)


@pytest.mark.integration
def test_conflict_cycle(synced: Fixture) -> None:
    fx = synced
    fx.edit_child("NOTES.md", "# Notes\n\nline one\n\nchild two\n\nline three\n")
    fx.edit_template("NOTES.md", "# Notes\n\nline one\n\ntemplate two\n\nline three\n")
    old_pin = fx.pin()
    code, out, _err = fx.update()
    assert code == 1
    assert statuses(out)["NOTES.md"] == "conflict"
    notes = fx.read("NOTES.md")
    assert "<<<<<<< yours" in notes
    assert (
        f"||||||| template@{old_pin[:7]}" in notes
    )  # --diff3: the base is labelled with the old pin
    assert f">>>>>>> template@{fx.head[:7]}" in notes
    assert fx.pin() == fx.head
    fx.child_commit("conflicted")
    code, out, _err = fx.check()
    assert code == 1
    assert statuses(out)["NOTES.md"] == "markers"
    code, _out, err = fx.update()
    assert code == 2
    assert "resolve the conflict markers" in err
    fx.edit_child("NOTES.md", "# Notes\n\nline one\n\nboth two\n\nline three\n")
    code, out, err = fx.check()
    assert code == 0, err
    assert statuses(out)["NOTES.md"] == "local"


@pytest.mark.integration
def test_new_and_seed_files(synced: Fixture) -> None:
    fx = synced
    write(fx.template, "template.toml", MANIFEST.replace('"ci.yml"]', '"ci.yml", "extra.yml"]'))
    write(fx.template, "extra.yml", "extra: 1\n")
    write(fx.template, "src/other.py", "print(2)\n")
    fx.template_commit()
    code, out, err = fx.check()
    assert code == 0, err
    assert statuses(out) == {"extra.yml": "new", "src/other.py": "seed-available"}
    assert fx.check("--strict")[0] == 1
    code, _out, err = fx.update("--seed")
    assert code == 0, err
    assert fx.read("extra.yml") == "extra: 1\n"
    assert fx.read("src/other.py") == "print(2)\n"


@pytest.mark.integration
def test_seed_offer_closes_with_the_pin(synced: Fixture) -> None:
    synced.edit_template("src/other.py", "print(2)\n")
    code, _out, err = synced.update()
    assert code == 0, err
    assert not synced.exists("src/other.py")
    synced.child_commit()
    code, out, err = synced.check()
    assert code == 0, err
    assert "src/other.py" not in out


@pytest.mark.integration
def test_delete_upstream(synced: Fixture) -> None:
    write(synced.template, "template.toml", MANIFEST.replace(', "ci.yml"]', "]"))
    sh(synced.template, "rm", "-q", "ci.yml")
    synced.template_commit("drop ci")
    code, out, err = synced.check()
    assert code == 0, err
    assert statuses(out)["ci.yml"] == "delete"
    code, _out, err = synced.update()
    assert code == 0, err
    assert not synced.exists("ci.yml")


@pytest.mark.integration
def test_delete_upstream_but_edited_locally(synced: Fixture) -> None:
    synced.edit_child("ci.yml", "name: ci\nrun: make mine\n")
    write(synced.template, "template.toml", MANIFEST.replace(', "ci.yml"]', "]"))
    sh(synced.template, "rm", "-q", "ci.yml")
    synced.template_commit("drop ci")
    code, out, _err = synced.check()
    assert code == 1
    assert statuses(out)["ci.yml"] == "drift"
    synced.update()
    assert synced.exists("ci.yml")


@pytest.mark.integration
def test_overrides(synced: Fixture) -> None:
    cfg = synced.read(".template.toml") + '".gitignore" = "merge"\n"ci.yml" = "ignore"\n'
    synced.edit_child(".template.toml", cfg, commit=False)
    synced.edit_child(".gitignore", "out/\nmine/\n", commit=False)
    synced.edit_child("ci.yml", "whatever\n")
    code, out, err = synced.check()
    assert code == 0, err
    assert statuses(out) == {".gitignore": "local", "ci.yml": "ignored"}


@pytest.mark.integration
def test_feature_requires_propagates(synced: Fixture) -> None:
    cfg = synced.read(".template.toml").replace("docs = true", "docs = false")
    synced.edit_child(".template.toml", cfg)
    code, out, err = synced.check("--all")
    assert code == 0, err
    assert "note: feature docs_extra turned off: it requires docs" in out
    for rel in ("docs.yml", "docs/index.md", "docs/extra.md"):
        assert rel not in statuses(out)


@pytest.mark.integration
def test_update_refuses_dirty_child(synced: Fixture) -> None:
    synced.edit_child("NOTES.md", "dirty\n", commit=False)
    code, _out, err = synced.update()
    assert code == 2
    assert "uncommitted changes" in err
    code, _out, err = synced.update("--force")
    assert code == 0, err


@pytest.mark.integration
def test_update_refuses_dirty_template_but_check_warns(synced: Fixture) -> None:
    write(synced.template, "ci.yml", "changed\n")
    code, out, err = synced.check()
    assert code == 0, err  # the working-tree change reads as a pending update
    assert statuses(out)["ci.yml"] == "update"
    assert "warning: template has uncommitted changes" in err
    code, _out, err = synced.update()
    assert code == 2
    assert "uncommitted changes" in err


@pytest.mark.integration
def test_check_without_config(synced: Fixture) -> None:
    (synced.child / ".template.toml").unlink()
    code, _out, err = synced.check()
    assert code == 2
    assert "no .template.toml or .local/.template.toml" in err
    assert "[template]" in err
    assert "project = " in err


@pytest.mark.integration
def test_config_in_local(synced: Fixture) -> None:
    fx = synced
    # Untracked in .local/, so the project's history holds nothing of the sync's.
    write(fx.child, ".git/info/exclude", ".local/\n")  # the fixture's .gitignore doesn't
    sh(fx.child, "rm", "-q", "--cached", ".template.toml")
    (fx.child / ".local").mkdir()
    (fx.child / ".template.toml").rename(fx.child / ".local/.template.toml")
    fx.child_commit("keep the config local")
    fx.add_lint_feature()
    fx.edit_template(".gitignore", "out/\n*.log\n")
    code, out, err = fx.update()
    assert code == 0, err
    assert "recorded in .local/.template.toml: lint = false" in out
    assert tomllib.loads(fx.read(".local/.template.toml"))["features"]["lint"] is False
    assert fx.pin() == fx.head
    assert not fx.exists(".template.toml")
    assert sh(fx.child, "status", "--porcelain").split() == ["M", ".gitignore"]


@pytest.mark.integration
def test_root_config_wins_over_local(synced: Fixture) -> None:
    write(synced.child, ".local/.template.toml", "not toml [\n")
    code, out, err = synced.check()
    assert code == 0, err
    assert "note: .local/.template.toml is not read: .template.toml takes precedence" in out


@pytest.mark.integration
def test_check_rejects_bad_config(synced: Fixture) -> None:
    synced.edit_child(
        ".template.toml", synced.read(".template.toml").replace("holder = ", "holdr = ")
    )
    code, _out, err = synced.check()
    assert code == 2
    assert "missing holder" in err


@pytest.mark.integration
def test_submodule(synced: Fixture) -> None:
    fx = synced
    lib = fx.dir / "lib"
    lib.mkdir()
    sh(lib, "init", "-q")
    write(lib, "lib.py", "x\n")
    sha = commit_all(lib, "lib")
    sh(fx.template, "submodule", "--quiet", "add", lib.as_posix(), "vendor/lib")
    fx.template_commit("submodule")
    other = fx.dir / "other"
    code, out, err = run("--template", fx.template, "new", other, "-y")
    assert code == 0, err
    assert f"submodule vendor/lib @ {sha[:7]}" in out
    assert (other / ".gitmodules").read_text() == (fx.template / ".gitmodules").read_text()
    assert (other / "vendor/lib/lib.py").exists()
    index = sh(other, "ls-files", "-s", "vendor/lib")
    assert "160000" in index
    assert sha in index
    # Nothing committed yet: the submodule is staged, not missing.
    code, out, err = run("--template", fx.template, "check", other, "--all")
    assert code == 0, err
    assert f"submodule vendor/lib: in sync at {sha[:7]}" in out
    commit_all(other, "initial")
    code, out, err = run("--template", fx.template, "check", other, "--all")
    assert code == 0, err
    assert f"submodule vendor/lib: in sync at {sha[:7]}" in out
    # Created without submodules, it really is missing, and check says how to add it.
    bare = fx.dir / "bare"
    code, _out, err = run("--template", fx.template, "new", bare, "-y", "--no-submodules")
    assert code == 0, err
    code, out, err = run("--template", fx.template, "check", bare)
    assert code == 0, err
    assert "submodule vendor/lib: not in the child (git submodule add " in out


# ---------------------------------------------------------------------------
# Token formats and aliases: one version value, many spellings
# ---------------------------------------------------------------------------

VERSION_TOKENS = """\
"3.14" = { key = "python", aliases = { all-supported = "3.11" } }
"==3.14.*" = { key = "python", format = "=={}.*", aliases = { all-supported = ">=3.11" } }
[tokens.'python: ["3.14"]']
key = "python"
format = 'python: ["{}"]'
aliases = { all-supported = 'python: ["3.11", "3.12", "3.13", "3.14"]' }
"""
MANIFEST_VERSIONS = f"""\
manifest_format = 3
exclude = ["template.toml"]

[tokens]
{VERSION_TOKENS}
[base]
managed = ["ci.yml", ".python-version"]
merge = ["pyproject.toml"]
"""
FILES_VERSIONS = {
    "ci.yml": 'name: ci\nmatrix:\n  python: ["3.14"]\nrun: test\n',
    ".python-version": "3.14\n",
    "pyproject.toml": (
        '[project]\nrequires-python = "==3.14.*"\n\n[tool.mypy]\npython_version = "3.14"\n'
    ),
}


@pytest.fixture
def fxv(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path, MANIFEST_VERSIONS, FILES_VERSIONS)


@pytest.mark.unit
def test_token_render_and_unformat() -> None:
    token = ts.Token("python", format="=={}.*", aliases={"all-supported": ">=3.11"})
    assert token.render("3.13") == "==3.13.*"
    assert token.render("all-supported") == ">=3.11"
    assert token.unformat("==3.14.*") == "3.14"
    assert token.unformat("3.14") is None
    assert ts.Token("project", transform="kebab", format="pkg-{}").render("My App") == "pkg-my-app"
    assert ts.Token("python").unformat("3.14") == "3.14"


@pytest.mark.unit
def test_placeholder_values_unformat_the_first_literal() -> None:
    text = 'manifest_format = 3\n[tokens]\n"==3.14.*" = { key = "python", format = "=={}.*" }\n'
    manifest = ts.parse_manifest(text, Path("."), where="t")
    assert manifest.placeholder_values() == {"python": "3.14"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ('"x" = { key = "k", format = "no placeholder" }', "format must be a string"),
        ('"x" = { key = "k", format = "{}{}" }', "format must be a string"),
        ('"x" = { key = "k", aliases = ["a"] }', "aliases must be a table"),
        ('"x" = { key = "k", aliases = { a = 1 } }', "aliases must be a table"),
    ],
)
def test_parse_manifest_rejects_bad_formats_and_aliases(entry: str, message: str) -> None:
    with pytest.raises(ts.ConfigError, match=message):
        ts.parse_manifest(f"manifest_format = 3\n[tokens]\n{entry}\n", Path("."), where="t")


@pytest.mark.integration
def test_validate_versions(fxv: Fixture) -> None:
    code, _out, err = fxv.validate()
    assert code == 0, err


@pytest.mark.integration
def test_validate_rejects_a_literal_that_is_not_a_rendering(fxv: Fixture) -> None:
    fxv.set_manifest(MANIFEST_VERSIONS.replace('"==3.14.*" = {', '"==3.13.*" = {'))
    assert_rejected(fxv, "[tokens] '==3.13.*': rendering the template's own python value")


@pytest.mark.integration
def test_validate_rejects_aliases_missing_from_one_spelling(fxv: Fixture) -> None:
    fxv.set_manifest(
        MANIFEST_VERSIONS.replace(
            '"3.14" = { key = "python", aliases = { all-supported = "3.11" } }',
            '"3.14" = { key = "python" }',
        )
    )
    assert_rejected(fxv, "[tokens] '3.14': no aliases all-supported")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("value", "python_version", "requires", "matrix"),
    [
        (None, "3.14", "==3.14.*", '["3.14"]'),
        ("3.12", "3.12", "==3.12.*", '["3.12"]'),
        ("all-supported", "3.11", ">=3.11", '["3.11", "3.12", "3.13", "3.14"]'),
    ],
)
def test_new_versions(
    fxv: Fixture, value: str | None, python_version: str, requires: str, matrix: str
) -> None:
    extra = ("--set", f"python={value}") if value else ()
    code, _out, err = run("--template", fxv.template, "new", fxv.child, "-y", *extra)
    assert code == 0, err
    assert fxv.read(".python-version") == f"{python_version}\n"
    assert fxv.read("pyproject.toml") == (
        f'[project]\nrequires-python = "{requires}"\n\n'
        f'[tool.mypy]\npython_version = "{python_version}"\n'
    )
    assert f"  python: {matrix}\n" in fxv.read("ci.yml")
    assert tomllib.loads(fxv.read(".template.toml"))["values"] == {"python": value or "3.14"}


@pytest.mark.integration
def test_new_prompt_names_the_aliases(fxv: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    ask = tty(monkeypatch, ["3.13"])
    code, _out, err = run("--template", fxv.template, "new", fxv.child)
    assert code == 0, err
    assert ask.call_args.args[0] == "python (or all-supported) [3.14]: "
    assert fxv.read(".python-version") == "3.13\n"


@pytest.mark.integration
def test_update_across_a_version_bump_keeps_the_pinned_version(fxv: Fixture) -> None:
    # A project pinned to 3.12. The template moves to 3.15 and changes another line
    # of the managed ci.yml in the same commit: the pin's text must be rendered with
    # the pin's tokens, or the project's ci.yml would read as edited (drift).
    code, _out, err = run(
        "--template", fxv.template, "new", fxv.child, "-y", "--set", "python=3.12"
    )
    assert code == 0, err
    fxv.child_commit()
    fxv.set_manifest(MANIFEST_VERSIONS.replace("3.14", "3.15"))
    for rel, text in FILES_VERSIONS.items():
        write(fxv.template, rel, text.replace("3.14", "3.15").replace("run: test", "run: test -x"))
    fxv.template_commit("python 3.15")
    code, out, err = fxv.check()
    assert code == 0, err
    assert statuses(out) == {"ci.yml": "update"}
    code, _out, err = fxv.update()
    assert code == 0, err
    assert fxv.read("ci.yml") == 'name: ci\nmatrix:\n  python: ["3.12"]\nrun: test -x\n'
    assert fxv.read(".python-version") == "3.12\n"


# ---------------------------------------------------------------------------
# Features with options: one choice among several, or none
# ---------------------------------------------------------------------------

MANIFEST_OPTIONS = """\
manifest_format = 3
exclude = ["template.toml"]

[base]
managed = ["README.md"]

[features.license]
description = "the license"
options = ["license_mit", "license_gnu"]
seed = ["LICENSE"]

[features.license_mit]
description = "MIT"

[features.license_gnu]
description = "GPL"
"""
FILES_OPTIONS = {
    "README.md": (
        '# demo\n<!-- <template-only feature="license"> -->\nlicensed\n<!-- </template-only> -->\n'
    ),
    "LICENSE": (
        '<template-only feature="license_mit">\nMIT\n</template-only>\n'
        '<template-only feature="license_gnu">\nGPL\n</template-only>\n'
    ),
}


@pytest.fixture
def fxo(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path, MANIFEST_OPTIONS, FILES_OPTIONS)


def options_manifest(text: str = MANIFEST_OPTIONS) -> ts.Manifest:
    return ts.parse_manifest(text, Path("."), where="t")


@pytest.mark.unit
def test_options_imply_requires_conflicts_and_defaults() -> None:
    features = options_manifest().features
    assert features["license_mit"].requires == ("license",)
    assert features["license_mit"].conflicts == ("license_gnu",)
    assert features["license_gnu"].conflicts == ("license_mit",)
    # The first option takes its feature's default; the rest are off.
    assert (features["license_mit"].default, features["license_gnu"].default) == (True, False)
    off = options_manifest(
        MANIFEST_OPTIONS.replace("[features.license]\n", "[features.license]\ndefault = false\n")
    )
    assert off.features["license_mit"].default is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('["license_mit", "license_gnu"]', '["license_mit"]', "list two or more other declared"),
        ('["license_mit", "license_gnu"]', '["license_mit", "nope"]', "list two or more other"),
        ('["license_mit", "license_gnu"]', '["license", "license_mit"]', "list two or more other"),
        ('["license_mit", "license_gnu"]', '["license_mit", "license_mit"]', "list two or more"),
        ('description = "GPL"\n', 'description = "GPL"\ndefault = false\n', "remove its default"),
        (
            'description = "GPL"\n',
            'description = "GPL"\noptions = ["license_mit", "license"]\n',
            "so it can't have options",
        ),
        ("manifest_format = 3", "manifest_format = 2", "options needs manifest_format = 3"),
    ],
)
def test_parse_manifest_rejects_bad_options(old: str, new: str, message: str) -> None:
    with pytest.raises(ts.ConfigError, match=message):
        options_manifest(MANIFEST_OPTIONS.replace(old, new))


@pytest.mark.unit
def test_parse_manifest_rejects_an_option_of_two_features() -> None:
    text = MANIFEST_OPTIONS + '\n[features.other]\noptions = ["license_gnu", "license_x"]\n'
    text += "\n[features.license_x]\n"
    with pytest.raises(ts.ConfigError, match="is an option of both license and other"):
        options_manifest(text)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("chosen", "on"),
    [
        ({}, {"license", "license_mit"}),
        # Choosing an option turns its feature on, and the first option's default off.
        ({"license_gnu": True}, {"license", "license_gnu"}),
        ({"license": False}, set()),
    ],
)
def test_effective_features_with_options(chosen: dict[str, bool], on: set[str]) -> None:
    enabled, _notes = ts.effective_features(options_manifest(), chosen)
    assert enabled == on


@pytest.mark.unit
def test_effective_features_refuses_a_feature_on_with_no_option() -> None:
    with pytest.raises(ts.ConfigError, match="license is on, but none of its options is"):
        ts.effective_features(options_manifest(), {"license": True, "license_mit": False})


@pytest.mark.integration
@pytest.mark.parametrize(
    ("answers", "license_text"),
    [
        ([""], "MIT\n"),
        (["2"], "GPL\n"),
        (["gnu"], "GPL\n"),
        (["License_GNU"], "GPL\n"),
        (["bogus", "gnu"], "GPL\n"),  # asked again
        (["none"], None),
        (["3"], None),
    ],
)
def test_new_asks_for_one_option_or_none(
    fxo: Fixture, monkeypatch: pytest.MonkeyPatch, answers: list[str], license_text: str | None
) -> None:
    ask = tty(monkeypatch, answers)
    code, out, err = run("--template", fxo.template, "new", fxo.child)
    assert code == 0, err
    assert "license (the license):\n  1. mit   MIT\n  2. gnu   GPL\n  3. none\n" in out
    assert [c.args[0] for c in ask.call_args_list] == ["license [mit]: "] * len(answers)
    if license_text is None:
        assert not fxo.exists("LICENSE")
        assert fxo.read("README.md") == "# demo\n"
    else:
        assert fxo.read("LICENSE") == license_text
        assert fxo.read("README.md") == "# demo\nlicensed\n"
    features = tomllib.loads(fxo.read(".template.toml"))["features"]
    assert features == {
        "license": license_text is not None,
        "license_mit": license_text == "MIT\n",
        "license_gnu": license_text == "GPL\n",
    }


@pytest.mark.integration
def test_new_defaults_to_none_when_the_feature_is_off_by_default(
    fxo: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fxo.set_manifest(
        MANIFEST_OPTIONS.replace("[features.license]\n", "[features.license]\ndefault = false\n")
    )
    ask = tty(monkeypatch, [""])
    code, _out, err = run("--template", fxo.template, "new", fxo.child)
    assert code == 0, err
    assert ask.call_args.args[0] == "license [none]: "
    assert not fxo.exists("LICENSE")


@pytest.mark.integration
def test_new_given_the_feature_on_asks_for_an_option_without_none(
    fxo: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask = tty(monkeypatch, ["2"])
    code, out, err = run("--template", fxo.template, "new", fxo.child, "--feature", "license=true")
    assert code == 0, err
    assert ask.call_count == 1
    assert "none" not in out.split("created")[0]
    assert fxo.read("LICENSE") == "GPL\n"


@pytest.mark.integration
def test_new_given_an_option_asks_nothing_about_its_feature(
    fxo: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask = tty(monkeypatch, [])
    code, _out, err = run(
        "--template", fxo.template, "new", fxo.child, "--feature", "license_gnu=true"
    )
    assert code == 0, err
    assert ask.call_count == 0
    assert fxo.read("LICENSE") == "GPL\n"


@pytest.mark.integration
def test_update_records_a_new_option_off(fxo: Fixture) -> None:
    code, _out, err = run(
        "--template", fxo.template, "new", fxo.child, "-y", "--feature", "license_gnu=true"
    )
    assert code == 0, err
    fxo.child_commit()
    fxo.set_manifest(
        MANIFEST_OPTIONS.replace('"license_gnu"]', '"license_gnu", "license_apache"]')
        + "\n[features.license_apache]\n"
    )
    code, out, err = fxo.update()  # not a terminal: takes the default
    assert code == 0, err
    assert "recorded in .template.toml: license_apache = false" in out


# ---------------------------------------------------------------------------
# [new] bootstrap: commands `new` runs in the project it creates
# ---------------------------------------------------------------------------


def py(code: str, stdout: str | None = None) -> str:
    """A [new].bootstrap entry that runs ``code`` with this interpreter."""
    entry = f"run = [{json.dumps(sys.executable)}, '-c', {json.dumps(code)}]"
    if stdout:
        entry += f", stdout = {json.dumps(stdout)}"
    return f"{{ {entry} }}"


def bootstrap_fixture(tmp_path: Path, steps: list[str], next_steps: str = "[]") -> Fixture:
    """The format-3 fixture with a [new] table of ``steps``."""
    entries = "".join(f"  {step},\n" for step in steps)
    new = f"[new]\nbootstrap = [\n{entries}]\nnext_steps = {next_steps}\n"
    exclude = 'exclude = ["template.toml"]\n'
    return Fixture(tmp_path, MANIFEST_V3.replace(exclude, f"{exclude}\n{new}"), FILES_V3)


def new3(fx: Fixture, *extra: object) -> tuple[int, str, str]:
    return run(
        "--template", fx.template, "new", fx.child, "-y", "--set", "github_user=jane", *extra
    )  # fmt: skip


@pytest.mark.unit
def test_parse_new_table() -> None:
    text = MANIFEST_V3 + (
        '\n[new]\nbootstrap = [{ run = ["uv", "sync"] }, '
        '{ run = ["uv", "run", "scan"], stdout = "out/base line.json" }]\n'
        'next_steps = ["git commit"]\n'
    )
    manifest = ts.parse_manifest(text, Path("."), where="t")
    assert manifest.bootstrap == (
        ts.BootstrapStep(("uv", "sync")),
        ts.BootstrapStep(("uv", "run", "scan"), "out/base line.json"),
    )
    assert [str(s) for s in manifest.bootstrap] == ["uv sync", "uv run scan > out/base line.json"]
    assert manifest.next_steps == ("git commit",)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("new", "message"),
    [
        ('bootstrap = { run = ["x"] }', "bootstrap: expected a list of tables"),
        ("bootstrap = [{ run = [] }]", "run: expected a command"),
        ("bootstrap = [{ stdout = 'x' }]", "run: expected a list"),
        ('bootstrap = [{ run = ["x"], shell = true }]', "unknown key(s) shell"),
        ('bootstrap = [{ run = ["x"], stdout = "/etc/x" }]', "relative to the project"),
        ('bootstrap = [{ run = ["x"], stdout = "../x" }]', "relative to the project"),
        ('bootstrap = [{ run = ["x"], stdout = "a/../b" }]', "relative to the project"),
        ('bootstrap = [{ run = ["x"], stdout = "C:x" }]', "relative to the project"),
        ('bootstrap = [{ run = ["x"], stdout = "" }]', "relative to the project"),
        ("next_steps = [1]", "next_steps: expected a list"),
        ("finally = []", "unknown key(s) finally"),
    ],
)
def test_parse_new_rejects_mistakes(new: str, message: str) -> None:
    with pytest.raises(ts.ConfigError) as excinfo:
        ts.parse_manifest(f"manifest_format = 3\n[new]\n{new}\n", Path("."), where="t")
    assert message in str(excinfo.value)


@pytest.mark.unit
def test_new_table_needs_format_3() -> None:
    with pytest.raises(ts.ConfigError, match=r"\[new\] needs manifest_format = 3"):
        ts.parse_manifest("manifest_format = 2\n[new]\n", Path("."), where="t")


@pytest.mark.integration
def test_new_runs_the_bootstrap_in_order_and_stages_its_results(tmp_path: Path) -> None:
    fx = bootstrap_fixture(
        tmp_path,
        [
            py("open('a.txt', 'w').write('from step 1')"),
            py("print(open('a.txt').read().upper())", stdout="out/b.txt"),
        ],
        next_steps='["git commit"]',
    )
    code, out, err = new3(fx)
    assert code == 0, err
    assert fx.read("out/b.txt").strip() == "FROM STEP 1"
    staged = sh(fx.child, "diff", "--cached", "--name-only").split()
    assert {"a.txt", "out/b.txt", ".template.toml"} <= set(staged)
    assert sh(fx.child, "rev-list", "--all").strip() == ""
    assert out.index("bootstrap:") < out.index("Next, in the project:") < out.index("git commit")


@pytest.mark.integration
def test_bootstrap_commands_do_not_see_the_templates_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "template-venv"))
    fx = bootstrap_fixture(
        tmp_path, [py("import os; print(os.environ.get('VIRTUAL_ENV'))", stdout="venv.txt")]
    )
    code, _out, err = new3(fx)
    assert code == 0, err
    assert fx.read("venv.txt").strip() == "None"


@pytest.mark.integration
def test_a_failed_bootstrap_step_stops_and_lists_what_is_left(tmp_path: Path) -> None:
    fx = bootstrap_fixture(
        tmp_path,
        [
            py("open('a.txt', 'w').write('1')"),
            py("print('half'); raise SystemExit(3)", stdout="half.txt"),
            py("open('c.txt', 'w').write('3')"),
        ],
        next_steps='["git commit"]',
    )
    code, out, err = new3(fx)
    assert code == ts.EXIT_DRIFT
    assert "failed (exit 3)" in err
    assert fx.exists("a.txt")
    assert not fx.exists("half.txt")  # no half-written file
    assert not fx.exists("c.txt")  # stopped at the failure
    assert fx.exists(".template.toml")  # the project itself is written
    left = out[out.index("Next, in the project:") :]
    assert "SystemExit(3)" in left
    assert "c.txt" in left
    assert left.rstrip().endswith("git commit")
    assert "a.txt" in sh(fx.child, "diff", "--cached", "--name-only").split()


@pytest.mark.integration
def test_a_missing_bootstrap_command_is_reported(tmp_path: Path) -> None:
    fx = bootstrap_fixture(tmp_path, ['{ run = ["no-such-command-for-template-sync"] }'])
    code, out, err = new3(fx)
    assert code == ts.EXIT_DRIFT
    assert "no-such-command-for-template-sync: not found on PATH" in err
    assert "no-such-command-for-template-sync" in out[out.index("Next, in the project:") :]


@pytest.mark.integration
def test_no_bootstrap_prints_the_commands_instead(tmp_path: Path) -> None:
    fx = bootstrap_fixture(tmp_path, [py("open('a.txt', 'w').write('1')")])
    code, out, err = new3(fx, "--no-bootstrap")
    assert code == 0, err
    assert not fx.exists("a.txt")
    assert "bootstrap:" not in out
    assert "open('a.txt', 'w')" in out[out.index("Next, in the project:") :]
