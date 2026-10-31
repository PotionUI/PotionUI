from datetime import datetime, timedelta
from types import SimpleNamespace

from src.features.recipes.targets import InstallTargets, remote_gpu_profile

MB = 1024 ** 2


def _backend(backend_id, driver, name, info=None, device=None):
    async def get_system_info():
        return info

    return SimpleNamespace(
        backend_id=backend_id, name=name, config=SimpleNamespace(effective_driver=driver, device=device),
        get_system_info=get_system_info,
    )


class Registry:
    def __init__(self, backends, default=None):
        self.backends = {b.backend_id: b for b in backends}
        self.backend_config_store = SimpleNamespace(
            get_default_backend=lambda engine: SimpleNamespace(id=default) if default else None
        )

    def get_backends_for_engine(self, engine):
        return list(self.backends.values())

    def get_backend(self, backend_id):
        return self.backends.get(backend_id)


WORKER_INFO = {
    "connected": True,
    "free_disk_bytes": 3100 * MB,
    "gpus": [{"name": "RTX 4090", "total_memory_bytes": 24 * 1024 ** 3, "compute_capability": "8.9"}],
}


def test_options_label_local_and_worker_backends_and_resolve_the_default():
    registry = Registry([_backend("native", "native.local", "Local"), _backend("pod", "native.remote", "rtx-pod")], default="pod")
    targets = InstallTargets(backend_registry=registry)

    options = targets.options("native")
    assert [(o.backend_id, o.label, o.kind) for o in options] == [("native", "This computer", "local"), ("pod", "Worker rtx-pod", "remote")]
    assert targets.resolve("native")[0].backend_id == "pod"
    assert targets.resolve("native", "native")[0].kind == "local"
    assert targets.resolve("native", "ghost")[0] is None


def test_other_engines_are_external_and_no_backend_still_offers_this_computer():
    registry = Registry([_backend("comfy", "comfyui", "My ComfyUI")])
    assert InstallTargets(backend_registry=registry).options("comfyui")[0].kind == "external"
    fallback = InstallTargets().options("native")
    assert [(o.backend_id, o.label) for o in fallback] == [(None, "This computer")]


def test_remote_profile_reads_gpu_and_free_disk_from_the_worker_and_caches_it():
    calls = []
    backend = _backend("pod", "native.remote", "rtx-pod", WORKER_INFO)
    original = backend.get_system_info

    async def counted():
        calls.append(1)
        return await original()

    backend.get_system_info = counted
    targets = InstallTargets(backend_registry=Registry([backend]), clock=lambda: 100.0)
    option = targets.options("native")[0]

    profile = targets.profile(option, ["lora", "vae"])
    assert profile.gpu.generation == "ada"
    assert profile.gpu.vram_gb == 24.0
    assert profile.destination_backend_id == "pod"
    assert profile.disk_for("lora").free_bytes == 3100 * MB
    targets.profile(option, ["lora"])
    assert len(calls) == 1
    assert targets.profile(option, ["lora"], probe_remote=False).probed is True


def test_unreachable_worker_gives_an_unprobed_profile():
    targets = InstallTargets(backend_registry=Registry([_backend("pod", "native.remote", "rtx-pod", {"connected": False})]))
    profile = targets.profile(targets.options("native")[0], ["lora"])

    assert profile.probed is False
    assert profile.disks == ()


def test_local_disks_group_model_types_by_volume(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    roots = SimpleNamespace(write_dir=lambda t: SimpleNamespace(path=first if t == "lora" else second))
    targets = InstallTargets(model_roots=roots, disk_usage=lambda p: SimpleNamespace(free=777, total=1000, used=0), gpu_profile_provider=lambda *a: remote_gpu_profile({}))

    disks = targets.profile(targets.options("native")[0], ["lora", "vae", "text_encoder"]).disks
    assert len(disks) == 1
    assert set(disks[0].model_types) == {"lora", "vae", "text_encoder"}
    assert disks[0].usable_bytes == 0


def test_estimate_is_none_without_history_and_a_median_with_it():
    now = datetime(2026, 10, 31, 12, 0, 0)

    def row(size, seconds, destination=None):
        return SimpleNamespace(total_bytes=size, downloaded_bytes=size, started_at=now, completed_at=now + timedelta(seconds=seconds), destination_backend_id=destination)

    repo = SimpleNamespace(rows=[], get_all=lambda limit=None, status=None, top_level_only=False: repo.rows)
    targets = InstallTargets(download_repository=repo)
    local = targets.options("native")[0]
    assert targets.estimate(local) is None

    repo.rows = [row(100 * MB, 10), row(300 * MB, 10), row(200 * MB, 10), row(1 * MB, 10), row(500 * MB, 1, "pod")]
    estimate = targets.estimate(local)
    assert estimate["bytes_per_sec"] == 20 * MB
    assert "3 completed downloads" in estimate["basis"]
