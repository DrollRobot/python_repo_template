#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Create projects from this template and keep them in sync with it.

Runs from a template checkout (the directory holding ``template.toml``) and
acts on a child project given as an argument. Nothing here ships to children:
a child carries only ``.template.toml`` -- which template it came from, the
template commit it was last synced to, its token values, feature choices and
per-file overrides. It is read from the child's root, or failing that from
``.local/``, which children gitignore, so a child can keep even that out of its
history. Standard library only, run through uv (the metadata above asks it for
Python >= 3.14, the version this repo targets); git on PATH.

    uv run scripts/template_sync.py validate            # template self-check
    uv run scripts/template_sync.py new    ../child     # create a project
    uv run scripts/template_sync.py check  ../child     # report drift / updates
    uv run scripts/template_sync.py update ../child     # apply updates, bump the pin

Every tracked template file is classified in template.toml as one of:

    managed  the template owns it: always brought to the template's version;
             local edits are drift (an error).
    merge    both sides edit it: 3-way merged (git merge-file) against the
             template commit the child was last synced to; conflicts are
             written with markers, like git.
    seed     copied when the project is created and never looked at again.

plus ``exclude`` for files that never ship (this script, its test, the
manifest). A child may override any file's strategy in ``[overrides]``,
including ``ignore``.

Template text is normalised before it is compared with, copied to, or merged
into a child: ``<template-only>`` blocks are stripped (always; or, when tagged
``feature="x"``, only when the child declined feature x, and when tagged
``feature="!x"``, only when it took it; ``feature="x|y"`` keeps the block when
any listed term holds), and each ``[tokens]`` literal is replaced by the
child's value, in contents and paths. A token may derive its replacement from
the value (``transform = "snake"``, ``"kebab"`` or ``"upper"``), so one value
feeds several spellings; may place it in surrounding text (``format``); may map
named values to fixed text (``aliases``); and may be ``required``, so ``new``
never falls back to the template's own literal. A marker is a line holding
nothing but the tag and the file's comment syntax, e.g. ``# <template-only>``
or ``<!-- </template-only> -->``.

A file can land at a different path in a child than it has in the template:
``[paths]`` maps a template directory prefix to a child one, so the template
can keep, say, its own README while its children get a different one.

``[new]`` lists commands ``new`` runs in the project it creates, after staging
its files (``bootstrap``: lock dependencies, install hooks, ...), and lines it
prints at the end (``next_steps``).

Exit codes: 0 clean / applied; 1 drift, conflicts, (``check --strict``)
pending updates, or a failed ``new`` bootstrap step; 2 configuration or usage
error.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_NAME = "template.toml"
CHILD_CONFIG_NAME = ".template.toml"
# Where a child's config is looked for, first match wins. .local/ is gitignored
# (*.local* in .gitignore), for a child that commits nothing of the sync's own.
CHILD_CONFIG_PATHS = (CHILD_CONFIG_NAME, f".local/{CHILD_CONFIG_NAME}")
# manifest_format history. A manifest declares the format of everything it uses,
# so a tool too old for it stops at the number instead of misreading it. Every
# older format stays readable: `check` and `update` parse the manifest as it was
# at a child's pin, which may predate a bump. A new format's additions must be
# rejected in a manifest that declares an older one.
#   1  exclude, [tokens], [base], [features], <template-only> markers
#   2  [paths]; negated feature markers, <template-only feature="!name">
#   3  [tokens] tables (transform, required, format, aliases); [features.x] conflicts
#      and options; any-of markers, <template-only feature="a|b">; [new] bootstrap
#      and next_steps
MANIFEST_FORMAT = 3
STRATEGIES = ("managed", "merge", "seed")
RULE_KINDS = (*STRATEGIES, "exclude")
CHILD_STRATEGIES = (*STRATEGIES, "ignore")
GITLINK_MODE = "160000"
EXECUTABLE_MODE = "100755"
EXIT_OK, EXIT_DRIFT, EXIT_CONFIG = 0, 1, 2

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
# Leftover conflict markers from an earlier update. Only the labelled forms:
# a bare "=======" is a legitimate setext heading in Markdown.
_CONFLICT_RE = re.compile(rb"^(?:<{7}|>{7}) ", re.MULTILINE)


class ConfigError(Exception):
    """A fatal problem with a manifest, a child config, or a repository."""


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


@functools.cache
def git_exe() -> str:
    """Return the absolute path of the git executable.

    Raises:
        ConfigError: If git is not on PATH.
    """
    exe = shutil.which("git")
    if exe is None:
        raise ConfigError("git is not on PATH")
    return exe


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``git -C root args...`` and capture its text output.

    Args:
        root: Directory git runs in.
        *args: Arguments after ``git -C root``.
        check: Raise when git exits non-zero.

    Returns:
        The finished process.

    Raises:
        ConfigError: If ``check`` and git fails.
    """
    proc = subprocess.run(  # noqa: S603  (fixed argv list, no shell)
        [git_exe(), "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and proc.returncode != 0:
        raise ConfigError(f"git {' '.join(args)} failed in {root}:\n{proc.stderr.strip()}")
    return proc


def git_out(root: Path, *args: str) -> str:
    """Return the stripped stdout of a git command that must succeed."""
    return git(root, *args).stdout.strip()


def is_git_repo(root: Path) -> bool:
    """Whether ``root`` is inside a git working tree."""
    return git(root, "rev-parse", "--show-toplevel", check=False).returncode == 0


@dataclass(frozen=True)
class Tracked:
    """One path in a repository's index.

    Attributes:
        path: Repository-relative path, ``/``-separated.
        gitlink: Whether it is a submodule (mode 160000).
        executable: Whether git records it as executable (mode 100755).
    """

    path: str
    gitlink: bool
    executable: bool = False


def tracked_files(root: Path) -> list[Tracked]:
    """Return every path in the index of ``root``, with its git mode decoded."""
    out = git(root, "ls-files", "-s", "-z").stdout
    result = []
    for rec in out.split("\0"):
        if rec:
            meta, path = rec.split("\t", 1)
            mode = meta.split()[0]
            result.append(Tracked(path, mode == GITLINK_MODE, mode == EXECUTABLE_MODE))
    return result


def tracked_at(root: Path, commit: str) -> dict[str, bool]:
    """Return every path in ``commit``'s tree, mapped to whether it is a submodule."""
    out = git(root, "ls-tree", "-r", "-z", commit).stdout
    result = {}
    for rec in out.split("\0"):
        if rec:
            meta, path = rec.split("\t", 1)
            result[path] = meta.split()[0] == GITLINK_MODE
    return result


def show_at(root: Path, commit: str, path: str) -> bytes:
    """Return the bytes of ``path`` as it was at ``commit``.

    Raises:
        ConfigError: If git cannot show it.
    """
    proc = subprocess.run(  # noqa: S603  (fixed argv list, no shell)
        [git_exe(), "-C", str(root), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()
        raise ConfigError(f"git show {commit[:7]}:{path} failed:\n{detail}")
    return proc.stdout


def head_commit(root: Path) -> str:
    """Return the full hash of ``root``'s HEAD."""
    return git_out(root, "rev-parse", "HEAD")


def commit_exists(root: Path, sha: str) -> bool:
    """Whether ``sha`` names a commit in ``root``."""
    return git(root, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """Whether ``ancestor`` is an ancestor of (or equal to) ``descendant``."""
    proc = git(root, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    return proc.returncode == 0


def dirty_tracked(root: Path) -> list[str]:
    """Return tracked paths with uncommitted changes (untracked files don't count)."""
    out = git(root, "status", "--porcelain", "--untracked-files=no", "-z").stdout
    return [rec[3:] for rec in out.split("\0") if rec]


def origin_url(root: Path) -> str:
    """Return the URL of ``root``'s ``origin`` remote, or ``""`` if it has none."""
    proc = git(root, "remote", "get-url", "origin", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def gitlink_sha(root: Path, treeish: str, path: str) -> str | None:
    """Return the commit a submodule ``path`` points at in ``treeish``, or ``None``."""
    proc = git(root, "ls-tree", treeish, "--", path, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    meta = proc.stdout.split("\t", 1)[0].split()
    return meta[2] if meta[0] == GITLINK_MODE else None


def staged_gitlink_sha(root: Path, path: str) -> str | None:
    """Return the commit ``path`` is staged at as a submodule in ``root``'s index, or ``None``.

    The index rather than HEAD, so a project fresh from `new` -- submodules
    added, nothing committed yet -- doesn't read as missing them.
    """
    proc = git(root, "ls-files", "-s", "--", path, check=False)
    meta = proc.stdout.split("\t", 1)[0].split()
    if proc.returncode == 0 and len(meta) > 1 and meta[0] == GITLINK_MODE:
        return meta[1]
    return None


def submodule_entries(root: Path) -> list[tuple[str, str, str]]:
    """Return ``(name, path, url)`` for every entry in ``root``'s ``.gitmodules``."""
    if not (root / ".gitmodules").is_file():
        return []
    proc = git(
        root,
        "config",
        "-f",
        ".gitmodules",
        "--get-regexp",
        r"^submodule\..*\.(path|url)$",
        check=False,
    )
    entries: dict[str, dict[str, str]] = {}
    for line in proc.stdout.splitlines():
        m = re.match(r"^submodule\.(.+)\.(path|url) (.*)$", line)
        if m:
            entries.setdefault(m.group(1), {})[m.group(2)] = m.group(3)
    return [(n, e["path"], e["url"]) for n, e in entries.items() if "path" in e and "url" in e]


# ---------------------------------------------------------------------------
# Normalisation: <template-only> markers and tokens
# ---------------------------------------------------------------------------

_FEATURE_TERM = r"!?[A-Za-z_][A-Za-z0-9_]*"
_MARKER_OPEN_RE = re.compile(
    rf'^<template-only(?:\s+feature="({_FEATURE_TERM}(?:\|{_FEATURE_TERM})*)")?>$'
)
_MARKER_CLOSE = "</template-only>"
_COMMENT_LEAD_RE = re.compile(r"^(?:#+|//+|<!--|/\*+|\*+|;+|--)\s*")
_COMMENT_TAIL_RE = re.compile(r"\s*(?:-->|\*/)$")


def marker_on(line: str) -> tuple[str, str | None] | None:
    """Return ``("open", feature)`` or ``("close", None)`` if the whole line is a marker.

    ``feature`` is the tag's feature expression -- a name, ``!``-prefixed when
    negated, or several such terms joined by ``|`` -- or ``None`` for an
    untagged block. The line may carry the file's comment syntax and nothing
    else, so prose that merely mentions a marker (in backticks, mid-sentence)
    never counts.

    Args:
        line: One line of text, without its newline.

    Returns:
        The marker's kind and feature expression, or ``None`` for any other line.
    """
    core = _COMMENT_LEAD_RE.sub("", line.strip(), count=1)
    core = _COMMENT_TAIL_RE.sub("", core, count=1).strip()
    if core == _MARKER_CLOSE:
        return ("close", None)
    m = _MARKER_OPEN_RE.match(core)
    return ("open", m.group(1)) if m else None


def feature_terms(feature: str) -> list[str]:
    """Split a marker's feature expression into its terms (``"!a|b"`` -> ``["!a", "b"]``)."""
    return feature.split("|")


def block_kept(feature: str | None, enabled: frozenset[str]) -> bool:
    """Whether a child with ``enabled`` features keeps a block tagged ``feature``.

    An untagged block is never kept. A tagged block is kept when any of its
    terms holds: ``x`` when x is enabled, ``!x`` when it is not.

    Args:
        feature: The block's feature expression, or ``None`` if untagged.
        enabled: The child's effective features.

    Returns:
        True to keep the block's lines, False to drop them.
    """
    if feature is None:
        return False
    for term in feature_terms(feature):
        if term.startswith("!"):
            if term[1:] not in enabled:
                return True
        elif term in enabled:
            return True
    return False


def apply_markers(text: str, enabled: frozenset[str] | None, *, where: str) -> tuple[str, set[str]]:
    """Strip marker lines and the template-only blocks that don't apply to a child.

    An untagged block is always dropped; a tagged block is dropped unless
    :func:`block_kept` says otherwise. ``enabled=None`` is validation mode: the
    text is returned unchanged, but balance is still checked and the set of
    feature expressions used is returned.

    Args:
        text: File text with LF line endings.
        enabled: The child's effective features, or ``None`` to validate only.
        where: Label for error messages, usually the file's path.

    Returns:
        The resulting text and the set of feature expressions seen.

    Raises:
        ConfigError: If markers are unbalanced or nested.
    """
    out: list[str] = []
    seen: set[str] = set()
    open_at: int | None = None
    open_feature: str | None = None
    swallow_blank = False  # a dropped block sat between blank lines: keep only one
    for n, line in enumerate(text.split("\n"), 1):
        marker = marker_on(line)
        if marker is None:
            in_block = open_at is not None
            keep_block = enabled is None or block_kept(open_feature, enabled)
            if not in_block or keep_block:
                if not (swallow_blank and line == ""):
                    out.append(line)
                swallow_blank = False
            continue
        kind, feature = marker
        if kind == "open":
            if open_at is not None:
                raise ConfigError(
                    f"{where}:{n}: <template-only> opened inside the block opened at line {open_at}"
                )
            open_at, open_feature = n, feature
            if feature:
                seen.add(feature)
        else:
            if open_at is None:
                raise ConfigError(f"{where}:{n}: </template-only> with no open block")
            dropped = enabled is not None and not block_kept(open_feature, enabled)
            swallow_blank = dropped and (not out or out[-1] == "")
            open_at = open_feature = None
        if enabled is None:
            out.append(line)
    if open_at is not None:
        raise ConfigError(f"{where}:{open_at}: <template-only> block is never closed")
    return "\n".join(out), seen


def substitute(text: str, replacements: dict[str, str]) -> str:
    """Replace every token literal in one pass, longest literal first.

    Single pass: a replacement is never itself substituted again.

    Args:
        text: Text to rewrite.
        replacements: Template literal -> what the child gets instead.

    Returns:
        The rewritten text.
    """
    if not replacements:
        return text
    pattern = "|".join(re.escape(lit) for lit in sorted(replacements, key=len, reverse=True))
    return re.sub(pattern, lambda m: replacements[m.group(0)], text)


def is_binary(data: bytes) -> bool:
    """Whether ``data`` looks binary (a NUL byte in its first 8 KiB)."""
    return b"\0" in data[:8192]


def normalise(
    data: bytes,
    replacements: dict[str, str],
    enabled: frozenset[str],
    *,
    where: str,
) -> bytes:
    """Turn template bytes into what the child should hold (markers, tokens, LF).

    Args:
        data: The template file's bytes.
        replacements: Token literal -> child replacement (:meth:`Manifest.replacements`).
        enabled: The child's effective features.
        where: Label for error messages.

    Returns:
        The child's version of the file; binary files pass through unchanged.

    Raises:
        ConfigError: If the text is not UTF-8 or its markers are unbalanced.
    """
    if is_binary(data):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ConfigError(f"{where}: not UTF-8 ({e})") from None
    text = text.replace("\r\n", "\n")
    text, _ = apply_markers(text, enabled, where=where)
    if text.endswith("\n"):
        # A stripped block can leave blank lines at EOF; keep the single
        # newline that end-of-file-fixer enforces on the child's side.
        text = text.rstrip("\n") + "\n"
    return substitute(text, replacements).encode("utf-8")


def map_path(path: str, manifest: Manifest, replacements: dict[str, str]) -> str:
    """Map a template path to its child path: longest ``[paths]`` prefix, then tokens."""
    for prefix, target in manifest.paths.items():
        if path.startswith(prefix):
            path = target + path[len(prefix) :]
            break
    return substitute(path, replacements)


def path_clashes(pairs: Iterable[tuple[str, str]]) -> list[str]:
    """Return one message per child path that several (template, child) path pairs land on."""
    sources: dict[str, list[str]] = {}
    for path, child_path in pairs:
        sources.setdefault(child_path, []).append(path)
    return [
        f"{child_path}: shipped from several template files: {', '.join(paths)}"
        for child_path, paths in sources.items()
        if len(paths) > 1
    ]


# ---------------------------------------------------------------------------
# Token transforms
# ---------------------------------------------------------------------------

# Each transform's result must match its pattern: a name usable as a Python
# package (snake), a distribution or command name (kebab), or an environment
# variable prefix (upper).
_TRANSFORM_CHECKS = {
    "snake": re.compile(r"^[a-z][a-z0-9_]*$"),
    "kebab": re.compile(r"^[a-z][a-z0-9-]*$"),
    "upper": re.compile(r"^[A-Z][A-Z0-9_]*$"),
}
TRANSFORMS = tuple(_TRANSFORM_CHECKS)


def transform_value(value: str, transform: str | None) -> str:
    """Derive one spelling of a value: ``My Widget`` -> ``my_widget``, ``my-widget``, ``MY_WIDGET``.

    Spaces, hyphens and underscores all separate words, so any of the three
    spellings (or a free-form name) yields the same results.

    Args:
        value: The child's value, as typed.
        transform: ``snake``, ``kebab``, ``upper``, or ``None`` for the value as-is.

    Returns:
        The derived spelling.

    Raises:
        ValueError: If the derived spelling is not a valid name of its kind.
    """
    if transform is None:
        return value
    words = [w for w in re.split(r"[\s_-]+", value.strip()) if w]
    derived = ("-" if transform == "kebab" else "_").join(words).lower()
    if transform == "upper":
        derived = derived.upper()
    if not _TRANSFORM_CHECKS[transform].match(derived):
        raise ValueError(
            f"{value!r} gives the {transform} name {derived!r}: use letters, digits and "
            "separators, starting with a letter"
        )
    return derived


# ---------------------------------------------------------------------------
# template.toml
# ---------------------------------------------------------------------------


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a path glob: ``*``/``?`` stop at ``/``; ``**`` spans directories."""
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


@dataclass(frozen=True)
class Token:
    """One ``[tokens]`` entry.

    Attributes:
        key: The child ``[values]`` key it reads.
        transform: How the value is spelled in place of this literal, if not as-is.
        required: Whether ``new`` must be given a value (no fallback to the literal).
        format: Text around the value, ``{}`` marking where it goes (``"=={}.*"``).
        aliases: Named values -> the replacement text they stand for, used as-is.
    """

    key: str
    transform: str | None = None
    required: bool = False
    format: str | None = None
    aliases: dict[str, str] = field(default_factory=dict)

    def render(self, value: str) -> str:
        """Return what replaces this token's literal for ``value``.

        Args:
            value: The child's value for :attr:`key`.

        Returns:
            The alias text if ``value`` names an alias, else the value transformed
            and placed into :attr:`format`.

        Raises:
            ValueError: If the transformed value is not a valid name of its kind.
        """
        if value in self.aliases:
            return self.aliases[value]
        derived = transform_value(value, self.transform)
        return self.format.replace("{}", derived) if self.format else derived

    def unformat(self, literal: str) -> str | None:
        """Return the value whose rendering is ``literal``, or ``None`` if it can't be one."""
        if self.format is None:
            return literal
        prefix, suffix = self.format.split("{}")
        if len(literal) < len(prefix) + len(suffix):
            return None
        if not (literal.startswith(prefix) and literal.endswith(suffix)):
            return None
        return literal[len(prefix) : len(literal) - len(suffix)]


@dataclass(frozen=True)
class Feature:
    """One ``[features.<name>]`` table.

    Attributes:
        name: The feature's name.
        description: One line shown in prompts and in the child's config.
        default: Whether it is on when a child doesn't choose.
        requires: Features that must be on for this one to be.
        conflicts: Features that can't be on together with this one (symmetric).
        options: Features of which exactly one is on while this one is. Each
            requires this one and conflicts with the others; the first takes
            this one's default, the rest default to off. Asked about as one
            choice among them, or none (this feature off).
    """

    name: str
    description: str
    default: bool
    requires: tuple[str, ...]
    conflicts: tuple[str, ...] = ()
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rule:
    """One pattern in ``exclude`` or in a ``managed``/``merge``/``seed`` list.

    Attributes:
        pattern: The glob as written.
        kind: ``managed``, ``merge``, ``seed`` or ``exclude``.
        feature: The feature whose table lists it, or ``None`` for ``[base]`` and ``exclude``.
        regex: The compiled glob.
    """

    pattern: str
    kind: str
    feature: str | None
    regex: re.Pattern[str] = field(repr=False, compare=False)

    def matches(self, path: str) -> bool:
        """Whether this rule's glob matches ``path``."""
        return self.regex.match(path) is not None

    @property
    def where(self) -> str:
        """Where the rule is written, for messages: ``[base].managed '*.yml'``."""
        if self.kind == "exclude":
            table = "exclude"
        elif self.feature is None:
            table = f"[base].{self.kind}"
        else:
            table = f"[features.{self.feature}].{self.kind}"
        return f"{table} {self.pattern!r}"


@dataclass(frozen=True)
class BootstrapStep:
    """One command in ``[new].bootstrap``, run in a newly created project.

    Attributes:
        run: The command and its arguments; no shell.
        stdout: A project-relative file the command's output is written to, or
            ``None`` to show the output.
    """

    run: tuple[str, ...]
    stdout: str | None = None

    def __str__(self) -> str:
        """The command as it would be typed in a shell."""
        line = subprocess.list2cmdline(self.run)
        return f"{line} > {self.stdout}" if self.stdout else line


@dataclass(frozen=True)
class Manifest:
    """A parsed ``template.toml``.

    Attributes:
        root: The template checkout.
        format: Its ``manifest_format``.
        tokens: Template literal -> the token it is.
        paths: Template directory prefix -> child directory prefix, longest first.
        features: Feature name -> feature, in declaration order.
        rules: Every classification rule.
        bootstrap: Commands ``new`` runs in the project it creates, in order.
        next_steps: Lines ``new`` prints when it is done.
    """

    root: Path
    format: int
    tokens: dict[str, Token]
    paths: dict[str, str]
    features: dict[str, Feature]
    rules: tuple[Rule, ...]
    bootstrap: tuple[BootstrapStep, ...] = ()
    next_steps: tuple[str, ...] = ()

    @property
    def value_keys(self) -> list[str]:
        """Every ``[values]`` key a child needs, sorted."""
        return sorted({t.key for t in self.tokens.values()})

    @property
    def required_keys(self) -> set[str]:
        """The value keys any of whose tokens is ``required``."""
        return {t.key for t in self.tokens.values() if t.required}

    def placeholder_values(self) -> dict[str, str]:
        """Map each value key to the template's own value: its first literal, unformatted.

        Substituting these changes nothing, as long as every literal of a key
        is a rendering of that value (``validate`` checks this).
        """
        values: dict[str, str] = {}
        for literal, token in self.tokens.items():
            if token.key not in values:
                values[token.key] = token.unformat(literal) or literal
        return values

    def option_of(self, name: str) -> str | None:
        """Return the feature whose ``options`` list ``name``, if any."""
        return next((f.name for f in self.features.values() if name in f.options), None)

    def keys_in(self, texts: list[str]) -> set[str]:
        """Return the value keys with a token literal in any of ``texts``."""
        return {t.key for literal, t in self.tokens.items() if any(literal in s for s in texts)}

    def aliases_of(self, key: str) -> list[str]:
        """Return the alias names the tokens of ``key`` declare, sorted."""
        return sorted({a for t in self.tokens.values() if t.key == key for a in t.aliases})

    def replacements(self, values: dict[str, str]) -> dict[str, str]:
        """Map each token literal to what a child with ``values`` gets instead.

        Args:
            values: The child's ``[values]``.

        Returns:
            Template literal -> replacement text (:meth:`Token.render`).

        Raises:
            ConfigError: If a value gives an invalid name under a transform.
        """
        result: dict[str, str] = {}
        for literal, token in self.tokens.items():
            try:
                result[literal] = token.render(values[token.key])
            except ValueError as e:
                raise ConfigError(f"[values] {token.key}: {e}") from None
        return result


def _table(raw: object, where: str) -> dict[str, Any]:
    """Return ``raw`` as a TOML table, or raise naming ``where``."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a table")
    return raw


def _str_list(raw: object, where: str) -> list[str]:
    """Return ``raw`` as a list of non-empty strings, or raise naming ``where``."""
    if not isinstance(raw, list) or not all(isinstance(x, str) and x for x in raw):
        raise ConfigError(f"{where}: expected a list of non-empty strings")
    return raw


def _reject_unknown(raw: dict[str, Any], allowed: Iterable[str], where: str) -> None:
    """Raise if ``raw`` has keys outside ``allowed``."""
    allowed = list(allowed)
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(allowed)}"
        )


_DIR_PREFIX_RE = re.compile(r"^(?:[^/]+/)+$")


def _is_dir_prefix(s: str) -> bool:
    """Whether ``s`` is a relative directory path ending in ``/``, without ``.``/``..``."""
    return bool(_DIR_PREFIX_RE.match(s)) and not {".", ".."} & set(s.split("/"))


def load_manifest(root: Path) -> Manifest:
    """Read and parse ``root/template.toml``.

    Raises:
        ConfigError: If it is missing or invalid.
    """
    path = root / MANIFEST_NAME
    if not path.is_file():
        raise ConfigError(f"{path}: not found -- is {root} a template checkout?")
    return parse_manifest(path.read_text("utf-8"), root, where=MANIFEST_NAME)


def _parse_token(literal: str, raw: object, fmt: int, where: str) -> Token:
    """Parse one ``[tokens]`` entry: a key name, or (format 3) a table."""
    if isinstance(raw, str):
        if not literal or not _IDENT_RE.match(raw):
            raise ConfigError(
                f"{where}: [tokens] {literal!r}: value must be an identifier naming a "
                "child [values] key"
            )
        return Token(raw)
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: [tokens] {literal!r}: expected a key name or a table")
    if fmt < 3:
        raise ConfigError(f"{where}: [tokens] {literal!r}: tables need manifest_format = 3")
    tw = f"{where}: [tokens] {literal!r}"
    _reject_unknown(raw, ("key", "transform", "required", "format", "aliases"), tw)
    key, transform, required = raw.get("key"), raw.get("transform"), raw.get("required", False)
    form, aliases = raw.get("format"), raw.get("aliases", {})
    if not literal or not isinstance(key, str) or not _IDENT_RE.match(key):
        raise ConfigError(f"{tw}: key must be an identifier naming a child [values] key")
    if transform is not None and transform not in TRANSFORMS:
        raise ConfigError(f"{tw}: transform must be one of {', '.join(TRANSFORMS)}")
    if not isinstance(required, bool):
        raise ConfigError(f"{tw}: required must be a boolean")
    if form is not None and (not isinstance(form, str) or form.count("{}") != 1):
        raise ConfigError(f'{tw}: format must be a string holding "{{}}" exactly once')
    valid_aliases = isinstance(aliases, dict) and all(
        isinstance(k, str) and k and isinstance(v, str) for k, v in aliases.items()
    )
    if not valid_aliases:
        raise ConfigError(f"{tw}: aliases must be a table of names to strings")
    return Token(key, transform, required, form, dict(aliases))


def parse_manifest(text: str, root: Path, *, where: str) -> Manifest:
    """Parse ``template.toml`` text.

    Args:
        text: The manifest's contents.
        root: The template checkout it belongs to.
        where: Label for error messages.

    Returns:
        The parsed manifest.

    Raises:
        ConfigError: If anything in it is invalid, or needs a newer format than declared.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{where}: {e}") from None
    w = where
    # The format before anything else: a newer manifest may have keys this tool doesn't know.
    fmt = raw.get("manifest_format")
    if type(fmt) is not int or fmt < 1:
        raise ConfigError(f"{w}: manifest_format must be a positive integer, not {fmt!r}")
    if fmt > MANIFEST_FORMAT:
        raise ConfigError(
            f"{w}: manifest_format = {fmt}, but this tool reads formats up to {MANIFEST_FORMAT}; "
            "use the sync tool from the same template checkout"
        )
    _reject_unknown(
        raw, ("manifest_format", "tokens", "paths", "exclude", "base", "features", "new"), w
    )
    if "paths" in raw and fmt < 2:
        raise ConfigError(f"{w}: [paths] needs manifest_format = 2")
    if "new" in raw and fmt < 3:
        raise ConfigError(f"{w}: [new] needs manifest_format = 3")
    bootstrap, next_steps = _parse_new(_table(raw.get("new", {}), f"{w}: [new]"), w)

    tokens = {
        literal: _parse_token(literal, value, fmt, w)
        for literal, value in _table(raw.get("tokens", {}), f"{w}: [tokens]").items()
    }

    paths: dict[str, str] = {}
    for prefix, target in _table(raw.get("paths", {}), f"{w}: [paths]").items():
        target_ok = isinstance(target, str) and (target == "" or _is_dir_prefix(target))
        if not _is_dir_prefix(prefix) or not target_ok:
            raise ConfigError(
                f'{w}: [paths] {prefix!r}: keys must be relative directories ending in "/", '
                f'and values the same or "" for the child\'s root'
            )
        paths[prefix] = target
    paths = dict(sorted(paths.items(), key=lambda kv: len(kv[0]), reverse=True))

    rules: list[Rule] = [
        Rule(pat, "exclude", None, glob_to_regex(pat))
        for pat in _str_list(raw.get("exclude", []), f"{w}: exclude")
    ]
    base = _table(raw.get("base", {}), f"{w}: [base]")
    _reject_unknown(base, STRATEGIES, f"{w}: [base]")
    for kind in STRATEGIES:
        for pat in _str_list(base.get(kind, []), f"{w}: [base].{kind}"):
            rules.append(Rule(pat, kind, None, glob_to_regex(pat)))

    features: dict[str, Feature] = {}
    with_default: set[str] = set()
    feature_keys = ("description", "default", "requires", "conflicts", "options", *STRATEGIES)
    for name, value in _table(raw.get("features", {}), f"{w}: [features]").items():
        fw = f"{w}: [features.{name}]"
        if not _IDENT_RE.match(name):
            raise ConfigError(f"{fw}: feature names must be identifiers")
        tbl = _table(value, fw)
        _reject_unknown(tbl, feature_keys, fw)
        description = tbl.get("description", "")
        default = tbl.get("default", True)
        if not isinstance(description, str) or not isinstance(default, bool):
            raise ConfigError(f"{fw}: description must be a string and default a boolean")
        for key in ("conflicts", "options"):
            if key in tbl and fmt < 3:
                raise ConfigError(f"{fw}: {key} needs manifest_format = 3")
        if "default" in tbl:
            with_default.add(name)
        requires = tuple(_str_list(tbl.get("requires", []), f"{fw}.requires"))
        conflicts = tuple(_str_list(tbl.get("conflicts", []), f"{fw}.conflicts"))
        options = tuple(_str_list(tbl.get("options", []), f"{fw}.options"))
        features[name] = Feature(name, description, default, requires, conflicts, options)
        for kind in STRATEGIES:
            for pat in _str_list(tbl.get(kind, []), f"{fw}.{kind}"):
                rules.append(Rule(pat, kind, name, glob_to_regex(pat)))

    features = _expand_options(features, with_default, w)
    for f in features.values():
        for req in f.requires:
            if req not in features:
                raise ConfigError(f"{w}: [features.{f.name}] requires unknown feature {req!r}")
        for other in f.conflicts:
            if other not in features or other == f.name:
                raise ConfigError(
                    f"{w}: [features.{f.name}] conflicts with {other!r}, which is not another "
                    "declared feature"
                )
    _check_no_cycles(features, w)
    return Manifest(
        root,
        fmt,
        tokens,
        paths,
        _symmetric_conflicts(features),
        tuple(rules),
        bootstrap,
        next_steps,
    )


def _parse_new(
    new: dict[str, Any], where: str
) -> tuple[tuple[BootstrapStep, ...], tuple[str, ...]]:
    """Parse the ``[new]`` table: ``bootstrap`` steps and ``next_steps`` lines."""
    nw = f"{where}: [new]"
    _reject_unknown(new, ("bootstrap", "next_steps"), nw)
    raw_steps = new.get("bootstrap", [])
    if not isinstance(raw_steps, list):
        raise ConfigError(f"{nw}.bootstrap: expected a list of tables")
    steps: list[BootstrapStep] = []
    for i, raw in enumerate(raw_steps):
        sw = f"{nw}.bootstrap[{i}]"
        step = _table(raw, sw)
        _reject_unknown(step, ("run", "stdout"), sw)
        run = _str_list(step.get("run"), f"{sw}.run")
        if not run:
            raise ConfigError(f"{sw}.run: expected a command and its arguments")
        stdout = step.get("stdout")
        stdout_ok = stdout is None or (
            isinstance(stdout, str)
            and bool(stdout)
            and not stdout.startswith("/")
            and not {"", ".", ".."} & set(stdout.replace("\\", "/").split("/"))
            and ":" not in stdout
        )
        if not stdout_ok:
            raise ConfigError(f"{sw}.stdout: expected a file path relative to the project")
        steps.append(BootstrapStep(tuple(run), stdout))
    next_steps = tuple(_str_list(new.get("next_steps", []), f"{nw}.next_steps"))
    return tuple(steps), next_steps


def _expand_options(
    features: dict[str, Feature], with_default: set[str], where: str
) -> dict[str, Feature]:
    """Check each ``options`` list and write out what it implies for its options.

    Each option requires the feature that lists it and conflicts with its
    other options. The first option takes that feature's default, so a project
    that keeps it gets the first option; the rest default to off.

    Args:
        features: The declared features.
        with_default: The features that declare ``default`` themselves.
        where: Label for error messages.

    Returns:
        ``features``, with the options' requires, conflicts and defaults filled in.

    Raises:
        ConfigError: If an options list is invalid.
    """
    owner: dict[str, str] = {}
    for f in features.values():
        if not f.options:
            continue
        fw = f"{where}: [features.{f.name}]"
        if (
            len(f.options) < 2
            or len(set(f.options)) < len(f.options)
            or not all(o in features and o != f.name for o in f.options)
        ):
            raise ConfigError(f"{fw}.options: list two or more other declared features")
        for o in f.options:
            ow = f"{where}: [features.{o}]"
            if o in owner:
                raise ConfigError(f"{ow} is an option of both {owner[o]} and {f.name}")
            if features[o].options:
                raise ConfigError(f"{ow} is an option of {f.name}, so it can't have options")
            if o in with_default:
                raise ConfigError(
                    f"{ow} is an option of {f.name}, whose default decides; remove its default"
                )
            owner[o] = f.name
    expanded = dict(features)
    for o, name in owner.items():
        options = features[name].options
        expanded[o] = dataclasses.replace(
            features[o],
            default=features[name].default if o == options[0] else False,
            requires=tuple(dict.fromkeys((*features[o].requires, name))),
            conflicts=tuple(
                dict.fromkeys((*features[o].conflicts, *(x for x in options if x != o)))
            ),
        )
    return expanded


def _symmetric_conflicts(features: dict[str, Feature]) -> dict[str, Feature]:
    """Return ``features`` with each conflict recorded on both of its sides."""
    pairs = {(f.name, other) for f in features.values() for other in f.conflicts}
    pairs |= {(b, a) for a, b in pairs}
    return {
        name: dataclasses.replace(f, conflicts=tuple(o for o in features if (name, o) in pairs))
        for name, f in features.items()
    }


def _check_no_cycles(features: dict[str, Feature], where: str) -> None:
    """Raise if ``requires`` links form a cycle."""
    state: dict[str, int] = {}  # 1 = on the current path, 2 = done

    def visit(name: str, path: list[str]) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            cycle = [*path[path.index(name) :], name]
            raise ConfigError(f"{where}: feature requires cycle: {' -> '.join(cycle)}")
        state[name] = 1
        for req in features[name].requires:
            visit(req, [*path, name])
        state[name] = 2

    for name in features:
        visit(name, [])


@dataclass(frozen=True)
class Classified:
    """The rule a tracked path falls under.

    Attributes:
        kind: ``managed``, ``merge``, ``seed`` or ``exclude``.
        feature: The rule's feature, or ``None``.
        rule: The rule itself.
    """

    kind: str
    feature: str | None
    rule: Rule


def classify_one(manifest: Manifest, path: str) -> Classified | None:
    """Return the single rule matching ``path``, or ``None`` if none does.

    Raises:
        ConfigError: If several rules match.
    """
    hits = [r for r in manifest.rules if r.matches(path)]
    if len(hits) > 1:
        raise ConfigError(f"{path}: matched by several rules: " + "; ".join(r.where for r in hits))
    if not hits:
        return None
    return Classified(hits[0].kind, hits[0].feature, hits[0])


def classify(manifest: Manifest, paths: Iterable[str]) -> dict[str, Classified]:
    """Classify every path, reporting all unclassified or ambiguous ones at once.

    Raises:
        ConfigError: Listing every path that matches no rule or several.
    """
    result: dict[str, Classified] = {}
    problems: list[str] = []
    for path in paths:
        try:
            cl = classify_one(manifest, path)
        except ConfigError as e:
            problems.append(str(e))
            continue
        if cl is None:
            problems.append(
                f"{path}: not classified -- add it to {MANIFEST_NAME} "
                "(managed, merge, seed, or exclude)"
            )
        else:
            result[path] = cl
    if problems:
        raise ConfigError("\n".join(problems))
    return result


def effective_features(
    manifest: Manifest, chosen: dict[str, bool]
) -> tuple[frozenset[str], list[str]]:
    """Resolve a child's feature choices into the set of features that are on.

    Unchosen features take their defaults, except that a default yields to an
    explicit choice it conflicts with, and a feature with ``options`` is on
    when one of them is chosen. Then ``requires`` turns off any feature whose
    requirement is off.

    Args:
        manifest: The template's manifest.
        chosen: The child's explicit choices.

    Returns:
        The features that are on, and notes on any that were turned off.

    Raises:
        ConfigError: If two conflicting features are both on, or a feature with
            ``options`` is on with none of them.
    """
    on = {name: f.default for name, f in manifest.features.items()}
    on.update(chosen)
    for name, f in manifest.features.items():
        if name not in chosen and any(chosen.get(o) for o in f.options):
            on[name] = True
    notes: list[str] = []
    for name, f in manifest.features.items():
        winners = [c for c in f.conflicts if chosen.get(c)]
        if name not in chosen and on[name] and winners:
            on[name] = False
            notes.append(f"feature {name} turned off: it conflicts with {', '.join(winners)}")
    changed = True
    while changed:
        changed = False
        for name, f in manifest.features.items():
            missing = [r for r in f.requires if not on[r]]
            if on[name] and missing:
                on[name] = False
                changed = True
                notes.append(f"feature {name} turned off: it requires {', '.join(missing)}")
    clashes = sorted(
        {tuple(sorted((a, b))) for a, f in manifest.features.items() for b in f.conflicts}
    )
    both_on = [f"{a} and {b}" for a, b in clashes if on[a] and on[b]]
    if both_on:
        raise ConfigError(
            "conflicting features are both on: " + "; ".join(both_on) + " -- turn one off"
        )
    empty = [
        name
        for name, f in manifest.features.items()
        if f.options and on[name] and not any(on[o] for o in f.options)
    ]
    if empty:
        raise ConfigError(
            "; ".join(f"{name} is on, but none of its options is" for name in empty)
            + " -- turn one option on, or the feature off"
        )
    return frozenset(n for n, v in on.items() if v), notes


# ---------------------------------------------------------------------------
# .template.toml
# ---------------------------------------------------------------------------

FEATURES_COMMENT = (
    "# Optional template features. A declined feature's files are not tracked;",
    "# switching one after an update has applied it is largely manual.",
)
NO_FEATURES_COMMENT = "# (the template declares no optional features)"


@dataclass(frozen=True)
class ChildConfig:
    """A parsed child ``.template.toml``.

    Attributes:
        root: The child project.
        config: The :data:`CHILD_CONFIG_PATHS` entry it was read from.
        url: The template URL it records.
        commit: The template commit the child is pinned to.
        values: Token values.
        features: Explicit feature choices.
        overrides: Per-file strategy overrides.
    """

    root: Path
    config: str
    url: str
    commit: str
    values: dict[str, str]
    features: dict[str, bool]
    overrides: dict[str, str]


def child_config_skeleton(manifest: Manifest) -> str:
    """Return a ``.template.toml`` to fill in by hand, for a project made without `new`."""
    return render_child_config(
        manifest,
        url="",
        commit="<template commit this project was created from>",
        values=dict.fromkeys(manifest.value_keys, ""),
        features={},
    )


def load_child_config(root: Path, manifest: Manifest) -> ChildConfig:
    """Read the child's ``.template.toml`` from the first of :data:`CHILD_CONFIG_PATHS`.

    Args:
        root: The child project.
        manifest: The template's manifest, which says what keys are valid.

    Returns:
        The parsed config.

    Raises:
        ConfigError: If none is found (the message carries a skeleton) or it is invalid.
    """
    config = next((rel for rel in CHILD_CONFIG_PATHS if (root / rel).is_file()), None)
    if config is None:
        raise ConfigError(
            f"{root}: no {' or '.join(CHILD_CONFIG_PATHS)}. Was this project created with "
            f"`template_sync.py new`? If not, write one by hand:\n\n"
            f"{child_config_skeleton(manifest)}"
        )
    path = root / config
    try:
        raw = tomllib.loads(path.read_text("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    w = str(path)
    _reject_unknown(raw, ("template", "values", "features", "overrides"), w)
    t = _table(raw.get("template", {}), f"{w}: [template]")
    _reject_unknown(t, ("url", "commit"), f"{w}: [template]")
    url = t.get("url", "")
    commit = t.get("commit", "")
    if not isinstance(url, str) or not isinstance(commit, str) or not _HEX_RE.match(commit):
        raise ConfigError(f"{w}: [template] needs url (string) and commit (a template commit hash)")

    values = _table(raw.get("values", {}), f"{w}: [values]")
    missing = [k for k in manifest.value_keys if k not in values]
    if missing:
        raise ConfigError(
            f"{w}: [values] missing {', '.join(missing)} (every template token needs a value)"
        )
    _reject_unknown(values, manifest.value_keys, f"{w}: [values]")
    if not all(isinstance(v, str) for v in values.values()):
        raise ConfigError(f"{w}: [values] must all be strings")

    features = _table(raw.get("features", {}), f"{w}: [features]")
    _reject_unknown(features, manifest.features, f"{w}: [features]")
    if not all(isinstance(v, bool) for v in features.values()):
        raise ConfigError(f"{w}: [features] must all be booleans")

    overrides = _table(raw.get("overrides", {}), f"{w}: [overrides]")
    for p, s in overrides.items():
        if s not in CHILD_STRATEGIES:
            raise ConfigError(
                f"{w}: [overrides] {p!r} = {s!r}; expected one of {', '.join(CHILD_STRATEGIES)}"
            )
    return ChildConfig(root, config, url, commit, dict(values), dict(features), dict(overrides))


def toml_str(s: str) -> str:
    """Quote ``s`` as a TOML basic string."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_child_config(
    manifest: Manifest,
    *,
    url: str,
    commit: str,
    values: dict[str, str],
    features: dict[str, bool],
) -> str:
    """Render a child's ``.template.toml``.

    Args:
        manifest: The template's manifest.
        url: The template URL to record.
        commit: The template commit to pin.
        values: Token values, one per :attr:`Manifest.value_keys`.
        features: Feature choices; missing ones are written as their defaults.

    Returns:
        The file's text.
    """
    lines = [
        f"# {CHILD_CONFIG_NAME} -- which template this project came from and how it is",
        "# kept in sync. Written by the template's scripts/template_sync.py; `update`",
        "# moves `commit`, and adds options the template has gained to [features].",
        "# Edit [values], [features] and [overrides] by hand.",
        "#",
        "# To bring in the template's later changes, commit your work, then run its sync",
        "# tool from a checkout of the template (here, ../template):",
        "#",
        "#   git -C ../template pull",
        "#   uv run ../template/scripts/template_sync.py check .     # what would change",
        "#   uv run ../template/scripts/template_sync.py update .    # apply it, then review",
        "#",
        "# To keep this file out of the project's history, `git rm --cached` it and move",
        "# it to .local/, which .gitignore ignores; a fresh clone then needs its own copy.",
        "#",
        "# This is the only file in the project that refers to the template. To cut the",
        "# project loose from it, delete this file.",
        "",
        "[template]",
        f"url = {toml_str(url)}",
        f"commit = {toml_str(commit)}",
        "",
        "[values]",
        "# One entry per token the template substitutes, in file contents and paths.",
        *[f"{k} = {toml_str(values[k])}" for k in manifest.value_keys],
        "",
        "[features]",
    ]
    if manifest.features:
        lines.extend(FEATURES_COMMENT)
        for name, f in manifest.features.items():
            if f.description:
                lines.append(f"# {f.description}")
            lines.append(f"{name} = {'true' if features.get(name, f.default) else 'false'}")
    else:
        lines.append(NO_FEATURES_COMMENT)
    lines += [
        "",
        "[overrides]",
        "# Per-file strategy overrides, keyed by path as listed in the template's",
        "# template.toml: managed, merge, seed, or ignore. For example:",
        '#   ".gitignore" = "merge"    # I add my own ignores; merge template changes in',
        '#   "Makefile" = "ignore"     # forked for good; stop tracking it',
        "",
    ]
    return "\n".join(lines)


def bump_pin(path: Path, commit: str) -> None:
    """Rewrite the ``commit = ...`` line of the child config at ``path``.

    Raises:
        ConfigError: If the line can't be found.
    """
    text = path.read_text("utf-8")
    new, n = re.subn(
        r'(?m)^(\s*commit\s*=\s*)"[^"]*"',
        lambda m: m.group(1) + toml_str(commit),
        text,
        count=1,
    )
    if n != 1:
        raise ConfigError(f"{path}: could not find the `commit = ...` line to update")
    path.write_text(new, "utf-8")


def with_feature_choices(text: str, features: dict[str, Feature], choices: dict[str, bool]) -> str:
    """Return ``.template.toml`` text with ``choices`` added at the end of its [features] table.

    Args:
        text: The config's current text.
        features: The manifest's features, for their descriptions.
        choices: Feature name -> choice to add.

    Returns:
        The new text; a ``[features]`` table is appended if there is none.
    """
    entries: list[str] = []
    for name, on in choices.items():
        if features[name].description:
            entries.append(f"# {features[name].description}")
        entries.append(f"{name} = {'true' if on else 'false'}")
    lines = text.rstrip("\n").split("\n")
    start = next((i for i, line in enumerate(lines) if line.strip() == "[features]"), None)
    if start is None:
        return "\n".join([*lines, "", "[features]", *FEATURES_COMMENT, *entries]) + "\n"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines)
    )
    section = lines[start + 1 : end]
    if NO_FEATURES_COMMENT in section:
        at = section.index(NO_FEATURES_COMMENT)
        section[at : at + 1] = FEATURES_COMMENT
    while section and not section[-1].strip():
        section.pop()
    rest = lines[end:]
    return (
        "\n".join([*lines[: start + 1], *section, *entries, *([""] if rest else []), *rest]) + "\n"
    )


def record_new_features(manifest: Manifest, cfg: ChildConfig, *, yes: bool) -> ChildConfig:
    """Settle the template features a child's config doesn't mention, and write them into it.

    Asked about when interactive. A feature not asked about is recorded as it
    takes effect: its default, unless that conflicts with a feature that is on
    or requires one that is off. Recorded either way, so the choice `update`
    applies shows in the child's config and in its diff.

    Args:
        manifest: The template's manifest.
        cfg: The child's config.
        yes: Never prompt.

    Returns:
        The config, re-read after the write (unchanged if nothing was undecided).

    Raises:
        ConfigError: If the choices can't be written into the file, or two
            conflicting features are both on.
    """
    undecided = [name for name in manifest.features if name not in cfg.features]
    if not undecided:
        return cfg
    intro = "The template has options this project hasn't chosen yet:"
    chosen = resolve_features(manifest, cfg.features, yes=yes, intro=intro)
    enabled, _ = effective_features(manifest, chosen)
    choices = {name: chosen.get(name, name in enabled) for name in undecided}
    summary = ", ".join(f"{name} = {'true' if v else 'false'}" for name, v in choices.items())
    path = cfg.root / cfg.config
    text = with_feature_choices(path.read_text("utf-8"), manifest.features, choices)
    try:
        recorded = tomllib.loads(text).get("features", {})
    except tomllib.TOMLDecodeError:
        recorded = {}
    if any(recorded.get(name) != v for name, v in choices.items()):
        raise ConfigError(
            f"{path}: could not add to [features]; add these by hand, then rerun: {summary}"
        )
    path.write_text(text, "utf-8")
    print(f"recorded in {cfg.config}: {summary}")
    return load_child_config(cfg.root, manifest)


# ---------------------------------------------------------------------------
# Planning a sync
# ---------------------------------------------------------------------------


@dataclass
class FileResult:
    """What a sync found, and would do, for one file.

    Attributes:
        path: Template-relative path.
        child_path: Child-relative path (``[paths]`` and tokens applied).
        strategy: The effective strategy (after ``[overrides]``).
        status: One word: ``ok``, ``update``, ``drift``, ``conflict``, ...
        note: Why, for the report.
        content: What ``update`` writes (``None``: nothing, or a delete).
        executable: Whether the written file should be executable.
        error: Counts as an error (``check`` exits 1).
        change: ``update`` would write or delete it.
    """

    path: str
    child_path: str
    strategy: str
    status: str = "ok"
    note: str = ""
    content: bytes | None = None
    executable: bool = False
    error: bool = False
    change: bool = False


@dataclass
class SyncPlan:
    """Everything ``check`` reports and ``update`` applies.

    Attributes:
        head: The template's HEAD.
        pin: The commit the child is pinned to.
        behind: Commits from pin to HEAD; ``None`` if the pin is not an ancestor.
        notes: Informational lines for the report.
        results: One per file the child tracks.
        submodules: One line per template submodule.
    """

    head: str
    pin: str
    behind: int | None
    notes: list[str]
    results: list[FileResult]
    submodules: list[str]

    @property
    def errors(self) -> list[FileResult]:
        """The results that count as errors."""
        return [r for r in self.results if r.error]

    @property
    def changes(self) -> list[FileResult]:
        """The results ``update`` would write or delete."""
        return [r for r in self.results if r.change]


def merge_file(
    mine: bytes, base: bytes, theirs: bytes, labels: tuple[str, str, str]
) -> tuple[bytes, int]:
    """3-way merge with ``git merge-file --diff3``.

    Args:
        mine: The child's version.
        base: The template's version at the child's pin.
        theirs: The template's version now.
        labels: Conflict-marker labels for the three, in that order.

    Returns:
        The merged bytes and the number of conflicts.

    Raises:
        ConfigError: If git merge-file itself fails.
    """
    with tempfile.TemporaryDirectory(prefix="template_sync-") as d:
        paths = []
        for name, data in (("mine", mine), ("base", base), ("theirs", theirs)):
            p = Path(d) / name
            p.write_bytes(data)
            paths.append(str(p))
        label_args = [arg for label in labels for arg in ("-L", label)]
        proc = subprocess.run(  # noqa: S603  (fixed argv list, no shell)
            [git_exe(), "merge-file", "-p", "--diff3", *label_args, *paths],
            capture_output=True,
            check=False,
        )
    if proc.returncode < 0 or proc.returncode > 127:
        detail = proc.stderr.decode(errors="replace").strip()
        raise ConfigError(f"git merge-file failed: {detail}")
    return proc.stdout, proc.returncode


def decide(
    r: FileResult,
    base: bytes | None,
    theirs: bytes | None,
    mine: bytes | None,
    labels: tuple[str, str, str],
) -> FileResult:
    """Fill in status and content for one file from its three normalised versions.

    Args:
        r: The file's result, with ``strategy`` set; filled in place.
        base: The template's version at the child's pin, or ``None`` if absent.
        theirs: The template's version now, or ``None`` if absent.
        mine: The child's version, or ``None`` if absent.
        labels: Conflict-marker labels for mine, base and theirs.

    Returns:
        ``r``.
    """
    if r.strategy == "seed":
        if theirs is not None and mine is None and base is None:
            r.status, r.content = "seed-available", theirs
            r.note = "added to the template since the pin; install with --seed"
        return r

    unresolved = mine is not None and not is_binary(mine) and _CONFLICT_RE.search(mine)
    if r.strategy == "merge" and unresolved:
        r.status, r.error = "markers", True
        r.note = "unresolved conflict markers from an earlier update"
        return r

    if theirs is None:
        return _decide_removed(r, base, mine)

    if mine is None:
        if base is None:
            r.status, r.change, r.content = "new", True, theirs
        elif r.strategy == "managed":
            r.status, r.error, r.content = "drift", True, theirs
            r.note = "missing; update restores it"
        else:
            r.status = "local"
            r.note = "deleted locally" + (
                " (changed in the template since)" if theirs != base else ""
            )
        return r

    if mine == theirs:
        return r

    if r.strategy == "managed":
        r.content = theirs
        if base is not None and mine == base:
            r.status, r.change = "update", True
        else:
            r.status, r.error = "drift", True
            r.note = "edited locally; update overwrites it"
            if base is not None and theirs != base:
                r.note += " (also changed in the template)"
        return r

    return _decide_merge(r, base, theirs, mine, labels)


def _decide_removed(r: FileResult, base: bytes | None, mine: bytes | None) -> FileResult:
    """Decide a file that is gone from the template since the pin."""
    if mine is None:
        return r
    if mine == base:
        r.status, r.change, r.note = "delete", True, "removed from the template"
    elif r.strategy == "managed":
        r.status, r.error = "drift", True
        r.note = "removed from the template, but edited locally; delete it or override"
    else:
        r.status = "removed-upstream"
        r.note = "removed from the template; kept because it was edited locally"
    return r


def _decide_merge(
    r: FileResult,
    base: bytes | None,
    theirs: bytes,
    mine: bytes,
    labels: tuple[str, str, str],
) -> FileResult:
    """Decide a ``merge`` file present on both sides and different."""
    if base is None:
        r.status = "local"
        r.note = (
            "added to the template since the pin, and differs from your copy (no base to merge)"
        )
        return r
    if mine == base:
        r.status, r.change, r.content = "update", True, theirs
        return r
    if theirs == base:
        r.status, r.note = "local", "edited locally"
        return r
    if is_binary(mine) or is_binary(base) or is_binary(theirs):
        r.status, r.error, r.note = "conflict", True, "binary; changed on both sides, cannot merge"
        return r
    merged, conflicts = merge_file(mine, base, theirs, labels)
    if conflicts:
        r.status, r.error, r.change, r.content = "conflict", True, True, merged
        r.note = f"{conflicts} conflict(s); update writes the file with markers"
    elif merged == mine:
        r.note = "already contains the template's changes"
    else:
        r.status, r.change, r.content = "merged", True, merged
        r.note = "changed on both sides; merges cleanly"
    return r


def child_file_set(
    manifest: Manifest,
    pin_manifest: Manifest,
    cfg: ChildConfig,
    head_paths: dict[str, Classified],
    pin_paths: Iterable[str],
    enabled: frozenset[str],
) -> list[tuple[str, str, str, str | None]]:
    """List ``(template path, child path, strategy, pin path)`` for every file the child tracks.

    The pin path is where the file was in the template at the pin, the base of a
    3-way merge, or ``None`` for a file added since. Paths that exist only at the
    pin (removed from the template since) are classified with the manifest as it
    was at the pin -- the rules the child received them under; unclassifiable
    ones are skipped.

    A file the template moved, so that a path added since lands on the child path
    a removed one did, is one file and not a removal plus an addition: its pin
    path is the old path. Moving a file into ``child/`` changes its template path
    and not its child path.

    Args:
        manifest: The template's manifest now.
        pin_manifest: The manifest at the child's pin.
        cfg: The child's config.
        head_paths: The template's tracked files now, classified.
        pin_paths: The template's tracked files at the pin.
        enabled: The child's effective features.

    Returns:
        One entry per file, sorted by template path.

    Raises:
        ConfigError: If two template files land on one child path.
    """
    replacements = manifest.replacements(cfg.values)
    at_pin = set(pin_paths)

    def entry(path: str, cl: Classified | None) -> tuple[str, str, str] | None:
        if cl is None or cl.kind == "exclude" or (cl.feature and cl.feature not in enabled):
            return None
        child_path = map_path(path, manifest, replacements)
        return path, child_path, cfg.overrides.get(path, cfg.overrides.get(child_path, cl.kind))

    current = [e for p, cl in head_paths.items() if (e := entry(p, cl))]
    removed = [
        e
        for p in sorted(at_pin - head_paths.keys())
        if (e := entry(p, classify_one(pin_manifest, p)))
    ]
    added = {child_path for path, child_path, _s in current if path not in at_pin}
    moved_from: dict[str, str] = {}
    result: list[tuple[str, str, str, str | None]] = []
    for path, child_path, strategy in removed:
        if child_path in added and child_path not in moved_from:
            moved_from[child_path] = path
        else:
            result.append((path, child_path, strategy, path))
    for path, child_path, strategy in current:
        pin_path = path if path in at_pin else moved_from.get(child_path)
        result.append((path, child_path, strategy, pin_path))
    result.sort(key=lambda e: e[0])
    clashes = path_clashes((path, child_path) for path, child_path, _s, _p in result)
    if clashes:
        raise ConfigError("\n".join(clashes))
    return result


def _read_pin_manifest(manifest: Manifest, pin: str, notes: list[str]) -> Manifest:
    """Parse the manifest as it was at ``pin``, falling back to the current one."""
    try:
        text = show_at(manifest.root, pin, MANIFEST_NAME).decode("utf-8")
        return parse_manifest(text, manifest.root, where=f"{pin[:7]}:{MANIFEST_NAME}")
    except ConfigError, UnicodeDecodeError:
        notes.append(
            f"no readable {MANIFEST_NAME} at the pin {pin[:7]}; files removed from the template "
            "since then are classified with the current rules"
        )
        return manifest


def build_plan(manifest: Manifest, cfg: ChildConfig, *, include_seed: bool) -> SyncPlan:
    """Compare a child with the template and work out what ``update`` would do.

    Args:
        manifest: The template's manifest.
        cfg: The child's config.
        include_seed: Count seed files added since the pin as changes (``update --seed``).

    Returns:
        The plan.

    Raises:
        ConfigError: If the pin is not in the template, or a file can't be classified.
    """
    root = manifest.root
    head = head_commit(root)
    if not commit_exists(root, cfg.commit):
        raise ConfigError(
            f"{cfg.config}: pinned template commit {cfg.commit} is not in {root} "
            "(shallow clone, rewritten history, or the wrong template?)"
        )
    pin = git_out(root, "rev-parse", f"{cfg.commit}^{{commit}}")
    behind = None
    if is_ancestor(root, pin, head):
        behind = int(git_out(root, "rev-list", "--count", f"{pin}..{head}"))

    enabled, notes = effective_features(manifest, cfg.features)
    replacements = manifest.replacements(cfg.values)
    for rel in CHILD_CONFIG_PATHS:
        if rel != cfg.config and (cfg.root / rel).is_file():
            notes.append(f"{rel} is not read: {cfg.config} takes precedence")
    for name, f in manifest.features.items():
        if name not in cfg.features:
            notes.append(
                f"[features] {name} is unset, so its default ({'on' if f.default else 'off'}) "
                "applies; update records a choice"
            )
    tracked = tracked_files(root)
    executable = {t.path for t in tracked if t.executable}
    head_paths = classify(manifest, [t.path for t in tracked if not t.gitlink])
    at_pin = {p for p, gl in tracked_at(root, pin).items() if not gl}
    pin_manifest = _read_pin_manifest(manifest, pin, notes)
    # The pin's text holds the pin's literals (say, last year's Python version), so
    # it is rendered with the pin's tokens; falls back if those need other values.
    try:
        pin_replacements = pin_manifest.replacements(cfg.values)
    except ConfigError, KeyError:
        pin_replacements = replacements
    known = set(head_paths) | at_pin
    known_child = {map_path(k, manifest, replacements) for k in known}
    notes += [
        f"[overrides] {p!r} matches no template file"
        for p in cfg.overrides
        if p not in known and p not in known_child
    ]

    labels = ("yours", f"template@{pin[:7]}", f"template@{head[:7]}")
    results = []
    for path, child_path, strategy, pin_path in child_file_set(
        manifest, pin_manifest, cfg, head_paths, at_pin, enabled
    ):
        r = FileResult(path, child_path, strategy)
        results.append(r)
        if strategy == "ignore":
            r.status, r.note = "ignored", "by [overrides]"
            continue
        theirs = base = mine = None
        if path in head_paths:
            theirs = normalise((root / path).read_bytes(), replacements, enabled, where=path)
            r.executable = path in executable
        if pin_path is not None:
            base = normalise(
                show_at(root, pin, pin_path),
                pin_replacements,
                enabled,
                where=f"{pin[:7]}:{pin_path}",
            )
        dst = cfg.root / child_path
        if dst.is_file():
            mine = dst.read_bytes()
            if not is_binary(mine):
                mine = mine.replace(b"\r\n", b"\n")
        decide(r, base, theirs, mine, labels)
        if r.status == "seed-available":
            r.change = include_seed

    return SyncPlan(head, pin, behind, notes, results, _submodule_report(root, cfg.root, tracked))


def _submodule_report(root: Path, child_root: Path, tracked: list[Tracked]) -> list[str]:
    """Describe each template submodule's state in the child, one line each."""
    lines = []
    child_is_repo = is_git_repo(child_root)
    urls = {spath: url for _name, spath, url in submodule_entries(root)}
    for t in tracked:
        if not t.gitlink:
            continue
        tsha = gitlink_sha(root, "HEAD", t.path) or "?"
        csha = staged_gitlink_sha(child_root, t.path) if child_is_repo else None
        if csha is None:
            url = urls.get(t.path, "<url>")
            lines.append(f"submodule {t.path}: not in the child (git submodule add {url} {t.path})")
        elif csha != tsha:
            lines.append(
                f"submodule {t.path}: template at {tsha[:7]}, child at {csha[:7]} "
                "-- bump deliberately"
            )
        else:
            lines.append(f"submodule {t.path}: in sync at {tsha[:7]}")
    return lines


_EXEC_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def write_result(child_root: Path, r: FileResult) -> None:
    """Write ``r.content`` to the child, with the executable bit as ``r.executable`` says.

    Raises:
        ConfigError: If ``r`` has no content to write.
    """
    if r.content is None:
        raise ConfigError(f"{r.child_path}: nothing to write")
    dst = child_root / r.child_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(r.content)
    mode = dst.stat().st_mode
    want = mode | _EXEC_BITS if r.executable else mode & ~_EXEC_BITS
    if want != mode:
        os.chmod(dst, want)


def print_plan(plan: SyncPlan, *, show_all: bool) -> None:
    """Print a plan: where the pin stands, notes, then one line per file that isn't ``ok``."""
    if plan.pin == plan.head:
        where = f"template @ {plan.head[:7]} (child pin is current)"
    elif plan.behind is None:
        where = (
            f"template @ {plan.head[:7]}; child pinned at {plan.pin[:7]}, "
            "which is not an ancestor of HEAD"
        )
    else:
        where = (
            f"template @ {plan.head[:7]}; child pinned at {plan.pin[:7]}, "
            f"{plan.behind} commit(s) behind"
        )
    print(where)
    for n in plan.notes:
        print(f"  note: {n}")
    for r in plan.results:
        if r.status == "ok" and not show_all:
            continue
        loc = r.path if r.path == r.child_path else f"{r.path} -> {r.child_path}"
        print(f"  {r.status:<15} {loc}" + (f"  -- {r.note}" if r.note else ""))
    for s in plan.submodules:
        if show_all or "in sync" not in s:
            print(f"  {s}")
    ok = sum(1 for r in plan.results if r.status == "ok")
    hidden = " (--all lists them)" if ok and not show_all else ""
    print(
        f"{ok} up to date{hidden}; {len(plan.changes)} pending change(s); "
        f"{len(plan.errors)} error(s)"
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def default_template_root() -> Path:
    """Return the template checkout holding this script."""
    return Path(__file__).resolve().parent.parent


def resolve_child(arg: str, manifest: Manifest) -> Path:
    """Resolve the child argument to a directory that is not the template.

    Raises:
        ConfigError: If it is not a directory, or is the template itself.
    """
    root = Path(arg).resolve()
    if not root.is_dir():
        raise ConfigError(f"{root}: not a directory")
    if root == manifest.root.resolve():
        raise ConfigError(f"{root} is the template itself, not a child (use `validate` here)")
    return root


def require_clean_template(root: Path, *, force: bool, warn_only: bool = False) -> None:
    """Refuse (or warn about) uncommitted changes to the template's tracked files.

    The pin records HEAD, so what is copied must be what HEAD holds.

    Args:
        root: The template checkout.
        force: Proceed anyway.
        warn_only: Print a warning instead of raising.

    Raises:
        ConfigError: If the template is dirty and neither ``force`` nor ``warn_only``.
    """
    dirty = dirty_tracked(root)
    if not dirty:
        return
    msg = (
        f"template has uncommitted changes to tracked files ({', '.join(dirty[:5])}"
        f"{', ...' if len(dirty) > 5 else ''}); the pin records HEAD, so what is copied must be "
        "what HEAD contains"
    )
    if warn_only:
        print(f"warning: {msg}", file=sys.stderr)
    elif not force:
        raise ConfigError(msg + " -- commit them, or pass --force")


def _validate_tags(manifest: Manifest, path: str, tags: set[str]) -> list[str]:
    """Check one file's marker feature expressions against the manifest."""
    errors = []
    for tag in sorted(tags):
        terms = feature_terms(tag)
        errors += [
            f'{path}: <template-only feature="{tag}"> names an undeclared feature: {name}'
            for name in sorted({t.lstrip("!") for t in terms} - set(manifest.features))
        ]
        if manifest.format < 2 and any(t.startswith("!") for t in terms):
            errors.append(f'{path}: negated markers (feature="!name") need manifest_format = 2')
        if manifest.format < 3 and len(terms) > 1:
            errors.append(f'{path}: any-of markers (feature="a|b") need manifest_format = 3')
    return errors


def _validate_tokens(manifest: Manifest, shipped_text: list[str]) -> tuple[list[str], list[str]]:
    """Check that every token literal is its key's transform and appears somewhere."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        replacements = manifest.replacements(manifest.placeholder_values())
    except ConfigError as e:
        return [f"[tokens]: {e}"], warnings
    for literal, token in manifest.tokens.items():
        if replacements[literal] != literal:
            errors.append(
                f"[tokens] {literal!r}: rendering the template's own {token.key} value gives "
                f"{replacements[literal]!r}, so a template-only substitution would change it; "
                "make the key's literals renderings of one value"
            )
        missing = sorted(set(manifest.aliases_of(token.key)) - set(token.aliases))
        if missing:
            errors.append(
                f"[tokens] {literal!r}: no aliases {', '.join(missing)}, which other "
                f"{token.key} tokens declare; a value naming one would be rendered as-is here"
            )
        if not any(literal in t for t in shipped_text):
            warnings.append(f"[tokens] {literal!r} appears in no shipped file or path")
    return errors, warnings


def cmd_validate(_args: argparse.Namespace, root: Path) -> int:
    """Check the manifest classifies every tracked file, markers balance, and so on.

    Returns:
        :data:`EXIT_OK`.

    Raises:
        ConfigError: Listing every problem found.
    """
    manifest = load_manifest(root)
    tracked = tracked_files(root)
    classified = classify(manifest, [t.path for t in tracked if not t.gitlink])
    errors: list[str] = []
    warnings: list[str] = []

    used = {cl.rule for cl in classified.values()}
    warnings += [
        f"{rule.where} matches no tracked file" for rule in manifest.rules if rule not in used
    ]

    shipped_text: list[str] = []
    for path, cl in sorted(classified.items()):
        if cl.kind == "exclude":
            continue
        data = (root / path).read_bytes()
        if is_binary(data):
            if cl.kind == "merge":
                errors.append(f"{path}: binary files cannot be `merge` (use managed or seed)")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{path}: not UTF-8")
            continue
        shipped_text.append(text + "\n" + path)
        try:
            _, tags = apply_markers(text, None, where=path)
        except ConfigError as e:
            errors.append(str(e))
            continue
        errors += _validate_tags(manifest, path, tags)

    shipped = [path for path, cl in classified.items() if cl.kind != "exclude"]
    identity = {literal: literal for literal in manifest.tokens}
    errors += path_clashes((path, map_path(path, manifest, identity)) for path in shipped)
    warnings += [
        f"[paths] {prefix!r} matches no shipped file"
        for prefix in manifest.paths
        if not any(path.startswith(prefix) for path in shipped)
    ]
    token_errors, token_warnings = _validate_tokens(manifest, shipped_text)
    errors += token_errors
    warnings += token_warnings

    if errors:
        raise ConfigError("\n".join(errors))
    counts = {k: sum(1 for cl in classified.values() if cl.kind == k) for k in RULE_KINDS}
    gitlinks = [t.path for t in tracked if t.gitlink]
    print(
        f"{MANIFEST_NAME}: ok -- {len(classified)} tracked files classified: "
        + ", ".join(f"{v} {k}" for k, v in counts.items())
    )
    if gitlinks:
        print(f"  submodules (managed via .gitmodules): {', '.join(gitlinks)}")
    if manifest.features:
        print(
            "  features: "
            + ", ".join(
                f"{n} (default {'on' if f.default else 'off'})"
                for n, f in manifest.features.items()
            )
        )
    for w in warnings:
        print(f"  warning: {w}")
    return EXIT_OK


def cmd_check(args: argparse.Namespace, root: Path) -> int:
    """Report a child's drift and pending updates; write nothing.

    Returns:
        :data:`EXIT_DRIFT` on errors (or pending updates with ``--strict``), else :data:`EXIT_OK`.
    """
    manifest = load_manifest(root)
    child_root = resolve_child(args.child, manifest)
    cfg = load_child_config(child_root, manifest)
    require_clean_template(root, force=False, warn_only=True)
    origin = origin_url(root)
    if cfg.url and origin and cfg.url != origin:
        print(
            f"warning: child was created from {cfg.url}, this template's origin is {origin}",
            file=sys.stderr,
        )
    plan = build_plan(manifest, cfg, include_seed=False)
    print_plan(plan, show_all=args.all)
    if plan.errors:
        return EXIT_DRIFT
    pending = plan.changes or any(r.status == "seed-available" for r in plan.results)
    return EXIT_DRIFT if args.strict and pending else EXIT_OK


def cmd_update(args: argparse.Namespace, root: Path) -> int:
    """Apply the template's changes to a child and move its pin to the template's HEAD.

    Returns:
        :data:`EXIT_DRIFT` if conflicts were written, else :data:`EXIT_OK`.

    Raises:
        ConfigError: If either repo is dirty (without ``--force``), the child is not
            a git repository, or it still has conflict markers from an earlier update.
    """
    manifest = load_manifest(root)
    child_root = resolve_child(args.child, manifest)
    cfg = load_child_config(child_root, manifest)
    require_clean_template(root, force=args.force)
    if not is_git_repo(child_root):
        raise ConfigError(
            f"{child_root}: not a git repository; update needs one so the result is reviewable"
        )
    dirty = dirty_tracked(child_root)
    if dirty and not args.force:
        raise ConfigError(
            f"child has uncommitted changes ({', '.join(dirty[:5])}"
            f"{', ...' if len(dirty) > 5 else ''}); commit or stash them so the update is one "
            "reviewable diff, or pass --force"
        )
    cfg = record_new_features(manifest, cfg, yes=args.yes)
    plan = build_plan(manifest, cfg, include_seed=args.seed)
    markers = [r.child_path for r in plan.results if r.status == "markers"]
    if markers:
        raise ConfigError(
            "resolve the conflict markers left by the previous update first: " + ", ".join(markers)
        )

    applied = 0
    for r in plan.results:
        if r.status == "delete":
            (child_root / r.child_path).unlink()
            applied += 1
        elif r.content is not None and (r.change or r.status == "drift"):
            write_result(child_root, r)
            applied += 1
    if plan.head != plan.pin:
        bump_pin(child_root / cfg.config, plan.head)
    print_plan(plan, show_all=args.all)
    conflicts = [r for r in plan.results if r.status == "conflict"]
    pin_note = f"; pin {plan.pin[:7]} -> {plan.head[:7]}" if plan.head != plan.pin else ""
    print(f"applied {applied} change(s){pin_note}")
    if conflicts:
        print(
            "resolve the conflict markers, then commit:", ", ".join(r.child_path for r in conflicts)
        )
        return EXIT_DRIFT
    return EXIT_OK


def parse_assignments(items: list[str], allowed: Iterable[str], flag: str) -> dict[str, str]:
    """Parse repeated ``KEY=VALUE`` options.

    Raises:
        ConfigError: If an item has no ``=`` or an unknown key.
    """
    allowed = list(allowed)
    result = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or key not in allowed:
            raise ConfigError(
                f"{flag} {item!r}: expected KEY=VALUE with KEY one of "
                f"{', '.join(allowed) or '(none)'}"
            )
        result[key] = value
    return result


def resolve_values(
    manifest: Manifest,
    given: dict[str, str],
    target: Path,
    *,
    yes: bool,
    ask: set[str] | None = None,
) -> dict[str, str]:
    """Settle token values for a new child: ``--set``, else a prompt, else the default.

    The default for ``project`` is the target directory's name; for any other
    key it is the template's own literal, so an unset placeholder stays a
    visible placeholder rather than being guessed. Only the keys in ``ask``
    are prompted for; the rest take their defaults. A ``required`` key has no
    default: it is prompted for until answered, whether in ``ask`` or not, and
    without a terminal (or with ``-y``) it must come from ``--set``.

    Args:
        manifest: The template's manifest.
        given: Values from ``--set``.
        target: The directory being created.
        yes: Never prompt.
        ask: The keys to prompt for, or ``None`` for every key.

    Returns:
        One value per :attr:`Manifest.value_keys`.

    Raises:
        ConfigError: If a required value is missing or empty, or a value gives an
            invalid name under one of its transforms.
    """
    defaults = manifest.placeholder_values()
    if "project" in defaults:
        defaults["project"] = target.name
    required = manifest.required_keys
    interactive = not yes and sys.stdin.isatty()
    values = {}
    for key in manifest.value_keys:
        if key in given:
            values[key] = given[key]
        elif key in required and interactive:
            values[key] = ""
            while not values[key]:
                values[key] = input(f"{key} (required): ").strip()
        elif key in required:
            raise ConfigError(f"{key} has no default: pass --set {key}=VALUE")
        elif interactive and (ask is None or key in ask):
            aliases = manifest.aliases_of(key)
            hint = f" (or {', '.join(aliases)})" if aliases else ""
            values[key] = input(f"{key}{hint} [{defaults[key]}]: ").strip() or defaults[key]
        else:
            values[key] = defaults[key]
        if key in required and not values[key].strip():
            raise ConfigError(f"--set {key}=: {key} needs a non-empty value")
    manifest.replacements(values)  # fail before anything is written
    return values


def requirements_first(features: dict[str, Feature]) -> list[str]:
    """Return the feature names in declaration order, each moved after those it requires.

    ``requires`` has no cycles (:func:`parse_manifest` rejects them), so this ends.
    """
    order: list[str] = []

    def visit(name: str) -> None:
        if name not in order:
            for requirement in features[name].requires:
                visit(requirement)
            order.append(name)

    for name in features:
        visit(name)
    return order


def ask_option(
    manifest: Manifest, name: str, chosen: dict[str, bool], *, allow_none: bool
) -> str | None:
    """Ask which of feature ``name``'s options to take, from a numbered list.

    Lists the options not already chosen off, each by its name less a
    ``<name>_`` prefix, and then ``none`` if allowed. Takes a number, a listed
    name or an option's full name, and asks again on anything else.

    Args:
        manifest: The template's manifest.
        name: A feature with ``options``.
        chosen: Choices so far.
        allow_none: Offer ``none``, for no option and ``name`` off.

    Returns:
        The option taken, or ``None`` for none (or if nothing could be offered).
    """
    f = manifest.features[name]
    entries: list[tuple[str, str | None, str]] = [
        (o.removeprefix(f"{name}_") or o, o, manifest.features[o].description)
        for o in f.options
        if chosen.get(o) is not False
    ]
    if allow_none:
        entries.append(("none", None, ""))
    if not entries:
        return None
    lookup: dict[str, tuple[str, str | None, str]] = {}
    for n, entry in enumerate(entries, 1):
        label, option, _ = entry
        lookup |= {str(n): entry, label.lower(): entry}
        if option:
            lookup[option.lower()] = entry
    default = entries[0] if f.default or not allow_none else entries[-1]
    width = max(len(label) for label, _, _ in entries)
    print(f"{name}{f' ({f.description})' if f.description else ''}:")
    for n, (label, _, description) in enumerate(entries, 1):
        print(f"  {n}. {label:<{width}}  {description}".rstrip())
    while True:
        reply = input(f"{name} [{default[0]}]: ").strip().lower()
        answer = default if not reply else lookup.get(reply)
        if answer is not None:
            return answer[1]


def resolve_features(
    manifest: Manifest, given: dict[str, bool], *, yes: bool, intro: str | None = None
) -> dict[str, bool]:
    """Settle feature choices: those given, else asked for, else (by omission) the default.

    Features are asked about in declaration order, except that each comes after
    the features it requires. A feature that conflicts with one already chosen
    on, or requires one chosen off, is not asked about; it is recorded as off.
    A feature with ``options`` is asked about once, as a choice of one of them
    or none (:func:`ask_option`); its options are not asked about one by one.

    Args:
        manifest: The template's manifest.
        given: Choices already made.
        yes: Never prompt.
        intro: A line printed before the first prompt.

    Returns:
        ``given`` plus every answer (and the ``False`` implied by a conflict or
        a declined requirement).
    """
    chosen = dict(given)
    pending = [name for name in requirements_first(manifest.features) if name not in chosen]
    if yes or not pending or not sys.stdin.isatty():
        return chosen
    if intro:
        print(intro)
    answers = {"y": True, "yes": True, "n": False, "no": False}
    for name in pending:
        f = manifest.features[name]
        if name in chosen:  # an option, taken when its feature was asked about
            continue
        # Requirements come first, so each is given or already settled.
        if any(chosen.get(c) for c in f.conflicts) or not all(chosen[r] for r in f.requires):
            chosen[name] = False
            continue
        owner = manifest.option_of(name)
        if owner is not None:
            # Its feature was given as on, with none of its options: ask for one.
            picked = ask_option(manifest, owner, chosen, allow_none=False)
            chosen[name] = picked == name
            if picked:
                chosen[picked] = True
            continue
        if f.options:
            picked = next((o for o in f.options if chosen.get(o)), None)
            if picked is None:
                picked = ask_option(manifest, name, chosen, allow_none=True)
            chosen[name] = picked is not None
            if picked:
                chosen[picked] = True
            continue
        prompt = (
            f"{name}{f' ({f.description})' if f.description else ''} "
            f"[{'Y/n' if f.default else 'y/N'}]: "
        )
        answer = None
        while answer is None:
            reply = input(prompt).strip().lower()
            answer = f.default if not reply else answers.get(reply)
        chosen[name] = answer
    return chosen


def is_empty_repo(target: Path) -> bool:
    """Whether ``target`` holds only a ``.git`` with no commits and nothing staged.

    That is what cloning a freshly created, empty GitHub repository leaves.
    """
    if [p.name for p in target.iterdir()] != [".git"]:
        return False
    if git(target, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0:
        return False
    return not git_out(target, "ls-files")


def _parse_feature_flags(items: list[str], manifest: Manifest) -> dict[str, bool]:
    """Parse ``--feature NAME=true|false`` options."""
    chosen = {}
    for k, v in parse_assignments(items, manifest.features, "--feature").items():
        if v.lower() not in ("true", "false"):
            raise ConfigError(f"--feature {k}={v}: expected true or false")
        chosen[k] = v.lower() == "true"
    return chosen


def _add_submodules(root: Path, target: Path) -> list[str]:
    """``git submodule add`` the template's submodules into ``target`` at their commits."""
    notes = []
    for _name, spath, url in submodule_entries(root):
        sha = gitlink_sha(root, "HEAD", spath)
        git(target, "submodule", "--quiet", "add", url, spath)
        if sha:
            git(target / spath, "fetch", "--quiet", "origin", sha, check=False)
            git(target / spath, "checkout", "--quiet", "--detach", sha)
            git(target, "add", spath)
        notes.append(f"submodule {spath} @ {(sha or '?')[:7]}")
    return notes


def unsubstituted_texts(
    root: Path, manifest: Manifest, paths: Iterable[str], enabled: frozenset[str]
) -> list[str]:
    """Return what a new child gets from ``paths``, before token substitution.

    Args:
        root: The template checkout.
        manifest: The template's manifest.
        paths: The template paths the child gets.
        enabled: The child's effective features.

    Returns:
        One entry per file: its child path, then its text with the child's
        ``<template-only>`` blocks applied. A binary file gives only its path:
        tokens are never substituted inside one.
    """
    texts = []
    for path in paths:
        data = normalise((root / path).read_bytes(), {}, enabled, where=path)
        text = "" if is_binary(data) else data.decode("utf-8")
        texts.append(f"{map_path(path, manifest, {})}\n{text}")
    return texts


def cmd_new(args: argparse.Namespace, root: Path) -> int:
    """Create a project from the template: write every shipped file, git init, stage.

    Features are settled first, so only the values the project's files use are
    asked for. Then the manifest's ``[new].bootstrap`` commands run in the project
    (unless ``--no-bootstrap``) and their results are staged too. Nothing is
    committed: the author identity is the user's.

    Returns:
        :data:`EXIT_OK`, or :data:`EXIT_DRIFT` when a bootstrap step failed (the
        project is written; the steps left are printed).

    Raises:
        ConfigError: If the target is not empty, the template is dirty (without
            ``--force``), or a value or feature choice is invalid.
    """
    manifest = load_manifest(root)
    target = Path(args.child).resolve()
    if target.exists() and (
        not target.is_dir() or (any(target.iterdir()) and not is_empty_repo(target))
    ):
        raise ConfigError(
            f"{target}: exists and is not an empty directory (or an empty git repository)"
        )
    if target == root.resolve():
        raise ConfigError("target is the template itself")
    require_clean_template(root, force=args.force)
    head = head_commit(root)
    given = parse_assignments(args.set, manifest.value_keys, "--set")
    chosen = resolve_features(manifest, _parse_feature_flags(args.feature, manifest), yes=args.yes)
    enabled, notes = effective_features(manifest, chosen)

    tracked = tracked_files(root)
    executable = {t.path for t in tracked if t.executable}
    classified = classify(manifest, [t.path for t in tracked if not t.gitlink])
    gitlinks = [t.path for t in tracked if t.gitlink]
    add_submodules = bool(gitlinks) and not args.no_submodules
    shipping = [
        path
        for path, cl in sorted(classified.items())
        if cl.kind != "exclude" and not (cl.feature and cl.feature not in enabled)
    ]
    used = manifest.keys_in(unsubstituted_texts(root, manifest, shipping, enabled))
    values = resolve_values(manifest, given, target, yes=args.yes, ask=used)
    replacements = manifest.replacements(values)
    shipped = [(path, map_path(path, manifest, replacements)) for path in shipping]
    clashes = path_clashes(shipped)
    if clashes:
        raise ConfigError("\n".join(clashes))
    child_paths = dict(shipped)

    target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    def write(path: str) -> None:
        r = FileResult(path, child_paths[path], classified[path].kind)
        r.content = normalise((root / path).read_bytes(), replacements, enabled, where=path)
        r.executable = path in executable
        write_result(target, r)
        written.append(r.child_path)

    for path, _child_path in shipped:
        if path == ".gitmodules" and add_submodules:
            continue  # written after `git submodule add`, which rewrites it
        write(path)

    git(target, "init", "-q")
    git(target, "symbolic-ref", "HEAD", "refs/heads/main")
    sub_notes: list[str] = []
    if add_submodules:
        sub_notes = _add_submodules(root, target)
        if ".gitmodules" in child_paths:
            write(".gitmodules")  # `git submodule add` rewrote it; the template's wins
    elif gitlinks:
        sub_notes.append(
            "submodules not added (--no-submodules); run: "
            + "; ".join(f"git submodule add {url} {p}" for _n, p, url in submodule_entries(root))
        )

    (target / CHILD_CONFIG_NAME).write_text(
        render_child_config(
            manifest,
            url=origin_url(root),
            commit=head,
            values=values,
            features={n: n in enabled for n in manifest.features},
        ),
        "utf-8",
    )
    git(target, "add", "-A")

    print(
        f"created {target} from template @ {head[:7]}: {len(written)} files, "
        f"{CHILD_CONFIG_NAME} written"
    )
    print("  values: " + ", ".join(f"{k}={v!r}" for k, v in values.items()))
    if manifest.features:
        print("  features on: " + (", ".join(sorted(enabled)) or "(none)"))
    for n in notes + sub_notes:
        print(f"  {n}")

    code = EXIT_OK
    pending: list[str] = []
    if manifest.bootstrap and args.no_bootstrap:
        pending = [str(step) for step in manifest.bootstrap]
    elif manifest.bootstrap:
        pending = run_bootstrap(target, manifest.bootstrap)
        git(target, "add", "-A")
        if pending:
            code = EXIT_DRIFT
    steps = [*pending, *manifest.next_steps]
    if not steps:
        print("everything is staged and nothing is committed: review `git status`, then commit")
        return code
    print("everything is staged and nothing is committed. Next, in the project:")
    for step in steps:
        print(f"  {step}")
    return code


def run_bootstrap(target: Path, steps: Iterable[BootstrapStep]) -> list[str]:
    """Run ``[new].bootstrap`` in a new project, stopping at the first step that fails.

    Each command is shown before it runs, and its output goes to the terminal
    (or to its ``stdout`` file). ``VIRTUAL_ENV`` is dropped from the commands'
    environment: a venv active where the template runs belongs to the template,
    not to the new project.

    Args:
        target: The new project's root.
        steps: The commands, in order.

    Returns:
        The steps not done, failed one first, as shell lines; empty when all ran.
    """
    todo = list(steps)
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    print("bootstrap:")
    for i, step in enumerate(todo):
        print(f"  $ {step}", flush=True)
        exe = shutil.which(step.run[0])
        if exe is None:
            print(f"error: {step.run[0]}: not found on PATH", file=sys.stderr)
            return [str(s) for s in todo[i:]]
        argv = [exe, *step.run[1:]]
        out = target / step.stdout if step.stdout else None
        if out is None:
            returncode = subprocess.run(  # noqa: S603  (manifest argv, no shell)
                argv, cwd=target, env=env, check=False
            ).returncode
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("wb") as handle:
                returncode = subprocess.run(  # noqa: S603  (manifest argv, no shell)
                    argv, cwd=target, env=env, stdout=handle, check=False
                ).returncode
        if returncode != 0:
            if out is not None:
                out.unlink(missing_ok=True)  # half a file is worse than none
            print(f"error: `{step}` failed (exit {returncode})", file=sys.stderr)
            return [str(s) for s in todo[i:]]
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="template_sync.py",
        description=(__doc__ or "").split("\n\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Strategies: managed (template owns it), merge (3-way), seed (copied once).",
    )
    parser.add_argument(
        "--template",
        metavar="DIR",
        help="template checkout to act from (default: the one holding this script)",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser(
        "validate",
        help=f"check {MANIFEST_NAME} classifies every tracked file, markers balance, etc.",
    )
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("check", help="report a child's drift and pending updates; writes nothing")
    p.add_argument("child", help="path to the child project")
    p.add_argument("--all", action="store_true", help="also list up-to-date files")
    p.add_argument(
        "--strict", action="store_true", help="exit 1 when updates are pending, not only on drift"
    )
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("update", help="apply the template's changes to a child and bump its pin")
    p.add_argument("child", help="path to the child project")
    p.add_argument(
        "--seed",
        action="store_true",
        help="also install seed files added to the template since the pin",
    )
    p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="never prompt; options the project hasn't chosen take their defaults",
    )
    p.add_argument(
        "--force", action="store_true", help="proceed despite uncommitted changes in either repo"
    )
    p.add_argument("--all", action="store_true", help="also list up-to-date files")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("new", help="create a project from the template")
    p.add_argument(
        "child", help="directory to create (must not exist, or be empty or an empty git repo)"
    )
    p.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "token value (repeatable); unset keys default to the directory name for `project`, "
            "else the template's literal, except required keys, which have no default"
        ),
    )
    p.add_argument(
        "--feature",
        action="append",
        default=[],
        metavar="NAME=true|false",
        help="feature choice (repeatable); unset features are asked about, or take the default",
    )
    p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="never prompt; use defaults for unset values and features",
    )
    p.add_argument(
        "--no-submodules", action="store_true", help="skip `git submodule add` (no network)"
    )
    p.add_argument(
        "--no-bootstrap",
        action="store_true",
        help=f"skip the commands {MANIFEST_NAME} lists under [new].bootstrap; print them instead",
    )
    p.add_argument(
        "--force", action="store_true", help="proceed despite uncommitted changes in the template"
    )
    p.set_defaults(func=cmd_new)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv: Arguments after the program name; ``None`` for ``sys.argv[1:]``.

    Returns:
        The exit code.
    """
    args = build_parser().parse_args(argv)
    try:
        root = Path(args.template).resolve() if args.template else default_template_root()
        code: int = args.func(args, root)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_CONFIG
    return code


if __name__ == "__main__":
    sys.exit(main())
