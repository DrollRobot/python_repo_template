# python-repo-template
<!-- FIXME: replace badges below with your own CI/PyPI/coverage links -->
[![CI](https://github.com/FIXME/python-repo-template/actions/workflows/ci.yml/badge.svg)](https://github.com/FIXME/python-repo-template/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.15-blue)](https://www.python.org/)
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
- **vulture** for dead code detection.
- **pytest** for testing.
- **mkdocs** for documentation. Integrates easily with GitHub Pages for hosting.
- **keyring** for local credential storage (optional backend). Cross-platform. Allows never keeping secrets in the repo.
- **Azure Keyvault** for remote secret storage (optional backend).
- **detect-secrets** for real-time secret scanning. Helps prevent accidental leaks.
- **GitHub Actions** for CI and docs deployment. Free for public repos, and widely used.
- **VSCode** for development. Most popular IDE. Lots of documentation. Many extensions.
- **Claude Code** as the coding agent. Includes baseline settings.

# Design choices
Some of the design choices I've made for this project:

- **No .env** To avoid keeping secrets/environment values within the repo, project
keeps non-secret environment values and user config options in a config.toml in a
standard OS-specific location. Secrets are kept in a credential backend (keyring or
keyvault ship as options): the schema's `CREDENTIAL_BACKEND` policy either names a
default backend, makes the user choose, or disables secret storage outright, and
the whole secret-storage layer is removable for projects whose config holds no
secrets.

## Making a new project from this template

1. **Clone the template** once, beside your projects (`git pull` it later to get
   its changes):
```bash
git clone https://github.com/DrollRobot/python_repo_template.git
```

2. **Create the project** from the template checkout:
```bash
cd python_repo_template
uv run scripts/template_sync.py new ../my-widget --set github_user=YOUR-GITHUB-USER
```
   It asks for whatever `--set` didn't give: the project name (default: the
   directory's name; any of `My Widget`, `my-widget` or `my_widget` gives both the
   `my_widget` package and the `my-widget` distribution), your GitHub user (no
   default), the Python version, the copyright holder, year and company for the
   license, and which optional features to keep, one license among them (MIT
   unless you decline it).

   The Python version defaults to the newest, 3.15, which the template always
   targets. A version such as `3.13` pins the project to exactly that Python
   (`requires-python = "==3.13.*"`, CI on 3.13 alone). `all-supported` makes a
   library: it supports every Python version still maintained (3.11 to 3.15 today),
   develops and type-checks on the oldest, and CI tests all of them.
   `-y` takes every default without asking; `--feature NAME=false` turns a feature
   off. The target must not exist, or be empty, or be an empty git repository (as
   cloning a newly created GitHub repo leaves). It writes the project, runs
   `git init` on `main`, and stages everything. It commits nothing.

3. **Finish the setup** in the new project:
```bash
cd ../my-widget
uv version 0.1.0                                 # it starts at the template's version
uv sync                                          # writes uv.lock
uv run detect-secrets scan > .secrets.baseline   # then audit every entry:
uv run detect-secrets audit .secrets.baseline
uv run pre-commit install
uv run pre-commit install --hook-type pre-push
git grep -n FIXME                                # placeholders left for you to fill in
git add -A && git commit
```

4. **If you kept mkdocs, enable GitHub Pages for docs:**
   - In the GitHub repo settings, set Pages source to the `gh-pages` branch.
   - Then deploy:
```bash
uv run mkdocs gh-deploy --force
```
   The GitHub Actions workflow in `.github/workflows/docs.yml` will keep the
   site updated on future pushes to `main`.

5. **If the project has private git dependencies:**
   1. Create the GitHub App (in the Github account → Settings → Developer settings → GitHub Apps → New):
       - Permissions → Repository permissions → Contents: Read-only (Metadata: Read is added automatically). Nothing else.
       - Webhook → uncheck Active (you don't need events).
       - Where can this be installed → Only on this account.
   2. Mint a private key — on the App's page, "Generate a private key", download the .pem. Note the Client ID shown at the top.
   3. Install the App onto the repos it needs to touch.
       - Org Settings → the App → Install → select repositories.
   4. Save the credentials in the repo using the private dependencies. The key is saved
      twice because Dependabot's CI runs can't read the repo's other secrets:
```bash
      gh secret set GH_APP_PRIVATE_KEY < path/to/app.private-key.pem
      gh secret set GH_APP_PRIVATE_KEY --app dependabot < path/to/app.private-key.pem
      gh variable set GH_APP_CLIENT_ID --body "<app client id>"
```
```powershell
      Get-Content -Raw path\to\app.private-key.pem | gh secret set GH_APP_PRIVATE_KEY
      Get-Content -Raw path\to\app.private-key.pem | gh secret set GH_APP_PRIVATE_KEY --app dependabot
      gh variable set GH_APP_CLIENT_ID --body "<app client id>"
```
   5. Give Dependabot its own token. Dependabot can't use the App key when it updates
      `uv.lock`, so it reads the private repos with a fine-grained personal access token
      (Settings → Developer settings → Personal access tokens → Fine-grained tokens):
       - Resource owner → the account that owns the private repos.
       - Repository access → Only select repositories → the private repos the project depends on.
       - Permissions → Repository permissions → Contents: Read-only. Nothing else.
       - Expiration → set one, and note when to rotate it.

      Save it in the repo (`gh` prompts for the value):
```bash
      gh secret set DEPENDABOT_GIT_TOKEN --app dependabot
```
   6. Uncomment the commented-out blocks that start with `# FIXME uncomment` in
      .github/workflows/ audit.yml, ci.yml, docs.yml and (with `container_job`)
      image.yml, and both in .github/dependabot.yml.
   7. Update actions/create-github-app-token to latest trusted commit.

6. **If the project runs as a scheduled Azure Container Apps job** (`container_job` on):
   1. Check the package can run unattended in a container:
       - Its settings resolve with no `config.toml`: from defaults, or from environment
         variables set on the job (`[job.env]` in the config below).
       - It reads secrets through the job's managed identity: azure-auth with
         `managed_identity=True`, or the Key Vault backend, whose
         `DefaultAzureCredential` picks the identity from an `AZURE_CLIENT_ID`
         variable on the job.
       - It logs to the console; Log Analytics keeps the output. It needs no log file.
       - It never prompts or opens a browser.
       - `<entrypoint> --help` imports every module: it is the image's smoke test.
   2. Copy `scripts/container_job.example.toml` somewhere outside the repo's tracked
      files, fill it in, and run the setup with an account that can grant roles:
```bash
      uv run scripts/container_job.py path/to/your.toml --dry-run
      uv run scripts/container_job.py path/to/your.toml
```
      It creates whatever is missing (the shared resources, the job's identities and
      roles, the job, the repo variables, the failed-run alert) and updates whatever
      differs from the file. To change a setting later, edit the file and run it again.
   3. The job starts on Microsoft's quickstart image. Each push to `main` then builds
      the package's image, publishes it, and points the job at it. A scheduled run also
      rebuilds `main` weekly on a freshly pulled base image; the cron in `image.yml`
      sets how often.

7. **Write some code!**

## Staying in sync

A project carries one file from the template: `.template.toml`, which records the
template commit it was last synced to (its pin), its values, its feature choices
and any per-file overrides. To keep even that out of the project's history, move
it to `.local/.template.toml` (`.local/` is gitignored); a fresh clone then needs
its own copy. Delete it to cut the project loose from the template.

To bring the template's later changes into a project, commit the project's work,
then from the template checkout:
```bash
git pull
uv run scripts/template_sync.py check ../my-widget    # what would change; writes nothing
uv run scripts/template_sync.py update ../my-widget   # apply it, then review and commit
```

`template.toml` decides what each file gets:

- **managed** files belong to the template: `update` brings them to the template's
  version, and `check` reports local edits as drift.
- **merge** files are edited on both sides (`pyproject.toml`, `AGENTS.md`,
  `.gitignore`, ...): `update` 3-way merges the template's changes into the
  project's copy, and leaves conflict markers, like git, when both sides changed
  the same lines.
- **seed** files (`README.md`, `src/<package>/main.py`, ...) are written once, by
  `new`. `update --seed` installs seed files the template has added since.

A project keeps the Python version it was created with when the template moves
on. To move it, change `python` in `.template.toml` and run `update`, which
rewrites the managed files; then make the same change by hand in the merge files
that name the version (`pyproject.toml`, `.python-version`, and `CONTRIBUTING.md`
if you have it), since `update` keeps your copies of those.

A project that forks a managed file for good sets, for example,
`"src/my_widget/config/cli.py" = "merge"` (or `"ignore"`) under `[overrides]` in
`.template.toml`. `update` also asks about features the template has gained and
records the answers there.

Projects created with the old setup scripts (`scripts/template_setup/`) have no
`.template.toml`: `check` prints a skeleton to fill in. Pin it to the last template
commit before the switch to `template_sync.py`. The first `update` then replaces the
managed files outright and may leave conflicts in `AGENTS.md` and `pyproject.toml`
to resolve once.

## Changing the template

- Classify every new tracked file in `template.toml`, under `[base]` or the
  feature it belongs to.
- Wrap anything that must not reach projects in a `<template-only>` block: a line
  holding only the tag in the file's comment syntax (`# <template-only>` ...
  `# </template-only>`). Tagged blocks (`feature="mkdocs"`, `feature="!mkdocs"`,
  `feature="a|b"`) reach only the projects whose features match.
- When Python ships a release (in October, when the oldest version also reaches
  end of life), move the version tokens in `template.toml` and their literals in
  the files together: `.python-version`, `pyproject.toml`, the CI matrix, the
  badges and `CONTRIBUTING.md`. The `all-supported` aliases list the maintained
  versions. `tests/test_template_manifest.py` fails on any literal left behind.
- `uv run scripts/template_sync.py validate` checks all of it; the pre-commit hook
  and CI run it too.
- CI also makes four projects from the template (defaults, every feature off,
  every feature on, a library) and runs each one's gates. Run the same check
  locally with `uv run pytest tests/test_template_manifest.py -m live`.
