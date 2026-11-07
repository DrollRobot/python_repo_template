# python-repo-template

<!-- FIXME: point these badges at your own repo, and drop the ones you don't use -->
[![CI](https://github.com/DrollRobot/python_repo_template/actions/workflows/ci.yml/badge.svg)](https://github.com/DrollRobot/python_repo_template/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.15-blue)](https://www.python.org/)
<!-- <template-only feature="license_mit"> -->
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
<!-- </template-only> -->
<!-- <template-only feature="license_apache"> -->
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
<!-- </template-only> -->
<!-- <template-only feature="license_gnu"> -->
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-green)](LICENSE)
<!-- </template-only> -->

<!-- FIXME: one paragraph -- what this package does, and who should use it -->
<!-- <template-only feature="mkdocs"> -->

Documentation: https://DrollRobot.github.io/python-repo-template/
<!-- </template-only> -->

## Installation

<!-- FIXME: replace with the real install command once the package is published -->
```bash
uv add git+https://github.com/DrollRobot/python_repo_template.git
```

## Usage

<!-- FIXME: the shortest example that does something useful -->
```python
import python_repo_template
```
<!-- <template-only feature="config_system"> -->

## Configuration

Settings live in a per-user `config.toml`, managed with the config command:

```bash
python-repo-template-config init     # interactive first-time setup
python-repo-template-config show     # the effective settings and where each comes from
python-repo-template-config set NAME VALUE
python-repo-template-config open     # open config.toml in its default program
```

Environment variables override the file; `python-repo-template-config --help` lists them.
<!-- </template-only> -->

## Development

```bash
uv sync                                     # create the venv, install dev/test deps
uv run pre-commit install                   # commit hooks
uv run pre-commit install --hook-type pre-push
uv run pytest -m "not live"                 # offline tests
```

<!-- FIXME: anything a contributor needs that the commands above don't cover --
     required config options, credentials, external services, test fixtures -->
<!-- <template-only feature="container_job"> -->

## Running as a container job

The package runs as a scheduled Azure Container Apps job. Each push to `main`
builds the image (`Dockerfile`), publishes it, and points the job at it
(`.github/workflows/image.yml`); a weekly scheduled run rebuilds `main` on a
freshly pulled base image.

To run unattended, the package must:

- resolve its settings with no `config.toml`: from defaults, or from
  environment variables set on the job (`[job.env]` in the job config);
- read secrets through the job's managed identity (for example the Key Vault
  backend, whose `DefaultAzureCredential` picks the identity from an
  `AZURE_CLIENT_ID` variable on the job);
- log to the console (Log Analytics keeps the output), and never prompt or
  open a browser;
- import every module from `python-repo-template --help`, the image's smoke test.

The job and everything it needs is described by a config file kept outside
the repository's tracked files (start from `scripts/container_job.example.toml`).
To create the job, or to change its schedule, arguments or alert later, edit
that file and run, with an account that can grant roles:

```bash
uv run scripts/container_job.py path/to/your.toml --dry-run
uv run scripts/container_job.py path/to/your.toml
```
<!-- </template-only> -->

## License

<!-- <template-only feature="license_mit"> -->
MIT; see [LICENSE](LICENSE).
<!-- </template-only> -->
<!-- <template-only feature="license_apache"> -->
Apache License 2.0; see [LICENSE](LICENSE).
<!-- </template-only> -->
<!-- <template-only feature="license_gnu"> -->
GNU General Public License v3.0; see [LICENSE](LICENSE).
<!-- </template-only> -->
<!-- <template-only feature="license_proprietary"> -->
Proprietary; all rights reserved. See [LICENSE](LICENSE).
<!-- </template-only> -->
