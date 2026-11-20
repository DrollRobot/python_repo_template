<!-- <template-only> -->
<!--
=============================================================================
TEMPLATE SETUP NOTES -- template-only: scripts/template_sync.py never ships it
=============================================================================
This AGENTS.md is part of a starter repo scaffold.
It instructs AI coding agents (GitHub Copilot, Cursor, etc.) on project
conventions, required checks, and how to run tests.
- Fill in the Package Purpose section.
=============================================================================
-->
<!-- </template-only> -->

# Agent Rules

## Package Purpose
<!-- FIXME: Describe what this package does, who consumes it, and any key
     constraints (e.g. "credentials are always supplied by the caller"). -->

## General rules
<!-- <template-only feature="config_system"> -->
- Environment values are are stored in the config system. Package config schema
  is defined in src/name/config/schema.py.
<!-- </template-only> -->
- Secrets are stored only in keyvault or keyring. Never in source, never in
  plain-text on disk.

<!-- <template-only> -->
## Template maintenance
This repo is a project template. `scripts/template_sync.py` creates projects
from it and keeps them in sync, as `template.toml` directs.
- Classify every new tracked file in `template.toml`: `managed`, `merge`,
  `seed` or `exclude`, under `[base]` or the `[features.*]` table it belongs
  to. `validate` fails on an unclassified file.
- Anything that must not reach projects goes in a template-only block: a
  marker line holding only the tag (`<template-only>`, or
  `<template-only feature="x">` to tie it to a feature) in the file's comment
  syntax, closed by `</template-only>`. Blocks do not nest. Every file must
  stay valid, and the template must keep working, with the markers in place.
- `python_repo_template`, `python-repo-template` and `DrollRobot` are tokens:
  every occurrence is replaced by the project's own value. Do not write them
  in shipped prose outside a template-only block unless the project's value
  belongs there.
- Run `uv run scripts/template_sync.py validate` after changing
  `template.toml` or any marker. The `template-validate` pre-commit hook runs
  it too.
- Then run `uv run pytest tests/test_template_manifest.py -m live`: it makes
  sample projects from the template and runs their gates, as CI does.
<!-- </template-only> -->

## Code Formatting and Style
- Follow pep8 style guidelines.
- Always include thorough docstrings for all functions and classes.
- Line length limit: 100 characters.
- Use type hints for all function signatures.

## pre-commit
- Do not fight automatic formatting. Always commit autoformatting changes, even if
  they're out of scope for the current task.
- Formatting changes and detect-secrets updates to the .secrets.baseline are routine
  and expected. If all that's required is re-staging and re-committing, DO NOT REPORT
  TO THE USER. Only report when user interaction is required.

## detect-secrets
This repo uses detect-secrets.
- Do not try to work around secret detection by renaming, splitting
  strings, `# pragma: allowlist secret`, or any other means. Let detect-secrets do
  its job. A new entry in the baseline for a user to audit is not a problem to be
  avoided. Simply tell the user an audit is required.
- Baseline updates during pre-commit checks are expected. Do not attempt to revert.
- Agents can/should freely scan for secrets:
```bash
uv run detect-secrets scan --baseline .secrets.baseline
```
- Agents should NEVER regenerate the baseline from scratch.
```bash
detect-secrets scan > .secrets.baseline
```
- Agents should NEVER attempt to audit (users only), or modify the `.secrets.baseline`
  file directly.

## Commit Messages
Review before writing commit messages: [AGENTS.COMMITTING.md](AGENTS.COMMITTING.md).

## Testing
For instructions on writing and running tests: [AGENTS.TESTING.md](AGENTS.TESTING.md).
ALWAYS READ BEFORE WRITING NEW CODE.

## Build and Release
For build and release procedures, see [AGENTS.RELEASING.md](AGENTS.RELEASING.md).
