# Staying in sync

A child project carries one file from the template: `.template.toml`. It
records the template commit it was last synced to, its values, its feature
choices and any per-file overrides.
If you prefer not to have the `.template.toml` in git history, the sync
script will also find it at `.local/.template.toml`.

## Bringing in the template's changes

```bash
git pull
uv run scripts/template_sync.py check ../my-widget    # shows what would change
uv run scripts/template_sync.py update ../my-widget   # apply changes
```

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
