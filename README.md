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

…or run any step on its own — see `scripts/template_setup/README.md` for the
full list of steps and what each one does.

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

**If using Github App tokens to access private repos**
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
6. Uncomment section in .github/workflows/ audit.yml and ci.yml.
7. Update actions/create-github-app-token to latest trusted commit.


**Write some code!**

## Optional features & how to remove them

Each optional feature is isolated so you can delete it cleanly: remove the listed
files and config lines, then re-lock with `uv lock`.

| Feature | Delete | Remove from config |
| --- | --- | --- |
| **Credentials (keyring)** | `tests/_bootstrap.py`, `scripts/setup_credentials.py` | the `keyring` dep in `pyproject.toml`; the credentials block in `.env.example`; the commented credentials example in `tests/conftest.py`; the keyring bullet above |
| **Azure KeyVault backend** | `tests/_keyvault.py` | the `keyvault` group + its `dev` include in `pyproject.toml`; the `KEYVAULT_*` block in `.env.example` (leave `CREDENTIAL_BACKEND=keyring`) |
| **MkDocs docs** | `docs/`, `mkdocs.yml`, `.github/workflows/docs.yml` | the `docs` group + its `dev` include in `pyproject.toml`; the mkdocs bullet + Pages step above |
| **Worktree scripts** | `scripts/new_worktree.py`, `scripts/complete_worktree.py`, `scripts/remove_worktree.py`, and their `tests/test_*.py` | — (`scripts/_cli.py` stays; the release script uses it) |
| **Template drift check** | `scripts/compare_to_template.py`, `tests/test_compare_to_template.py` | — |
| **Release script** | `scripts/push_new_tag_to_main.py` | — (also delete `scripts/_cli.py` if nothing else uses it) |

The common case — dropping KeyVault but keeping keyring — is just deleting
`tests/_keyvault.py` and the two `pyproject.toml` lines; the keyring path keeps
working with no other changes.
