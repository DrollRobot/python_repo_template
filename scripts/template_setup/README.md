# Template setup scripts

One-time helpers for turning a fresh clone of this template into your own
project. They use **only the Python standard library**, so they run before any
dependencies are installed.

Run the guided, end-to-end flow:

```powershell
uv run scripts/template_setup/setup_new_project.py
# or, without uv:
python scripts/template_setup/setup_new_project.py
```

The orchestrator shows a numbered checklist of every step below, all checked
by default. Toggle steps by number (`3`, `1 4`, `5-8`, commas ok) or with
`all`/`none`, then type `run` to execute — that is the single confirmation:
each checked step prompts for its inputs right before it runs and applies its
changes without asking again. Type `q` to quit without changing anything.
Steps always execute in the order listed below regardless of how they were
toggled, with `cleanup.py` last.

…or run any step on its own:

| Script | What it does |
| --- | --- |
| `rename_project.py NAME` | Replace `python_repo_template` / `python-repo-template` everywhere, rename the package folder, and turn `*.code-workspace.FIXME.jsonc` into `NAME.code-workspace`. |
| `strip_template_headers.py` | Remove the `TEMPLATE SETUP NOTES` banner from the top of every file. |
| `set_github_user.py USER` | Replace `DrollRobot` with your GitHub username. |
| `choose_shell.py` | Ask whether to install the Claude Code command hooks. Decline (or pass `--no-hooks`) and it deletes all four hook files; accept and it asks your primary shell (bash/powershell), wires that pair into `.claude/settings.json`, and deletes the other shell's files. |
| `protect_auto_memory.py` | Ask whether to enable the auto-memory write guard (off by default). Accept and it wires `.claude/hooks/protect-auto-memory.py` into `.claude/settings.json` so Claude asks before writing to its memory directory; decline (or pass `--no-guard`) and it deletes the hook file. |
| `set_python_version.py [VERSION]` | Retarget the project's Python version everywhere it is declared (`.python-version`, `pyproject.toml`, pre-commit, docs, README badge, issue template). |
| `set_version.py [VERSION]` | Set the project's release version in `pyproject.toml` (default `0.1.0` for a fresh project). |
| `reset_changelog.py` | Drop the template's own `CHANGELOG.md` history and put the blank `CHANGELOG.md.FIXME` skeleton in its place. |
| `find_fixmes.py` | List every remaining `FIXME` (in contents and file names). Read-only. |
| `choose_license.py` | Pick one `LICENSE.*.FIXME`, fill in the copyright line, delete the rest. |
| `remove_mkdocs.py` | Drop the docs site if you don't want one: deletes `docs/`, `mkdocs.yml`, and the Pages workflow, and strips the `docs` dependency group and every mkdocs reference from `pyproject.toml`, `.gitignore`, `README.md`, `CONTRIBUTING.md`, and `AGENTS.RELEASING.md`. |
| `reinit_git.py` | **Destructive.** Delete `.git` and run `git init` for a fresh history. |
| `cleanup.py` | **Destructive.** Delete this `template_setup/` folder and the unit tests for the dev scripts (the scripts stay) once you're done. |

Most scripts accept `--dry-run` (preview without writing) and `-y`/`--yes`
(skip the confirmation prompt). Run standalone, every change is previewed and
confirmed before it is applied; under the orchestrator, typing `run` is the
confirmation and the steps apply without asking again.

The orchestrator's fixed order — **strip headers → rename → set user → set
python version → set version → reset changelog → choose shell → protect
auto-memory → choose license → remove mkdocs → find FIXMEs → reinit git →
cleanup** — is also the suggested order when running steps by hand. (Strip
before rename so the workspace header is removed while the file still ends in
`.jsonc`. Reset the changelog after rename and set-user so the skeleton's
links pick up the new project name and username.) The whole `template_setup/`
folder is disposable — `cleanup.py` (or the orchestrator) removes it when
you're finished.
