"""Unit tests for the pure helpers in scripts/compare_to_template.py.

The scripts folder is not a package, so the module is imported by adding the
folder to sys.path, mirroring how the scripts import their shared _cli module.
Token strings (template name, author username) are built from the module's
constants rather than written literally, so the template-setup rename scripts
cannot rewrite them here.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from compare_to_template import (
    _MARKER,
    MANIFEST,
    TEMPLATE_KEBAB,
    TEMPLATE_SNAKE,
    TEMPLATE_USER,
    BaselineFile,
    CompareContext,
    Comparison,
    ProjectNames,
    compare_one,
    diff_files_for,
    effective_strict,
    github_user_from_url,
    is_excluded,
    map_project_path,
    normalize_eol,
    normalize_template_text,
    pyproject_name,
    python_version_forms,
    replace_case_insensitive,
    replay_cleanup_pyproject,
    replay_python_version,
    resolve_code,
    script_version,
    script_version_note,
    self_check_action,
    strip_template_header,
    version_tuple,
)

NAMES = ProjectNames(snake="my_proj", kebab="my-proj", github_user="octocat")

BANNER = (
    "# =============================================================================\n"
    f"# {_MARKER} -- remove this block when you use this template\n"
    "# Explanatory text for template users.\n"
    "# =============================================================================\n"
    "\n"
)


def write(root: Path, rel: str, text: str) -> Path:
    """Write a file with exact contents (no newline translation)."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def make_ctx(
    tmp_path: Path,
    *,
    names: ProjectNames = NAMES,
    dotted: str | None = None,
    compact: str | None = None,
    ran_cleanup: bool = False,
    has_mkdocs: bool = True,
) -> CompareContext:
    """Create template/ and project/ dirs under tmp_path and build a context."""
    template_root = tmp_path / "template"
    project_root = tmp_path / "project"
    template_root.mkdir(exist_ok=True)
    project_root.mkdir(exist_ok=True)
    return CompareContext(
        template_root=template_root,
        project_root=project_root,
        names=names,
        dotted=dotted,
        compact=compact,
        ran_cleanup=ran_cleanup,
        has_mkdocs=has_mkdocs,
    )


# --- version parsing ---------------------------------------------------------


def test_version_tuple_parses_numeric_versions() -> None:
    assert version_tuple("1.2.3") == (1, 2, 3)
    assert version_tuple("10.0") == (10, 0)


def test_version_tuple_rejects_non_numeric_parts() -> None:
    assert version_tuple("1.2.3rc1") is None
    assert version_tuple("abc") is None
    assert version_tuple("") is None


def test_script_version_finds_declaration() -> None:
    assert script_version('x = 1\n__version__ = "1.4.0"\n') == "1.4.0"
    assert script_version("__version__ = '2.0.0'\n") == "2.0.0"


def test_script_version_missing_returns_none() -> None:
    assert script_version("x = 1\n") is None


def test_python_version_forms_major_minor() -> None:
    assert python_version_forms("3.13") == ("3.13", "3.13", "py313")


def test_python_version_forms_keeps_patch_in_full_form() -> None:
    assert python_version_forms("3.14.3") == ("3.14.3", "3.14", "py314")


def test_python_version_forms_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="not a valid Python version"):
        python_version_forms("py314")


# --- GitHub username extraction ----------------------------------------------


def test_github_user_from_https_url() -> None:
    assert github_user_from_url("https://github.com/octocat/my-proj.git") == "octocat"


def test_github_user_from_scp_style_ssh_url() -> None:
    assert github_user_from_url("git@github.com:octocat/my-proj.git") == "octocat"


def test_github_user_from_ssh_scheme_url() -> None:
    assert github_user_from_url("ssh://git@github.com/octocat/my-proj.git") == "octocat"


def test_github_user_from_non_github_url_is_none() -> None:
    assert github_user_from_url("https://gitlab.com/octocat/my-proj.git") is None
    assert github_user_from_url("") is None


# --- text normalization -------------------------------------------------------


def test_normalize_eol_converts_crlf_and_cr() -> None:
    assert normalize_eol("a\r\nb\rc\n") == "a\nb\nc\n"


def test_replace_case_insensitive_replaces_all_casings() -> None:
    old = TEMPLATE_USER
    text = f"see {old} and {old.upper()} and {old.title()}"
    assert replace_case_insensitive(text, old, "octocat") == "see octocat and octocat and octocat"


def test_strip_template_header_removes_hash_banner() -> None:
    assert strip_template_header(BANNER + "content\n") == "content\n"


def test_strip_template_header_removes_slash_banner() -> None:
    banner = f"// ====\n// {_MARKER}\n// ====\n\ncontent\n"
    assert strip_template_header(banner) == "content\n"


def test_strip_template_header_removes_markdown_banner() -> None:
    banner = f"<!--\n{_MARKER}\nexplanation\n-->\n\n# Title\n"
    assert strip_template_header(banner) == "# Title\n"


def test_strip_template_header_without_marker_is_unchanged() -> None:
    text = "# just a comment\ncontent\n"
    assert strip_template_header(text) == text


def test_strip_template_header_without_separators_is_unchanged() -> None:
    text = f"# {_MARKER}\ncontent\n"
    assert strip_template_header(text) == text


def test_strip_template_header_ignores_marker_in_code() -> None:
    text = f'_MARKER = "{_MARKER}"\n'
    assert strip_template_header(text) == text


# --- setup-script replays ------------------------------------------------------


def test_replay_python_version_rewrites_pyproject_pins() -> None:
    text = 'requires-python = ">=3.14"\ntarget-version = "py314"\npython_version = "3.14"\n'
    result = replay_python_version("pyproject.toml", text, "3.13", "py313")
    expected = 'requires-python = ">=3.13"\ntarget-version = "py313"\npython_version = "3.13"\n'
    assert result == expected


def test_replay_python_version_rewrites_precommit_pin() -> None:
    result = replay_python_version(
        ".pre-commit-config.yaml", "  python: python3.14\n", "3.13", "py313"
    )
    assert result == "  python: python3.13\n"


def test_replay_python_version_rewrites_contributing_and_readme() -> None:
    assert (
        replay_python_version("CONTRIBUTING.md", "Requires Python 3.14+.\n", "3.13", "py313")
        == "Requires Python 3.13+.\n"
    )
    assert (
        replay_python_version("README.md", "badge/python-3.14%2B-blue\n", "3.13", "py313")
        == "badge/python-3.13%2B-blue\n"
    )


def test_replay_python_version_rewrites_bug_report_placeholder() -> None:
    text = 'id: python-version\n  attributes:\n    placeholder: "3.14.3"\n'
    result = replay_python_version(".github/ISSUE_TEMPLATE/bug_report.yml", text, "3.13", "py313")
    assert 'placeholder: "3.13.3"' in result


def test_replay_python_version_leaves_other_files_alone() -> None:
    text = "python 3.14 mentioned in prose\n"
    assert replay_python_version("SECURITY.md", text, "3.13", "py313") == text


def test_replay_cleanup_pyproject_drops_template_only_lines() -> None:
    text = (
        'addopts = [\n    "--cov=scripts",\n]\nmypy_path = ["scripts", "scripts/template_setup"]\n'
    )
    assert replay_cleanup_pyproject(text) == 'addopts = [\n]\nmypy_path = ["scripts"]\n'


def test_replay_cleanup_pyproject_tolerates_missing_snippets() -> None:
    text = "unrelated = true\n"
    assert replay_cleanup_pyproject(text) == text


# --- token mapping -------------------------------------------------------------


def test_map_project_path_renames_tokens_in_path() -> None:
    rel = f"docs/reference/{TEMPLATE_SNAKE}.md"
    assert map_project_path(rel, NAMES) == "docs/reference/my_proj.md"


def test_map_project_path_leaves_plain_paths_alone() -> None:
    assert map_project_path("scripts/_cli.py", NAMES) == "scripts/_cli.py"


def test_normalize_template_text_full_pipeline() -> None:
    text = BANNER + f"pkg {TEMPLATE_SNAKE} dist {TEMPLATE_KEBAB} by {TEMPLATE_USER.title()}\n"
    result = normalize_template_text("notes.md", text, NAMES)
    assert result == "pkg my_proj dist my-proj by octocat\n"


def test_normalize_template_text_skips_user_when_unknown() -> None:
    names = ProjectNames(snake="my_proj", kebab="my-proj", github_user=None)
    text = f"by {TEMPLATE_USER}\n"
    assert normalize_template_text("notes.md", text, names) == f"by {TEMPLATE_USER}\n"


# --- strictness ------------------------------------------------------------------


def test_effective_strict_demotes_mkdocs_edited_files_when_removed() -> None:
    entry = BaselineFile("CONTRIBUTING.md")
    assert effective_strict(entry, has_mkdocs=True) is True
    assert effective_strict(entry, has_mkdocs=False) is False


def test_effective_strict_keeps_other_files_strict() -> None:
    entry = BaselineFile("SECURITY.md")
    assert effective_strict(entry, has_mkdocs=False) is True


def test_effective_strict_never_promotes_lenient_files() -> None:
    entry = BaselineFile("pyproject.toml", strict=False)
    assert effective_strict(entry, has_mkdocs=True) is False


# --- version notes and self-check ------------------------------------------------


def test_script_version_note_outdated_and_ahead() -> None:
    older = '__version__ = "1.1.0"\n'
    newer = '__version__ = "1.2.0"\n'
    assert "outdated" in script_version_note(newer, older)
    assert "upstream" in script_version_note(older, newer)


def test_script_version_note_same_version() -> None:
    text = '__version__ = "1.0.0"\n'
    assert "without a version bump" in script_version_note(text, text)


def test_script_version_note_missing_version_is_empty() -> None:
    assert script_version_note("x = 1\n", '__version__ = "1.0.0"\n') == ""


def test_self_check_action_identical_content() -> None:
    assert self_check_action("1.0.0", "1.0.0", same_content=True) == "ok"


def test_self_check_action_project_older() -> None:
    assert self_check_action("1.2.0", "1.1.0", same_content=False) == "update"


def test_self_check_action_project_newer() -> None:
    assert self_check_action("1.1.0", "1.2.0", same_content=False) == "ahead"


def test_self_check_action_unparseable_version() -> None:
    assert self_check_action("1.1.0", None, same_content=False) == "update"


def test_self_check_action_same_version_different_content() -> None:
    assert self_check_action("1.1.0", "1.1.0", same_content=False) == "refresh"


# --- compare_one -------------------------------------------------------------------


def test_compare_one_match_after_normalization(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    template = BANNER + f"pkg {TEMPLATE_SNAKE} dist {TEMPLATE_KEBAB} by {TEMPLATE_USER}\n"
    write(ctx.template_root, "notes.md", template)
    write(ctx.project_root, "notes.md", "pkg my_proj dist my-proj by octocat\n")
    result = compare_one(BaselineFile("notes.md"), ctx)
    assert result.status == "match"


def test_compare_one_match_ignores_line_endings(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    write(ctx.template_root, "notes.md", "line one\nline two\n")
    write(ctx.project_root, "notes.md", "line one\r\nline two\r\n")
    assert compare_one(BaselineFile("notes.md"), ctx).status == "match"


def test_compare_one_modified_strict(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    write(ctx.template_root, "notes.md", "template says A\n")
    write(ctx.project_root, "notes.md", "project says B\n")
    result = compare_one(BaselineFile("notes.md"), ctx)
    assert result.status == "modified"
    assert result.template_norm is not None
    assert result.project_norm is not None


def test_compare_one_review_for_lenient_entries(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    write(ctx.template_root, "notes.md", "template says A\n")
    write(ctx.project_root, "notes.md", "project says B\n")
    result = compare_one(BaselineFile("notes.md", strict=False), ctx)
    assert result.status == "review"
    assert "expected to differ" in result.note


def test_compare_one_missing_required(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    write(ctx.template_root, "notes.md", "content\n")
    assert compare_one(BaselineFile("notes.md"), ctx).status == "missing"


def test_compare_one_absent_optional(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    write(ctx.template_root, "notes.md", "content\n")
    assert compare_one(BaselineFile("notes.md", required=False), ctx).status == "absent"


def test_compare_one_no_template(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    write(ctx.project_root, "notes.md", "content\n")
    assert compare_one(BaselineFile("notes.md"), ctx).status == "no-template"


def test_compare_one_existence_only_ignores_content(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    write(ctx.template_root, "README.md", "template readme\n")
    write(ctx.project_root, "README.md", "a completely different project readme\n")
    result = compare_one(BaselineFile("README.md", compare_content=False), ctx)
    assert result.status == "match"
    assert "contents not compared" in result.note
    # The contents are never read, so no normalized text is stored for --diff.
    assert result.template_norm is None
    assert result.project_norm is None


def test_compare_one_existence_only_missing_required_is_drift(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    write(ctx.template_root, "README.md", "template readme\n")
    result = compare_one(BaselineFile("README.md", compare_content=False), ctx)
    assert result.status == "missing"


def test_compare_one_existence_only_absent_when_optional(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    write(ctx.template_root, "extra.md", "template extra\n")
    result = compare_one(BaselineFile("extra.md", required=False, compare_content=False), ctx)
    assert result.status == "absent"


def test_compare_one_binary_files(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    (ctx.template_root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    (ctx.project_root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    assert compare_one(BaselineFile("blob.bin"), ctx).status == "match"
    (ctx.project_root / "blob.bin").write_bytes(b"\xff\xfe\x00\x02")
    result = compare_one(BaselineFile("blob.bin"), ctx)
    assert result.status == "modified"
    assert "binary" in result.note


def test_compare_one_notes_script_versions(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    write(ctx.template_root, "scripts/helper.py", '__version__ = "1.2.0"\nnew = True\n')
    write(ctx.project_root, "scripts/helper.py", '__version__ = "1.1.0"\n')
    result = compare_one(BaselineFile("scripts/helper.py"), ctx)
    assert result.status == "modified"
    assert "project 1.1.0 < template 1.2.0" in result.note


def test_compare_one_demotes_mkdocs_edited_file(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, has_mkdocs=False)
    write(ctx.template_root, "CONTRIBUTING.md", "with docs section\n")
    write(ctx.project_root, "CONTRIBUTING.md", "docs section removed\n")
    result = compare_one(BaselineFile("CONTRIBUTING.md"), ctx)
    assert result.status == "review"
    assert "mkdocs removed" in result.note


def test_compare_one_maps_renamed_paths(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    rel = f"docs/reference/{TEMPLATE_SNAKE}.md"
    write(ctx.template_root, rel, f"::: {TEMPLATE_SNAKE}\n")
    write(ctx.project_root, "docs/reference/my_proj.md", "::: my_proj\n")
    result = compare_one(BaselineFile(rel, required=False, strict=False), ctx)
    assert result.status == "match"
    assert result.project_rel == "docs/reference/my_proj.md"


# --- manifest ------------------------------------------------------------------------


def test_manifest_has_no_duplicates() -> None:
    paths = [entry.path for entry in MANIFEST]
    assert len(paths) == len(set(paths))


def test_manifest_readme_is_required_but_existence_only() -> None:
    (readme,) = [entry for entry in MANIFEST if entry.path == "README.md"]
    assert readme.required is True
    assert readme.compare_content is False


def test_manifest_covers_all_tracked_template_files() -> None:
    """Every tracked template file must be in the manifest or excluded.

    Runs only in the template repository itself; in a generated project this
    test file is deleted by cleanup.py anyway.
    """
    root = Path(__file__).resolve().parent.parent
    if pyproject_name(root) != TEMPLATE_KEBAB:
        pytest.skip("not running inside the template repository")
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    result = subprocess.run(  # noqa: S603  (fixed argv list, no shell)
        [git, "-C", str(root), "ls-files"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    tracked = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    manifest_paths = {entry.path for entry in MANIFEST}
    uncovered = [p for p in tracked if p not in manifest_paths and not is_excluded(p)]
    assert uncovered == [], f"add to MANIFEST or EXCLUDED_*: {uncovered}"
    missing = [p for p in sorted(manifest_paths) if not (root / p).is_file()]
    assert missing == [], f"manifest entries not in template: {missing}"


# --- diff output (VS Code) --------------------------------------------------------------


def test_diff_files_for_writes_pairs_and_skips_match_and_binary(tmp_path: Path) -> None:
    results = [
        Comparison(
            BaselineFile("a.md"), "a.md", "modified", template_norm="T-A\n", project_norm="P-A\n"
        ),
        Comparison(
            BaselineFile("b.md", strict=False),
            "b.md",
            "review",
            template_norm="T-B\n",
            project_norm="P-B\n",
        ),
        Comparison(
            BaselineFile("blob.bin"), "blob.bin", "modified", note=" (binary)"
        ),  # no norm texts
        Comparison(BaselineFile("c.md"), "c.md", "match"),  # unchanged; nothing to diff
    ]
    base = tmp_path / "out"
    pairs = diff_files_for(results, base)

    assert len(pairs) == 2
    (left_a, right_a), (_left_b, right_b) = pairs
    assert (left_a, right_a) == (base / "template" / "a.md", base / "project" / "a.md")
    assert left_a.read_text(encoding="utf-8") == "T-A\n"
    assert right_a.read_text(encoding="utf-8") == "P-A\n"
    assert right_b.read_text(encoding="utf-8") == "P-B\n"
    # The binary and matching entries produce no files.
    assert not (base / "template" / "blob.bin").exists()
    assert not (base / "template" / "c.md").exists()


def test_diff_files_for_uses_project_rel_for_the_project_side(tmp_path: Path) -> None:
    rel = f"docs/reference/{TEMPLATE_SNAKE}.md"
    results = [
        Comparison(
            BaselineFile(rel, required=False, strict=False),
            "docs/reference/my_proj.md",
            "review",
            template_norm="t\n",
            project_norm="p\n",
        ),
    ]
    ((left, right),) = diff_files_for(results, tmp_path)
    assert left == tmp_path / "template" / rel
    assert right == tmp_path / "project" / "docs/reference/my_proj.md"


def test_resolve_code_splits_an_explicit_tool() -> None:
    assert resolve_code("codium") == ["codium"]
    assert resolve_code("code --new-window") == ["code", "--new-window"]


def test_resolve_code_ignores_a_blank_override() -> None:
    # A blank override falls through to auto-detection of the 'code' CLI, which
    # is present or not depending on the environment.
    expected = ["code"] if shutil.which("code") else None
    assert resolve_code("   ") == expected
