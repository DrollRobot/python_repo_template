# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `scripts/compare_to_template.py` (1.0.0), a dev helper that compares a
  generated project's baseline files (GitHub config, dev scripts, AGENTS docs,
  lint/format config, ...) against a template checkout and reports drift. It
  replays the template-setup transformations (project/username rename, header
  strip, Python-version pins, cleanup.py's pyproject trims) before diffing, so
  only real drift is reported; files are classified strict/lenient and
  required/optional, with `--diff` for unified diffs, `--all` for the full
  list, and exit code 1 on drift for CI use. Before comparing it checks its
  own `__version__` on both sides and offers to update the project's copy from
  the template. Its unit tests also enforce that every tracked template file
  is either in the comparison manifest or explicitly excluded, so new template
  files force a comparison decision.

### Changed

- `scripts/` is now type-checked and coverage-measured. Mypy targets moved into
  `pyproject.toml` (`files = ["src", "tests", "scripts"]` plus `mypy_path`), so
  AGENTS.TESTING.md, CI, and the pre-commit hook all run a bare `uv run mypy`
  and can no longer drift apart. The nine `# type: ignore[import-not-found]`
  workarounds in the dev-script tests are gone, and pytest coverage now
  includes `scripts/` (`--cov=scripts`).
- `template_setup/cleanup.py` also trims the template-only pyproject.toml lines
  when the scaffolding is removed: it drops `--cov=scripts` (the dev-script
  tests are deleted alongside it) and narrows `mypy_path` to `["scripts"]`,
  aborting loudly before deleting anything if pyproject.toml has drifted from
  the template.

### Fixed

- Strict-mode typing gaps the mypy blind spot was hiding: bare `dict`
  annotations in `template_setup/choose_shell.py` and
  `template_setup/protect_auto_memory.py` are now `dict[str, Any]`, and
  `remove_worktree.py`'s `open_worktree_slugs` accepts any sequence instead of
  requiring an exact `list` element type (patch bump to 1.2.3).

## [1.7.0] - 2026-07-04

### Added

- Committed `.claude/settings.json` with permission deny rules
  (`Edit(uv.lock)`, `Edit(**/uv.lock)`) so Claude Code agents cannot hand-edit
  the lockfile with the Edit/Write tools. Dependency pins belong in
  `pyproject.toml` (e.g. `[tool.uv] constraint-dependencies`), followed by
  `uv lock`; `uv lock`/`uv sync` themselves are unaffected.

### Changed

- `template_setup/choose_shell.py` and `template_setup/protect_auto_memory.py`
  now refuse to run when an existing `.claude/settings.json` is unreadable,
  invalid JSON, or not a JSON object, exiting with an error instead of
  "starting fresh" and silently discarding its contents (such as the new
  permission deny rules).

### Fixed

- Restored parenthesized `except (A, B):` handlers in the `scripts/` helpers.
  Ruff's py314 formatter had rewritten them to the PEP 758 unparenthesized
  form, a syntax error on Python 3.13 that broke the ruff pre-commit hook in
  projects that copied these helpers. Each site is now guarded with
  `# fmt: skip`, and the four versioned scripts got a patch bump.
- The same Python 3.13 syntax fix for the tracked `.claude/hooks/` scripts,
  which run under the system `python` and would silently fail to parse (and so
  never enforce their checks) on a 3.13 interpreter.

## [1.6.0] - 2026-06-29

### Added

- Optional GitHub App token authentication in the CI and audit workflows, letting
  them install private dependencies. A commented `create-github-app-token` block
  in `.github/workflows/ci.yml` and `audit.yml` (with a matching git-auth step)
  can be uncommented and pointed at one or more private repos; README.md documents
  creating the App and storing the `GRAPH_AUTH_CLIENT_ID` variable and
  `GRAPH_AUTH_APP_PRIVATE_KEY` secret.
- Opt-in auto-memory write guard: a `PreToolUse` hook
  (`.claude/hooks/protect-auto-memory.py`) that asks for approval before Claude
  writes to its auto-memory directory. Off by default; the new
  `template_setup/protect_auto_memory.py` step (and the guided
  `setup_new_project.py`) prompts whether to enable it, wiring it project-scoped
  via `$CLAUDE_PROJECT_DIR`. Declining deletes the hook file; the hook header
  documents how to run it globally instead.

## [1.5.1] - 2026-06-25

### Added

- Cross-device PR handoff in `complete_worktree.py`, for when the device holding
  the worktree has no authenticated `gh`. `--push-pr-to-notes` pushes the branch
  and attaches `PR.md` (with the base and title) as a per-slug git note; on
  another device, `--gh-from-notes` creates the PR with `gh`, or
  `--web-from-notes` opens a prefilled PR form in the browser with no `gh` auth.
  Either side fetches the note, creates the PR, and then removes the note from
  origin.

### Changed

- `new_worktree.py` now syncs the base branch with origin before creating the
  worktree: it offers to push a local base that is ahead of origin (so the new
  worktree includes those commits), warns if the base has diverged, and warns
  about uncommitted changes that can never transfer into a worktree.
- `remove_worktree.py` now warns before work is lost -- before
  `git worktree remove --force` discards uncommitted changes, and before
  `git branch -D` deletes a branch with commits that were never pushed to origin.
- The worktree scripts reject malformed slugs (leading, trailing, or doubled
  slashes) instead of building an invalid branch name.

### Removed

- The `WT_HOME`, `WT_BASE`, and `WT_PREFIX` environment overrides from the
  worktree scripts. The sibling `<repo>-wt` worktree directory and the `wt/`
  branch prefix are now fixed; the base branch stays a positional argument
  (default `develop`).

## [1.5.0] - 2026-06-17

### Added

- `remove_mkdocs.py` template-setup step that drops the documentation site for
  projects that don't want one: it deletes `docs/`, `mkdocs.yml`, and the Pages
  deploy workflow, and strips the `docs` dependency group and every mkdocs
  reference from `pyproject.toml`, `.gitignore`, `README.md`, `CONTRIBUTING.md`,
  and `AGENTS.RELEASING.md`. Offered as an optional step in the
  `setup_new_project.py` orchestrator.
- `set_version.py` template-setup step that resets the project's release version
  in `pyproject.toml` (default `0.1.0`), and `reset_changelog.py`, which drops the
  template's own `CHANGELOG.md` history in favour of a blank `CHANGELOG.md.FIXME`
  skeleton. Both are wired into the `setup_new_project.py` orchestrator.
- Proprietary (internal-use) license option: a fourth `LICENSE.proprietary.FIXME`
  candidate for confidential or internal-only projects. The license chooser offers
  it and prompts for an owning company name in addition to the copyright holder;
  all candidate placeholders now use a labelled `FIXME{...}` brace form.

### Changed

- `choose_shell.py` now asks whether to install the Claude Code command hooks at
  all before asking for a primary shell. Declining (or passing `--no-hooks`)
  removes all four hook files instead of wiring any, so projects that don't want
  the hooks ship none. The `setup_new_project.py` orchestrator gates the shell
  prompt behind this choice.
- Default Python target raised from 3.13 to 3.14 throughout the template:
  `.python-version`, `pyproject.toml` (`requires-python`, ruff `target-version`,
  mypy `python_version`), `.pre-commit-config.yaml`, the docs, the README badge,
  and the issue template. `set_python_version.py`'s built-in default is bumped to
  3.14 to match.

### Removed

- `pydantic-settings` is no longer a template dependency. It was an unused runtime
  dependency -- `.env` loading goes through `python-dotenv` in the `test` group.

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

[Unreleased]: https://github.com/DrollRobot/python_repo_template/compare/v1.7.0...HEAD
[1.7.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.5.1...v1.6.0
[1.5.1]: https://github.com/DrollRobot/python_repo_template/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/DrollRobot/python_repo_template/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/DrollRobot/python_repo_template/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/DrollRobot/python_repo_template/releases/tag/v1.0.1
