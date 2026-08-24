"""Regression coverage: the admin "Clear VRAM" HTTP route and the
automation node's "backend_action" share ONE implementation
(`residency.clear_vram`) instead of two independently-maintained copies.

Before this fix, `BackendController.clear_backend_vram` called
`GpuResidencyManager.offload_all(device)` directly, with no lease exclusion
and no fallback sweep of the model-lifecycle cache — so a component that
ended up GPU-resident without registering with the residency ledger (a
placement path that forgot to) was invisible to the admin action even though
the automation node's equivalent action already caught it. The first two
tests below prove the route now catches this: they fail against the old
route body (`offload_all(backend_config.device)` with nothing else) and pass
against `residency.clear_vram`.
"""
import sys
import os
from unittest.mock import Mock, AsyncMock, MagicMock, patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.features.backends.routes import BackendController
from src.features.backends.backend_config import BackendConfigManager, NATIVE_ENGINE, NativeBackendConfig
from src.features.backends.backend_registry import BackendRegistry
from src.platform.settings.settings import SettingsManager
from src.platform.security.user import AccountType, User
from src.platform.runtime.native.memory.residency import GpuResidencyManager, clear_vram


class FakeCachedModel:
    """Mimics a NativeModel wrapper as seen through ModelLifecycleManager.cached_values()."""

    def __init__(self, device, estimated_vram_gb):
        self.device = device
        self.estimated_vram_gb = estimated_vram_gb
        self.offload_calls = 0

    def offload(self):
        self.offload_calls += 1
        self.device = "cpu"


@pytest.fixture
def admin_user():
    user = Mock(spec=User)
    user.account_type = AccountType.ADMIN
    return user


@pytest.fixture
def native_backend():
    return NativeBackendConfig(id="native-1", name="Local GPU", engine=NATIVE_ENGINE, enabled=True, priority=1)


def _controller(lifecycle_manager):
    settings_manager = Mock(spec=SettingsManager)
    backend_config_manager = Mock(spec=BackendConfigManager)
    backend_config_manager.get_default_backend_ids.return_value = {}
    backend_registry = Mock(spec=BackendRegistry)
    backend_registry.refresh_backends = AsyncMock()
    backend_registry.backend_config_manager = backend_config_manager
    return BackendController(settings_manager, backend_registry, lifecycle_manager)


class TestClearBackendVramSweepsLifecycleCache:
    """The admin route now shares residency.clear_vram with the automation
    node, so it must sweep ModelLifecycleManager.cached_values() for a
    component the residency ledger never saw."""

    @pytest.mark.asyncio
    async def test_route_offloads_an_unregistered_gpu_resident_cache_entry(self, admin_user, native_backend):
        unregistered_straggler = FakeCachedModel("cuda:0", 6.0)

        lifecycle = MagicMock()
        lifecycle.leased_values.return_value = []
        lifecycle.cached_values.return_value = [unregistered_straggler]
        lifecycle.cleanup = Mock()

        controller = _controller(lifecycle)
        controller.backend_config_manager.get_backend.return_value = native_backend

        residency_manager = MagicMock()
        residency_manager.offload_all.return_value = GpuResidencyManager().offload_all("cuda")  # empty OffloadResult

        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=residency_manager,
        ):
            response = await controller.clear_backend_vram("native-1", user=admin_user)

        assert response.success is True
        # This is exactly what the pre-fix route missed: nothing in the
        # residency ledger, but a real GPU-resident cache entry the sweep
        # must still catch.
        assert unregistered_straggler.offload_calls == 1
        assert unregistered_straggler.device == "cpu"
        assert response.data["offloaded_count"] == 1
        assert response.data["swept_count"] == 1

    @pytest.mark.asyncio
    async def test_route_excludes_a_leased_model_from_both_ledger_and_sweep(self, admin_user, native_backend):
        leased_gpu = FakeCachedModel("cuda:0", 9.0)

        lifecycle = MagicMock()
        lifecycle.leased_values.return_value = [leased_gpu]
        lifecycle.cached_values.return_value = [leased_gpu]
        lifecycle.cleanup = Mock()

        controller = _controller(lifecycle)
        controller.backend_config_manager.get_backend.return_value = native_backend

        residency_manager = MagicMock()
        residency_manager.offload_all.return_value = GpuResidencyManager().offload_all("cuda")

        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=residency_manager,
        ):
            response = await controller.clear_backend_vram("native-1", user=admin_user)

        residency_manager.offload_all.assert_called_once_with(native_backend.device, exclude=[leased_gpu])
        assert leased_gpu.offload_calls == 0
        assert response.data["offloaded_count"] == 0
        assert response.data["swept_count"] == 0


class TestClearVramSharedFunction:
    """Direct coverage of residency.clear_vram against the REAL
    GpuResidencyManager (no mocking of the manager itself) — the CPU-only
    repro this test replaces lived at
    scratchpad/clear_vram_repro.py during investigation."""

    def test_offloads_a_registered_component_and_sweeps_an_unregistered_one(self):
        class StubModel:
            def __init__(self, device, size_gb):
                self.device = device
                self.estimated_vram_gb = size_gb
                self.offload_calls = 0

            def offload(self):
                self.offload_calls += 1
                self.device = "cpu"

        manager = GpuResidencyManager()
        registered = StubModel("cuda:0", 24.0)
        manager.note_resident(registered, "cuda:0", registered.estimated_vram_gb)
        unregistered_straggler = StubModel("cuda:0", 3.0)

        lifecycle = MagicMock()
        lifecycle.leased_values.return_value = []
        lifecycle.cached_values.return_value = [registered, unregistered_straggler]

        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=manager,
        ):
            result = clear_vram("cuda:0", lifecycle)

        assert registered.device == "cpu"
        assert unregistered_straggler.device == "cpu"
        assert result.offloaded_count == 2
        assert result.swept_count == 1
        assert result.freed_gb == pytest.approx(27.0)
        assert result.failed_count == 0

    def test_lifecycle_manager_none_still_offloads_the_ledger_only(self):
        class StubModel:
            def __init__(self, device, size_gb):
                self.device = device
                self.estimated_vram_gb = size_gb

            def offload(self):
                self.device = "cpu"

        manager = GpuResidencyManager()
        registered = StubModel("cuda:0", 10.0)
        manager.note_resident(registered, "cuda:0", registered.estimated_vram_gb)

        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=manager,
        ):
            result = clear_vram("cuda:0", None)

        assert registered.device == "cpu"
        assert result.offloaded_count == 1
        assert result.swept_count == 0

    def test_sweep_does_not_double_count_a_component_the_ledger_already_offloaded(self):
        class StubModel:
            def __init__(self, device, size_gb):
                self.device = device
                self.estimated_vram_gb = size_gb
                self.offload_calls = 0

            def offload(self):
                self.offload_calls += 1
                self.device = "cpu"

        manager = GpuResidencyManager()
        model = StubModel("cuda:0", 12.0)
        manager.note_resident(model, "cuda:0", model.estimated_vram_gb)

        lifecycle = MagicMock()
        lifecycle.leased_values.return_value = []
        # The same object the ledger will offload is ALSO in the cache -
        # by the time the sweep runs it must already read back as cpu.
        lifecycle.cached_values.return_value = [model]

        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=manager,
        ):
            result = clear_vram("cuda:0", lifecycle)

        assert model.offload_calls == 1
        assert result.offloaded_count == 1
        assert result.swept_count == 0
