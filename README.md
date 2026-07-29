# python-repo-template
<!-- FIXME: replace badges below with your own CI/PyPI/coverage links -->
[![CI](https://github.com/FIXME/python-repo-template/actions/workflows/ci.yml/badge.svg)](https://github.com/FIXME/python-repo-template/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue)](https://www.python.org/)
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
- **keyring** for local credential storage (optional backend). Cross-platform. Allows never keeping secrets in the repo.
- **Azure Keyvault** for remote secret storage (optional backend).
- **detect-secrets** for real-time secret scanning. Helps prevent accidental leaks.
- **GitHub Actions** for CI and docs deployment. Free for public repos, and widely used.
- **VSCode** for development. Most popular IDE. Lots of documentation. Many extensions.
- **Claude Code** as the coding agent. Includes some helpful hooks and baseline settings.

# Design choices
Some of the design choices I've made for this project:

- **No .env** To avoid keeping secrets/environment values within the repo, project
keeps non-secret environment values and user config options in a config.toml in a
standard OS-specific location. Secrets are kept in a credential backend the user
selects (keyring or keyvault ship as options); the whole secret-storage layer is
removable for projects whose config holds no secrets.

## Making a new repo from this template

1. **Clone the repo.** Name the new folder after your project:
```bash
git clone https://github.com/DrollRobot/python_repo_template.git YOUR-PROJECT-NAME
```

2. **Edit `scripts/setup.toml`** to determine which features you want to keep.

3. **Run the setup script:**
```powershell
uv run scripts/template_setup/setup_new_project.py
```
   It validates every field in the config up front — if anything is wrong,
   nothing runs and every problem is listed at once. It then previews every
   change, asks for a single confirmation, and applies everything, including
   dropping any optional features you turned off.
   Add `--dry-run` to preview only, or `-y`/`--yes` to skip the confirmation
   (the preview still runs first).

   It also replaces this README with `README.md.FIXME`, a skeleton README for
   your project, and deletes the `.FIXME` file. Work through the FIXMEs it
   leaves — including the license badge, which must match the license you
   chose. Re-running the setup will not touch that README again: the skeleton
   is gone, so the step reports "already reset" and leaves your version alone.

   This does **not** delete `scripts/template_setup/` — that stays a
   separate, manual step (`cleanup.py`, or just delete the folder) once
   you're done with it.

5. **Create the venv and install dev/test dependencies:**
```powershell
uv sync
```

6. **Install and update pre-commit hooks:**
```powershell
uv run pre-commit install
uv run pre-commit install --hook-type pre-push
uv run pre-commit autoupdate
```

7. **If you kept mkdocs, enable GitHub Pages for docs:**
   - In the GitHub repo settings, set Pages source to the `gh-pages` branch.
   - Then deploy:
```bash
uv run mkdocs gh-deploy --force
```
   The GitHub Actions workflow in `.github/workflows/docs.yml` will keep the
   site updated on future pushes to `main`.

8. **Initialize the secrets baseline:**
```powershell
uv run detect-secrets scan > .secrets.baseline
```

9. **If using GitHub App tokens to access private repos:**
   1. Create the GitHub App (in the Github account → Settings → Developer settings → GitHub Apps → New):
       - Permissions → Repository permissions → Contents: Read-only (Metadata: Read is added automatically). Nothing else.
       - Webhook → uncheck Active (you don't need events).
       - Where can this be installed → Only on this account.
   2. Mint a private key — on the App's page, "Generate a private key", download the .pem. Note the numeric App ID shown at the top.
   3. Install the App onto the repos it needs to touch.
       - Org Settings → the App → Install → select repositories.
   4. Store the credentials in connectwise-tools (the repo whose CI runs):
```bash
       gh secret set GRAPH_AUTH_APP_PRIVATE_KEY < path/to/app.private-key.pem
       gh variable set GRAPH_AUTH_CLIENT_ID --body "123456"
```
```powershell
       Get-Content -Raw path\to\app.private-key.pem | gh secret set GRAPH_AUTH_APP_PRIVATE_KEY
       gh variable set GRAPH_AUTH_CLIENT_ID --body "123456"
```
   6. Uncomment section in .github/workflows/ audit.yml, ci.yml, and docs.yml.
   7. Update actions/create-github-app-token to latest trusted commit.

10. **Write some code!**
