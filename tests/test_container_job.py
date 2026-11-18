"""Tests for scripts/container_job.py.

Azure is faked at the HTTP layer (:class:`FakeAzure`): PUTs store resources, GETs
read them back, so a test can apply a plan and then plan again against the result.
GitHub is faked at the client layer (:class:`FakeGitHub`).

The example config is the starting point of every test: two jobs, each with its own
identity.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import container_job

EXAMPLE = Path(__file__).resolve().parent.parent / "scripts" / "container_job.example.toml"

JOBS = "Microsoft.App/jobs"
RULES = "Microsoft.Insights/scheduledQueryRules"
IDENTITIES = "Microsoft.ManagedIdentity/userAssignedIdentities"
REGISTRIES = "Microsoft.ContainerRegistry/registries"

READER = container_job.ROLE_IMAGE_READER
WRITER = container_job.ROLE_IMAGE_WRITER
# The data actions of the two registry roles, as Azure defines them.
_IMAGES = "Microsoft.ContainerRegistry/registries/repositories"
_DATA_ACTIONS = {
    READER: [f"{_IMAGES}/metadata/read", f"{_IMAGES}/content/read"],
    WRITER: [
        f"{_IMAGES}/metadata/read",
        f"{_IMAGES}/content/read",
        f"{_IMAGES}/metadata/write",
        f"{_IMAGES}/content/write",
    ],
}
# What limits a registry role to the example config's image name, "name".
_ON_THIS_IMAGE = (
    "@Request[Microsoft.ContainerRegistry/registries/repositories:name] "
    "StringEqualsIgnoreCase 'name'"
)


class FakeAzure:
    """Azure Resource Manager as a dict of resources, behind a transport function."""

    def __init__(self) -> None:
        self.resources: dict[str, dict[str, Any]] = {}
        self.unregistered: set[str] = set()
        self.writes: list[tuple[str, str]] = []

    def add(self, path: str, resource: dict[str, Any]) -> None:
        self.resources[path.lower()] = {"id": path, **resource}

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        payload = json.loads(body) if body else None
        if method != "GET":
            self.writes.append((method, path))
        return self._handle(method, path, query, payload)

    @staticmethod
    def _reply(status: int, data: Any = None) -> tuple[int, bytes]:
        return status, b"" if data is None else json.dumps(data).encode()

    def _handle(
        self, method: str, path: str, query: dict[str, list[str]], payload: Any
    ) -> tuple[int, bytes]:
        key = path.lower()
        if key.endswith("/providers/microsoft.authorization/roledefinitions"):
            name = query["$filter"][0].split("'")[1]
            permissions = [{"actions": [], "dataActions": _DATA_ACTIONS.get(name, [])}]
            definition = {"id": f"/roledefs/{name}", "properties": {"permissions": permissions}}
            return self._reply(200, {"value": [definition]})
        if key.endswith("/providers/microsoft.authorization/roleassignments"):
            principal = query["$filter"][0].split("'")[1]
            found = [
                r
                for k, r in self.resources.items()
                if "/roleassignments/" in k and r["properties"]["principalId"] == principal
            ]
            return self._reply(200, {"value": found})
        if method == "GET" and key.endswith(tuple(f"/{kind.lower()}" for kind in _KINDS)):
            # A resource group's resources of one type; their child resources are left out.
            members = [
                r
                for k, r in self.resources.items()
                if k.startswith(f"{key}/") and "/" not in k[len(key) + 1 :]
            ]
            return self._reply(200, {"value": members})
        if "/providers/" in key and key.count("/providers/") == 1 and method == "GET":
            namespace = path.rsplit("/providers/", 1)[1]
            if "/" not in namespace:
                state = "NotRegistered" if namespace in self.unregistered else "Registered"
                return self._reply(200, {"registrationState": state})
        if method == "POST" and key.endswith("/register"):
            self.unregistered.discard(path.rsplit("/", 2)[-2])
            return self._reply(200, {})
        if method == "POST" and key.endswith("/sharedkeys"):
            return self._reply(200, {"primarySharedKey": "shared-key"})
        if method == "GET":
            resource = self.resources.get(key)
            return self._reply(200, resource) if resource else self._reply(404, {"error": {}})
        if method == "DELETE":
            self.resources.pop(key, None)
            return self._reply(200)
        if method == "PATCH":
            current = self.resources[key]
            current["properties"] = {**current.get("properties", {}), **payload["properties"]}
            return self._reply(200, current)
        if method == "PUT":
            resource = {"id": path, **payload}
            properties = dict(resource.get("properties") or {})
            if "/roleassignments/" in key:
                properties["scope"] = path.split("/providers/Microsoft.Authorization/")[0]
            elif key.rsplit("/", 2)[-2] == "userassignedidentities":
                # Azure keeps an identity's principal and client ids when it is updated.
                existing = (self.resources.get(key) or {}).get("properties") or {}
                properties.update(
                    principalId=existing.get("principalId") or str(uuid.uuid4()),
                    clientId=existing.get("clientId") or str(uuid.uuid4()),
                    tenantId="t",
                )
            elif key.rsplit("/", 2)[-2] == "virtualnetworks":
                # Azure creates a network's subnets as resources of their own.
                for subnet in properties.get("subnets") or []:
                    self.add(f"{path}/subnets/{subnet['name']}", subnet)
            properties.setdefault("provisioningState", "Succeeded")
            if "/workspaces/" in key:
                properties["customerId"] = "customer-id"
            resource["properties"] = properties
            self.resources[key] = resource
            return self._reply(201, resource)
        raise AssertionError(f"unhandled {method} {path}")


_KINDS = (JOBS, RULES, IDENTITIES)


class FakeGitHub:
    """The GitHub client, with variables kept in a dict."""

    repository = "owner/name"

    def __init__(self) -> None:
        self.vars: dict[str, str] = {}

    def subject_prefix(self) -> str:
        return "repo:owner@1/name@2"

    def variables(self) -> dict[str, str]:
        return dict(self.vars)

    def set_variable(self, name: str, value: str) -> None:
        self.vars[name] = value


@pytest.fixture
def config() -> container_job.Config:
    return container_job.load_config(EXAMPLE)


def _planner(
    config: container_job.Config, azure: FakeAzure, github: FakeGitHub
) -> container_job.Planner:
    return container_job.Planner(
        config,
        container_job.Arm("token", transport=azure),
        github,  # type: ignore[arg-type]
        "tenant-id",
        sleep=lambda seconds: None,
    )


def _vault(azure: FakeAzure, config: container_job.Config, *, deny: bool = True) -> str:
    vault = config.key_vault
    assert vault is not None
    path = (
        f"/subscriptions/{config.shared.subscription}/resourceGroups/{vault.resource_group}"
        f"/providers/Microsoft.KeyVault/vaults/{vault.name}"
    )
    azure.add(
        path,
        {
            "properties": {
                "networkAcls": {
                    "defaultAction": "Deny" if deny else "Allow",
                    "ipRules": [{"value": "203.0.113.7"}],
                    "virtualNetworkRules": [],
                }
            }
        },
    )
    return path


def _built(config: container_job.Config) -> tuple[FakeAzure, FakeGitHub, str]:
    """Run a config against an empty subscription and apply the plan."""
    azure, github = FakeAzure(), FakeGitHub()
    vault_path = _vault(azure, config)
    container_job.apply(_planner(config, azure, github).plan_all())
    return azure, github, vault_path


def _path(config: container_job.Config, kind: str, name: str) -> str:
    return (
        f"/subscriptions/{config.shared.subscription}/resourceGroups/"
        f"{config.shared.resource_group}/providers/{kind}/{name}"
    )


def _resource(
    azure: FakeAzure, config: container_job.Config, kind: str, name: str
) -> dict[str, Any]:
    return azure.resources[_path(config, kind, name).lower()]


def _names(azure: FakeAzure, config: container_job.Config, kind: str) -> list[str]:
    """Return the names of the resource group's resources of one type."""
    prefix = _path(config, kind, "").lower()
    return sorted(
        k[len(prefix) :]
        for k in azure.resources
        if k.startswith(prefix) and "/" not in k[len(prefix) :]
    )


def _secret_roles(azure: FakeAzure, config: container_job.Config, identity: str) -> list[str]:
    """Return the secrets an identity holds a role assignment on."""
    principal = _resource(azure, config, IDENTITIES, identity)["properties"]["principalId"]
    return sorted(
        r["properties"]["scope"].rsplit("/", 1)[-1]
        for k, r in azure.resources.items()
        if "/roleassignments/" in k
        and r["properties"]["principalId"] == principal
        and "/secrets/" in r["properties"]["scope"]
    )


def _registry_role(azure: FakeAzure, config: container_job.Config, identity: str) -> dict[str, Any]:
    """Return the properties of the one role assignment an identity holds on the registry."""
    principal = _resource(azure, config, IDENTITIES, identity)["properties"]["principalId"]
    registry = _path(config, REGISTRIES, config.shared.registry)
    held: list[dict[str, Any]] = [
        r["properties"]
        for k, r in azure.resources.items()
        if "/roleassignments/" in k
        and r["properties"]["principalId"] == principal
        and r["properties"]["scope"] == registry
    ]
    assert len(held) == 1
    return held[0]


def _grant(
    azure: FakeAzure, config: container_job.Config, identity: str, role: str, scope: str
) -> None:
    """Give an identity a role by hand, with no condition."""
    principal = _resource(azure, config, IDENTITIES, identity)["properties"]["principalId"]
    azure.add(
        f"{scope}/providers/Microsoft.Authorization/roleAssignments/{uuid.uuid4()}",
        {
            "properties": {
                "roleDefinitionId": f"/roledefs/{role}",
                "principalId": principal,
                "scope": scope,
            }
        },
    )


def _role_count(azure: FakeAzure) -> int:
    return sum("/roleassignments/" in key for key in azure.resources)


def _with_job(config: container_job.Config, name: str, **changes: Any) -> container_job.Config:
    """Return the config with one job's settings changed."""
    assert name in {job.name for job in config.jobs}
    jobs = tuple(replace(job, **changes) if job.name == name else job for job in config.jobs)
    return replace(config, jobs=jobs)


def _with_identity(config: container_job.Config, name: str, **changes: Any) -> container_job.Config:
    """Return the config with one identity's settings changed."""
    assert name in {identity.name for identity in config.identities}
    identities = tuple(
        replace(identity, **changes) if identity.name == name else identity
        for identity in config.identities
    )
    return replace(config, identities=identities)


def _only(config: container_job.Config, job_name: str) -> container_job.Config:
    """Return the config with one job, and the identity it runs as, and no others."""
    jobs = tuple(job for job in config.jobs if job.name == job_name)
    used = {job.identity for job in jobs}
    identities = tuple(identity for identity in config.identities if identity.name in used)
    return replace(config, jobs=jobs, identities=identities)


def _changes(plan: container_job.Plan) -> list[tuple[str, str]]:
    return sorted((change.kind, change.what) for change in plan.changes)


# region config


@pytest.mark.unit
def test_the_example_config_loads(config: container_job.Config) -> None:
    assert [job.name for job in config.jobs] == ["job-example-daily", "job-example-weekly"]
    assert config.jobs[0].args == ("daily", "--log-level", "DEBUG")
    assert config.jobs[0].identity == "id-example-daily"
    assert config.jobs[1].alert_rule == "alert-job-example-weekly-failed"
    assert config.ci_identity == "id-example-ci"
    assert config.key_vault is not None
    assert {identity.name: identity.secrets for identity in config.identities} == {
        "id-example-daily": ("example-secret-name",),
        "id-example-weekly": ("example-secret-name", "example-report-secret-name"),
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("timeout = 600\n", "", r"\[job 1\] is missing timeout"),
        ("timeout = 600\n", "timeout = 600\ncolour = 'blue'\n", r"unknown key\(s\) colour"),
        ("cpu = 0.5\n", "cpu = 'lots'\n", r"\[job 2\]\.cpu"),
        ('repository = "owner/name"', 'repository = "name"', r"owner/name"),
        ('repository = "owner/name"', 'repository = "owner/na\'me"', r"owner/name"),
        ("[alert]\n", "[alarm]\n", r"is missing alert"),
        ("[ci]\n", "[[ci]]\n", r"\[ci\] is missing or is not a table"),
        (
            'secrets = ["example-secret-name"]\n',
            "",
            r"\[identity 1\] is missing secrets",
        ),
        (
            'identity = "id-example-daily"',
            'identity = "id-example-nobody"',
            r"Job job-example-daily runs as id-example-nobody, which no \[\[identity\]\]",
        ),
        (
            'identity = "id-example-weekly"',
            'identity = "id-example-daily"',
            r"No job runs as the identity id-example-weekly",
        ),
        (
            'identity = "id-example-ci"',
            'identity = "id-example-daily"',
            r"\[ci\]\.identity id-example-daily is also a job's identity",
        ),
        (
            'name = "job-example-weekly"',
            'name = "job-example-daily"',
            r"More than one \[\[job\]\] table has the name job-example-daily",
        ),
        (
            'alert_rule = "alert-job-example-weekly-failed"',
            'alert_rule = "alert-job-example-daily-failed"',
            r"table has the alert_rule alert-job-example-daily-failed",
        ),
        (
            'name = "id-example-weekly"',
            'name = "ID-EXAMPLE-DAILY"',
            r"More than one \[\[identity\]\] table has the name id-example-daily",
        ),
    ],
)
def test_load_config_fails_loudly_on_a_bad_setting(
    tmp_path: Path, old: str, new: str, message: str
) -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "job.toml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

    with pytest.raises(container_job.ConfigError, match=message):
        container_job.load_config(path)


@pytest.mark.unit
def test_load_config_needs_at_least_one_job(tmp_path: Path) -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    start, end = text.index("# One [[job]] table"), text.index("[alert]")
    path = tmp_path / "job.toml"
    path.write_text(text[:start] + text[end:], encoding="utf-8")

    with pytest.raises(container_job.ConfigError, match=r"is missing job"):
        container_job.load_config(path)


@pytest.mark.unit
def test_load_config_refuses_a_single_job_table(tmp_path: Path) -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    start, end = text.index('[[job]]\nname = "job-example-weekly"'), text.index("[alert]")
    path = tmp_path / "job.toml"
    path.write_text((text[:start] + text[end:]).replace("[[job]]", "[job]"), encoding="utf-8")

    with pytest.raises(container_job.ConfigError, match=r"at least one \[\[job\]\] table"):
        container_job.load_config(path)


def _without_key_vault(text: str) -> str:
    start, end = text.index("[key_vault]"), text.index("# One [[identity]] table")
    return text[:start] + text[end:]


@pytest.mark.unit
def test_load_config_allows_leaving_out_the_key_vault(tmp_path: Path) -> None:
    lines = _without_key_vault(EXAMPLE.read_text(encoding="utf-8")).splitlines(keepends=True)
    path = tmp_path / "job.toml"
    path.write_text(
        "".join(line for line in lines if not line.startswith("secrets = ")), encoding="utf-8"
    )

    loaded = container_job.load_config(path)

    assert loaded.key_vault is None
    assert [identity.secrets for identity in loaded.identities] == [(), ()]


@pytest.mark.unit
def test_load_config_refuses_secrets_without_a_key_vault(tmp_path: Path) -> None:
    path = tmp_path / "job.toml"
    path.write_text(_without_key_vault(EXAMPLE.read_text(encoding="utf-8")), encoding="utf-8")

    with pytest.raises(
        container_job.ConfigError, match=r"\[identity 1\] lists secrets, but the file has no"
    ):
        container_job.load_config(path)


# region plans


@pytest.mark.integration
@pytest.mark.acceptance
def test_an_empty_subscription_gets_everything_in_dependency_order(
    config: container_job.Config,
) -> None:
    azure, github = FakeAzure(), FakeGitHub()
    azure.unregistered = {"Microsoft.App"}
    _vault(azure, config)

    plan = _planner(config, azure, github).plan_all()
    whats = [change.what for change in plan.changes]

    def first(prefix: str) -> int:
        return next(i for i, what in enumerate(whats) if what.startswith(prefix))

    assert first("register resource provider Microsoft.App") < first("resource group")
    assert first("virtual network") < first("Container Apps environment")
    assert first("Container Apps environment") < first("job job-example-daily")
    assert first("registry acrexamplejobs") < first("managed identity id-example-daily")
    assert first("managed identity id-example-daily") < first(
        f"role {READER} on image name in registry acrexamplejobs for id-example-daily"
    )
    assert first("managed identity id-example-weekly") < first("job job-example-weekly")
    assert first("job job-example-weekly") < first(
        "role Container Apps Jobs Contributor on job job-example-weekly for id-example-ci"
    )
    assert first("Key Vault kv-example") < first("job job-example-daily")
    assert {c.kind for c in plan.changes} == {"create", "update"}

    container_job.apply(plan)

    registry = _resource(azure, config, REGISTRIES, "acrexamplejobs")
    assert registry["properties"]["roleAssignmentMode"] == "AbacRepositoryPermissions"
    assert registry["properties"]["adminUserEnabled"] is False
    assert _names(azure, config, JOBS) == ["job-example-daily", "job-example-weekly"]
    daily = _resource(azure, config, JOBS, "job-example-daily")
    container = daily["properties"]["template"]["containers"][0]
    assert container["image"] == container_job.QUICKSTART_IMAGE
    assert container["args"] == ["daily", "--log-level", "DEBUG"]
    assert daily["tags"] == {container_job.REPOSITORY_TAG: "owner/name"}
    assert list(daily["identity"]["userAssignedIdentities"]) == [
        _path(config, IDENTITIES, "id-example-daily")
    ]
    assert _secret_roles(azure, config, "id-example-daily") == ["example-secret-name"]
    assert _secret_roles(azure, config, "id-example-weekly") == [
        "example-report-secret-name",
        "example-secret-name",
    ]
    assert _names(azure, config, RULES) == [
        "alert-job-example-daily-failed",
        "alert-job-example-weekly-failed",
    ]
    assert set(github.vars) == {
        "ACR_NAME",
        "AZURE_CLIENT_ID",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_TENANT_ID",
        "CONTAINER_APP_JOBS",
        "CONTAINER_APP_RESOURCE_GROUP",
    }
    assert github.vars["CONTAINER_APP_JOBS"] == "job-example-daily job-example-weekly"
    assert github.vars["AZURE_CLIENT_ID"] not in ("", None)


@pytest.mark.integration
@pytest.mark.acceptance
def test_a_second_run_finds_nothing_to_change(config: container_job.Config) -> None:
    azure, github, _ = _built(config)
    azure.writes.clear()

    plan = _planner(config, azure, github).plan_all()

    assert plan.changes == []
    assert plan.notes == []


@pytest.mark.integration
@pytest.mark.acceptance
def test_a_changed_schedule_updates_that_job_alone_and_keeps_its_image(
    config: container_job.Config,
) -> None:
    azure, github, _ = _built(config)
    released = "acrexamplejobs.azurecr.io/app:abc123"
    daily = _resource(azure, config, JOBS, "job-example-daily")
    daily["properties"]["template"]["containers"][0]["image"] = released
    daily["tags"]["owner"] = "soc"
    changed = _with_job(config, "job-example-daily", schedule="0 6 * * *")

    plan = _planner(changed, azure, github).plan_all()

    assert _changes(plan) == [("update", "job job-example-daily (schedule)")]
    container_job.apply(plan)
    job = _resource(azure, changed, JOBS, "job-example-daily")
    assert job["properties"]["configuration"]["scheduleTriggerConfig"]["cronExpression"] == (
        "0 6 * * *"
    )
    assert job["properties"]["template"]["containers"][0]["image"] == released
    assert job["tags"] == {"owner": "soc", container_job.REPOSITORY_TAG: "owner/name"}


@pytest.mark.integration
@pytest.mark.acceptance
def test_a_dropped_secret_loses_its_role_and_an_added_one_gains_it(
    config: container_job.Config,
) -> None:
    azure, github, _ = _built(config)
    changed = _with_identity(config, "id-example-weekly", secrets=("other-secret",))

    plan = _planner(changed, azure, github).plan_all()

    assert _changes(plan) == [
        ("create", "role Key Vault Secrets User on secret other-secret for id-example-weekly"),
        (
            "remove",
            "role Key Vault Secrets User on example-report-secret-name for id-example-weekly",
        ),
        ("remove", "role Key Vault Secrets User on example-secret-name for id-example-weekly"),
    ]
    container_job.apply(plan)
    assert _secret_roles(azure, changed, "id-example-weekly") == ["other-secret"]
    assert _secret_roles(azure, changed, "id-example-daily") == ["example-secret-name"]
    assert _planner(changed, azure, github).plan_all().changes == []


@pytest.mark.integration
@pytest.mark.acceptance
def test_registry_roles_reach_this_repos_image_name_and_no_other(
    config: container_job.Config,
) -> None:
    azure, _, _ = _built(config)

    pull = _registry_role(azure, config, "id-example-daily")
    push = _registry_role(azure, config, "id-example-ci")

    assert pull["roleDefinitionId"] == f"/roledefs/{READER}"
    assert pull["conditionVersion"] == "2.0"
    assert pull["condition"] == (
        f"((!(ActionMatches{{'{_IMAGES}/content/read'}}) "
        f"AND !(ActionMatches{{'{_IMAGES}/metadata/read'}})) "
        f"OR ({_ON_THIS_IMAGE}))"
    )
    assert push["roleDefinitionId"] == f"/roledefs/{WRITER}"
    assert push["conditionVersion"] == "2.0"
    assert push["condition"].endswith(f") OR ({_ON_THIS_IMAGE}))")
    # An action the condition left out would stay allowed on every image name.
    for action in _DATA_ACTIONS[WRITER]:
        assert f"!(ActionMatches{{'{action}'}})" in push["condition"]


@pytest.mark.integration
@pytest.mark.parametrize("properties", [{"roleAssignmentMode": "LegacyRegistryPermissions"}, {}])
def test_a_registry_that_cannot_limit_a_role_to_an_image_name_stops_the_run(
    config: container_job.Config, properties: dict[str, str]
) -> None:
    azure, github = FakeAzure(), FakeGitHub()
    _vault(azure, config)
    azure.add(_path(config, REGISTRIES, config.shared.registry), {"properties": properties})

    with pytest.raises(
        container_job.AzureError,
        match=r"az acr update --name acrexamplejobs --resource-group rg-container-jobs "
        r"--role-assignment-mode rbac-abac",
    ):
        _planner(config, azure, github).plan_all()
    assert azure.writes == []


@pytest.mark.integration
def test_a_registry_role_a_condition_cannot_limit_stops_the_run(
    config: container_job.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    azure, github = FakeAzure(), FakeGitHub()
    _vault(azure, config)
    monkeypatch.setitem(_DATA_ACTIONS, READER, [])

    with pytest.raises(container_job.AzureError, match=r"can't be limited to one image name"):
        _planner(config, azure, github).plan_all()


@pytest.mark.integration
@pytest.mark.acceptance
@pytest.mark.parametrize("held_condition", ["none", "another image name"])
def test_a_registry_role_that_reaches_other_images_is_limited_in_place(
    config: container_job.Config, held_condition: str
) -> None:
    azure, github, _ = _built(config)
    held = _registry_role(azure, config, "id-example-ci")
    wanted = held["condition"]
    if held_condition == "none":
        del held["condition"], held["conditionVersion"]
    else:
        held["condition"] = wanted.replace("'name'", "'other'")
    roles = _role_count(azure)

    plan = _planner(config, azure, github).plan_all()

    assert _changes(plan) == [
        (
            "update",
            f"role {WRITER} on image name in registry acrexamplejobs for id-example-ci (condition)",
        )
    ]
    container_job.apply(plan)
    assert _registry_role(azure, config, "id-example-ci")["condition"] == wanted
    assert _role_count(azure) == roles
    assert _planner(config, azure, github).plan_all().changes == []


@pytest.mark.integration
@pytest.mark.acceptance
def test_roles_that_cover_the_whole_registry_are_removed(config: container_job.Config) -> None:
    azure, github, _ = _built(config)
    registry = _path(config, REGISTRIES, config.shared.registry)
    _grant(azure, config, "id-example-daily", "AcrPull", registry)
    _grant(azure, config, "id-example-ci", "AcrPush", registry)
    # Another registry, whose name starts with this one's: not this script's to change.
    _grant(azure, config, "id-example-daily", "AcrPull", f"{registry}2")
    roles = _role_count(azure)

    plan = _planner(config, azure, github).plan_all()

    assert _changes(plan) == [
        ("remove", "role AcrPull on acrexamplejobs for id-example-daily"),
        ("remove", "role AcrPush on acrexamplejobs for id-example-ci"),
    ]
    container_job.apply(plan)
    assert _role_count(azure) == roles - 2
    assert _planner(config, azure, github).plan_all().changes == []


@pytest.mark.integration
@pytest.mark.acceptance
def test_jobs_can_share_an_identity(config: container_job.Config) -> None:
    shared = replace(
        _with_job(config, "job-example-weekly", identity="id-example-daily"),
        identities=config.identities[:1],
    )

    azure, github, _ = _built(shared)

    assert _names(azure, shared, IDENTITIES) == ["id-example-ci", "id-example-daily"]
    for name in ("job-example-daily", "job-example-weekly"):
        job = _resource(azure, shared, JOBS, name)
        assert list(job["identity"]["userAssignedIdentities"]) == [
            _path(shared, IDENTITIES, "id-example-daily")
        ]
    assert _planner(shared, azure, github).plan_all().changes == []


@pytest.mark.integration
@pytest.mark.acceptance
def test_a_job_added_later_starts_on_the_image_the_others_run(
    config: container_job.Config,
) -> None:
    azure, github, _ = _built(_only(config, "job-example-daily"))
    released = "acrexamplejobs.azurecr.io/app:abc123"
    daily = _resource(azure, config, JOBS, "job-example-daily")
    daily["properties"]["template"]["containers"][0]["image"] = released

    plan = _planner(config, azure, github).plan_all()

    assert ("create", f"job job-example-weekly (schedule 0 12 * * 1, image {released})") in (
        _changes(plan)
    )
    assert (
        "update",
        "repo variable CONTAINER_APP_JOBS = job-example-daily job-example-weekly",
    ) in (_changes(plan))
    container_job.apply(plan)
    weekly = _resource(azure, config, JOBS, "job-example-weekly")
    assert weekly["properties"]["template"]["containers"][0]["image"] == released
    assert _planner(config, azure, github).plan_all().changes == []


@pytest.mark.integration
@pytest.mark.acceptance
def test_a_job_dropped_from_the_config_is_removed_with_its_alert_and_identity(
    config: container_job.Config,
) -> None:
    azure, github, _ = _built(config)
    weekly_principal = _resource(azure, config, IDENTITIES, "id-example-weekly")["properties"][
        "principalId"
    ]
    changed = _only(config, "job-example-daily")

    plan = _planner(changed, azure, github).plan_all()

    assert [(c.kind, c.what) for c in plan.changes] == [
        ("update", "repo variable CONTAINER_APP_JOBS = job-example-daily"),
        ("remove", "alert alert-job-example-weekly-failed"),
        ("remove", "job job-example-weekly"),
        ("remove", "managed identity id-example-weekly, and every role it holds"),
    ]
    container_job.apply(plan)
    assert _names(azure, changed, JOBS) == ["job-example-daily"]
    assert _names(azure, changed, RULES) == ["alert-job-example-daily-failed"]
    assert _names(azure, changed, IDENTITIES) == ["id-example-ci", "id-example-daily"]
    assert not [
        k
        for k, r in azure.resources.items()
        if "/roleassignments/" in k and r["properties"]["principalId"] == weekly_principal
    ]
    assert github.vars["CONTAINER_APP_JOBS"] == "job-example-daily"
    assert _planner(changed, azure, github).plan_all().changes == []


@pytest.mark.integration
@pytest.mark.acceptance
def test_other_repos_jobs_and_untagged_ones_are_left_alone(
    config: container_job.Config,
) -> None:
    azure, github, _ = _built(config)
    azure.add(
        _path(config, JOBS, "job-other-repo"),
        {"tags": {container_job.REPOSITORY_TAG: "owner/other"}, "properties": {}},
    )
    azure.add(_path(config, JOBS, "job-made-by-hand"), {"properties": {}})
    azure.add(
        _path(config, IDENTITIES, "id-other-repo"),
        {"tags": {"Container-Job-Repository": "owner/other"}, "properties": {}},
    )

    plan = _planner(config, azure, github).plan_all()

    assert plan.changes == []


@pytest.mark.integration
@pytest.mark.parametrize(
    ("kind", "name", "message"),
    [
        (JOBS, "job-example-daily", r"Job job-example-daily belongs to the repo owner/other"),
        (IDENTITIES, "id-example-weekly", r"Managed identity id-example-weekly belongs to"),
        (RULES, "alert-job-example-daily-failed", r"Alert alert-job-example-daily-failed belongs"),
    ],
)
def test_a_name_another_repo_uses_stops_the_run(
    config: container_job.Config, kind: str, name: str, message: str
) -> None:
    azure, github = FakeAzure(), FakeGitHub()
    _vault(azure, config)
    azure.add(
        _path(config, kind, name),
        {
            "tags": {container_job.REPOSITORY_TAG: "owner/other"},
            "properties": {"principalId": "p", "clientId": "c"},
        },
    )

    with pytest.raises(container_job.AzureError, match=message):
        _planner(config, azure, github).plan_all()


@pytest.mark.integration
@pytest.mark.acceptance
def test_resources_from_before_the_tag_are_tagged_and_keep_their_roles(
    config: container_job.Config,
) -> None:
    azure, github, _ = _built(config)
    for resource in azure.resources.values():
        resource.pop("tags", None)

    plan = _planner(config, azure, github).plan_all()

    assert _changes(plan) == [
        ("update", "alert alert-job-example-daily-failed on failed job-example-daily runs"),
        ("update", "alert alert-job-example-weekly-failed on failed job-example-weekly runs"),
        ("update", "job job-example-daily (tag)"),
        ("update", "job job-example-weekly (tag)"),
        ("update", "managed identity id-example-ci (tag)"),
        ("update", "managed identity id-example-daily (tag)"),
        ("update", "managed identity id-example-weekly (tag)"),
    ]
    container_job.apply(plan)
    assert _planner(config, azure, github).plan_all().changes == []


@pytest.mark.integration
def test_a_changed_alert_email_replaces_the_receiver(config: container_job.Config) -> None:
    azure, github, _ = _built(config)
    changed = replace(config, alert=replace(config.alert, email="soc@example.com"))

    plan = _planner(changed, azure, github).plan_all()

    assert [(c.kind, c.what) for c in plan.changes] == [
        ("update", "action group ag-example-alerts: email soc@example.com")
    ]


@pytest.mark.integration
def test_a_changed_repo_variable_is_set_back(config: container_job.Config) -> None:
    azure, github, _ = _built(config)
    github.vars["ACR_NAME"] = "somethingelse"

    plan = _planner(config, azure, github).plan_all()

    assert [(c.kind, c.what) for c in plan.changes] == [
        ("update", "repo variable ACR_NAME = acrexamplejobs")
    ]


@pytest.mark.integration
def test_the_vault_rule_keeps_existing_rules(config: container_job.Config) -> None:
    azure, _, vault_path = _built(config)

    acls = azure.resources[vault_path.lower()]["properties"]["networkAcls"]
    assert acls["ipRules"] == [{"value": "203.0.113.7"}]
    assert [rule["id"].rsplit("/", 1)[-1] for rule in acls["virtualNetworkRules"]] == ["snet-cae"]


@pytest.mark.integration
def test_an_open_vault_gets_no_rule(config: container_job.Config) -> None:
    azure, github = FakeAzure(), FakeGitHub()
    _vault(azure, config, deny=False)

    plan = _planner(config, azure, github).plan_all()

    assert not any(c.what.startswith("Key Vault") for c in plan.changes)


@pytest.mark.integration
def test_an_existing_subnet_without_the_vault_endpoint_is_reported_not_changed(
    config: container_job.Config,
) -> None:
    azure, github, _ = _built(config)
    subnet = next(r for k, r in azure.resources.items() if k.endswith("/subnets/snet-cae"))
    subnet["properties"]["serviceEndpoints"] = []

    plan = _planner(config, azure, github).plan_all()

    assert plan.changes == []
    assert any("Microsoft.KeyVault service endpoint" in note for note in plan.notes)


# region client


@pytest.mark.unit
def test_an_azure_error_names_the_request() -> None:
    def refuse(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        return 403, json.dumps({"error": {"message": "not allowed"}}).encode()

    arm = container_job.Arm("token", transport=refuse)

    with pytest.raises(container_job.AzureError, match=r"PUT /x failed \(403\): not allowed"):
        arm.send("PUT", "/x", "2024-01-01", {})


@pytest.mark.unit
def test_list_all_follows_every_page() -> None:
    first = f"{container_job.ARM}/things?api-version=1"
    second = f"{container_job.ARM}/things?api-version=1&page=2"
    calls: list[str] = []

    def pages(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        calls.append(url)
        page: dict[str, Any] = {"value": [{"id": "/b"}]}
        if url == first:
            page = {"value": [{"id": "/a"}], "nextLink": second}
        return 200, json.dumps(page).encode()

    arm = container_job.Arm("token", transport=pages)

    assert [resource["id"] for resource in arm.list_all("/things", "1")] == ["/a", "/b"]
    assert calls == [first, second]


@pytest.mark.unit
def test_list_all_of_a_missing_resource_group_is_empty() -> None:
    def missing(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        return 404, json.dumps({"error": {"code": "ResourceGroupNotFound"}}).encode()

    assert container_job.Arm("token", transport=missing).list_all("/things", "1") == []


@pytest.mark.unit
def test_list_all_never_sends_the_token_outside_azure() -> None:
    calls: list[str] = []

    def elsewhere(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        calls.append(url)
        return 200, json.dumps({"value": [], "nextLink": "https://example.com/next"}).encode()

    arm = container_job.Arm("token", transport=elsewhere)

    with pytest.raises(container_job.AzureError, match=r"outside https://management\.azure\.com"):
        arm.list_all("/things", "1")
    assert calls == [f"{container_job.ARM}/things?api-version=1"]
