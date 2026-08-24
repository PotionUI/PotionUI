import pytest
import asyncio
import sys
import os
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from typing import Dict, Any, List
from datetime import datetime
from fastapi import HTTPException

# Add the src directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.features.generation.routes import GenerationController
from src.platform.http.base_controller import APIResponse
from src.platform.filesystem import FileStore
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.dto import GenerationRequest, GenerationStatus
from src.platform.websocket import ConnectionManager
from src.features.generation.websocket_handler import WebSocketHandler
from src.pipelines.outputs import (
    GenerationOutput, ImageGenerationOutput, GalleryGenerationOutput,
    ProgressGenerationOutput
)
from src.features.generation import GenerationHistoryManager
from src.features.generation.run_report_recorder import RunReportRecorder
from PIL import Image
from src.features.generation.handlers.image_handler import ImageGenerationOutputHandler


class TestGenerationController:
    """Comprehensive tests for GenerationController"""

    @pytest.fixture
    def mock_generation_orchestrator(self):
        """Mock GenerationOrchestrator"""
        mock = Mock(spec=GenerationOrchestrator)
        mock.status_tracker = Mock()
        return mock

    @pytest.fixture
    def mock_file_service(self):
        """Mock FileStore"""
        mock = Mock(spec=FileStore)
        return mock

    @pytest.fixture
    def mock_generation_history_manager(self):
        """Mock GenerationHistoryManager"""
        mock = Mock(spec=GenerationHistoryManager)
        return mock

    @pytest.fixture
    def mock_run_report_recorder(self):
        """Mock RunReportRecorder"""
        mock = Mock(spec=RunReportRecorder)
        return mock

    @pytest.fixture
    def mock_current_user(self):
        """Mock current user"""
        user = Mock()
        user.id = "test-user-123"
        user.username = "testuser"
        return user

    @pytest.fixture
    def sample_generation_request(self):
        """Sample generation request data"""
        return GenerationRequest(
            preset_id="test-preset-123",
            prompt="A beautiful landscape",
            negative_prompt="ugly, blurry",
            mode="txt2img",
            generation_settings={
                'resolution': {'width': 1024, 'height': 768},
                'num_images': 2,
                'seed': 42
            },
            form_data={'tabs': {'style': 'realistic'}}
        )

    @pytest.fixture
    def sample_generation_status(self):
        """Sample generation status"""
        return GenerationStatus(
            id="test-generation-123",
            status="running",
            created_at=str(datetime.now()),
            preset_id="test-preset-123",
            user_id="test-user-123",
            progress=0.5
        )

    @pytest.fixture
    def sample_image(self):
        """Create a sample PIL Image"""
        img = Image.new('RGB', (100, 100), color='red')
        return img

    @pytest.fixture
    def controller(self, mock_generation_orchestrator, mock_generation_history_manager, mock_file_service, mock_run_report_recorder):
        """Create GenerationController instance with mocked dependencies"""
        controller = GenerationController(
            mock_generation_orchestrator,
            mock_generation_history_manager,
            mock_file_service,
            mock_run_report_recorder
        )
        return controller

    @pytest.mark.asyncio
    async def test_controller_initialization(self, mock_generation_orchestrator, mock_generation_history_manager, mock_file_service, mock_run_report_recorder):
        """Test controller proper initialization"""
        # Act
        controller = GenerationController(
            mock_generation_orchestrator,
            mock_generation_history_manager,
            mock_file_service,
            mock_run_report_recorder
        )

        # Assert
        assert controller.generation_orchestrator == mock_generation_orchestrator
        assert controller.history_manager == mock_generation_history_manager
        assert controller.file_service == mock_file_service
        assert isinstance(controller.connection_manager, ConnectionManager)
        assert isinstance(controller.websocket_handler, WebSocketHandler)

    @pytest.mark.asyncio
    async def test_start_generation_success(self, controller, sample_generation_request, mock_current_user):
        """Test successful generation start"""
        # Arrange
        expected_result = {
            'generation_id': 'test-gen-123',
            'status': {'status': 'pending'},
            'backend': {'type': 'local'}
        }
        controller.generation_orchestrator.start_generation.return_value = expected_result

        # Act
        result = await controller.start_generation(sample_generation_request, mock_current_user)

        # Assert
        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data == expected_result
        
        # Verify service was called correctly
        controller.generation_orchestrator.start_generation.assert_called_once_with(
            sample_generation_request,
            mock_current_user.id,
            output_callback=controller._handle_generation_output
        )

    @pytest.mark.asyncio
    async def test_start_generation_validation_error(self, controller, sample_generation_request, mock_current_user):
        """Test generation start with validation error"""
        # Arrange
        controller.generation_orchestrator.start_generation.side_effect = ValueError("Invalid preset")

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.start_generation(sample_generation_request, mock_current_user)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "validation_error"
        assert "Invalid preset" in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_start_generation_form_binding_error(self, controller, sample_generation_request, mock_current_user):
        """FormBindingError must be caught BEFORE the generic ValueError branch
        (it subclasses ValueError) and surfaced as HTTP 422 with structured
        per-field errors, not the flat 400 validation_error shape."""
        from src.features.forms.binding import FormBindingError

        error = FormBindingError(
            errors=["prompt: required field is missing", "steps: 500 exceeds the maximum 100"],
            field_errors={
                "prompt": ["required field is missing"],
                "steps": ["500 exceeds the maximum 100"],
            },
            coercions=["cfg: '3.5' -> 3.5 (numeric string)"],
            stripped=["unknown_key"],
        )
        controller.generation_orchestrator.start_generation.side_effect = error

        with pytest.raises(HTTPException) as exc_info:
            await controller.start_generation(sample_generation_request, mock_current_user)

        assert exc_info.value.status_code == 422
        detail = exc_info.value.detail
        assert detail["error"] == "form_validation_failed"
        assert detail["field_errors"] == {
            "prompt": ["required field is missing"],
            "steps": ["500 exceeds the maximum 100"],
        }
        assert detail["coercions"] == ["cfg: '3.5' -> 3.5 (numeric string)"]
        assert detail["stripped"] == ["unknown_key"]
        assert detail["message"] == str(error)

    @pytest.mark.asyncio
    async def test_start_generation_model_not_found_maps_to_404(self, controller, sample_generation_request, mock_current_user):
        from src.features.models.exceptions import ModelNotFoundException

        controller.generation_orchestrator.start_generation.side_effect = ModelNotFoundException("gone")

        with pytest.raises(HTTPException) as exc_info:
            await controller.start_generation(sample_generation_request, mock_current_user)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail['error'] == "model_not_found"

    @pytest.mark.asyncio
    async def test_start_generation_model_access_denied_maps_to_404_not_403(
        self, controller, sample_generation_request, mock_current_user,
    ):
        """404, not 403: a 403 (or a message naming the model) would confirm
        that the referenced model id exists but belongs to someone else -
        the same existence-concealing rationale as GenerationPolicy."""
        from src.features.models.exceptions import ModelAccessDeniedException

        controller.generation_orchestrator.start_generation.side_effect = ModelAccessDeniedException(
            "Access denied to model 'super-secret-model-id'"
        )

        with pytest.raises(HTTPException) as exc_info:
            await controller.start_generation(sample_generation_request, mock_current_user)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail['error'] == "model_not_found"
        assert "super-secret-model-id" not in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_start_generation_general_error(self, controller, sample_generation_request, mock_current_user):
        """Test generation start with general error"""
        # Arrange
        controller.generation_orchestrator.start_generation.side_effect = Exception("Service unavailable")

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.start_generation(sample_generation_request, mock_current_user)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "generation_start_failed"
        assert "Failed to start generation" in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_get_generation_status_success(self, controller, sample_generation_status, mock_current_user):
        """Test successful status retrieval"""
        # Arrange
        controller.generation_orchestrator.get_generation_status.return_value = sample_generation_status

        # Act
        result = await controller.get_generation_status("test-generation-123", mock_current_user)

        # Assert
        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data['id'] == "test-generation-123"
        assert result.data['status'] == 'running'
        assert result.data['progress'] == 0.5

    @pytest.mark.asyncio
    async def test_get_generation_status_not_found(self, controller, mock_current_user):
        """Test status retrieval for non-existent generation"""
        # Arrange
        controller.generation_orchestrator.get_generation_status.return_value = None

        # Act & Assert
        with patch('src.features.generation.routes.generation_repo') as mock_repo:
            mock_repo.get_by_id.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await controller.get_generation_status("nonexistent", mock_current_user)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail['error'] == "generation_not_found"

    @pytest.mark.asyncio
    async def test_get_generation_status_falls_back_to_db_when_tracker_misses(self, controller, mock_current_user):
        """An upload (or any generation that fell out of the in-memory status
        tracker) must still resolve via the history DB rather than 404ing -
        the frontend's reload-restore path treats a 404 as "gone" and drops
        the tab's result."""
        controller.generation_orchestrator.get_generation_status.return_value = None

        db_generation = Mock()
        db_generation.user_id = "test-user-123"
        db_generation.to_dict.return_value = {"id": "test-generation-123", "status": "completed"}

        with patch('src.features.generation.routes.generation_repo') as mock_repo:
            mock_repo.get_by_id.return_value = db_generation

            result = await controller.get_generation_status("test-generation-123", mock_current_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data == {"id": "test-generation-123", "status": "completed"}
        mock_repo.get_by_id.assert_called_once_with("test-generation-123")

    @pytest.mark.asyncio
    async def test_get_generation_status_db_fallback_other_user_denied(self, controller, mock_current_user):
        """The DB fallback still enforces ownership: a generation found only
        in the history DB and owned by someone else must 404, not leak."""
        controller.generation_orchestrator.get_generation_status.return_value = None

        db_generation = Mock()
        db_generation.user_id = "someone-else"
        db_generation.to_dict.return_value = {"id": "test-generation-123", "status": "completed"}

        with patch('src.features.generation.routes.generation_repo') as mock_repo:
            mock_repo.get_by_id.return_value = db_generation

            with pytest.raises(HTTPException) as exc_info:
                await controller.get_generation_status("test-generation-123", mock_current_user)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail['error'] == "generation_not_found"

    @pytest.mark.asyncio
    async def test_cancel_generation_success(self, controller, sample_generation_status, mock_current_user):
        """Test successful generation cancellation"""
        # Arrange
        controller.generation_orchestrator.cancel_generation.return_value = True
        controller.generation_orchestrator.get_generation_status.return_value = sample_generation_status
        controller.connection_manager.broadcast_to_generation = AsyncMock()

        # Act
        result = await controller.cancel_generation("test-generation-123", mock_current_user)

        # Assert
        assert isinstance(result, APIResponse)
        assert result.success is True
        assert "cancelled successfully" in result.message

        # Verify broadcast was called
        controller.connection_manager.broadcast_to_generation.assert_called_once()
        call_args = controller.connection_manager.broadcast_to_generation.call_args
        assert call_args[0][0] == "test-generation-123"
        assert call_args[0][1]['type'] == 'generation_cancelled'

    @pytest.mark.asyncio
    async def test_cancel_generation_failed(self, controller, sample_generation_status, mock_current_user):
        """Test failed generation cancellation"""
        # Arrange: ownership resolves to the caller, but the orchestrator can't cancel.
        controller.generation_orchestrator.get_generation_status.return_value = sample_generation_status
        controller.generation_orchestrator.cancel_generation.return_value = False

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.cancel_generation("test-generation-123", mock_current_user)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "cancel_failed"

    @pytest.mark.asyncio
    async def test_cancel_generation_exception(self, controller, sample_generation_status, mock_current_user):
        """Test generation cancellation with exception"""
        # Arrange
        controller.generation_orchestrator.get_generation_status.return_value = sample_generation_status
        controller.generation_orchestrator.cancel_generation.side_effect = Exception("Service error")

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.cancel_generation("test-generation-123", mock_current_user)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "cancel_failed"

    @pytest.mark.asyncio
    async def test_cancel_generation_other_user_denied(self, controller, sample_generation_status, mock_current_user):
        """A non-owner cancelling another user's generation gets a 404 (no leak),
        and the orchestrator is never asked to cancel."""
        # Arrange: generation is owned by a different user.
        sample_generation_status.user_id = "someone-else"
        controller.generation_orchestrator.get_generation_status.return_value = sample_generation_status
        controller.generation_orchestrator.cancel_generation = AsyncMock(return_value=True)

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await controller.cancel_generation("test-generation-123", mock_current_user)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail['error'] == "generation_not_found"
        controller.generation_orchestrator.cancel_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_generation_status_other_user_denied(self, controller, sample_generation_status, mock_current_user):
        """A non-owner reading another user's generation status gets a 404."""
        sample_generation_status.user_id = "someone-else"
        controller.generation_orchestrator.get_generation_status.return_value = sample_generation_status

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_generation_status("test-generation-123", mock_current_user)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail['error'] == "generation_not_found"

    @pytest.mark.asyncio
    async def test_list_generations_filters_to_owner(self, controller, mock_current_user):
        """A non-admin sees only their own active generations."""
        import types
        records = [
            types.SimpleNamespace(user_id="test-user-123", model_dump=lambda: {"id": "gen-1", "status": "running"}),
            types.SimpleNamespace(user_id="other-user", model_dump=lambda: {"id": "gen-2", "status": "running"}),
        ]
        controller.generation_orchestrator.status_tracker.list_active.return_value = records

        result = await controller.list_generations(mock_current_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data == [{"id": "gen-1", "status": "running"}]

    @pytest.mark.asyncio
    async def test_list_generations_admin_sees_all(self, controller):
        """An administrator sees every user's active generations."""
        import types
        from src.platform.security.user import AccountType
        admin = types.SimpleNamespace(id="admin-1", account_type=AccountType.ADMIN)
        records = [
            types.SimpleNamespace(user_id="user-a", model_dump=lambda: {"id": "gen-1", "status": "running"}),
            types.SimpleNamespace(user_id="user-b", model_dump=lambda: {"id": "gen-2", "status": "running"}),
        ]
        controller.generation_orchestrator.status_tracker.list_active.return_value = records

        result = await controller.list_generations(admin)

        assert result.data == [
            {"id": "gen-1", "status": "running"},
            {"id": "gen-2", "status": "running"},
        ]

    @pytest.mark.asyncio
    async def test_get_generation_history_success(self, controller, mock_current_user):
        """Test successful history retrieval"""
        # Arrange - now delegate to history_manager
        controller.history_manager.get_history.return_value = {
            'generations': [{"id": "gen-1"}, {"id": "gen-2"}],
            'total': 2,
            'limit': 50,
            'offset': 0,
            'filters': {}
        }

        # Act
        result = await controller.get_generation_history(mock_current_user, limit=50, offset=0)

        # Assert
        assert isinstance(result, APIResponse)
        assert result.success is True
        assert 'generations' in result.data
        assert 'total' in result.data
        assert result.data['total'] == 2

    @pytest.mark.asyncio
    async def test_get_generation_history_threads_semantic_query(self, controller, mock_current_user):
        """The semantic_query param reaches the history manager unchanged."""
        controller.history_manager.get_history.return_value = {
            'generations': [], 'total': 0, 'limit': 50, 'offset': 0, 'filters': {}
        }

        result = await controller.get_generation_history(
            mock_current_user, semantic_query="red fox in snow"
        )

        assert result.success is True
        kwargs = controller.history_manager.get_history.call_args.kwargs
        assert kwargs["semantic_query"] == "red fox in snow"

    @pytest.mark.asyncio
    async def test_get_generation_history_with_date_filters(self, controller, mock_current_user):
        """Test history retrieval with date filters"""
        # Arrange - delegate to history_manager
        controller.history_manager.get_history.return_value = {
            'generations': [{"id": "gen-1"}],
            'total': 1,
            'limit': 50,
            'offset': 0,
            'filters': {
                'status': "completed",
                'created_from': "2024-01-01",
                'created_to': "2024-01-31",
                'completed_from': "2024-01-01 10:00:00",
                'completed_to': "2024-01-31 23:59:59",
                'tag_ids': None
            }
        }

        # Act
        result = await controller.get_generation_history(
            mock_current_user,
            limit=50,
            offset=0,
            status="completed",
            created_from="2024-01-01",
            created_to="2024-01-31",
            completed_from="2024-01-01 10:00:00",
            completed_to="2024-01-31 23:59:59"
        )

        # Assert
        assert isinstance(result, APIResponse)
        assert result.success is True
        assert 'filters' in result.data
        assert result.data['filters']['created_from'] == "2024-01-01"
        assert result.data['filters']['created_to'] == "2024-01-31"
        assert result.data['filters']['completed_from'] == "2024-01-01 10:00:00"
        assert result.data['filters']['completed_to'] == "2024-01-31 23:59:59"

        # Verify history_manager was called with correct parameters
        controller.history_manager.get_history.assert_called_once_with(
            user_id=mock_current_user.id,
            limit=50,
            offset=0,
            status="completed",
            created_from="2024-01-01",
            created_to="2024-01-31",
            completed_from="2024-01-01 10:00:00",
            completed_to="2024-01-31 23:59:59",
            tag_ids=None,
            include_tags=True,
            media_type=None,
            search=None,
            mode=None,
            preset_id=None,
            model_name=None,
            min_rating=None,
            favorites_only=False,
            collection_id=None,
            used_phrasebook_value_id=None,
            sort_by=None,
            sort_dir=None,
            system_tag=None,
            semantic_query=None
        )

    @pytest.mark.asyncio
    async def test_get_generation_history_invalid_date_format(self, controller, mock_current_user):
        """Test history retrieval with invalid date format"""
        # Arrange - history_manager raises exception for invalid date
        from src.features.generation.exceptions import InvalidDateFilterException
        controller.history_manager.get_history.side_effect = InvalidDateFilterException(
            "Invalid date format for created_from. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
        )

        # Act & Assert - controller raises HTTPException via error_response
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_generation_history(
                mock_current_user,
                created_from="invalid-date"
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "invalid_date_format"
        assert "Invalid date format for created_from" in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_get_generation_history_invalid_date_range(self, controller, mock_current_user):
        """Test history retrieval with invalid date range"""
        # Arrange - history_manager raises exception for invalid date range
        from src.features.generation.exceptions import InvalidDateFilterException
        controller.history_manager.get_history.side_effect = InvalidDateFilterException(
            "created_from date must be before created_to date"
        )

        # Act & Assert - controller raises HTTPException via error_response
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_generation_history(
                mock_current_user,
                created_from="2024-01-31",
                created_to="2024-01-01"  # from > to
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "invalid_date_format"
        assert "created_from date must be before created_to date" in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_get_generation_by_id_success(self, controller, mock_current_user):
        """Test successful generation retrieval by ID"""
        # Arrange - delegate to history_manager
        controller.history_manager.get_by_id.return_value = {
            "id": "test-gen-123",
            "status": "completed",
            "parameters": {},
            "models": []
        }

        # Act
        result = await controller.get_generation_by_id("test-gen-123", mock_current_user)

        # Assert
        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data['id'] == "test-gen-123"

    @pytest.mark.asyncio
    async def test_get_generation_by_id_not_found(self, controller, mock_current_user):
        """Test generation retrieval for non-existent ID"""
        # Arrange - history_manager raises exception
        from src.features.generation.exceptions import GenerationNotFoundException
        controller.history_manager.get_by_id.side_effect = GenerationNotFoundException("Generation 'nonexistent' not found")

        # Act & Assert - controller raises HTTPException via error_response
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_generation_by_id("nonexistent", mock_current_user)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail['error'] == "generation_not_found"
        assert "not found" in exc_info.value.detail['message'].lower()

    @pytest.mark.asyncio
    async def test_get_history_facets_delegates_to_history_query(self, controller, mock_current_user):
        """`get_history_facets` calls `history_query`, not `history_manager` -
        `get_facets` was removed from `GenerationHistoryManager` in favor of
        the routes controller calling `GenerationHistoryQuery` directly."""
        controller.history_query.get_facets.return_value = {"modes": ["txt2img"]}

        result = await controller.get_history_facets(mock_current_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        controller.history_query.get_facets.assert_called_once_with(mock_current_user.id)

    @pytest.mark.asyncio
    async def test_get_generation_params_delegates_to_history_query(self, controller, mock_current_user):
        """`get_generation_params` calls `history_query`, not `history_manager`."""
        controller.history_query.get_params.return_value = {"seed": 42}

        result = await controller.get_generation_params("test-gen-123", 0, mock_current_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        controller.history_query.get_params.assert_called_once_with(
            generation_id="test-gen-123", index=0, user_id=mock_current_user.id
        )

    @pytest.mark.asyncio
    async def test_count_generations_by_tags_delegates_to_history_query(self, controller, mock_current_user):
        """`count_generations_by_tags` calls `history_query`, not `history_manager`."""
        controller.history_query.count_generations_by_tags.return_value = 3

        result = await controller.count_generations_by_tags(["tag-1"], mock_current_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data["count"] == 3
        controller.history_query.count_generations_by_tags.assert_called_once_with(
            tag_ids=["tag-1"], user_id=mock_current_user.id
        )

    @pytest.mark.asyncio
    async def test_delete_generation_history_success(self, controller, mock_current_user):
        """Test successful generation deletion"""
        # Arrange - delegate to history_manager
        controller.history_manager.delete.return_value = {
            'files_deleted_fs': 4,
            'files_deleted_db': 2,
            'files_failed_fs': 0
        }

        # Act
        result = await controller.delete_generation_history("test-gen-123", mock_current_user)

        # Assert
        assert isinstance(result, APIResponse)
        assert result.success is True
        assert "deleted successfully" in result.message
        assert "4 files from filesystem" in result.message
        assert "2 file records from database" in result.message

    @pytest.mark.asyncio
    async def test_delete_generation_history_not_found(self, controller, mock_current_user):
        """Test deletion of non-existent generation"""
        # Arrange - history_manager raises exception
        from src.features.generation.exceptions import GenerationNotFoundException
        controller.history_manager.delete.side_effect = GenerationNotFoundException("Generation 'nonexistent' not found")

        # Act & Assert - controller raises HTTPException via error_response
        with pytest.raises(HTTPException) as exc_info:
            await controller.delete_generation_history("nonexistent", mock_current_user)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail['error'] == "generation_not_found"
        assert "not found" in exc_info.value.detail['message'].lower()

    @pytest.mark.asyncio
    async def test_delete_generation_history_with_file_errors(self, controller, mock_current_user):
        """Test generation deletion with some file deletion failures"""
        # Arrange - delegate to history_manager
        controller.history_manager.delete.return_value = {
            'files_deleted_fs': 2,
            'files_deleted_db': 3,
            'files_failed_fs': 1
        }

        # Act
        result = await controller.delete_generation_history("test-gen-123", mock_current_user)

        # Assert
        assert isinstance(result, APIResponse)
        assert result.success is True
        assert "deleted successfully" in result.message
        # The message should indicate warning about failed filesystem deletion
        assert "3 file records from database" in result.message
        assert "Warning" in result.message or "1 files" in result.message

    @pytest.mark.asyncio
    async def test_handle_generation_output_completion(self, controller, sample_generation_status):
        """Test handling generation completion output"""
        # Arrange
        controller.generation_orchestrator.get_generation_status.return_value = sample_generation_status
        controller.connection_manager.broadcast_to_generation = AsyncMock()

        # Act - passing None signals completion
        await controller._handle_generation_output("test-gen-123", None)

        # Assert
        controller.connection_manager.broadcast_to_generation.assert_called_once()
        call_args = controller.connection_manager.broadcast_to_generation.call_args
        assert call_args[0][0] == "test-gen-123"
        assert call_args[0][1]['type'] == 'generation_complete'

    @pytest.mark.asyncio
    async def test_handle_generation_output_with_output(self, controller, sample_generation_status, sample_image):
        """Test handling generation output with actual output"""
        # Arrange
        controller.generation_orchestrator.get_generation_status.return_value = sample_generation_status
        controller._broadcast_generation_output = AsyncMock()
        
        output = ProgressGenerationOutput(
            pipe_id=2,
            state="Processing...",
            title="Running"
        )

        # Act
        await controller._handle_generation_output("test-gen-123", output)

        # Assert
        controller._broadcast_generation_output.assert_called_once_with(
            "test-gen-123", output, sample_generation_status
        )

    @pytest.mark.asyncio
    async def test_handle_generation_output_no_status(self, controller):
        """Test handling output when generation status not found"""
        # Arrange
        controller.generation_orchestrator.get_generation_status.return_value = None
        controller.connection_manager.broadcast_to_generation = AsyncMock()

        output = ProgressGenerationOutput(pipe_id=2, state="Processing...", title="Running")

        # Act
        await controller._handle_generation_output("nonexistent", output)

        # Assert - should return early, no broadcast
        controller.connection_manager.broadcast_to_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_generation_output_no_subscribers(self, controller, sample_generation_status):
        """Test broadcasting with no subscribers"""
        # Arrange
        controller.connection_manager.generation_connections = {}
        controller.connection_manager.broadcast_to_generation = AsyncMock()

        output = ProgressGenerationOutput(pipe_id=2, state="Processing...", title="Running")

        # Act
        await controller._broadcast_generation_output("test-gen-123", output, sample_generation_status)

        # Assert - should not broadcast
        controller.connection_manager.broadcast_to_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_generation_output_records_run_report_without_subscribers(
        self, controller, sample_generation_status
    ):
        """A generation nobody is watching over WebSocket must still get a run
        report - recording must not be gated on has_subscribers the way the
        WebSocket broadcast itself is."""
        controller.connection_manager.generation_connections = {}
        controller.connection_manager.broadcast_to_generation = AsyncMock()

        output = ProgressGenerationOutput(pipe_id=2, state="Processing...", title="Running")

        await controller._broadcast_generation_output("test-gen-123", output, sample_generation_status)

        controller.run_report_recorder.record_output.assert_called_once()
        assert controller.run_report_recorder.record_output.call_args[0][0] == "test-gen-123"

    @pytest.mark.asyncio
    async def test_handle_generation_output_completion_flushes_run_report(
        self, controller, sample_generation_status
    ):
        """The completion sentinel (output=None) must flush the run report -
        it is the only terminal signal every generation reaches, regardless
        of whether it completed, failed, or was cancelled."""
        controller.generation_orchestrator.get_generation_status.return_value = sample_generation_status
        controller.connection_manager.broadcast_to_generation = AsyncMock()

        await controller._handle_generation_output("test-gen-123", None)

        controller.run_report_recorder.flush.assert_called_once()
        assert controller.run_report_recorder.flush.call_args[0][0] == "test-gen-123"

    @pytest.mark.asyncio
    async def test_broadcast_generation_output_with_subscribers(self, controller, sample_generation_status):
        """Test successful broadcasting with subscribers"""
        # Arrange
        controller.connection_manager.generation_connections = {"test-gen-123": ["client1"]}
        controller.connection_manager.broadcast_to_generation = AsyncMock()
        
        output = ProgressGenerationOutput(
            pipe_id=2,
            state="Processing...",
            title="Running"
        )

        # Mock the GenerationOutputSerializer
        with patch('src.features.generation.routes.GenerationOutputSerializer') as mock_mapper_class:
            mock_mapper = Mock()
            mock_mapper.serialize_output.return_value = {
                'type': 'generation_status',
                'generation_id': 'test-gen-123',
                'pipe_id': 2,
                'progress': 0.5
            }
            mock_mapper_class.return_value = mock_mapper

            # Act
            await controller._broadcast_generation_output("test-gen-123", output, sample_generation_status)

            # Assert
            controller.connection_manager.broadcast_to_generation.assert_called_once()
            call_args = controller.connection_manager.broadcast_to_generation.call_args
            assert call_args[0][0] == "test-gen-123"
            message = call_args[0][1]
            assert message['type'] == 'generation_status'
            assert message['progress'] == 0.5  # progress is kept and updated from status

    @pytest.mark.asyncio
    async def test_broadcast_generation_output_serialization_error(self, controller, sample_generation_status):
        """Test broadcasting with serialization error"""
        # Arrange
        controller.connection_manager.generation_connections = {"test-gen-123": ["client1"]}
        controller.connection_manager.broadcast_to_generation = AsyncMock()
        
        output = ProgressGenerationOutput(pipe_id=2, state="Processing...", title="Running")

        # Mock the GenerationOutputSerializer to raise exception
        with patch('src.features.generation.routes.GenerationOutputSerializer') as mock_mapper_class:
            mock_mapper = Mock()
            mock_mapper.serialize_output.side_effect = Exception("Serialization failed")
            mock_mapper_class.return_value = mock_mapper

            # Act
            await controller._broadcast_generation_output("test-gen-123", output, sample_generation_status)

            # Assert - error message should be broadcast
            controller.connection_manager.broadcast_to_generation.assert_called_once()
            call_args = controller.connection_manager.broadcast_to_generation.call_args
            assert call_args[0][0] == "test-gen-123"
            message = call_args[0][1]
            assert message['type'] == 'generation_error'
            assert 'Serialization failed' in message['data']['error']

    @pytest.mark.asyncio
    async def test_handle_websocket(self, controller):
        """Test WebSocket handling delegation"""
        # Arrange
        mock_websocket = Mock()
        client_id = "test-client-123"
        mock_user = Mock()
        controller.websocket_handler.handle_websocket = AsyncMock()

        # Act
        await controller.handle_websocket(mock_websocket, client_id, mock_user)

        # Assert
        controller.websocket_handler.handle_websocket.assert_called_once_with(
            mock_websocket,
            client_id,
            controller.generation_orchestrator.status_tracker,
            mock_user
        )


class TestGenerationHistoryFilesIntegration:
    """Critical integration tests for history page file loading.
    
    These tests verify the end-to-end flow from generation to history API
    that ensures images are properly persisted and can be loaded by the frontend.
    """
    
    def test_image_generation_creates_database_file_record(self):
        """
        CRITICAL TEST: Verify that image generation creates database file records.

        This test would have FAILED before the fix, catching the bug where:
        - Images were saved to disk
        - But no database records were created
        - History API returned empty files arrays
        - Frontend couldn't load images
        """
        generation_id = "test_gen_123"
        user_id = "test_user_456"

        # Create a test image (simulating pipe output)
        test_image = Image.new('RGB', (100, 100), color='red')

        # Create image output (non-temporary = should be saved to DB)
        image_output = ImageGenerationOutput(
            image=test_image,
            temporary=False,  # This is the key - permanent images should create DB records
            pipe_name="generator"
        )

        # Mock successful file creation
        from src.features.generation.records import File
        mock_file = File(
            id="file_123",
            file_path=f"outputs/2024-01-01/{generation_id}/0.png",
            file_type="IMAGE",
            user_id=user_id,
            file_size=1000,
            pipe_name="generator",
            is_final=True
        )

        # Process the image output - mock _save_image and _save_file_record on the handler
        handler = ImageGenerationOutputHandler(generation_id, user_id)

        # Mock image save to avoid file system operations (_save_image returns tuple of (path, thumbnails))
        save_return_value = (f"outputs/2024-01-01/{generation_id}/0.png", {"small": "thumb.jpg"})
        with patch.object(handler, '_save_image', return_value=save_return_value), \
             patch.object(handler, '_save_file_record', return_value=mock_file) as mock_save_record:
            result = handler.handle(image_output)

            # CRITICAL ASSERTION: Verify database file record was created
            mock_save_record.assert_called_once()

            # Verify the method was called with correct args
            call_args = mock_save_record.call_args
            assert call_args[0][0] == f"outputs/2024-01-01/{generation_id}/0.png"  # file_path
            assert call_args[0][1] == image_output  # output object

            # Verify handler result includes file metadata
            assert result['processed'] is True
            assert result['file_id'] == "file_123"  # This was missing before the fix
    
    def test_history_api_file_structure(self):
        """
        Test that files have the structure expected by the history API.
        
        This verifies the contract between the file persistence and frontend.
        """
        from src.features.generation.records import File, Generation
        
        # Create test generation with files (simulating successful generation)
        generation_id = "test_gen_123"
        user_id = "test_user_456"
        
        generation = Generation(
            id=generation_id,
            preset_id="test_preset",
            form_data={},
            user_id=user_id,
            status="completed"
        )
        
        # Files that should be created by our handlers
        test_files = [
            File(
                id="file_1",
                file_path=f"outputs/2024-01-01/{generation_id}/0.png",
                file_type="image",
                user_id=user_id,
                file_size=1000,
                pipe_name="generator",
                is_final=True
            ),
            File(
                id="file_2",
                file_path=f"outputs/2024-01-01/{generation_id}/1.png", 
                file_type="image",
                user_id=user_id,
                file_size=1200,
                pipe_name="generator",
                is_final=True
            )
        ]
        
        # Simulate what the history API does
        generation.files = test_files
        generation_dict = generation.to_dict(include_files=True)
        
        # CRITICAL ASSERTION: Files array should not be empty
        assert 'files' in generation_dict
        assert generation_dict['files'] != []  # This was the original bug!
        assert len(generation_dict['files']) == 2
        
        # Verify file structure matches what frontend expects
        for file_dict in generation_dict['files']:
            assert 'id' in file_dict  # Needed for /api/files/{id}/blob
            assert 'file_path' in file_dict
            assert 'file_type' in file_dict
            assert 'is_final' in file_dict
            assert file_dict['file_type'] == 'image'
            assert isinstance(file_dict['id'], (str, int))
    
    def test_blob_endpoint_compatibility(self):
        """
        Test that file records are compatible with the blob endpoint.
        
        This ensures files can be served by /api/files/{file_id}/blob
        """
        from src.features.generation.records import File
        
        # File record as created by our handlers
        file_record = File(
            id="file_blob_test",
            file_path="outputs/2024-01-01/gen_123/0.png", 
            file_type="image",
            user_id="user_123",
            file_size=1500,
            pipe_name="generator",
            is_final=True
        )
        
        # Verify structure matches blob endpoint requirements
        file_dict = file_record.to_dict()
        
        # These fields are required by the blob endpoint
        assert file_dict['id'] is not None
        assert file_dict['file_path'] is not None 
        assert file_dict['user_id'] is not None  # For security check
        assert file_dict['file_type'] == 'image'
        
        # File path should be resolvable
        assert file_dict['file_path'].endswith('.png')
        assert 'outputs/' in file_dict['file_path']
    
    def test_temporary_images_do_not_create_database_records(self):
        """Test that temporary images don't clutter the database."""
        generation_id = "test_gen_123" 
        user_id = "test_user_456"
        
        # Create temporary image output (like progress previews)
        image_output = ImageGenerationOutput(
            image=Image.new('RGB', (50, 50), color='blue'),
            temporary=True,  # Temporary images should NOT create DB records
            pipe_name="generator"
        )
        
        with patch('src.features.generation.handlers.image_handler.generation_repo.add_file') as mock_add_file:
            handler = ImageGenerationOutputHandler(generation_id, user_id)
            result = handler.handle(image_output)
            
            # Verify NO database record was created for temporary image
            mock_add_file.assert_not_called()
            
            # Verify the response indicates temporary status
            assert result['processed'] is True
            assert result['temporary'] is True
            assert 'file_id' not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
