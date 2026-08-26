"""Tests for src.features.sessions.operations."""
import pytest
from unittest.mock import Mock
from datetime import datetime

from src.features.sessions import operations
from src.features.sessions.operations.save import _merge_mode_data
from src.features.sessions.routes import SessionController
from src.features.sessions.dto import (
    Session,
    SaveSessionRequest,
    UpdateSessionRequest,
)


class TestSessionControllerRead:
    """Tests for the sessions feature's read operations.

    get_sessions_for_preset/get_session_by_id are pure DB reads and live on
    SessionController (repository-backed), not in operations.
    """

    @pytest.fixture
    def mock_session_repo(self):
        """Mock session repository."""
        return Mock()

    @pytest.fixture
    def controller(self, mock_session_repo):
        """Create controller with a mocked repository (operations unused for reads)."""
        return SessionController(
            session_repository=mock_session_repo,
            plugin_registry=Mock(),
        )

    @pytest.fixture
    def sample_session(self):
        """Sample session DTO."""
        return Session(
            id="session-123",
            user_id="user-123",
            preset_id="preset-456",
            name="Test Session",
            data={"key": "value"},
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

    @pytest.mark.asyncio
    async def test_get_sessions_for_preset_success(self, controller, mock_session_repo, sample_session):
        """Test getting all sessions for a preset."""
        mock_session_repo.get_by_user_and_preset.return_value = [sample_session]

        response = await controller.get_sessions_for_preset("user-123", "preset-456")

        assert len(response.data) == 1
        assert response.data[0]["id"] == "session-123"
        assert response.data[0]["name"] == "Test Session"
        assert "user_id" not in response.data[0]  # Should not include user_id
        mock_session_repo.get_by_user_and_preset.assert_called_once_with("user-123", "preset-456")

    @pytest.mark.asyncio
    async def test_get_sessions_for_preset_empty(self, controller, mock_session_repo):
        """Test getting sessions when none exist."""
        mock_session_repo.get_by_user_and_preset.return_value = []

        response = await controller.get_sessions_for_preset("user-123", "preset-456")

        assert len(response.data) == 0

    @pytest.mark.asyncio
    async def test_get_session_by_id_success(self, controller, mock_session_repo, sample_session):
        """Test getting a session by ID."""
        mock_session_repo.get_by_id.return_value = sample_session

        response = await controller.get_session_by_id("user-123", "session-123")

        assert response.data["id"] == "session-123"
        assert response.data["name"] == "Test Session"
        assert "user_id" not in response.data
        mock_session_repo.get_by_id.assert_called_once_with("session-123")

    @pytest.mark.asyncio
    async def test_get_session_by_id_not_found(self, controller, mock_session_repo):
        """Test getting a non-existent session."""
        mock_session_repo.get_by_id.return_value = None

        response = await controller.get_session_by_id("user-123", "nonexistent")

        assert response.success is False
        assert response.error == "session_not_found"

    @pytest.mark.asyncio
    async def test_get_session_by_id_access_denied(self, controller, mock_session_repo, sample_session):
        """Test getting a session owned by another user."""
        mock_session_repo.get_by_id.return_value = sample_session

        response = await controller.get_session_by_id("other-user", "session-123")

        assert response.success is False
        assert response.error == "session_access_denied"


class TestSaveSessionCreate:
    """Tests for operations.save_session create path."""

    @pytest.fixture
    def mock_session_repo(self):
        """Mock session repository."""
        return Mock()

    @pytest.fixture
    def mock_plugin_registry(self):
        """Mock plugin registry."""
        registry = Mock()
        context = Mock()
        context.data = {}
        registry.execute_hook.return_value = (context, [])
        return registry

    @pytest.fixture
    def sample_save_request(self):
        """Sample save session request."""
        return SaveSessionRequest(
            preset_id="preset-456",
            name="New Session",
            data={"form_data": "value"},
            mode=None
        )

    def test_save_session_creates_new(self, mock_session_repo, mock_plugin_registry, sample_save_request):
        """Test saving a new session."""
        mock_session_repo.get_by_user_preset_and_name.return_value = None
        mock_session_repo.create.return_value = Session(
            id="new-session-id",
            user_id="user-123",
            preset_id="preset-456",
            name="New Session",
            data={"form_data": "value"},
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        result, message = operations.save_session(
            mock_session_repo, mock_plugin_registry, None, None, "user-123", sample_save_request
        )

        assert result["name"] == "New Session"
        assert "saved successfully" in message
        mock_session_repo.create.assert_called_once()

    def test_save_session_updates_existing(self, mock_session_repo, mock_plugin_registry, sample_save_request):
        """Test saving a session that already exists updates it."""
        existing = Session(
            id="existing-id",
            user_id="user-123",
            preset_id="preset-456",
            name="New Session",
            data={"old_data": "old_value"},
            created_at=datetime.now()
        )
        mock_session_repo.get_by_user_preset_and_name.return_value = existing
        mock_session_repo.update.return_value = Session(
            id="existing-id",
            user_id="user-123",
            preset_id="preset-456",
            name="New Session",
            data={"form_data": "value"},
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        result, message = operations.save_session(
            mock_session_repo, mock_plugin_registry, None, None, "user-123", sample_save_request
        )

        assert result["id"] == "existing-id"
        assert "updated successfully" in message
        mock_session_repo.update.assert_called_once()
        mock_session_repo.create.assert_not_called()

    def test_save_session_blocked_by_hook(self, mock_session_repo, mock_plugin_registry, sample_save_request):
        """Test session creation blocked by plugin hook."""
        mock_session_repo.get_by_user_preset_and_name.return_value = None
        context = Mock()
        context.data = {"blocked": True, "block_reason": "Plugin blocked this"}
        mock_plugin_registry.execute_hook.return_value = (context, [])

        with pytest.raises(ValueError, match="Plugin blocked this"):
            operations.save_session(mock_session_repo, mock_plugin_registry, None, None, "user-123", sample_save_request)

        mock_session_repo.create.assert_not_called()


class TestUpdateSession:
    """Tests for operations.update_session."""

    @pytest.fixture
    def mock_session_repo(self):
        """Mock session repository."""
        return Mock()

    @pytest.fixture
    def mock_plugin_registry(self):
        """Mock plugin registry."""
        registry = Mock()
        context = Mock()
        context.data = {}
        registry.execute_hook.return_value = (context, [])
        return registry

    @pytest.fixture
    def sample_session(self):
        """Sample session DTO."""
        return Session(
            id="session-123",
            user_id="user-123",
            preset_id="preset-456",
            name="Test Session",
            data={"key": "value"},
            created_at=datetime.now()
        )

    @pytest.fixture
    def sample_update_request(self):
        """Sample update session request."""
        return UpdateSessionRequest(
            name="Updated Session",
            data={"new_key": "new_value"}
        )

    def test_update_session_success(self, mock_session_repo, mock_plugin_registry, sample_session, sample_update_request):
        """Test updating a session."""
        mock_session_repo.get_by_id.return_value = sample_session
        mock_session_repo.get_by_user_preset_and_name.return_value = None
        mock_session_repo.update.return_value = Session(
            id="session-123",
            user_id="user-123",
            preset_id="preset-456",
            name="Updated Session",
            data={"new_key": "new_value"},
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        result = operations.update_session(
            mock_session_repo, mock_plugin_registry, None, None, "user-123", "session-123", sample_update_request
        )

        assert result["name"] == "Updated Session"
        mock_session_repo.update.assert_called_once()

    def test_update_session_not_found(self, mock_session_repo, mock_plugin_registry, sample_update_request):
        """Test updating a non-existent session."""
        mock_session_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Session not found"):
            operations.update_session(
                mock_session_repo, mock_plugin_registry, None, None, "user-123", "nonexistent", sample_update_request
            )

    def test_update_session_access_denied(self, mock_session_repo, mock_plugin_registry, sample_session, sample_update_request):
        """Test updating a session owned by another user."""
        mock_session_repo.get_by_id.return_value = sample_session

        with pytest.raises(ValueError, match="Access denied"):
            operations.update_session(
                mock_session_repo, mock_plugin_registry, None, None, "other-user", "session-123", sample_update_request
            )

        mock_session_repo.update.assert_not_called()

    def test_update_session_name_conflict(self, mock_session_repo, mock_plugin_registry, sample_session, sample_update_request):
        """Test updating session to a name that already exists."""
        mock_session_repo.get_by_id.return_value = sample_session
        existing_with_name = Session(
            id="other-session",
            user_id="user-123",
            preset_id="preset-456",
            name="Updated Session",
            data={},
            created_at=datetime.now()
        )
        mock_session_repo.get_by_user_preset_and_name.return_value = existing_with_name

        with pytest.raises(ValueError, match="already exists"):
            operations.update_session(
                mock_session_repo, mock_plugin_registry, None, None, "user-123", "session-123", sample_update_request
            )

        mock_session_repo.update.assert_not_called()

    def test_update_session_blocked_by_hook(
        self,
        mock_session_repo,
        mock_plugin_registry,
        sample_session,
        sample_update_request
    ):
        """Test session update blocked by plugin hook."""
        mock_session_repo.get_by_id.return_value = sample_session
        mock_session_repo.get_by_user_preset_and_name.return_value = None
        context = Mock()
        context.data = {"blocked": True, "block_reason": "Update blocked"}
        mock_plugin_registry.execute_hook.return_value = (context, [])

        with pytest.raises(ValueError, match="Update blocked"):
            operations.update_session(
                mock_session_repo, mock_plugin_registry, None, None, "user-123", "session-123", sample_update_request
            )

        mock_session_repo.update.assert_not_called()


class TestDeleteSession:
    """Tests for operations.delete_session."""

    @pytest.fixture
    def mock_session_repo(self):
        """Mock session repository."""
        return Mock()

    @pytest.fixture
    def mock_plugin_registry(self):
        """Mock plugin registry."""
        registry = Mock()
        context = Mock()
        context.data = {}
        registry.execute_hook.return_value = (context, [])
        return registry

    @pytest.fixture
    def sample_session(self):
        """Sample session DTO."""
        return Session(
            id="session-123",
            user_id="user-123",
            preset_id="preset-456",
            name="Test Session",
            data={"key": "value"},
            created_at=datetime.now()
        )

    def test_delete_session_success(self, mock_session_repo, mock_plugin_registry, sample_session):
        """Test deleting a session."""
        mock_session_repo.get_by_id.return_value = sample_session
        mock_session_repo.delete.return_value = True

        result = operations.delete_session(mock_session_repo, mock_plugin_registry, "user-123", "session-123")

        assert "deleted successfully" in result
        mock_session_repo.delete.assert_called_once_with("session-123")

    def test_delete_session_not_found(self, mock_session_repo, mock_plugin_registry):
        """Test deleting a non-existent session."""
        mock_session_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Session not found"):
            operations.delete_session(mock_session_repo, mock_plugin_registry, "user-123", "nonexistent")

        mock_session_repo.delete.assert_not_called()

    def test_delete_session_access_denied(self, mock_session_repo, mock_plugin_registry, sample_session):
        """Test deleting a session owned by another user."""
        mock_session_repo.get_by_id.return_value = sample_session

        with pytest.raises(ValueError, match="Access denied"):
            operations.delete_session(mock_session_repo, mock_plugin_registry, "other-user", "session-123")

        mock_session_repo.delete.assert_not_called()

    def test_delete_session_db_failure(self, mock_session_repo, mock_plugin_registry, sample_session):
        """Test session deletion when database fails."""
        mock_session_repo.get_by_id.return_value = sample_session
        mock_session_repo.delete.return_value = False

        with pytest.raises(ValueError, match="Failed to delete"):
            operations.delete_session(mock_session_repo, mock_plugin_registry, "user-123", "session-123")

    def test_delete_session_blocked_by_hook(
        self,
        mock_session_repo,
        mock_plugin_registry,
        sample_session
    ):
        """Test session deletion blocked by plugin hook."""
        mock_session_repo.get_by_id.return_value = sample_session
        context = Mock()
        context.data = {"blocked": True, "block_reason": "Delete blocked"}
        mock_plugin_registry.execute_hook.return_value = (context, [])

        with pytest.raises(ValueError, match="Delete blocked"):
            operations.delete_session(mock_session_repo, mock_plugin_registry, "user-123", "session-123")

        mock_session_repo.delete.assert_not_called()


class TestMergeModeData:
    """Tests for _merge_mode_data."""

    def test_merge_with_explicit_mode(self):
        """Test merging data with explicit mode."""
        existing_data = {"txt2img": {"prompt": "old"}}
        new_data = {"prompt": "new", "steps": 20}

        result = _merge_mode_data(existing_data, new_data, mode="txt2img")

        assert result["txt2img"] == {"prompt": "new", "steps": 20}

    def test_merge_with_selected_mode(self):
        """Test merging data when selectedMode is in new_data."""
        existing_data = {"txt2img": {"prompt": "old"}}
        new_data = {"selectedMode": "txt2img", "prompt": "new"}

        result = _merge_mode_data(existing_data, new_data, mode=None)

        assert result["txt2img"] == {"selectedMode": "txt2img", "prompt": "new"}

    def test_merge_mode_based_data_structure(self):
        """Test merging when both have mode-based structure."""
        existing_data = {"txt2img": {"prompt": "old"}, "img2img": {"strength": 0.5}}
        new_data = {"txt2img": {"prompt": "new"}}

        result = _merge_mode_data(existing_data, new_data, mode=None)

        assert result["txt2img"] == {"prompt": "new"}
        assert result["img2img"] == {"strength": 0.5}

    def test_merge_fallback_complete_replacement(self):
        """Test fallback to complete replacement when mode can't be detected."""
        existing_data = {"key": "value"}
        new_data = {"new_key": "new_value"}

        result = _merge_mode_data(existing_data, new_data, mode=None)

        assert result == {"new_key": "new_value"}

    def test_merge_mode_keyed_disjoint_keys_preserves_other_modes(self):
        """Saving in a new mode must not wipe data stored under other modes."""
        existing_data = {"txt2vid": {"prompt": "txt prompt", "formData": {"a": 1}}}
        new_data = {"img2vid": {"prompt": "img prompt", "formData": {"b": 2}}}

        result = _merge_mode_data(existing_data, new_data, mode=None)

        assert result["txt2vid"] == {"prompt": "txt prompt", "formData": {"a": 1}}
        assert result["img2vid"] == {"prompt": "img prompt", "formData": {"b": 2}}

    def test_merge_mode_keyed_into_empty_existing(self):
        """First save of a mode-keyed session produces the expected shape."""
        result = _merge_mode_data({}, {"txt2vid": {"prompt": "hi"}}, mode=None)

        assert result == {"txt2vid": {"prompt": "hi"}}


class TestSessionHooks:
    """Tests for hook execution in operations.save_session."""

    @pytest.fixture
    def mock_session_repo(self):
        """Mock session repository."""
        repo = Mock()
        repo.get_by_user_preset_and_name.return_value = None
        repo.get_by_id.return_value = None
        repo.delete.return_value = True
        return repo

    @pytest.fixture
    def mock_plugin_registry(self):
        """Mock plugin registry."""
        return Mock()

    def test_before_create_hook_executed(self, mock_session_repo, mock_plugin_registry):
        """Test that before_create hook is executed."""
        context = Mock()
        context.data = {}
        mock_plugin_registry.execute_hook.return_value = (context, [])
        mock_session_repo.create.return_value = Session(
            id="new-id",
            user_id="user-123",
            preset_id="preset-456",
            name="Test",
            data={},
            created_at=datetime.now()
        )

        request = SaveSessionRequest(preset_id="preset-456", name="Test", data={})
        operations.save_session(mock_session_repo, mock_plugin_registry, None, None, "user-123", request)

        # Check that execute_hook was called (before and after)
        assert mock_plugin_registry.execute_hook.call_count == 2

    def test_after_create_hook_executed(self, mock_session_repo, mock_plugin_registry):
        """Test that after_create hook is executed."""
        context = Mock()
        context.data = {}
        mock_plugin_registry.execute_hook.return_value = (context, [])
        mock_session_repo.create.return_value = Session(
            id="new-id",
            user_id="user-123",
            preset_id="preset-456",
            name="Test",
            data={},
            created_at=datetime.now()
        )

        request = SaveSessionRequest(preset_id="preset-456", name="Test", data={})
        operations.save_session(mock_session_repo, mock_plugin_registry, None, None, "user-123", request)

        # Verify both hooks were called
        calls = mock_plugin_registry.execute_hook.call_args_list
        hook_names = [call[0][0] for call in calls]
        assert "session.before_create" in hook_names
        assert "session.after_create" in hook_names

    def test_hook_can_block_create_operation(self, mock_session_repo, mock_plugin_registry):
        """Test that hooks can block create operations."""
        context = Mock()
        context.data = {"blocked": True, "block_reason": "Blocked by test"}
        mock_plugin_registry.execute_hook.return_value = (context, [])

        request = SaveSessionRequest(preset_id="preset-456", name="Test", data={})

        with pytest.raises(ValueError, match="Blocked by test"):
            operations.save_session(mock_session_repo, mock_plugin_registry, None, None, "user-123", request)

        mock_session_repo.create.assert_not_called()

    def test_hook_can_modify_data(self, mock_session_repo, mock_plugin_registry):
        """Test that hooks can modify session data."""
        context = Mock()
        context.data = {"data": {"modified": True}}  # Hook modifies data
        mock_plugin_registry.execute_hook.return_value = (context, [])
        mock_session_repo.create.return_value = Session(
            id="new-id",
            user_id="user-123",
            preset_id="preset-456",
            name="Test",
            data={"modified": True},
            created_at=datetime.now()
        )

        request = SaveSessionRequest(preset_id="preset-456", name="Test", data={"original": True})
        result, _ = operations.save_session(mock_session_repo, mock_plugin_registry, None, None, "user-123", request)

        # Verify create was called with modified data
        create_call = mock_session_repo.create.call_args
        assert create_call[0][0].data == {"modified": True}
