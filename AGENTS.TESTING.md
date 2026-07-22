# Tests

## Writing tests

- All new code should have unit and integration tests, and e2e/live tests
    wherever possible.
- All tests should use the tag system described below. Tests MUST have at least one Scope tag (`unit`, `integration`, or `e2e`).

### Test Tags

| Tag | Axis | Description |
|------|------|-------------|
| `unit` | Scope | Single function/class in isolation; all dependencies mocked or stubbed. |
| `integration` | Scope | Multiple real components wired together across a boundary. |
| `e2e` | Scope | Whole application end to end, driven like a real user. |
| `smoke` | Purpose | Fast "is it fundamentally broken" check. |
| `regression` | Purpose | Guards against reintroduction of a previously fixed bug. |
| `acceptance` | Purpose | Verifies behavior against a requirement or user-facing spec. |
| `functional` | Purpose | Tests behavior/output of a feature without regard to internal structure. |
| `live` | Dependency | Requires a real external resource — network, live tenant, secrets, third-party API. |
| `destructive_local` | Dependency | Mutates the host/device running pytest. Skipped by default. |
| `destructive_remote` | Dependency | Mutates a remote/external system. Skipped by default. |
| `slow` | Performance | Long-running. |

## Running tests

- Tests should be run before committing code.
- Use the test procedure below.
```
# if pyproject.toml was changed:
uv sync
uv lock --check

# code checks and formatting
uv run ruff check .                 # lint
uv run ruff format .                # apply ruff formatting
uv run mypy                         # type check (targets set in pyproject.toml)

# if package requires cross-platform support: type check the other OS targets
# (bare `uv run mypy` above only checks the host platform)
uv run mypy --platform win32        # type check as Windows
uv run mypy --platform darwin       # type check as macOS
uv run mypy --platform linux        # type check as Linux

# tests (destructive tests are skipped by default; see note below)
uv run pytest -m "not live"         # offline tests
uv run pytest                       # live and not-live tests (when credentialed)
```

## Destructive tests

Destructive tests never run in the normal procedure above. There are two
independent categories, mutating two different things, gated completely
separately so that clearing one can never accidentally arm the other:

| | `destructive_local` | `destructive_remote` |
|---|---|---|
| Mutates | This host/device | A remote/external system (cloud resource, database, API tenant, ...) |
| Collection gate | `--run-destructive-local` | `--run-destructive-remote` |
| Execution gate | `DISPOSABLE_ENVIRONMENT=1` (machine-wide env var) | `tests/verify_remote_disposable.py` exits 0 |

Both categories require BOTH their own layers (flag + gate) to run. Neither
category's gate says anything about the other: `DISPOSABLE_ENVIRONMENT` only
describes this machine, and the remote check only describes whichever target
this project's own configuration currently points at.

### Local destructive tests

If the package contains `destructive_local` tests, check the
`DISPOSABLE_ENVIRONMENT` environment variable.
- `0` = User says this system is not disposable; never run `destructive_local` tests.
- `1` = User has decided this system is disposable; ask user once per session if
    `destructive_local` tests should be run.
- Not set = the system has not been assessed. Do NOT run `destructive_local`
    tests. Provide the user the commands below and ask them to set the
    variable. Any value other than `1` (including typos or an unset variable)
    is treated as non-disposable, so `0` is the safe choice.

Use `0` on a normal machine (never run `destructive_local` tests here). Use `1`
ONLY on a disposable VM/container you are willing to have mutated. The
commands below show `0`; change it to `1` only on a throwaway host. Each takes
effect in new sessions, not the shell that runs it.
```
# windows (admin PowerShell)
[Environment]::SetEnvironmentVariable('DISPOSABLE_ENVIRONMENT','0','Machine')

# linux
echo 'DISPOSABLE_ENVIRONMENT=0' | sudo tee -a /etc/environment

# macOS
echo 'export DISPOSABLE_ENVIRONMENT=0' | sudo tee -a /etc/zprofile
```

**Agents must NEVER set `DISPOSABLE_ENVIRONMENT` themselves.**

If the package contains `destructive_local` tests, the variable is set, and
the user has approved running them in this session, run:
```
uv run pytest --run-destructive-local
```

### Remote destructive tests

`destructive_remote` tests mutate a remote/external system that this
project's own configuration points at (environment variables, a settings
file, IaC state, ... — there is no guarantee a given project even uses a
`.env` file), not this host, so gating them on a local variable would be the
wrong fix in the opposite direction: whether a remote target is safe to
destroy has nothing to do with this machine, and a local flag would silently
follow that configuration to a different, unmarked (or worse, production)
target.

Instead, the remote target itself carries the marker, in whatever form that
system supports (a resource tag, a database marker row, a custom field on an
API tenant, ...). Each project supplies a pair of scripts:

- `scripts/mark_remote_disposable.py` — run manually, rarely (once per
  target, or to renew an expiring marker). Confirms with a human, then writes
  the marker onto the actual remote resource the project is currently
  configured to point at.
- `tests/verify_remote_disposable.py` — run automatically by the test
  suite the first time a `destructive_remote` test executes in a session.
  Reads the marker back from that same resource and exits 0 if present and
  unexpired, non-zero otherwise. Its exit code is the only thing pytest
  reads. It lives in `tests/`, not `scripts/`, because its only caller is
  the test suite's own gate.

Both ship as stubs in this template (the marker mechanism is project- and
system-specific) — see the FIXME in each script's docstring. Until
`tests/verify_remote_disposable.py` is implemented, `destructive_remote` tests fail
closed with a clear message.

**Agents must NEVER implement `mark_remote_disposable.py`'s case-by-case
logic, or run it, without the user's explicit direction.** Its entire job is
asserting "it is safe to destroy this remote thing" — treat it with at least
the same caution as `DISPOSABLE_ENVIRONMENT` above, not less.

If the package contains `destructive_remote` tests and the user has approved
running them in this session, run:
```
uv run pytest --run-destructive-remote
```

Running both categories together: pass both flags.
```
uv run pytest --run-destructive-local --run-destructive-remote
```
