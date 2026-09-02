"""`backend.provisioning.RunPodProvisioningManager` against a fake RunPod
client (no network, no `httpx.MockTransport` needed here - the manager only
calls a handful of named async methods) and a fake `PluginRepository` (the
manager's only path to encrypted-at-rest storage for the worker token)."""

import logging
import re

import pytest

from backend.client import NetworkVolume, Pod, RunPodAPIError, RunPodNotFoundError
from backend.provisioning import (
    NETWORK_VOLUME_CREATE,
    NETWORK_VOLUME_NONE,
    ProvisioningProfile,
    RunPodProvisioningManager,
)
from backend.resources import RunPodResourceManager
from src.plugin_api.compute import (
    STAGE_CREATING,
    STAGE_PREPARING,
    STAGE_READY,
    STAGE_STARTING,
    STAGE_WAITING_WORKER,
)

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
        self.started = []
        self.terminated = []
        self.deleted_volumes = []
        self.pod_to_return = None  # set by a test to force get_pod()'s answer
        self._pods_by_id = {}

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
        container_registry_auth_id=None, allowed_cuda_versions=None,
    ):
        pod_id = f"pod-{len(self.created_pods) + 1}"
        self.created_pods.append({
            "name": name, "image_name": image_name, "gpu_type_ids": gpu_type_ids,
            "env": env, "ports": ports, "network_volume_id": network_volume_id,
            "volume_mount_path": volume_mount_path,
            "container_registry_auth_id": container_registry_auth_id,
            "allowed_cuda_versions": allowed_cuda_versions,
        })
        pod = Pod(
            id=pod_id, name=name, image=image_name, desired_status="RUNNING",
            public_ip=None, port_mappings={}, ports=ports, cost_per_hr=None,
            network_volume_id=network_volume_id,
        )
        self._pods_by_id[pod_id] = pod
        return pod

    async def get_pod(self, pod_id):
        # A test that sets `pod_to_return` wants full control (reconcile
        # tests); absent that, hand back whatever `create_pod` produced for
        # this id - a real bring-up's poll loop starts from that pod.
        if self.pod_to_return is not None:
            return self.pod_to_return
        if pod_id in self._pods_by_id:
            return self._pods_by_id[pod_id]
        raise RunPodNotFoundError(404, "not found")

    async def stop_pod(self, pod_id):
        self.stopped.append(pod_id)
        return Pod(
            id=pod_id, name="", image="", desired_status="EXITED", public_ip=None,
            port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
        )

    async def start_pod(self, pod_id):
        self.started.append(pod_id)
        pod = Pod(
            id=pod_id, name="", image="", desired_status="RUNNING", public_ip=None,
            port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
        )
        self._pods_by_id[pod_id] = pod
        return pod

    async def terminate_pod(self, pod_id):
        self.terminated.append(pod_id)

    async def delete_network_volume(self, volume_id):
        self.deleted_volumes.append(volume_id)


@pytest.fixture
def resources(scratch_db, monkeypatch):
    monkeypatch.setattr("src.platform.database.database.db", scratch_db)
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


async def _noop_report(progress):
    pass


async def _no_sleep(seconds):
    pass


def _recording_report():
    """A `ProgressReporter` that records every `ProvisionProgress` it's
    called with, in order, on `.seen`."""
    seen = []

    async def report(progress):
        seen.append(progress)

    report.seen = seen
    return report


def _deduped_stages(progresses):
    stages = []
    for p in progresses:
        if not stages or stages[-1] != p.stage:
            stages.append(p.stage)
    return stages


# ---- provision: volume reuse ------------------------------------------------

async def test_provision_creates_a_new_volume_when_none_recorded(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    result = await manager.provision(_profile(), _noop_report)

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

    await manager.provision(_profile(region="US-TX-3"), _noop_report)

    assert resources.get("prof-1", "network_volume").meta["data_center_id"] == "US-TX-3"


async def test_provision_reuses_an_existing_volume_for_the_same_profile(resources, repo, client):
    resources.record("prof-1", "network_volume", "vol-existing")
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    result = await manager.provision(_profile(), _noop_report)

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
        await manager.provision(_profile(), _noop_report)

    assert len(failing_client.created_volumes) == 1
    recorded_volume_id = resources.get("prof-1", "network_volume").runpod_id

    result = await manager.provision(_profile(), _noop_report)

    assert len(failing_client.created_volumes) == 1  # not recreated on retry
    assert result.volume_id == recorded_volume_id


# ---- provision: network_volume mode ("__none__" / existing id) ------------

async def test_provision_none_mode_creates_no_volume_and_omits_it_from_the_pod(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    result = await manager.provision(_profile(network_volume=NETWORK_VOLUME_NONE), _noop_report)

    assert client.created_volumes == []
    assert client.created_pods[0]["network_volume_id"] is None
    assert client.created_pods[0]["volume_mount_path"] == "/workspace"
    assert result.volume_id is None
    assert resources.get("prof-1", "network_volume") is None


async def test_provision_existing_volume_id_is_used_directly_and_recorded(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    result = await manager.provision(_profile(network_volume="vol-account-1", region="EU-NL-1"), _noop_report)

    assert client.created_volumes == []  # not created, not looked up again - the caller already verified it
    assert result.volume_id == "vol-account-1"
    assert client.created_pods[0]["network_volume_id"] == "vol-account-1"
    assert client.created_pods[0]["volume_mount_path"] == "/models"
    recorded = resources.get("prof-1", "network_volume")
    assert recorded.runpod_id == "vol-account-1"
    assert recorded.meta["data_center_id"] == "EU-NL-1"


async def test_provision_creates_pod_with_env_and_http_port(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    await manager.provision(_profile(worker_port=9200), _noop_report)

    pod_call = client.created_pods[0]
    assert pod_call["ports"] == ["9200/http"]
    assert pod_call["env"]["POTIONUI_WORKER_HOST"] == "0.0.0.0"
    assert pod_call["env"]["POTIONUI_WORKER_PORT"] == "9200"
    assert pod_call["env"]["POTIONUI_WORKER_PROVIDER"] == "runpod"
    assert "POTIONUI_WORKER_TOKEN" in pod_call["env"]


# ---- provision: readiness gating (bite-checked) -----------------------------

async def test_provision_ready_true_when_probe_succeeds(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)
    result = await manager.provision(_profile(), _noop_report)
    assert result.ready is True


async def test_provision_ready_false_when_probe_fails(resources, repo, client):
    manager = RunPodProvisioningManager(
        client, resources, repo, readiness_probe=_never_ready, sleep=_no_sleep,
        handshake_attempts=2, handshake_interval_seconds=1,
    )
    result = await manager.provision(_profile(), _noop_report)
    assert result.ready is False


async def test_readiness_probe_receives_the_runpod_proxy_url_and_the_real_token(resources, repo, client):
    seen = {}

    async def capturing_probe(base_url, token):
        seen["base_url"] = base_url
        seen["token"] = token
        return True

    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=capturing_probe)
    result = await manager.provision(_profile(worker_port=8100), _noop_report)

    assert seen["base_url"] == f"https://{result.pod_id}-8100.proxy.runpod.net"
    assert seen["token"] == result.worker_token


# ---- worker token: high entropy, encrypted-at-rest, never logged -----------

async def test_worker_token_is_high_entropy_and_stored_encrypted(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)
    result = await manager.provision(_profile(), _noop_report)

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

    result = await manager.provision(_profile(), _noop_report)

    assert result.worker_token not in caplog.text


# ---- provision: progress reporting ------------------------------------------

async def test_provision_reports_stage_sequence_preparing_to_ready(resources, repo, client):
    """The happy-path stage sequence, deduped by consecutive repeats, is
    preparing -> creating -> starting -> waiting_worker -> ready. The probe
    fails once so a `waiting_worker` report actually happens before the
    handshake succeeds."""
    report = _recording_report()
    attempts = {"n": 0}

    async def probe(base_url, token):
        attempts["n"] += 1
        return attempts["n"] >= 2

    manager = RunPodProvisioningManager(
        client, resources, repo, readiness_probe=probe, sleep=_no_sleep, handshake_interval_seconds=1,
    )

    result = await manager.provision(_profile(), report)

    assert result.ready is True
    assert _deduped_stages(report.seen) == [
        STAGE_PREPARING, STAGE_CREATING, STAGE_STARTING, STAGE_WAITING_WORKER, STAGE_READY,
    ]
    assert report.seen[-1].stage == STAGE_READY
    assert report.seen[-1].message == "Worker is up"


async def test_provision_preparing_report_names_the_created_volume_size_and_dc(resources, repo, client):
    report = _recording_report()
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    await manager.provision(_profile(volume_size_gb=250, region="US-TX-3"), report)

    preparing = [p for p in report.seen if p.stage == STAGE_PREPARING]
    assert any("250 GB" in p.message and "US-TX-3" in p.message for p in preparing)


async def test_provision_preparing_report_names_no_persistent_volume(resources, repo, client):
    report = _recording_report()
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    await manager.provision(_profile(network_volume=NETWORK_VOLUME_NONE), report)

    preparing = [p for p in report.seen if p.stage == STAGE_PREPARING]
    assert any("No persistent volume" in p.message for p in preparing)


async def test_provision_creating_reports_pod_requested_then_created(resources, repo, client):
    report = _recording_report()
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    result = await manager.provision(_profile(), report)

    creating = [p for p in report.seen if p.stage == STAGE_CREATING]
    assert any("Requesting pod" in p.message and "NVIDIA GeForce RTX 4090" in p.message for p in creating)
    assert any(f"Pod {result.pod_id} created" == p.message for p in creating)


async def test_provision_wait_for_pod_running_polls_with_increasing_elapsed(resources, repo):
    """A pod that comes back EXITED for two polls, then RUNNING, must
    produce at least two `starting` reports naming an increasing elapsed
    time before the manager moves on."""

    class SlowStartClient(FakeRunPodClient):
        def __init__(self):
            super().__init__()
            self.get_pod_calls = 0

        async def create_pod(self, **kwargs):
            pod = await super().create_pod(**kwargs)
            exited = Pod(
                id=pod.id, name=pod.name, image=pod.image, desired_status="EXITED",
                public_ip=None, port_mappings={}, ports=pod.ports, cost_per_hr=None,
                network_volume_id=pod.network_volume_id,
            )
            self._pods_by_id[pod.id] = exited
            return exited

        async def get_pod(self, pod_id):
            self.get_pod_calls += 1
            if self.get_pod_calls >= 2:
                running = Pod(
                    id=pod_id, name="", image="", desired_status="RUNNING", public_ip=None,
                    port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
                )
                self._pods_by_id[pod_id] = running
            return self._pods_by_id[pod_id]

    client = SlowStartClient()
    report = _recording_report()
    manager = RunPodProvisioningManager(
        client, resources, repo, readiness_probe=_always_ready, sleep=_no_sleep, poll_interval_seconds=5,
    )

    result = await manager.provision(_profile(), report)

    assert result.ready is True
    starting = [p for p in report.seen if p.stage == STAGE_STARTING]
    waiting = [p for p in starting if "Waiting for pod" in p.message]
    assert len(waiting) >= 2
    elapsed = [int(re.search(r"\((\d+)s", p.message).group(1)) for p in waiting]
    assert elapsed == sorted(elapsed)
    assert elapsed[0] < elapsed[-1]
    assert starting[-1].message.endswith("is RUNNING")


async def test_provision_worker_probe_retries_then_succeeds(resources, repo, client):
    report = _recording_report()
    attempts = {"n": 0}

    async def probe(base_url, token):
        attempts["n"] += 1
        return attempts["n"] > 3  # fails 3 times, succeeds on the 4th

    manager = RunPodProvisioningManager(
        client, resources, repo, readiness_probe=probe, sleep=_no_sleep, handshake_interval_seconds=1,
    )

    result = await manager.provision(_profile(), report)

    assert result.ready is True
    waiting_worker = [p for p in report.seen if p.stage == STAGE_WAITING_WORKER]
    assert len(waiting_worker) == 3
    ready = [p for p in report.seen if p.stage == STAGE_READY]
    assert len(ready) == 1
    assert report.seen[-1].stage == STAGE_READY


async def test_provision_worker_probe_exhausted_reports_failure_and_is_not_ready(resources, repo, client):
    report = _recording_report()
    manager = RunPodProvisioningManager(
        client, resources, repo, readiness_probe=_never_ready, sleep=_no_sleep,
        handshake_attempts=3, handshake_interval_seconds=1,
    )

    result = await manager.provision(_profile(), report)

    assert result.ready is False
    assert report.seen[-1].stage != STAGE_READY
    assert report.seen[-1].stage == STAGE_WAITING_WORKER
    assert "3 attempts" in report.seen[-1].message
    waiting_worker = [p for p in report.seen if p.stage == STAGE_WAITING_WORKER]
    assert len(waiting_worker) == 4  # 3 per-attempt reports + the final exhaustion report


async def test_provision_pod_start_timeout_raises(resources, repo):
    class NeverRunningClient(FakeRunPodClient):
        async def create_pod(self, **kwargs):
            pod = await super().create_pod(**kwargs)
            exited = Pod(
                id=pod.id, name=pod.name, image=pod.image, desired_status="EXITED",
                public_ip=None, port_mappings={}, ports=pod.ports, cost_per_hr=None,
                network_volume_id=pod.network_volume_id,
            )
            self._pods_by_id[pod.id] = exited
            return exited

        async def get_pod(self, pod_id):
            return self._pods_by_id[pod_id]  # stays EXITED forever

    client = NeverRunningClient()
    manager = RunPodProvisioningManager(
        client, resources, repo, readiness_probe=_always_ready, sleep=_no_sleep,
        pod_start_timeout_seconds=10, poll_interval_seconds=5,
    )

    with pytest.raises(RunPodAPIError):
        await manager.provision(_profile(), _noop_report)


async def test_provision_pod_disappearing_mid_start_raises(resources, repo):
    class DisappearingClient(FakeRunPodClient):
        async def create_pod(self, **kwargs):
            pod = await super().create_pod(**kwargs)
            exited = Pod(
                id=pod.id, name=pod.name, image=pod.image, desired_status="EXITED",
                public_ip=None, port_mappings={}, ports=pod.ports, cost_per_hr=None,
                network_volume_id=pod.network_volume_id,
            )
            self._pods_by_id[pod.id] = exited
            return exited

        async def get_pod(self, pod_id):
            raise RunPodNotFoundError(404, f"/pods/{pod_id} not found")

    client = DisappearingClient()
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready, sleep=_no_sleep)

    with pytest.raises(RunPodAPIError):
        await manager.provision(_profile(), _noop_report)


# ---- reconcile ---------------------------------------------------------------

# ---- start ---------------------------------------------------------------------

def _pod(pod_id, status):
    return Pod(
        id=pod_id, name="", image="", desired_status=status, public_ip=None,
        port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
    )


def _record_pod(resources, repo, client, *, status="EXITED", token="tok-stored"):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    if token is not None:
        repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", token, is_secret=True)
    client._pods_by_id["pod-1"] = _pod("pod-1", status)


def _start_manager(client, resources, repo, **overrides):
    kwargs = dict(readiness_probe=_always_ready, sleep=_no_sleep)
    kwargs.update(overrides)
    return RunPodProvisioningManager(client, resources, repo, **kwargs)


async def test_start_resumes_an_exited_pod_and_reports_starting_to_ready(resources, repo, client):
    _record_pod(resources, repo, client)
    report = _recording_report()

    result = await _start_manager(client, resources, repo).start("prof-1", report)

    assert client.started == ["pod-1"]
    assert result.pod_id == "pod-1"
    assert result.ready is True
    assert result.base_url == "https://pod-1-8100.proxy.runpod.net"
    assert result.worker_token == "tok-stored"  # the stored token, never a new one
    assert _deduped_stages(report.seen) == [STAGE_STARTING, STAGE_READY]
    assert report.seen[0].message == "Resuming pod pod-1"
    assert report.seen[1].message == "Pod pod-1 is RUNNING"


async def test_start_of_an_already_running_pod_skips_the_resume_call(resources, repo, client):
    _record_pod(resources, repo, client, status="RUNNING")
    report = _recording_report()

    result = await _start_manager(client, resources, repo).start("prof-1", report)

    assert client.started == []
    assert result.ready is True
    assert result.base_url == "https://pod-1-8100.proxy.runpod.net"
    assert _deduped_stages(report.seen) == [STAGE_STARTING, STAGE_READY]
    assert report.seen[0].message == "Pod pod-1 is RUNNING"


async def test_start_polls_until_the_resumed_pod_is_running(resources, repo):
    class SlowResumeClient(FakeRunPodClient):
        def __init__(self):
            super().__init__()
            self.get_pod_calls = 0

        async def start_pod(self, pod_id):
            self.started.append(pod_id)
            return _pod(pod_id, "EXITED")

        async def get_pod(self, pod_id):
            self.get_pod_calls += 1
            return _pod(pod_id, "RUNNING" if self.get_pod_calls >= 3 else "EXITED")

    client = SlowResumeClient()
    _record_pod(resources, repo, client)
    report = _recording_report()

    result = await _start_manager(client, resources, repo, poll_interval_seconds=5).start("prof-1", report)

    assert result.ready is True
    waiting = [p for p in report.seen if p.stage == STAGE_STARTING and "Waiting for pod" in p.message]
    assert len(waiting) >= 2
    elapsed = [int(re.search(r"\((\d+)s", p.message).group(1)) for p in waiting]
    assert elapsed == sorted(elapsed)
    assert elapsed[0] < elapsed[-1]


async def test_start_pod_that_never_reaches_running_raises(resources, repo):
    class StuckClient(FakeRunPodClient):
        async def start_pod(self, pod_id):
            return _pod(pod_id, "EXITED")

        async def get_pod(self, pod_id):
            return _pod(pod_id, "EXITED")

    client = StuckClient()
    _record_pod(resources, repo, client)
    manager = _start_manager(client, resources, repo, pod_start_timeout_seconds=10, poll_interval_seconds=5)

    with pytest.raises(RunPodAPIError, match="did not reach RUNNING"):
        await manager.start("prof-1", _noop_report)


async def test_start_returns_not_ready_when_the_worker_never_answers(resources, repo, client):
    _record_pod(resources, repo, client)
    report = _recording_report()
    manager = _start_manager(client, resources, repo, readiness_probe=_never_ready, handshake_attempts=2)

    result = await manager.start("prof-1", report)

    assert result.ready is False
    assert result.base_url == "https://pod-1-8100.proxy.runpod.net"
    assert _deduped_stages(report.seen) == [STAGE_STARTING, STAGE_WAITING_WORKER]


async def test_start_returns_the_recorded_volume_id(resources, repo, client):
    _record_pod(resources, repo, client)
    resources.record("prof-1", "network_volume", "vol-9", meta={"data_center_id": "US-TX-3"})

    result = await _start_manager(client, resources, repo).start("prof-1", _noop_report)

    assert result.volume_id == "vol-9"


async def test_start_without_a_recorded_pod_raises(resources, repo, client):
    with pytest.raises(RunPodAPIError, match="No pod recorded"):
        await _start_manager(client, resources, repo).start("prof-1", _noop_report)


async def test_start_without_a_stored_worker_token_raises(resources, repo, client):
    _record_pod(resources, repo, client, token=None)

    with pytest.raises(RunPodAPIError, match="Worker token missing"):
        await _start_manager(client, resources, repo).start("prof-1", _noop_report)
    assert client.started == []


async def test_start_of_a_pod_gone_on_runpod_raises(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)

    with pytest.raises(RunPodAPIError, match="no longer exists"):
        await _start_manager(client, resources, repo).start("prof-1", _noop_report)
    assert client.started == []


async def test_start_of_a_terminated_pod_raises(resources, repo, client):
    _record_pod(resources, repo, client, status="TERMINATED")

    with pytest.raises(RunPodAPIError, match="TERMINATED"):
        await _start_manager(client, resources, repo).start("prof-1", _noop_report)
    assert client.started == []


# ---- reconcile -----------------------------------------------------------------

async def test_reconcile_missing_when_nothing_recorded(resources, repo, client):
    manager = RunPodProvisioningManager(client, resources, repo)
    outcome = await manager.reconcile("never-provisioned")
    assert outcome.state == "missing"
    assert outcome.detail == "No pod recorded for this profile"


async def test_reconcile_missing_when_runpod_no_longer_has_the_pod(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    client.pod_to_return = None  # get_pod() raises RunPodNotFoundError
    manager = RunPodProvisioningManager(client, resources, repo)

    outcome = await manager.reconcile("prof-1")

    assert outcome.state == "missing"
    assert "pod-1" in outcome.detail
    assert "no longer exists" in outcome.detail


async def test_reconcile_stopped_when_exited(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    client.pod_to_return = Pod(
        id="pod-1", name="", image="", desired_status="EXITED", public_ip=None,
        port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
    )
    manager = RunPodProvisioningManager(client, resources, repo)

    outcome = await manager.reconcile("prof-1")

    assert outcome.state == "stopped"
    assert "EXITED" in outcome.detail


async def test_reconcile_missing_when_terminated(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    client.pod_to_return = Pod(
        id="pod-1", name="", image="", desired_status="TERMINATED", public_ip=None,
        port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
    )
    manager = RunPodProvisioningManager(client, resources, repo)

    outcome = await manager.reconcile("prof-1")

    assert outcome.state == "missing"
    assert "TERMINATED" in outcome.detail


async def test_reconcile_unreachable_for_other_desired_status(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    client.pod_to_return = Pod(
        id="pod-1", name="", image="", desired_status="RESTARTING", public_ip=None,
        port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
    )
    manager = RunPodProvisioningManager(client, resources, repo)

    outcome = await manager.reconcile("prof-1")

    assert outcome.state == "unreachable"
    assert outcome.detail == "Pod pod-1 is RESTARTING"


async def test_reconcile_unreachable_when_worker_token_missing(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    client.pod_to_return = Pod(
        id="pod-1", name="", image="", desired_status="RUNNING", public_ip="1.2.3.4",
        port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
    )
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    outcome = await manager.reconcile("prof-1")

    assert outcome.state == "unreachable"
    assert "re-provision" in outcome.detail


async def test_reconcile_running_when_running_and_handshake_succeeds(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)
    client.pod_to_return = Pod(
        id="pod-1", name="", image="", desired_status="RUNNING", public_ip="1.2.3.4",
        port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
    )
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    outcome = await manager.reconcile("prof-1")

    assert outcome.state == "running"
    assert outcome.detail == "Pod pod-1 RUNNING, worker answered"


async def test_reconcile_unreachable_when_running_but_handshake_fails(resources, repo, client):
    resources.record("prof-1", "pod", "pod-1", meta={"worker_port": 8100})
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)
    client.pod_to_return = Pod(
        id="pod-1", name="", image="", desired_status="RUNNING", public_ip="1.2.3.4",
        port_mappings={}, ports=[], cost_per_hr=None, network_volume_id=None,
    )
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_never_ready)

    outcome = await manager.reconcile("prof-1")

    assert outcome.state == "unreachable"
    assert outcome.detail == "Pod pod-1 RUNNING but the worker handshake failed"


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


async def test_deprovision_terminate_of_an_already_gone_pod_still_cleans_up(resources, repo, client):
    class PodGoneClient(type(client)):
        async def terminate_pod(self, pod_id):
            raise RunPodNotFoundError(404, f"/pods/{pod_id} not found")

        async def delete_network_volume(self, volume_id):
            raise RunPodNotFoundError(404, f"/networkvolumes/{volume_id} not found")

    gone = PodGoneClient()
    resources.record("prof-1", "pod", "pod-1")
    resources.record("prof-1", "network_volume", "vol-1")
    repo.set_plugin_setting(PLUGIN_ID, "worker_token:prof-1", "tok", is_secret=True)
    manager = RunPodProvisioningManager(gone, resources, repo)

    result = await manager.deprovision("prof-1", terminate_pod=True, delete_volume=True)

    assert result.pod_terminated is True
    assert result.volume_deleted is True
    assert resources.get("prof-1", "pod") is None
    assert resources.get("prof-1", "network_volume") is None
    assert repo.get_plugin_setting(PLUGIN_ID, "worker_token:prof-1") is None


async def test_deprovision_stop_of_an_already_gone_pod_does_not_raise(resources, repo, client):
    class PodGoneClient(type(client)):
        async def stop_pod(self, pod_id):
            raise RunPodNotFoundError(404, f"/pods/{pod_id} not found")

    gone = PodGoneClient()
    resources.record("prof-1", "pod", "pod-1")
    manager = RunPodProvisioningManager(gone, resources, repo)

    result = await manager.deprovision("prof-1")

    assert result.pod_stopped is True
    assert resources.get("prof-1", "pod") is None


async def test_provision_passes_the_registry_auth_id_to_create_pod(resources, repo):
    client = FakeRunPodClient()
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    await manager.provision(_profile(container_registry_auth_id="cra-1"), _noop_report)

    assert client.created_pods[-1]["container_registry_auth_id"] == "cra-1"


async def test_provision_passes_the_allowed_cuda_versions_to_create_pod(resources, repo):
    client = FakeRunPodClient()
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    await manager.provision(_profile(allowed_cuda_versions=("13.0",)), _noop_report)

    assert client.created_pods[-1]["allowed_cuda_versions"] == ["13.0"]


async def test_provision_with_no_allowed_cuda_versions_constrains_nothing(resources, repo):
    client = FakeRunPodClient()
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)

    await manager.provision(_profile(), _noop_report)

    assert client.created_pods[-1]["allowed_cuda_versions"] == []


async def test_provision_recreates_the_volume_when_the_recorded_one_is_gone(resources, repo):
    class VolumeGoneClient(FakeRunPodClient):
        async def get_network_volume(self, volume_id):
            raise RunPodNotFoundError(404, f"/networkvolumes/{volume_id} not found")

    client = VolumeGoneClient()
    manager = RunPodProvisioningManager(client, resources, repo, readiness_probe=_always_ready)
    resources.record("prof-1", "network_volume", "vol-deleted-in-console")

    result = await manager.provision(_profile(), _noop_report)

    assert result.volume_id != "vol-deleted-in-console"
    assert resources.get("prof-1", "network_volume").runpod_id == result.volume_id
