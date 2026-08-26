"""Tests for TagController.

list_tags/search_tags are pure DB reads (repository +
`effective_user_id_for_type`), made directly by the controller. Mutations
delegate to `src.features.tags.operations`; `mock_operations` patches the
`operations` module as imported into `routes.py`.
"""
import pytest
from unittest.mock import Mock, AsyncMock
from datetime import datetime

from src.features.tags import routes as routes_module
from src.features.tags.routes import TagController
from src.features.tags.dto import Tag, TagWithCount, TagType, CreateTagRequest, UpdateTagRequest
from src.platform.security.user import User


class TestTagController:
    """Comprehensive tests for TagController."""

    @pytest.fixture
    def mock_operations(self, monkeypatch):
        """Patch the `operations` module as seen by routes.py."""
        mock = Mock()
        monkeypatch.setattr(routes_module, "operations", mock)
        return mock

    @pytest.fixture
    def mock_repository(self):
        """Mock TagRepository."""
        return Mock()

    @pytest.fixture
    def controller(self, mock_operations, mock_repository):
        """Create controller with a mocked repository (operations patched above)."""
        return TagController(
            tag_repository=mock_repository,
            plugin_registry=Mock(),
            database_preset_repository=Mock(),
            file_preset_repository=Mock(),
        )

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

    # ========== _get_owned_tag Tests (delete's pre-fetch ownership check) ==========

    def test_get_owned_tag_model_tag(self, controller, mock_repository, sample_model_tag):
        """A MODEL tag (no owner) resolves for any caller."""
        mock_repository.get_tag_by_id.return_value = sample_model_tag

        result = controller._get_owned_tag("tag-123", "user-123")

        mock_repository.get_tag_by_id.assert_called_once_with("tag-123")
        assert result.id == "tag-123"
        assert result.type == TagType.MODEL

    def test_get_owned_tag_generation_tag_own_tag(self, controller, mock_repository, sample_generation_tag):
        """A caller's own GENERATION tag resolves."""
        mock_repository.get_tag_by_id.return_value = sample_generation_tag

        result = controller._get_owned_tag("tag-456", "user-123")

        assert result.id == "tag-456"
        assert result.type == TagType.GENERATION

    def test_get_owned_tag_generation_tag_other_user_fails(self, controller, mock_repository, sample_generation_tag):
        """Another user's GENERATION tag is denied."""
        mock_repository.get_tag_by_id.return_value = sample_generation_tag

        with pytest.raises(ValueError, match="Tag not found or access denied"):
            controller._get_owned_tag("tag-456", "other-user")

    def test_get_owned_tag_not_found(self, controller, mock_repository):
        """A missing tag raises the bare 'Tag not found' (not the "or access
        denied" variant) - it can't be an ownership violation if it doesn't exist."""
        mock_repository.get_tag_by_id.return_value = None

        with pytest.raises(ValueError, match="^Tag not found$"):
            controller._get_owned_tag("nonexistent", "user-123")

    # ========== List Tags Tests ==========

    @pytest.mark.asyncio
    async def test_list_tags_success(self, controller, mock_repository, sample_user, sample_tag_with_count):
        """Test successful tag listing."""
        mock_repository.get_tags_with_counts.return_value = [sample_tag_with_count]

        result = await controller.list_tags(TagType.MODEL, sample_user)

        assert result.success is True
        assert "tags" in result.data
        assert len(result.data["tags"]) == 1
        assert result.data["total"] == 1

    @pytest.mark.asyncio
    async def test_list_tags_model_type_uses_no_user_id(self, controller, mock_repository, sample_user, sample_tag_with_count):
        """MODEL tags are global - listing them must not scope to a user_id."""
        mock_repository.get_tags_with_counts.return_value = [sample_tag_with_count]

        await controller.list_tags(TagType.MODEL, sample_user)

        mock_repository.get_tags_with_counts.assert_called_once_with(type="MODEL", user_id=None)

    @pytest.mark.asyncio
    async def test_list_tags_generation_type_uses_user_id(self, controller, mock_repository, sample_user, sample_generation_tag):
        """GENERATION tags are user-specific - listing them must scope to the caller."""
        mock_repository.get_tags_with_counts.return_value = [sample_generation_tag]

        await controller.list_tags(TagType.GENERATION, sample_user)

        mock_repository.get_tags_with_counts.assert_called_once_with(type="GENERATION", user_id="user-123")

    @pytest.mark.asyncio
    async def test_list_tags_empty(self, controller, mock_repository, sample_user):
        """Test listing when no tags exist."""
        mock_repository.get_tags_with_counts.return_value = []

        result = await controller.list_tags(TagType.MODEL, sample_user)

        assert result.success is True
        assert len(result.data["tags"]) == 0
        assert result.data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_tags_error(self, controller, mock_repository, sample_user):
        """Test error handling when listing fails."""
        mock_repository.get_tags_with_counts.side_effect = ValueError("Database error")

        result = await controller.list_tags(TagType.MODEL, sample_user)

        assert result.success is False
        assert result.error == "list_tags_failed"
        assert "Database error" in result.message

    # ========== Search Tags Tests ==========

    @pytest.mark.asyncio
    async def test_search_tags_success(self, controller, mock_repository, sample_user, sample_model_tag):
        """Test successful tag search."""
        mock_repository.search_tags.return_value = [sample_model_tag]

        result = await controller.search_tags("port", TagType.MODEL, 10, sample_user)

        assert result.success is True
        assert "tags" in result.data
        assert len(result.data["tags"]) == 1
        mock_repository.search_tags.assert_called_once_with(query="port", type="MODEL", user_id=None, limit=10)

    @pytest.mark.asyncio
    async def test_search_tags_generation_type_uses_user_id(self, controller, mock_repository, sample_user, sample_generation_tag):
        """Test searching GENERATION tags uses user_id."""
        mock_repository.search_tags.return_value = [sample_generation_tag]

        await controller.search_tags("fav", TagType.GENERATION, 5, sample_user)

        mock_repository.search_tags.assert_called_once_with(query="fav", type="GENERATION", user_id="user-123", limit=5)

    @pytest.mark.asyncio
    async def test_search_tags_no_results(self, controller, mock_repository, sample_user):
        """Test search with no results."""
        mock_repository.search_tags.return_value = []

        result = await controller.search_tags("nonexistent", TagType.MODEL, 10, sample_user)

        assert result.success is True
        assert len(result.data["tags"]) == 0

    @pytest.mark.asyncio
    async def test_search_tags_error(self, controller, mock_repository, sample_user):
        """Test error handling when search fails."""
        mock_repository.search_tags.side_effect = Exception("Search error")

        result = await controller.search_tags("test", TagType.MODEL, 10, sample_user)

        assert result.success is False
        assert result.error == "search_tags_failed"

    # ========== Create Tag Tests ==========

    @pytest.mark.asyncio
    async def test_create_tag_success(self, controller, mock_operations, mock_repository, sample_user, sample_model_tag):
        """Test successful tag creation."""
        mock_operations.create_tag.return_value = sample_model_tag

        request = CreateTagRequest(name="Portrait", type=TagType.MODEL)
        result = await controller.create_tag(request, sample_user)

        assert result.success is True
        assert "tag" in result.data
        assert result.data["tag"]["name"] == "Portrait"
        assert "created successfully" in result.data["message"]
        mock_operations.create_tag.assert_called_once_with(mock_repository, controller.plugins, request, "user-123", is_admin=False)

    @pytest.mark.asyncio
    async def test_create_tag_invalid_type(self, controller, mock_operations, sample_user):
        """Test tag creation with invalid type."""
        mock_operations.create_tag.side_effect = ValueError("Invalid tag type")

        request = CreateTagRequest(name="Test", type=TagType.MODEL)
        result = await controller.create_tag(request, sample_user)

        assert result.success is False
        assert result.error == "create_tag_failed"

    @pytest.mark.asyncio
    async def test_create_tag_blocked_by_hook(self, controller, mock_operations, sample_user):
        """Test tag creation blocked by plugin hook."""
        mock_operations.create_tag.side_effect = ValueError("Not allowed")

        request = CreateTagRequest(name="Test", type=TagType.MODEL)
        result = await controller.create_tag(request, sample_user)

        assert result.success is False
        assert "Not allowed" in result.message

    # ========== Update Tag Tests ==========

    @pytest.mark.asyncio
    async def test_update_tag_success(self, controller, mock_operations, mock_repository, sample_user, sample_model_tag):
        """Test successful tag update."""
        updated_tag = Tag(
            id="tag-123",
            name="Updated Name",
            type=TagType.MODEL,
            user_id=None,
            created_at=sample_model_tag.created_at
        )
        mock_operations.update_tag.return_value = updated_tag

        request = UpdateTagRequest(name="Updated Name")
        result = await controller.update_tag("tag-123", request, sample_user)

        assert result.success is True
        assert result.data["tag"]["name"] == "Updated Name"
        assert "updated successfully" in result.data["message"]
        mock_operations.update_tag.assert_called_once_with(mock_repository, controller.plugins, "tag-123", request, "user-123", is_admin=False)

    @pytest.mark.asyncio
    async def test_update_tag_not_found(self, controller, mock_operations, sample_user):
        """Test updating non-existent tag."""
        mock_operations.update_tag.side_effect = ValueError("Tag not found or access denied")

        request = UpdateTagRequest(name="Test")
        result = await controller.update_tag("nonexistent", request, sample_user)

        assert result.success is False
        assert result.error == "update_tag_failed"

    @pytest.mark.asyncio
    async def test_update_tag_access_denied(self, controller, mock_operations, sample_user):
        """Test updating another user's tag."""
        mock_operations.update_tag.side_effect = ValueError("Tag not found or access denied")

        request = UpdateTagRequest(name="Hacked")
        result = await controller.update_tag("tag-456", request, sample_user)

        assert result.success is False
        assert "access denied" in result.message.lower()

    # ========== Delete Tag Tests ==========

    @pytest.mark.asyncio
    async def test_delete_tag_success(self, controller, mock_operations, mock_repository, sample_user, sample_model_tag):
        """Test successful tag deletion."""
        mock_repository.get_tag_by_id.return_value = sample_model_tag
        mock_operations.delete_tag.return_value = True

        result = await controller.delete_tag("tag-123", sample_user)

        assert result.success is True
        assert "deleted successfully" in result.data["message"]
        mock_operations.delete_tag.assert_called_once_with(
            mock_repository, controller.plugins, controller.preset_repository, controller.file_preset_repository,
            "tag-123", "user-123", is_admin=False,
        )

    @pytest.mark.asyncio
    async def test_delete_tag_not_found(self, controller, mock_repository, sample_user):
        """Test deleting non-existent tag - the pre-delete name lookup raises."""
        mock_repository.get_tag_by_id.return_value = None

        result = await controller.delete_tag("nonexistent", sample_user)

        assert result.success is False
        assert result.error == "delete_tag_failed"

    @pytest.mark.asyncio
    async def test_delete_tag_access_denied(self, controller, mock_repository, sample_user, sample_generation_tag):
        """Test deleting another user's tag - the pre-delete ownership check raises."""
        mock_repository.get_tag_by_id.return_value = sample_generation_tag

        result = await controller.delete_tag("tag-456", Mock(id="other-user"))

        assert result.success is False

    @pytest.mark.asyncio
    async def test_delete_tag_blocked_by_hook(self, controller, mock_operations, mock_repository, sample_user, sample_model_tag):
        """Test tag deletion blocked by plugin hook."""
        mock_repository.get_tag_by_id.return_value = sample_model_tag
        mock_operations.delete_tag.side_effect = ValueError("Cannot delete system tag")

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
