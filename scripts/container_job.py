"""Create or update a repo's Azure Container Apps jobs, and all they need, from a config file.

The config file is the whole description of the repo's jobs: the shared resources they
run on, the identities they run as and the Key Vault secrets each may read, each job's
schedule and arguments, the GitHub repo variables the image workflow uses, and the
failed-run alerts. To change something later, edit the file and run this script again.
Each run compares the file with Azure and GitHub, lists every difference, and asks once
before changing anything. ``--dry-run`` lists the differences and stops.

What it manages, in order:

1. Shared resources (resource providers, resource group, registry, Log Analytics
   workspace, virtual network and subnet, Container Apps environment): created when
   missing. Existing ones are never changed, since some of their settings can't change
   in place; differences that matter are reported.
2. The identities the jobs run as: the right to pull the repo's image and no other, Key
   Vault Secrets User on exactly the secrets listed for each, and the vault's network
   rule for the environment's subnet.
3. The jobs: schedule, arguments, environment variables, CPU, memory, timeout. Every job
   runs the repo's one image. A new job starts on the image the repo's other jobs run,
   or on Microsoft's public quickstart image when there is none yet; after that the
   image is never touched, since the release workflow (``.github/workflows/image.yml``)
   owns it.
4. The CI identity the image workflow signs in as: the right to push the repo's image
   and no other, rights to update the jobs, and a federated credential trusting the
   repo's ``main`` branch.
5. The repo variables the image workflow reads.
6. The failed-run alerts, one per job, and the email they go to.
7. Jobs, alert rules and identities the file no longer lists: removed.

The resource group is shared with other repos' jobs, so every job, alert rule and
identity this script manages carries the tag ``container-job-repository``, set to the
repo's name. Only resources with this repo's tag are ever removed, and a run stops when
a name in the file belongs to another repo's resource.

The registry is shared with other repos too, so every registry role granted here is
limited to the repo's own image name, which is the GitHub repo's name. One repo's CI
then can't replace the image another repo's jobs run. Azure calls an image name a
repository, and limits a role to one only in a registry whose role assignment mode is
"RBAC Registry + ABAC Repository Permissions". A registry created here is in that mode,
and a run stops at an existing registry that is not.

It signs in through the Azure CLI (``az login``) and the GitHub CLI (``gh auth
login``). Granting roles needs Owner or User Access Administrator rights. See
``container_job.example.toml`` for every setting.

Usage:
    uv run scripts/container_job.py path/to/container_job.toml
    uv run scripts/container_job.py path/to/container_job.toml --dry-run
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, NoReturn

ARM = "https://management.azure.com"

# ARM API versions, one per resource type this script touches.
API_PROVIDERS = "2021-04-01"
API_RESOURCE_GROUPS = "2021-04-01"
API_REGISTRIES = "2025-11-01"
API_WORKSPACES = "2023-09-01"
API_WORKSPACE_KEYS = "2020-08-01"
API_NETWORK = "2024-01-01"
API_CONTAINER_APPS = "2024-03-01"
API_IDENTITIES = "2023-01-31"
API_ROLES = "2022-04-01"
API_VAULTS = "2023-07-01"
API_ACTION_GROUPS = "2023-01-01"
API_SCHEDULED_QUERIES = "2022-06-15"

# Resource providers the managed resources belong to; each must be registered on the
# subscription before its first resource can be created.
PROVIDERS = (
    "Microsoft.App",
    "Microsoft.ContainerRegistry",
    "Microsoft.Insights",
    "Microsoft.ManagedIdentity",
    "Microsoft.Network",
    "Microsoft.OperationalInsights",
)

QUICKSTART_IMAGE = "mcr.microsoft.com/k8se/quickstart-jobs:latest"
WORKLOAD_PROFILE = "Consumption"
GITHUB_ISSUER = "https://token.actions.githubusercontent.com"
FEDERATED_AUDIENCE = "api://AzureADTokenExchange"
FEDERATED_CREDENTIAL = "github-main"

# The tag on every job, alert rule and identity this script manages; its value is the
# repo whose config owns the resource. Lowercase: Azure ignores case in tag names.
REPOSITORY_TAG = "container-job-repository"

# The registry's role assignment mode in which a role can be limited to one image name.
REGISTRY_MODE = "AbacRepositoryPermissions"
# What a role assignment's condition tests: the image name an action is on. Azure calls
# an image name a repository.
IMAGE_NAME_ATTRIBUTE = "@Request[Microsoft.ContainerRegistry/registries/repositories:name]"
CONDITION_VERSION = "2.0"

# The registry roles that a condition can limit to one image name.
ROLE_IMAGE_READER = "Container Registry Repository Reader"
ROLE_IMAGE_WRITER = "Container Registry Repository Writer"
# The roles of a registry in the other mode, which cover every image in it. A registry
# in REGISTRY_MODE ignores them, and assignments left from before are removed.
ROLE_ACR_PULL = "AcrPull"
ROLE_ACR_PUSH = "AcrPush"
SUPERSEDED_ROLE = "the registry ignores this role; the one limited to the image name replaces it"
ROLE_SECRETS_USER = "Key Vault Secrets User"
ROLE_IDENTITY_OPERATOR = "Managed Identity Operator"
ROLE_JOBS_CONTRIBUTOR = "Container Apps Jobs Contributor"

# How long and how often to wait for a resource to finish provisioning. A Container
# Apps environment on a virtual network can take 20 minutes.
WAIT_TIMEOUT_SECONDS = 45 * 60
WAIT_INTERVAL_SECONDS = 15

# Provisioning states that end a wait.
_DONE_STATES = frozenset({"succeeded"})
_FAILED_STATES = frozenset({"failed", "canceled", "cancelled"})


class ConfigError(Exception):
    """The config file is missing, malformed, or has a wrong or missing setting."""


class AzureError(Exception):
    """Azure refused a request, or a resource failed to provision."""


# region output

# Console output for the run: a section header, aligned facts, commands echoed
# before they run, and colour where the terminal takes it (never with NO_COLOR,
# or when output is piped). Kept in this file so the script stands alone.


def _enable_windows_ansi() -> None:
    """Turn on ANSI escape processing in the legacy Windows console.

    Windows Terminal and VS Code handle ANSI already; this is a no-op there.
    """
    if sys.platform != "win32":  # also narrows ctypes.windll for type checkers
        return
    import ctypes

    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except (OSError, AttributeError):  # fmt: skip
        pass


def _supports_color() -> bool:
    """Decide whether to emit ANSI colours, enabling VT processing on Windows.

    Returns:
        True when stdout is a terminal and ``NO_COLOR`` is unset.
    """
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        _enable_windows_ansi()
    return True


_COLOR = _supports_color()
CYAN, GREEN, YELLOW, RED, GRAY, RESET = (
    (code if _COLOR else "")
    for code in ("\x1b[36m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[90m", "\x1b[0m")
)
_assume_yes = False


def section(title: str) -> None:
    """Print a section header.

    Args:
        title: Section title.
    """
    print()
    print(f"{CYAN}== {title} =={RESET}", flush=True)


def info(label: str, value: str) -> None:
    """Print an aligned ``label: value`` line.

    Args:
        label: Short field name.
        value: Field value.
    """
    print(f"  {GRAY}{label + ':':<20}{RESET}{value}")


def echo(command_text: str) -> None:
    """Show what is about to run.

    Args:
        command_text: The step, as a short line.
    """
    print(f"  {GRAY}> {command_text}{RESET}", flush=True)


def warn(message: str) -> None:
    """Print a warning line.

    Args:
        message: Warning text.
    """
    print(f"{YELLOW}{message}{RESET}")


def success(message: str) -> None:
    """Print a success line.

    Args:
        message: Success text.
    """
    print(f"{GREEN}{message}{RESET}")


def die(message: str) -> NoReturn:
    """Print an error and exit with status 1.

    Args:
        message: Error text.
    """
    print(f"{RED}ERROR: {message}{RESET}", file=sys.stderr)
    sys.exit(1)


def confirm(prompt: str) -> bool:
    """Ask a yes/no question; always yes under ``--yes``.

    Args:
        prompt: Question to display (without the ``[y/n]`` suffix).

    Returns:
        True for yes, False for no.
    """
    if _assume_yes:
        print(f"{prompt} [y/n] y {GRAY}(auto: --yes){RESET}")
        return True
    while True:
        answer = input(f"{prompt} [y/n] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please answer 'y' or 'n'.")


def capture(args: list[str]) -> str:
    """Run a command and return its stdout; exit the script if it fails.

    stderr streams to the terminal, so the tool's own message shows.

    Args:
        args: Command and arguments.

    Returns:
        The command's stdout, stripped.
    """
    result = subprocess.run(  # noqa: S603  (fixed argv list, no shell)
        args, stdout=subprocess.PIPE, encoding="utf-8", errors="replace", check=False
    )
    if result.returncode != 0:
        die(f"{subprocess.list2cmdline(args)} failed (exit {result.returncode})")
    return (result.stdout or "").strip()


# region config


@dataclass(frozen=True)
class Shared:
    """Resources shared by every job in the environment; see the example config."""

    subscription: str
    location: str
    resource_group: str
    registry: str
    log_workspace: str
    vnet: str
    vnet_address_prefix: str
    subnet: str
    subnet_address_prefix: str
    environment: str


@dataclass(frozen=True)
class Identity:
    """An identity jobs run as, and the secrets it may read; see the example config."""

    name: str
    secrets: tuple[str, ...]


@dataclass(frozen=True)
class Job:
    """One scheduled job; see the example config."""

    name: str
    identity: str
    schedule: str
    args: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    cpu: float
    memory: str
    timeout: int
    alert_rule: str


@dataclass(frozen=True)
class KeyVault:
    """The vault the identities read secrets from; see the example config."""

    name: str
    resource_group: str


@dataclass(frozen=True)
class Alert:
    """Who hears about failed runs; see the example config."""

    email: str
    action_group: str


@dataclass(frozen=True)
class Config:
    """A parsed and validated config file."""

    shared: Shared
    ci_identity: str
    identities: tuple[Identity, ...]
    jobs: tuple[Job, ...]
    key_vault: KeyVault | None
    alert: Alert
    repository: str


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the required TOML table *name*.

    Args:
        raw: The parsed document.
        name: Table name.

    Returns:
        The table.

    Raises:
        ConfigError: If the table is missing or isn't a table.
    """
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] is missing or is not a table.")
    return value


def _tables(raw: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Return the required array of TOML tables *name*.

    Args:
        raw: The parsed document.
        name: Array name.

    Returns:
        The tables, in file order; at least one.

    Raises:
        ConfigError: If the file has no ``[[name]]`` table, or *name* is not an array of
            tables.
    """
    value = raw.get(name)
    if not isinstance(value, list) or not value or not all(isinstance(t, dict) for t in value):
        raise ConfigError(
            f"The file needs at least one [[{name}]] table, written with double brackets."
        )
    return value


def _check_keys(table: dict[str, Any], name: str, required: set[str], optional: set[str]) -> None:
    """Fail loudly on a missing required key or an unknown key in *table*.

    Args:
        table: One TOML table.
        name: The table's name, for messages.
        required: Keys that must be present.
        optional: Keys that may be present.

    Raises:
        ConfigError: If a required key is missing or an unknown key is present.
    """
    missing = sorted(required - table.keys())
    if missing:
        raise ConfigError(f"[{name}] is missing {', '.join(missing)}.")
    unknown = sorted(table.keys() - required - optional)
    if unknown:
        raise ConfigError(f"[{name}] has unknown key(s) {', '.join(unknown)}.")


def _str(table: dict[str, Any], key: str, name: str) -> str:
    """Return a non-empty string setting.

    Args:
        table: One TOML table.
        key: Setting name.
        name: The table's name, for messages.

    Returns:
        The value.

    Raises:
        ConfigError: If the value is not a non-empty string.
    """
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"[{name}].{key} must be a non-empty string.")
    return value


def _str_list(table: dict[str, Any], key: str, name: str) -> tuple[str, ...]:
    """Return a list-of-strings setting.

    Args:
        table: One TOML table.
        key: Setting name.
        name: The table's name, for messages.

    Returns:
        The values, in order.

    Raises:
        ConfigError: If the value is not a list of strings.
    """
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"[{name}].{key} must be a list of strings.")
    return tuple(value)


def _load_identity(table: dict[str, Any], label: str, *, has_vault: bool) -> Identity:
    """Read one ``[[identity]]`` table.

    Args:
        table: The table.
        label: The table's name and position, for messages.
        has_vault: Whether the file has a ``[key_vault]`` table. With one, the identity
            must list its secrets; without one, it must not.

    Returns:
        The identity.

    Raises:
        ConfigError: If a setting is missing, unknown or of the wrong type, or the table
            lists secrets when the file has no ``[key_vault]``.
    """
    if not has_vault and "secrets" in table:
        raise ConfigError(f"[{label}] lists secrets, but the file has no [key_vault].")
    _check_keys(table, label, {"name", "secrets"} if has_vault else {"name"}, set())
    return Identity(
        name=_str(table, "name", label),
        secrets=_str_list(table, "secrets", label) if has_vault else (),
    )


def _load_job(table: dict[str, Any], label: str) -> Job:
    """Read one ``[[job]]`` table.

    Args:
        table: The table.
        label: The table's name and position, for messages.

    Returns:
        The job.

    Raises:
        ConfigError: If a setting is missing, unknown or of the wrong type.
    """
    _check_keys(
        table,
        label,
        {"name", "identity", "schedule", "args", "cpu", "memory", "timeout", "alert_rule"},
        {"env"},
    )
    cpu = table["cpu"]
    if isinstance(cpu, bool) or not isinstance(cpu, int | float) or cpu <= 0:
        raise ConfigError(f"[{label}].cpu must be a positive number of cores, such as 0.25.")
    timeout = table["timeout"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ConfigError(f"[{label}].timeout must be a positive whole number of seconds.")
    env_raw = table.get("env", {})
    if not isinstance(env_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env_raw.items()
    ):
        raise ConfigError(f"[{label}].env must be a table of string values.")
    return Job(
        name=_str(table, "name", label),
        identity=_str(table, "identity", label),
        schedule=_str(table, "schedule", label),
        args=_str_list(table, "args", label),
        env=tuple(sorted(env_raw.items())),
        cpu=float(cpu),
        memory=_str(table, "memory", label),
        timeout=timeout,
        alert_rule=_str(table, "alert_rule", label),
    )


def _repeated(names: Iterable[str]) -> list[str]:
    """Return the names that appear more than once, ignoring case as Azure does.

    Args:
        names: Resource names.

    Returns:
        The repeated names, lowercased and sorted.
    """
    counts = Counter(name.lower() for name in names)
    return sorted(name for name, count in counts.items() if count > 1)


def _check_references(
    ci_identity: str, identities: tuple[Identity, ...], jobs: tuple[Job, ...]
) -> None:
    """Fail loudly when the jobs and identities don't fit together.

    Args:
        ci_identity: The CI identity's name.
        identities: Every ``[[identity]]`` table.
        jobs: Every ``[[job]]`` table.

    Raises:
        ConfigError: If two tables share a job name, alert rule or identity name, a job
            runs as an identity no table defines, no job runs as an identity, or the CI
            identity is also a job's identity.
    """
    for what, names in (
        ("[[job]] table has the name", [job.name for job in jobs]),
        ("[[job]] table has the alert_rule", [job.alert_rule for job in jobs]),
        ("[[identity]] table has the name", [identity.name for identity in identities]),
    ):
        repeated = _repeated(names)
        if repeated:
            raise ConfigError(f"More than one {what} {', '.join(repeated)}.")
    defined = {identity.name.lower() for identity in identities}
    for job in jobs:
        if job.identity.lower() not in defined:
            raise ConfigError(
                f"Job {job.name} runs as {job.identity}, which no [[identity]] table defines."
            )
    used = {job.identity.lower() for job in jobs}
    unused = sorted(identity.name for identity in identities if identity.name.lower() not in used)
    if unused:
        raise ConfigError(f"No job runs as the identity {', '.join(unused)}.")
    if ci_identity.lower() in defined:
        raise ConfigError(
            f"[ci].identity {ci_identity} is also a job's identity; the image workflow needs "
            "an identity of its own."
        )


def load_config(path: Path) -> Config:
    """Read and validate the config file at *path*.

    Args:
        path: The TOML file.

    Returns:
        The parsed config.

    Raises:
        ConfigError: If the file is missing or malformed, a setting is missing, unknown,
            or of the wrong type, or the jobs and identities don't fit together.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"No config file at {path}.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    _check_keys(
        raw, "the top level", {"shared", "ci", "identity", "job", "alert", "github"}, {"key_vault"}
    )

    shared_t = _table(raw, "shared")
    shared_keys = {f.name for f in fields(Shared)}
    _check_keys(shared_t, "shared", shared_keys, set())
    shared = Shared(**{key: _str(shared_t, key, "shared") for key in shared_keys})

    ci_t = _table(raw, "ci")
    _check_keys(ci_t, "ci", {"identity"}, set())
    ci_identity = _str(ci_t, "identity", "ci")

    key_vault = None
    if "key_vault" in raw:
        vault_t = _table(raw, "key_vault")
        _check_keys(vault_t, "key_vault", {"name", "resource_group"}, set())
        key_vault = KeyVault(
            name=_str(vault_t, "name", "key_vault"),
            resource_group=_str(vault_t, "resource_group", "key_vault"),
        )

    identities = tuple(
        _load_identity(table, f"identity {number}", has_vault=key_vault is not None)
        for number, table in enumerate(_tables(raw, "identity"), start=1)
    )
    jobs = tuple(
        _load_job(table, f"job {number}")
        for number, table in enumerate(_tables(raw, "job"), start=1)
    )
    _check_references(ci_identity, identities, jobs)

    alert_t = _table(raw, "alert")
    _check_keys(alert_t, "alert", {"email", "action_group"}, set())
    alert = Alert(
        email=_str(alert_t, "email", "alert"),
        action_group=_str(alert_t, "action_group", "alert"),
    )

    github_t = _table(raw, "github")
    _check_keys(github_t, "github", {"repository"}, set())
    repository = _str(github_t, "repository", "github")
    # The name becomes the image name, written into role assignment conditions.
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repository):
        raise ConfigError("[github].repository must be owner/name.")

    return Config(
        shared=shared,
        ci_identity=ci_identity,
        identities=identities,
        jobs=jobs,
        key_vault=key_vault,
        alert=alert,
        repository=repository,
    )


# region clients

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]
"""Sends one HTTP request: ``(method, url, headers, body) -> (status, body)``."""


def _urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    """Send one HTTP request with urllib.

    Args:
        method: HTTP method.
        url: Full URL.
        headers: Request headers.
        body: Request body, or None.

    Returns:
        The status code and response body; error statuses are returned, not raised.
    """
    request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310  (https only)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310  (https only)
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _az() -> str:
    """Return the Azure CLI's path.

    Returns:
        The path ``shutil.which`` finds for ``az``.
    """
    found = shutil.which("az")
    if found is None:
        die("The Azure CLI (az) is not on PATH. Install it and run 'az login'.")
    return found


def azure_token(subscription: str) -> tuple[str, str]:
    """Get a management token for *subscription* from the Azure CLI's sign-in.

    Args:
        subscription: Subscription id.

    Returns:
        ``(token, tenant id)``.
    """
    output = capture(
        [
            _az(),
            "account",
            "get-access-token",
            "--resource",
            f"{ARM}/",
            "--subscription",
            subscription,
            "--output",
            "json",
        ]
    )
    data = json.loads(output)
    return str(data["accessToken"]), str(data["tenant"])


class Arm:
    """A minimal Azure Resource Manager client over a pluggable transport."""

    def __init__(self, token: str, transport: Transport = _urllib_transport) -> None:
        """Create the client.

        Args:
            token: Bearer token for ``management.azure.com``.
            transport: Sends requests; tests replace it.
        """
        self._token = token
        self._transport = transport

    def request(
        self, method: str, path: str, api_version: str, body: Any = None
    ) -> tuple[int, Any]:
        """Send one ARM request.

        Args:
            method: HTTP method.
            path: Resource path, starting ``/subscriptions/`` or ``/providers/``. May
                carry its own query string; ``api-version`` is added.
            api_version: The resource type's API version.
            body: JSON-serializable body, or None.

        Returns:
            The status code and the decoded JSON body (None when empty).
        """
        separator = "&" if "?" in path else "?"
        return self._send(method, f"{ARM}{path}{separator}api-version={api_version}", body)

    def _send(self, method: str, url: str, body: Any = None) -> tuple[int, Any]:
        """Send one request, with the bearer token, to a full ARM URL.

        Args:
            method: HTTP method.
            url: Full URL, with its ``api-version``.
            body: JSON-serializable body, or None.

        Returns:
            The status code and the decoded JSON body (None when empty).
        """
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        data = None if body is None else json.dumps(body).encode("utf-8")
        status, raw = self._transport(method, url, headers, data)
        decoded = json.loads(raw) if raw.strip() else None
        return status, decoded

    def _ok(self, method: str, path: str, status: int, decoded: Any) -> Any:
        """Return *decoded*, or raise for an error status.

        Args:
            method: HTTP method, for the message.
            path: Resource path, for the message.
            status: HTTP status code.
            decoded: Decoded response body.

        Returns:
            The decoded body.

        Raises:
            AzureError: If the status is an error.
        """
        if status >= 400:
            error = (decoded or {}).get("error", {}) if isinstance(decoded, dict) else {}
            message = error.get("message") or decoded
            raise AzureError(f"{method} {path} failed ({status}): {message}")
        return decoded

    def get(self, path: str, api_version: str) -> Any:
        """Read a resource.

        Args:
            path: Resource path.
            api_version: API version.

        Returns:
            The resource, or None when it doesn't exist.
        """
        status, decoded = self.request("GET", path, api_version)
        if status == 404:
            return None
        return self._ok("GET", path, status, decoded)

    def list_all(self, path: str, api_version: str) -> list[dict[str, Any]]:
        """Read every resource in a collection, following its pages.

        Args:
            path: The collection's path, such as a resource group's jobs.
            api_version: API version.

        Returns:
            The resources; none when the collection's resource group doesn't exist.

        Raises:
            AzureError: If a page fails, or links to a next page outside Azure Resource
                Manager, which must never be sent the token.
        """
        found: list[dict[str, Any]] = []
        status, decoded = self.request("GET", path, api_version)
        while status != 404:
            page = self._ok("GET", path, status, decoded) or {}
            found.extend(page.get("value") or [])
            next_link = page.get("nextLink")
            if not next_link:
                break
            if not str(next_link).startswith(f"{ARM}/"):
                raise AzureError(f"GET {path} linked to a next page outside {ARM}: {next_link}")
            status, decoded = self._send("GET", str(next_link))
        return found

    def send(self, method: str, path: str, api_version: str, body: Any = None) -> Any:
        """Send a write request and return its result.

        Args:
            method: ``PUT``, ``PATCH``, ``POST`` or ``DELETE``.
            path: Resource path.
            api_version: API version.
            body: JSON body, or None.

        Returns:
            The decoded response body.
        """
        status, decoded = self.request(method, path, api_version, body)
        return self._ok(method, path, status, decoded)

    def wait(self, path: str, api_version: str, *, sleep: Callable[[float], None]) -> Any:
        """Wait for a resource's provisioning to finish.

        Args:
            path: Resource path.
            api_version: API version.
            sleep: Waits between polls; tests replace it.

        Returns:
            The provisioned resource.

        Raises:
            AzureError: If provisioning fails or takes longer than
                :data:`WAIT_TIMEOUT_SECONDS`.
        """
        deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
        while True:
            resource = self.get(path, api_version)
            state = str(((resource or {}).get("properties") or {}).get("provisioningState", ""))
            if state.lower() in _DONE_STATES:
                return resource
            if state.lower() in _FAILED_STATES:
                raise AzureError(f"{path} provisioning ended in state {state}.")
            if time.monotonic() > deadline:
                raise AzureError(f"{path} was still {state or 'missing'} after the timeout.")
            sleep(WAIT_INTERVAL_SECONDS)


class GitHub:
    """The GitHub CLI calls this script makes."""

    def __init__(self, repository: str) -> None:
        """Create the client.

        Args:
            repository: ``owner/name``.
        """
        self.repository = repository

    @staticmethod
    def _gh() -> str:
        """Return the GitHub CLI's path.

        Returns:
            The path ``shutil.which`` finds for ``gh``.
        """
        found = shutil.which("gh")
        if found is None:
            die("The GitHub CLI (gh) is not on PATH. Install it and run 'gh auth login'.")
        return found

    def subject_prefix(self) -> str:
        """Return the repo's OIDC subject prefix, e.g. ``repo:owner@id/name@id``.

        Returns:
            The prefix GitHub puts before ``:ref:...`` in its tokens' subject.
        """
        return capture(
            [
                self._gh(),
                "api",
                f"repos/{self.repository}/actions/oidc/customization/sub",
                "--jq",
                ".sub_claim_prefix",
            ]
        )

    def variables(self) -> dict[str, str]:
        """Return the repo's Actions variables.

        Returns:
            Variable name to value.
        """
        output = capture(
            [self._gh(), "variable", "list", "--repo", self.repository, "--json", "name,value"]
        )
        return {item["name"]: item["value"] for item in json.loads(output or "[]")}

    def set_variable(self, name: str, value: str) -> None:
        """Create or replace one repo variable.

        Args:
            name: Variable name.
            value: Variable value.
        """
        capture([self._gh(), "variable", "set", name, "--repo", self.repository, "--body", value])


# region plan


@dataclass
class Change:
    """One difference between the config and Azure or GitHub, with how to fix it."""

    kind: str  # "create", "update" or "remove"
    what: str
    apply: Callable[[], None]
    detail: str = ""


@dataclass
class Plan:
    """Everything a run would change, plus differences it reports but won't change."""

    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Grant:
    """One role assignment an identity should hold."""

    role: str
    scope: str
    label: str
    # Limits the role to part of what is under the scope; None for all of it.
    condition: str | None = None


def _same(left: str | None, right: str | None) -> bool:
    """Compare two resource ids or names, ignoring case as Azure does.

    Args:
        left: One id.
        right: The other.

    Returns:
        Whether they name the same thing.
    """
    return (left or "").lower() == (right or "").lower()


def _same_condition(left: str | None, right: str | None) -> bool:
    """Compare two role assignment conditions, ignoring how they are spaced.

    Args:
        left: One condition, or None for no condition.
        right: The other.

    Returns:
        Whether they say the same thing.
    """
    return "".join((left or "").split()) == "".join((right or "").split())


def _under(scope: str, parent: str) -> bool:
    """Return whether a role assignment's scope is a resource or something below it.

    Args:
        scope: The assignment's scope.
        parent: The resource's id.

    Returns:
        Whether *scope* is *parent* or a resource id below it.
    """
    scope, parent = scope.lower(), parent.lower()
    return scope == parent or scope.startswith(f"{parent}/")


def _tag(resource: dict[str, Any] | None, name: str) -> str | None:
    """Return the value of tag *name* on a resource, ignoring case in the tag's name.

    Args:
        resource: A resource as Azure returns it, or None.
        name: Tag name.

    Returns:
        The tag's value, or None when the resource doesn't carry the tag.
    """
    for key, value in ((resource or {}).get("tags") or {}).items():
        if key.lower() == name.lower():
            return str(value)
    return None


def _image(job: dict[str, Any] | None) -> str | None:
    """Return the image a job's container runs.

    Args:
        job: A job resource, or None.

    Returns:
        The image, or None when there is no job or it names no image.
    """
    template = ((job or {}).get("properties") or {}).get("template") or {}
    image = (template.get("containers") or [{}])[0].get("image")
    return None if image is None else str(image)


class Planner:
    """Compares a config with Azure and GitHub, and builds the :class:`Plan`."""

    def __init__(
        self,
        config: Config,
        arm: Arm,
        github: GitHub,
        tenant: str,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the planner.

        Args:
            config: The parsed config.
            arm: Azure Resource Manager client.
            github: GitHub client.
            tenant: The subscription's Entra tenant id.
            sleep: Waits between provisioning polls; tests replace it.
        """
        self.config = config
        self.arm = arm
        self.github = github
        self.tenant = tenant
        self.sleep = sleep
        self.plan = Plan()
        self._roles: dict[str, dict[str, Any]] = {}
        # The image workflow names the image after the GitHub repo.
        self.image_name = config.repository.split("/", 1)[1]
        shared = config.shared
        self.sub_id = f"/subscriptions/{shared.subscription}"
        self.rg_id = f"{self.sub_id}/resourceGroups/{shared.resource_group}"
        self.registry_id = (
            f"{self.rg_id}/providers/Microsoft.ContainerRegistry/registries/{shared.registry}"
        )
        self.workspace_id = (
            f"{self.rg_id}/providers/Microsoft.OperationalInsights/workspaces/"
            f"{shared.log_workspace}"
        )
        self.vnet_id = f"{self.rg_id}/providers/Microsoft.Network/virtualNetworks/{shared.vnet}"
        self.subnet_id = f"{self.vnet_id}/subnets/{shared.subnet}"
        self.environment_id = (
            f"{self.rg_id}/providers/Microsoft.App/managedEnvironments/{shared.environment}"
        )
        self.ci_identity_id = self._identity_path(config.ci_identity)
        self.action_group_id = (
            f"{self.rg_id}/providers/Microsoft.Insights/actionGroups/{config.alert.action_group}"
        )
        vault = config.key_vault
        self.vault_id = "" if vault is None else self._vault_path(vault)

    def _vault_path(self, vault: KeyVault) -> str:
        """Return the resource id of the job's Key Vault.

        Args:
            vault: The vault settings; the vault is in the shared subscription.

        Returns:
            Its resource id.
        """
        return (
            f"{self.sub_id}/resourceGroups/{vault.resource_group}"
            f"/providers/Microsoft.KeyVault/vaults/{vault.name}"
        )

    def _identity_path(self, name: str) -> str:
        """Return the resource id of user-assigned identity *name*.

        Args:
            name: Identity name.

        Returns:
            Its resource id.
        """
        return f"{self.rg_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}"

    def _job_path(self, name: str) -> str:
        """Return the resource id of job *name*.

        Args:
            name: Job name.

        Returns:
            Its resource id.
        """
        return f"{self.rg_id}/providers/Microsoft.App/jobs/{name}"

    def _alert_path(self, rule: str) -> str:
        """Return the resource id of alert rule *rule*.

        Args:
            rule: Alert rule name.

        Returns:
            Its resource id.
        """
        return f"{self.rg_id}/providers/Microsoft.Insights/scheduledQueryRules/{rule}"

    def _add(self, kind: str, what: str, apply: Callable[[], None], detail: str = "") -> None:
        """Record one change.

        Args:
            kind: ``create``, ``update`` or ``remove``.
            what: What changes, for the listing.
            apply: Makes the change.
            detail: Extra line for the listing, such as old and new values.
        """
        self.plan.changes.append(Change(kind, what, apply, detail))

    # -- this repo's tag, which marks the resources its config owns

    def _tags(self, current: dict[str, Any] | None) -> dict[str, str]:
        """Return a resource's tags with this repo's tag set, keeping its other tags.

        Args:
            current: The resource as Azure returns it, or None when it doesn't exist.

        Returns:
            The tags to write.
        """
        kept = {
            str(key): str(value)
            for key, value in ((current or {}).get("tags") or {}).items()
            if key.lower() != REPOSITORY_TAG
        }
        return {**kept, REPOSITORY_TAG: self.config.repository}

    def _is_tagged(self, resource: dict[str, Any], what: str) -> bool:
        """Return whether an existing resource carries this repo's tag.

        Args:
            resource: The resource as Azure returns it.
            what: The resource's kind and name, for the message.

        Returns:
            True when it carries this repo's tag, False when it carries no repo's.

        Raises:
            AzureError: If it carries another repo's tag: that repo's config owns it.
        """
        owner = _tag(resource, REPOSITORY_TAG)
        if owner is None:
            return False
        if not _same(owner, self.config.repository):
            raise AzureError(
                f"{what} belongs to the repo {owner} (its {REPOSITORY_TAG} tag), not "
                f"{self.config.repository}. Pick another name for this repo's."
            )
        return True

    def _tagged(self, collection: str, api_version: str) -> list[str]:
        """Return the names of the resources of one type that carry this repo's tag.

        Args:
            collection: Resource type, such as ``Microsoft.App/jobs``.
            api_version: The type's API version.

        Returns:
            The names of this repo's resources of that type in the resource group.
        """
        return [
            str(resource["id"]).rsplit("/", 1)[-1]
            for resource in self.arm.list_all(f"{self.rg_id}/providers/{collection}", api_version)
            if _same(_tag(resource, REPOSITORY_TAG), self.config.repository)
        ]

    # -- lookups made at apply time, once the resources they read exist

    def _principal(self, identity_id: str) -> str:
        """Return an identity's principal (object) id.

        Args:
            identity_id: The identity's resource id.

        Returns:
            Its principal id.
        """
        identity = self.arm.get(identity_id, API_IDENTITIES)
        if identity is None:
            raise AzureError(f"{identity_id} does not exist.")
        return str(identity["properties"]["principalId"])

    def _client_id(self, identity_id: str) -> str:
        """Return an identity's client id.

        Args:
            identity_id: The identity's resource id.

        Returns:
            Its client id.
        """
        identity = self.arm.get(identity_id, API_IDENTITIES)
        if identity is None:
            raise AzureError(f"{identity_id} does not exist.")
        return str(identity["properties"]["clientId"])

    def _role(self, role: str) -> dict[str, Any]:
        """Return built-in role *role* as Azure defines it.

        Args:
            role: Role name, such as ``Key Vault Secrets User``.

        Returns:
            The role definition.
        """
        if role not in self._roles:
            query = urllib.parse.quote(f"roleName eq '{role}'")
            found = self.arm.get(
                f"{self.sub_id}/providers/Microsoft.Authorization/roleDefinitions?$filter={query}",
                API_ROLES,
            )
            values = (found or {}).get("value", [])
            if len(values) != 1:
                raise AzureError(f"Found {len(values)} role definitions named {role!r}.")
            self._roles[role] = values[0]
        return self._roles[role]

    def _role_definition(self, role: str) -> str:
        """Return the resource id of built-in role *role*.

        Args:
            role: Role name.

        Returns:
            The role definition's id.
        """
        return str(self._role(role)["id"])

    def _image_grant(self, role: str) -> Grant:
        """Return the grant of a registry role, limited to this repo's image name.

        The condition reads "the action is none of the role's, or it is on this image
        name". It lists every action the role grants, read from the role's definition:
        an action it left out would stay allowed on every image in the registry.

        Args:
            role: ``ROLE_IMAGE_READER`` or ``ROLE_IMAGE_WRITER``.

        Returns:
            The grant, on the registry.

        Raises:
            AzureError: If the role grants anything a condition can't limit.
        """
        permissions = (self._role(role).get("properties") or {}).get("permissions") or []
        actions = sorted({str(a) for p in permissions for a in p.get("dataActions") or []})
        unlimited = sorted({str(a) for p in permissions for a in p.get("actions") or []})
        if not actions or unlimited:
            raise AzureError(
                f"The role {role!r} can't be limited to one image name: it grants "
                f"{', '.join(unlimited) or 'no data actions'}."
            )
        others = " AND ".join(f"!(ActionMatches{{'{action}'}})" for action in actions)
        return Grant(
            role,
            self.registry_id,
            f"image {self.image_name} in registry {self.config.shared.registry}",
            f"(({others}) OR ({IMAGE_NAME_ATTRIBUTE} StringEqualsIgnoreCase '{self.image_name}'))",
        )

    # -- the seven parts

    def plan_all(self) -> Plan:
        """Compare everything, in the order it must be created, then removed.

        Returns:
            The plan.
        """
        self.plan_shared()
        self.plan_identities()
        self.plan_jobs()
        self.plan_ci_identity()
        self.plan_repo_variables()
        self.plan_alerts()
        self.plan_removals()
        return self.plan

    def _create_and_wait(self, path: str, api_version: str, body: dict[str, Any]) -> None:
        """PUT a new resource and wait for it to finish provisioning.

        Args:
            path: Resource id.
            api_version: API version.
            body: The resource.
        """
        self.arm.send("PUT", path, api_version, body)
        self.arm.wait(path, api_version, sleep=self.sleep)

    def plan_shared(self) -> None:
        """Plan the shared resources: create what's missing, report what differs."""
        shared = self.config.shared
        location = shared.location

        for namespace in PROVIDERS:
            provider_path = f"{self.sub_id}/providers/{namespace}"
            provider = self.arm.get(provider_path, API_PROVIDERS) or {}
            if str(provider.get("registrationState", "")).lower() != "registered":
                self._add(
                    "create",
                    f"register resource provider {namespace}",
                    functools.partial(self._register, provider_path),
                )

        if self.arm.get(self.rg_id, API_RESOURCE_GROUPS) is None:
            self._add(
                "create",
                f"resource group {shared.resource_group}",
                lambda: self.arm.send(
                    "PUT", self.rg_id, API_RESOURCE_GROUPS, {"location": location}
                ),
            )

        registry = self.arm.get(self.registry_id, API_REGISTRIES)
        if registry is None:
            self._add(
                "create",
                f"registry {shared.registry} (Basic, admin user off, rights per image name)",
                lambda: self._create_and_wait(
                    self.registry_id,
                    API_REGISTRIES,
                    {
                        "location": location,
                        "sku": {"name": "Basic"},
                        "properties": {
                            "adminUserEnabled": False,
                            "roleAssignmentMode": REGISTRY_MODE,
                        },
                    },
                ),
            )
        else:
            self._check_registry_mode(registry)

        if self.arm.get(self.workspace_id, API_WORKSPACES) is None:
            self._add(
                "create",
                f"Log Analytics workspace {shared.log_workspace}",
                lambda: self._create_and_wait(
                    self.workspace_id,
                    API_WORKSPACES,
                    {
                        "location": location,
                        "properties": {"sku": {"name": "PerGB2018"}, "retentionInDays": 30},
                    },
                ),
            )

        subnet_body = {
            "name": shared.subnet,
            "properties": {
                "addressPrefix": shared.subnet_address_prefix,
                "delegations": [
                    {
                        "name": "Microsoft.App.environments",
                        "properties": {"serviceName": "Microsoft.App/environments"},
                    }
                ],
                "serviceEndpoints": [{"service": "Microsoft.KeyVault"}],
            },
        }
        vnet = self.arm.get(self.vnet_id, API_NETWORK)
        if vnet is None:
            self._add(
                "create",
                f"virtual network {shared.vnet} ({shared.vnet_address_prefix}) with subnet "
                f"{shared.subnet} ({shared.subnet_address_prefix})",
                lambda: self._create_and_wait(
                    self.vnet_id,
                    API_NETWORK,
                    {
                        "location": location,
                        "properties": {
                            "addressSpace": {"addressPrefixes": [shared.vnet_address_prefix]},
                            "subnets": [subnet_body],
                        },
                    },
                ),
            )
        else:
            subnet = self.arm.get(self.subnet_id, API_NETWORK)
            if subnet is None:
                self._add(
                    "create",
                    f"subnet {shared.subnet} ({shared.subnet_address_prefix})",
                    lambda: self._create_and_wait(self.subnet_id, API_NETWORK, subnet_body),
                )
            else:
                self._note_subnet(subnet)

        environment = self.arm.get(self.environment_id, API_CONTAINER_APPS)
        if environment is None:
            self._add(
                "create",
                f"Container Apps environment {shared.environment} on {shared.subnet}",
                self._create_environment,
                "can take 20 minutes",
            )
        else:
            actual_subnet = (
                (environment.get("properties") or {}).get("vnetConfiguration") or {}
            ).get("infrastructureSubnetId")
            if not _same(actual_subnet, self.subnet_id):
                self.plan.notes.append(
                    f"environment {shared.environment} is on subnet {actual_subnet}, not "
                    f"{shared.subnet}; an environment can't move, so it is left as it is."
                )

    def _check_registry_mode(self, registry: dict[str, Any]) -> None:
        """Stop the run at a registry that can't limit a role to one image name.

        Switching the mode changes the roles every repo that uses the registry needs,
        so it is left to the person running this.

        Args:
            registry: The registry as Azure returns it.

        Raises:
            AzureError: If the registry is in another role assignment mode.
        """
        mode = (registry.get("properties") or {}).get("roleAssignmentMode")
        if _same(mode, REGISTRY_MODE):
            return
        shared = self.config.shared
        raise AzureError(
            f"Registry {shared.registry} can't limit a role to one image name: its role "
            f"assignment mode is {mode or 'not set'}, not {REGISTRY_MODE}. Switch it, then "
            "run this script in every repo that uses the registry:\n"
            f"    az acr update --name {shared.registry} --resource-group "
            f"{shared.resource_group} --role-assignment-mode rbac-abac"
        )

    def _note_subnet(self, subnet: dict[str, Any]) -> None:
        """Report what an existing subnet lacks that the environment and vault need.

        Args:
            subnet: The subnet resource.
        """
        properties = subnet.get("properties") or {}
        delegations = {
            str((d.get("properties") or {}).get("serviceName", "")).lower()
            for d in properties.get("delegations") or []
        }
        endpoints = {
            str(e.get("service", "")).lower() for e in properties.get("serviceEndpoints") or []
        }
        name = self.config.shared.subnet
        if "microsoft.app/environments" not in delegations:
            self.plan.notes.append(f"subnet {name} is not delegated to Microsoft.App/environments.")
        if "microsoft.keyvault" not in endpoints:
            self.plan.notes.append(
                f"subnet {name} has no Microsoft.KeyVault service endpoint, so a firewalled "
                "vault can't allow it."
            )

    def _register(self, provider_path: str) -> None:
        """Register a resource provider and wait until it is registered.

        Args:
            provider_path: ``/subscriptions/<id>/providers/<namespace>``.
        """
        self.arm.send("POST", f"{provider_path}/register", API_PROVIDERS)
        deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
        while True:
            provider = self.arm.get(provider_path, API_PROVIDERS) or {}
            if str(provider.get("registrationState", "")).lower() == "registered":
                return
            if time.monotonic() > deadline:
                raise AzureError(f"{provider_path} did not finish registering.")
            self.sleep(WAIT_INTERVAL_SECONDS)

    def _create_environment(self) -> None:
        """Create the Container Apps environment on the subnet, logging to the workspace."""
        workspace = self.arm.get(self.workspace_id, API_WORKSPACES)
        if workspace is None:
            raise AzureError(f"{self.workspace_id} does not exist.")
        keys = self.arm.send("POST", f"{self.workspace_id}/sharedKeys", API_WORKSPACE_KEYS)
        self._create_and_wait(
            self.environment_id,
            API_CONTAINER_APPS,
            {
                "location": self.config.shared.location,
                "properties": {
                    "workloadProfiles": [
                        {"name": WORKLOAD_PROFILE, "workloadProfileType": WORKLOAD_PROFILE}
                    ],
                    "vnetConfiguration": {
                        "infrastructureSubnetId": self.subnet_id,
                        "internal": False,
                    },
                    "appLogsConfiguration": {
                        "destination": "log-analytics",
                        "logAnalyticsConfiguration": {
                            "customerId": workspace["properties"]["customerId"],
                            "sharedKey": keys["primarySharedKey"],
                        },
                    },
                },
            },
        )

    def _plan_identity(self, identity_id: str, name: str) -> bool:
        """Plan creating a user-assigned identity, or tagging one that has no repo's tag.

        Args:
            identity_id: Its resource id.
            name: Its name, for the listing.

        Returns:
            Whether it already exists.
        """
        current = self.arm.get(identity_id, API_IDENTITIES)
        body = {"location": self.config.shared.location, "tags": self._tags(current)}
        put = functools.partial(self.arm.send, "PUT", identity_id, API_IDENTITIES, body)
        if current is None:
            self._add("create", f"managed identity {name}", put)
            return False
        if not self._is_tagged(current, f"Managed identity {name}"):
            self._add(
                "update", f"managed identity {name} (tag)", put, f"adds the {REPOSITORY_TAG} tag"
            )
        return True

    def _assignments(self, principal: str) -> list[dict[str, Any]]:
        """Return every role assignment held by *principal* in the subscription.

        Args:
            principal: Principal (object) id.

        Returns:
            The assignments' properties, each with its ``id`` added.
        """
        query = urllib.parse.quote(f"principalId eq '{principal}'")
        found = self.arm.get(
            f"{self.sub_id}/providers/Microsoft.Authorization/roleAssignments?$filter={query}",
            API_ROLES,
        )
        return [{**item["properties"], "id": item["id"]} for item in (found or {}).get("value", [])]

    def _plan_roles(
        self,
        identity_id: str,
        name: str,
        exists: bool,
        desired: list[Grant],
        managed: list[tuple[str, str, str]],
    ) -> None:
        """Plan the role assignments one identity should hold.

        Args:
            identity_id: The identity's resource id.
            name: The identity's name, for the listing.
            exists: Whether the identity exists yet.
            desired: Each assignment it should hold.
            managed: ``(role, resource id, reason)`` for each role this script owns
                completely on a resource: an assignment of that role on the resource or
                below it that is not in *desired* is removed, and the listing gives the
                reason.
        """
        held: list[dict[str, Any]] = []
        if exists:
            held = self._assignments(self._principal(identity_id))

        for grant in desired:
            role_id = self._role_definition(grant.role)
            current = next(
                (
                    a
                    for a in held
                    if _same(a.get("roleDefinitionId"), role_id)
                    and _same(a.get("scope"), grant.scope)
                ),
                None,
            )
            what = f"role {grant.role} on {grant.label} for {name}"
            if current is None:
                self._add("create", what, functools.partial(self._assign, identity_id, grant))
            elif grant.condition is not None and not _same_condition(
                current.get("condition"), grant.condition
            ):
                # An identity can hold a role on a scope once, so the assignment it
                # holds is rewritten in place.
                self._add(
                    "update",
                    f"{what} (condition)",
                    functools.partial(self._assign, identity_id, grant, str(current["id"])),
                    "it holds the role there with another condition, or with none",
                )

        for role, resource_id, reason in managed:
            role_id = self._role_definition(role)
            wanted = {grant.scope.lower() for grant in desired if grant.role == role}
            for assignment in held:
                scope = str(assignment.get("scope", ""))
                if (
                    _same(assignment.get("roleDefinitionId"), role_id)
                    and _under(scope, resource_id)
                    and scope.lower() not in wanted
                ):
                    self._add(
                        "remove",
                        f"role {role} on {scope.rsplit('/', 1)[-1]} for {name}",
                        functools.partial(
                            self.arm.send, "DELETE", str(assignment["id"]), API_ROLES
                        ),
                        reason,
                    )

    def _assign(self, identity_id: str, grant: Grant, assignment_id: str | None = None) -> None:
        """Grant a role to an identity, or rewrite the assignment it already holds.

        Args:
            identity_id: The identity's resource id.
            grant: The role, the scope it applies to, and its condition.
            assignment_id: The resource id of the assignment to rewrite; None makes one.
        """
        properties: dict[str, Any] = {
            "roleDefinitionId": self._role_definition(grant.role),
            "principalId": self._principal(identity_id),
            "principalType": "ServicePrincipal",
        }
        if grant.condition is not None:
            properties["condition"] = grant.condition
            properties["conditionVersion"] = CONDITION_VERSION
        path = assignment_id or (
            f"{grant.scope}/providers/Microsoft.Authorization/roleAssignments/{uuid.uuid4()}"
        )
        self.arm.send("PUT", path, API_ROLES, {"properties": properties})

    def plan_identities(self) -> None:
        """Plan each identity the jobs run as, its roles, and the vault's network rule."""
        vault = self.config.key_vault
        for identity in self.config.identities:
            identity_id = self._identity_path(identity.name)
            exists = self._plan_identity(identity_id, identity.name)
            desired = [self._image_grant(ROLE_IMAGE_READER)]
            managed = [(ROLE_ACR_PULL, self.registry_id, SUPERSEDED_ROLE)]
            if vault is not None:
                desired += [
                    Grant(
                        ROLE_SECRETS_USER, f"{self.vault_id}/secrets/{secret}", f"secret {secret}"
                    )
                    for secret in identity.secrets
                ]
                managed.append(
                    (
                        ROLE_SECRETS_USER,
                        f"{self.vault_id}/secrets",
                        "no longer listed in the config",
                    )
                )
            self._plan_roles(identity_id, identity.name, exists, desired, managed)
        if vault is not None:
            self._plan_vault_rule(vault)

    def _plan_vault_rule(self, vault: KeyVault) -> None:
        """Plan the vault firewall rule allowing the environment's subnet.

        Args:
            vault: The vault settings.
        """
        resource = self.arm.get(self.vault_id, API_VAULTS)
        if resource is None:
            raise AzureError(f"Key Vault {vault.name} does not exist in {vault.resource_group}.")
        acls = (resource.get("properties") or {}).get("networkAcls") or {}
        if str(acls.get("defaultAction", "Allow")).lower() != "deny":
            return
        rules = acls.get("virtualNetworkRules") or []
        if any(_same(rule.get("id"), self.subnet_id) for rule in rules):
            return

        def add_rule() -> None:
            current = self.arm.get(self.vault_id, API_VAULTS) or {}
            current_acls = (current.get("properties") or {}).get("networkAcls") or {}
            new_rules = [*(current_acls.get("virtualNetworkRules") or []), {"id": self.subnet_id}]
            self.arm.send(
                "PATCH",
                self.vault_id,
                API_VAULTS,
                {"properties": {"networkAcls": {**current_acls, "virtualNetworkRules": new_rules}}},
            )

        self._add(
            "update",
            f"Key Vault {vault.name}: allow subnet {self.config.shared.subnet}",
            add_rule,
            "its firewall denies by default; existing rules are kept",
        )

    def _job_body(self, job: Job, image: str, current: dict[str, Any] | None) -> dict[str, Any]:
        """Return a job as the config describes it, running *image*.

        Args:
            job: The job's settings.
            image: The container image.
            current: The job as Azure returns it, whose other tags are kept, or None when
                it doesn't exist.

        Returns:
            The job resource body.
        """
        identity_id = self._identity_path(job.identity)
        container: dict[str, Any] = {
            "name": job.name,
            "image": image,
            "args": list(job.args),
            "resources": {"cpu": job.cpu, "memory": job.memory},
        }
        if job.env:
            container["env"] = [{"name": name, "value": value} for name, value in job.env]
        return {
            "location": self.config.shared.location,
            "tags": self._tags(current),
            "identity": {
                "type": "UserAssigned",
                "userAssignedIdentities": {identity_id: {}},
            },
            "properties": {
                "environmentId": self.environment_id,
                "workloadProfileName": WORKLOAD_PROFILE,
                "configuration": {
                    "triggerType": "Schedule",
                    "replicaTimeout": job.timeout,
                    "replicaRetryLimit": 0,
                    "scheduleTriggerConfig": {
                        "cronExpression": job.schedule,
                        "parallelism": 1,
                        "replicaCompletionCount": 1,
                    },
                    "registries": [
                        {
                            "server": f"{self.config.shared.registry}.azurecr.io",
                            "identity": identity_id,
                        }
                    ],
                },
                "template": {"containers": [container]},
            },
        }

    @staticmethod
    def _job_settings(job: dict[str, Any]) -> dict[str, Any]:
        """Return the settings of a job resource this script manages.

        Args:
            job: A job resource, as Azure returns it or as :meth:`_job_body` builds it.

        Returns:
            The comparable settings.
        """
        properties = job.get("properties") or {}
        configuration = properties.get("configuration") or {}
        schedule = configuration.get("scheduleTriggerConfig") or {}
        containers = (properties.get("template") or {}).get("containers") or [{}]
        container = containers[0]
        resources = container.get("resources") or {}
        registries = configuration.get("registries") or []
        identities = (job.get("identity") or {}).get("userAssignedIdentities") or {}
        return {
            "trigger": str(configuration.get("triggerType", "")).lower(),
            "schedule": schedule.get("cronExpression"),
            "timeout": configuration.get("replicaTimeout"),
            "retries": configuration.get("replicaRetryLimit"),
            "args": list(container.get("args") or []),
            "env": sorted(
                (str(item.get("name")), str(item.get("value")))
                for item in container.get("env") or []
            ),
            "cpu": float(resources.get("cpu") or 0),
            "memory": resources.get("memory"),
            "registries": sorted(
                (str(r.get("server", "")).lower(), str(r.get("identity", "")).lower())
                for r in registries
            ),
            "identities": sorted(key.lower() for key in identities),
            "tag": (_tag(job, REPOSITORY_TAG) or "").lower(),
        }

    def plan_jobs(self) -> None:
        """Plan every job: create the missing ones, and update what differs in the rest.

        The repo's jobs all run one image, so a new job starts on the image the others
        already run, and on the quickstart image only when no job has been released yet.
        """
        jobs = self.config.jobs
        current = {
            job.name: self.arm.get(self._job_path(job.name), API_CONTAINER_APPS) for job in jobs
        }
        images = (_image(current[job.name]) for job in jobs)
        released = next(
            (image for image in images if image and image != QUICKSTART_IMAGE), QUICKSTART_IMAGE
        )
        for job in jobs:
            self._plan_job(job, current[job.name], released)

    def _plan_job(self, job: Job, current: dict[str, Any] | None, released: str) -> None:
        """Plan one job: create it, or update what differs and keep its image.

        Args:
            job: The job's settings.
            current: The job as Azure returns it, or None when it doesn't exist.
            released: The image a new job starts on.

        Raises:
            AzureError: If a job of that name belongs to another repo.
        """
        job_id = self._job_path(job.name)
        if current is None:
            starts_on = (
                "the quickstart image until the first release"
                if released == QUICKSTART_IMAGE
                else f"image {released}"
            )
            self._add(
                "create",
                f"job {job.name} (schedule {job.schedule}, {starts_on})",
                functools.partial(
                    self._create_and_wait,
                    job_id,
                    API_CONTAINER_APPS,
                    self._job_body(job, released, None),
                ),
            )
            return
        self._is_tagged(current, f"Job {job.name}")
        image = _image(current) or QUICKSTART_IMAGE
        wanted = self._job_body(job, image, current)
        have, want = self._job_settings(current), self._job_settings(wanted)
        differences = [key for key in want if have.get(key) != want[key]]
        if differences:
            detail = "; ".join(f"{key}: {have.get(key)!r} -> {want[key]!r}" for key in differences)
            self._add(
                "update",
                f"job {job.name} ({', '.join(differences)})",
                functools.partial(self._create_and_wait, job_id, API_CONTAINER_APPS, wanted),
                f"{detail}; image kept: {image}",
            )

    def plan_ci_identity(self) -> None:
        """Plan the CI identity, its roles, and its federated credential."""
        name = self.config.ci_identity
        exists = self._plan_identity(self.ci_identity_id, name)
        self._plan_roles(
            self.ci_identity_id,
            name,
            exists,
            [
                self._image_grant(ROLE_IMAGE_WRITER),
                *(
                    Grant(ROLE_JOBS_CONTRIBUTOR, self._job_path(job.name), f"job {job.name}")
                    for job in self.config.jobs
                ),
                Grant(
                    ROLE_JOBS_CONTRIBUTOR,
                    self.environment_id,
                    f"environment {self.config.shared.environment}",
                ),
                *(
                    Grant(
                        ROLE_IDENTITY_OPERATOR,
                        self._identity_path(identity.name),
                        f"identity {identity.name}",
                    )
                    for identity in self.config.identities
                ),
            ],
            [(ROLE_ACR_PUSH, self.registry_id, SUPERSEDED_ROLE)],
        )
        subject = f"{self.github.subject_prefix()}:ref:refs/heads/main"
        credential_path = (
            f"{self.ci_identity_id}/federatedIdentityCredentials/{FEDERATED_CREDENTIAL}"
        )
        body = {
            "properties": {
                "issuer": GITHUB_ISSUER,
                "subject": subject,
                "audiences": [FEDERATED_AUDIENCE],
            }
        }
        current = self.arm.get(credential_path, API_IDENTITIES) if exists else None
        properties = (current or {}).get("properties") or {}
        if (
            current is None
            or properties.get("issuer") != GITHUB_ISSUER
            or properties.get("subject") != subject
            or list(properties.get("audiences") or []) != [FEDERATED_AUDIENCE]
        ):
            self._add(
                "create" if current is None else "update",
                f"federated credential {FEDERATED_CREDENTIAL} on {name}",
                lambda: self.arm.send("PUT", credential_path, API_IDENTITIES, body),
                f"trusts {subject}",
            )

    def desired_variables(self, ci_client_id: str) -> dict[str, str]:
        """Return the repo variables the image workflow reads.

        Args:
            ci_client_id: The CI identity's client id.

        Returns:
            Variable name to value.
        """
        shared = self.config.shared
        return {
            "ACR_NAME": shared.registry,
            "AZURE_CLIENT_ID": ci_client_id,
            "AZURE_SUBSCRIPTION_ID": shared.subscription,
            "AZURE_TENANT_ID": self.tenant,
            # Every job's name, space-separated: the workflow points each at the image.
            "CONTAINER_APP_JOBS": " ".join(job.name for job in self.config.jobs),
            "CONTAINER_APP_RESOURCE_GROUP": shared.resource_group,
        }

    def plan_repo_variables(self) -> None:
        """Plan the repo variables; the CI identity's client id is read when it exists."""
        identity = self.arm.get(self.ci_identity_id, API_IDENTITIES)
        client_id = "" if identity is None else str(identity["properties"]["clientId"])
        current = self.github.variables()
        for name, value in self.desired_variables(client_id).items():
            if identity is not None and current.get(name) == value:
                continue
            if name == "AZURE_CLIENT_ID" and identity is None:
                shown = "(the new CI identity's client id)"
            else:
                shown = value
            self._add(
                "update" if name in current else "create",
                f"repo variable {name} = {shown}",
                functools.partial(self._set_variable, name),
            )

    def _set_variable(self, name: str) -> None:
        """Set repo variable *name* to its value, read once the CI identity exists.

        Args:
            name: Variable name, a key of :meth:`desired_variables`.
        """
        client_id = self._client_id(self.ci_identity_id)
        self.github.set_variable(name, self.desired_variables(client_id)[name])

    def _alert_body(self, job: Job, current: dict[str, Any] | None) -> dict[str, Any]:
        """Return one job's failed-run alert rule as the config describes it.

        Args:
            job: The job the rule watches.
            current: The rule as Azure returns it, whose other tags are kept, or None
                when it doesn't exist.

        Returns:
            The scheduled query rule body.
        """
        return {
            "location": self.config.shared.location,
            "tags": self._tags(current),
            "properties": {
                "displayName": job.alert_rule,
                "description": f"A {job.name} execution failed or timed out.",
                "severity": 1,
                "enabled": True,
                "evaluationFrequency": "PT5M",
                "windowSize": "PT10M",
                "scopes": [self.workspace_id],
                "criteria": {
                    "allOf": [
                        {
                            "query": (
                                f"ContainerAppSystemLogs_CL | where JobName_s == '{job.name}' "
                                "| where Reason_s in ('BackoffLimitExceeded', 'DeadlineExceeded')"
                            ),
                            "timeAggregation": "Count",
                            "operator": "GreaterThan",
                            "threshold": 0,
                            "failingPeriods": {
                                "numberOfEvaluationPeriods": 1,
                                "minFailingPeriodsToAlert": 1,
                            },
                        }
                    ]
                },
                "muteActionsDuration": "PT10M",
                "autoMitigate": False,
                "actions": {"actionGroups": [self.action_group_id]},
            },
        }

    def plan_alerts(self) -> None:
        """Plan the action group's email receiver and each job's failed-run alert rule."""
        alert = self.config.alert
        group = self.arm.get(self.action_group_id, API_ACTION_GROUPS)
        emails = sorted(
            str(r.get("emailAddress", "")).lower()
            for r in ((group or {}).get("properties") or {}).get("emailReceivers") or []
        )
        if group is None or emails != [alert.email.lower()]:
            body = {
                "location": "Global",
                "properties": {
                    "groupShortName": alert.action_group[:12],
                    "enabled": True,
                    "emailReceivers": [
                        {
                            "name": "email",
                            "emailAddress": alert.email,
                            "useCommonAlertSchema": False,
                        }
                    ],
                },
            }
            self._add(
                "create" if group is None else "update",
                f"action group {alert.action_group}: email {alert.email}",
                lambda: self.arm.send("PUT", self.action_group_id, API_ACTION_GROUPS, body),
                "" if group is None else f"was {', '.join(emails) or 'no email'}",
            )

        for job in self.config.jobs:
            alert_id = self._alert_path(job.alert_rule)
            rule = self.arm.get(alert_id, API_SCHEDULED_QUERIES)
            if rule is not None:
                self._is_tagged(rule, f"Alert {job.alert_rule}")
            wanted = self._alert_body(job, rule)
            if rule is None or not self._same_alert(rule, wanted):
                self._add(
                    "create" if rule is None else "update",
                    f"alert {job.alert_rule} on failed {job.name} runs",
                    functools.partial(
                        self.arm.send, "PUT", alert_id, API_SCHEDULED_QUERIES, wanted
                    ),
                )

    @staticmethod
    def _same_alert(have: dict[str, Any], want: dict[str, Any]) -> bool:
        """Compare the parts of an alert rule this script manages.

        Args:
            have: The rule as Azure returns it.
            want: The rule as :meth:`_alert_body` builds it.

        Returns:
            Whether they match.
        """

        def parts(rule: dict[str, Any]) -> tuple[Any, ...]:
            properties = rule.get("properties") or {}
            criteria = ((properties.get("criteria") or {}).get("allOf") or [{}])[0]
            groups = (properties.get("actions") or {}).get("actionGroups") or []
            return (
                properties.get("enabled"),
                criteria.get("query"),
                sorted(str(s).lower() for s in properties.get("scopes") or []),
                sorted(str(g).lower() for g in groups),
                (_tag(rule, REPOSITORY_TAG) or "").lower(),
            )

        return parts(have) == parts(want)

    def plan_removals(self) -> None:
        """Plan removing what carries this repo's tag but the config no longer lists.

        Alert rules go first, then jobs, then identities, so nothing is removed while a
        resource that uses it remains.
        """
        config = self.config
        detail = "carries this repo's tag, but is not in the config"

        rules = {job.alert_rule.lower() for job in config.jobs}
        for name in self._tagged("Microsoft.Insights/scheduledQueryRules", API_SCHEDULED_QUERIES):
            if name.lower() not in rules:
                self._add(
                    "remove",
                    f"alert {name}",
                    functools.partial(
                        self.arm.send, "DELETE", self._alert_path(name), API_SCHEDULED_QUERIES
                    ),
                    detail,
                )

        jobs = {job.name.lower() for job in config.jobs}
        for name in self._tagged("Microsoft.App/jobs", API_CONTAINER_APPS):
            if name.lower() not in jobs:
                self._add(
                    "remove",
                    f"job {name}",
                    functools.partial(
                        self.arm.send, "DELETE", self._job_path(name), API_CONTAINER_APPS
                    ),
                    detail,
                )

        identities = {identity.name.lower() for identity in config.identities}
        identities.add(config.ci_identity.lower())
        for name in self._tagged(
            "Microsoft.ManagedIdentity/userAssignedIdentities", API_IDENTITIES
        ):
            if name.lower() not in identities:
                self._add(
                    "remove",
                    f"managed identity {name}, and every role it holds",
                    functools.partial(self._remove_identity, self._identity_path(name)),
                    detail,
                )

    def _remove_identity(self, identity_id: str) -> None:
        """Delete an identity, and first every role assignment it holds.

        Deleting only the identity would leave its role assignments behind, naming a
        principal that no longer exists.

        Args:
            identity_id: The identity's resource id.
        """
        for assignment in self._assignments(self._principal(identity_id)):
            self.arm.send("DELETE", str(assignment["id"]), API_ROLES)
        self.arm.send("DELETE", identity_id, API_IDENTITIES)


# region main


def show(plan: Plan) -> None:
    """Print the plan.

    Args:
        plan: What a run would change and report.
    """
    for note in plan.notes:
        warn(f"  {note}")
    if not plan.changes:
        success("Everything matches the config; nothing to change.")
        return
    print(f"\n  Changes ({len(plan.changes)}):")
    for change in plan.changes:
        print(f"    {change.kind:<6} {change.what}")
        if change.detail:
            print(f"           {change.detail}")


def apply(plan: Plan) -> None:
    """Make every change in the plan, in order.

    Args:
        plan: The changes to make.
    """
    for change in plan.changes:
        echo(f"{change.kind} {change.what}")
        change.apply()
    success(f"Made {len(plan.changes)} change(s).")


def main(argv: list[str] | None = None) -> None:
    """Parse arguments, compare the config with Azure and GitHub, and apply the plan.

    Args:
        argv: Command-line arguments; ``sys.argv[1:]`` when None.
    """
    global _assume_yes
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("config", type=Path, help="The TOML config file for the repo's jobs.")
    parser.add_argument(
        "--dry-run", action="store_true", help="List the differences without changing anything."
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args(argv)
    _assume_yes = args.yes

    section("Container Apps jobs")
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        die(str(exc))
    info("Config", str(args.config))
    info("Repository", config.repository)
    info("Jobs", ", ".join(job.name for job in config.jobs))

    token, tenant = azure_token(config.shared.subscription)
    planner = Planner(config, Arm(token), GitHub(config.repository), tenant)
    try:
        plan = planner.plan_all()
        show(plan)
        if not plan.changes or args.dry_run:
            if args.dry_run:
                print("\n  (dry run -- nothing changed)")
            return
        print()
        if not confirm(f"Make these {len(plan.changes)} change(s)?"):
            print("  Aborted; nothing changed.")
            sys.exit(1)
        apply(plan)
    except AzureError as exc:
        die(str(exc))
    except subprocess.CalledProcessError as exc:
        die(f"{exc.cmd} failed (exit {exc.returncode}).")


if __name__ == "__main__":
    main()
