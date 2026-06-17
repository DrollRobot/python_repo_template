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

…or run any step on its own:

| Script | What it does |
| --- | --- |
| `rename_project.py NAME` | Replace `python_repo_template` / `python-repo-template` everywhere, rename the package folder, and turn `*.code-workspace.FIXME.jsonc` into `NAME.code-workspace`. |
| `strip_template_headers.py` | Remove the `TEMPLATE SETUP NOTES` banner from the top of every file. |
| `set_github_user.py USER` | Replace `DrollRobot` with your GitHub username. |
| `choose_shell.py` | Pick your primary shell (bash/powershell), wire its Claude Code command hooks into `.claude/settings.json`, and delete the other shell's hook files. |
| `set_python_version.py [VERSION]` | Retarget the project's Python version everywhere it is declared (`.python-version`, `pyproject.toml`, pre-commit, docs, README badge, issue template). |
| `set_version.py [VERSION]` | Set the project's release version in `pyproject.toml` (default `0.1.0` for a fresh project). |
| `reset_changelog.py` | Drop the template's own `CHANGELOG.md` history and put the blank `CHANGELOG.md.FIXME` skeleton in its place. |
| `find_fixmes.py` | List every remaining `FIXME` (in contents and file names). Read-only. |
| `choose_license.py` | Pick one `LICENSE.*.FIXME`, fill in the copyright line, delete the rest. |
| `reinit_git.py` | **Destructive.** Delete `.git` and run `git init` for a fresh history. |
| `cleanup.py` | **Destructive.** Delete this `template_setup/` folder and the unit tests for the dev scripts (the scripts stay) once you're done. |

Most scripts accept `--dry-run` (preview without writing) and `-y`/`--yes`
(skip the confirmation prompt). Every change is previewed and confirmed before
it is applied.

Suggested order: **strip headers → rename → set user → set python version →
set version → reset changelog → choose shell → choose license → find FIXMEs →
reinit git → cleanup.** (Strip before rename so the workspace
header is removed while the file still ends in `.jsonc`. Reset the changelog
after rename and set-user so the skeleton's links pick up the new project name
and username.) The whole
`template_setup/` folder is
disposable — `cleanup.py` (or the orchestrator) removes it when you're finished.
