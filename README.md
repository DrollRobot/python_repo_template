# python-repo-template

<!-- FIXME: replace badges below with your own CI/PyPI/coverage links -->
[![CI](https://github.com/FIXME/python-repo-template/actions/workflows/ci.yml/badge.svg)](https://github.com/FIXME/python-repo-template/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE) # FIXME replace with correct license link

<!-- FIXME: one paragraph describing what this package does and who should use it -->

My goal with this project is to create a baseline for new python projects with the most commonly used tools and community best practices.

It's mostly for personal use, but maybe others will have suggestions for improvements, or find it useful themselves?


## Making a new repo from this template

- Clone the repo. Name the new folder after your project:
```bash
git clone https://github.com/DrollRobot/python_repo_template.git YOUR-PROJECT-NAME
```
- Rename `src/python_repo_template/` folder to your project name.

- Perform a recursive find/replace replacing 'python_repo_template' with your_project_name.
- Perform a recursive find/replace replacing 'python-repo-template' with your-project-name.

**Choose a license**

- The template includes a few of the common licenses. Pick one, delete the others.
- Rename the chosen file to `LICENSE`. (no file extension)
- Update the copyright year and name inside the LICENSE file.
- Update the license badge in the README.md (this) file.

**Fill in all `FIXME` placeholders**

- Perform a recursive search for 'FIXME', and fix all issues found.

** Create venv and install basic dev/test dependencies**

```powershell
uv sync
```

**Install and update pre-commit hooks**

```powershell
uv run pre-commit install
uv run pre-commit autoupdate
```

**Create CLAUDE.md symlink**

- Claude AI does not read `AGENTS.md` directly; a symlink lets it see the same instructions.
```powershell
# Windows
New-Item -ItemType SymbolicLink -Path CLAUDE.md -Target AGENTS.md
```
```bash
# macOS / Linux
ln -s AGENTS.md CLAUDE.md
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


