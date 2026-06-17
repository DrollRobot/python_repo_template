# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `set_version.py` template-setup step that resets the project's release version
  in `pyproject.toml` (default `0.1.0`), and `reset_changelog.py`, which drops the
  template's own `CHANGELOG.md` history in favour of a blank `CHANGELOG.md.FIXME`
  skeleton. Both are wired into the `setup_new_project.py` orchestrator.

## [1.4.0] - 2026-06-16

### Added

- `choose_shell.py` template-setup step that asks for the primary shell and
  wires the matching Claude Code `PreToolUse` command hooks into
  `.claude/settings.json` (merging idempotently and preserving unrelated
  entries), then removes the unused hook pair. Wired into the
  `setup_new_project.py` orchestrator.
- `set_python_version.py` template-setup step that retargets the project's
  Python version everywhere it is declared (`.python-version`, `pyproject.toml`,
  pre-commit, docs, badge, issue template). The default lives in one
  `DEFAULT_VERSION` constant; it is also wired into the `setup_new_project.py`
  orchestrator.
- Per-script `__version__` constant, printed at startup, on every dev helper
  script so copies can be compared across repos.
- `--no-version` option to `push_new_tag_to_main.py`, which merges, tags, and
  pushes without changing the version. Passing `--version` with the version
  already in use is treated the same way.

### Changed

- `push_new_tag_to_main.py` now fetches from origin and fast-forwards both the
  source branch and `main` before merging, aborting if either has diverged, so a
  release can no longer be cut from a stale branch (e.g. a PR merged on the
  remote but not yet pulled).
- Credentials made modular. The Azure KeyVault backend moved out of
  `tests/_bootstrap.py` into its own `tests/_keyvault.py`, loaded lazily by the
  dispatcher; `_bootstrap.py` no longer imports `azure`. The default backend is
  now keyring (was keyvault), still switchable at runtime via `CREDENTIAL_BACKEND`
  in `.env`. `setup_credentials.py` is now self-contained (no longer imports from
  `tests/`). Settings load from `.env` (was `.env.testing`). Added an "Optional
  features & how to remove them" guide to the README, and declared the
  `integration` pytest marker.

## [1.3.0] - 2026-06-12

### Added

- `-y/--yes` flag to `push_new_tag_to_main.py` for non-interactive releases.

### Changed

- `push_new_tag_to_main.py` now takes the bump level as a positional argument
  (e.g. `push_new_tag_to_main.py patch`) instead of `--bump`.

## [1.2.1] - 2026-06-11

### Added

- `remove_worktree.py` shows an interactive picker of open worktrees when run
  without a slug.

### Changed

- Template cleanup now removes the dev-script tests while keeping the scripts
  themselves.

### Fixed

- `complete_worktree.py` no longer fails its clean-tree check when `PR.md` is
  uncommitted; the PR body file is exempt.

## [1.2.0] - 2026-06-11

### Added

- `-y/--yes` non-interactive flag to the worktree scripts.
- `CLAUDE.md` that redirects to `AGENTS.md`.

### Changed

- `new_worktree.py` is now interactive, matching the other helpers: a setup
  summary, per-step y/n confirmations, and echoed commands with streamed output.

## [1.1.0] - 2026-06-10

### Added

- `scripts/template_setup/` suite for starting a new project from the template:
  rename the project, strip template headers, set the GitHub user, choose a
  license, list remaining FIXMEs, reinitialize git, and an orchestrator that
  runs the steps and removes the suite when done. Each step previews its
  changes and asks before applying.
- Startup warning when scripts run under plain Git Bash (mintty), where
  interactive prompts can freeze; points to winpty, PowerShell, or the VS Code
  terminal instead.

### Changed

- Worktree and release helper scripts rewritten from PowerShell/shell to
  stdlib-only Python with a shared interactive `_cli` module.

### Removed

- PowerShell helper scripts, replaced by the Python versions.
- Standalone shell and PowerShell unwanted-strings scanners, superseded by the
  pytest-based scan in `tests/test_unwanted_strings.py`.

## [1.0.2] - 2026-06-09

### Added

- `New-Worktree.ps1` script for creating git worktrees.
- `find-unwanted-strings.sh` shell scanner and a pytest-based unwanted-strings
  scan.

### Fixed

- Dev and release scripts derive the package name from a variable instead of
  hardcoding it.
- Added a delay between `uv` commands to avoid intermittent "uv.exe busy"
  errors on Windows.

## [1.0.1] - 2026-06-02

Initial release: a Python project template scaffold.

### Added

- `src/`-layout package managed with uv, plus `pyproject.toml`, pre-commit,
  ruff, and mypy configuration.
- GitHub issue and pull-request templates, Dependabot config, and CI, audit,
  and docs workflows.
- Selectable MIT, Apache-2.0, and GNU license templates.
- MkDocs documentation site.
- Dev scripts: keyring-based `setup_credentials.py` and a release-tagging
  script (PowerShell and shell), plus a PowerShell unwanted-strings scanner.
- Test scaffolding: `conftest.py` and a `_bootstrap` module for settings and
  keyring-backed credentials in tests.
- `AGENTS.md` agent instructions.

[Unreleased]: https://github.com/DrollRobot/python_repo_template/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/DrollRobot/python_repo_template/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/DrollRobot/python_repo_template/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/DrollRobot/python_repo_template/releases/tag/v1.0.1
