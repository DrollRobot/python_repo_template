# Agent testing instructions

```
# if pyproject.toml was changed:
uv sync                               # after pyproject.toml edits
uv lock --check                       # verify lockfile in sync

# code checks and formatting
uv run ruff check .                   # lint
uv run ruff format .                  # apply ruff formatting
uv run mypy src tests                 # type check
uv run pytest -m "not integration"    # offline tests
uv run pytest                         # online and offline tests (when credentialed)
```
