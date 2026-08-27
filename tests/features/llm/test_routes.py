"""
Tests for LLMController.

The controller calls `src.features.llm.operations` functions directly
(module-level, no injected manager) for mutations (create/update/delete
configuration, set-default, generate, assign/unassign); pure reads
(configuration list/detail, assignment listings, the assignment summary) go
straight to `LLMRepository` and the llm `mappers`. `mock_operations` patches
the `operations` module as imported into `routes.py`, so tests assert against
it exactly like the previous manager mock, without the controller holding a
stateful collaborator it doesn't need.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.llm import routes as routes_module
from src.features.llm.routes import LLMController
from src.platform.http.base_controller import APIResponse
from src.features.llm.dto import (
    LLMConfigRequest,
    LLMGenerateRequest,
    LLMGenerateResponse,
    UserLLMAssignmentRequest,
)
from src.features.llm.repository import LLMConfig
from src.features.llm.exceptions import (
    ConfigurationNotFoundException,
    ConfigurationExistsException,
    CannotDeleteDefaultConfigException,
    VisionNotSupportedException,
    GenerationFailedException,
    AssignmentNotFoundException,
)


@pytest.fixture
def mock_operations(monkeypatch):
    """Patch the `operations` module as seen by routes.py."""
    mock = Mock()
    monkeypatch.setattr(routes_module, "operations", mock)
    return mock


@pytest.fixture
def mock_repo():
    """Create a mock LLM repository."""
    return Mock()


@pytest.fixture
def mock_llm_service():
    """Create a mock LLM service."""
    mock = Mock()
    mock.test_configuration = AsyncMock()
    return mock


@pytest.fixture
def mock_settings():
    return Mock()


@pytest.fixture
def mock_plugins():
    return Mock()


@pytest.fixture
def mock_user():
    """Create a mock user object."""
    user = Mock()
    user.id = "test-user-123"
    user.username = "testuser"
    return user


@pytest.fixture
def controller(mock_operations, mock_repo, mock_llm_service, mock_settings, mock_plugins):
    """Create an LLMController instance with mocked collaborators."""
    return LLMController(
        llm_repository=mock_repo,
        llm_service=mock_llm_service,
        settings=mock_settings,
        plugin_registry=mock_plugins,
    )


@pytest.fixture
def mock_download_manager():
    mock = Mock()
    mock.ensure_local_hf_repo = Mock()
    return mock


@pytest.fixture
def controller_with_downloads(mock_operations, mock_repo, mock_llm_service, mock_settings, mock_plugins, mock_download_manager):
    return LLMController(
        llm_repository=mock_repo,
        llm_service=mock_llm_service,
        settings=mock_settings,
        plugin_registry=mock_plugins,
        download_queue=mock_download_manager,
    )


@pytest.fixture
def sample_llm_config():
    return LLMConfig(
        id="test-123",
        name="Test",
        type="ollama",
        enabled=True,
        base_url="http://localhost:11434",
        model="llama2",
        system_message="Test",
        temperature=0.7,
        max_tokens=1000,
        timeout=30,
    )


class TestLLMController:
    """Tests for the thin LLMController."""

    # =========================================================================
    # Configuration Endpoint Tests
    # =========================================================================

    def test_get_all_configurations_success(self, controller, mock_repo, sample_llm_config):
        """Test successful retrieval of all configurations."""
        mock_repo.get_all_configurations.return_value = {"test-123": sample_llm_config}
        mock_repo.default_provider = "test-123"

        result = controller.get_all_configurations()

        assert result.success is True
        assert "configurations" in result.data
        assert result.data["configurations"][0].id == "test-123"
        assert result.data["configurations"][0].is_default is True

    def test_get_all_configurations_redacts_api_key(self, controller, mock_repo, sample_llm_config):
        """List responses report api_key_set and never expose the key."""
        sample_llm_config.api_key = "super-secret"
        mock_repo.get_all_configurations.return_value = {"test-123": sample_llm_config}
        mock_repo.default_provider = "test-123"

        result = controller.get_all_configurations()

        cfg = result.data["configurations"][0]
        assert cfg.api_key_set is True
        assert "api_key" not in cfg.model_dump()

    def test_list_native_checkpoints_success(self, controller, monkeypatch):
        """Native checkpoint listing delegates to native_library, not operations."""
        from src.features.llm.native_library import NativeCheckpointEntry

        entries = [NativeCheckpointEntry(name="qwen3-tiny", path="/models/llm/qwen3-tiny", model_type="qwen3", supported=True, vision=False)]
        monkeypatch.setattr("src.features.llm.native_library.list_native_checkpoints", lambda: entries)

        result = controller.list_native_checkpoints()

        assert result.success is True
        assert result.data == [
            {"name": "qwen3-tiny", "path": "/models/llm/qwen3-tiny", "model_type": "qwen3", "supported": True, "vision": False, "reason": None, "quant_modes": [], "shared_te": False}
        ]

    def test_list_native_checkpoints_error(self, controller, monkeypatch):
        monkeypatch.setattr(
            "src.features.llm.native_library.list_native_checkpoints",
            Mock(side_effect=Exception("disk error")),
        )

        result = controller.list_native_checkpoints()

        assert result.success is False
        assert result.error == "list_native_checkpoints_failed"

    def test_get_all_configurations_error(self, controller, mock_repo):
        """Test error handling in get_all_configurations."""
        mock_repo.get_all_configurations.side_effect = Exception("Database error")

        result = controller.get_all_configurations()

        assert result.success is False
        assert result.error == "get_configurations_failed"

    def test_get_configuration_success(self, controller, mock_repo, sample_llm_config):
        """Test successful retrieval of specific configuration."""
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_repo.default_provider = "other-config"

        result = controller.get_configuration("test-123")

        assert result.success is True
        assert result.data.id == "test-123"

    def test_get_configuration_not_found(self, controller, mock_repo):
        """Test configuration retrieval for non-existent config."""
        mock_repo.get_configuration.return_value = None

        result = controller.get_configuration("nonexistent")

        assert result.success is False
        assert result.error == "configuration_not_found"

    def test_create_configuration_success(self, controller, mock_operations, mock_repo, mock_plugins):
        """Test successful configuration creation."""
        request = LLMConfigRequest(
            name="Test LLM",
            type="ollama",
            enabled=True,
            base_url="http://localhost:11434",
            model="llama2",
            system_message="You are helpful"
        )
        mock_operations.create_configuration.return_value = "new-config-123"

        result = controller.create_configuration(request)

        assert result.success is True
        assert result.data["id"] == "new-config-123"
        assert "created successfully" in result.message
        mock_operations.create_configuration.assert_called_once_with(mock_repo, mock_plugins, request)

    def test_create_configuration_already_exists(self, controller, mock_operations):
        """Test configuration creation when already exists."""
        request = LLMConfigRequest(
            name="Test",
            type="ollama",
            enabled=True,
            base_url="http://localhost:11434",
            model="llama2",
            system_message="Test"
        )
        mock_operations.create_configuration.side_effect = ConfigurationExistsException("Exists")

        result = controller.create_configuration(request)

        assert result.success is False
        assert result.error == "configuration_already_exists"

    def test_delete_configuration_success(self, controller, mock_operations):
        """Test successful configuration deletion."""
        mock_operations.delete_configuration.return_value = "test-123"

        result = controller.delete_configuration("test-123")

        assert result.success is True
        assert result.data["id"] == "test-123"
        assert "deleted successfully" in result.message

    def test_delete_configuration_default(self, controller, mock_operations):
        """Test deletion of default configuration (should fail)."""
        mock_operations.delete_configuration.side_effect = CannotDeleteDefaultConfigException("Cannot delete")

        result = controller.delete_configuration("default-config")

        assert result.success is False
        assert result.error == "cannot_delete_default"

    def test_set_default_provider_success(self, controller, mock_operations):
        """Test successful default provider setting."""
        mock_operations.set_default_provider.return_value = "test-123"

        result = controller.set_default_provider("test-123")

        assert result.success is True
        assert result.data["default_provider"] == "test-123"

    # =========================================================================
    # Generation Endpoint Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_generate_response_success(self, controller, mock_operations, mock_user):
        """Test successful response generation."""
        request = LLMGenerateRequest(prompt="Hello, world!")
        mock_operations.generate_response = AsyncMock(return_value=LLMGenerateResponse(
            content="Hello! How can I help?",
            model="llama2",
            provider_id="test-123",
            tokens_used=50
        ))

        result = await controller.generate_response(request, mock_user)

        assert result.success is True
        assert result.data.content == "Hello! How can I help?"

    @pytest.mark.asyncio
    async def test_generate_response_no_config(self, controller, mock_operations, mock_user):
        """Test response generation with no configuration."""
        request = LLMGenerateRequest(prompt="Hello")
        mock_operations.generate_response = AsyncMock(side_effect=ConfigurationNotFoundException("No config"))

        result = await controller.generate_response(request, mock_user)

        assert result.success is False
        assert result.error == "configuration_not_found"

    @pytest.mark.asyncio
    async def test_generate_response_vision_not_supported(self, controller, mock_operations, mock_user):
        """Test response generation with vision not supported."""
        request = LLMGenerateRequest(prompt="Describe", image_data="base64data")
        mock_operations.generate_response = AsyncMock(side_effect=VisionNotSupportedException("No vision"))

        result = await controller.generate_response(request, mock_user)

        assert result.success is False
        assert result.error == "vision_not_supported"

    @pytest.mark.asyncio
    async def test_generate_response_failure(self, controller, mock_operations, mock_user):
        """Test response generation failure."""
        request = LLMGenerateRequest(prompt="Hello")
        mock_operations.generate_response = AsyncMock(side_effect=GenerationFailedException("Failed"))

        result = await controller.generate_response(request, mock_user)

        assert result.success is False
        assert result.error == "generation_failed"

    @pytest.mark.asyncio
    async def test_test_configuration_success(self, controller, mock_repo, mock_llm_service, sample_llm_config):
        """Test successful configuration testing."""
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_llm_service.test_configuration.return_value = {"success": True, "response_time": 0.5}

        result = await controller.test_configuration("test-123")

        assert result.success is True
        assert "test successful" in result.message

    @pytest.mark.asyncio
    async def test_test_configuration_not_found(self, controller, mock_repo):
        """Testing a non-existent configuration reports not-found."""
        mock_repo.get_configuration.return_value = None

        result = await controller.test_configuration("nonexistent")

        assert result.success is False
        assert result.error == "configuration_not_found"

    @pytest.mark.asyncio
    async def test_test_configuration_failed(self, controller, mock_repo, mock_llm_service, sample_llm_config):
        """Test failed configuration testing."""
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_llm_service.test_configuration.return_value = {"success": False, "error": "Timeout"}

        result = await controller.test_configuration("test-123")

        assert result.success is False
        assert result.error == "test_failed"

    # =========================================================================
    # User Assignment Endpoint Tests
    # =========================================================================

    def test_assign_llm_to_user_success(self, controller, mock_operations):
        """Test successful LLM assignment to user."""
        request = UserLLMAssignmentRequest(user_id="user-123", llm_config_id="config-123")
        mock_operations.assign_llm_to_user.return_value = {
            "user_id": "user-123",
            "llm_config_id": "config-123"
        }

        result = controller.assign_llm_to_user(request)

        assert result.success is True
        assert result.data["user_id"] == "user-123"

    def test_unassign_llm_from_user_success(self, controller, mock_operations):
        """Test successful LLM unassignment from user."""
        mock_operations.unassign_llm_from_user.return_value = {
            "user_id": "user-123",
            "llm_config_id": "config-123"
        }

        result = controller.unassign_llm_from_user("user-123", "config-123")

        assert result.success is True

    def test_unassign_llm_from_user_not_found(self, controller, mock_operations):
        """Test unassigning non-existent assignment."""
        mock_operations.unassign_llm_from_user.side_effect = AssignmentNotFoundException("Not found")

        result = controller.unassign_llm_from_user("user-123", "config-123")

        assert result.success is False
        assert result.error == "assignment_not_found"

    def test_get_user_llm_assignments_success(self, controller, mock_repo):
        """Test getting user's LLM assignments."""
        mock_repo.get_user_llm_configurations.return_value = {}

        result = controller.get_user_llm_assignments("user-123")

        assert result.success is True
        assert result.data["user_id"] == "user-123"
        assert result.data["llm_configs"] == []

    def test_get_user_llm_assignments_redacts_api_key(self, controller, mock_repo, sample_llm_config):
        """A user's assignment list reports api_key_set and never the key."""
        sample_llm_config.api_key = "super-secret"
        mock_repo.get_user_llm_configurations.return_value = {"test-123": sample_llm_config}

        result = controller.get_user_llm_assignments("user-123")

        cfg = result.data["llm_configs"][0]
        assert cfg["api_key_set"] is True
        assert "api_key" not in cfg
        assert "super-secret" not in str(result.data)

    def test_get_all_user_llm_assignments_success(self, controller, mock_repo):
        """Test getting all user LLM assignments (admin)."""
        mock_repo.get_all_user_llm_assignments.return_value = {}
        mock_repo.get_all_configurations.return_value = {}

        result = controller.get_all_user_llm_assignments()

        assert result.success is True
        assert "assignments" in result.data

    def test_get_all_user_llm_assignments_redacts_api_key(self, controller, mock_repo, sample_llm_config):
        """The admin all-assignments view reports api_key_set and never the key."""
        sample_llm_config.api_key = "super-secret"
        mock_repo.get_all_user_llm_assignments.return_value = {"user-123": ["test-123"]}
        mock_repo.get_all_configurations.return_value = {"test-123": sample_llm_config}

        result = controller.get_all_user_llm_assignments()

        cfg = result.data["assignments"][0]["llm_configs"][0]
        assert cfg["api_key_set"] is True
        assert "api_key" not in cfg
        assert "super-secret" not in str(result.data)

    def test_get_llm_assignments_success(self, controller, mock_repo, sample_llm_config):
        """Test getting the users directly assigned to an LLM configuration."""
        mock_repo.get_configuration.return_value = sample_llm_config
        mock_repo.get_llm_users.return_value = ["user-123"]

        result = controller.get_llm_assignments("config-123")

        assert result.success is True
        assert result.data["assignments"] == [{"user_id": "user-123"}]

    def test_get_llm_assignments_config_not_found(self, controller, mock_repo):
        """Test getting assignments for an unknown LLM configuration."""
        mock_repo.get_configuration.return_value = None

        result = controller.get_llm_assignments("missing-config")

        assert result.success is False
        assert result.error == "configuration_not_found"

    def test_get_llm_assignment_summary_success(self, controller, mock_repo):
        """Test getting the per-configuration assignment-count summary."""
        mock_repo.get_llm_assignment_summary.return_value = {
            "config-1": {"assignment_count": 2, "group_count": 1},
            "config-2": {"assignment_count": 0, "group_count": 0}
        }

        result = controller.get_llm_assignment_summary()

        assert result.success is True
        assert result.data["config-1"] == {"assignment_count": 2, "group_count": 1}
        assert result.data["config-2"] == {"assignment_count": 0, "group_count": 0}

    # =========================================================================
    # gemma3 chat-tokenizer on-demand fetch
    # =========================================================================

    @pytest.mark.asyncio
    async def test_fetch_gemma3_chat_tokenizer_without_download_manager_errors_cleanly(self, controller):
        """A controller built without a download_queue (the default, e.g. a
        composition path that never wired one) fails clean rather than
        crashing on `self.download_queue.ensure_local_hf_repo`."""
        result = await controller.fetch_gemma3_chat_tokenizer()
        assert result.success is False
        assert result.error == "download_queue_unavailable"

    @pytest.mark.asyncio
    async def test_fetch_gemma3_chat_tokenizer_success(self, controller_with_downloads, mock_download_manager, tmp_path):
        # `_models_dir()` falls back to `SettingRepository().get_setting_by_key`
        # when no override reaches it, which is a real, unmocked repository -
        # patched here to a fixed path rather than hitting the settings DB.
        with patch('src.features.llm.native_te_adoption._models_dir', return_value=tmp_path):
            result = await controller_with_downloads.fetch_gemma3_chat_tokenizer()

        assert result.success is True
        assert "path" in result.data
        mock_download_manager.ensure_local_hf_repo.assert_called_once()
        args, kwargs = mock_download_manager.ensure_local_hf_repo.call_args
        from src.features.llm.native_te_adoption import GEMMA3_CHAT_TOKENIZER_REPO
        assert args[0] == GEMMA3_CHAT_TOKENIZER_REPO

    @pytest.mark.asyncio
    async def test_fetch_gemma3_chat_tokenizer_reports_download_failure(self, controller_with_downloads, mock_download_manager):
        mock_download_manager.ensure_local_hf_repo.side_effect = RuntimeError("network unreachable")

        result = await controller_with_downloads.fetch_gemma3_chat_tokenizer()

        assert result.success is False
        assert result.error == "fetch_gemma3_chat_tokenizer_failed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
