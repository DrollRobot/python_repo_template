# Getting started

Making a project from the template takes three steps; the last sections cover
what depends on the features you keep.

## 1. Clone the template

Clone it once, beside your projects. `git pull` it later to get its changes.

```bash
git clone https://github.com/DrollRobot/python_repo_template.git
```

## 2. Create the project

From the template checkout:

```bash
cd python_repo_template
uv run scripts/template_sync.py new ../my-widget --set github_user=YOUR-GITHUB-USER
```

It asks which optional features to keep, and which license to use: MIT (the
default), Apache 2.0, GPL 3.0, proprietary, or none, which leaves out the
`LICENSE` file. Then it asks for each value that `--set` didn't give and the
project's files use:

- the project name (default: the directory's name). Any of `My Widget`,
  `my-widget` or `my_widget` gives both the `my_widget` package and the
  `my-widget` distribution.
- your GitHub user (no default).
- the Python version.
- whatever the chosen license names: the copyright holder and year, and for
  the proprietary license, also the company.

The Python version defaults to the newest, 3.15, which the template always
targets. A version such as `3.13` pins the project to exactly that Python
(`requires-python = "==3.13.*"`, CI on 3.13 alone). `all-supported` makes a
library: it supports every Python version still maintained (3.11 to 3.15
today), develops and type-checks on the oldest, and CI tests all of them.

`-y` takes every default without asking; `--feature NAME=false` turns a
feature off. The target must not exist, or be empty, or be an empty git
repository (as cloning a newly created GitHub repo leaves). It writes the
project, runs `git init` on `main`, and stages everything.

Then it bootstraps the project, running the commands under `[new]` in
`template.toml`: it sets the version to 0.1.0, runs `uv sync` (writing
`uv.lock`), scans for secrets into `.secrets.baseline`, installs the pre-commit
and pre-push hooks, and stages the result. It commits nothing.
`--no-bootstrap` skips this and prints the commands instead; if a command
fails, `new` stops there and prints the ones left.

## 3. Finish the setup

In the new project:

```bash
cd ../my-widget
uv run detect-secrets audit .secrets.baseline    # mark each finding real or false
git grep -n FIXME                                # placeholders left for you to fill in
git commit
```

The pre-commit hooks refuse the commit until every baseline entry is audited.

Then write some code. To bring in the template's later changes, see
[Staying in sync](staying-in-sync.md).

## If you kept mkdocs

Enable GitHub Pages for the project's docs: in the GitHub repo's settings, set
the Pages source to the `gh-pages` branch. Then deploy:

```bash
uv run mkdocs gh-deploy --force
```

The workflow in `.github/workflows/docs.yml` keeps the site updated on later
pushes to `main`.

## If the project has private git dependencies

CI and Dependabot need credentials to read them: see
[Private dependencies](private-dependencies.md).

## If the project runs as a scheduled job

With `container_job` on, the package runs as a scheduled Azure Container Apps
job: see [Container job](container-job.md).

## If the project has settings

With `config_system` on, the package reads its settings from a per-user
`config.toml`: see [Config system](config-system.md).
