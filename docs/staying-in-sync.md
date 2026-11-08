# Staying in sync

A project carries one file from the template: `.template.toml`, which records
the template commit it was last synced to (its pin), its values, its feature
choices and any per-file overrides. To keep even that out of the project's
history, move it to `.local/.template.toml` (`.local/` is gitignored); a fresh
clone then needs its own copy. Delete it to cut the project loose from the
template.

## Bringing in the template's changes

Commit the project's work, then from the template checkout:

```bash
git pull
uv run scripts/template_sync.py check ../my-widget    # what would change; writes nothing
uv run scripts/template_sync.py update ../my-widget   # apply it, then review and commit
```

`update` also asks about features the template has gained, and records the
answers in `.template.toml`.

## What each file gets

`template.toml` decides:

- **managed** files belong to the template: `update` brings them to the
  template's version, and `check` reports local edits as drift.
- **merge** files are edited on both sides (`pyproject.toml`, `AGENTS.md`,
  `.gitignore`, ...): `update` 3-way merges the template's changes into the
  project's copy, and leaves conflict markers, like git, when both sides
  changed the same lines.
- **seed** files (`README.md`, `src/<package>/main.py`, ...) are written once,
  by `new`. `update --seed` installs seed files the template has added since.

## Forking a managed file

A project that forks a managed file for good sets, for example,
`"src/my_widget/config/cli.py" = "merge"` (or `"ignore"`) under `[overrides]`
in `.template.toml`.

## Moving to another Python version

A project keeps the Python version it was created with when the template moves
on. To move it, change `python` in `.template.toml` and run `update`, which
rewrites the managed files. Then make the same change by hand in the merge
files that name the version (`pyproject.toml`, `.python-version`, and
`CONTRIBUTING.md` if you have it), since `update` keeps your copies of those.

## Projects from the old setup scripts

Projects created with the old setup scripts (`scripts/template_setup/`) have no
`.template.toml`: `check` prints a skeleton to fill in. Pin it to the last
template commit before the switch to `template_sync.py`. The first `update`
then replaces the managed files outright, and may leave conflicts in
`AGENTS.md` and `pyproject.toml` to resolve once.
