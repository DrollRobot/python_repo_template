<!--
=============================================================================
TEMPLATE SETUP NOTES -- remove this block - FIXME
=============================================================================
This AGENTS.md is part of python_repo_template, a starter repo scaffold.
It instructs AI coding agents (GitHub Copilot, Cursor, etc.) on project
conventions, required checks, and how to run tests.
- Fill in the Package Purpose section.
=============================================================================
-->

# Agent Rules

## Package Purpose

<!-- FIXME: Describe what this package does, who consumes it, and any key
     constraints (e.g. "credentials are always supplied by the caller"). -->

`python-repo-template` is a Python package that ...

## Code Formatting and Style

- Follow pep8 style guidelines.
- Always include thorough docstrings for all functions and classes.

## Writing Tests for New Code

- Pure logic (parsers, utilities): unit tests in `tests/`. No network or I/O.
- External calls (HTTP, DB, filesystem): integration tests marked
  `@pytest.mark.integration`.
- Export new public symbols from `src/python_repo_template/__init__.py`.

## After Any Code Changes:

```
# dependencies
uv sync                               # after pyproject.toml edits
uv lock --check                       # verify lockfile in sync
uv run pre-commit autoupdate          # periodically update precommit dependencies

# code checks
uv run ruff check .                   # lint
uv run ruff format .                  # apply ruff formatting
uv run mypy src tests                 # type check

# tests
uv run pytest -m "not integration"    # offline tests
uv run pytest                         # online and offline tests (when credentialed)

# docs
uv run mkdocs build --strict          # build docs, fail on warnings
uv run mkdocs gh-deploy --force       # push to GitHub Pages (gh-pages branch)

```
