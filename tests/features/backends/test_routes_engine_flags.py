"""Tests for the native engine-flags endpoints (torch.compile / stream prefetch).

Controller-level, like TestBackendOptimizations in test_routes.py: probe/catalog/
attention are patched at their import site; the flag override modules are the
REAL ones so the immediate-apply contract (setting saved -> enabled() flips with
no restart and no env var) is exercised for real, with overrides reset per test.
"""
import pytest
from unittest.mock import Mock, AsyncMock
from fastapi import HTTPException

from src.features.backends.routes import BackendController
from src.features.backends.backend_config import (
    BackendConfigStore,
    BaseBackendConfig,
    NativeBackendConfig,
    NativeRemoteBackendConfig,
)
from src.features.backends.backend_registry import BackendRegistry
from src.platform.runtime.native.memory import partial
from src.platform.runtime.native.optimizations import compile as torch_compile_mod
from src.platform.settings import runtime_flags
from src.platform.settings.settings import Settings
from src.platform.security.user import AccountType, User
from vendor.gpl.comfyui import ops


class _NoEngineFlagsConfig(BaseBackendConfig):
    pass


@pytest.fixture(autouse=True)
def clean_flag_state(monkeypatch):
    values = runtime_flags.runtime_flag_values()
    snapshot = dict(values)
    monkeypatch.setattr(ops, "RUNTIME_FLAGS", ops.RUNTIME_FLAGS)
    monkeypatch.setattr(runtime_flags, "_vendor_bound", runtime_flags._vendor_bound)
    values["native_torch_compile"] = False
    values["native_stream_prefetch"] = False
    yield
    values.clear()
    values.update(snapshot)


@pytest.fixture
def local_backend():
    return NativeBackendConfig(id="native-1", name="Local GPU", enabled=True, priority=1)


@pytest.fixture
def store(local_backend):
    bcm = Mock(spec=BackendConfigStore)
    bcm.get_default_backend_ids.return_value = {}
    bcm.get_backend.return_value = local_backend
    return bcm


@pytest.fixture
def controller(store):
    registry = Mock(spec=BackendRegistry)
    registry.refresh_backends = AsyncMock()
    registry.backend_config_store = store
    return BackendController(Mock(spec=Settings), registry)


@pytest.fixture
def admin_user():
    user = Mock(spec=User)
    user.account_type = AccountType.ADMIN
    return user


@pytest.fixture
def regular_user():
    user = Mock(spec=User)
    user.account_type = AccountType.USER
    return user


@pytest.mark.asyncio
async def test_get_engine_flags_reports_every_engine_setting_with_stored_values(controller, admin_user, local_backend):
    local_backend.engine_flags = {"native_torch_compile": True}

    response = await controller.get_engine_flags("native-1", user=admin_user)

    flags = response.data["engine_flags"]
    assert set(flags) == set(runtime_flags.ENGINE_FLAG_KEYS)
    assert flags["native_torch_compile"] is True
    assert flags["native_stream_prefetch"] is False
    assert response.data["is_local"] is True


@pytest.mark.asyncio
async def test_set_engine_flags_requires_admin(controller, regular_user, store):
    with pytest.raises(HTTPException) as exc_info:
        await controller.set_engine_flags("native-1", {"native_torch_compile": True}, user=regular_user)
    assert exc_info.value.status_code == 403
    store.update_backend.assert_not_called()


@pytest.mark.asyncio
async def test_set_engine_flags_rejects_unknown_backend(controller, admin_user, store):
    store.get_backend.return_value = None
    with pytest.raises(HTTPException) as exc_info:
        await controller.set_engine_flags("nope", {"native_torch_compile": True}, user=admin_user)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_set_engine_flags_rejects_backend_without_engine_settings(controller, admin_user, store):
    store.get_backend.return_value = _NoEngineFlagsConfig(id="comfy-1", name="Comfy", engine="comfyui")
    with pytest.raises(HTTPException) as exc_info:
        await controller.set_engine_flags("comfy-1", {"native_torch_compile": True}, user=admin_user)
    assert exc_info.value.status_code == 400
    store.update_backend.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [{"native_torch_compile": "maybe"}, {"not_a_setting": True}, {"profiling_enabled": True}])
async def test_set_engine_flags_rejects_invalid_input(controller, admin_user, store, local_backend, flags):
    with pytest.raises(HTTPException) as exc_info:
        await controller.set_engine_flags("native-1", flags, user=admin_user)
    assert exc_info.value.status_code == 400
    store.update_backend.assert_not_called()
    assert local_backend.engine_flags == {}
    assert torch_compile_mod.torch_compile_enabled() is False


@pytest.mark.asyncio
async def test_set_torch_compile_persists_a_bool_and_applies_immediately(controller, admin_user, store, local_backend):
    assert torch_compile_mod.torch_compile_enabled() is False

    response = await controller.set_engine_flags("native-1", {"native_torch_compile": "on"}, user=admin_user)

    store.update_backend.assert_called_once_with("native-1", local_backend)
    assert local_backend.engine_flags == {"native_torch_compile": True}
    assert type(local_backend.engine_flags["native_torch_compile"]) is bool
    assert runtime_flags.runtime_flag("native_torch_compile") is True
    assert torch_compile_mod.torch_compile_enabled() is True
    assert response.data["engine_flags"]["native_torch_compile"] is True
    assert response.data["engine_flags"]["native_stream_prefetch"] is False


@pytest.mark.asyncio
async def test_saved_values_are_coerced_to_each_settings_type(controller, admin_user, local_backend):
    await controller.set_engine_flags(
        "native-1",
        {
            "native_fp8_matmul": "on",
            "native_fp8_quantize": "FORCE",
            "native_min_inference_memory_gb": "2.5",
            "native_ltx_decode_tile_px": "256",
        },
        user=admin_user,
    )

    stored = local_backend.engine_flags
    assert stored == {
        "native_fp8_matmul": True,
        "native_fp8_quantize": "force",
        "native_min_inference_memory_gb": 2.5,
        "native_ltx_decode_tile_px": 256,
    }
    assert type(stored["native_fp8_matmul"]) is bool
    assert type(stored["native_min_inference_memory_gb"]) is float
    assert type(stored["native_ltx_decode_tile_px"]) is int


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [{"native_fp8_quantize": "sometimes"}, {"native_ltx_decode_tile_px": "12.5"}])
async def test_out_of_type_values_are_rejected(controller, admin_user, store, local_backend, flags):
    with pytest.raises(HTTPException) as exc_info:
        await controller.set_engine_flags("native-1", flags, user=admin_user)
    assert exc_info.value.status_code == 400
    store.update_backend.assert_not_called()
    assert local_backend.engine_flags == {}


@pytest.mark.asyncio
async def test_saving_one_backend_leaves_another_backends_values_alone(controller, admin_user, store, local_backend):
    remote = NativeRemoteBackendConfig(id="remote-1", name="Worker", engine_flags={"native_torch_compile": False})
    store.get_backend.side_effect = lambda backend_id: {"native-1": local_backend, "remote-1": remote}[backend_id]

    await controller.set_engine_flags("native-1", {"native_torch_compile": True}, user=admin_user)

    assert local_backend.engine_flags == {"native_torch_compile": True}
    assert remote.engine_flags == {"native_torch_compile": False}
    response = await controller.get_engine_flags("remote-1", user=admin_user)
    assert response.data["engine_flags"]["native_torch_compile"] is False


@pytest.mark.asyncio
async def test_set_stream_prefetch_off_turns_it_off(controller, admin_user, local_backend):
    local_backend.engine_flags = {"native_stream_prefetch": True}
    runtime_flags.runtime_flag_values()["native_stream_prefetch"] = True
    assert partial.stream_prefetch_enabled() is True

    response = await controller.set_engine_flags("native-1", {"native_stream_prefetch": False}, user=admin_user)

    assert local_backend.engine_flags == {"native_stream_prefetch": False}
    assert runtime_flags.runtime_flag("native_stream_prefetch") is False
    assert partial.stream_prefetch_enabled() is False
    assert response.data["engine_flags"]["native_stream_prefetch"] is False


@pytest.mark.asyncio
async def test_set_both_flags_in_one_call(controller, admin_user, store, local_backend):
    response = await controller.set_engine_flags(
        "native-1", {"native_torch_compile": True, "native_stream_prefetch": True}, user=admin_user
    )

    store.update_backend.assert_called_once()
    assert local_backend.engine_flags == {"native_torch_compile": True, "native_stream_prefetch": True}
    assert response.data["engine_flags"]["native_torch_compile"] is True
    assert response.data["engine_flags"]["native_stream_prefetch"] is True


@pytest.mark.asyncio
async def test_omitted_flag_is_left_unchanged(controller, admin_user, local_backend):
    local_backend.engine_flags = {"native_torch_compile": True}

    response = await controller.set_engine_flags("native-1", {"native_stream_prefetch": True}, user=admin_user)

    assert local_backend.engine_flags == {"native_torch_compile": True, "native_stream_prefetch": True}
    assert response.data["engine_flags"]["native_torch_compile"] is True
    assert response.data["engine_flags"]["native_stream_prefetch"] is True


@pytest.mark.asyncio
async def test_remote_backend_flags_are_saved_but_not_applied_here(controller, admin_user, store):
    remote = NativeRemoteBackendConfig(id="remote-1", name="Worker")
    store.get_backend.return_value = remote

    response = await controller.set_engine_flags("remote-1", {"native_torch_compile": True}, user=admin_user)

    store.update_backend.assert_called_once_with("remote-1", remote)
    assert remote.engine_flags == {"native_torch_compile": True}
    assert response.data["engine_flags"]["native_torch_compile"] is True
    assert runtime_flags.runtime_flag("native_torch_compile") is False
