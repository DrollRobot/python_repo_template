# python-repo-template
[![CI](https://github.com/DrollRobot/python_repo_template/actions/workflows/ci.yml/badge.svg)](https://github.com/DrollRobot/python_repo_template/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.15-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE) # FIXME replace with correct license link

My goal with this project is to create a baseline for new python projects with the
most commonly used tools and community best practices.

It's mostly for personal use. But maybe others will have suggestions? Or find it
useful themselves?

# Tool choices
Based on personal preference, and what I understand are the most widely used
tools in the Python ecosystem.

- **uv** for dependency management and virtual environments. Fast. Allows very simple
   package install directly from Github.
- **pre-commit** for git hooks to run checks before commits.
- **ruff** for linting and formatting. Fast.
- **mypy** for static type checking.
- **vulture** for dead code detection.
- **pytest** for testing.
- **mkdocs** for documentation. Integrates easily with GitHub Pages for hosting.
- **keyring** for local credential storage (optional backend). Cross-platform. Allows
   never keeping secrets in the repo.
- **Azure Keyvault** for remote secret storage (optional backend).
- **detect-secrets** for real-time secret scanning. Helps prevent accidental leaks.
- **GitHub Actions** for CI and docs deployment. Free for public repos, and widely used.
- **VSCode** for development. Most popular IDE. Lots of documentation. Many extensions.
- **Claude Code** as the coding agent.


# Design choices
Some of the design choices I've made for this project:

- **Configuration system.** An optional feature that allows keeping a project's settings
  in `config.toml` in the operating system's user config folder, not in the repo.
  Secrets live in a credential store (the OS keyring or Azure Key Vault). Allows
  environment variable override, for CI. A config command creates and edits the file.
  See the [config system](https://drollrobot.github.io/python_repo_template/config-system/)
  docs.

# Documentation
https://drollrobot.github.io/python_repo_template/

- [Getting started](https://drollrobot.github.io/python_repo_template/getting-started/): create a project from the template.
- [Staying in sync](https://drollrobot.github.io/python_repo_template/staying-in-sync/): bring the template's later changes into a project.
- [Config system](https://drollrobot.github.io/python_repo_template/config-system/): how a project's settings and secrets work.
- [Private dependencies](https://drollrobot.github.io/python_repo_template/private-dependencies/): let CI and Dependabot read private git dependencies.
- [Container job](https://drollrobot.github.io/python_repo_template/container-job/): run the package as a scheduled Azure Container Apps job.
- [Changing the template](https://drollrobot.github.io/python_repo_template/changing-the-template/): for the template's maintainers.
