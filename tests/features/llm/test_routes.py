"""Tests for LLMController - thin controller pattern."""

import pytest
from unittest.mock import Mock, AsyncMock
from typing import Dict, Any

from src.features.llm.routes import LLMController
from src.platform.http.base_controller import APIResponse
from src.features.llm.dto import (
    LLMConfigRequest,
    LLMConfigResponse,
    LLMGenerateRequest,
    LLMGenerateResponse,
    UserLLMAssignmentRequest,
)
from src.features.llm import LLMManager
from src.features.llm.exceptions import (
    ConfigurationNotFoundException,
    ConfigurationExistsException,
    ConfigurationCreationFailedException,
    CannotDeleteDefaultConfigException,
    VisionNotSupportedException,
    GenerationFailedException,
    AssignmentNotFoundException,
)


class TestLLMController:
    """Tests for the thin LLMController."""

    @pytest.fixture
    def mock_llm_manager(self):
        """Create a mock LLMManager."""
        mock = Mock(spec=LLMManager)
        mock.generate_response = AsyncMock()
        mock.test_configuration = AsyncMock()
        return mock

    @pytest.fixture
    def mock_user(self):
        """Create a mock user object."""
        user = Mock()
        user.id = "test-user-123"
        user.username = "testuser"
        return user

    @pytest.fixture
    def controller(self, mock_llm_manager):
        """Create an LLMController instance with mocked manager."""
        return LLMController(mock_llm_manager)

    @pytest.fixture
    def mock_download_manager(self):
        mock = Mock()
        mock.ensure_local_hf_repo = Mock()
        return mock

    @pytest.fixture
    def controller_with_downloads(self, mock_llm_manager, mock_download_manager):
        return LLMController(mock_llm_manager, mock_download_manager)

    # =========================================================================
    # Configuration Endpoint Tests
    # =========================================================================

    def test_get_all_configurations_success(self, controller, mock_llm_manager):
        """Test successful retrieval of all configurations."""
        mock_llm_manager.get_all_configurations.return_value = {
            "configurations": [
                LLMConfigResponse(
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
                    is_default=True
                )
            ],
            "default_provider": "test-123"
        }

        result = controller.get_all_configurations()

        assert result.success is True
        assert "configurations" in result.data

    def test_list_native_checkpoints_success(self, controller, monkeypatch):
        """Native checkpoint listing delegates to native_library, not the manager."""
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

    def test_get_all_configurations_error(self, controller, mock_llm_manager):
        """Test error handling in get_all_configurations."""
        mock_llm_manager.get_all_configurations.side_effect = Exception("Database error")

        result = controller.get_all_configurations()

        assert result.success is False
        assert result.error == "get_configurations_failed"

    def test_get_configuration_success(self, controller, mock_llm_manager):
        """Test successful retrieval of specific configuration."""
        mock_llm_manager.get_configuration.return_value = LLMConfigResponse(
            id="test-123",
            name="Test",
            type="ollama",
            enabled=True,
            base_url="http://localhost:11434",
            model="llama2",
            system_message="Test",
            temperature=0.7,
            max_tokens=1000,
            timeout=30
        )

        result = controller.get_configuration("test-123")

        assert result.success is True
        assert result.data.id == "test-123"

    def test_get_configuration_not_found(self, controller, mock_llm_manager):
        """Test configuration retrieval for non-existent config."""
        mock_llm_manager.get_configuration.side_effect = ConfigurationNotFoundException("Not found")

        result = controller.get_configuration("nonexistent")

        assert result.success is False
        assert result.error == "configuration_not_found"

    def test_create_configuration_success(self, controller, mock_llm_manager):
        """Test successful configuration creation."""
        request = LLMConfigRequest(
            name="Test LLM",
            type="ollama",
            enabled=True,
            base_url="http://localhost:11434",
            model="llama2",
            system_message="You are helpful"
        )
        mock_llm_manager.create_configuration.return_value = "new-config-123"

        result = controller.create_configuration(request)

        assert result.success is True
        assert result.data["id"] == "new-config-123"
        assert "created successfully" in result.message

    def test_create_configuration_already_exists(self, controller, mock_llm_manager):
        """Test configuration creation when already exists."""
        request = LLMConfigRequest(
            name="Test",
            type="ollama",
            enabled=True,
            base_url="http://localhost:11434",
            model="llama2",
            system_message="Test"
        )
        mock_llm_manager.create_configuration.side_effect = ConfigurationExistsException("Exists")

        result = controller.create_configuration(request)

        assert result.success is False
        assert result.error == "configuration_already_exists"

    def test_delete_configuration_success(self, controller, mock_llm_manager):
        """Test successful configuration deletion."""
        mock_llm_manager.delete_configuration.return_value = "test-123"

        result = controller.delete_configuration("test-123")

        assert result.success is True
        assert result.data["id"] == "test-123"
        assert "deleted successfully" in result.message

    def test_delete_configuration_default(self, controller, mock_llm_manager):
        """Test deletion of default configuration (should fail)."""
        mock_llm_manager.delete_configuration.side_effect = CannotDeleteDefaultConfigException("Cannot delete")

        result = controller.delete_configuration("default-config")

        assert result.success is False
        assert result.error == "cannot_delete_default"

    def test_set_default_provider_success(self, controller, mock_llm_manager):
        """Test successful default provider setting."""
        mock_llm_manager.set_default_provider.return_value = "test-123"

        result = controller.set_default_provider("test-123")

        assert result.success is True
        assert result.data["default_provider"] == "test-123"

    # =========================================================================
    # Generation Endpoint Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_generate_response_success(self, controller, mock_llm_manager, mock_user):
        """Test successful response generation."""
        request = LLMGenerateRequest(prompt="Hello, world!")
        mock_llm_manager.generate_response.return_value = LLMGenerateResponse(
            content="Hello! How can I help?",
            model="llama2",
            provider_id="test-123",
            tokens_used=50
        )

        result = await controller.generate_response(request, mock_user)

        assert result.success is True
        assert result.data.content == "Hello! How can I help?"

    @pytest.mark.asyncio
    async def test_generate_response_no_config(self, controller, mock_llm_manager, mock_user):
        """Test response generation with no configuration."""
        request = LLMGenerateRequest(prompt="Hello")
        mock_llm_manager.generate_response.side_effect = ConfigurationNotFoundException("No config")

        result = await controller.generate_response(request, mock_user)

        assert result.success is False
        assert result.error == "configuration_not_found"

    @pytest.mark.asyncio
    async def test_generate_response_vision_not_supported(self, controller, mock_llm_manager, mock_user):
        """Test response generation with vision not supported."""
        request = LLMGenerateRequest(prompt="Describe", image_data="base64data")
        mock_llm_manager.generate_response.side_effect = VisionNotSupportedException("No vision")

        result = await controller.generate_response(request, mock_user)

        assert result.success is False
        assert result.error == "vision_not_supported"

    @pytest.mark.asyncio
    async def test_generate_response_failure(self, controller, mock_llm_manager, mock_user):
        """Test response generation failure."""
        request = LLMGenerateRequest(prompt="Hello")
        mock_llm_manager.generate_response.side_effect = GenerationFailedException("Failed")

        result = await controller.generate_response(request, mock_user)

        assert result.success is False
        assert result.error == "generation_failed"

    @pytest.mark.asyncio
    async def test_test_configuration_success(self, controller, mock_llm_manager):
        """Test successful configuration testing."""
        mock_llm_manager.test_configuration.return_value = {"success": True, "response_time": 0.5}

        result = await controller.test_configuration("test-123")

        assert result.success is True
        assert "test successful" in result.message

    @pytest.mark.asyncio
    async def test_test_configuration_failed(self, controller, mock_llm_manager):
        """Test failed configuration testing."""
        mock_llm_manager.test_configuration.return_value = {"success": False, "error": "Timeout"}

        result = await controller.test_configuration("test-123")

        assert result.success is False
        assert result.error == "test_failed"

    # =========================================================================
    # User Assignment Endpoint Tests
    # =========================================================================

    def test_assign_llm_to_user_success(self, controller, mock_llm_manager):
        """Test successful LLM assignment to user."""
        request = UserLLMAssignmentRequest(user_id="user-123", llm_config_id="config-123")
        mock_llm_manager.assign_llm_to_user.return_value = {
            "user_id": "user-123",
            "llm_config_id": "config-123"
        }

        result = controller.assign_llm_to_user(request)

        assert result.success is True
        assert result.data["user_id"] == "user-123"

    def test_unassign_llm_from_user_success(self, controller, mock_llm_manager):
        """Test successful LLM unassignment from user."""
        mock_llm_manager.unassign_llm_from_user.return_value = {
            "user_id": "user-123",
            "llm_config_id": "config-123"
        }

        result = controller.unassign_llm_from_user("user-123", "config-123")

        assert result.success is True

    def test_unassign_llm_from_user_not_found(self, controller, mock_llm_manager):
        """Test unassigning non-existent assignment."""
        mock_llm_manager.unassign_llm_from_user.side_effect = AssignmentNotFoundException("Not found")

        result = controller.unassign_llm_from_user("user-123", "config-123")

        assert result.success is False
        assert result.error == "assignment_not_found"

    def test_get_user_llm_assignments_success(self, controller, mock_llm_manager):
        """Test getting user's LLM assignments."""
        mock_llm_manager.get_user_llm_assignments.return_value = {
            "user_id": "user-123",
            "llm_configs": []
        }

        result = controller.get_user_llm_assignments("user-123")

        assert result.success is True
        assert result.data["user_id"] == "user-123"

    def test_get_all_user_llm_assignments_success(self, controller, mock_llm_manager):
        """Test getting all user LLM assignments (admin)."""
        mock_llm_manager.get_all_user_llm_assignments.return_value = {
            "assignments": []
        }

        result = controller.get_all_user_llm_assignments()

        assert result.success is True
        assert "assignments" in result.data

    def test_get_llm_assignments_success(self, controller, mock_llm_manager):
        """Test getting the users directly assigned to an LLM configuration."""
        mock_llm_manager.get_llm_assignments.return_value = {
            "llm_config_id": "config-123",
            "assignments": [{"user_id": "user-123"}]
        }

        result = controller.get_llm_assignments("config-123")

        assert result.success is True
        assert result.data["assignments"] == [{"user_id": "user-123"}]

    def test_get_llm_assignments_config_not_found(self, controller, mock_llm_manager):
        """Test getting assignments for an unknown LLM configuration."""
        mock_llm_manager.get_llm_assignments.side_effect = ConfigurationNotFoundException("Not found")

        result = controller.get_llm_assignments("missing-config")

        assert result.success is False
        assert result.error == "configuration_not_found"

    def test_get_llm_assignment_summary_success(self, controller, mock_llm_manager):
        """Test getting the per-configuration assignment-count summary."""
        mock_llm_manager.get_assignment_summary.return_value = {
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
        """A controller built without a download_manager (the default, e.g. a
        composition path that never wired one) fails clean rather than
        crashing on `self.download_manager.ensure_local_hf_repo`."""
        result = await controller.fetch_gemma3_chat_tokenizer()
        assert result.success is False
        assert result.error == "download_manager_unavailable"

    @pytest.mark.asyncio
    async def test_fetch_gemma3_chat_tokenizer_success(self, controller_with_downloads, mock_download_manager):
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
