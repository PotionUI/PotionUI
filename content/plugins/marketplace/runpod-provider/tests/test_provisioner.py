"""`backend.provisioner.RunpodComputeProvisioner`: the adapter core's
`ComputeProvisionerRegistry` dispatches to (`compute.register` hook). Proves
the translation between `src.plugin_api.compute`'s typed contract and
`RunPodProvisioningManager` - the manager itself is exercised directly in
`test_provisioning.py`, so these tests fake at the `RunPodClient` boundary
only, the same way `test_api.py` used to for the now-removed bespoke routes.
"""

import pytest

import backend.provisioner as provisioner_module
import backend.resources as resources_module
from backend.catalog_client import DataCenter as LiveDataCenter
from backend.catalog_client import GpuAvailability, RunPodCatalogError
from backend.client import NetworkVolume, Pod
from backend.provisioner import RunpodComputeProvisioner
from backend.resources import RunPodResourceManager
from src.plugin_api.compute import ComputeProvisionerError, ProvisionRequest

PLUGIN_ID = "runpod-provider"


class FakeSetting:
    def __init__(self, value):
        self.setting_value = value


class FakePluginRepository:
    def __init__(self, initial=None):
        self._store = dict(initial or {})

    def set_plugin_setting(self, plugin_id, key, value, is_secret=False):
        self._store[(plugin_id, key)] = value

    def get_plugin_setting(self, plugin_id, key):
        value = self._store.get((plugin_id, key))
        return FakeSetting(value) if value is not None else None

    def delete_plugin_setting(self, plugin_id, key):
        return self._store.pop((plugin_id, key), None) is not None


class FakeRunPodClient:
    """Swapped in for `backend.provisioner.RunPodClient` - constructed the
    same way (`RunPodClient(api_key=...)`), never touches the network."""

    #: Every instance constructed, in order - `provision()` builds its own
    #: `RunPodClient(api_key=...)` internally, so a test recovers the instance
    #: it actually used from here rather than injecting one.
    instances = []

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.create_network_volume_calls = []
        self.create_pod_calls = []
        FakeRunPodClient.instances.append(self)

    async def aclose(self):
        pass

    async def create_network_volume(self, *, name, size_gb, data_center_id):
        self.create_network_volume_calls.append(
            {"name": name, "size_gb": size_gb, "data_center_id": data_center_id}
        )
        return NetworkVolume(id="vol-1", name=name, size_gb=size_gb, data_center_id=data_center_id)

    async def create_pod(self, *, name, image_name, gpu_type_ids, env, ports, **kwargs):
        self.create_pod_calls.append({"name": name, "image_name": image_name, **kwargs})
        return Pod(
            id="pod-1", name=name, image=image_name, desired_status="RUNNING",
            public_ip=None, port_mappings={}, ports=ports, cost_per_hr=None,
            network_volume_id=kwargs.get("network_volume_id"),
        )

    async def get_pod(self, pod_id):
        return Pod(
            id=pod_id, name="", image="", desired_status="RUNNING", public_ip="1.2.3.4",
            port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
        )

    async def stop_pod(self, pod_id):
        return Pod(
            id=pod_id, name="", image="", desired_status="EXITED", public_ip=None,
            port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
        )

    async def terminate_pod(self, pod_id):
        return None

    async def delete_network_volume(self, volume_id):
        return None


async def _always_ready(base_url, token):
    return True


@pytest.fixture
def resources(scratch_db, monkeypatch):
    monkeypatch.setattr(resources_module, "db", scratch_db)
    manager = RunPodResourceManager()
    manager.create_table()
    return manager


@pytest.fixture
def repo():
    return FakePluginRepository(initial={
        (PLUGIN_ID, "api_key"): "configured-key",
        (PLUGIN_ID, "gpu_type_id"): "NVIDIA GeForce RTX 4090",
        (PLUGIN_ID, "region"): "US-TX-3",
        (PLUGIN_ID, "volume_size_gb"): "100",
        (PLUGIN_ID, "worker_image"): "example/worker:latest",
    })


@pytest.fixture(autouse=True)
def catalog_offline(monkeypatch):
    """Every test gets the pre-live-catalog behavior (static fallback) by
    default and never touches the network - `describe_fields` tests that
    want the live catalog instead monkeypatch `catalog_client.get_catalog`
    again inside the test body."""

    async def _raise(api_key, **kwargs):
        raise RunPodCatalogError("catalog unavailable in tests")

    monkeypatch.setattr(provisioner_module.catalog_client, "get_catalog", _raise)


@pytest.fixture
def provisioner(resources, repo, monkeypatch):
    FakeRunPodClient.instances = []
    monkeypatch.setattr(provisioner_module, "RunPodClient", FakeRunPodClient)
    import backend.provisioning as provisioning_module
    monkeypatch.setattr(provisioning_module, "default_readiness_probe", _always_ready)

    instance = RunpodComputeProvisioner()
    instance._plugin_repository = repo
    instance._resources = resources
    return instance


async def test_describe_fields_reports_gpu_type_and_volume_size(provisioner):
    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    assert by_key["gpu_type_id"].type == "select"
    assert by_key["gpu_type_id"].default == "NVIDIA GeForce RTX 4090"
    assert any(o.value == "NVIDIA GeForce RTX 4090" for o in by_key["gpu_type_id"].options)
    assert by_key["volume_size_gb"].type == "number"
    assert by_key["volume_size_gb"].default == 100


async def test_describe_fields_reports_data_center_as_a_required_select(provisioner):
    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    field = by_key["data_center_id"]
    assert field.type == "select"
    assert field.required is True
    assert field.default == "US-TX-3"  # from the `region` plugin setting
    assert any(o.value == "US-TX-3" and o.detail == "US" for o in field.options)
    assert len(field.options) > 1


async def test_describe_fields_data_center_has_no_default_when_region_setting_is_unset(provisioner, repo):
    repo._store.pop((PLUGIN_ID, "region"))

    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    assert by_key["data_center_id"].default is None


async def test_describe_fields_gpu_type_declares_it_depends_on_data_center(provisioner):
    # True both on the static-catalog fallback (exercised by every other test
    # via the autouse `catalog_offline` fixture) and on the live-catalog path.
    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    assert by_key["gpu_type_id"].depends_on == ["data_center_id"]


async def test_describe_fields_falls_back_to_static_catalogs_when_graphql_fails(provisioner):
    # `catalog_offline` (autouse) already makes `get_catalog` raise - this
    # test pins down the fallback's user-visible shape rather than relying
    # on the other tests' incidental assertions.
    fields = await provisioner.describe_fields({"data_center_id": "US-TX-3"})

    by_key = {f.key: f for f in fields}
    assert "static catalog" in by_key["data_center_id"].help_text
    assert "static catalog" in by_key["gpu_type_id"].help_text
    # The static GPU catalog isn't scoped by data center, so it stays the
    # full unfiltered list even with a data center chosen.
    assert len(by_key["gpu_type_id"].options) > 1


def _fake_live_catalog(monkeypatch):
    data_centers = [
        LiveDataCenter(
            id="EU-NL-1",
            name="Amsterdam",
            location="Netherlands",
            gpus=[
                GpuAvailability(
                    gpu_type_id="NVIDIA GeForce RTX 4090",
                    gpu_type_display_name="RTX 4090",
                    stock_status="High",
                    memory_gb=24,
                ),
                GpuAvailability(
                    gpu_type_id="NVIDIA H100 80GB HBM3",
                    gpu_type_display_name="H100 80GB",
                    stock_status="None",
                    memory_gb=80,
                ),
            ],
        ),
        LiveDataCenter(id="US-TX-3", name="Texas", location="United States", gpus=[]),
    ]

    async def _get_catalog(api_key, **kwargs):
        return data_centers

    monkeypatch.setattr(provisioner_module.catalog_client, "get_catalog", _get_catalog)
    return data_centers


async def test_describe_fields_data_center_options_come_from_the_live_catalog(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)

    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    values = {o.value for o in by_key["data_center_id"].options}
    assert values == {"EU-NL-1", "US-TX-3"}
    assert any(o.value == "EU-NL-1" and o.detail == "Netherlands" for o in by_key["data_center_id"].options)


async def test_describe_fields_gpu_type_is_empty_until_a_data_center_is_chosen(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)

    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    assert by_key["gpu_type_id"].options == []
    assert "data center" in by_key["gpu_type_id"].help_text.lower()


async def test_describe_fields_gpu_type_scopes_to_the_chosen_data_center_and_excludes_out_of_stock(
    provisioner, monkeypatch
):
    _fake_live_catalog(monkeypatch)

    fields = await provisioner.describe_fields({"data_center_id": "EU-NL-1"})

    by_key = {f.key: f for f in fields}
    options = by_key["gpu_type_id"].options
    assert [o.value for o in options] == ["NVIDIA GeForce RTX 4090"]
    assert options[0].detail == "24 GB · High stock"


async def test_describe_fields_gpu_type_is_empty_for_a_data_center_with_no_stock(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)

    fields = await provisioner.describe_fields({"data_center_id": "US-TX-3"})

    by_key = {f.key: f for f in fields}
    assert by_key["gpu_type_id"].options == []


async def test_provision_returns_connection_details_with_handle_as_profile_name(provisioner):
    result = await provisioner.provision(
        ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"})
    )

    assert result.handle == "prof-1"
    assert result.resource_ref == "pod-1"
    assert result.base_url.startswith("https://pod-1-8100.proxy.runpod.net")
    assert result.worker_token
    assert result.ready is True


async def test_provision_sends_the_chosen_data_center_to_both_volume_and_pod(provisioner):
    await provisioner.provision(
        ProvisionRequest(
            profile_name="prof-1",
            values={"gpu_type_id": "NVIDIA GeForce RTX 4090", "data_center_id": "EU-NL-1"},
        )
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_network_volume_calls[-1]["data_center_id"] == "EU-NL-1"
    assert client.create_pod_calls[-1]["data_center_ids"] == ["EU-NL-1"]


async def test_provision_falls_back_to_the_region_setting_when_data_center_is_omitted(provisioner):
    # `repo` fixture seeds region="US-TX-3" - a caller that never mentions
    # data_center_id at all (not the admin form, which always sends it).
    await provisioner.provision(
        ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"})
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_network_volume_calls[-1]["data_center_id"] == "US-TX-3"
    assert client.create_pod_calls[-1]["data_center_ids"] == ["US-TX-3"]


async def test_provision_without_data_center_or_region_setting_raises_clean_error_naming_it(provisioner, repo):
    repo._store.pop((PLUGIN_ID, "region"))

    with pytest.raises(ComputeProvisionerError, match="data_center_id"):
        await provisioner.provision(
            ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"})
        )

    # Never reaches RunPod at all - not a call made with an empty/invalid value.
    assert FakeRunPodClient.instances == []


async def test_provision_rejects_an_empty_string_data_center_with_no_region_fallback(provisioner, repo):
    """Core's own required-field validation only checks a value isn't `None` -
    an empty string satisfies "present". This is the provisioner's own
    belt-and-braces check against exactly the bug that shipped: `values` with
    `data_center_id: ""` and no `region` setting used to reach RunPod as
    `dataCenterId: ""`, which RunPod rejects as "not provided"."""
    repo._store.pop((PLUGIN_ID, "region"))

    with pytest.raises(ComputeProvisionerError, match="data_center_id"):
        await provisioner.provision(
            ProvisionRequest(
                profile_name="prof-1",
                values={"gpu_type_id": "NVIDIA GeForce RTX 4090", "data_center_id": ""},
            )
        )

    assert FakeRunPodClient.instances == []


async def test_provision_without_image_ref_or_setting_raises(provisioner, repo):
    repo._store.pop((PLUGIN_ID, "worker_image"))

    with pytest.raises(ComputeProvisionerError):
        await provisioner.provision(ProvisionRequest(profile_name="prof-1", values={}))


async def test_provision_without_api_key_raises(provisioner, repo):
    repo._store.pop((PLUGIN_ID, "api_key"))

    with pytest.raises(ComputeProvisionerError):
        await provisioner.provision(ProvisionRequest(profile_name="prof-1", values={}))


async def test_status_reconciles_through_the_real_manager(provisioner, resources, repo):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)

    status = await provisioner.status("prof-1")

    assert status.state == "running"


async def test_stop_stops_without_terminating(provisioner, resources):
    resources.record("prof-1", "pod", "pod-1")

    await provisioner.stop("prof-1")

    assert resources.get("prof-1", "pod") is not None  # stopped, not removed


async def test_terminate_removes_the_pod_record(provisioner, resources, repo):
    resources.record("prof-1", "pod", "pod-1")
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)

    await provisioner.terminate("prof-1")

    assert resources.get("prof-1", "pod") is None
