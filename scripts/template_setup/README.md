# Template setup scripts

One-time helpers for turning a fresh clone of this template into your own
project. They use **only the Python standard library**, so they run before any
dependencies are installed.

## Guided, config-driven setup

Edit `scripts/template_setup/setup.toml` with your values, then run:

```powershell
uv run scripts/template_setup/setup_new_project.py
# or, without uv:
python scripts/template_setup/setup_new_project.py
```

This:

1. **Validates** every field in `setup.toml` up front. If anything is wrong
   (a missing key, an invalid license choice, a KeyVault flag with no
   backend, an unsafe `reinit`), every problem is listed at once and
   **nothing is changed**.
2. **Previews** every change every step would make, in canonical order.
3. Asks for **one confirmation**.
4. **Applies** everything. A step failing doesn't block the rest — failures
   are collected and reported at the end; each can be re-run on its own.
5. Always finishes with a **read-only FIXME report**, whether or not
   anything failed.

Flags: `--dry-run` stops after the preview (nothing applied); `-y`/`--yes`
skips the confirmation (the preview still runs first); `--config PATH` points
at a config file other than the default `scripts/template_setup/setup.toml`.

This does **not** delete `scripts/template_setup/` — `cleanup.py` stays a
separate, manual step. Run it yourself (or just delete the folder) whenever
you're done with the scaffolding.

…or run any step on its own — see the table below for the full list and what
each one does. Run standalone, each script previews its changes and asks
before applying; most accept `--dry-run` and `-y`/`--yes`.

## `setup.toml` field reference

| Table | Field | Meaning |
| --- | --- | --- |
| `[project]` | `name` | New project name (any case/separators). Derives both the snake_case import name and the kebab-case distribution name. |
| `[project]` | `github_user` | Replaces `DrollRobot` everywhere. |
| `[project]` | `python_version` | Python version to target (`MAJOR.MINOR` or `MAJOR.MINOR.PATCH`). |
| `[project]` | `version` | Initial release version written to `pyproject.toml`. |
| `[license]` | `key` | One of `mit` / `apache` / `gnu` / `proprietary` — mandatory, no default. |
| `[license]` | `year`, `name` | Copyright year and holder; required unless `key = "gnu"`. |
| `[license]` | `company` | Owning company; required only when `key = "proprietary"`. |
| `[claude]` | `shell` | `powershell` or `bash` — which hook flavor to wire in. No-op if both hooks below are `false`. |
| `[claude]` | `no_chained_commands`, `canonical_commands` | Each Claude Code command hook is independently optional (see below). |
| `[claude]` | `auto_memory_guard` | Enable the auto-memory write-guard hook. |
| `[features]` | `mkdocs` | Keep or remove the MkDocs documentation site. |
| `[features]` | `keyring` | Keep or remove the OS-keyring credentials backend. Independent of `azure_keyvault`. |
| `[features]` | `azure_keyvault` | Keep or remove the Azure KeyVault credentials backend. Independent of `keyring`. |
| `[git]` | `reinit` | **Destructive.** Deletes `.git` and starts a fresh history. Guarded — see below. |
| `[git]` | `branch` | Initial branch name for the re-initialized repository. |

### Claude Code command hooks

The template ships two independent `PreToolUse` hooks per shell flavor:

- `no-chained-commands` — requires one shell command per tool call, so a
  permission allowlist keeps matching.
- `canonical-commands` — keeps shell invocation consistent, so you don't need
  to allow multiple equivalent commands.

Each is toggled independently in `[claude]`; `shell` picks which flavor
(`powershell`/`bash`) of whichever hooks you keep gets wired into
`.claude/settings.json`. Declining both removes all four hook files.

### The `reinit` guard

`[git].reinit = true` is validated, not just executed: `reinit_git.py` checks
that the repository's root commit still matches this template's own initial
commit before allowing the delete. If history was already replaced by an
earlier reinit (or this isn't a template clone at all), the whole run is
refused with **zero changes made** — set `reinit = false`, or investigate.
This only protects the config-driven, zero-prompt path; running
`reinit_git.py` standalone still just asks for a normal confirmation, since a
human running it directly already sees the current branch/origin printed.

Note: a shallow clone (`git clone --depth 1`) fails this check even on a
genuine pristine clone, because the root commit object isn't present
locally — do a full clone before reinitializing.

## Every script, standalone

| Script | What it does |
| --- | --- |
| `rename_project.py NAME` | Replace `python_repo_template` / `python-repo-template` everywhere, rename the package folder, and turn `*.code-workspace.FIXME.jsonc` into `NAME.code-workspace`. |
| `strip_template_headers.py` | Remove the `TEMPLATE SETUP NOTES` banner from the top of every file. |
| `set_github_user.py USER` | Replace `DrollRobot` with your GitHub username. |
| `choose_shell.py` | Ask, per hook (`no-chained-commands`, `canonical-commands`), whether to install it. Decline both (or pass `--no-hooks`) and it deletes all four hook files; accept at least one and it asks your primary shell (bash/powershell), wires the wanted pair into `.claude/settings.json`, and deletes every hook file that isn't wanted. |
| `protect_auto_memory.py` | Ask whether to enable the auto-memory write guard (off by default). Accept and it wires `.claude/hooks/protect-auto-memory.py` into `.claude/settings.json` so Claude asks before writing to its memory directory; decline (or pass `--no-guard`) and it deletes the hook file. |
| `set_python_version.py [VERSION]` | Retarget the project's Python version everywhere it is declared (`.python-version`, `pyproject.toml`, pre-commit, docs, README badge, issue template). |
| `set_version.py [VERSION]` | Set the project's release version in `pyproject.toml` (default `0.1.0` for a fresh project). |
| `reset_changelog.py` | Drop the template's own `CHANGELOG.md` history and put the blank `CHANGELOG.md.FIXME` skeleton in its place. |
| `find_fixmes.py` | List every remaining `FIXME` (in contents and file names). Read-only. |
| `choose_license.py` | Pick one `LICENSE.*.FIXME`, fill in the copyright line, delete the rest. |
| `remove_mkdocs.py` | Drop the docs site if you don't want one: deletes `docs/`, `mkdocs.yml`, and the Pages workflow, and strips the `docs` dependency group and every mkdocs reference from `pyproject.toml`, `.gitignore`, `README.md`, `CONTRIBUTING.md`, and `AGENTS.RELEASING.md`. |
| `remove_keyring.py` | Drop the keyring credentials backend: deletes `tests/_keyring.py` and `scripts/setup_credentials.py`, and strips the `keyring` dependency and the keyring bullet/table row from `pyproject.toml` and `README.md`. Independent of KeyVault. |
| `remove_keyvault.py` | Drop the Azure KeyVault backend: deletes `tests/_keyvault.py`, and strips the `keyvault` dependency group, the `KEYVAULT_*` block in `.env.example`, and the table row in `README.md`. Independent of keyring. |
| `remove_credentials.py` | Drop the shared credentials dispatcher (`tests/_bootstrap.py`) and the generic `.env.example`/`tests/conftest.py` references. Only meaningful once **both** `remove_keyring.py` and `remove_keyvault.py` have already run — with no backend left, the dispatcher has nothing to dispatch to. |
| `reinit_git.py` | **Destructive.** Delete `.git` and run `git init`. |

Most scripts accept `--dry-run` (preview without writing) and `-y`/`--yes`
(skip the confirmation prompt). Run standalone, every change is previewed and
confirmed before it is applied; under the orchestrator, the single preview +
confirmation covers every checked step.

The orchestrator's fixed order — **strip headers → rename → set user → set
python version → set version → reset changelog → Claude command hooks →
Claude auto-memory guard → choose license → remove mkdocs → remove keyring →
remove keyvault → remove credentials dispatcher → reinit git → find FIXMEs**
(only the removal/reinit steps run conditionally) — is also the suggested
order when running steps by hand. (Strip before rename so the workspace
header is removed while the file still ends in `.jsonc`. Reset the changelog
after rename and set-user so the skeleton's links pick up the new project
name and username.) `cleanup.py` is **not** part of this list — it is a
separate, manual step; run it (or delete `scripts/template_setup/` yourself)
whenever you're finished with the scaffolding.
