<!--
=============================================================================
TEMPLATE SETUP NOTES -- remove this block - FIXME
=============================================================================
This AGENTS.md is part of a starter repo scaffold.
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
- Environment values are are stored in the config system. Package config schema
  is defined in src/name/config/schema.py.
- Secrets are stored only in keyvault or keyring. Never in source, never in
  plain-text on disk.

## Code Formatting and Style
- Follow pep8 style guidelines.
- Always include thorough docstrings for all functions and classes.
- Line length limit: 100 characters.
- Use type hints for all function signatures.
- Do not fight automatic formatting. Always commit autoformatting changes, even if
  they're out of scope for the current task.

## detect-secrets
This repo uses detect-secrets.
- Baseline updates during pre-commit checks are expected. Do not attempt to revert.
- Agents can/should freely scan for secrets:
```bash
uv run detect-secrets scan --baseline .secrets.baseline
```
- Agents should NEVER regenerate the baseline
```bash
detect-secrets scan > .secrets.baseline
```
- Agents should NEVER attempt to audit (users only) or modify the `.secrets.baseline`
  file directly.

## Commit Messages
Review before writing commit messages: [AGENTS.COMMITTING.md](AGENTS.COMMITTING.md).

## Testing
For instructions on writing and running tests: [AGENTS.TESTING.md](AGENTS.TESTING.md).
ALWAYS READ BEFORE WRITING NEW CODE.

## Build and Release
For build and release procedures, see [AGENTS.RELEASING.md](AGENTS.RELEASING.md).
