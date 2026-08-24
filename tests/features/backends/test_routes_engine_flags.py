"""Tests for the native engine-flags endpoints (torch.compile / stream prefetch).

Controller-level, like TestBackendOptimizations in test_routes.py: probe/catalog/
attention are patched at their import site; the flag override modules are the
REAL ones so the immediate-apply contract (setting saved -> enabled() flips with
no restart and no env var) is exercised for real, with overrides reset per test.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
from fastapi import HTTPException

from src.features.backends.routes import BackendController
from src.features.backends.backend_config import BackendConfigManager, NATIVE_ENGINE, NativeBackendConfig
from src.features.backends.backend_registry import BackendRegistry
from src.platform.runtime.native.memory import partial
from src.platform.runtime.native.optimizations import compile as torch_compile_mod
from src.platform.runtime.native.optimizations.probe import SystemProbe
from src.platform.settings.settings import SettingsManager
from src.platform.security.user import AccountType, User


@pytest.fixture(autouse=True)
def clean_flag_state(monkeypatch):
    monkeypatch.setattr(torch_compile_mod, "_compile_override", None)
    monkeypatch.setattr(partial, "_prefetch_policy_override", None)
    monkeypatch.delenv(torch_compile_mod.NATIVE_TORCH_COMPILE_ENV, raising=False)
    monkeypatch.delenv(partial.NATIVE_STREAM_PREFETCH_ENV, raising=False)


@pytest.fixture
def mock_settings_manager():
    return Mock(spec=SettingsManager)


@pytest.fixture
def controller(mock_settings_manager):
    bcm = Mock(spec=BackendConfigManager)
    bcm.get_default_backend_ids.return_value = {}
    bcm.get_backend.return_value = NativeBackendConfig(
        id="native-1", name="Local GPU", engine=NATIVE_ENGINE, enabled=True, priority=1
    )
    registry = Mock(spec=BackendRegistry)
    registry.refresh_backends = AsyncMock()
    registry.backend_config_manager = bcm
    return BackendController(mock_settings_manager, registry)


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


# ---------------------------------------------------------------- #
# GET /{backend_id}/optimizations : engine_flags in the payload
# ---------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_optimizations_reports_engine_flags(controller, admin_user):
    fake_probe = SystemProbe(
        cuda_available=True, compute_capability=(9, 0),
        active_backend="sdpa", available_backends=["sdpa"],
    )
    torch_compile_mod.set_torch_compile_override("on")
    with patch("src.platform.runtime.native.optimizations.probe_system", return_value=fake_probe), \
         patch("src.platform.runtime.native.optimizations.CATALOG", {}), \
         patch("src.platform.runtime.native.attention") as mock_attention:
        mock_attention.get_backend_override.return_value = None
        response = await controller.get_backend_optimizations("native-1", user=admin_user)

    assert response.success is True
    assert response.data["engine_flags"] == {"torch_compile": True, "stream_prefetch": False}


# ---------------------------------------------------------------- #
# PUT /{backend_id}/optimizations/engine-flags
# ---------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_set_engine_flags_requires_admin(controller, regular_user):
    with pytest.raises(HTTPException) as exc_info:
        await controller.set_engine_flags("native-1", torch_compile="on", user=regular_user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_set_engine_flags_rejects_non_native_backend(controller, admin_user):
    controller.backend_config_manager.get_backend.return_value = None
    with pytest.raises(HTTPException) as exc_info:
        await controller.set_engine_flags("nope", torch_compile="on", user=admin_user)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_set_engine_flags_rejects_invalid_value(controller, admin_user, mock_settings_manager):
    with pytest.raises(HTTPException) as exc_info:
        await controller.set_engine_flags("native-1", torch_compile="maybe", user=admin_user)
    assert exc_info.value.status_code == 400
    mock_settings_manager.set_setting.assert_not_called()
    assert torch_compile_mod.torch_compile_enabled() is False


@pytest.mark.asyncio
async def test_set_torch_compile_persists_and_applies_immediately(
    controller, admin_user, mock_settings_manager
):
    assert torch_compile_mod.torch_compile_enabled() is False

    response = await controller.set_engine_flags("native-1", torch_compile="on", user=admin_user)

    mock_settings_manager.set_setting.assert_called_once_with("native_torch_compile", "on")
    assert torch_compile_mod.torch_compile_enabled() is True
    assert response.data["engine_flags"]["torch_compile"] is True
    assert response.data["engine_flags"]["stream_prefetch"] is False


@pytest.mark.asyncio
async def test_set_stream_prefetch_off_beats_env(controller, admin_user, mock_settings_manager, monkeypatch):
    monkeypatch.setenv(partial.NATIVE_STREAM_PREFETCH_ENV, "on")
    assert partial.stream_prefetch_enabled() is True

    response = await controller.set_engine_flags("native-1", stream_prefetch="off", user=admin_user)

    mock_settings_manager.set_setting.assert_called_once_with("native_stream_prefetch", "off")
    assert partial.stream_prefetch_enabled() is False
    assert response.data["engine_flags"]["stream_prefetch"] is False


@pytest.mark.asyncio
async def test_set_both_flags_in_one_call(controller, admin_user, mock_settings_manager):
    response = await controller.set_engine_flags(
        "native-1", torch_compile="on", stream_prefetch="on", user=admin_user
    )

    assert mock_settings_manager.set_setting.call_count == 2
    assert response.data["engine_flags"] == {"torch_compile": True, "stream_prefetch": True}


@pytest.mark.asyncio
async def test_omitted_flag_is_left_unchanged(controller, admin_user, mock_settings_manager):
    torch_compile_mod.set_torch_compile_override("on")

    response = await controller.set_engine_flags("native-1", stream_prefetch="on", user=admin_user)

    mock_settings_manager.set_setting.assert_called_once_with("native_stream_prefetch", "on")
    assert response.data["engine_flags"] == {"torch_compile": True, "stream_prefetch": True}
