"""Tests for LLMManager."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any

from src.features.llm.manager import LLMManager
from src.features.llm.exceptions import (
    ConfigurationNotFoundException,
    ConfigurationExistsException,
    ConfigurationCreationFailedException,
    CannotDeleteDefaultConfigException,
    VisionNotSupportedException,
    GenerationFailedException,
    AssignmentNotFoundException,
    AssignmentFailedException,
)
from src.features.llm.dto import (
    LLMConfigRequest,
    LLMConfigResponse,
    LLMGenerateRequest,
)
from src.features.llm.repository import LLMConfig
from src.features.llm.clients.base import LLMResponse as ServiceLLMResponse


class TestLLMManager:
    """Tests for the LLMManager class."""

    @pytest.fixture
    def mock_llm_repository(self):
        """Create a mock LLM repository."""
        mock = Mock()
        mock.default_provider = "default-config-123"
        return mock

    @pytest.fixture
    def mock_llm_service(self):
        """Create a mock LLM service."""
        mock = Mock()
        mock.generate_response = AsyncMock()
        mock.test_configuration = AsyncMock()
        return mock

    @pytest.fixture
    def mock_settings_manager(self):
        """Create a mock settings manager."""
        mock = Mock()
        mock.get_file_storage_directory = Mock(return_value="/test/storage")
        return mock

    @pytest.fixture
    def mock_plugin_registry(self):
        """Create a mock plugin registry."""
        mock = Mock()
        # Default: hooks don't block
        mock.execute_hook = Mock(return_value=(
            Mock(data={"blocked": False}),
            []
        ))
        return mock

    @pytest.fixture
    def manager(self, mock_llm_repository, mock_llm_service, mock_settings_manager, mock_plugin_registry):
        """Create an LLMManager instance with mocked dependencies."""
        return LLMManager(
            llm_repository=mock_llm_repository,
            llm_service=mock_llm_service,
            settings_manager=mock_settings_manager,
            plugin_registry=mock_plugin_registry
        )

    @pytest.fixture
    def sample_llm_config(self):
        """Create a sample LLM configuration."""
        return LLMConfig(
            id="test-config-123",
            name="Test LLM",
            type="ollama",
            enabled=True,
            base_url="http://localhost:11434",
            api_key=None,
            model="llama2",
            system_message="You are a helpful assistant",
            temperature=0.7,
            max_tokens=1000,
            timeout=30,
            supports_vision=False
        )

    @pytest.fixture
    def sample_config_request(self):
        """Create a sample configuration request."""
        return LLMConfigRequest(
            name="Test LLM",
            type="ollama",
            enabled=True,
            base_url="http://localhost:11434",
            model="llama2",
            system_message="You are a helpful assistant",
            temperature=0.7,
            max_tokens=1000,
            timeout=30
        )

    # =========================================================================
    # Configuration Management Tests
    # =========================================================================

    def test_get_all_configurations(self, manager, sample_llm_config):
        """Test getting all configurations."""
        manager.repository.get_all_configurations.return_value = {
            "test-config-123": sample_llm_config
        }
        manager.repository.default_provider = "test-config-123"

        result = manager.get_all_configurations()

        assert "configurations" in result
        assert "default_provider" in result
        assert len(result["configurations"]) == 1
        assert result["configurations"][0].id == "test-config-123"
        assert result["configurations"][0].is_default is True

    def test_get_configuration_success(self, manager, sample_llm_config):
        """Test getting a specific configuration."""
        manager.repository.get_configuration.return_value = sample_llm_config
        manager.repository.default_provider = "other-config"

        result = manager.get_configuration("test-config-123")

        assert isinstance(result, LLMConfigResponse)
        assert result.id == "test-config-123"
        assert result.is_default is False

    def test_get_configuration_not_found(self, manager):
        """Test getting a non-existent configuration."""
        manager.repository.get_configuration.return_value = None

        with pytest.raises(ConfigurationNotFoundException):
            manager.get_configuration("nonexistent")

    def test_get_configuration_redacts_api_key(self, manager, sample_llm_config):
        """The response never carries the API key, only api_key_set."""
        sample_llm_config.api_key = "super-secret"
        manager.repository.get_configuration.return_value = sample_llm_config
        manager.repository.default_provider = "other-config"

        result = manager.get_configuration("test-config-123")

        assert result.api_key_set is True
        assert "api_key" not in result.model_dump()
        assert "super-secret" not in str(result.model_dump())

    def test_get_all_configurations_redacts_api_key(self, manager, sample_llm_config):
        """List responses report api_key_set and never expose the key."""
        sample_llm_config.api_key = "super-secret"
        manager.repository.get_all_configurations.return_value = {"test-config-123": sample_llm_config}
        manager.repository.default_provider = "test-config-123"

        result = manager.get_all_configurations()

        cfg = result["configurations"][0]
        assert cfg.api_key_set is True
        assert "api_key" not in cfg.model_dump()

    def test_update_configuration_keeps_key_on_sentinel(self, manager, sample_config_request, sample_llm_config):
        """A '__unchanged__' api_key on update keeps the stored key."""
        sample_llm_config.api_key = "stored-key"
        manager.repository.get_configuration.return_value = sample_llm_config
        manager.repository.update_configuration.return_value = True
        sample_config_request.api_key = "__unchanged__"

        manager.update_configuration("test-config-123", sample_config_request)

        saved = manager.repository.update_configuration.call_args[0][1]
        assert saved.api_key == "stored-key"

    def test_update_configuration_keeps_key_on_empty(self, manager, sample_config_request, sample_llm_config):
        """An empty api_key on update keeps the stored key."""
        sample_llm_config.api_key = "stored-key"
        manager.repository.get_configuration.return_value = sample_llm_config
        manager.repository.update_configuration.return_value = True
        sample_config_request.api_key = ""

        manager.update_configuration("test-config-123", sample_config_request)

        saved = manager.repository.update_configuration.call_args[0][1]
        assert saved.api_key == "stored-key"

    def test_update_configuration_replaces_key_with_real_value(self, manager, sample_config_request, sample_llm_config):
        """A real, non-sentinel api_key replaces the stored key."""
        sample_llm_config.api_key = "stored-key"
        manager.repository.get_configuration.return_value = sample_llm_config
        manager.repository.update_configuration.return_value = True
        sample_config_request.api_key = "brand-new-key"

        manager.update_configuration("test-config-123", sample_config_request)

        saved = manager.repository.update_configuration.call_args[0][1]
        assert saved.api_key == "brand-new-key"

    def test_create_configuration_success(self, manager, sample_config_request):
        """Test creating a configuration."""
        manager.repository.get_configuration.return_value = None
        manager.repository.create_configuration.return_value = True

        with patch('src.features.llm.manager.generate_ulid', return_value="new-config-123"):
            result = manager.create_configuration(sample_config_request)

        assert result == "new-config-123"
        manager.repository.create_configuration.assert_called_once()

    def test_create_configuration_already_exists(self, manager, sample_config_request, sample_llm_config):
        """Test creating a configuration that already exists."""
        sample_config_request.id = "existing-config"
        manager.repository.get_configuration.return_value = sample_llm_config

        with pytest.raises(ConfigurationExistsException):
            manager.create_configuration(sample_config_request)

    def test_create_configuration_blocked_by_hook(self, manager, sample_config_request, mock_plugin_registry):
        """Test that hooks can block configuration creation."""
        mock_plugin_registry.execute_hook.return_value = (
            Mock(data={"blocked": True, "block_reason": "Not allowed"}),
            []
        )

        with pytest.raises(ConfigurationCreationFailedException) as exc_info:
            manager.create_configuration(sample_config_request)
        assert "Not allowed" in str(exc_info.value)

    def test_delete_configuration_success(self, manager, sample_llm_config):
        """Test deleting a configuration."""
        manager.repository.get_configuration.return_value = sample_llm_config
        manager.repository.default_provider = "other-config"
        manager.repository.delete_configuration.return_value = True

        result = manager.delete_configuration("test-config-123")

        assert result == "test-config-123"

    def test_delete_configuration_not_found(self, manager):
        """Test deleting a non-existent configuration."""
        manager.repository.get_configuration.return_value = None

        with pytest.raises(ConfigurationNotFoundException):
            manager.delete_configuration("nonexistent")

    def test_delete_default_configuration(self, manager, sample_llm_config):
        """Test that default configuration cannot be deleted."""
        manager.repository.get_configuration.return_value = sample_llm_config
        manager.repository.default_provider = "test-config-123"

        with pytest.raises(CannotDeleteDefaultConfigException):
            manager.delete_configuration("test-config-123")

    def test_set_default_provider_success(self, manager, sample_llm_config):
        """Test setting the default provider."""
        manager.repository.get_configuration.return_value = sample_llm_config
        manager.repository.set_default_provider.return_value = True

        result = manager.set_default_provider("test-config-123")

        assert result == "test-config-123"

    # =========================================================================
    # Generation Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_generate_response_success(self, manager, sample_llm_config):
        """Test successful response generation."""
        request = LLMGenerateRequest(prompt="Hello, world!")
        manager.repository.get_default_configuration.return_value = sample_llm_config

        service_response = ServiceLLMResponse(
            content="Hello! How can I help you?",
            model="llama2",
            provider_id="test-config-123",
            tokens_used=50
        )
        manager.llm_service.generate_response.return_value = service_response

        result = await manager.generate_response(request, "user-123")

        assert result.content == "Hello! How can I help you?"
        assert result.model == "llama2"

    @pytest.mark.asyncio
    async def test_generate_response_no_config(self, manager):
        """Test generation with no configuration."""
        request = LLMGenerateRequest(prompt="Hello")
        manager.repository.get_default_configuration.return_value = None

        with pytest.raises(ConfigurationNotFoundException):
            await manager.generate_response(request, "user-123")

    @pytest.mark.asyncio
    async def test_generate_response_vision_not_supported(self, manager, sample_llm_config):
        """Test generation with image when vision not supported."""
        sample_llm_config.supports_vision = False
        request = LLMGenerateRequest(prompt="Describe this", image_data="base64data")
        manager.repository.get_default_configuration.return_value = sample_llm_config

        with pytest.raises(VisionNotSupportedException):
            await manager.generate_response(request, "user-123")

    @pytest.mark.asyncio
    async def test_generate_response_removes_thinking_tags(self, manager, sample_llm_config):
        """Test that thinking tags are removed from response."""
        request = LLMGenerateRequest(prompt="Explain something")
        manager.repository.get_default_configuration.return_value = sample_llm_config

        service_response = ServiceLLMResponse(
            content="<think>Internal thought</think>Actual response",
            model="llama2",
            provider_id="test-config-123",
            tokens_used=50
        )
        manager.llm_service.generate_response.return_value = service_response

        result = await manager.generate_response(request, "user-123")

        assert "<think>" not in result.content
        assert "Internal thought" not in result.content
        assert "Actual response" in result.content

    @pytest.mark.asyncio
    async def test_generate_response_blocked_by_hook(self, manager, sample_llm_config, mock_plugin_registry):
        """Test that hooks can block generation."""
        request = LLMGenerateRequest(prompt="Blocked prompt")
        manager.repository.get_default_configuration.return_value = sample_llm_config

        # First call is for before_generate hook, make it block
        mock_plugin_registry.execute_hook.return_value = (
            Mock(data={"blocked": True, "block_reason": "Content not allowed"}),
            []
        )

        with pytest.raises(GenerationFailedException) as exc_info:
            await manager.generate_response(request, "user-123")
        assert "Content not allowed" in str(exc_info.value)

    # =========================================================================
    # User Assignment Tests
    # =========================================================================

    def test_assign_llm_to_user_success(self, manager, sample_llm_config):
        """Test assigning LLM to user."""
        manager.repository.get_configuration.return_value = sample_llm_config
        manager.repository.assign_llm_to_user.return_value = True

        result = manager.assign_llm_to_user("user-123", "test-config-123")

        assert result["user_id"] == "user-123"
        assert result["llm_config_id"] == "test-config-123"

    def test_assign_llm_to_user_config_not_found(self, manager):
        """Test assigning non-existent LLM to user."""
        manager.repository.get_configuration.return_value = None

        with pytest.raises(ConfigurationNotFoundException):
            manager.assign_llm_to_user("user-123", "nonexistent")

    def test_unassign_llm_from_user_success(self, manager):
        """Test unassigning LLM from user."""
        manager.repository.is_llm_assigned_to_user.return_value = True
        manager.repository.unassign_llm_from_user.return_value = True

        result = manager.unassign_llm_from_user("user-123", "test-config-123")

        assert result["user_id"] == "user-123"
        assert result["llm_config_id"] == "test-config-123"

    def test_unassign_llm_from_user_not_assigned(self, manager):
        """Test unassigning LLM that's not assigned."""
        manager.repository.is_llm_assigned_to_user.return_value = False

        with pytest.raises(AssignmentNotFoundException):
            manager.unassign_llm_from_user("user-123", "test-config-123")

    def test_get_user_llm_assignments(self, manager, sample_llm_config):
        """Test getting user's LLM assignments."""
        manager.repository.get_user_llm_configurations.return_value = {
            "test-config-123": sample_llm_config
        }

        result = manager.get_user_llm_assignments("user-123")

        assert result["user_id"] == "user-123"
        assert len(result["llm_configs"]) == 1

    def test_get_user_llm_assignments_redacts_api_key(self, manager, sample_llm_config):
        """A user's assignment list reports api_key_set and never the key."""
        sample_llm_config.api_key = "super-secret"
        manager.repository.get_user_llm_configurations.return_value = {
            "test-config-123": sample_llm_config
        }

        result = manager.get_user_llm_assignments("user-123")

        cfg = result["llm_configs"][0]
        assert cfg["api_key_set"] is True
        assert "api_key" not in cfg
        assert "super-secret" not in str(result)

    def test_get_llm_assignments_success(self, manager, sample_llm_config):
        """Test getting the users directly assigned to an LLM configuration."""
        manager.repository.get_configuration.return_value = sample_llm_config
        manager.repository.get_llm_users.return_value = ["user-a", "user-b"]

        result = manager.get_llm_assignments("test-config-123")

        assert result["llm_config_id"] == "test-config-123"
        assert result["assignments"] == [{"user_id": "user-a"}, {"user_id": "user-b"}]

    def test_get_llm_assignments_config_not_found(self, manager):
        """Test getting assignments for a nonexistent LLM configuration."""
        manager.repository.get_configuration.return_value = None

        with pytest.raises(ConfigurationNotFoundException):
            manager.get_llm_assignments("nonexistent")

    def test_get_assignment_summary(self, manager):
        """Test getting the per-configuration assignment-count summary."""
        manager.repository.get_llm_assignment_summary.return_value = {
            "config-1": {"assignment_count": 2, "group_count": 1}
        }

        result = manager.get_assignment_summary()

        assert result == {"config-1": {"assignment_count": 2, "group_count": 1}}

    def test_get_all_user_llm_assignments_redacts_api_key(self, manager, sample_llm_config):
        """The admin all-assignments view reports api_key_set and never the key."""
        sample_llm_config.api_key = "super-secret"
        manager.repository.get_all_user_llm_assignments.return_value = {
            "user-123": ["test-config-123"]
        }
        manager.repository.get_all_configurations.return_value = {
            "test-config-123": sample_llm_config
        }

        result = manager.get_all_user_llm_assignments()

        cfg = result["assignments"][0]["llm_configs"][0]
        assert cfg["api_key_set"] is True
        assert "api_key" not in cfg
        assert "super-secret" not in str(result)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
