"""Tests for scripts/container_job.py.

Azure is faked at the HTTP layer (:class:`FakeAzure`): PUTs store resources, GETs
read them back, so a test can apply a plan and then plan again against the result.
GitHub is faked at the client layer (:class:`FakeGitHub`).
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
            return self._reply(200, {"value": [{"id": f"/roledefs/{name}"}]})
        if key.endswith("/providers/microsoft.authorization/roleassignments"):
            principal = query["$filter"][0].split("'")[1]
            found = [
                r
                for k, r in self.resources.items()
                if "/roleassignments/" in k and r["properties"]["principalId"] == principal
            ]
            return self._reply(200, {"value": found})
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
                properties.update(
                    principalId=str(uuid.uuid4()), clientId=str(uuid.uuid4()), tenantId="t"
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
    """Run the example config against an empty subscription and apply the plan."""
    azure, github = FakeAzure(), FakeGitHub()
    vault_path = _vault(azure, config)
    container_job.apply(_planner(config, azure, github).plan_all())
    return azure, github, vault_path


def _job(azure: FakeAzure, config: container_job.Config) -> dict[str, Any]:
    path = (
        f"/subscriptions/{config.shared.subscription}/resourceGroups/"
        f"{config.shared.resource_group}/providers/Microsoft.App/jobs/{config.job.name}"
    )
    return azure.resources[path.lower()]


# region config


@pytest.mark.unit
def test_the_example_config_loads(config: container_job.Config) -> None:
    assert config.job.name == "job-example"
    assert config.job.args == ("--log-level", "DEBUG")
    assert config.key_vault is not None
    assert config.key_vault.secrets == ("example-secret-name",)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("timeout = 600\n", "", r"\[job\] is missing timeout"),
        ("timeout = 600\n", "timeout = 600\ncolour = 'blue'\n", r"unknown key\(s\) colour"),
        ("cpu = 0.25\n", "cpu = 'lots'\n", r"\[job\]\.cpu"),
        ('repository = "owner/name"', 'repository = "name"', r"owner/name"),
        ("[alert]\n", "[alarm]\n", r"is missing alert"),
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
def test_load_config_allows_leaving_out_the_key_vault(tmp_path: Path) -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    start, end = text.index("[key_vault]"), text.index("[alert]")
    path = tmp_path / "job.toml"
    path.write_text(text[:start] + text[end:], encoding="utf-8")

    assert container_job.load_config(path).key_vault is None


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
    assert first("Container Apps environment") < first("job job-example")
    assert first("managed identity id-example-job") < first("role AcrPull")
    assert first("job job-example") < first("role Container Apps Jobs Contributor on job")
    assert first("Key Vault kv-example") < first("job job-example")
    assert {c.kind for c in plan.changes} == {"create", "update"}

    container_job.apply(plan)

    job = _job(azure, config)
    container = job["properties"]["template"]["containers"][0]
    assert container["image"] == container_job.QUICKSTART_IMAGE
    assert container["args"] == ["--log-level", "DEBUG"]
    assert set(github.vars) == {
        "ACR_NAME",
        "AZURE_CLIENT_ID",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_TENANT_ID",
        "CONTAINER_APP_JOB",
        "CONTAINER_APP_RESOURCE_GROUP",
    }
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
def test_a_changed_schedule_updates_the_job_and_keeps_its_image(
    config: container_job.Config,
) -> None:
    azure, github, _ = _built(config)
    released = "acrexamplejobs.azurecr.io/app:abc123"
    _job(azure, config)["properties"]["template"]["containers"][0]["image"] = released
    changed = replace(config, job=replace(config.job, schedule="0 6 * * *"))

    plan = _planner(changed, azure, github).plan_all()

    assert [(c.kind, c.what) for c in plan.changes] == [("update", "job job-example (schedule)")]
    container_job.apply(plan)
    job = _job(azure, changed)
    assert job["properties"]["configuration"]["scheduleTriggerConfig"]["cronExpression"] == (
        "0 6 * * *"
    )
    assert job["properties"]["template"]["containers"][0]["image"] == released


@pytest.mark.integration
@pytest.mark.acceptance
def test_a_dropped_secret_loses_its_role_and_an_added_one_gains_it(
    config: container_job.Config,
) -> None:
    azure, github, _ = _built(config)
    assert config.key_vault is not None
    changed = replace(config, key_vault=replace(config.key_vault, secrets=("other-secret",)))

    plan = _planner(changed, azure, github).plan_all()

    assert sorted((c.kind, c.what) for c in plan.changes) == [
        ("create", "role Key Vault Secrets User on secret other-secret"),
        ("remove", "role Key Vault Secrets User on example-secret-name"),
    ]
    container_job.apply(plan)
    assert _planner(changed, azure, github).plan_all().changes == []


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


@pytest.mark.unit
def test_an_azure_error_names_the_request() -> None:
    def refuse(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        return 403, json.dumps({"error": {"message": "not allowed"}}).encode()

    arm = container_job.Arm("token", transport=refuse)

    with pytest.raises(container_job.AzureError, match=r"PUT /x failed \(403\): not allowed"):
        arm.send("PUT", "/x", "2024-01-01", {})
