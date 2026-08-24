"""Tests for TagController."""
import pytest
from unittest.mock import Mock, AsyncMock
from datetime import datetime

from src.features.tags.routes import TagController
from src.features.tags.dto import Tag, TagWithCount, TagType, CreateTagRequest, UpdateTagRequest
from src.platform.security.user import User


class TestTagController:
    """Comprehensive tests for TagController."""

    @pytest.fixture
    def mock_manager(self):
        """Mock tag manager."""
        return Mock()

    @pytest.fixture
    def controller(self, mock_manager):
        """Create controller with mocked manager."""
        return TagController(mock_manager)

    @pytest.fixture
    def sample_user(self):
        """Sample user object."""
        user = Mock(spec=User)
        user.id = "user-123"
        user.username = "testuser"
        return user

    @pytest.fixture
    def sample_model_tag(self):
        """Sample MODEL tag."""
        return Tag(
            id="tag-123",
            name="Portrait",
            type=TagType.MODEL,
            user_id=None,
            created_at=datetime.now()
        )

    @pytest.fixture
    def sample_generation_tag(self):
        """Sample GENERATION tag."""
        return Tag(
            id="tag-456",
            name="Favorites",
            type=TagType.GENERATION,
            user_id="user-123",
            created_at=datetime.now()
        )

    @pytest.fixture
    def sample_tag_with_count(self):
        """Sample tag with count."""
        return TagWithCount(
            id="tag-123",
            name="Portrait",
            type=TagType.MODEL,
            user_id=None,
            created_at=datetime.now(),
            usage_count=5
        )

    # ========== List Tags Tests ==========

    @pytest.mark.asyncio
    async def test_list_tags_success(self, controller, mock_manager, sample_user, sample_tag_with_count):
        """Test successful tag listing."""
        mock_manager.get_tags.return_value = [sample_tag_with_count]

        result = await controller.list_tags(TagType.MODEL, sample_user)

        assert result.success is True
        assert "tags" in result.data
        assert len(result.data["tags"]) == 1
        assert result.data["total"] == 1
        mock_manager.get_tags.assert_called_once_with(TagType.MODEL, "user-123")

    @pytest.mark.asyncio
    async def test_list_tags_empty(self, controller, mock_manager, sample_user):
        """Test listing when no tags exist."""
        mock_manager.get_tags.return_value = []

        result = await controller.list_tags(TagType.MODEL, sample_user)

        assert result.success is True
        assert len(result.data["tags"]) == 0
        assert result.data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_tags_error(self, controller, mock_manager, sample_user):
        """Test error handling when listing fails."""
        mock_manager.get_tags.side_effect = ValueError("Database error")

        result = await controller.list_tags(TagType.MODEL, sample_user)

        assert result.success is False
        assert result.error == "list_tags_failed"
        assert "Database error" in result.message

    # ========== Search Tags Tests ==========

    @pytest.mark.asyncio
    async def test_search_tags_success(self, controller, mock_manager, sample_user, sample_model_tag):
        """Test successful tag search."""
        mock_manager.search_tags.return_value = [sample_model_tag]

        result = await controller.search_tags("port", TagType.MODEL, 10, sample_user)

        assert result.success is True
        assert "tags" in result.data
        assert len(result.data["tags"]) == 1
        mock_manager.search_tags.assert_called_once_with("port", TagType.MODEL, "user-123", 10)

    @pytest.mark.asyncio
    async def test_search_tags_no_results(self, controller, mock_manager, sample_user):
        """Test search with no results."""
        mock_manager.search_tags.return_value = []

        result = await controller.search_tags("nonexistent", TagType.MODEL, 10, sample_user)

        assert result.success is True
        assert len(result.data["tags"]) == 0

    @pytest.mark.asyncio
    async def test_search_tags_error(self, controller, mock_manager, sample_user):
        """Test error handling when search fails."""
        mock_manager.search_tags.side_effect = Exception("Search error")

        result = await controller.search_tags("test", TagType.MODEL, 10, sample_user)

        assert result.success is False
        assert result.error == "search_tags_failed"

    # ========== Create Tag Tests ==========

    @pytest.mark.asyncio
    async def test_create_tag_success(self, controller, mock_manager, sample_user, sample_model_tag):
        """Test successful tag creation."""
        mock_manager.create_tag.return_value = sample_model_tag

        request = CreateTagRequest(name="Portrait", type=TagType.MODEL)
        result = await controller.create_tag(request, sample_user)

        assert result.success is True
        assert "tag" in result.data
        assert result.data["tag"]["name"] == "Portrait"
        assert "created successfully" in result.data["message"]
        mock_manager.create_tag.assert_called_once_with(request, "user-123", is_admin=False)

    @pytest.mark.asyncio
    async def test_create_tag_invalid_type(self, controller, mock_manager, sample_user):
        """Test tag creation with invalid type."""
        mock_manager.create_tag.side_effect = ValueError("Invalid tag type")

        request = CreateTagRequest(name="Test", type=TagType.MODEL)
        result = await controller.create_tag(request, sample_user)

        assert result.success is False
        assert result.error == "create_tag_failed"

    @pytest.mark.asyncio
    async def test_create_tag_blocked_by_hook(self, controller, mock_manager, sample_user):
        """Test tag creation blocked by plugin hook."""
        mock_manager.create_tag.side_effect = ValueError("Not allowed")

        request = CreateTagRequest(name="Test", type=TagType.MODEL)
        result = await controller.create_tag(request, sample_user)

        assert result.success is False
        assert "Not allowed" in result.message

    # ========== Update Tag Tests ==========

    @pytest.mark.asyncio
    async def test_update_tag_success(self, controller, mock_manager, sample_user, sample_model_tag):
        """Test successful tag update."""
        updated_tag = Tag(
            id="tag-123",
            name="Updated Name",
            type=TagType.MODEL,
            user_id=None,
            created_at=sample_model_tag.created_at
        )
        mock_manager.update_tag.return_value = updated_tag

        request = UpdateTagRequest(name="Updated Name")
        result = await controller.update_tag("tag-123", request, sample_user)

        assert result.success is True
        assert result.data["tag"]["name"] == "Updated Name"
        assert "updated successfully" in result.data["message"]
        mock_manager.update_tag.assert_called_once_with("tag-123", request, "user-123", is_admin=False)

    @pytest.mark.asyncio
    async def test_update_tag_not_found(self, controller, mock_manager, sample_user):
        """Test updating non-existent tag."""
        mock_manager.update_tag.side_effect = ValueError("Tag not found or access denied")

        request = UpdateTagRequest(name="Test")
        result = await controller.update_tag("nonexistent", request, sample_user)

        assert result.success is False
        assert result.error == "update_tag_failed"

    @pytest.mark.asyncio
    async def test_update_tag_access_denied(self, controller, mock_manager, sample_user):
        """Test updating another user's tag."""
        mock_manager.update_tag.side_effect = ValueError("Tag not found or access denied")

        request = UpdateTagRequest(name="Hacked")
        result = await controller.update_tag("tag-456", request, sample_user)

        assert result.success is False
        assert "access denied" in result.message.lower()

    # ========== Delete Tag Tests ==========

    @pytest.mark.asyncio
    async def test_delete_tag_success(self, controller, mock_manager, sample_user, sample_model_tag):
        """Test successful tag deletion."""
        mock_manager.get_tag_by_id.return_value = sample_model_tag
        mock_manager.delete_tag.return_value = True

        result = await controller.delete_tag("tag-123", sample_user)

        assert result.success is True
        assert "deleted successfully" in result.data["message"]
        mock_manager.delete_tag.assert_called_once_with("tag-123", "user-123", is_admin=False)

    @pytest.mark.asyncio
    async def test_delete_tag_not_found(self, controller, mock_manager, sample_user):
        """Test deleting non-existent tag."""
        mock_manager.get_tag_by_id.side_effect = ValueError("Tag not found")

        result = await controller.delete_tag("nonexistent", sample_user)

        assert result.success is False
        assert result.error == "delete_tag_failed"

    @pytest.mark.asyncio
    async def test_delete_tag_access_denied(self, controller, mock_manager, sample_user):
        """Test deleting another user's tag."""
        mock_manager.get_tag_by_id.side_effect = ValueError("Tag not found or access denied")

        result = await controller.delete_tag("tag-456", sample_user)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_delete_tag_blocked_by_hook(self, controller, mock_manager, sample_user, sample_model_tag):
        """Test tag deletion blocked by plugin hook."""
        mock_manager.get_tag_by_id.return_value = sample_model_tag
        mock_manager.delete_tag.side_effect = ValueError("Cannot delete system tag")

        result = await controller.delete_tag("tag-123", sample_user)

        assert result.success is False
        assert "Cannot delete system tag" in result.message

    # ========== Router Factory Tests ==========

    def test_build_router_wires_controller(self, controller):
        """Test that build_router wires the container's tag_controller."""
        from types import SimpleNamespace
        from src.features.tags.routes import build_router

        router = build_router(SimpleNamespace(tag_controller=controller))
        assert router.prefix == "/api/tags"
