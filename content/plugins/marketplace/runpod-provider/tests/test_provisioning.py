"""`backend.provisioning.RunPodProvisioningManager` against a fake RunPod
client (no network, no `httpx.MockTransport` needed here - the manager only
calls a handful of named async methods) and a fake `PluginRepository` (the
manager's only path to encrypted-at-rest storage for the worker token)."""

import logging

import pytest

import backend.resources as resources_module
from backend.client import NetworkVolume, Pod, RunPodAPIError, RunPodNotFoundError
from backend.provisioning import (
    NETWORK_VOLUME_CREATE,
    NETWORK_VOLUME_NONE,
    ProvisioningProfile,
    RunPodProvisioningManager,
)
from backend.resources import RunPodResourceManager

PLUGIN_ID = "runpod-provider"


class FakeSetting:
    def __init__(self, value):
        self.setting_value = value


class FakePluginRepository:
    """Stands in for `src.plugin_api.PluginRepository` - a plain dict, since
    the manager only ever calls three methods on it."""

    def __init__(self):
        self._store = {}
        self.set_calls = []

    def set_plugin_setting(self, plugin_id, key, value, is_secret=False):
        self.set_calls.append((plugin_id, key, value, is_secret))
        self._store[(plugin_id, key)] = value

    def get_plugin_setting(self, plugin_id, key):
        value = self._store.get((plugin_id, key))
        return FakeSetting(value) if value is not None else None

    def delete_plugin_setting(self, plugin_id, key):
        return self._store.pop((plugin_id, key), None) is not None


class FakeRunPodClient:
    def __init__(self):
        self.created_volumes = []
        self.created_pods = []
        self.stopped = []
        self.terminated = []
        self.deleted_volumes = []
        self.pod_to_return = None  # set by a test to control get_pod()

    async def get_network_volume(self, volume_id):
        return NetworkVolume(id=volume_id, name="existing", size_gb=100, data_center_id="US-TX-3")

    async def create_network_volume(self, *, name, size_gb, data_center_id):
        vol_id = f"vol-{len(self.created_volumes) + 1}"
        self.created_volumes.append(
            {"name": name, "size_gb": size_gb, "data_center_id": data_center_id}
        )
        return NetworkVolume(id=vol_id, name=name, size_gb=size_gb, data_center_id=data_center_id)

    async def create_pod(
        self, *, name, image_name, gpu_type_ids, env, ports,
        container_disk_in_gb=20, volume_in_gb=20, network_volume_id=None,
        volume_mount_path="/workspace", cloud_type="SECURE", data_center_ids=None,
        container_registry_auth_id=None,
    ):
        pod_id = f"pod-{len(self.created_pods) + 1}"
        self.created_pods.append({
            "name": name, "image_name": image_name, "gpu_type_ids": gpu_type_ids,
            "env": env, "ports": ports, "network_volume_id": network_volume_id,
            "volume_mount_path": volume_mount_path,
            "container_registry_auth_id": container_registry_auth_id,
        })
        return Pod(
            id=pod_id, name=name, image=image_name, desired_status="RUNNING",
            public_ip=None, port_mappings={}, ports=ports, cost_per_hr=None,
            network_volume_id=network_volume_id,
        )

    async def get_pod(self, pod_id):
        if self.pod_to_return is None:
            raise RunPodNotFoundError(404, "not found")
        return self.pod_to_return

    async def stop_pod(self, pod_id):
        self.stopped.append(pod_id)
        return Pod(
            id=pod_id, name="", image="", desired_status="EXITED", public_ip=None,
            port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
        )

    async def terminate_pod(self, pod_id):
        self.terminated.append(pod_id)

    async def delete_network_volume(self, volume_id):
        self.deleted_volumes.append(volume_id)


@pytest.fixture
def resources(scratch_db, monkeypatch):
    monkeypatch.setattr(resources_module, "db", scratch_db)
    manager = RunPodResourceManager()
    manager.create_table()
    return manager


@pytest.fixture
def repo():
    return FakePluginRepository()


@pytest.fixture
def client():
    return FakeRunPodClient()


def _profile(**overrides):
    defaults = dict(
        name="prof-1",
        gpu_type_id="NVIDIA GeForce RTX 4090",
        image_ref="example/worker:latest",
        region="US-TX-3",
        volume_size_gb=100,
        worker_port=8100,
        container_disk_gb=20,
    )
    defaults.update(overrides)
    return ProvisioningProfile(**defaults)


async def _always_ready(base_url, token):
    return True


async def _never_ready(base_url, token):
    return False


# ---- provision: volume reuse ------------------------------------------------

async def test_provision_creates_a_new_volume_when_none_recorded(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    result = await manager.provision(_profile())

    assert len(client.created_volumes) == 1
    assert client.created_volumes[0]["size_gb"] == 100
    assert client.created_volumes[0]["data_center_id"] == "US-TX-3"
    assert result.volume_id.startswith("vol-")
    assert resources.get("prof-1", "network_volume").runpod_id == result.volume_id


async def test_provision_records_the_new_volumes_data_center_in_meta(resources, repo, client):
    """The recorded data center is how a later `provision()` call for this
    profile knows where the volume (and its models) already live - see
    `provisioner.py`'s `_pinned_data_center`."""
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    await manager.provision(_profile(region="US-TX-3"))

    assert resources.get("prof-1", "network_volume").meta["data_center_id"] == "US-TX-3"


async def test_provision_reuses_an_existing_volume_for_the_same_profile(resources, repo, client):
    resources.record("prof-1", "network_volume", "vol-existing")
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    result = await manager.provision(_profile())

    assert client.created_volumes == []
    assert result.volume_id == "vol-existing"
    assert client.created_pods[0]["network_volume_id"] == "vol-existing"


async def test_provision_retry_after_a_failed_pod_create_reuses_the_volume(resources, repo, client):
    """The volume is created and recorded *before* `create_pod` runs (see
    `provisioning.py`) - if `create_pod` then fails, the volume record must
    already be in place so the next `provision()` call for the same profile
    reuses it instead of creating (and billing for) a second one."""

    class FailFirstCreatePod(FakeRunPodClient):
        def __init__(self):
            super().__init__()
            self.pod_create_attempts = 0

        async def create_pod(self, **kwargs):
            self.pod_create_attempts += 1
            if self.pod_create_attempts == 1:
                raise RunPodAPIError(500, "create pod: could not find any pods with required specifications")
            return await super().create_pod(**kwargs)

    failing_client = FailFirstCreatePod()
    manager = RunPodProvisioningManager(failing_client, resources, repo, readiness_probe=_always_ready)

    with pytest.raises(RunPodAPIError):
        await manager.provision(_profile())

    assert len(failing_client.created_volumes) == 1
    recorded_volume_id = resources.get("prof-1", "network_volume").runpod_id

    result = await manager.provision(_profile())

    assert len(failing_client.created_volumes) == 1  # not recreated on retry
    assert result.volume_id == recorded_volume_id


# ---- provision: network_volume mode ("__none__" / existing id) ------------

async def test_provision_none_mode_creates_no_volume_and_omits_it_from_the_pod(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    result = await manager.provision(_profile(network_volume=NETWORK_VOLUME_NONE))

    assert client.created_volumes == []
    assert client.created_pods[0]["network_volume_id"] is None
    assert client.created_pods[0]["volume_mount_path"] == "/workspace"
    assert result.volume_id is None
    assert resources.get("prof-1", "network_volume") is None


async def test_provision_existing_volume_id_is_used_directly_and_recorded(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    result = await manager.provision(_profile(network_volume="vol-account-1", region="EU-NL-1"))

    assert client.created_volumes == []  # not created, not looked up again - the caller already verified it
    assert result.volume_id == "vol-account-1"
    assert client.created_pods[0]["network_volume_id"] == "vol-account-1"
    assert client.created_pods[0]["volume_mount_path"] == "/models"
    recorded = resources.get("prof-1", "network_volume")
    assert recorded.runpod_id == "vol-account-1"
    assert recorded.meta["data_center_id"] == "EU-NL-1"


async def test_provision_creates_pod_with_env_and_http_port(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    await manager.provision(_profile(worker_port=9200))

    pod_call = client.created_pods[0]
    assert pod_call["ports"] == ["9200/http"]
    assert pod_call["env"]["POTIONUI_WORKER_HOST"] == "0.0.0.0"
    assert pod_call["env"]["POTIONUI_WORKER_PORT"] == "9200"
    assert pod_call["env"]["POTIONUI_WORKER_PROVIDER"] == "runpod"
    assert "POTIONUI_WORKER_TOKEN" in pod_call["env"]


# ---- provision: readiness gating (bite-checked) -----------------------------

async def test_provision_ready_true_when_probe_succeeds(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)
    result = await manager.provision(_profile())
    assert result.ready is True


async def test_provision_ready_false_when_probe_fails(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_never_ready)
    result = await manager.provision(_profile())
    assert result.ready is False


async def test_readiness_probe_receives_the_runpod_proxy_url_and_the_real_token(resources, repo, client):
    seen = {}

    async def capturing_probe(base_url, token):
        seen["base_url"] = base_url
        seen["token"] = token
        return True

    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=capturing_probe)
    result = await manager.provision(_profile(worker_port=8100))

    assert seen["base_url"] == f"https://{result.pod_id}-8100.proxy.runpod.net"
    assert seen["token"] == result.worker_token


# ---- worker token: high entropy, encrypted-at-rest, never logged -----------

async def test_worker_token_is_high_entropy_and_stored_encrypted(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)
    result = await manager.provision(_profile())

    assert len(result.worker_token) >= 32
    assert len(set(repo.set_calls[-1][2])) > 10  # not a constant/degenerate string

    plugin_id, key, value, is_secret = repo.set_calls[-1]
    assert plugin_id == PLUGIN_ID
    assert key == "worker_token:prof-1"
    assert value == result.worker_token
    assert is_secret is True


async def test_worker_token_never_appears_in_logs(resources, repo, client, caplog):
    caplog.set_level(logging.DEBUG)
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    result = await manager.provision(_profile())

    assert result.worker_token not in caplog.text


# ---- reconcile ---------------------------------------------------------------

async def test_reconcile_missing_when_nothing_recorded(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo)
    assert await manager.reconcile("never-provisioned") == "missing"


async def test_reconcile_missing_when_runpod_no_longer_has_the_pod(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    client.pod_to_return = None  # get_pod() raises RunPodNotFoundError
    manager = RunPodProvisioningManager(client, resources, repo)

    assert await manager.reconcile("prof-1") == "missing"


async def test_reconcile_stopped_when_exited(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    client.pod_to_return = Pod(
        id="pod-1", name="", image="", desired_status="EXITED", public_ip=None,
        port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
    )
    manager = RunPodProvisioningManager(client, resources, repo)

    assert await manager.reconcile("prof-1") == "stopped"


async def test_reconcile_running_when_running_and_handshake_succeeds(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)
    client.pod_to_return = Pod(
        id="pod-1", name="", image="", desired_status="RUNNING", public_ip="1.2.3.4",
        port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
    )
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    assert await manager.reconcile("prof-1") == "running"


async def test_reconcile_unreachable_when_running_but_handshake_fails(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)
    client.pod_to_return = Pod(
        id="pod-1", name="", image="", desired_status="RUNNING", public_ip="1.2.3.4",
        port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
    )
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_never_ready)

    assert await manager.reconcile("prof-1") == "unreachable"


# ---- deprovision: keep-volume default (bite-checked) ------------------------

async def test_deprovision_default_stops_the_pod_and_never_touches_the_volume(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1")
    resources.record("prof-1", "network_volume", "vol-1")
    manager = RunPodProvisioningManager(client, resources, repo)

    result = await manager.deprovision("prof-1")

    assert result.pod_stopped is True
    assert result.pod_terminated is False
    assert result.volume_deleted is False
    assert client.stopped == ["pod-1"]
    assert client.terminated == []
    assert client.deleted_volumes == []
    assert resources.get("prof-1", "network_volume") is not None


async def test_deprovision_terminate_without_delete_volume_still_keeps_the_volume(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1")
    resources.record("prof-1", "network_volume", "vol-1")
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)
    manager = RunPodProvisioningManager(client, resources, repo)

    result = await manager.deprovision("prof-1", terminate_pod=True)

    assert result.pod_terminated is True
    assert result.volume_deleted is False
    assert client.deleted_volumes == []
    assert resources.get("prof-1", "network_volume") is not None
    assert resources.get("prof-1", "pod") is None
    assert repo.get_plugin_setting(PLUGIN_ID, "worker_token:prof-1") is None


async def test_deprovision_terminate_and_delete_volume_removes_both(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1")
    resources.record("prof-1", "network_volume", "vol-1")
    manager = RunPodProvisioningManager(client, resources, repo)

    result = await manager.deprovision("prof-1", terminate_pod=True, delete_volume=True)

    assert result.pod_terminated is True
    assert result.volume_deleted is True
    assert client.deleted_volumes == ["vol-1"]
    assert resources.get("prof-1", "network_volume") is None
    assert resources.get("prof-1", "pod") is None


async def test_provision_passes_the_registry_auth_id_to_create_pod(resources, repo):
    client = FakeRunPodClient()
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    await manager.provision(_profile(container_registry_auth_id="cra-1"))

    assert client.created_pods[-1]["container_registry_auth_id"] == "cra-1"


async def test_provision_recreates_the_volume_when_the_recorded_one_is_gone(resources, repo):
    class VolumeGoneClient(FakeRunPodClient):
        async def get_network_volume(self, volume_id):
            raise RunPodNotFoundError(404, f"/networkvolumes/{volume_id} not found")

    client = VolumeGoneClient()
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)
    resources.record("prof-1", "network_volume", "vol-deleted-in-console")

    result = await manager.provision(_profile())

    assert result.volume_id != "vol-deleted-in-console"
    assert resources.get("prof-1", "network_volume").runpod_id == result.volume_id
