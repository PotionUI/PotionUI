"""Tests for src.features.llm.operations (formerly LLMManager)."""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.llm import operations
from src.features.llm.mappers import config_to_response, assignment_config_to_response
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
    LLMGenerateRequest,
)
from src.features.llm.repository import LLMConfig
from src.features.llm.clients.base import LLMResponse as ServiceLLMResponse


@pytest.fixture
def mock_repo():
    """Create a mock LLM repository."""
    mock = Mock()
    mock.default_provider = "default-config-123"
    return mock


@pytest.fixture
def mock_llm_service():
    """Create a mock LLM service."""
    mock = Mock()
    mock.generate_response = AsyncMock()
    mock.test_configuration = AsyncMock()
    return mock


@pytest.fixture
def mock_settings():
    """Create a mock settings manager."""
    mock = Mock()
    mock.get_file_storage_directory = Mock(return_value="/test/storage")
    return mock


@pytest.fixture
def mock_plugins():
    """Create a mock plugin registry."""
    mock = Mock()
    # Default: hooks don't block
    mock.execute_hook = Mock(return_value=(
        Mock(data={"blocked": False}),
        []
    ))
    return mock


@pytest.fixture
def sample_llm_config():
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
def sample_config_request():
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
# Mapper Tests (formerly the get_all/get_configuration redaction tests)
# =========================================================================

class TestMappers:
    def test_config_to_response_marks_default(self, sample_llm_config):
        result = config_to_response(sample_llm_config, is_default=True)

        assert result.id == "test-config-123"
        assert result.is_default is True

    def test_config_to_response_not_default(self, sample_llm_config):
        result = config_to_response(sample_llm_config, is_default=False)

        assert result.is_default is False

    def test_config_to_response_redacts_api_key(self, sample_llm_config):
        """The response never carries the API key, only api_key_set."""
        sample_llm_config.api_key = "super-secret"

        result = config_to_response(sample_llm_config, is_default=False)

        assert result.api_key_set is True
        assert "api_key" not in result.model_dump()
        assert "super-secret" not in str(result.model_dump())

    def test_assignment_config_to_response_redacts_api_key(self, sample_llm_config):
        sample_llm_config.api_key = "super-secret"

        result = assignment_config_to_response(sample_llm_config)

        assert result.api_key_set is True
        assert result.is_default is False
        assert "api_key" not in result.model_dump()
        assert "super-secret" not in str(result.model_dump())


# =========================================================================
# Configuration Management Tests
# =========================================================================

class TestConfigurationOperations:
    def test_update_configuration_keeps_key_on_sentinel(self, mock_repo, mock_plugins, sample_config_request, sample_llm_config):
        """A '__unchanged__' api_key on update keeps the stored key."""
        sample_llm_config.api_key = "stored-key"
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_repo.update_configuration.return_value = True
        sample_config_request.api_key = "__unchanged__"

        operations.update_configuration(mock_repo, mock_plugins, "test-config-123", sample_config_request)

        saved = mock_repo.update_configuration.call_args[0][1]
        assert saved.api_key == "stored-key"

    def test_update_configuration_keeps_key_on_empty(self, mock_repo, mock_plugins, sample_config_request, sample_llm_config):
        """An empty api_key on update keeps the stored key."""
        sample_llm_config.api_key = "stored-key"
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_repo.update_configuration.return_value = True
        sample_config_request.api_key = ""

        operations.update_configuration(mock_repo, mock_plugins, "test-config-123", sample_config_request)

        saved = mock_repo.update_configuration.call_args[0][1]
        assert saved.api_key == "stored-key"

    def test_update_configuration_replaces_key_with_real_value(self, mock_repo, mock_plugins, sample_config_request, sample_llm_config):
        """A real, non-sentinel api_key replaces the stored key."""
        sample_llm_config.api_key = "stored-key"
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_repo.update_configuration.return_value = True
        sample_config_request.api_key = "brand-new-key"

        operations.update_configuration(mock_repo, mock_plugins, "test-config-123", sample_config_request)

        saved = mock_repo.update_configuration.call_args[0][1]
        assert saved.api_key == "brand-new-key"

    def test_create_configuration_success(self, mock_repo, mock_plugins, sample_config_request):
        """Test creating a configuration."""
        mock_repo.get_configuration.return_value = None
        mock_repo.create_configuration.return_value = True

        with patch('src.features.llm.operations.configuration.generate_ulid', return_value="new-config-123"):
            result = operations.create_configuration(mock_repo, mock_plugins, sample_config_request)

        assert result == "new-config-123"
        mock_repo.create_configuration.assert_called_once()

    def test_create_configuration_already_exists(self, mock_repo, mock_plugins, sample_config_request, sample_llm_config):
        """Test creating a configuration that already exists."""
        sample_config_request.id = "existing-config"
        mock_repo.get_configuration.return_value = sample_llm_config

        with pytest.raises(ConfigurationExistsException):
            operations.create_configuration(mock_repo, mock_plugins, sample_config_request)

    def test_create_configuration_blocked_by_hook(self, mock_repo, mock_plugins, sample_config_request):
        """Test that hooks can block configuration creation."""
        mock_plugins.execute_hook.return_value = (
            Mock(data={"blocked": True, "block_reason": "Not allowed"}),
            []
        )

        with pytest.raises(ConfigurationCreationFailedException) as exc_info:
            operations.create_configuration(mock_repo, mock_plugins, sample_config_request)
        assert "Not allowed" in str(exc_info.value)

    def test_delete_configuration_success(self, mock_repo, mock_plugins, sample_llm_config):
        """Test deleting a configuration."""
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_repo.default_provider = "other-config"
        mock_repo.delete_configuration.return_value = True

        result = operations.delete_configuration(mock_repo, mock_plugins, None, "test-config-123")

        assert result == "test-config-123"

    def test_delete_configuration_not_found(self, mock_repo, mock_plugins):
        """Test deleting a non-existent configuration."""
        mock_repo.get_configuration.return_value = None

        with pytest.raises(ConfigurationNotFoundException):
            operations.delete_configuration(mock_repo, mock_plugins, None, "nonexistent")

    def test_delete_default_configuration(self, mock_repo, mock_plugins, sample_llm_config):
        """Test that default configuration cannot be deleted."""
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_repo.default_provider = "test-config-123"

        with pytest.raises(CannotDeleteDefaultConfigException):
            operations.delete_configuration(mock_repo, mock_plugins, None, "test-config-123")

    def test_delete_configuration_removes_tool_governance_config(self, mock_repo, mock_plugins, sample_llm_config):
        """Deleting a config removes its tool-governance entry, when a
        tool_governance_repository is wired."""
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_repo.default_provider = "other-config"
        mock_repo.delete_configuration.return_value = True
        tool_governance_repository = Mock()

        operations.delete_configuration(mock_repo, mock_plugins, tool_governance_repository, "test-config-123")

        tool_governance_repository.delete_config.assert_called_once_with("test-config-123")

    def test_set_default_provider_success(self, mock_repo, sample_llm_config):
        """Test setting the default provider."""
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_repo.set_default_provider.return_value = True

        result = operations.set_default_provider(mock_repo, "test-config-123")

        assert result == "test-config-123"


# =========================================================================
# Generation Tests
# =========================================================================

class TestGenerationOperations:
    @pytest.mark.asyncio
    async def test_generate_response_success(self, mock_repo, mock_llm_service, mock_settings, mock_plugins, sample_llm_config):
        """Test successful response generation."""
        request = LLMGenerateRequest(prompt="Hello, world!")
        mock_repo.get_default_configuration.return_value = sample_llm_config

        service_response = ServiceLLMResponse(
            content="Hello! How can I help you?",
            model="llama2",
            provider_id="test-config-123",
            tokens_used=50
        )
        mock_llm_service.generate_response.return_value = service_response

        result = await operations.generate_response(
            mock_repo, mock_llm_service, mock_settings, mock_plugins, request, "user-123"
        )

        assert result.content == "Hello! How can I help you?"
        assert result.model == "llama2"

    @pytest.mark.asyncio
    async def test_generate_response_no_config(self, mock_repo, mock_llm_service, mock_settings, mock_plugins):
        """Test generation with no configuration."""
        request = LLMGenerateRequest(prompt="Hello")
        mock_repo.get_default_configuration.return_value = None

        with pytest.raises(ConfigurationNotFoundException):
            await operations.generate_response(
                mock_repo, mock_llm_service, mock_settings, mock_plugins, request, "user-123"
            )

    @pytest.mark.asyncio
    async def test_generate_response_vision_not_supported(self, mock_repo, mock_llm_service, mock_settings, mock_plugins, sample_llm_config):
        """Test generation with image when vision not supported."""
        sample_llm_config.supports_vision = False
        request = LLMGenerateRequest(prompt="Describe this", image_data="base64data")
        mock_repo.get_default_configuration.return_value = sample_llm_config

        with pytest.raises(VisionNotSupportedException):
            await operations.generate_response(
                mock_repo, mock_llm_service, mock_settings, mock_plugins, request, "user-123"
            )

    @pytest.mark.asyncio
    async def test_generate_response_removes_thinking_tags(self, mock_repo, mock_llm_service, mock_settings, mock_plugins, sample_llm_config):
        """Test that thinking tags are removed from response."""
        request = LLMGenerateRequest(prompt="Explain something")
        mock_repo.get_default_configuration.return_value = sample_llm_config

        service_response = ServiceLLMResponse(
            content="<think>Internal thought</think>Actual response",
            model="llama2",
            provider_id="test-config-123",
            tokens_used=50
        )
        mock_llm_service.generate_response.return_value = service_response

        result = await operations.generate_response(
            mock_repo, mock_llm_service, mock_settings, mock_plugins, request, "user-123"
        )

        assert "<think>" not in result.content
        assert "Internal thought" not in result.content
        assert "Actual response" in result.content

    @pytest.mark.asyncio
    async def test_generate_response_blocked_by_hook(self, mock_repo, mock_llm_service, mock_settings, mock_plugins, sample_llm_config):
        """Test that hooks can block generation."""
        request = LLMGenerateRequest(prompt="Blocked prompt")
        mock_repo.get_default_configuration.return_value = sample_llm_config

        # First call is for before_generate hook, make it block
        mock_plugins.execute_hook.return_value = (
            Mock(data={"blocked": True, "block_reason": "Content not allowed"}),
            []
        )

        with pytest.raises(GenerationFailedException) as exc_info:
            await operations.generate_response(
                mock_repo, mock_llm_service, mock_settings, mock_plugins, request, "user-123"
            )
        assert "Content not allowed" in str(exc_info.value)


# =========================================================================
# User Assignment Tests
# =========================================================================

class TestAssignmentOperations:
    def test_assign_llm_to_user_success(self, mock_repo, sample_llm_config):
        """Test assigning LLM to user."""
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_repo.assign_llm_to_user.return_value = True

        result = operations.assign_llm_to_user(mock_repo, "user-123", "test-config-123")

        assert result["user_id"] == "user-123"
        assert result["llm_config_id"] == "test-config-123"

    def test_assign_llm_to_user_config_not_found(self, mock_repo):
        """Test assigning non-existent LLM to user."""
        mock_repo.get_configuration.return_value = None

        with pytest.raises(ConfigurationNotFoundException):
            operations.assign_llm_to_user(mock_repo, "user-123", "nonexistent")

    def test_unassign_llm_from_user_success(self, mock_repo):
        """Test unassigning LLM from user."""
        mock_repo.is_llm_assigned_to_user.return_value = True
        mock_repo.unassign_llm_from_user.return_value = True

        result = operations.unassign_llm_from_user(mock_repo, "user-123", "test-config-123")

        assert result["user_id"] == "user-123"
        assert result["llm_config_id"] == "test-config-123"

    def test_unassign_llm_from_user_not_assigned(self, mock_repo):
        """Test unassigning LLM that's not assigned."""
        mock_repo.is_llm_assigned_to_user.return_value = False

        with pytest.raises(AssignmentNotFoundException):
            operations.unassign_llm_from_user(mock_repo, "user-123", "test-config-123")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
