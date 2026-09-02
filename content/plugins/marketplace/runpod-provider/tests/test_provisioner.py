"""`backend.provisioner.RunpodComputeProvisioner`: the adapter core's
`ComputeProvisionerRegistry` dispatches to (`compute.register` hook). Proves
the translation between `src.plugin_api.compute`'s typed contract and
`RunPodProvisioningManager` - the manager itself is exercised directly in
`test_provisioning.py`, so these tests fake at the `RunPodClient` boundary
only.
"""

import asyncio

import pytest

import backend.provisioner as provisioner_module
from backend.catalog_client import DataCenter as LiveDataCenter
from backend.catalog_client import GpuAvailability, RunPodCatalogError
from backend.client import NetworkVolume, Pod, RunPodAPIError, RunPodNotFoundError
from backend.provisioner import ComputeProvisionerError, RunpodComputeProvisioner
from backend.provisioning import NETWORK_VOLUME_CREATE, NETWORK_VOLUME_NONE
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

    #: Class-level so `_account_volumes()` (a fresh instance per call) sees
    #: whatever a test seeded, without needing to inject an instance.
    network_volumes = []

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.create_network_volume_calls = []
        self.create_pod_calls = []
        self.started = []
        self.terminated = []
        FakeRunPodClient.instances.append(self)

    async def aclose(self):
        pass

    async def get_network_volume(self, volume_id):
        return NetworkVolume(id=volume_id, name="existing", size_gb=100, data_center_id="EU-NL-1")

    async def list_network_volumes(self):
        return list(FakeRunPodClient.network_volumes)

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

    async def start_pod(self, pod_id):
        self.started.append(pod_id)
        return Pod(
            id=pod_id, name="", image="", desired_status="RUNNING", public_ip=None,
            port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
        )

    async def terminate_pod(self, pod_id):
        self.terminated.append(pod_id)

    async def delete_network_volume(self, volume_id):
        return None


async def _always_ready(base_url, token):
    return True


async def _noop_report(progress):
    pass


@pytest.fixture
def resources(scratch_db, monkeypatch):
    monkeypatch.setattr("src.platform.database.database.db", scratch_db)
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
    """Every test gets an unreachable live catalog by default and never
    touches the network - `describe_fields` tests that want the live catalog
    instead monkeypatch `catalog_client.get_catalog` again inside the test
    body."""

    async def _raise(api_key, **kwargs):
        raise RunPodCatalogError("catalog unavailable in tests")

    monkeypatch.setattr(provisioner_module.catalog_client, "get_catalog", _raise)


@pytest.fixture
def provisioner(resources, repo, monkeypatch):
    FakeRunPodClient.instances = []
    FakeRunPodClient.network_volumes = []
    monkeypatch.setattr(provisioner_module, "RunPodClient", FakeRunPodClient)
    import backend.provisioning as provisioning_module
    monkeypatch.setattr(provisioning_module, "default_readiness_probe", _always_ready)

    instance = RunpodComputeProvisioner()
    instance._plugin_repository = repo
    instance._resources = resources
    return instance


async def test_describe_fields_reports_gpu_type_and_volume_size(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)
    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    assert by_key["gpu_type_id"].type == "select"
    assert by_key["gpu_type_id"].default == "NVIDIA GeForce RTX 4090"
    assert any(o.value == "NVIDIA GeForce RTX 4090" for o in by_key["gpu_type_id"].options)
    assert by_key["volume_size_gb"].type == "number"
    assert by_key["volume_size_gb"].default == 100


async def test_describe_fields_refuses_when_catalog_unavailable(provisioner):
    # `catalog_offline` (autouse) means no live catalog. A static GPU list x
    # a static data-center list with no stock data is a guessing game over
    # combinations that mostly don't exist - the form must refuse loudly
    # (naming the reason) instead of rendering guesswork.
    with pytest.raises(ComputeProvisionerError) as excinfo:
        await provisioner.describe_fields()

    message = str(excinfo.value)
    assert "unreachable" in message
    assert "catalog unavailable in tests" in message


async def test_describe_fields_data_center_is_optional_without_region_setting(provisioner, repo, monkeypatch):
    repo._store.pop((PLUGIN_ID, "region"))
    _fake_live_catalog(monkeypatch)

    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    assert by_key["data_center_id"].required is False


async def test_describe_fields_gpu_type_never_depends_on_data_center(provisioner, monkeypatch):
    # A RunPod Pod can float with no data center at all - GPU is the primary
    # field; `data_center_id` narrows from it, never the other way around.
    _fake_live_catalog(monkeypatch)
    live_fields = await provisioner.describe_fields()
    assert {f.key: f for f in live_fields}["gpu_type_id"].depends_on == []


async def test_describe_fields_data_center_depends_on_gpu_type_in_live_mode(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)

    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    assert by_key["data_center_id"].depends_on == ["gpu_type_id", "network_volume"]
    assert by_key["data_center_id"].required is False


async def test_describe_fields_error_points_at_plugin_settings(provisioner):
    # The refusal must tell the admin where to act, not just that it failed.
    with pytest.raises(ComputeProvisionerError) as excinfo:
        await provisioner.describe_fields()

    assert "plugin settings" in str(excinfo.value)


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
        LiveDataCenter(
            id="US-TX-3",
            name="Texas",
            location="United States",
            gpus=[
                GpuAvailability(
                    gpu_type_id="NVIDIA GeForce RTX 4090",
                    gpu_type_display_name="RTX 4090",
                    stock_status="Medium",
                    memory_gb=24,
                ),
            ],
        ),
    ]

    async def _get_catalog(api_key, **kwargs):
        return data_centers

    monkeypatch.setattr(provisioner_module.catalog_client, "get_catalog", _get_catalog)
    return data_centers


async def test_describe_fields_gpu_type_options_are_unioned_across_data_centers(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)

    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    options = by_key["gpu_type_id"].options
    rtx = next(o for o in options if o.value == "NVIDIA GeForce RTX 4090")
    assert "available in 2 data centers" in rtx.detail
    assert "24 GB" in rtx.detail


async def test_describe_fields_gpu_type_excludes_a_gpu_with_no_stock_anywhere(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)  # H100 is "None" stock in the only data center listing it

    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    values = {o.value for o in by_key["gpu_type_id"].options}
    assert "NVIDIA H100 80GB HBM3" not in values


async def test_describe_fields_data_center_is_empty_until_a_gpu_is_chosen(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)

    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    assert by_key["data_center_id"].options == []
    assert "gpu" in by_key["data_center_id"].help_text.lower()


async def test_describe_fields_data_center_options_scoped_to_the_chosen_gpu_and_excludes_out_of_stock(
    provisioner, monkeypatch
):
    _fake_live_catalog(monkeypatch)

    fields = await provisioner.describe_fields({"gpu_type_id": "NVIDIA H100 80GB HBM3"})

    by_key = {f.key: f for f in fields}
    # H100 is only listed (out of stock) in EU-NL-1 - no data center stocks it.
    real_options = [o for o in by_key["data_center_id"].options if o.value]
    assert real_options == []


async def test_describe_fields_data_center_options_include_an_automatic_choice(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)

    fields = await provisioner.describe_fields({"gpu_type_id": "NVIDIA GeForce RTX 4090"})

    by_key = {f.key: f for f in fields}
    options = by_key["data_center_id"].options
    assert options[0].value == ""
    assert options[0].label == "Automatic"
    values = {o.value for o in options}
    assert values == {"", "EU-NL-1", "US-TX-3"}
    real = {o.value: o.detail for o in options if o.value}
    assert real["EU-NL-1"] == "24 GB · High stock"
    assert real["US-TX-3"] == "24 GB · Medium stock"


async def test_describe_fields_network_volume_options_include_account_volumes(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)
    FakeRunPodClient.network_volumes = [
        NetworkVolume(id="vol-1", name="my-models", size_gb=200, data_center_id="EU-NL-1")
    ]

    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    options = by_key["network_volume"].options
    assert {o.value for o in options} == {"__create__", "__none__", "vol-1"}
    assert by_key["network_volume"].default == "__create__"
    account_option = next(o for o in options if o.value == "vol-1")
    assert account_option.label == "my-models"
    assert account_option.detail == "vol-1 · 200 GB · EU-NL-1"


async def test_describe_fields_network_volume_degrades_when_listing_fails(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)

    async def _raise(self):
        raise RunPodAPIError(500, "listing unavailable")

    monkeypatch.setattr(FakeRunPodClient, "list_network_volumes", _raise)

    fields = await provisioner.describe_fields()

    by_key = {f.key: f for f in fields}
    assert {o.value for o in by_key["network_volume"].options} == {"__create__", "__none__"}


async def test_describe_fields_volume_size_present_only_when_creating(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)

    default_fields = await provisioner.describe_fields()
    assert "volume_size_gb" in {f.key for f in default_fields}

    none_fields = await provisioner.describe_fields({"network_volume": NETWORK_VOLUME_NONE})
    assert "volume_size_gb" not in {f.key for f in none_fields}

    existing_fields = await provisioner.describe_fields({"network_volume": "vol-1"})
    assert "volume_size_gb" not in {f.key for f in existing_fields}


async def test_describe_fields_data_center_pinned_by_selected_existing_volume(provisioner, monkeypatch):
    _fake_live_catalog(monkeypatch)
    FakeRunPodClient.network_volumes = [
        NetworkVolume(id="vol-1", name="my-models", size_gb=200, data_center_id="EU-NL-1")
    ]

    fields = await provisioner.describe_fields(
        {"gpu_type_id": "NVIDIA GeForce RTX 4090", "network_volume": "vol-1"}
    )

    by_key = {f.key: f for f in fields}
    dc_field = by_key["data_center_id"]
    assert dc_field.required is True
    assert dc_field.default == "EU-NL-1"
    assert [o.value for o in dc_field.options] == ["EU-NL-1"]
    assert "pinned" in dc_field.help_text.lower()


async def test_provision_returns_connection_details_with_handle_as_profile_name(provisioner):
    result = await provisioner.provision(
        ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
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
        ), _noop_report
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_network_volume_calls[-1]["data_center_id"] == "EU-NL-1"
    assert client.create_pod_calls[-1]["data_center_ids"] == ["EU-NL-1"]


def _fake_multi_stock_catalog(monkeypatch, entries, gpu_type_id="NVIDIA GeForce RTX 4090"):
    """`entries` is `[(data_center_id, stock_status), ...]`, all for the same
    `gpu_type_id` - lets a test control exactly which data center should win
    an auto-pick."""
    data_centers = [
        LiveDataCenter(
            id=dc_id,
            name=dc_id,
            location=None,
            gpus=[
                GpuAvailability(
                    gpu_type_id=gpu_type_id, gpu_type_display_name="RTX 4090", stock_status=stock, memory_gb=24
                )
            ],
        )
        for dc_id, stock in entries
    ]

    async def _get_catalog(api_key, **kwargs):
        return data_centers

    monkeypatch.setattr(provisioner_module.catalog_client, "get_catalog", _get_catalog)
    return data_centers


async def test_provision_auto_picks_the_best_stocked_data_center(provisioner, monkeypatch):
    _fake_multi_stock_catalog(monkeypatch, [("CA-MTL-1", "Medium"), ("US-TX-3", "High"), ("EU-NL-1", "Low")])

    await provisioner.provision(
        ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_network_volume_calls[-1]["data_center_id"] == "US-TX-3"
    assert client.create_pod_calls[-1]["data_center_ids"] == ["US-TX-3"]


async def test_provision_auto_pick_tiebreaks_deterministically_on_equal_stock(provisioner, monkeypatch):
    _fake_multi_stock_catalog(monkeypatch, [("US-TX-3", "High"), ("CA-MTL-1", "High")])

    await provisioner.provision(
        ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_network_volume_calls[-1]["data_center_id"] == "CA-MTL-1"  # alphabetically first


async def test_provision_auto_pick_respects_an_existing_volumes_data_center(provisioner, resources, monkeypatch):
    """The volume (and the models on it) already lives in EU-NL-1 - the
    auto-pick must reuse that, even though US-TX-3 is the better-stocked
    choice for a brand-new volume."""
    resources.record("prof-1", "network_volume", "vol-existing", meta={"data_center_id": "EU-NL-1"})
    _fake_multi_stock_catalog(monkeypatch, [("US-TX-3", "High"), ("EU-NL-1", "Low")])

    await provisioner.provision(
        ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_network_volume_calls == []  # reused, not recreated
    assert client.create_pod_calls[-1]["network_volume_id"] == "vol-existing"
    assert client.create_pod_calls[-1]["data_center_ids"] == ["EU-NL-1"]


async def test_provision_explicit_data_center_mismatching_the_existing_volume_raises(
    provisioner, resources, monkeypatch
):
    resources.record("prof-1", "network_volume", "vol-existing", meta={"data_center_id": "EU-NL-1"})
    _fake_multi_stock_catalog(monkeypatch, [("US-TX-3", "High"), ("EU-NL-1", "Low")])

    with pytest.raises(ComputeProvisionerError, match="EU-NL-1"):
        await provisioner.provision(
            ProvisionRequest(
                profile_name="prof-1",
                values={"gpu_type_id": "NVIDIA GeForce RTX 4090", "data_center_id": "US-TX-3"},
            ), _noop_report
        )

    assert FakeRunPodClient.instances == []


async def test_provision_explicit_data_center_matching_the_existing_volume_succeeds(
    provisioner, resources, monkeypatch
):
    resources.record("prof-1", "network_volume", "vol-existing", meta={"data_center_id": "EU-NL-1"})
    _fake_multi_stock_catalog(monkeypatch, [("US-TX-3", "High"), ("EU-NL-1", "Low")])

    result = await provisioner.provision(
        ProvisionRequest(
            profile_name="prof-1",
            values={"gpu_type_id": "NVIDIA GeForce RTX 4090", "data_center_id": "EU-NL-1"},
        ), _noop_report
    )

    assert result.handle == "prof-1"
    client = FakeRunPodClient.instances[-1]
    assert client.create_network_volume_calls == []


async def test_provision_none_mode_skips_the_volume_entirely(provisioner, resources):
    result = await provisioner.provision(
        ProvisionRequest(
            profile_name="prof-1",
            values={"gpu_type_id": "NVIDIA GeForce RTX 4090", "network_volume": NETWORK_VOLUME_NONE},
        ), _noop_report
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_network_volume_calls == []
    assert client.create_pod_calls[-1]["network_volume_id"] is None
    assert resources.get("prof-1", "network_volume") is None
    assert result.handle == "prof-1"


async def test_provision_with_existing_volume_id_pins_dc_and_records_it(provisioner, resources, monkeypatch):
    # The catalog's best-stocked auto-pick is US-TX-3 - the volume's own DC
    # (EU-NL-1, per the fake's `get_network_volume`) must win anyway.
    _fake_multi_stock_catalog(monkeypatch, [("US-TX-3", "High"), ("EU-NL-1", "Low")])

    result = await provisioner.provision(
        ProvisionRequest(
            profile_name="prof-1",
            values={"gpu_type_id": "NVIDIA GeForce RTX 4090", "network_volume": "vol-account-1"},
        ), _noop_report
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_network_volume_calls == []
    assert client.create_pod_calls[-1]["network_volume_id"] == "vol-account-1"
    assert client.create_pod_calls[-1]["data_center_ids"] == ["EU-NL-1"]  # from the fake's get_network_volume
    recorded = resources.get("prof-1", "network_volume")
    assert recorded.runpod_id == "vol-account-1"
    assert recorded.meta["data_center_id"] == "EU-NL-1"
    assert result.handle == "prof-1"


async def test_provision_with_stale_explicit_volume_id_raises_clean_error_naming_it(provisioner, monkeypatch):
    async def _raise_not_found(self, volume_id):
        raise RunPodNotFoundError(404, f"/networkvolumes/{volume_id} not found")

    monkeypatch.setattr(FakeRunPodClient, "get_network_volume", _raise_not_found)

    with pytest.raises(ComputeProvisionerError, match="vol-gone"):
        await provisioner.provision(
            ProvisionRequest(
                profile_name="prof-1",
                values={"gpu_type_id": "NVIDIA GeForce RTX 4090", "network_volume": "vol-gone"},
            ), _noop_report
        )


async def test_provision_with_existing_volume_id_and_conflicting_data_center_raises(provisioner):
    # The fake's `get_network_volume` always reports "EU-NL-1".
    with pytest.raises(ComputeProvisionerError, match="EU-NL-1"):
        await provisioner.provision(
            ProvisionRequest(
                profile_name="prof-1",
                values={
                    "gpu_type_id": "NVIDIA GeForce RTX 4090",
                    "network_volume": "vol-account-1",
                    "data_center_id": "US-TX-3",
                },
            ), _noop_report
        )

    assert FakeRunPodClient.instances[-1].create_pod_calls == []


async def test_provision_falls_back_to_the_region_setting_when_data_center_is_omitted(provisioner):
    # `repo` fixture seeds region="US-TX-3" - a caller that never mentions
    # data_center_id at all (not the admin form, which always sends it).
    await provisioner.provision(
        ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_network_volume_calls[-1]["data_center_id"] == "US-TX-3"
    assert client.create_pod_calls[-1]["data_center_ids"] == ["US-TX-3"]


async def test_provision_without_data_center_or_region_setting_raises_clean_error_naming_it(provisioner, repo):
    repo._store.pop((PLUGIN_ID, "region"))

    with pytest.raises(ComputeProvisionerError, match="data_center_id"):
        await provisioner.provision(
            ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
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
            ), _noop_report
        )

    assert FakeRunPodClient.instances == []


async def test_provision_without_image_ref_or_setting_raises(provisioner, repo):
    repo._store.pop((PLUGIN_ID, "worker_image"))

    with pytest.raises(ComputeProvisionerError):
        await provisioner.provision(ProvisionRequest(profile_name="prof-1", values={}), _noop_report)


async def test_provision_constrains_host_cuda_to_the_worker_images_floor_by_default(provisioner):
    """With the setting untouched, every pod created must be constrained to a
    host advertising CUDA 13.0 - the worker image's torch is a cu130 build,
    and an older host driver drops it to CPU without failing the generation."""
    await provisioner.provision(
        ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_pod_calls[-1]["allowed_cuda_versions"] == ["13.0"]


async def test_provision_with_the_setting_cleared_constrains_nothing(provisioner, repo):
    """An admin running a worker image built against an older torch clears the
    setting to opt out; RunPod reads the absent field as "any CUDA version"."""
    repo._store[(PLUGIN_ID, "allowed_cuda_versions")] = ""

    await provisioner.provision(
        ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_pod_calls[-1]["allowed_cuda_versions"] == []


async def test_provision_accepts_several_allowed_cuda_versions(provisioner, repo):
    repo._store[(PLUGIN_ID, "allowed_cuda_versions")] = "13.0, 12.9"

    await provisioner.provision(
        ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
    )

    client = FakeRunPodClient.instances[-1]
    assert client.create_pod_calls[-1]["allowed_cuda_versions"] == ["13.0", "12.9"]


async def test_provision_without_api_key_raises(provisioner, repo):
    repo._store.pop((PLUGIN_ID, "api_key"))

    with pytest.raises(ComputeProvisionerError):
        await provisioner.provision(ProvisionRequest(profile_name="prof-1", values={}), _noop_report)


async def test_provision_no_capacity_error_gets_an_actionable_suffix(provisioner, monkeypatch):
    """RunPod's own pod-create error ("could not find any pods with required
    specifications") gives no hint that it means "out of stock" - the
    wrapped `ComputeProvisionerError` must say so, on top of the original
    message."""

    async def _raise(self, **kwargs):
        raise RunPodAPIError(500, "create pod: could not find any pods with required specifications")

    monkeypatch.setattr(FakeRunPodClient, "create_pod", _raise)

    with pytest.raises(ComputeProvisionerError) as excinfo:
        await provisioner.provision(
            ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
        )

    message = str(excinfo.value)
    assert "could not find any pods with required specifications" in message
    assert "out of stock" in message.lower()


async def test_provision_unrelated_error_is_not_rewritten(provisioner, monkeypatch):
    async def _raise(self, **kwargs):
        raise RunPodAPIError(401, "RunPod API key was rejected")

    monkeypatch.setattr(FakeRunPodClient, "create_pod", _raise)

    with pytest.raises(ComputeProvisionerError) as excinfo:
        await provisioner.provision(
            ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}), _noop_report
        )

    message = str(excinfo.value)
    assert "out of stock" not in message.lower()


async def test_status_reconciles_through_the_real_manager(provisioner, resources, repo):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)

    status = await provisioner.status("prof-1")

    assert status.state == "running"
    assert status.detail == "Pod pod-1 RUNNING, worker answered"


async def test_status_detail_for_exited_pod_names_it(provisioner, resources, monkeypatch):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})

    class ExitedClient(FakeRunPodClient):
        async def get_pod(self, pod_id):
            return Pod(
                id=pod_id, name="", image="", desired_status="EXITED", public_ip=None,
                port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
            )

    monkeypatch.setattr(provisioner_module, "RunPodClient", ExitedClient)

    status = await provisioner.status("prof-1")

    assert status.state == "stopped"
    assert "EXITED" in status.detail


async def test_provision_cancelled_mid_bringup_terminates_the_pod_and_reraises(provisioner, resources, monkeypatch):
    """An operator terminating the row mid-bring-up cancels the task -
    `CancelledError` can surface from any await, here from the worker
    handshake wait. The provisioner must best-effort tear down the pod it
    already created before letting the cancellation propagate."""
    import backend.provisioning as provisioning_module

    hang = asyncio.Event()

    async def hanging_probe(base_url, token):
        await hang.wait()
        return True

    monkeypatch.setattr(provisioning_module, "default_readiness_probe", hanging_probe)

    task = asyncio.create_task(
        provisioner.provision(
            ProvisionRequest(profile_name="prof-1", values={"gpu_type_id": "NVIDIA GeForce RTX 4090"}),
            _noop_report,
        )
    )
    for _ in range(1000):
        if resources.get("prof-1", "pod") is not None:
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("provision() never reached the pod-created point")

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert FakeRunPodClient.instances[-1].terminated == ["pod-1"]
    assert resources.get("prof-1", "pod") is None


async def test_start_returns_connection_details_for_the_recorded_pod(provisioner, resources, repo):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)

    result = await provisioner.start("prof-1", _noop_report)

    assert result.handle == "prof-1"
    assert result.resource_ref == "pod-1"
    assert result.base_url == "https://pod-1-8100.proxy.runpod.net"
    assert result.worker_token == "tok"
    assert result.ready is True
    # The fake's get_pod says RUNNING already - idempotent, no resume call.
    assert FakeRunPodClient.instances[-1].started == []


async def test_start_resumes_an_exited_pod(provisioner, resources, repo, monkeypatch):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)

    class ExitedUntilStartedClient(FakeRunPodClient):
        async def get_pod(self, pod_id):
            status = "RUNNING" if pod_id in self.started else "EXITED"
            return Pod(
                id=pod_id, name="", image="", desired_status=status, public_ip=None,
                port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
            )

    monkeypatch.setattr(provisioner_module, "RunPodClient", ExitedUntilStartedClient)

    result = await provisioner.start("prof-1", _noop_report)

    assert FakeRunPodClient.instances[-1].started == ["pod-1"]
    assert result.ready is True


async def test_start_without_a_recorded_pod_is_a_provisioner_error(provisioner):
    with pytest.raises(ComputeProvisionerError, match="No pod recorded"):
        await provisioner.start("prof-1", _noop_report)


async def test_start_without_api_key_raises(provisioner, repo):
    repo._store.pop((PLUGIN_ID, "api_key"))

    with pytest.raises(ComputeProvisionerError):
        await provisioner.start("prof-1", _noop_report)


async def test_stop_stops_without_terminating(provisioner, resources):
    resources.record("prof-1", "pod", "pod-1")

    await provisioner.stop("prof-1")

    assert resources.get("prof-1", "pod") is not None  # stopped, not removed


async def test_terminate_removes_the_pod_record(provisioner, resources, repo):
    resources.record("prof-1", "pod", "pod-1")
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)

    await provisioner.terminate("prof-1")

    assert resources.get("prof-1", "pod") is None
