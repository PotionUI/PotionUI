"""
Tests for WorkspaceController.

Uses the same direct-controller-method pattern as other controller tests
(e.g., test_tag_controller.py): instantiate the controller with a mocked manager
and call methods directly rather than going through the FastAPI router.
"""

import pytest
from unittest.mock import Mock, AsyncMock
from datetime import datetime

from src.features.workspaces.routes import WorkspaceController
from src.features.workspaces.dto import (
    SaveWorkspaceRequest,
    UpdateWorkspaceRequest,
    WorkspaceResponse,
)
from src.features.workspaces.records import Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workspace_response(
    workspace_id='ws-1',
    name='Test WS',
    data=None,
    created_at='2024-01-01T12:00:00',
    updated_at='2024-06-01T12:00:00',
):
    """Build a WorkspaceResponse DTO."""
    return WorkspaceResponse(
        id=workspace_id,
        name=name,
        data=data or {'tabs': []},
        created_at=created_at,
        updated_at=updated_at,
    )


def _make_workspace(workspace_id='ws-1', user_id='user-1', name='Test WS', data=None):
    """Build a Workspace database record."""
    return Workspace(
        id=workspace_id,
        user_id=user_id,
        name=name,
        data=data or {'tabs': []},
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=datetime(2024, 6, 1, 12, 0, 0),
    )


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestWorkspaceController:
    """Comprehensive tests for WorkspaceController."""

    @pytest.fixture
    def mock_manager(self):
        """Mock WorkspaceManager."""
        return Mock()

    @pytest.fixture
    def mock_repository(self):
        """Mock WorkspaceRepository."""
        return Mock()

    @pytest.fixture
    def controller(self, mock_manager, mock_repository):
        """WorkspaceController with mocked manager and repository."""
        return WorkspaceController(workspace_manager=mock_manager, workspace_repository=mock_repository)

    # ========== get_workspaces (repository-backed pure read) ==========

    @pytest.mark.asyncio
    async def test_get_workspaces_success(self, controller, mock_repository):
        """Returns success response with list of workspace dicts."""
        ws1 = _make_workspace('ws-1', name='Alpha')
        ws2 = _make_workspace('ws-2', name='Beta')
        mock_repository.get_by_user.return_value = [ws1, ws2]

        result = await controller.get_workspaces('user-1')

        assert result.success is True
        assert isinstance(result.data, list)
        assert len(result.data) == 2
        # Model dumps contain 'id' and 'name'
        ids = [item['id'] for item in result.data]
        assert 'ws-1' in ids
        assert 'ws-2' in ids
        mock_repository.get_by_user.assert_called_once_with('user-1')

    @pytest.mark.asyncio
    async def test_get_workspaces_empty(self, controller, mock_repository):
        """Returns success with empty list when user has no workspaces."""
        mock_repository.get_by_user.return_value = []

        result = await controller.get_workspaces('user-1')

        assert result.success is True
        assert result.data == []

    @pytest.mark.asyncio
    async def test_get_workspaces_error(self, controller, mock_repository):
        """Returns error APIResponse on exception."""
        mock_repository.get_by_user.side_effect = Exception("DB failure")

        result = await controller.get_workspaces('user-1')

        assert result.success is False
        assert result.error == 'get_workspaces_failed'
        assert 'DB failure' in result.message

    # ========== get_workspace_by_id (repository-backed pure read) ==========

    @pytest.mark.asyncio
    async def test_get_workspace_by_id_success(self, controller, mock_repository):
        """Returns success with workspace dict for the correct owner."""
        ws = _make_workspace('ws-1', user_id='user-1', name='My WS')
        mock_repository.get_by_id.return_value = ws

        result = await controller.get_workspace_by_id('ws-1', 'user-1')

        assert result.success is True
        assert result.data['id'] == 'ws-1'
        assert result.data['name'] == 'My WS'
        mock_repository.get_by_id.assert_called_once_with('ws-1')

    @pytest.mark.asyncio
    async def test_get_workspace_by_id_not_found(self, controller, mock_repository):
        """Returns workspace_not_found error when the workspace does not exist."""
        mock_repository.get_by_id.return_value = None

        result = await controller.get_workspace_by_id('ws-missing', 'user-1')

        assert result.success is False
        assert result.error == 'workspace_not_found'

    @pytest.mark.asyncio
    async def test_get_workspace_by_id_access_denied(self, controller, mock_repository):
        """Returns workspace_access_denied error when owned by a different user."""
        ws = _make_workspace('ws-1', user_id='owner-user')
        mock_repository.get_by_id.return_value = ws

        result = await controller.get_workspace_by_id('ws-1', 'attacker')

        assert result.success is False
        assert result.error == 'workspace_access_denied'

    @pytest.mark.asyncio
    async def test_get_workspace_by_id_generic_error(self, controller, mock_repository):
        """Returns get_workspace_failed for unexpected exceptions."""
        mock_repository.get_by_id.side_effect = Exception("Unexpected error")

        result = await controller.get_workspace_by_id('ws-1', 'user-1')

        assert result.success is False
        assert result.error == 'get_workspace_failed'

    # ========== save_workspace ==========

    @pytest.mark.asyncio
    async def test_save_workspace_success(self, controller, mock_manager):
        """Returns success with workspace dict and confirmation message."""
        ws = _make_workspace_response('ws-new', name='New WS')
        mock_manager.save_workspace.return_value = ws

        request = SaveWorkspaceRequest(name='New WS', data={'tabs': []})
        result = await controller.save_workspace('user-1', request)

        assert result.success is True
        assert result.data['id'] == 'ws-new'
        assert 'New WS' in result.message
        mock_manager.save_workspace.assert_called_once_with('user-1', request)

    @pytest.mark.asyncio
    async def test_save_workspace_value_error(self, controller, mock_manager):
        """Returns save_workspace_failed on ValueError."""
        mock_manager.save_workspace.side_effect = ValueError("Validation error")

        result = await controller.save_workspace(
            'user-1', SaveWorkspaceRequest(name='WS', data={})
        )

        assert result.success is False
        assert result.error == 'save_workspace_failed'
        assert 'Validation error' in result.message

    @pytest.mark.asyncio
    async def test_save_workspace_generic_error(self, controller, mock_manager):
        """Returns save_workspace_failed on generic Exception."""
        mock_manager.save_workspace.side_effect = Exception("DB down")

        result = await controller.save_workspace(
            'user-1', SaveWorkspaceRequest(name='WS', data={})
        )

        assert result.success is False
        assert result.error == 'save_workspace_failed'

    # ========== update_workspace ==========

    @pytest.mark.asyncio
    async def test_update_workspace_success(self, controller, mock_manager):
        """Returns success with updated workspace data."""
        ws = _make_workspace_response('ws-1', name='Updated WS')
        mock_manager.update_workspace.return_value = ws

        request = UpdateWorkspaceRequest(name='Updated WS')
        result = await controller.update_workspace('ws-1', 'user-1', request)

        assert result.success is True
        assert result.data['name'] == 'Updated WS'
        assert 'updated' in result.message.lower()
        mock_manager.update_workspace.assert_called_once_with('ws-1', 'user-1', request)

    @pytest.mark.asyncio
    async def test_update_workspace_not_found(self, controller, mock_manager):
        """Returns workspace_not_found when manager raises 'not found'."""
        mock_manager.update_workspace.side_effect = ValueError("Workspace not found")

        result = await controller.update_workspace(
            'ws-missing', 'user-1', UpdateWorkspaceRequest()
        )

        assert result.success is False
        assert result.error == 'workspace_not_found'

    @pytest.mark.asyncio
    async def test_update_workspace_access_denied(self, controller, mock_manager):
        """Returns workspace_access_denied when manager raises 'access denied'."""
        mock_manager.update_workspace.side_effect = ValueError("Access denied to this workspace")

        result = await controller.update_workspace(
            'ws-1', 'attacker', UpdateWorkspaceRequest()
        )

        assert result.success is False
        assert result.error == 'workspace_access_denied'

    @pytest.mark.asyncio
    async def test_update_workspace_generic_error(self, controller, mock_manager):
        """Returns update_workspace_failed for unexpected exceptions."""
        mock_manager.update_workspace.side_effect = Exception("Unexpected")

        result = await controller.update_workspace(
            'ws-1', 'user-1', UpdateWorkspaceRequest()
        )

        assert result.success is False
        assert result.error == 'update_workspace_failed'

    # ========== delete_workspace ==========

    @pytest.mark.asyncio
    async def test_delete_workspace_success(self, controller, mock_manager):
        """Returns success with deletion message."""
        mock_manager.delete_workspace.return_value = "Workspace 'My WS' deleted successfully"

        result = await controller.delete_workspace('ws-1', 'user-1')

        assert result.success is True
        assert "deleted successfully" in result.message
        mock_manager.delete_workspace.assert_called_once_with('ws-1', 'user-1')

    @pytest.mark.asyncio
    async def test_delete_workspace_not_found(self, controller, mock_manager):
        """Returns workspace_not_found when manager raises 'not found'."""
        mock_manager.delete_workspace.side_effect = ValueError("Workspace not found")

        result = await controller.delete_workspace('ws-missing', 'user-1')

        assert result.success is False
        assert result.error == 'workspace_not_found'

    @pytest.mark.asyncio
    async def test_delete_workspace_access_denied(self, controller, mock_manager):
        """Returns workspace_access_denied when manager raises 'access denied'."""
        mock_manager.delete_workspace.side_effect = ValueError("Access denied to this workspace")

        result = await controller.delete_workspace('ws-1', 'attacker')

        assert result.success is False
        assert result.error == 'workspace_access_denied'

    @pytest.mark.asyncio
    async def test_delete_workspace_generic_error(self, controller, mock_manager):
        """Returns delete_workspace_failed for unexpected exceptions."""
        mock_manager.delete_workspace.side_effect = Exception("DB failure")

        result = await controller.delete_workspace('ws-1', 'user-1')

        assert result.success is False
        assert result.error == 'delete_workspace_failed'
