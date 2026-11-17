import json

import httpx
import pytest

from backend import client as client_module
from backend.client import (
    DEFAULT_BASE_URL,
    NetworkVolume,
    Pod,
    RunPodAPIError,
    RunPodAuthError,
    RunPodClient,
    RunPodFeatureUnavailable,
    RunPodNotFoundError,
    RunPodPlacementError,
    RunPodRateLimitError,
    RunPodValidationError,
    normalize_base_url,
)
from backend.gpu_catalog import STATIC_GPU_CATALOG


def _client(handler, api_key="test-key", **kwargs) -> RunPodClient:
    return RunPodClient(api_key=api_key, transport=httpx.MockTransport(handler), **kwargs)


POD_PAYLOAD = {
    "id": "7h9k2m4n6p",
    "name": "pytorch-training",
    "image": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
    "args": "",
    "disk": 50,
    "ports": ["8888/http", "22/tcp"],
    "env": {"MODEL_NAME": "llama-3"},
    "registry": None,
    "status": "RUNNING",
    "actions": ["stop", "restart", "terminate"],
    "mounts": {
        "persistent": {"size": 20, "path": "/workspace"},
        "network": [{"volumeId": "agv6w2qcg7", "path": "/models"}],
    },
    "gpu": {"id": "NVIDIA GeForce RTX 4090", "count": 1, "vcpuCount": 16, "memory": 64},
    "cloud": "SECURE",
    "dataCenterId": "US-KS-2",
    "cudaVersion": "12.8",
    "ssh": {"proxy": None, "direct": None},
    "template": "9x4m2p7v",
    "cost": 0.44,
    "locked": False,
    "globalNetworking": {"enabled": False},
    "runtime": {"uptime": 3600, "ports": [{"private": 22, "public": 34446, "type": "tcp", "ip": "195.26.233.3"}]},
    "createdAt": "2026-06-01T12:00:00Z",
    "startedAt": "2026-06-01T12:02:00Z",
}

EXITED_POD = {**POD_PAYLOAD, "status": "EXITED", "actions": ["start", "terminate"], "runtime": None}

VOLUME_PAYLOAD = {
    "id": "agv6w2qcg7",
    "name": "my-dataset",
    "size": 50,
    "dataCenter": "EU-RO-1",
    "tags": [],
    "type": "STANDARD",
}

PAGINATION_END = {"nextCursor": None, "hasNextPage": False}


def _problem(status, detail, **extra):
    return httpx.Response(
        status, json={"title": "err", "status": status, "detail": detail, **extra}
    )


def test_default_base_url_is_the_v2_host():
    assert DEFAULT_BASE_URL == "https://api.runpod.io/v2"
    assert RunPodClient(api_key="k").base_url == "https://api.runpod.io/v2"


@pytest.mark.parametrize(
    "stored",
    [
        "https://rest.runpod.io/v1",
        "https://rest.runpod.io/v1/",
        "  https://rest.runpod.io/v1  ",
        "",
        None,
    ],
)
def test_a_stored_legacy_base_url_falls_back_to_the_v2_default(stored):
    assert normalize_base_url(stored) == DEFAULT_BASE_URL
    assert RunPodClient(api_key="k", base_url=stored or "").base_url == DEFAULT_BASE_URL


def test_a_custom_base_url_is_left_alone():
    assert normalize_base_url("https://proxy.example/runpod/v2") == "https://proxy.example/runpod/v2"
    assert normalize_base_url("https://proxy.example/runpod/v2/") == "https://proxy.example/runpod/v2"
    assert RunPodClient(api_key="k", base_url="https://proxy.example/v2").base_url == "https://proxy.example/v2"


async def test_legacy_base_url_requests_hit_the_v2_host():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"pods": [], "pagination": PAGINATION_END})

    client = _client(handler, base_url="https://rest.runpod.io/v1/")
    await client.list_pods()
    await client.aclose()

    assert seen[0].startswith("https://api.runpod.io/v2/pods")


async def test_every_request_carries_the_bearer_auth_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"pods": [], "pagination": PAGINATION_END})

    client = _client(handler, api_key="super-secret-key")
    await client.list_pods()
    await client.aclose()

    assert captured["auth"] == "Bearer super-secret-key"


async def test_validate_api_key_true_on_200_and_asks_for_one_pod():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"pods": [], "pagination": PAGINATION_END})

    client = _client(handler)
    assert await client.validate_api_key() is True
    await client.aclose()

    assert seen[0].url.path == "/v2/pods"
    assert seen[0].url.params["limit"] == "1"


async def test_validate_api_key_false_on_401_with_the_v2_error_body():
    client = _client(lambda request: _problem(401, "missing bearer token"))
    assert await client.validate_api_key() is False
    await client.aclose()


async def test_validate_api_key_403_is_also_false():
    client = _client(lambda request: _problem(403, "forbidden"))
    assert await client.validate_api_key() is False
    await client.aclose()


async def test_auth_error_surfaces_the_detail():
    client = _client(lambda request: _problem(401, "missing bearer token"))
    with pytest.raises(RunPodAuthError, match="missing bearer token"):
        await client.get_pod("p")
    await client.aclose()


async def test_get_pod_404_raises_not_found_with_the_detail():
    client = _client(lambda request: _problem(404, "pod not found"))
    with pytest.raises(RunPodNotFoundError, match="pod not found"):
        await client.get_pod("missing-pod")
    await client.aclose()


async def test_server_error_raises_api_error_with_status_code_and_detail():
    client = _client(lambda request: _problem(500, "boom"))
    with pytest.raises(RunPodAPIError, match="boom") as excinfo:
        await client.get_pod("pod-123")
    assert excinfo.value.status_code == 500
    await client.aclose()


async def test_non_json_error_body_falls_back_to_the_text():
    client = _client(lambda request: httpx.Response(502, text="bad gateway"))
    with pytest.raises(RunPodAPIError, match="bad gateway"):
        await client.get_pod("pod-123")
    await client.aclose()


async def test_422_is_a_validation_error_naming_the_field():
    body = {
        "title": "Unprocessable Entity",
        "status": 422,
        "detail": "Request validation failed.",
        "errors": ["$: additional properties 'volumeInGb' not allowed"],
    }
    client = _client(lambda request: httpx.Response(422, json=body))
    with pytest.raises(RunPodValidationError) as excinfo:
        await client.create_pod(name="n", image_name="img", gpu_type_ids=["g"], env={}, ports=[])
    await client.aclose()

    assert excinfo.value.status_code == 422
    assert "volumeInGb" in str(excinfo.value)
    assert "Request validation failed." in str(excinfo.value)


async def test_get_pod_parses_the_v2_pod_shape():
    client = _client(lambda request: httpx.Response(200, json=POD_PAYLOAD))
    pod = await client.get_pod("7h9k2m4n6p")
    await client.aclose()

    assert isinstance(pod, Pod)
    assert pod.id == "7h9k2m4n6p"
    assert pod.image.startswith("runpod/pytorch")
    assert pod.desired_status == "RUNNING"
    assert pod.public_ip == "195.26.233.3"
    assert pod.port_mappings == {"22": 34446}
    assert pod.ports == ["8888/http", "22/tcp"]
    assert pod.cost_per_hr == 0.44
    assert pod.network_volume_id == "agv6w2qcg7"


async def test_pod_without_runtime_or_network_mount_parses():
    data = {**POD_PAYLOAD, "runtime": None, "mounts": {"persistent": {"size": 20, "path": "/workspace"}}}
    client = _client(lambda request: httpx.Response(200, json=data))
    pod = await client.get_pod("7h9k2m4n6p")
    await client.aclose()

    assert pod.public_ip is None
    assert pod.port_mappings == {}
    assert pod.network_volume_id is None


async def test_create_pod_sends_the_v2_body_with_a_network_volume():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/pods"
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=EXITED_POD)

    client = _client(handler)
    pod = await client.create_pod(
        name="potionui-worker",
        image_name="example/worker:latest",
        gpu_type_ids=["NVIDIA GeForce RTX 4090"],
        env={"POTIONUI_WORKER_TOKEN": "tok"},
        ports=["8100/http"],
        container_disk_in_gb=20,
        volume_in_gb=20,
        network_volume_id="vol-1",
        volume_mount_path="/models",
        data_center_ids=["US-TX-3"],
        container_registry_auth_id="reg_abc",
        allowed_cuda_versions=["13.0"],
    )
    await client.aclose()

    body = captured["body"]
    assert body == {
        "name": "potionui-worker",
        "image": "example/worker:latest",
        "env": {"POTIONUI_WORKER_TOKEN": "tok"},
        "ports": ["8100/http"],
        "disk": 20,
        "cloud": "SECURE",
        "mounts": {"network": [{"volumeId": "vol-1", "path": "/models"}]},
        "dataCenterIds": ["US-TX-3"],
        "registry": "reg_abc",
        "gpu": {"id": "NVIDIA GeForce RTX 4090", "count": 1, "allowedCudaVersions": ["13.0"]},
    }
    assert isinstance(pod, Pod)
    assert pod.desired_status == "EXITED"


async def test_create_pod_uses_a_persistent_mount_without_a_network_volume():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=POD_PAYLOAD)

    client = _client(handler)
    await client.create_pod(
        name="n", image_name="img", gpu_type_ids=["g"], env={}, ports=[], volume_in_gb=20
    )
    await client.aclose()

    assert captured["body"]["mounts"] == {"persistent": {"size": 20, "path": "/workspace"}}
    for legacy in ("volumeInGb", "networkVolumeId", "gpuTypeIds", "imageName", "cloudType"):
        assert legacy not in captured["body"]


async def test_create_pod_omits_optional_fields_when_unset():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=POD_PAYLOAD)

    client = _client(handler)
    await client.create_pod(
        name="n", image_name="img", gpu_type_ids=["g"], env={}, ports=[], allowed_cuda_versions=[]
    )
    await client.aclose()

    body = captured["body"]
    assert "dataCenterIds" not in body
    assert "registry" not in body
    assert body["gpu"] == {"id": "g", "count": 1}


async def test_create_pod_never_sends_spot_or_savings_fields():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=POD_PAYLOAD)

    client = _client(handler)
    await client.create_pod(name="n", image_name="img", gpu_type_ids=["g"], env={}, ports=[])
    await client.aclose()

    assert not {"interruptible", "spot", "bidPerGpu", "savingsPlan"} & set(captured["body"])


async def test_create_pod_tries_the_next_gpu_after_a_placement_400():
    tried = []

    def handler(request: httpx.Request) -> httpx.Response:
        gpu = json.loads(request.content)["gpu"]["id"]
        tried.append(gpu)
        if gpu == "first":
            return _problem(400, "no capacity")
        return httpx.Response(201, json=POD_PAYLOAD)

    client = _client(handler)
    pod = await client.create_pod(
        name="n", image_name="img", gpu_type_ids=["first", "second"], env={}, ports=[]
    )
    await client.aclose()

    assert tried == ["first", "second"]
    assert pod.id == "7h9k2m4n6p"


async def test_create_pod_raises_a_placement_error_when_every_gpu_is_refused():
    client = _client(lambda request: _problem(400, "combination could not be placed"))
    with pytest.raises(RunPodPlacementError, match="could not be placed"):
        await client.create_pod(name="n", image_name="img", gpu_type_ids=["a", "b"], env={}, ports=[])
    await client.aclose()


async def test_create_pod_does_not_retry_other_errors():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _problem(402, "insufficient balance")

    client = _client(handler)
    with pytest.raises(RunPodAPIError, match="insufficient balance") as excinfo:
        await client.create_pod(name="n", image_name="img", gpu_type_ids=["a", "b"], env={}, ports=[])
    await client.aclose()

    assert excinfo.value.status_code == 402
    assert len(calls) == 1


async def test_list_pods_follows_every_page():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("cursor"))
        if request.url.params.get("cursor") is None:
            return httpx.Response(
                200,
                json={
                    "pods": [{**POD_PAYLOAD, "id": "a"}],
                    "pagination": {"nextCursor": "c2", "hasNextPage": True},
                },
            )
        return httpx.Response(
            200, json={"pods": [{**POD_PAYLOAD, "id": "b"}], "pagination": PAGINATION_END}
        )

    client = _client(handler)
    pods = await client.list_pods()
    await client.aclose()

    assert [pod.id for pod in pods] == ["a", "b"]
    assert seen == [None, "c2"]


async def test_list_pods_stops_on_a_repeated_cursor():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"pods": [POD_PAYLOAD], "pagination": {"nextCursor": "same", "hasNextPage": True}},
        )

    client = _client(handler)
    pods = await client.list_pods()
    await client.aclose()

    assert len(calls) == 2
    assert len(pods) == 2


async def test_list_network_volumes_follows_pages_and_parses_v2_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/network-volumes"
        if request.url.params.get("cursor") is None:
            return httpx.Response(
                200,
                json={
                    "networkVolumes": [VOLUME_PAYLOAD],
                    "pagination": {"nextCursor": "next", "hasNextPage": True},
                },
            )
        return httpx.Response(
            200,
            json={
                "networkVolumes": [{**VOLUME_PAYLOAD, "id": "second", "dataCenter": "US-TX-3"}],
                "pagination": PAGINATION_END,
            },
        )

    client = _client(handler)
    volumes = await client.list_network_volumes()
    await client.aclose()

    assert [v.id for v in volumes] == ["agv6w2qcg7", "second"]
    assert volumes[0] == NetworkVolume(id="agv6w2qcg7", name="my-dataset", size_gb=50, data_center_id="EU-RO-1")
    assert volumes[1].data_center_id == "US-TX-3"


async def test_create_network_volume_payload_and_parsing():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/network-volumes"
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=VOLUME_PAYLOAD)

    client = _client(handler)
    volume = await client.create_network_volume(name="my-dataset", size_gb=50, data_center_id="EU-RO-1")
    await client.aclose()

    assert captured["body"] == {"name": "my-dataset", "size": 50, "dataCenter": "EU-RO-1"}
    assert volume.id == "agv6w2qcg7"
    assert volume.size_gb == 50
    assert volume.data_center_id == "EU-RO-1"


async def test_get_and_delete_network_volume_paths():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json=VOLUME_PAYLOAD)

    client = _client(handler)
    await client.get_network_volume("agv6w2qcg7")
    await client.delete_network_volume("agv6w2qcg7")
    await client.aclose()

    assert seen == [
        ("GET", "/v2/network-volumes/agv6w2qcg7"),
        ("DELETE", "/v2/network-volumes/agv6w2qcg7"),
    ]


async def test_terminate_pod_issues_delete():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(204)

    client = _client(handler)
    await client.terminate_pod("pod-123")
    await client.aclose()

    assert captured == {"method": "DELETE", "path": "/v2/pods/pod-123"}


async def test_stop_pod_posts_the_stop_action():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=EXITED_POD)

    client = _client(handler)
    pod = await client.stop_pod("pod-123")
    await client.aclose()

    assert captured == {"method": "POST", "path": "/v2/pods/pod-123/action", "body": {"action": "stop"}}
    assert pod.desired_status == "EXITED"


async def test_start_pod_posts_the_start_action():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=POD_PAYLOAD)

    client = _client(handler)
    pod = await client.start_pod("pod-123")
    await client.aclose()

    assert captured == {"path": "/v2/pods/pod-123/action", "body": {"action": "start"}}
    assert pod.desired_status == "RUNNING"


async def test_action_without_a_body_reads_the_pod_back():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(200, json=EXITED_POD)

    client = _client(handler)
    pod = await client.stop_pod("pod-123")
    await client.aclose()

    assert calls == [("POST", "/v2/pods/pod-123/action"), ("GET", "/v2/pods/pod-123")]
    assert pod.desired_status == "EXITED"


async def test_stop_on_an_already_stopped_pod_is_not_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _problem(409, "action not valid for current pod status")
        return httpx.Response(200, json=EXITED_POD)

    client = _client(handler)
    pod = await client.stop_pod("pod-123")
    await client.aclose()

    assert pod.desired_status == "EXITED"


async def test_start_on_a_pod_that_is_already_starting_is_not_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _problem(409, "action not valid for current pod status")
        return httpx.Response(200, json={**POD_PAYLOAD, "status": "STARTING"})

    client = _client(handler)
    pod = await client.start_pod("pod-123")
    await client.aclose()

    assert pod.desired_status == "STARTING"


async def test_a_genuine_409_still_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _problem(409, "action not valid for current pod status")
        return httpx.Response(200, json={**POD_PAYLOAD, "status": "ERROR"})

    client = _client(handler)
    with pytest.raises(RunPodAPIError) as excinfo:
        await client.stop_pod("pod-123")
    await client.aclose()

    assert excinfo.value.status_code == 409


@pytest.fixture
def slept(monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    return waits


async def test_429_on_a_get_waits_for_retry_after_then_succeeds(slept):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "7"},
                json={"title": "Too Many Requests", "status": 429, "detail": "rate limit exceeded for the minute window"},
            )
        return httpx.Response(200, json=POD_PAYLOAD)

    client = _client(handler)
    pod = await client.get_pod("pod-123")
    await client.aclose()

    assert pod.id == "7h9k2m4n6p"
    assert slept == [7.0]
    assert len(calls) == 2


async def test_429_falls_back_to_the_ratelimit_header(slept):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429,
                headers={"RateLimit": '"minute";r=0;t=12, "hour";r=2800;t=1812, "day";r=49500;t=45012'},
                json={"title": "Too Many Requests", "status": 429, "detail": "slow down"},
            )
        return httpx.Response(204)

    client = _client(handler)
    await client.terminate_pod("pod-123")
    await client.aclose()

    assert slept == [12.0]


async def test_429_retries_are_bounded(slept):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"detail": "slow down"})

    client = _client(handler)
    with pytest.raises(RunPodRateLimitError, match="slow down"):
        await client.get_pod("pod-123")
    await client.aclose()

    assert len(calls) == client_module.MAX_RATE_LIMIT_RETRIES + 1
    assert len(slept) == client_module.MAX_RATE_LIMIT_RETRIES


async def test_429_with_a_long_wait_raises_without_sleeping(slept):
    client = _client(lambda request: httpx.Response(429, headers={"Retry-After": "3600"}, json={"detail": "day window"}))
    with pytest.raises(RunPodRateLimitError) as excinfo:
        await client.get_pod("pod-123")
    await client.aclose()

    assert slept == []
    assert excinfo.value.retry_after == 3600.0
    assert "3600" in str(excinfo.value)


async def test_429_on_a_create_is_a_clear_error_and_is_not_retried(slept):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "5"}, json={"detail": "slow down"})

    client = _client(handler)
    with pytest.raises(RunPodRateLimitError, match="rate limit"):
        await client.create_pod(name="n", image_name="img", gpu_type_ids=["g"], env={}, ports=[])
    await client.aclose()

    assert len(calls) == 1
    assert slept == []


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


async def test_real_401_body_for_a_bad_key_surfaces_its_detail():
    body = {"detail": "Subject token is invalid or expired", "status": 401, "title": "Unauthorized"}
    client = _client(lambda request: httpx.Response(401, json=body, headers={"content-type": "application/problem+json"}))
    with pytest.raises(RunPodAuthError, match="Subject token is invalid or expired"):
        await client.list_pods()
    await client.aclose()


async def test_real_422_body_names_the_offending_field():
    body = {
        "detail": "Request validation failed.",
        "errors": ["$: additional properties 'bogus' not allowed"],
        "status": 422,
        "title": "Unprocessable Entity",
    }
    client = _client(lambda request: httpx.Response(422, json=body, headers={"content-type": "application/problem+json"}))
    with pytest.raises(RunPodValidationError, match="'bogus'"):
        await client._request("POST", "/pods", json={"bogus": 1})
    await client.aclose()


async def test_real_placement_400_becomes_a_placement_error():
    detail = "There are no longer any instances available with the requested specifications. Please refresh and try again."
    client = _client(lambda request: _problem(400, detail))
    with pytest.raises(RunPodPlacementError, match="no longer any instances available"):
        await client.create_pod(name="n", image_name="img", gpu_type_ids=["g"], env={}, ports=[])
    await client.aclose()


async def test_real_balance_400_on_volume_create_surfaces_the_detail():
    client = _client(lambda request: _problem(400, "You must have at least $5 in your account to create a network volume"))
    with pytest.raises(RunPodAPIError, match="at least \\$5"):
        await client.create_network_volume(name="v", size_gb=10, data_center_id="EU-RO-1")
    await client.aclose()


REAL_VOLUME_POD = {
    "actions": ["stop", "restart", "terminate"],
    "args": "",
    "cloud": "SECURE",
    "cost": 0.69,
    "cudaVersion": "13.2",
    "dataCenterId": "EU-SE-1",
    "disk": 5,
    "env": {},
    "globalNetworking": {"enabled": False},
    "gpu": {"count": 1, "id": "NVIDIA RTX A6000", "memory": 50, "vcpuCount": 9},
    "id": "podneutral0001",
    "image": "nginx:alpine",
    "locked": False,
    "mounts": {"network": [{"path": "/models", "volumeId": "volneutral01"}]},
    "name": "potionui-apitest",
    "ports": ["80/http"],
    "registry": None,
    "runtime": None,
    "ssh": {"direct": None, "proxy": {"command": "ssh x@ssh.runpod.io", "host": "ssh.runpod.io", "port": 22, "username": "x"}},
    "status": "RUNNING",
    "tags": [],
    "template": None,
}


async def test_a_real_just_created_pod_is_running_with_no_runtime_yet():
    client = _client(lambda request: httpx.Response(201, json=REAL_VOLUME_POD))
    pod = await client.create_pod(name="n", image_name="img", gpu_type_ids=["g"], env={}, ports=["80/http"])
    await client.aclose()

    assert pod.desired_status == "RUNNING"
    assert pod.cost_per_hr == 0.69
    assert pod.network_volume_id == "volneutral01"
    assert pod.public_ip is None
    assert pod.port_mappings == {}
