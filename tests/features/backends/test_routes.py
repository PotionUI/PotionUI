import pytest
import sys
import os
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any
from fastapi import HTTPException

# Add the src directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.features.backends.routes import BackendController
from src.platform.http.base_controller import APIResponse
from src.features.backends.backend_config import (
    BackendConfigManager, BackendHealth, BackendStatus, NATIVE_ENGINE,
    BaseBackendConfig, NativeBackendConfig
)
from src.features.backends.backend_registry import BackendRegistry
from src.platform.runtime.native.optimizations.catalog import OptimizationStatus, Requirement
from src.platform.runtime.native.optimizations.probe import SystemProbe
from src.platform.settings.settings import SettingsManager
from src.platform.security.user import AccountType, User


from typing import Optional
from pydantic import Field


class PluginBackendConfig(BaseBackendConfig):
    """Stands in for a plugin-registered engine (e.g. comfyui)."""
    engine: str = "comfyui"
    host: str = "127.0.0.1"
    port: int = 8188


class SecretBackendConfig(BaseBackendConfig):
    """A plugin engine with a credential field marked secret (like ComfyUI's api_key)."""
    engine: str = "comfyui"
    host: str = "127.0.0.1"
    api_key: Optional[str] = Field(default=None, json_schema_extra={"secret": True})


class TestBackendController:
    """Comprehensive tests for BackendController"""

    @pytest.fixture
    def mock_settings_manager(self):
        """Mock SettingsManager"""
        mock = Mock(spec=SettingsManager)
        return mock

    @pytest.fixture
    def mock_backend_config_manager(self):
        """Mock BackendConfigManager"""
        mock = Mock(spec=BackendConfigManager)
        # get_default_backend_ids() is consulted by every serialization path
        # (_serialize + list_backends); default to "nothing is default" so
        # tests that don't care about is_default get a real dict, not a Mock.
        mock.get_default_backend_ids.return_value = {}
        return mock

    @pytest.fixture
    def mock_backend_registry(self, mock_backend_config_manager):
        """Mock BackendRegistry"""
        mock = Mock(spec=BackendRegistry)
        mock.refresh_backends = AsyncMock()
        mock.backend_config_manager = mock_backend_config_manager
        return mock

    @pytest.fixture
    def sample_local_backend(self):
        """Sample native backend configuration"""
        return NativeBackendConfig(
            id="local-1",
            name="Local GPU",
            engine=NATIVE_ENGINE,
            enabled=True,
            priority=1
        )

    @pytest.fixture
    def sample_plugin_backend(self):
        """Sample plugin-registered backend configuration"""
        return PluginBackendConfig(
            id="comfyui-1",
            name="ComfyUI",
            enabled=True,
            priority=2
        )

    @pytest.fixture
    def sample_health_info(self):
        """Sample backend health information"""
        return BackendHealth(
            backend_id="test-backend",
            status=BackendStatus.AVAILABLE,
            last_check="2024-01-01T00:00:00Z",
            response_time_ms=100.5,
            gpu_info={"gpu_count": 1, "gpu_memory": "24GB"}
        )

    @pytest.fixture
    def controller(self, mock_settings_manager, mock_backend_registry):
        """BackendController instance with mocked dependencies"""
        controller = BackendController(mock_settings_manager, mock_backend_registry)
        return controller

    @pytest.mark.asyncio
    async def test_list_backends_success(self, controller, sample_local_backend, sample_plugin_backend):
        """Test successful listing of backends"""
        # Arrange
        backends = [sample_local_backend, sample_plugin_backend]
        controller.backend_config_manager.get_backends.return_value = backends

        # Act
        response = await controller.list_backends()

        # Assert
        assert response.success is True
        assert len(response.data) == 2
        assert response.data[0]["id"] == "local-1"
        assert response.data[1]["id"] == "comfyui-1"
        controller.backend_config_manager.get_backends.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_backends_marks_is_default_per_engine(
        self, controller, sample_local_backend, sample_plugin_backend
    ):
        """is_default is True only for the backend whose id matches its
        engine's default in get_default_backend_ids(), never cross-engine."""
        # Arrange
        backends = [sample_local_backend, sample_plugin_backend]
        controller.backend_config_manager.get_backends.return_value = backends
        controller.backend_config_manager.get_default_backend_ids.return_value = {
            "native": "local-1"
        }

        # Act
        response = await controller.list_backends()

        # Assert
        by_id = {b["id"]: b for b in response.data}
        assert by_id["local-1"]["is_default"] is True
        # comfyui-1 is a different engine with no default entry - must be False.
        assert by_id["comfyui-1"]["is_default"] is False

    @pytest.mark.asyncio
    async def test_list_backends_exception(self, controller):
        """Test list_backends with exception"""
        # Arrange
        controller.backend_config_manager.get_backends.side_effect = Exception("Database error")

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.list_backends()

        # The controller returns an error_response which raises HTTPException
        assert "Database error" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_get_enabled_backends_success(self, controller, sample_local_backend):
        """Test successful retrieval of enabled backends"""
        # Arrange
        enabled_backends = [sample_local_backend]
        controller.backend_config_manager.get_enabled_backends.return_value = enabled_backends

        # Act
        response = await controller.get_enabled_backends()

        # Assert
        assert response.success is True
        assert len(response.data) == 1
        assert response.data[0]["enabled"] is True
        controller.backend_config_manager.get_enabled_backends.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_enabled_backends_marks_is_default(self, controller, sample_local_backend):
        """Test that is_default is enriched onto enabled backends too"""
        # Arrange
        controller.backend_config_manager.get_enabled_backends.return_value = [sample_local_backend]
        controller.backend_config_manager.get_default_backend_ids.return_value = {"native": "local-1"}

        # Act
        response = await controller.get_enabled_backends()

        # Assert
        assert response.data[0]["is_default"] is True

    @pytest.mark.asyncio
    async def test_get_default_backend_success(self, controller, sample_local_backend):
        """Test successful retrieval of default backend for an engine"""
        # Arrange
        controller.backend_config_manager.get_default_backend.return_value = sample_local_backend
        controller.backend_config_manager.get_default_backend_ids.return_value = {"native": "local-1"}

        # Act
        response = await controller.get_default_backend("native")

        # Assert
        assert response.success is True
        assert response.data["id"] == "local-1"
        assert response.data["is_default"] is True
        controller.backend_config_manager.get_default_backend.assert_called_once_with("native")

    @pytest.mark.asyncio
    async def test_get_default_backend_no_backend_for_engine(self, controller):
        """Test that no backend for the requested engine surfaces an error.

        Note: the inner 404 raised by error_response() is caught by the
        method's own broad `except Exception`, same as get_backend_not_found
        below - it is re-wrapped as a 400 whose message still carries the
        original 'no_backend_for_engine' text.
        """
        # Arrange
        controller.backend_config_manager.get_default_backend.return_value = None

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_default_backend("comfyui")

        assert "no_backend_for_engine" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_get_backend_success(self, controller, sample_local_backend):
        """Test successful retrieval of specific backend"""
        # Arrange
        backend_id = "local-1"
        controller.backend_config_manager.get_backend.return_value = sample_local_backend

        # Act
        response = await controller.get_backend(backend_id)

        # Assert
        assert response.success is True
        assert response.data["id"] == backend_id
        controller.backend_config_manager.get_backend.assert_called_once_with(backend_id)

    @pytest.mark.asyncio
    async def test_get_backend_is_default_true_for_engine_default(
        self, controller, sample_local_backend
    ):
        """is_default is True when this backend's id is its engine's default."""
        # Arrange
        controller.backend_config_manager.get_backend.return_value = sample_local_backend
        controller.backend_config_manager.get_default_backend_ids.return_value = {"native": "local-1"}

        # Act
        response = await controller.get_backend("local-1")

        # Assert
        assert response.data["is_default"] is True

    @pytest.mark.asyncio
    async def test_get_backend_is_default_false_for_same_engine_non_default(
        self, controller, sample_plugin_backend
    ):
        """A same-engine backend that isn't the recorded default must read False,
        even when some OTHER backend of that engine is the default."""
        # Arrange: comfyui-1 is not the default; a different comfyui backend is.
        controller.backend_config_manager.get_backend.return_value = sample_plugin_backend
        controller.backend_config_manager.get_default_backend_ids.return_value = {
            "comfyui": "comfyui-2"
        }

        # Act
        response = await controller.get_backend("comfyui-1")

        # Assert
        assert response.data["is_default"] is False

    @pytest.mark.asyncio
    async def test_get_backend_not_found(self, controller):
        """Test get_backend when backend not found"""
        # Arrange
        backend_id = "non-existent"
        controller.backend_config_manager.get_backend.return_value = None

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_backend(backend_id)

        # The inner 404 gets wrapped in an outer exception handler
        # Check that the error message contains backend_not_found
        assert "backend_not_found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_get_backend_stats_success(self, controller, sample_local_backend):
        """Test successful retrieval of per-backend model stats"""
        # Arrange
        controller.backend_config_manager.get_backend.return_value = sample_local_backend
        with patch(
            "src.features.models.availability_repository.model_availability_repo.stats_for_backend"
        ) as mock_stats:
            mock_stats.return_value = {
                "indexed_models": 5,
                "total_size_bytes": 3 * 1024 ** 3,
                "last_indexed_at": "2024-01-01T00:00:00",
            }

            # Act
            response = await controller.get_backend_stats("local-1")

        # Assert
        assert response.success is True
        assert response.data["backend_id"] == "local-1"
        assert response.data["indexed_models"] == 5
        assert response.data["total_size_gb"] == 3.0
        assert response.data["last_indexed_at"] == "2024-01-01T00:00:00"
        mock_stats.assert_called_once_with("local-1")

    @pytest.mark.asyncio
    async def test_get_backend_stats_never_indexed_is_zeroed_not_an_error(self, controller, sample_local_backend):
        """A backend that's never been indexed has no availability rows yet -
        that's a legitimate zero, not a failure."""
        # Arrange
        controller.backend_config_manager.get_backend.return_value = sample_local_backend
        with patch(
            "src.features.models.availability_repository.model_availability_repo.stats_for_backend"
        ) as mock_stats:
            mock_stats.return_value = {"indexed_models": 0, "total_size_bytes": 0, "last_indexed_at": None}

            # Act
            response = await controller.get_backend_stats("local-1")

        # Assert
        assert response.success is True
        assert response.data["indexed_models"] == 0
        assert response.data["total_size_gb"] == 0
        assert response.data["last_indexed_at"] is None

    @pytest.mark.asyncio
    async def test_get_backend_stats_not_found(self, controller):
        """Test get_backend_stats when backend not found"""
        # Arrange
        controller.backend_config_manager.get_backend.return_value = None

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_backend_stats("non-existent")

        assert "backend_not_found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_create_backend_success(self, controller, sample_local_backend):
        """Test successful backend creation"""
        # Arrange
        backend_data = {
            "id": "local-1",
            "name": "Local GPU",
            "engine": "native",
            "enabled": True
        }
        controller.backend_config_manager.validate_backend_config.return_value = sample_local_backend
        controller.backend_config_manager.add_backend.return_value = None

        # Act
        response = await controller.create_backend(backend_data)

        # Assert
        assert response.success is True
        assert response.data["id"] == "local-1"
        assert "created successfully" in response.message
        controller.backend_config_manager.validate_backend_config.assert_called_once_with(backend_data)
        controller.backend_config_manager.add_backend.assert_called_once_with(sample_local_backend)
        controller.backend_registry.refresh_backends.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_backend_validation_error(self, controller):
        """Test create_backend with validation error"""
        # Arrange
        backend_data = {"invalid": "data"}
        controller.backend_config_manager.validate_backend_config.side_effect = ValueError("Invalid configuration")

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.create_backend(backend_data)

        assert exc_info.value.status_code == 400
        assert "backend_validation_failed" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_update_backend_success(self, controller, sample_local_backend):
        """A partial update is merged onto the existing config before validation."""
        # Arrange
        backend_id = "local-1"
        backend_data = {"name": "Updated Local GPU"}
        controller.backend_config_manager.get_backend.return_value = sample_local_backend
        controller.backend_config_manager.validate_backend_config.return_value = sample_local_backend
        controller.backend_config_manager.update_backend.return_value = None

        # Act
        response = await controller.update_backend(backend_id, backend_data)

        # Assert
        assert response.success is True
        assert "updated successfully" in response.message
        # The controller validates the MERGED config, not the raw partial body:
        # id and engine are forced back on, and untouched fields survive.
        merged = controller.backend_config_manager.validate_backend_config.call_args[0][0]
        assert merged["name"] == "Updated Local GPU"
        assert merged["id"] == backend_id
        assert merged["engine"] == sample_local_backend.engine
        assert merged["priority"] == sample_local_backend.priority
        controller.backend_config_manager.update_backend.assert_called_once_with(backend_id, sample_local_backend)
        controller.backend_registry.refresh_backends.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_backend_not_found(self, controller):
        """Updating an unknown backend raises a 404, not a rewrapped 400."""
        controller.backend_config_manager.get_backend.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await controller.update_backend("nope", {"name": "x"})

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "backend_not_found"

    @pytest.mark.asyncio
    async def test_update_backend_cannot_change_engine(self, controller, sample_local_backend):
        """`engine` is immutable: a body trying to change it is overridden."""
        controller.backend_config_manager.get_backend.return_value = sample_local_backend
        controller.backend_config_manager.validate_backend_config.return_value = sample_local_backend

        await controller.update_backend("local-1", {"engine": "comfyui"})

        merged = controller.backend_config_manager.validate_backend_config.call_args[0][0]
        assert merged["engine"] == sample_local_backend.engine

    @pytest.mark.asyncio
    async def test_delete_backend_success(self, controller):
        """Test successful backend deletion"""
        # Arrange
        backend_id = "local-1"
        controller.backend_config_manager.remove_backend.return_value = None

        # Act
        response = await controller.delete_backend(backend_id)

        # Assert
        assert response.success is True
        assert "deleted successfully" in response.message
        controller.backend_config_manager.remove_backend.assert_called_once_with(backend_id)
        controller.backend_registry.refresh_backends.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_backend_error(self, controller):
        """Test delete_backend with error"""
        # Arrange
        backend_id = "local-1"
        controller.backend_config_manager.remove_backend.side_effect = ValueError("Backend not found")

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.delete_backend(backend_id)

        assert exc_info.value.status_code == 400
        assert "backend_delete_failed" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_test_backend_success(self, controller, sample_health_info):
        """Test successful backend testing"""
        # Arrange
        backend_id = "test-backend"
        mock_backend = Mock()
        mock_backend.health_check = AsyncMock(return_value=sample_health_info)
        controller.backend_registry.get_backend.return_value = mock_backend

        # Act
        response = await controller.test_backend(backend_id)

        # Assert
        assert response.success is True
        controller.backend_registry.get_backend.assert_called_once_with(backend_id)
        mock_backend.health_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_backend_not_found(self, controller):
        """Test test_backend when backend not found"""
        # Arrange
        backend_id = "non-existent"
        controller.backend_registry.get_backend.return_value = None
        controller.backend_config_manager.get_backend.return_value = None

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.test_backend(backend_id)

        # The inner 404 gets wrapped in an outer exception handler
        # Check that the error message contains backend_not_found
        assert "backend_not_found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_get_backend_health_success(self, controller, sample_local_backend, sample_health_info):
        """Test successful backend health check"""
        # Arrange
        backend_id = "local-1"
        controller.backend_config_manager.get_backend.return_value = sample_local_backend
        mock_backend = Mock()
        mock_backend.health_check = AsyncMock(return_value={"status": "available"})
        controller.backend_registry.get_backend.return_value = mock_backend

        # Act
        response = await controller.get_backend_health(backend_id)

        # Assert
        assert response.success is True
        assert response.data["backend_id"] == "local-1"
        mock_backend.health_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_backend_system_info_success(self, controller, sample_local_backend):
        """Test successful backend system info retrieval"""
        # Arrange
        backend_id = "test-backend"
        system_info = {"cpu": "Intel i9", "memory": "32GB"}
        controller.backend_config_manager.get_backend.return_value = sample_local_backend
        mock_backend = Mock()
        mock_backend.get_system_info = AsyncMock(return_value=system_info)
        controller.backend_registry.get_backend.return_value = mock_backend

        # Act
        response = await controller.get_backend_system_info(backend_id)

        # Assert
        assert response.success is True
        assert response.data == system_info
        mock_backend.get_system_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_backend_health_success(self, controller, sample_local_backend, sample_health_info):
        """Test successful listing of backend health statuses"""
        # Arrange
        backends = [sample_local_backend]
        controller.backend_config_manager.get_backends.return_value = backends

        mock_backend = Mock()
        mock_backend.health_check = AsyncMock(return_value={"status": "available"})
        controller.backend_registry.get_backend.return_value = mock_backend

        # Act
        response = await controller.list_backend_health()

        # Assert
        assert response.success is True
        assert len(response.data) == 1
        assert response.data[0]["backend_id"] == "local-1"
        assert response.data[0]["backend_name"] == "Local GPU"
        assert response.data[0]["enabled"] is True
        assert "health" in response.data[0]

    @pytest.mark.asyncio
    async def test_list_backend_health_backend_not_loaded(self, controller, sample_local_backend):
        """Test list_backend_health when backend not loaded in registry"""
        # Arrange
        backends = [sample_local_backend]
        controller.backend_config_manager.get_backends.return_value = backends
        controller.backend_registry.get_backend.return_value = None

        # Act
        response = await controller.list_backend_health()

        # Assert
        assert response.success is True
        assert len(response.data) == 1
        assert response.data[0]["health"]["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_get_engines_success(self, controller):
        """
        /engines returns a descriptor per engine: label, singleton flag, and the
        fields the engine declares. The admin UI renders forms from these, so it
        never hardcodes any engine's settings.
        """
        # Arrange
        controller.backend_registry.get_engine_descriptors.return_value = [
            {"engine": NATIVE_ENGINE, "label": "Native", "singleton": True, "fields": []},
            {
                "engine": "comfyui",
                "label": "ComfyUI",
                "singleton": False,
                "fields": [
                    {"name": "host", "label": "Host", "type": "string", "required": False,
                     "default": "127.0.0.1", "description": None, "secret": False},
                    {"name": "api_key", "label": "API Key", "type": "string", "required": False,
                     "default": None, "description": None, "secret": True},
                ],
            },
        ]

        # Act
        response = await controller.get_engines()

        # Assert
        assert response.success is True
        by_engine = {d["engine"]: d for d in response.data}
        assert by_engine[NATIVE_ENGINE]["singleton"] is True
        assert by_engine[NATIVE_ENGINE]["fields"] == []
        assert by_engine["comfyui"]["singleton"] is False
        assert by_engine["comfyui"]["label"] == "ComfyUI"
        assert [f["name"] for f in by_engine["comfyui"]["fields"]] == ["host", "api_key"]
        controller.backend_registry.get_engine_descriptors.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_default_backend_success(self, controller):
        """Test making a backend the default for its engine"""
        # Arrange
        controller.backend_config_manager.set_default_backend.return_value = None

        # Act
        response = await controller.set_default_backend("comfyui-1")

        # Assert
        assert response.success is True
        controller.backend_config_manager.set_default_backend.assert_called_once_with("comfyui-1")
        controller.backend_registry.refresh_backends.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_default_backend_not_found(self, controller):
        """Test set_default_backend when the backend doesn't exist"""
        # Arrange
        controller.backend_config_manager.set_default_backend.side_effect = ValueError(
            "Backend with ID 'missing' not found"
        )

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.set_default_backend("missing")

        assert exc_info.value.status_code == 404
        assert "backend_not_found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_error_handling_with_logging(self, controller):
        """Test that errors are properly logged"""
        # Arrange
        controller.backend_config_manager.get_backends.side_effect = Exception("Test error")

        # Act & Assert
        with pytest.raises(HTTPException):
            await controller.list_backends()

        # Verify that the logger was called (logger is initialized via super().__init__())
        # The logger is a real logger now, so we can't easily verify it was called
        # Just verify the exception was raised properly


class TestBackendSecretRedaction:
    """Secret-marked config fields (e.g. a ComfyUI api_key) must never be echoed
    to a client, and an update that omits them must not wipe the stored value."""

    @pytest.fixture
    def controller(self):
        settings_manager = Mock(spec=SettingsManager)
        backend_config_manager = Mock(spec=BackendConfigManager)
        backend_config_manager.get_default_backend_ids.return_value = {}
        backend_registry = Mock(spec=BackendRegistry)
        backend_registry.refresh_backends = AsyncMock()
        backend_registry.backend_config_manager = backend_config_manager
        return BackendController(settings_manager, backend_registry)

    @pytest.mark.asyncio
    async def test_list_redacts_secret_and_exposes_boolean(self, controller):
        backend = SecretBackendConfig(id="c1", name="ComfyUI", api_key="super-secret-key")
        controller.backend_config_manager.get_backends.return_value = [backend]

        response = await controller.list_backends()
        data = response.data[0]

        assert "api_key" not in data
        assert "super-secret-key" not in str(data)
        assert data["has_api_key"] is True

    @pytest.mark.asyncio
    async def test_get_redacts_absent_secret_as_false(self, controller):
        backend = SecretBackendConfig(id="c1", name="ComfyUI", api_key=None)
        controller.backend_config_manager.get_backend.return_value = backend

        response = await controller.get_backend("c1")

        assert "api_key" not in response.data
        assert response.data["has_api_key"] is False

    @pytest.mark.asyncio
    async def test_create_response_does_not_echo_secret(self, controller):
        backend = SecretBackendConfig(id="c1", name="ComfyUI", api_key="new-key")
        controller.backend_config_manager.validate_backend_config.return_value = backend

        response = await controller.create_backend(
            {"name": "ComfyUI", "engine": "comfyui", "api_key": "new-key"}
        )

        assert "api_key" not in response.data
        assert "new-key" not in str(response.data)
        assert response.data["has_api_key"] is True

    @pytest.mark.asyncio
    async def test_update_omitting_secret_preserves_stored_value(self, controller):
        """The classic redaction-roundtrip bug: editing other fields must keep the
        stored credential when the client sends no api_key."""
        existing = SecretBackendConfig(id="c1", name="ComfyUI", api_key="stored-key")
        controller.backend_config_manager.get_backend.return_value = existing
        controller.backend_config_manager.validate_backend_config.return_value = existing

        # Client changes the name only - api_key not in the payload.
        await controller.update_backend("c1", {"name": "Renamed"})

        merged = controller.backend_config_manager.validate_backend_config.call_args[0][0]
        assert merged["api_key"] == "stored-key"
        assert merged["name"] == "Renamed"

    @pytest.mark.asyncio
    async def test_update_blank_secret_preserves_stored_value(self, controller):
        """An explicitly blank/masked secret is treated as 'unchanged', not 'clear'."""
        existing = SecretBackendConfig(id="c1", name="ComfyUI", api_key="stored-key")
        controller.backend_config_manager.get_backend.return_value = existing
        controller.backend_config_manager.validate_backend_config.return_value = existing

        await controller.update_backend("c1", {"api_key": "   "})

        merged = controller.backend_config_manager.validate_backend_config.call_args[0][0]
        assert merged["api_key"] == "stored-key"

    @pytest.mark.asyncio
    async def test_update_with_new_secret_value_replaces_it(self, controller):
        existing = SecretBackendConfig(id="c1", name="ComfyUI", api_key="stored-key")
        controller.backend_config_manager.get_backend.return_value = existing
        controller.backend_config_manager.validate_backend_config.return_value = existing

        await controller.update_backend("c1", {"api_key": "rotated-key"})

        merged = controller.backend_config_manager.validate_backend_config.call_args[0][0]
        assert merged["api_key"] == "rotated-key"


class TestBackendRequestShape:
    """
    Engine-specific settings are FLAT top-level fields on the request models,
    matching the backend config classes (and therefore the GET response shape).

    Regression: BackendCreateRequest once declared a nested `config: Dict` that
    no config class had, so Pydantic silently dropped every engine-specific
    field and a ComfyUI backend was always created pointing at 127.0.0.1:8188
    regardless of what the admin typed.
    """

    def test_create_request_preserves_engine_specific_fields(self):
        from src.features.backends.dto import BackendCreateRequest

        req = BackendCreateRequest(
            name="My ComfyUI",
            engine="comfyui",
            priority=5,
            timeout_seconds=600,
            host="192.168.1.50",
            port=9999,
            secure=True,
        )
        dumped = req.model_dump()

        assert dumped["host"] == "192.168.1.50"
        assert dumped["port"] == 9999
        assert dumped["secure"] is True
        assert dumped["timeout_seconds"] == 600
        assert "config" not in dumped

    def test_create_request_rejects_is_default(self):
        """is_default is set only via POST /{id}/set-default, never on create."""
        from src.features.backends.dto import BackendCreateRequest

        assert "is_default" not in BackendCreateRequest.model_fields

    def test_update_request_has_no_engine(self):
        """engine is immutable - it decides which config class validates the backend."""
        from src.features.backends.dto import BackendUpdateRequest

        assert "engine" not in BackendUpdateRequest.model_fields


class TestBackendOptimizations:
    """Tests for the native-engine Optimizations panel endpoints. probe/catalog/
    installer/attention are patched at their import site inside backend_controller,
    per the plan - never touching real hardware, pip, or torch."""

    @pytest.fixture
    def mock_settings_manager(self):
        return Mock(spec=SettingsManager)

    @pytest.fixture
    def mock_backend_config_manager(self):
        mock = Mock(spec=BackendConfigManager)
        mock.get_default_backend_ids.return_value = {}
        return mock

    @pytest.fixture
    def mock_backend_registry(self, mock_backend_config_manager):
        mock = Mock(spec=BackendRegistry)
        mock.refresh_backends = AsyncMock()
        mock.backend_config_manager = mock_backend_config_manager
        return mock

    @pytest.fixture
    def controller(self, mock_settings_manager, mock_backend_registry):
        return BackendController(mock_settings_manager, mock_backend_registry)

    @pytest.fixture
    def native_backend(self):
        return NativeBackendConfig(id="native-1", name="Local GPU", engine=NATIVE_ENGINE, enabled=True, priority=1)

    @pytest.fixture
    def comfyui_backend(self):
        return PluginBackendConfig(id="comfyui-1", name="ComfyUI", enabled=True, priority=2)

    @pytest.fixture
    def admin_user(self):
        user = Mock(spec=User)
        user.account_type = AccountType.ADMIN
        return user

    @pytest.fixture
    def regular_user(self):
        user = Mock(spec=User)
        user.account_type = AccountType.USER
        return user

    @pytest.fixture
    def fake_probe(self):
        return SystemProbe(cuda_available=True, compute_capability=(9, 0), active_backend="sage2",
                            available_backends=["sage2", "sage", "sdpa"])

    def _fake_status(self, opt_id="sageattention2", installable=True, installed=False, active=False):
        return OptimizationStatus(
            opt_id=opt_id, name="SageAttention 2", description="desc", benefit="benefit",
            needs_restart=False, installed=installed, installed_version=None, active=active,
            requirements=[Requirement(id="cuda_available", label="CUDA GPU", met=True, detail="")],
            installable=installable,
        )

    # ---------------------------------------------------------------- #
    # GET /{backend_id}/optimizations
    # ---------------------------------------------------------------- #

    @pytest.mark.asyncio
    async def test_get_optimizations_requires_authentication(self, controller):
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_backend_optimizations("native-1", user=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_optimizations_requires_admin(self, controller, regular_user):
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_backend_optimizations("native-1", user=regular_user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_optimizations_404_unknown_backend(self, controller, admin_user):
        controller.backend_config_manager.get_backend.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_backend_optimizations("nope", user=admin_user)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "backend_not_found"

    @pytest.mark.asyncio
    async def test_get_optimizations_400_non_native(self, controller, admin_user, comfyui_backend):
        controller.backend_config_manager.get_backend.return_value = comfyui_backend
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_backend_optimizations("comfyui-1", user=admin_user)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "optimizations_not_supported"

    @pytest.mark.asyncio
    async def test_get_optimizations_success_shape(self, controller, admin_user, native_backend, fake_probe):
        controller.backend_config_manager.get_backend.return_value = native_backend
        fake_opt = Mock()
        fake_opt.status.return_value = self._fake_status()

        with patch("src.platform.runtime.native.optimizations.probe_system", return_value=fake_probe), \
             patch("src.platform.runtime.native.optimizations.CATALOG", {"sageattention2": fake_opt}), \
             patch("src.platform.runtime.native.attention") as mock_attention:
            mock_attention.get_backend_override.return_value = None
            response = await controller.get_backend_optimizations("native-1", user=admin_user)

        assert response.success is True
        assert response.data["system"]["active_backend"] == "sage2"
        assert response.data["optimizations"][0]["opt_id"] == "sageattention2"
        assert response.data["pinned_backend"] is None

    # ---------------------------------------------------------------- #
    # POST /{backend_id}/optimizations/{opt_id}/install
    # ---------------------------------------------------------------- #

    @pytest.mark.asyncio
    async def test_install_optimization_404_unknown_opt(self, controller, admin_user, native_backend, fake_probe):
        controller.backend_config_manager.get_backend.return_value = native_backend
        with patch("src.platform.runtime.native.optimizations.probe_system", return_value=fake_probe), \
             patch("src.platform.runtime.native.optimizations.get_optimization", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await controller.install_backend_optimization("native-1", "nonexistent", user=admin_user)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "unknown_optimization"

    @pytest.mark.asyncio
    async def test_install_optimization_requirements_not_met_short_circuits(
        self, controller, admin_user, native_backend, fake_probe
    ):
        controller.backend_config_manager.get_backend.return_value = native_backend
        fake_opt = Mock()
        fake_opt.requirements.return_value = [
            Requirement(id="nvcc", label="CUDA toolkit (nvcc)", met=False, detail="install nvcc"),
        ]

        with patch("src.platform.runtime.native.optimizations.probe_system", return_value=fake_probe), \
             patch("src.platform.runtime.native.optimizations.get_optimization", return_value=fake_opt), \
             patch("src.platform.runtime.native.optimizations.installer") as mock_installer:
            with pytest.raises(HTTPException) as exc_info:
                await controller.install_backend_optimization("native-1", "sageattention2", user=admin_user)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "requirements_not_met"
        mock_installer.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_install_optimization_409_when_busy(self, controller, admin_user, native_backend, fake_probe):
        controller.backend_config_manager.get_backend.return_value = native_backend
        fake_opt = Mock()
        fake_opt.requirements.return_value = []

        with patch("src.platform.runtime.native.optimizations.probe_system", return_value=fake_probe), \
             patch("src.platform.runtime.native.optimizations.get_optimization", return_value=fake_opt), \
             patch("src.platform.runtime.native.optimizations.installer") as mock_installer:
            mock_installer.start.side_effect = RuntimeError("An installation is already in progress")
            with pytest.raises(HTTPException) as exc_info:
                await controller.install_backend_optimization("native-1", "sageattention2", user=admin_user)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "install_in_progress"

    @pytest.mark.asyncio
    async def test_install_optimization_success(self, controller, admin_user, native_backend, fake_probe):
        controller.backend_config_manager.get_backend.return_value = native_backend
        fake_opt = Mock()
        fake_opt.requirements.return_value = []
        fake_job = Mock(status="running")

        with patch("src.platform.runtime.native.optimizations.probe_system", return_value=fake_probe), \
             patch("src.platform.runtime.native.optimizations.get_optimization", return_value=fake_opt), \
             patch("src.platform.runtime.native.optimizations.installer") as mock_installer:
            mock_installer.start.return_value = fake_job
            response = await controller.install_backend_optimization("native-1", "sageattention2", user=admin_user)

        assert response.success is True
        assert response.data == {"opt_id": "sageattention2", "status": "running"}
        mock_installer.start.assert_called_once_with(fake_opt, fake_probe)

    # ---------------------------------------------------------------- #
    # GET /{backend_id}/optimizations/jobs/current
    # ---------------------------------------------------------------- #

    @pytest.mark.asyncio
    async def test_get_current_job_no_job_active_false(self, controller, admin_user, native_backend):
        controller.backend_config_manager.get_backend.return_value = native_backend
        with patch("src.platform.runtime.native.optimizations.installer") as mock_installer:
            mock_installer.current_job = None
            response = await controller.get_current_optimization_job("native-1", offset=0, user=admin_user)

        assert response.success is True
        assert response.data == {"active": False}

    @pytest.mark.asyncio
    async def test_get_current_job_returns_lines_after_offset(self, controller, admin_user, native_backend):
        controller.backend_config_manager.get_backend.return_value = native_backend
        fake_job = Mock(opt_id="sageattention2", status="running", result=None, error=None)
        fake_job.log = [(1.0, "line1"), (2.0, "line2"), (3.0, "line3")]

        with patch("src.platform.runtime.native.optimizations.installer") as mock_installer:
            mock_installer.current_job = fake_job
            response = await controller.get_current_optimization_job("native-1", offset=1, user=admin_user)

        assert response.data["active"] is True
        assert response.data["log"] == ["line2", "line3"]
        assert response.data["next_offset"] == 3

    # ---------------------------------------------------------------- #
    # POST /{backend_id}/optimizations/jobs/current/cancel
    # ---------------------------------------------------------------- #

    @pytest.mark.asyncio
    async def test_cancel_current_job(self, controller, admin_user, native_backend):
        controller.backend_config_manager.get_backend.return_value = native_backend
        with patch("src.platform.runtime.native.optimizations.installer") as mock_installer:
            mock_installer.cancel = AsyncMock(return_value=True)
            response = await controller.cancel_current_optimization_job("native-1", user=admin_user)

        assert response.success is True
        assert response.data == {"cancelled": True}

    # ---------------------------------------------------------------- #
    # PUT /{backend_id}/optimizations/attention-backend
    # ---------------------------------------------------------------- #

    @pytest.mark.asyncio
    async def test_set_attention_backend_rejects_invalid_name(self, controller, admin_user, native_backend):
        controller.backend_config_manager.get_backend.return_value = native_backend
        with pytest.raises(HTTPException) as exc_info:
            await controller.set_attention_backend("native-1", "not-a-real-backend", user=admin_user)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "invalid_backend"

    @pytest.mark.asyncio
    async def test_set_attention_backend_success_persists_and_sets_override(
        self, controller, admin_user, native_backend
    ):
        controller.backend_config_manager.get_backend.return_value = native_backend

        with patch("src.platform.runtime.native.attention") as mock_attention:
            mock_attention.known_backends.return_value = frozenset({"sage2", "sage", "flash", "sdpa"})
            mock_attention.get_backend_override.return_value = "flash"
            mock_attention.get_attention_backend.return_value = "flash"

            response = await controller.set_attention_backend("native-1", "flash", user=admin_user)

        controller.settings_manager.set_setting.assert_called_once_with("native_attention_backend", "flash")
        mock_attention.set_backend_override.assert_called_once_with("flash")
        assert response.data == {"pinned_backend": "flash", "active_backend": "flash"}

    @pytest.mark.asyncio
    async def test_set_attention_backend_auto_persists_empty_string(self, controller, admin_user, native_backend):
        controller.backend_config_manager.get_backend.return_value = native_backend

        with patch("src.platform.runtime.native.attention") as mock_attention:
            mock_attention.known_backends.return_value = frozenset({"sage2", "sage", "flash", "sdpa"})
            mock_attention.get_backend_override.return_value = None
            mock_attention.get_attention_backend.return_value = "sage2"

            await controller.set_attention_backend("native-1", "auto", user=admin_user)

        controller.settings_manager.set_setting.assert_called_once_with("native_attention_backend", "")
        mock_attention.set_backend_override.assert_called_once_with("")

    @pytest.mark.asyncio
    async def test_set_attention_backend_accepts_pin_only_backend_via_mocked_known_backends(
        self, controller, admin_user, native_backend
    ):
        # A PIN_ONLY backend (e.g. "sparge") is never in BACKEND_PRIORITY, but
        # IS in attention.known_backends() -- the endpoint must validate against
        # the latter (the regression this test guards: it used to validate
        # against BACKEND_PRIORITY alone and reject every pin-only name).
        controller.backend_config_manager.get_backend.return_value = native_backend

        with patch("src.platform.runtime.native.attention") as mock_attention:
            mock_attention.known_backends.return_value = frozenset({"sage2", "sage", "flash", "sdpa", "sparge"})
            mock_attention.get_backend_override.return_value = "sparge"
            mock_attention.get_attention_backend.return_value = "sparge"

            response = await controller.set_attention_backend("native-1", "sparge", user=admin_user)

        controller.settings_manager.set_setting.assert_called_once_with("native_attention_backend", "sparge")
        mock_attention.set_backend_override.assert_called_once_with("sparge")
        assert response.data == {"pinned_backend": "sparge", "active_backend": "sparge"}

    @pytest.mark.asyncio
    async def test_set_attention_backend_accepts_sparge_against_real_dispatcher(
        self, controller, admin_user, native_backend
    ):
        # Same regression, but exercised against the REAL src.platform.runtime.native.attention
        # module (no mocking) -- "sparge" must be accepted as a name regardless of
        # whether the sparge package is actually installed on this machine; pin
        # validity is about NAME recognition, not runtime availability (an
        # unavailable pin still falls back to sdpa with a warning at dispatch time).
        controller.backend_config_manager.get_backend.return_value = native_backend
        response = await controller.set_attention_backend("native-1", "sparge", user=admin_user)
        assert response.success is True
        controller.settings_manager.set_setting.assert_called_once_with("native_attention_backend", "sparge")

    @pytest.mark.asyncio
    async def test_set_attention_backend_still_rejects_unknown_name_with_real_dispatcher(
        self, controller, admin_user, native_backend
    ):
        controller.backend_config_manager.get_backend.return_value = native_backend
        with pytest.raises(HTTPException) as exc_info:
            await controller.set_attention_backend("native-1", "definitely-not-a-backend", user=admin_user)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "invalid_backend"

    # ---------------------------------------------------------------- #
    # POST /{backend_id}/optimizations/benchmark
    # ---------------------------------------------------------------- #

    @pytest.mark.asyncio
    async def test_benchmark_requires_authentication(self, controller):
        with pytest.raises(HTTPException) as exc_info:
            await controller.benchmark_backend_optimizations("native-1", user=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_benchmark_requires_admin(self, controller, regular_user):
        with pytest.raises(HTTPException) as exc_info:
            await controller.benchmark_backend_optimizations("native-1", user=regular_user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_benchmark_404_unknown_backend(self, controller, admin_user):
        controller.backend_config_manager.get_backend.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await controller.benchmark_backend_optimizations("nope", user=admin_user)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "backend_not_found"

    @pytest.mark.asyncio
    async def test_benchmark_400_non_native(self, controller, admin_user, comfyui_backend):
        controller.backend_config_manager.get_backend.return_value = comfyui_backend
        with pytest.raises(HTTPException) as exc_info:
            await controller.benchmark_backend_optimizations("comfyui-1", user=admin_user)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "optimizations_not_supported"

    @pytest.mark.asyncio
    async def test_benchmark_409_when_already_running(self, controller, admin_user, native_backend):
        controller.backend_config_manager.get_backend.return_value = native_backend
        with patch(
            "src.platform.runtime.native.optimizations.run_benchmark",
            new=AsyncMock(side_effect=RuntimeError("benchmark already running")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await controller.benchmark_backend_optimizations("native-1", user=admin_user)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "benchmark_in_progress"

    @pytest.mark.asyncio
    async def test_benchmark_400_when_cuda_unavailable(self, controller, admin_user, native_backend):
        controller.backend_config_manager.get_backend.return_value = native_backend
        with patch(
            "src.platform.runtime.native.optimizations.run_benchmark",
            new=AsyncMock(side_effect=RuntimeError("Attention benchmark requires a CUDA device")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await controller.benchmark_backend_optimizations("native-1", user=admin_user)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "cuda_unavailable"

    @pytest.mark.asyncio
    async def test_benchmark_success_shape(self, controller, admin_user, native_backend):
        controller.backend_config_manager.get_backend.return_value = native_backend
        fake_result = {
            "dtype": "bfloat16",
            "shape": [1, 48, 8192, 128],
            "iterations": 20,
            "active_backend": "sage2",
            "results": [
                {"backend": "sdpa", "ms": 2.5, "speedup": 1.0, "ok": True, "error": None},
                {"backend": "sage2", "ms": 1.1, "speedup": 2.27, "ok": True, "error": None},
            ],
        }
        with patch(
            "src.platform.runtime.native.optimizations.run_benchmark",
            new=AsyncMock(return_value=fake_result),
        ):
            response = await controller.benchmark_backend_optimizations("native-1", user=admin_user)

        assert response.success is True
        assert response.data == fake_result


class TestBackendQuickActions:
    """The native engine describes its own admin actions (formerly the
    standalone `clear-local-vram` marketplace plugin); other engines default
    to none unless they override quick_actions() themselves."""

    def test_native_backend_declares_clear_vram_and_restart(self):
        backend = NativeBackendConfig(id="native-1", name="Local GPU", engine=NATIVE_ENGINE, enabled=True, priority=1)
        actions = backend.quick_actions()
        by_id = {a["id"]: a for a in actions}

        assert by_id["clear-vram"]["endpoint"] == "/api/backends/native-1/actions/clear-vram"
        assert by_id["clear-vram"]["method"] == "POST"
        assert by_id["clear-vram"]["poll_health_after"] is False
        assert by_id["clear-vram"]["label"] == "Clear VRAM"
        assert "RAM cache stays warm" in by_id["clear-vram"]["confirm"]

        assert by_id["clear-cache"]["endpoint"] == "/api/backends/native-1/actions/clear-cache"
        assert by_id["clear-cache"]["method"] == "POST"
        assert by_id["clear-cache"]["danger"] is True
        assert by_id["clear-cache"]["poll_health_after"] is False
        assert by_id["clear-cache"]["label"] == "Clear VRAM & Cache (RAM)"
        assert "reload models from disk" in by_id["clear-cache"]["confirm"]

        assert by_id["restart-backend"]["endpoint"] == "/api/admin/restart"
        assert by_id["restart-backend"]["danger"] is True
        assert by_id["restart-backend"]["poll_health_after"] is True

    def test_plugin_backend_has_no_quick_actions_by_default(self):
        backend = PluginBackendConfig(id="comfyui-1", name="ComfyUI", enabled=True, priority=2)
        assert backend.quick_actions() == []

    @pytest.mark.asyncio
    async def test_get_backend_response_includes_quick_actions(self):
        settings_manager = Mock(spec=SettingsManager)
        backend_config_manager = Mock(spec=BackendConfigManager)
        backend_config_manager.get_default_backend_ids.return_value = {}
        backend_registry = Mock(spec=BackendRegistry)
        backend_registry.backend_config_manager = backend_config_manager

        controller = BackendController(settings_manager, backend_registry)
        native_backend = NativeBackendConfig(id="native-1", name="Local GPU", engine=NATIVE_ENGINE, enabled=True, priority=1)
        backend_config_manager.get_backend.return_value = native_backend

        response = await controller.get_backend("native-1")

        action_ids = {a["id"] for a in response.data["quick_actions"]}
        assert action_ids == {"clear-vram", "clear-cache", "restart-backend"}


class TestClearBackendVram:
    """POST /api/backends/{backend_id}/actions/clear-vram - VRAM-only teardown.

    Offloads GPU-resident weights to host RAM via GpuResidencyManager; the
    ModelLifecycleManager RAM cache is never evicted, only gc'd/emptied.
    """

    @pytest.fixture
    def mock_settings_manager(self):
        return Mock(spec=SettingsManager)

    @pytest.fixture
    def mock_backend_config_manager(self):
        mock = Mock(spec=BackendConfigManager)
        mock.get_default_backend_ids.return_value = {}
        return mock

    @pytest.fixture
    def mock_backend_registry(self, mock_backend_config_manager):
        mock = Mock(spec=BackendRegistry)
        mock.refresh_backends = AsyncMock()
        mock.backend_config_manager = mock_backend_config_manager
        return mock

    @pytest.fixture
    def mock_model_lifecycle_manager(self):
        mock = Mock()
        mock.stats.return_value = {"keys": ["sdxl-checkpoint"]}
        mock.invalidate = Mock()
        mock.cleanup = Mock()
        mock.leased_values.return_value = []
        mock.cached_values.return_value = []
        return mock

    @pytest.fixture
    def controller(self, mock_settings_manager, mock_backend_registry, mock_model_lifecycle_manager):
        return BackendController(mock_settings_manager, mock_backend_registry, mock_model_lifecycle_manager)

    @pytest.fixture
    def native_backend(self):
        return NativeBackendConfig(id="native-1", name="Local GPU", engine=NATIVE_ENGINE, enabled=True, priority=1)

    @pytest.fixture
    def comfyui_backend(self):
        return PluginBackendConfig(id="comfyui-1", name="ComfyUI", enabled=True, priority=2)

    @pytest.fixture
    def admin_user(self):
        user = Mock(spec=User)
        user.account_type = AccountType.ADMIN
        return user

    @pytest.fixture
    def regular_user(self):
        user = Mock(spec=User)
        user.account_type = AccountType.USER
        return user

    @pytest.fixture
    def mock_residency_manager(self):
        from src.platform.runtime.native.memory.residency import OffloadResult
        mock = Mock()
        mock.offload_all.return_value = OffloadResult(["dit", "vae"], freed_gb=8.0)
        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=mock,
        ):
            yield mock

    @pytest.mark.asyncio
    async def test_requires_authentication(self, controller):
        with pytest.raises(HTTPException) as exc_info:
            await controller.clear_backend_vram("native-1", user=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_requires_admin(self, controller, regular_user):
        with pytest.raises(HTTPException) as exc_info:
            await controller.clear_backend_vram("native-1", user=regular_user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_404_unknown_backend(self, controller, admin_user):
        controller.backend_config_manager.get_backend.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await controller.clear_backend_vram("nope", user=admin_user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_400_non_native(self, controller, admin_user, comfyui_backend):
        controller.backend_config_manager.get_backend.return_value = comfyui_backend
        with pytest.raises(HTTPException) as exc_info:
            await controller.clear_backend_vram("comfyui-1", user=admin_user)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "optimizations_not_supported"

    @pytest.mark.asyncio
    async def test_success_offloads_gpu_residents_without_evicting_cache(
        self, controller, admin_user, native_backend, mock_model_lifecycle_manager, mock_residency_manager
    ):
        controller.backend_config_manager.get_backend.return_value = native_backend

        response = await controller.clear_backend_vram("native-1", user=admin_user)

        assert response.success is True
        assert response.data["offloaded_count"] == 2
        assert response.data["swept_count"] == 0
        mock_residency_manager.offload_all.assert_called_once_with(native_backend.device, exclude=[])
        mock_model_lifecycle_manager.cleanup.assert_called_once_with(aggressive=False)
        mock_model_lifecycle_manager.invalidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_succeeds_without_a_model_lifecycle_manager(
        self, admin_user, native_backend, mock_settings_manager, mock_backend_registry, mock_residency_manager
    ):
        # cleanup() is a nice-to-have, not required - offloading GPU residents
        # is the whole point of this action and doesn't depend on it.
        controller = BackendController(mock_settings_manager, mock_backend_registry, model_lifecycle_manager=None)
        controller.backend_config_manager.get_backend.return_value = native_backend

        response = await controller.clear_backend_vram("native-1", user=admin_user)

        assert response.success is True
        mock_residency_manager.offload_all.assert_called_once_with(native_backend.device, exclude=[])


class TestClearBackendCache:
    """POST /api/backends/{backend_id}/actions/clear-cache - VRAM & RAM cache teardown."""

    @pytest.fixture
    def mock_settings_manager(self):
        return Mock(spec=SettingsManager)

    @pytest.fixture
    def mock_backend_config_manager(self):
        mock = Mock(spec=BackendConfigManager)
        mock.get_default_backend_ids.return_value = {}
        return mock

    @pytest.fixture
    def mock_backend_registry(self, mock_backend_config_manager):
        mock = Mock(spec=BackendRegistry)
        mock.refresh_backends = AsyncMock()
        mock.backend_config_manager = mock_backend_config_manager
        return mock

    @pytest.fixture
    def mock_model_lifecycle_manager(self):
        mock = Mock()
        mock.stats.return_value = {"keys": ["sdxl-checkpoint"]}
        mock.invalidate = Mock()
        return mock

    @pytest.fixture
    def controller(self, mock_settings_manager, mock_backend_registry, mock_model_lifecycle_manager):
        return BackendController(mock_settings_manager, mock_backend_registry, mock_model_lifecycle_manager)

    @pytest.fixture
    def native_backend(self):
        return NativeBackendConfig(id="native-1", name="Local GPU", engine=NATIVE_ENGINE, enabled=True, priority=1)

    @pytest.fixture
    def comfyui_backend(self):
        return PluginBackendConfig(id="comfyui-1", name="ComfyUI", enabled=True, priority=2)

    @pytest.fixture
    def admin_user(self):
        user = Mock(spec=User)
        user.account_type = AccountType.ADMIN
        return user

    @pytest.fixture
    def regular_user(self):
        user = Mock(spec=User)
        user.account_type = AccountType.USER
        return user

    @pytest.mark.asyncio
    async def test_requires_authentication(self, controller):
        with pytest.raises(HTTPException) as exc_info:
            await controller.clear_backend_cache("native-1", user=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_requires_admin(self, controller, regular_user):
        with pytest.raises(HTTPException) as exc_info:
            await controller.clear_backend_cache("native-1", user=regular_user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_404_unknown_backend(self, controller, admin_user):
        controller.backend_config_manager.get_backend.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await controller.clear_backend_cache("nope", user=admin_user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_400_non_native(self, controller, admin_user, comfyui_backend):
        controller.backend_config_manager.get_backend.return_value = comfyui_backend
        with pytest.raises(HTTPException) as exc_info:
            await controller.clear_backend_cache("comfyui-1", user=admin_user)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "optimizations_not_supported"

    @pytest.mark.asyncio
    async def test_success_invalidates_model_lifecycle_manager(
        self, controller, admin_user, native_backend, mock_model_lifecycle_manager
    ):
        controller.backend_config_manager.get_backend.return_value = native_backend

        response = await controller.clear_backend_cache("native-1", user=admin_user)

        assert response.success is True
        assert response.data["cache_keys_cleared"] == ["sdxl-checkpoint"]
        mock_model_lifecycle_manager.invalidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_model_lifecycle_manager_errors_cleanly(self, admin_user, native_backend, mock_settings_manager, mock_backend_registry):
        controller = BackendController(mock_settings_manager, mock_backend_registry, model_lifecycle_manager=None)
        controller.backend_config_manager.get_backend.return_value = native_backend

        with pytest.raises(HTTPException) as exc_info:
            await controller.clear_backend_cache("native-1", user=admin_user)
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail["error"] == "model_lifecycle_unavailable"
