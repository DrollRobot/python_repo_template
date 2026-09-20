# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.15.0] - 2026-09-20 - Live schema gating

### Changed

- **Breaking:** the shared config test object split in two. `ConfigTestObject`
  is now secret-free and `SecretTestObject` carries the secret;
  `NoSecretsTestObject` is gone. Tests in a synced project that name either
  must move to one of the two.
- `remove_secret_storage.py` checks the schema before deleting anything and
  refuses while `Settings` still declares a secret field, naming the fields to
  fix. `--force` deletes anyway.
- `remove_secret_storage.py` also rewrites the config package docstring,
  which described the machinery it had just deleted and pointed at
  `credential_backend` — a key config.toml rejects once the dispatcher is
  gone. It prints two more follow-ups: set `CREDENTIAL_BACKEND = "none"`, and
  expect `config/cli.py` coverage to drop because its secret commands can
  never run.
- `compare_to_template.py` prints its update offers one line per file, and
  compares `config/__init__.py` leniently once secret storage is removed.

### Fixed

- The config CLI gates on the live schema instead of an import-time snapshot,
  so the secret commands, `init`'s backend prompt, and the `"none"`-policy
  notice follow the project's own `Settings`.
- The shipped config tests no longer read the project's own
  `CREDENTIAL_BACKEND` where they mean the schema they installed themselves,
  so a project that sets it to `"none"` gets a green suite instead of three
  failures.
- The two shipped secrets guards no longer inherit git's hook environment,
  which made both fail when committing from a worktree.

## [1.14.0] - 2026-09-07 - Claude Code hooks removed

### Removed

- The Claude Code hooks and everything that wired them: the hook scripts,
  `wire_hook.py` and `choose_shell.py`, the `[claude]` setup table, the
  `compare_to_template.py` gates, and the hook tests. A setup config that still
  carries the `[claude]` table keeps working; its keys no longer do anything.

### Fixed

- `.secrets.baseline` is tracked in the repository, so a fresh clone and CI have
  the baseline the detect-secrets hook and the audit gate need.
- `compare_to_template.py` no longer compares `.secrets.baseline`, which every
  project scans and audits for itself.

### Security

- Raised the `mkdocs-material` floor to `>=9.7.7` (GHSA-xvg9-69gf-fjrf: DOM XSS
  in search suggestions).

## [1.13.0] - 2026-09-01 - Configurable secret storage

### Added

- A `CREDENTIAL_BACKEND` policy in the settings schema: `"none"` (no secrets at
  all), a backend name (used without prompting), or `"prompt"`.
- Per-secret storage names, held in reserved `<field>_secret_name` config keys
  that `init` and `set-secret` write.
- `option()` and `secret()` field factories in the schema. `secret()` takes no
  value and keeps the resolved secret out of `repr()`.
- Removable secret storage: the `[features].secret_storage` flag and
  `remove_secret_storage.py` drop the dispatcher, every backend, and their
  tests, while the rest of the config system keeps working.
- `README.md.FIXME` and `reset_readme.py`, so setup replaces the template's
  README as it already replaced the changelog.
- `--no-remote` on the release and worktree scripts, for repositories with no
  origin.
- detect-secrets as a CI gate: an `audit.yml` job scans every tracked file and
  fails on any baseline entry that is unaudited or confirmed real.
- A ban on inline allowlist pragmas, enforced by a Claude Code hook and by
  `tests/test_no_inline_suppressions_for_secrets.py`, which scans the whole
  repository.

### Changed

- Which backend stores secrets is the user's choice, not the developer's. The
  hardcoded `keyring` default is gone, `set`/`unset` accept the reserved backend
  keys, `init` prompts for one, and each backend declares its own config keys --
  so deleting a backend file also removes its keys from the legal config.
- The CLI asks for a secret's name and its value separately, masking only the
  value, and never asks for a value on a read-only backend such as keyvault.
- `keyring` and `keyvault` moved from dependency groups to extras, so an end
  user of an installed package can opt into a backend.
- `scripts/setup.toml` renamed to `scripts/template_setup.toml`, to keep it
  distinct from the per-user runtime `config.toml`. Rename yours when adopting
  the updated scripts.
- `compare_to_template.py` diffs the content of `config/schema.py`, leniently.

### Fixed

- The azure SDK is no longer forced onto every downstream install; it ships in
  the `keyvault` extra alone.
- Hook wiring travels between machines. Hooks are wired in exec form and run
  under `uv run --no-project`, instead of relying on a shell and an interpreter
  name that is missing or a stub on other platforms.

### Security

- Secret values can no longer originate in source: `secret()` cannot express a
  default, and the resolver rejects a schema that gives one.
- `.claude/settings.json` denies regenerating `.secrets.baseline` from scratch,
  which would discard its audit history.

## [1.12.0] - 2026-07-20 - Setup from one config file

### Added

- `scripts/setup.toml` drives the whole template setup: every choice lives in
  one file that `setup_new_project.py` validates at once, previews, and applies
  with a single confirmation.
- A pre-push pre-commit stage running `uv lock --locked`, `uv audit`, and the
  offline test suite. The commit-time hooks no longer re-run on push.
- Destructive tests split into `destructive_local` and `destructive_remote`,
  each with its own flag and proof-of-disposability check, so opting into one
  cannot arm the other.

### Changed

- `compare_to_template.py` reads the feature choices and excludes a declined
  feature's files entirely, instead of marking them optional.
- The committed VS Code workspace hides less from the explorer and drops its
  auto-approve stubs.

### Fixed

- The offline test filter named the retired `integration` marker instead of
  `live`, letting live tests into offline runs.
- Setup no longer leaves behind tests for hooks it deleted.
- Credential setup stops hiding non-secret fields; only passwords and client
  secrets are masked.

## [1.11.0] - 2026-07-13 - Command hook parity

### Added

- The bash command hook gained the checks its PowerShell counterpart already
  had, and both gained opt-in debug logging via `CLAUDE_HOOK_DEBUG_LOG`.

### Changed

- Both command hooks block a leading `cd` outright, and the bash hook accepts
  `bash script.sh` as the single script-invocation form.

## [1.10.0] - 2026-07-12 - Dependency floors and worktree guards

### Added

- `scripts/update_floors.py`, which raises each direct dependency's `>=` floor
  to the newest version its upper bound allows and reports the majors a cap
  holds back.
- A `remove_worktree.py` preflight that aborts when a config file copied into
  the worktree (rather than symlinked) has diverged, so force-removal cannot
  discard it.
- GitHub App token authentication in the docs workflow, matching `ci.yml` and
  `audit.yml`.

### Changed

- `complete_worktree.py` takes the PR title from `PR.md` front-matter instead of
  the last commit subject, and aborts when it is missing.
- `compare_to_template.py` version-checks every `scripts/*.py` helper, not only
  itself.

### Fixed

- A worktree opened from a repository with an active venv no longer inherits the
  parent `.venv`.
- Restored Python 3.10 compatibility in the dev scripts and the stub guard, via
  the `tomli` backport.

### Security

- Raised the `pytest` floor to `>=9.0.3` (GHSA-6w46-j5rx-g56g) and the
  `python-dotenv` floor to `>=1.2.2` (GHSA-mf9w-mj56-hr94).

## [1.9.0] - 2026-07-06 - Side-by-side template diffs

### Changed

- `compare_to_template.py` opens drift as side-by-side diffs in VS Code, with
  `--diff-tool` to choose the editor and a fallback to terminal unified diffs.
  `README.md` is compared for existence only, since every project rewrites it.

## [1.8.1] - 2026-07-06 - Cross-platform type check

### Fixed

- The Windows-only `os.startfile` call in the file-opening helpers is guarded on
  `sys.platform`, so mypy passes off Windows.

## [1.8.0] - 2026-07-06 - Settings and gitignore helpers

### Added

- `scripts/open_claude_settings.py` and `scripts/open_gitignore.py`, which open
  the project, local, and global copies of those files in a resolved editor.

### Changed

- CI's `check` job runs on Linux, macOS, and Windows, so the cross-platform
  helpers are exercised everywhere. Branch protection now expects the per-OS
  status checks.

### Fixed

- `_cli.py`'s Windows ANSI console setup is guarded on `sys.platform`, so mypy
  passes off Windows.

## [1.7.0] - 2026-07-04 - Template drift detection

### Added

- `scripts/compare_to_template.py`, which replays the setup transformations and
  reports where a generated project has drifted from the template.
- `tests/test_mypy_stub_guard.py`, which fails when mypy config silences an
  import whose type stubs are published on PyPI.
- A committed `.claude/settings.json` with deny rules, so agents cannot
  hand-edit `uv.lock`.

### Changed

- `scripts/` is type-checked and coverage-measured, and mypy's targets live in
  `pyproject.toml` so the docs, CI, and pre-commit cannot drift apart.
- The setup steps that own `.claude/settings.json` refuse to run on an
  unreadable or invalid file, instead of starting fresh and discarding its
  contents.

### Fixed

- Ruff's py314 formatter had rewritten `except (A, B):` into the PEP 758 form, a
  syntax error on Python 3.13 that broke the copied helper scripts and hooks.

## [1.6.0] - 2026-06-29 - Private dependencies in CI

### Added

- A commented GitHub App token block in the CI and audit workflows for
  installing private dependencies, documented in the README.
- An opt-in hook that asks for approval before Claude writes to its auto-memory
  directory.

## [1.5.1] - 2026-06-25 - Worktree safety

### Added

- Cross-device PR handoff in `complete_worktree.py`, for a device with no
  authenticated `gh`: the PR body travels to another device as a git note.

### Changed

- `new_worktree.py` syncs the base branch with origin before creating the
  worktree, and `remove_worktree.py` warns before discarding uncommitted changes
  or unpushed commits.
- The worktree scripts reject malformed slugs instead of building an invalid
  branch name.

### Removed

- The `WT_HOME`, `WT_BASE`, and `WT_PREFIX` overrides. The worktree directory
  and branch prefix are now fixed.

## [1.5.0] - 2026-06-17 - Optional docs, licenses, Python 3.14

### Added

- `remove_mkdocs.py`, which drops the documentation site and every reference to
  it, for projects that do not want one.
- `set_version.py` and `reset_changelog.py` setup steps, which reset the
  project's version and replace the template's changelog history.
- A proprietary (internal-use) license option.

### Changed

- Setup asks whether to install the Claude Code hooks at all; declining removes
  them instead of wiring any.
- The default Python target rose from 3.13 to 3.14 everywhere it is declared.

### Removed

- `pydantic-settings`, an unused runtime dependency.

## [1.4.0] - 2026-06-16 - Modular credentials

### Added

- `choose_shell.py` and `set_python_version.py` setup steps.
- A `__version__` on every dev helper script, so copies can be compared across
  repositories.
- `--no-version` on `push_new_tag_to_main.py`, which merges, tags, and pushes
  without changing the version.

### Changed

- `push_new_tag_to_main.py` fast-forwards the source branch and `main` before
  merging, so a release cannot be cut from a stale branch.
- Credentials are modular: the Key Vault backend moved into its own lazily
  loaded module, the default backend is keyring, and settings load from `.env`.

## [1.3.0] - 2026-06-12 - Release script arguments

### Added

- `-y/--yes` on `push_new_tag_to_main.py`, for non-interactive releases.

### Changed

- `push_new_tag_to_main.py` takes the bump level as a positional argument
  instead of `--bump`.

## [1.2.1] - 2026-06-11 - Worktree picker

### Added

- `remove_worktree.py` shows a picker of open worktrees when run without a slug.

### Changed

- Template cleanup removes the dev-script tests while keeping the scripts.

### Fixed

- `complete_worktree.py`'s clean-tree check exempts an uncommitted `PR.md`.

## [1.2.0] - 2026-06-11 - Interactive worktree scripts

### Added

- `-y/--yes` on the worktree scripts, and a `CLAUDE.md` redirecting to
  `AGENTS.md`.

### Changed

- `new_worktree.py` is interactive like the other helpers: a setup summary,
  per-step confirmations, and streamed output.

## [1.1.0] - 2026-06-10 - Python template setup suite

### Added

- `scripts/template_setup/`, the suite that turns the template into a new
  project: rename it, strip the template headers, set the GitHub user, choose a
  license, sweep the FIXMEs, reinitialize git, then remove itself. Every step
  previews its changes and asks before applying.
- A startup warning when the scripts run under plain Git Bash, where interactive
  prompts can freeze.

### Changed

- The worktree and release helpers were rewritten from PowerShell and shell into
  stdlib-only Python sharing an interactive `_cli` module.
- The standalone shell and PowerShell unwanted-strings scanners became a single
  pytest scan.

## [1.0.2] - 2026-06-09 - Worktrees and string scanning

### Added

- `New-Worktree.ps1`, for creating git worktrees.
- An unwanted-strings scan, as a shell scanner and a pytest test.

### Fixed

- The dev and release scripts derive the package name instead of hardcoding it.
- A delay between `uv` commands, avoiding intermittent "uv.exe busy" errors on
  Windows.

## [1.0.1] - 2026-06-02 - Initial release

Initial release: a Python project template scaffold.

### Added

- A `src/`-layout package managed with uv, with `pyproject.toml`, pre-commit,
  ruff, and mypy configured.
- GitHub issue and pull-request templates, Dependabot config, and the CI, audit,
  and docs workflows.
- Selectable MIT, Apache-2.0, and GNU licenses, and a MkDocs documentation site.
- Dev scripts for keyring-based credential setup and release tagging, and test
  scaffolding for settings and keyring-backed credentials.
- `AGENTS.md` agent instructions.

[Unreleased]: https://github.com/DrollRobot/python_repo_template/compare/v1.15.0...HEAD
[1.15.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.14.0...v1.15.0
[1.14.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.13.0...v1.14.0
[1.13.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.12.0...v1.13.0
[1.12.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.11.0...v1.12.0
[1.11.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.10.0...v1.11.0
[1.10.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.9.0...v1.10.0
[1.9.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.8.1...v1.9.0
[1.8.1]: https://github.com/DrollRobot/python_repo_template/compare/v1.8.0...v1.8.1
[1.8.0]: https://github.com/DrollRobot/python_repo_template/compare/v1.7.0...v1.8.0
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
