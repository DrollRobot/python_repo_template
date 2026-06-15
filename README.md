# python-repo-template

<!-- FIXME: replace badges below with your own CI/PyPI/coverage links -->
[![CI](https://github.com/FIXME/python-repo-template/actions/workflows/ci.yml/badge.svg)](https://github.com/FIXME/python-repo-template/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE) # FIXME replace with correct license link

<!-- FIXME: one paragraph describing what this package does and who should use it -->

My goal with this project is to create a baseline for new python projects with the most commonly used tools and community best practices.

It's mostly for personal use. But maybe others will have suggestions? Or find it useful themselves?


# Tool choices

Based on some personal preference, and what I understand are the most widely used tools in the Python ecosystem.

- **uv** for dependency management and virtual environments. Fast. Allows very simple package install directly from Github.
- **pre-commit** for git hooks to run checks before commits.
- **ruff** for linting and formatting. Fast.
- **mypy** for static type checking.
- **pytest** for testing.
- **mkdocs** for documentation. Integrates easily with GitHub Pages for hosting.
- **keyring** for credential storage. Cross-platform. Allows never keeping secrets in the repo.
- **detect-secrets** for scanning for secrets before commits. Helps prevent accidental leaks.
- **GitHub Actions** for CI and docs deployment. Free for public repos, and widely used.
- **VSCode** for development. Most popular IDE. Lots of documentation. Many extensions.

## Making a new repo from this template

- Clone the repo. Name the new folder after your project:
```bash
git clone https://github.com/DrollRobot/python_repo_template.git YOUR-PROJECT-NAME
```
**Run the setup scripts**

The `scripts/template_setup/` folder has standard-library Python helpers that
automate the tedious parts. Run the whole transition in one guided pass:

```powershell
uv run scripts/template_setup/setup_new_project.py
```

…or run any step on its own (details in `scripts/template_setup/README.md`):

- `rename_project.py NAME` — replace `python_repo_template` / `python-repo-template`
  throughout and rename the package folder + `.code-workspace` file.
- `strip_template_headers.py` — remove the `TEMPLATE SETUP NOTES` header banners.
- `set_github_user.py USER` — replace `DrollRobot` with your GitHub username.
- `choose_license.py` — pick one license, fill in the copyright line, delete the rest.
- `find_fixmes.py` — list every remaining `FIXME` (read-only checklist).
- `reinit_git.py` — delete `.git` and start a fresh history (destructive).
- `cleanup.py` — delete the `template_setup/` folder and the dev-script tests
  once you're done (destructive).

Each script previews its changes and asks before applying; most accept
`--dry-run` and `-y`/`--yes`. After choosing a license, update the license badge
near the top of this README to match.

** Create venv and install basic dev/test dependencies**

```powershell
uv sync
```

**Install and update pre-commit hooks**

```powershell
uv run pre-commit install
uv run pre-commit autoupdate
```

**If using mkdocs, enable GitHub Pages for docs**

- In the GitHub repo settings, set Pages source to the `gh-pages` branch.
- Then deploy:
```bash
uv run mkdocs gh-deploy --force
```

The GitHub Actions workflow in `.github/workflows/docs.yml` will keep the site updated on future pushes to `main`.

---

**Initialize the secrets baseline**

```powershell
uvx detect-secrets scan > .secrets.baseline
```

**Write some code!**
