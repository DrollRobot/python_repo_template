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
- No .env files. Non-secret environment/user values live in the per-user
  config.toml managed by the config CLI (`python-repo-template-config`);
  secrets live in the OS keyring or Azure Key Vault, never in the repo.
  Option names are defined once, in `src/python_repo_template/config/schema.py`.
- Fail early, fail loudly. Avoid default values that could mask errors.

## Code Formatting and Style
- Follow pep8 style guidelines.
- Always include thorough docstrings for all functions and classes.
- Line length limit: 100 characters.
- Use type hints for all function signatures.

## Commit Messages
Review before writing commit messages: [AGENTS.COMMITTING.md](AGENTS.COMMITTING.md).

## Testing
For instructions on writing and running tests: [AGENTS.TESTING.md](AGENTS.TESTING.md).
ALWAYS READ BEFORE WRITING NEW CODE.

## Build and Release
For build and release procedures, see [AGENTS.RELEASING.md](AGENTS.RELEASING.md).
