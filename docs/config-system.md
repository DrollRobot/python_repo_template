# Config system

With the `config_system` feature on, a project's package gets a config package
(`src/<package>/config/`): per-user settings that live outside the repo, a
command to manage them, and, with `secret_storage` on, secrets kept in a
credential store instead of a file.

The examples below are for a project named `my-widget`: package `my_widget`,
config command `my-widget-config`, environment variable prefix `MY_WIDGET_`.

## Where settings live

Each user's settings live in a `config.toml` in the operating system's standard
per-user config folder, never in the repo:

| OS | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\my_widget\config.toml` |
| Linux | `~/.config/my_widget/config.toml` (respects `XDG_CONFIG_HOME`) |
| macOS | `~/Library/Application Support/my_widget/config.toml` |

`MY_WIDGET_CONFIG_DIR` moves the folder elsewhere, for tests and portable
installs. `my-widget-config path` prints where the file is.

## Defining settings

`src/my_widget/config/schema.py` defines every setting, in one dataclass,
`Settings`. Each field is declared with `option()` or `secret()`:

- `option(help=..., default_value=...)` is a plain setting. Without a
  `default_value`, it is required.
- `secret(help=...)` is a secret. It has no default, since secret values never
  appear in source, so it is always required.

Everything else follows from the fields: the config command's prompts, the
file's validation, the environment variable names, and the tests' fake
configurations. To add, rename or remove a setting, edit `Settings` and nothing
else. The template's example fields (`tenant_id`, `client_id`, `api_url`, ...)
are placeholders to replace.

## Reading settings in code

```python
from my_widget.config import load_settings

settings = load_settings()  # or load_settings(profile="contoso")
settings.api_url
```

## Where each value comes from

`load_settings` takes each field from the first of these that has it:

1. Explicit overrides passed by the calling code, for example from command-line
   flags.
2. An environment variable: `MY_WIDGET_` plus the field's name in capitals,
   such as `MY_WIDGET_API_URL`. This is the path for CI and other headless
   runs: it needs no file and no credential store.
3. The credential store, for secret fields only (see [Secrets](#secrets)).
4. `config.toml`: the selected profile's table, then the file's top-level keys.
5. The field's default, for plain settings only.

A required value missing from every layer raises an error that names the fix.
`load_settings` never creates the file and never falls back silently.

## Profiles

One file can hold settings for several tenants or environments. Top-level keys
act as shared fallbacks:

```toml
# Single profile: bare top-level keys.
tenant_id = "..."

# Several profiles. Top-level keys are shared fallbacks.
default_profile = "contoso"

[profiles.contoso]
tenant_id = "..."
```

The profile in use is the first of: the `profile` argument to
`load_settings`, the `MY_WIDGET_PROFILE` environment variable, the file's
`default_profile`, else the top-level keys as one unnamed profile.

The file is validated strictly: an unknown key, a secret value written into
the file, or a malformed table stops the program with an error naming the
problem.

## The config command

`my-widget-config` (also `python -m my_widget.config`) creates and edits the
file, so it never needs editing by hand. It keeps the comments in the file.

| Command | What it does |
|---|---|
| `init` | Interactive first-time setup: prompts for each setting from the schema. `--force` starts the file over. |
| `path` | Print the file's location. |
| `open` | Open the file in the program the OS associates with it. |
| `show` | The effective settings, with secrets masked and where each value came from. |
| `set KEY VALUE` | Write a plain setting. |
| `unset KEY` | Remove a setting, so its default applies again. |
| `list-profiles` | List the profiles, marking the default. |
| `use PROFILE` | Set `default_profile`. |
| `set-secret KEY` | Store a secret (see below). |
| `delete-secret KEY` | Remove a secret from the credential store. |

The two secret commands exist only when the schema has secret fields and its
`CREDENTIAL_BACKEND` isn't `"none"`.

## Secrets

With `secret_storage` on, secret values live in a credential store, never in
`config.toml` or on the command line, where shell history would keep them.
The file holds only each secret's storage name (`<field>_secret_name`), which
is not itself secret. `init` and `set-secret` ask for the name in plain view
and for the value hidden.

`CREDENTIAL_BACKEND` in `schema.py` sets the policy:

- `"prompt"` (the template's default): no default store; the user picks one
  with `credential_backend` in `config.toml`, through `init` or `set`.
- a store's name, such as `"keyring"`: that store, unless a profile picks
  another with `credential_backend`.
- `"none"`: the package stores no secrets. Environment variables still work.

Two stores ship, each its own feature:

- **keyring** (the `keyring` feature): the OS credential store, which is
  Windows Credential Manager, macOS Keychain, or the Linux Secret Service.
  Secrets are filed per profile, under the service `my_widget:<profile>` (or
  `my_widget` for the unnamed profile). On a machine with no usable store (a
  container, CI, Linux without a Secret Service) it stops with an error; it
  never falls back to writing secrets to a file.
- **keyvault** (the `keyvault` feature): Azure Key Vault, read-only. A profile
  sets `credential_backend = "keyvault"` and `keyvault_url`. It signs in with
  `DefaultAzureCredential` (`az login`, a managed identity, or service
  principal variables) and keeps values in memory only. Change secrets in the
  Azure portal or with `az keyvault secret set`. Key Vault names can't hold
  underscores, so `client_secret` is looked up as `client-secret`.

Each store's packages are an optional extra, so users install only the one
they use: `pip install my-widget[keyring]`, or `uv sync --extra keyring`.

## Leaving parts out

Each part is a feature you can decline at `new`:

- `config_system` off: no config package at all.
- `secret_storage` off: settings without secrets. The secret machinery is left
  out, and secret-related keys are illegal in `config.toml`.
- `keyring` or `keyvault` off: that store is left out, with its optional
  dependencies.
