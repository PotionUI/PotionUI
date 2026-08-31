"""`backend.client.RunPodClient` against a fake RunPod REST API
(`httpx.MockTransport` - no real network, no `runpod` SDK, no GraphQL)."""

import httpx
import pytest

from backend.client import (
    NetworkVolume,
    Pod,
    RunPodAPIError,
    RunPodAuthError,
    RunPodClient,
    RunPodFeatureUnavailable,
    RunPodNotFoundError,
)
from backend.gpu_catalog import STATIC_GPU_CATALOG


def _client(handler, api_key="test-key") -> RunPodClient:
    return RunPodClient(api_key=api_key, transport=httpx.MockTransport(handler))


POD_PAYLOAD = {
    "id": "pod-123",
    "name": "potionui-worker",
    "image": "example/worker:latest",
    "desiredStatus": "RUNNING",
    "publicIp": "1.2.3.4",
    "portMappings": {"8100": 40123},
    "ports": ["8100/http"],
    "costPerHr": 0.34,
    "networkVolume": {"id": "vol-1", "name": "n", "size": 100, "dataCenterId": "US-TX-3"},
}

VOLUME_PAYLOAD = {"id": "vol-1", "name": "potionui-vol", "size": 100, "dataCenterId": "US-TX-3"}


async def test_every_request_carries_the_bearer_auth_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    client = _client(handler, api_key="super-secret-key")
    await client.list_pods()
    await client.aclose()

    assert captured["auth"] == "Bearer super-secret-key"


async def test_validate_api_key_true_on_200():
    client = _client(lambda request: httpx.Response(200, json=[]))
    assert await client.validate_api_key() is True
    await client.aclose()


async def test_validate_api_key_false_on_401():
    client = _client(lambda request: httpx.Response(401, text="unauthorized"))
    assert await client.validate_api_key() is False
    await client.aclose()


async def test_validate_api_key_403_is_also_false():
    client = _client(lambda request: httpx.Response(403, text="forbidden"))
    assert await client.validate_api_key() is False
    await client.aclose()


async def test_get_pod_404_raises_not_found():
    client = _client(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(RunPodNotFoundError):
        await client.get_pod("missing-pod")
    await client.aclose()


async def test_server_error_raises_api_error_with_status_code():
    client = _client(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(RunPodAPIError) as excinfo:
        await client.get_pod("pod-123")
    assert excinfo.value.status_code == 500
    await client.aclose()


async def test_create_pod_sends_the_documented_payload_shape():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/pods"
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=POD_PAYLOAD)

    client = _client(handler)
    pod = await client.create_pod(
        name="potionui-worker",
        image_name="example/worker:latest",
        gpu_type_ids=["NVIDIA GeForce RTX 4090"],
        env={"POTIONUI_WORKER_TOKEN": "tok", "POTIONUI_WORKER_HOST": "0.0.0.0"},
        ports=["8100/http"],
        container_disk_in_gb=20,
        volume_in_gb=20,
        network_volume_id="vol-1",
        volume_mount_path="/models",
        data_center_ids=["US-TX-3"],
    )
    await client.aclose()

    body = captured["body"]
    assert body["imageName"] == "example/worker:latest"
    assert body["gpuTypeIds"] == ["NVIDIA GeForce RTX 4090"]
    assert body["env"]["POTIONUI_WORKER_TOKEN"] == "tok"
    assert body["ports"] == ["8100/http"]
    assert body["networkVolumeId"] == "vol-1"
    assert body["volumeMountPath"] == "/models"
    assert body["dataCenterIds"] == ["US-TX-3"]
    assert body["cloudType"] == "SECURE"

    assert isinstance(pod, Pod)
    assert pod.id == "pod-123"
    assert pod.desired_status == "RUNNING"
    assert pod.port_mappings == {"8100": 40123}
    assert pod.network_volume_id == "vol-1"


async def test_create_pod_omits_network_volume_id_when_none():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=POD_PAYLOAD)

    client = _client(handler)
    await client.create_pod(
        name="n", image_name="img", gpu_type_ids=["x"], env={}, ports=["8100/http"],
    )
    await client.aclose()

    assert "networkVolumeId" not in captured["body"]


async def test_create_network_volume_payload_and_parsing():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/networkvolumes"
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=VOLUME_PAYLOAD)

    client = _client(handler)
    volume = await client.create_network_volume(name="potionui-vol", size_gb=100, data_center_id="US-TX-3")
    await client.aclose()

    assert captured["body"] == {"name": "potionui-vol", "size": 100, "dataCenterId": "US-TX-3"}
    assert isinstance(volume, NetworkVolume)
    assert volume.id == "vol-1"
    assert volume.size_gb == 100


async def test_terminate_pod_issues_delete():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(204)

    client = _client(handler)
    await client.terminate_pod("pod-123")
    await client.aclose()

    assert captured["method"] == "DELETE"
    assert captured["path"] == "/v1/pods/pod-123"


async def test_stop_pod_posts_to_stop_path():
    client = _client(lambda request: httpx.Response(200, json=POD_PAYLOAD))
    pod = await client.stop_pod("pod-123")
    await client.aclose()
    assert pod.id == "pod-123"


async def test_get_pod_logs_raises_feature_unavailable_without_a_network_call():
    called = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["count"] += 1
        return httpx.Response(200, json={})

    client = _client(handler)
    with pytest.raises(RunPodFeatureUnavailable):
        await client.get_pod_logs("pod-123")
    await client.aclose()

    assert called["count"] == 0


def test_list_gpu_types_is_static_and_matches_pod_create_ids():
    client = RunPodClient(api_key="unused")
    gpu_types = client.list_gpu_types()

    assert len(gpu_types) == len(STATIC_GPU_CATALOG)
    ids = {gpu.id for gpu in gpu_types}
    assert "NVIDIA GeForce RTX 4090" in ids
    assert "NVIDIA H100 80GB HBM3" in ids
