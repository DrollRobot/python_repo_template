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

## General rules
- All environment specific values should live in .env, not in source.
- Fail early, fail loudly. Avoid default values that could mask errors. 

## Code Formatting and Style

- Follow pep8 style guidelines.
- Always include thorough docstrings for all functions and classes.
- Line length limit: 100 characters.
- Use type hints for all function signatures.

## Writing Tests for New Code

- All new code should have tests for every branch.
- Pure logic branches (parsers, utilities with no network or I/O) get unit
  tests in `tests/`.
- Tests for branches with external calls (HTTP, DB) should be marked
  `@pytest.mark.integration`.

## Testing after code changes

After writing new code, run tests as described in [AGENTS.TESTING.md](AGENTS.TESTING.md).

## Build and Release

For build and release procedures, see [AGENTS.RELEASING.md](AGENTS.RELEASING.md).