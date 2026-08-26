"""
Tests for WorkspaceManager.

WorkspaceManager delegates all persistence to WorkspaceRepository and
raises ValueError for ownership or not-found conditions.
"""

import pytest
from unittest.mock import Mock
from datetime import datetime

from src.features.workspaces.manager import WorkspaceManager
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

def _make_workspace(
    workspace_id='ws-1',
    user_id='user-1',
    name='My Workspace',
    data=None,
):
    """Return a Workspace dataclass instance with sensible defaults."""
    return Workspace(
        id=workspace_id,
        user_id=user_id,
        name=name,
        data=data or {'tabs': []},
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=datetime(2024, 6, 1, 12, 0, 0),
    )


def _make_manager():
    """Return a WorkspaceManager with mocked dependencies."""
    mock_repo = Mock()
    mock_plugins = Mock()
    manager = WorkspaceManager(
        workspace_repository=mock_repo,
        plugin_registry=mock_plugins,
    )
    return manager, mock_repo, mock_plugins


def _make_controller():
    """Return a WorkspaceController with a mocked repository.

    get_workspaces/get_workspace_by_id are pure DB reads and live on the
    controller (repository-backed), not on WorkspaceManager.
    """
    mock_repo = Mock()
    controller = WorkspaceController(
        workspace_manager=Mock(),
        workspace_repository=mock_repo,
    )
    return controller, mock_repo


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestGetWorkspaces:
    """Tests for the workspaces feature's read: get_workspaces."""

    @pytest.mark.asyncio
    async def test_returns_list_of_workspace_responses(self):
        """get_workspaces returns a WorkspaceResponse dict for each repo result."""
        controller, mock_repo = _make_controller()
        ws1 = _make_workspace('ws-1', name='Alpha')
        ws2 = _make_workspace('ws-2', name='Beta')
        mock_repo.get_by_user.return_value = [ws1, ws2]

        response = await controller.get_workspaces('user-1')

        assert len(response.data) == 2
        assert response.data[0]['id'] == 'ws-1'
        assert response.data[0]['name'] == 'Alpha'
        assert response.data[1]['id'] == 'ws-2'
        assert response.data[1]['name'] == 'Beta'
        mock_repo.get_by_user.assert_called_once_with('user-1')

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_workspaces(self):
        """get_workspaces returns empty list when user has no workspaces."""
        controller, mock_repo = _make_controller()
        mock_repo.get_by_user.return_value = []

        response = await controller.get_workspaces('user-1')

        assert response.data == []

    @pytest.mark.asyncio
    async def test_response_excludes_user_id(self):
        """WorkspaceResponse must not expose user_id (security)."""
        controller, mock_repo = _make_controller()
        mock_repo.get_by_user.return_value = [_make_workspace()]

        response = await controller.get_workspaces('user-1')

        assert 'user_id' not in response.data[0]


class TestGetWorkspaceById:
    """Tests for the workspaces feature's read: get_workspace_by_id."""

    @pytest.mark.asyncio
    async def test_returns_workspace_response_when_found_and_owner_matches(self):
        """Returns a WorkspaceResponse dict for the correct owner."""
        controller, mock_repo = _make_controller()
        ws = _make_workspace(user_id='user-1')
        mock_repo.get_by_id.return_value = ws

        response = await controller.get_workspace_by_id('ws-1', 'user-1')

        assert response.success is True
        assert response.data['id'] == 'ws-1'
        mock_repo.get_by_id.assert_called_once_with('ws-1')

    @pytest.mark.asyncio
    async def test_raises_value_error_when_not_found(self):
        """Errors with workspace_not_found when the workspace does not exist."""
        controller, mock_repo = _make_controller()
        mock_repo.get_by_id.return_value = None

        response = await controller.get_workspace_by_id('ws-missing', 'user-1')

        assert response.success is False
        assert response.error == 'workspace_not_found'

    @pytest.mark.asyncio
    async def test_raises_value_error_when_wrong_user(self):
        """Errors with workspace_access_denied when owned by a different user."""
        controller, mock_repo = _make_controller()
        ws = _make_workspace(user_id='owner-user')
        mock_repo.get_by_id.return_value = ws

        response = await controller.get_workspace_by_id('ws-1', 'attacker-user')

        assert response.success is False
        assert response.error == 'workspace_access_denied'

    @pytest.mark.asyncio
    async def test_response_contains_correct_data(self):
        """Returned response dict carries the correct workspace data."""
        controller, mock_repo = _make_controller()
        ws = _make_workspace(data={'tabs': [{'name': 'T1'}]})
        mock_repo.get_by_id.return_value = ws

        response = await controller.get_workspace_by_id('ws-1', 'user-1')

        assert response.data['data'] == {'tabs': [{'name': 'T1'}]}


class TestSaveWorkspace:
    """Tests for WorkspaceManager.save_workspace."""

    def test_save_calls_repo_create_and_returns_response(self):
        """save_workspace calls repository.create and returns WorkspaceResponse."""
        manager, mock_repo, _ = _make_manager()
        created_ws = _make_workspace('ws-new', name='New WS')
        mock_repo.create.return_value = created_ws

        request = SaveWorkspaceRequest(name='New WS', data={'tabs': []})
        result = manager.save_workspace('user-1', request)

        mock_repo.create.assert_called_once()
        assert isinstance(result, WorkspaceResponse)
        assert result.name == 'New WS'

    def test_save_creates_workspace_with_correct_user_id(self):
        """The Workspace passed to repository.create has the given user_id."""
        manager, mock_repo, _ = _make_manager()
        mock_repo.create.side_effect = lambda ws: ws  # return the workspace itself

        request = SaveWorkspaceRequest(name='WS', data={})
        manager.save_workspace('user-42', request)

        created_arg = mock_repo.create.call_args[0][0]
        assert created_arg.user_id == 'user-42'
        assert created_arg.name == 'WS'

    def test_save_workspace_response_has_expected_fields(self):
        """Response includes id, name, data, and timestamps."""
        manager, mock_repo, _ = _make_manager()
        saved = _make_workspace('ws-99', name='Saved')
        mock_repo.create.return_value = saved

        result = manager.save_workspace('user-1', SaveWorkspaceRequest(name='Saved', data={}))

        assert result.id == 'ws-99'
        assert result.name == 'Saved'
        assert result.created_at is not None


class TestUpdateWorkspace:
    """Tests for WorkspaceManager.update_workspace."""

    def test_update_calls_repo_update_and_returns_response(self):
        """update_workspace calls repository.update and returns WorkspaceResponse."""
        manager, mock_repo, _ = _make_manager()
        existing = _make_workspace(name='Old Name', user_id='user-1')
        updated = _make_workspace(name='New Name', user_id='user-1')
        mock_repo.get_by_id.return_value = existing
        mock_repo.update.return_value = updated

        request = UpdateWorkspaceRequest(name='New Name')
        result = manager.update_workspace('ws-1', 'user-1', request)

        mock_repo.update.assert_called_once()
        assert isinstance(result, WorkspaceResponse)
        assert result.name == 'New Name'

    def test_update_applies_partial_name_only(self):
        """When only name is provided, data is carried over from existing workspace."""
        manager, mock_repo, _ = _make_manager()
        existing = _make_workspace(name='Old', data={'tabs': [{'name': 'T1'}]})
        mock_repo.get_by_id.return_value = existing
        mock_repo.update.side_effect = lambda ws: ws

        request = UpdateWorkspaceRequest(name='Updated Name', data=None)
        manager.update_workspace('ws-1', 'user-1', request)

        updated_arg = mock_repo.update.call_args[0][0]
        assert updated_arg.name == 'Updated Name'
        assert updated_arg.data == {'tabs': [{'name': 'T1'}]}  # unchanged

    def test_update_raises_value_error_when_not_found(self):
        """Raises ValueError when workspace is not found."""
        manager, mock_repo, _ = _make_manager()
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="not found"):
            manager.update_workspace('ws-missing', 'user-1', UpdateWorkspaceRequest())

    def test_update_raises_value_error_when_wrong_user(self):
        """Raises ValueError when workspace belongs to a different user."""
        manager, mock_repo, _ = _make_manager()
        ws = _make_workspace(user_id='owner')
        mock_repo.get_by_id.return_value = ws

        with pytest.raises(ValueError, match="[Aa]ccess denied"):
            manager.update_workspace('ws-1', 'attacker', UpdateWorkspaceRequest())


class TestDeleteWorkspace:
    """Tests for WorkspaceManager.delete_workspace."""

    def test_delete_calls_repo_delete_and_returns_message(self):
        """delete_workspace calls repository.delete and returns success string."""
        manager, mock_repo, _ = _make_manager()
        ws = _make_workspace(name='To Delete', user_id='user-1')
        mock_repo.get_by_id.return_value = ws
        mock_repo.delete.return_value = True

        result = manager.delete_workspace('ws-1', 'user-1')

        mock_repo.delete.assert_called_once_with('ws-1')
        assert isinstance(result, str)
        assert 'To Delete' in result

    def test_delete_raises_value_error_when_not_found(self):
        """Raises ValueError when workspace does not exist."""
        manager, mock_repo, _ = _make_manager()
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="not found"):
            manager.delete_workspace('ws-missing', 'user-1')

    def test_delete_raises_value_error_when_wrong_user(self):
        """Raises ValueError when workspace belongs to a different user."""
        manager, mock_repo, _ = _make_manager()
        ws = _make_workspace(user_id='real-owner')
        mock_repo.get_by_id.return_value = ws

        with pytest.raises(ValueError, match="[Aa]ccess denied"):
            manager.delete_workspace('ws-1', 'attacker')

    def test_delete_raises_value_error_when_repo_returns_false(self):
        """Raises ValueError when the underlying delete operation fails."""
        manager, mock_repo, _ = _make_manager()
        ws = _make_workspace(user_id='user-1')
        mock_repo.get_by_id.return_value = ws
        mock_repo.delete.return_value = False

        with pytest.raises(ValueError):
            manager.delete_workspace('ws-1', 'user-1')
