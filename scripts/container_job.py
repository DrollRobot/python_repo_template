"""Create or update an Azure Container Apps job, and everything it needs, from a config file.

The config file is the whole description of the job: the shared resources it runs on,
its identities and their roles, its schedule and arguments, the Key Vault secrets it
reads, the GitHub repo variables its image workflow uses, and its failed-run alert. To
change something later, edit the file and run this script again. Each run compares the
file with Azure and GitHub, lists every difference, and asks once before changing
anything. ``--dry-run`` lists the differences and stops.

What it manages, in order:

1. Shared resources (resource providers, resource group, registry, Log Analytics
   workspace, virtual network and subnet, Container Apps environment): created when
   missing. Existing ones are never changed, since some of their settings can't change
   in place; differences that matter are reported.
2. The job's identity: AcrPull on the registry, Key Vault Secrets User on exactly the
   listed secrets, and the vault's network rule for the environment's subnet.
3. The job: schedule, arguments, environment variables, CPU, memory, timeout. A new
   job starts on Microsoft's public quickstart image; after that the image is never
   touched, since the release workflow (``.github/workflows/image.yml``) owns it.
4. The CI identity the image workflow signs in as: AcrPush, rights to update the job,
   and a federated credential trusting the repo's ``main`` branch.
5. The repo variables the image workflow reads.
6. The failed-run alert and the email it goes to.

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
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, NoReturn

ARM = "https://management.azure.com"

# ARM API versions, one per resource type this script touches.
API_PROVIDERS = "2021-04-01"
API_RESOURCE_GROUPS = "2021-04-01"
API_REGISTRIES = "2023-07-01"
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

ROLE_ACR_PULL = "AcrPull"
ROLE_ACR_PUSH = "AcrPush"
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
class Job:
    """The job and its two identities; see the example config."""

    name: str
    identity: str
    ci_identity: str
    schedule: str
    args: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    cpu: float
    memory: str
    timeout: int


@dataclass(frozen=True)
class KeyVault:
    """The vault the job reads secrets from, and which secrets; see the example config."""

    name: str
    resource_group: str
    secrets: tuple[str, ...]


@dataclass(frozen=True)
class Alert:
    """The failed-run alert; see the example config."""

    email: str
    action_group: str
    rule: str


@dataclass(frozen=True)
class Config:
    """A parsed and validated config file."""

    shared: Shared
    job: Job
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


def load_config(path: Path) -> Config:
    """Read and validate the config file at *path*.

    Args:
        path: The TOML file.

    Returns:
        The parsed config.

    Raises:
        ConfigError: If the file is missing or malformed, or a setting is missing,
            unknown, or of the wrong type.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"No config file at {path}.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    _check_keys(raw, "the top level", {"shared", "job", "alert", "github"}, {"key_vault"})

    shared_t = _table(raw, "shared")
    shared_keys = {f.name for f in fields(Shared)}
    _check_keys(shared_t, "shared", shared_keys, set())
    shared = Shared(**{key: _str(shared_t, key, "shared") for key in shared_keys})

    job_t = _table(raw, "job")
    _check_keys(
        job_t,
        "job",
        {"name", "identity", "ci_identity", "schedule", "args", "cpu", "memory", "timeout"},
        {"env"},
    )
    cpu = job_t["cpu"]
    if isinstance(cpu, bool) or not isinstance(cpu, int | float) or cpu <= 0:
        raise ConfigError("[job].cpu must be a positive number of cores, such as 0.25.")
    timeout = job_t["timeout"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ConfigError("[job].timeout must be a positive whole number of seconds.")
    env_raw = job_t.get("env", {})
    if not isinstance(env_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env_raw.items()
    ):
        raise ConfigError("[job].env must be a table of string values.")
    job = Job(
        name=_str(job_t, "name", "job"),
        identity=_str(job_t, "identity", "job"),
        ci_identity=_str(job_t, "ci_identity", "job"),
        schedule=_str(job_t, "schedule", "job"),
        args=_str_list(job_t, "args", "job"),
        env=tuple(sorted(env_raw.items())),
        cpu=float(cpu),
        memory=_str(job_t, "memory", "job"),
        timeout=timeout,
    )

    key_vault = None
    if "key_vault" in raw:
        vault_t = _table(raw, "key_vault")
        _check_keys(vault_t, "key_vault", {"name", "resource_group", "secrets"}, set())
        key_vault = KeyVault(
            name=_str(vault_t, "name", "key_vault"),
            resource_group=_str(vault_t, "resource_group", "key_vault"),
            secrets=_str_list(vault_t, "secrets", "key_vault"),
        )

    alert_t = _table(raw, "alert")
    _check_keys(alert_t, "alert", {"email", "action_group", "rule"}, set())
    alert = Alert(
        email=_str(alert_t, "email", "alert"),
        action_group=_str(alert_t, "action_group", "alert"),
        rule=_str(alert_t, "rule", "alert"),
    )

    github_t = _table(raw, "github")
    _check_keys(github_t, "github", {"repository"}, set())
    repository = _str(github_t, "repository", "github")
    if repository.count("/") != 1:
        raise ConfigError("[github].repository must be owner/name.")

    return Config(shared=shared, job=job, key_vault=key_vault, alert=alert, repository=repository)


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
        url = f"{ARM}{path}{separator}api-version={api_version}"
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


def _same(left: str | None, right: str | None) -> bool:
    """Compare two resource ids or names, ignoring case as Azure does.

    Args:
        left: One id.
        right: The other.

    Returns:
        Whether they name the same thing.
    """
    return (left or "").lower() == (right or "").lower()


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
        self._role_ids: dict[str, str] = {}
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
        self.job_id = f"{self.rg_id}/providers/Microsoft.App/jobs/{config.job.name}"
        self.identity_id = self._identity_path(config.job.identity)
        self.ci_identity_id = self._identity_path(config.job.ci_identity)
        self.action_group_id = (
            f"{self.rg_id}/providers/Microsoft.Insights/actionGroups/{config.alert.action_group}"
        )
        self.alert_id = (
            f"{self.rg_id}/providers/Microsoft.Insights/scheduledQueryRules/{config.alert.rule}"
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

    def _add(self, kind: str, what: str, apply: Callable[[], None], detail: str = "") -> None:
        """Record one change.

        Args:
            kind: ``create``, ``update`` or ``remove``.
            what: What changes, for the listing.
            apply: Makes the change.
            detail: Extra line for the listing, such as old and new values.
        """
        self.plan.changes.append(Change(kind, what, apply, detail))

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

    def _role_definition(self, role: str) -> str:
        """Return the resource id of built-in role *role*.

        Args:
            role: Role name, such as ``AcrPull``.

        Returns:
            The role definition's id.
        """
        if role not in self._role_ids:
            query = urllib.parse.quote(f"roleName eq '{role}'")
            found = self.arm.get(
                f"{self.sub_id}/providers/Microsoft.Authorization/roleDefinitions?$filter={query}",
                API_ROLES,
            )
            values = (found or {}).get("value", [])
            if len(values) != 1:
                raise AzureError(f"Found {len(values)} role definitions named {role!r}.")
            self._role_ids[role] = str(values[0]["id"])
        return self._role_ids[role]

    # -- the six parts

    def plan_all(self) -> Plan:
        """Compare everything, in the order it must be created.

        Returns:
            The plan.
        """
        self.plan_shared()
        self.plan_job_identity()
        self.plan_job()
        self.plan_ci_identity()
        self.plan_repo_variables()
        self.plan_alert()
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

        if self.arm.get(self.registry_id, API_REGISTRIES) is None:
            self._add(
                "create",
                f"registry {shared.registry} (Basic, admin user off)",
                lambda: self._create_and_wait(
                    self.registry_id,
                    API_REGISTRIES,
                    {
                        "location": location,
                        "sku": {"name": "Basic"},
                        "properties": {"adminUserEnabled": False},
                    },
                ),
            )

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
        """Plan creating a user-assigned identity if it is missing.

        Args:
            identity_id: Its resource id.
            name: Its name, for the listing.

        Returns:
            Whether it already exists.
        """
        if self.arm.get(identity_id, API_IDENTITIES) is not None:
            return True
        self._add(
            "create",
            f"managed identity {name}",
            lambda: self.arm.send(
                "PUT", identity_id, API_IDENTITIES, {"location": self.config.shared.location}
            ),
        )
        return False

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
        exists: bool,
        desired: list[tuple[str, str, str]],
        managed_prefixes: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Plan the role assignments one identity should hold.

        Args:
            identity_id: The identity's resource id.
            exists: Whether the identity exists yet.
            desired: ``(role, scope, label)`` for each assignment it should hold.
            managed_prefixes: ``(role, scope prefix)`` pairs this script owns
                completely: an assignment of that role under that prefix that is not
                in *desired* is removed.
        """
        held: list[dict[str, Any]] = []
        if exists:
            held = self._assignments(self._principal(identity_id))

        def holds(role: str, scope: str) -> bool:
            role_id = self._role_definition(role)
            return any(
                _same(a.get("roleDefinitionId"), role_id) and _same(a.get("scope"), scope)
                for a in held
            )

        for role, scope, label in desired:
            if exists and holds(role, scope):
                continue
            self._add(
                "create",
                f"role {role} on {label}",
                functools.partial(self._assign, identity_id, role, scope),
            )

        for role, prefix in managed_prefixes:
            role_id = self._role_definition(role)
            wanted = {scope.lower() for r, scope, _ in desired if r == role}
            for assignment in held:
                scope = str(assignment.get("scope", ""))
                if (
                    _same(assignment.get("roleDefinitionId"), role_id)
                    and scope.lower().startswith(prefix.lower())
                    and scope.lower() not in wanted
                ):
                    self._add(
                        "remove",
                        f"role {role} on {scope.rsplit('/', 1)[-1]}",
                        functools.partial(
                            self.arm.send, "DELETE", str(assignment["id"]), API_ROLES
                        ),
                        "no longer listed in the config",
                    )

    def _assign(self, identity_id: str, role: str, scope: str) -> None:
        """Grant *role* on *scope* to an identity.

        Args:
            identity_id: The identity's resource id.
            role: Role name.
            scope: Resource id the role applies to.
        """
        self.arm.send(
            "PUT",
            f"{scope}/providers/Microsoft.Authorization/roleAssignments/{uuid.uuid4()}",
            API_ROLES,
            {
                "properties": {
                    "roleDefinitionId": self._role_definition(role),
                    "principalId": self._principal(identity_id),
                    "principalType": "ServicePrincipal",
                }
            },
        )

    def plan_job_identity(self) -> None:
        """Plan the job identity, its roles, and the vault's network rule."""
        job = self.config.job
        exists = self._plan_identity(self.identity_id, job.identity)
        desired = [(ROLE_ACR_PULL, self.registry_id, f"registry {self.config.shared.registry}")]
        managed: tuple[tuple[str, str], ...] = ()
        vault = self.config.key_vault
        if vault is not None:
            desired += [
                (ROLE_SECRETS_USER, f"{self.vault_id}/secrets/{secret}", f"secret {secret}")
                for secret in vault.secrets
            ]
            managed = ((ROLE_SECRETS_USER, f"{self.vault_id}/secrets/"),)
        self._plan_roles(self.identity_id, exists, desired, managed)
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

    def _job_body(self, image: str) -> dict[str, Any]:
        """Return the job as the config describes it, running *image*.

        Args:
            image: The container image.

        Returns:
            The job resource body.
        """
        job = self.config.job
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
            "identity": {
                "type": "UserAssigned",
                "userAssignedIdentities": {self.identity_id: {}},
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
                            "identity": self.identity_id,
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
        }

    def plan_job(self) -> None:
        """Plan the job: create it on the quickstart image, or update what differs."""
        job = self.config.job
        current = self.arm.get(self.job_id, API_CONTAINER_APPS)
        if current is None:
            self._add(
                "create",
                f"job {job.name} (schedule {job.schedule}, quickstart image until the first "
                "release)",
                lambda: self._create_and_wait(
                    self.job_id, API_CONTAINER_APPS, self._job_body(QUICKSTART_IMAGE)
                ),
            )
            return
        containers = ((current.get("properties") or {}).get("template") or {}).get(
            "containers"
        ) or [{}]
        image = str(containers[0].get("image", QUICKSTART_IMAGE))
        wanted = self._job_body(image)
        have, want = self._job_settings(current), self._job_settings(wanted)
        differences = [key for key in want if have.get(key) != want[key]]
        if differences:
            detail = "; ".join(f"{key}: {have.get(key)!r} -> {want[key]!r}" for key in differences)
            self._add(
                "update",
                f"job {job.name} ({', '.join(differences)})",
                lambda: self._create_and_wait(self.job_id, API_CONTAINER_APPS, wanted),
                f"{detail}; image kept: {image}",
            )

    def plan_ci_identity(self) -> None:
        """Plan the CI identity, its roles, and its federated credential."""
        job = self.config.job
        exists = self._plan_identity(self.ci_identity_id, job.ci_identity)
        self._plan_roles(
            self.ci_identity_id,
            exists,
            [
                (ROLE_ACR_PUSH, self.registry_id, f"registry {self.config.shared.registry}"),
                (ROLE_JOBS_CONTRIBUTOR, self.job_id, f"job {job.name}"),
                (
                    ROLE_JOBS_CONTRIBUTOR,
                    self.environment_id,
                    f"environment {self.config.shared.environment}",
                ),
                (ROLE_IDENTITY_OPERATOR, self.identity_id, f"identity {job.identity}"),
            ],
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
                f"federated credential {FEDERATED_CREDENTIAL} on {job.ci_identity}",
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
            "CONTAINER_APP_JOB": self.config.job.name,
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

    def _alert_body(self) -> dict[str, Any]:
        """Return the failed-run alert rule as the config describes it.

        Returns:
            The scheduled query rule body.
        """
        job = self.config.job.name
        return {
            "location": self.config.shared.location,
            "properties": {
                "displayName": self.config.alert.rule,
                "description": f"A {job} execution failed or timed out.",
                "severity": 1,
                "enabled": True,
                "evaluationFrequency": "PT5M",
                "windowSize": "PT10M",
                "scopes": [self.workspace_id],
                "criteria": {
                    "allOf": [
                        {
                            "query": (
                                f"ContainerAppSystemLogs_CL | where JobName_s == '{job}' "
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

    def plan_alert(self) -> None:
        """Plan the action group's email receiver and the failed-run alert rule."""
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

        rule = self.arm.get(self.alert_id, API_SCHEDULED_QUERIES)
        wanted = self._alert_body()
        if rule is None or not self._same_alert(rule, wanted):
            self._add(
                "create" if rule is None else "update",
                f"alert {alert.rule} on failed {self.config.job.name} runs",
                lambda: self.arm.send("PUT", self.alert_id, API_SCHEDULED_QUERIES, wanted),
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
            )

        return parts(have) == parts(want)


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
    parser.add_argument("config", type=Path, help="The job's TOML config file.")
    parser.add_argument(
        "--dry-run", action="store_true", help="List the differences without changing anything."
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args(argv)
    _assume_yes = args.yes

    section("Container Apps job")
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        die(str(exc))
    info("Config", str(args.config))
    info("Job", config.job.name)

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
