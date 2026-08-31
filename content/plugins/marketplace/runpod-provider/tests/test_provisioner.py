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

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def aclose(self):
        pass

    async def create_network_volume(self, *, name, size_gb, data_center_id):
        return NetworkVolume(id="vol-1", name=name, size_gb=size_gb, data_center_id=data_center_id)

    async def create_pod(self, *, name, image_name, gpu_type_ids, env, ports, **kwargs):
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


@pytest.fixture
def provisioner(resources, repo, monkeypatch):
    monkeypatch.setattr(provisioner_module, "RunPodClient", FakeRunPodClient)
    import backend.provisioning as provisioning_module
    monkeypatch.setattr(provisioning_module, "default_readiness_probe", _always_ready)

    instance = RunpodComputeProvisioner()
    instance._plugin_repository = repo
    instance._resources = resources
    return instance


async def test_list_gpu_types_returns_the_static_catalog(provisioner):
    gpu_types = await provisioner.list_gpu_types()
    assert any(g.id == "NVIDIA GeForce RTX 4090" for g in gpu_types)


async def test_provision_returns_connection_details_with_handle_as_profile_name(provisioner):
    result = await provisioner.provision(ProvisionRequest(profile_name="prof-1"))

    assert result.handle == "prof-1"
    assert result.resource_ref == "pod-1"
    assert result.base_url.startswith("https://pod-1-8100.proxy.runpod.net")
    assert result.worker_token
    assert result.ready is True


async def test_provision_without_image_ref_or_setting_raises(provisioner, repo):
    repo._store.pop((PLUGIN_ID, "worker_image"))

    with pytest.raises(ComputeProvisionerError):
        await provisioner.provision(ProvisionRequest(profile_name="prof-1"))


async def test_provision_without_api_key_raises(provisioner, repo):
    repo._store.pop((PLUGIN_ID, "api_key"))

    with pytest.raises(ComputeProvisionerError):
        await provisioner.provision(ProvisionRequest(profile_name="prof-1"))


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
