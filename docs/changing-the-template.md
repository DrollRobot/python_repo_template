# Changing the template

For the template's maintainers.

## Classifying files

- Classify every new tracked file in `template.toml`, under `[base]` or the
  feature it belongs to.
- Files only projects get live under `child/`, which `[paths]` maps to the
  project's root: `child/README.md` becomes the project's `README.md`.
- Moving a file in the template is safe as long as it still lands on the same
  project path: `update` treats the new path as the old file, and merges
  against the old path's content.
- The template's own README, `mkdocs.yml` and `docs/` (this site) are
  excluded: projects get `child/mkdocs.yml` and `child/docs/` instead. Files a
  project ships can't link to this site, because token substitution would
  rewrite the URL; they name the page instead, as `docs/<page>.md` in the
  template.

## Keeping parts of a file out of projects

Wrap anything that must not reach projects in a `<template-only>` block: a
line holding only the tag in the file's comment syntax (`# <template-only>` ...
`# </template-only>`). Tagged blocks (`feature="mkdocs"`, `feature="!mkdocs"`,
`feature="a|b"`) reach only the projects whose features match.

## Moving to a new Python release

When Python ships a release (in October, when the oldest version also reaches
end of life), move the version tokens in `template.toml` and their literals in
the files together: `.python-version`, `pyproject.toml`, the CI matrix, the
badges and `CONTRIBUTING.md`. The `all-supported` aliases list the maintained
versions. `tests/test_template_manifest.py` fails on any literal left behind.

## Checking your changes

- `uv run scripts/template_sync.py validate` checks all of the above; the
  pre-commit hook and CI run it too.
- CI also makes four projects from the template (defaults, every feature off,
  every feature on, a library) and runs each one's gates. Run the same check
  locally with `uv run pytest tests/test_template_manifest.py -m live`.
- `uv run mkdocs build --strict` builds this site.
