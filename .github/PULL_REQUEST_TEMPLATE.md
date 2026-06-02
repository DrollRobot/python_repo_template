<!--
=============================================================================
TEMPLATE SETUP NOTES -- remove this block - FIXME
=============================================================================
This PULL_REQUEST_TEMPLATE.md is part of python_repo_template, a starter
repo scaffold.

Purpose: GitHub pre-fills the PR description box with this content whenever
a contributor opens a pull request. It prompts them to summarize their
change and confirm they have run the required checks before requesting review.

To customize:
- Add or remove checklist items to match your project's definition of done.
- Keep the checklist short -- contributors skip long lists.
=============================================================================
-->

## Summary

<!-- One-sentence description of what this PR does. -->

## Checklist

- [ ] `uv run pre-commit run --all-files` passes clean
- [ ] `uv run pytest -m "not integration"` passes
- [ ] New code has type annotations and docstrings
- [ ] New public symbols are exported from `src/python_repo_template/__init__.py`
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`
- [ ] Docs updated if the public API changed
