"""Tests for hardware-derived native backend defaults.

`NativeBackendConfig` used to hardcode device="cuda", dtype="float16",
gpu_max_vram=8 regardless of the host. These tests fake torch both ways (GPU
present / absent) and assert the derived values, without touching a real GPU.
"""

import unittest
from unittest.mock import Mock, patch

from src.features.backends.native_hardware import (
    NativeHardwareDefaults,
    detect_native_hardware_defaults,
)


def _fake_torch(cuda_available: bool, major: int = 8, minor: int = 6, device_name: str = "NVIDIA RTX 5090"):
    torch = Mock()
    torch.cuda.is_available.return_value = cuda_available
    torch.cuda.get_device_name.return_value = device_name
    torch.cuda.get_device_capability.return_value = (major, minor)
    return torch


class TestDetectNativeHardwareDefaults(unittest.TestCase):
    def setUp(self):
        detect_native_hardware_defaults.cache_clear()

    def tearDown(self):
        detect_native_hardware_defaults.cache_clear()

    def test_no_cuda_defaults_to_cpu_float32(self):
        fake_torch = _fake_torch(cuda_available=False)

        with patch.dict("sys.modules", {"torch": fake_torch}):
            result = detect_native_hardware_defaults()

        self.assertEqual(result, NativeHardwareDefaults(device="cpu", dtype="float32", gpu_max_vram=0))

    def test_ampere_or_newer_prefers_bfloat16(self):
        fake_torch = _fake_torch(cuda_available=True, major=8, minor=6)

        with patch.dict("sys.modules", {"torch": fake_torch}), \
             patch(
                 "src.platform.runtime.native.memory.residency.total_vram_gb",
                 return_value=32.0,
             ), \
             patch(
                 "src.platform.runtime.native.memory.residency.minimum_inference_memory_gb",
                 return_value=1.0,
             ):
            result = detect_native_hardware_defaults()

        self.assertEqual(result.device, "cuda")
        self.assertEqual(result.dtype, "bfloat16")
        self.assertEqual(result.gpu_max_vram, 31)

    def test_pre_ampere_falls_back_to_float16(self):
        fake_torch = _fake_torch(cuda_available=True, major=7, minor=5)  # Turing

        with patch.dict("sys.modules", {"torch": fake_torch}), \
             patch(
                 "src.platform.runtime.native.memory.residency.total_vram_gb",
                 return_value=8.0,
             ), \
             patch(
                 "src.platform.runtime.native.memory.residency.minimum_inference_memory_gb",
                 return_value=1.0,
             ):
            result = detect_native_hardware_defaults()

        self.assertEqual(result.dtype, "float16")

    def test_gpu_max_vram_derives_from_total_minus_reserve(self):
        fake_torch = _fake_torch(cuda_available=True)

        with patch.dict("sys.modules", {"torch": fake_torch}), \
             patch(
                 "src.platform.runtime.native.memory.residency.total_vram_gb",
                 return_value=90.0,
             ), \
             patch(
                 "src.platform.runtime.native.memory.residency.minimum_inference_memory_gb",
                 return_value=2.0,
             ):
            result = detect_native_hardware_defaults()

        self.assertEqual(result.gpu_max_vram, 88)

    def test_gpu_max_vram_never_goes_below_one(self):
        """A tiny/misreported total must not derive a useless-or-negative cap."""
        fake_torch = _fake_torch(cuda_available=True)

        with patch.dict("sys.modules", {"torch": fake_torch}), \
             patch(
                 "src.platform.runtime.native.memory.residency.total_vram_gb",
                 return_value=0.5,
             ), \
             patch(
                 "src.platform.runtime.native.memory.residency.minimum_inference_memory_gb",
                 return_value=1.0,
             ):
            result = detect_native_hardware_defaults()

        self.assertEqual(result.gpu_max_vram, 1)

    def test_result_is_cached_across_calls(self):
        """Probing runs once per process - repeated NativeBackendConfig() construction
        (the auto-provisioned backend's persisted config stays `{}` forever) must not
        re-probe or re-log on every call."""
        fake_torch = _fake_torch(cuda_available=True)

        with patch.dict("sys.modules", {"torch": fake_torch}), \
             patch(
                 "src.platform.runtime.native.memory.residency.total_vram_gb",
                 return_value=24.0,
             ) as total_vram, \
             patch(
                 "src.platform.runtime.native.memory.residency.minimum_inference_memory_gb",
                 return_value=1.0,
             ):
            detect_native_hardware_defaults()
            detect_native_hardware_defaults()

        total_vram.assert_called_once()


if __name__ == "__main__":
    unittest.main()
