"""Tests for the native engine's backend."""

import unittest
from unittest.mock import Mock, patch

from src.features.backends.backend_config import NativeBackendConfig
from src.features.backends.model_listing import BackendModel
from src.features.backends.native_backend import NativeBackend


def _backend(**config_overrides) -> NativeBackend:
    cfg = NativeBackendConfig(id="local", name="Local Generation", **config_overrides)
    generation_manager = Mock()
    generation_manager.gpu_manager = Mock()
    return NativeBackend(backend_config=cfg, generation_manager=generation_manager)


class TestNativeBackendPreparePipes(unittest.TestCase):
    """
    device/dtype/gpu_max_vram configure the native engine, so the backend injects
    them into the pipeline - the mirror of ComfyUIBackend injecting host/port.
    """

    def test_injects_device_dtype_and_vram_limit(self):
        backend = _backend(device="cuda:1", dtype="bfloat16", gpu_max_vram=24)

        pipes = backend.prepare_pipes([{"name": "generator", "config": {}}])

        self.assertEqual(
            pipes[0]["config"],
            {"device": "cuda:1", "dtype": "bfloat16", "vram_limit_gb": 24},
        )

    def test_creates_config_dict_when_absent(self):
        backend = _backend(device="cuda")

        pipes = backend.prepare_pipes([{"name": "generator", "config": None}])

        self.assertEqual(pipes[0]["config"]["device"], "cuda")

    def test_preset_value_wins_over_injection(self):
        """`setdefault`: a pipe that already sets device keeps its own value."""
        backend = _backend(device="cuda", dtype="float16")

        pipes = backend.prepare_pipes([{"name": "detailer", "config": {"device": "cpu"}}])

        self.assertEqual(pipes[0]["config"]["device"], "cpu")
        self.assertEqual(pipes[0]["config"]["dtype"], "float16")

    def test_sets_the_gpu_managers_cap(self):
        """MemoryManager/ModelLifecycleManager read the budget off GpuManager directly."""
        backend = _backend(gpu_max_vram=24)

        backend.prepare_pipes([{"name": "generator", "config": {}}])

        backend.generation_manager.gpu_manager.set_vram_cap_gb.assert_called_once_with(24)

    def test_survives_a_generation_manager_without_a_gpu_manager(self):
        backend = _backend(device="cuda")
        backend.generation_manager = Mock(spec=[])  # no gpu_manager attribute

        pipes = backend.prepare_pipes([{"name": "generator", "config": {}}])

        self.assertEqual(pipes[0]["config"]["device"], "cuda")


class TestNativeBackendListModels(unittest.IsolatedAsyncioTestCase):
    """
    `models_dir` is read lazily through a freshly-constructed SettingsManager, because
    BackendRegistry builds backends with `backend_class(backend_config=config)` and
    cannot inject one. Guards against reintroducing a module-level `settings_manager`
    import, which `src.platform.settings.settings` does not export.
    """

    async def test_scans_the_models_dir_from_settings(self):
        backend = _backend()
        settings = Mock()
        settings.get_models_dir.return_value = "/srv/weights"

        with patch("src.platform.settings.settings.SettingsManager", return_value=settings), \
             patch("src.platform.settings.repository.SettingRepository"), \
             patch(
                 "src.features.backends.native_backend.scan_native_models",
                 return_value=[],
             ) as scan:
            await backend.list_models()

        scan.assert_called_once_with("/srv/weights")

    async def test_deduplicates_the_scan_result(self):
        backend = _backend()
        dupe = BackendModel(model_type="upscalers", filename="up.pth", ref="up.pth")
        settings = Mock()
        settings.get_models_dir.return_value = "models"

        with patch("src.platform.settings.settings.SettingsManager", return_value=settings), \
             patch("src.platform.settings.repository.SettingRepository"), \
             patch(
                 "src.features.backends.native_backend.scan_native_models",
                 return_value=[dupe, dupe],
             ):
            models = await backend.list_models()

        self.assertEqual(len(models), 1)


class TestNativeEngineFields(unittest.TestCase):
    def test_device_options_come_from_the_host(self):
        fields = {f["name"]: f for f in NativeBackendConfig.engine_fields()}

        self.assertIn("cpu", fields["device"]["options"])
        self.assertEqual(fields["dtype"]["options"], ["float32", "float16", "bfloat16"])
        self.assertIsNone(fields["gpu_max_vram"]["options"])
        self.assertEqual(fields["gpu_max_vram"]["type"], "number")


def _fake_torch(cuda_available: bool, device_count: int = 1, device_name: str = "NVIDIA RTX 5090"):
    torch = Mock()
    torch.cuda.is_available.return_value = cuda_available
    torch.cuda.device_count.return_value = device_count
    torch.cuda.get_device_name.return_value = device_name
    torch.cuda.get_device_properties.return_value = Mock(total_memory=32 * 1024 ** 3)
    torch.cuda.memory_allocated.return_value = 0
    return torch


class TestNativeBackendHealthCheck(unittest.IsolatedAsyncioTestCase):
    """
    Status must reflect whether the CONFIGURED device is actually usable, not
    whether the host happens to have a GPU: a cpu-configured backend is always
    healthy, a cuda-configured one is only healthy when CUDA (and the requested
    index) is actually there. Previously this always reported "healthy".
    """

    async def test_cpu_configured_is_healthy_without_a_gpu(self):
        backend = _backend(device="cpu")
        fake_torch = _fake_torch(cuda_available=False)

        with patch.dict("sys.modules", {"torch": fake_torch}):
            health = await backend.health_check()

        self.assertEqual(health["status"], "healthy")
        self.assertFalse(health["gpu_available"])

    async def test_cpu_configured_is_healthy_even_with_a_gpu_present(self):
        """gpu_available is informational only for a cpu-configured backend."""
        backend = _backend(device="cpu")
        fake_torch = _fake_torch(cuda_available=True)

        with patch.dict("sys.modules", {"torch": fake_torch}):
            health = await backend.health_check()

        self.assertEqual(health["status"], "healthy")
        self.assertTrue(health["gpu_available"])

    async def test_cuda_configured_and_available_is_healthy(self):
        backend = _backend(device="cuda")
        fake_torch = _fake_torch(cuda_available=True, device_count=1)

        with patch.dict("sys.modules", {"torch": fake_torch}):
            health = await backend.health_check()

        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["gpu_name"], "NVIDIA RTX 5090")

    async def test_cuda_configured_but_unavailable_is_degraded(self):
        """The bug this fixes: a GPU-less box configured for cuda used to report
        "healthy" and only fail once a generation was actually attempted. Reuses
        the existing "degraded" status (already rendered as a warning badge by
        the admin UI) rather than inventing a new one."""
        backend = _backend(device="cuda")
        fake_torch = _fake_torch(cuda_available=False)

        with patch.dict("sys.modules", {"torch": fake_torch}):
            health = await backend.health_check()

        self.assertEqual(health["status"], "degraded")
        self.assertIn("no CUDA-capable", health["reason"])

    async def test_cuda_index_out_of_range_is_degraded(self):
        backend = _backend(device="cuda:1")
        fake_torch = _fake_torch(cuda_available=True, device_count=1)

        with patch.dict("sys.modules", {"torch": fake_torch}):
            health = await backend.health_check()

        self.assertEqual(health["status"], "degraded")
        self.assertIn("1 CUDA device(s)", health["reason"])

    async def test_unexpected_error_reports_error_status(self):
        backend = _backend(device="cuda")
        fake_torch = Mock()
        fake_torch.cuda.is_available.side_effect = RuntimeError("driver fault")

        with patch.dict("sys.modules", {"torch": fake_torch}):
            health = await backend.health_check()

        self.assertEqual(health["status"], "error")
        self.assertIn("driver fault", health["error"])


if __name__ == "__main__":
    unittest.main()
