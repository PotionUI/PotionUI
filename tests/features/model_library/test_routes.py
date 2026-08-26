"""Tests for ModelCollectionController.

list_collections is a pure DB read (`repository.list(user_id)`), made
directly by the controller. Mutations delegate to
`src.features.model_library.operations`; `mock_operations` patches the
`operations` module as imported into `routes.py`.
"""
import pytest
from unittest.mock import Mock
from datetime import datetime

from src.features.model_library import routes as routes_module
from src.features.model_library.routes import ModelCollectionController
from src.features.model_library.dto import (
    CreateModelCollectionRequest,
    UpdateModelCollectionRequest,
    MoveModelCollectionRequest,
    ModelCollectionMembersRequest,
)
from src.features.model_library.records.model_collection import ModelCollection
from src.platform.security.user import User


class TestModelCollectionController:
    """Comprehensive tests for ModelCollectionController."""

    @pytest.fixture
    def mock_operations(self, monkeypatch):
        """Patch the `operations` module as seen by routes.py."""
        mock = Mock()
        monkeypatch.setattr(routes_module, "operations", mock)
        return mock

    @pytest.fixture
    def mock_repository(self):
        """Mock ModelCollectionRepository."""
        return Mock()

    @pytest.fixture
    def controller(self, mock_operations, mock_repository):
        """Create controller with a mocked repository (operations patched above)."""
        return ModelCollectionController(mock_repository)

    @pytest.fixture
    def user_a(self):
        user = Mock(spec=User)
        user.id = "user-a"
        return user

    @pytest.fixture
    def user_b(self):
        user = Mock(spec=User)
        user.id = "user-b"
        return user

    @pytest.fixture
    def sample_collection(self):
        return ModelCollection(
            id="col-123",
            name="My Checkpoints",
            user_id="user-a",
            parent_id=None,
            created_at=datetime.now(),
            item_count=0,
        )

    # ========== List ==========

    @pytest.mark.asyncio
    async def test_list_collections_success(self, controller, mock_repository, user_a, sample_collection):
        mock_repository.list.return_value = [sample_collection]

        result = await controller.list_collections(user_a)

        assert result.success is True
        assert result.data["total"] == 1
        assert result.data["collections"][0]["id"] == "col-123"
        mock_repository.list.assert_called_once_with("user-a")

    @pytest.mark.asyncio
    async def test_list_collections_empty(self, controller, mock_repository, user_a):
        mock_repository.list.return_value = []

        result = await controller.list_collections(user_a)

        assert result.success is True
        assert result.data["total"] == 0

    # ========== Create ==========

    @pytest.mark.asyncio
    async def test_create_collection_success(self, controller, mock_operations, mock_repository, user_a, sample_collection):
        mock_operations.create_collection.return_value = sample_collection

        request = CreateModelCollectionRequest(name="My Checkpoints")
        result = await controller.create_collection(request, user_a)

        assert result.success is True
        assert result.data["collection"]["id"] == "col-123"
        mock_operations.create_collection.assert_called_once_with(mock_repository, "My Checkpoints", "user-a", None)

    @pytest.mark.asyncio
    async def test_create_collection_empty_name_fails(self, controller, mock_operations, user_a):
        mock_operations.create_collection.side_effect = ValueError("Collection name is required")

        request = CreateModelCollectionRequest(name="")
        result = await controller.create_collection(request, user_a)

        assert result.success is False
        assert result.error == "create_collection_failed"

    # ========== Rename ==========

    @pytest.mark.asyncio
    async def test_rename_collection_success(self, controller, mock_operations, user_a, sample_collection):
        renamed = ModelCollection(id="col-123", name="Renamed", user_id="user-a")
        mock_operations.rename_collection.return_value = renamed

        request = UpdateModelCollectionRequest(name="Renamed")
        result = await controller.rename_collection("col-123", request, user_a)

        assert result.success is True
        assert result.data["collection"]["name"] == "Renamed"

    @pytest.mark.asyncio
    async def test_rename_collection_wrong_owner_denied(self, controller, mock_operations, user_b):
        mock_operations.rename_collection.side_effect = ValueError("Collection not found or access denied")

        request = UpdateModelCollectionRequest(name="Hacked")
        result = await controller.rename_collection("col-123", request, user_b)

        assert result.success is False
        assert "access denied" in result.message.lower()

    # ========== Move ==========

    @pytest.mark.asyncio
    async def test_move_collection_success(self, controller, mock_operations, mock_repository, user_a, sample_collection):
        mock_operations.move_collection.return_value = sample_collection

        request = MoveModelCollectionRequest(parent_id="parent-1")
        result = await controller.move_collection("col-123", request, user_a)

        assert result.success is True
        mock_operations.move_collection.assert_called_once_with(mock_repository, "col-123", "parent-1", "user-a")

    @pytest.mark.asyncio
    async def test_move_collection_cycle_rejected(self, controller, mock_operations, user_a):
        mock_operations.move_collection.side_effect = ValueError(
            "Cannot move a collection into itself or one of its subfolders"
        )

        request = MoveModelCollectionRequest(parent_id="col-123")
        result = await controller.move_collection("col-123", request, user_a)

        assert result.success is False
        assert result.error == "move_collection_failed"

    # ========== Delete ==========

    @pytest.mark.asyncio
    async def test_delete_collection_success(self, controller, mock_operations, mock_repository, user_a):
        mock_operations.delete_collection.return_value = True

        result = await controller.delete_collection("col-123", user_a)

        assert result.success is True
        mock_operations.delete_collection.assert_called_once_with(mock_repository, "col-123", "user-a")

    @pytest.mark.asyncio
    async def test_delete_collection_wrong_owner_denied(self, controller, mock_operations, user_b):
        mock_operations.delete_collection.side_effect = ValueError("Collection not found or access denied")

        result = await controller.delete_collection("col-123", user_b)

        assert result.success is False
        assert result.error == "delete_collection_failed"

    # ========== Members ==========

    @pytest.mark.asyncio
    async def test_add_members_success(self, controller, mock_operations, mock_repository, user_a):
        mock_operations.add_members.return_value = 2

        request = ModelCollectionMembersRequest(model_ids=["m1", "m2"])
        result = await controller.add_members("col-123", request, user_a)

        assert result.success is True
        assert result.data["added"] == 2
        mock_operations.add_members.assert_called_once_with(mock_repository, "col-123", ["m1", "m2"], "user-a")

    @pytest.mark.asyncio
    async def test_add_members_wrong_owner_denied(self, controller, mock_operations, user_b):
        mock_operations.add_members.side_effect = ValueError("Collection not found or access denied")

        request = ModelCollectionMembersRequest(model_ids=["m1"])
        result = await controller.add_members("col-123", request, user_b)

        assert result.success is False
        assert result.error == "add_members_failed"

    @pytest.mark.asyncio
    async def test_remove_members_success(self, controller, mock_operations, user_a):
        mock_operations.remove_members.return_value = 1

        request = ModelCollectionMembersRequest(model_ids=["m1"])
        result = await controller.remove_members("col-123", request, user_a)

        assert result.success is True
        assert result.data["removed"] == 1
