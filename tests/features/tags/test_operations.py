"""Tests for src.features.tags.operations (create/update/delete)."""
import pytest
from datetime import datetime
from unittest.mock import Mock

from src.features.tags import operations
from src.features.tags.errors import TagInUseByPresetError
from src.features.tags.dto import Tag, TagType, CreateTagRequest, UpdateTagRequest
from src.features.tags.repository import TagRepository
from src.features.presets.repository import DatabasePresetRepository
from src.features.presets.file_repository import FilePresetRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import HookContext


@pytest.fixture
def mock_repository():
    """Create a mock TagRepository."""
    return Mock(spec=TagRepository)


@pytest.fixture
def mock_plugin_registry():
    """Create a mock PluginRegistry."""
    registry = Mock(spec=PluginRegistry)
    # Default: no hooks block anything
    context = HookContext(hook_name="test", plugin_id="test", data={})
    registry.execute_hook.return_value = (context, [])
    return registry


@pytest.fixture
def mock_database_preset_repository():
    """Create a mock DatabasePresetRepository - no preset references the tag by default."""
    repo = Mock(spec=DatabasePresetRepository)
    repo.find_presets_referencing_tag.return_value = []
    return repo


@pytest.fixture
def mock_file_preset_repository():
    """Create a mock FilePresetRepository."""
    return Mock(spec=FilePresetRepository)


@pytest.fixture
def sample_model_tag():
    """Create a sample MODEL tag."""
    return Tag(
        id="tag-123",
        name="Portrait",
        type=TagType.MODEL,
        user_id=None,  # MODEL tags are global
        created_at=datetime.utcnow()
    )


@pytest.fixture
def sample_generation_tag():
    """Create a sample GENERATION tag."""
    return Tag(
        id="tag-456",
        name="Favorites",
        type=TagType.GENERATION,
        user_id="user-123",
        created_at=datetime.utcnow()
    )


class TestCreateTag:
    def test_create_model_tag(self, mock_repository, mock_plugin_registry, sample_model_tag):
        """Test creating a MODEL tag."""
        mock_repository.create_tag.return_value = sample_model_tag

        request = CreateTagRequest(name="Portrait", type=TagType.MODEL)
        result = operations.create_tag(mock_repository, mock_plugin_registry, request, "user-123", is_admin=True)

        mock_repository.create_tag.assert_called_once_with(
            name="Portrait", type="MODEL", user_id=None
        )
        assert result.name == "Portrait"
        assert result.type == TagType.MODEL
        assert result.user_id is None

    def test_create_generation_tag(self, mock_repository, mock_plugin_registry, sample_generation_tag):
        """Test creating a GENERATION tag."""
        mock_repository.create_tag.return_value = sample_generation_tag

        request = CreateTagRequest(name="Favorites", type=TagType.GENERATION)
        result = operations.create_tag(mock_repository, mock_plugin_registry, request, "user-123", is_admin=True)

        mock_repository.create_tag.assert_called_once_with(
            name="Favorites", type="GENERATION", user_id="user-123"
        )
        assert result.user_id == "user-123"

    def test_create_tag_blocked_by_hook(self, mock_repository, mock_plugin_registry):
        """Test that hook can block tag creation."""
        context = HookContext(
            hook_name="tag.before_create",
            plugin_id="test",
            data={"blocked": True, "block_reason": "Not allowed"}
        )
        mock_plugin_registry.execute_hook.return_value = (context, [])

        request = CreateTagRequest(name="Test", type=TagType.MODEL)

        with pytest.raises(ValueError, match="Not allowed"):
            operations.create_tag(mock_repository, mock_plugin_registry, request, "user-123", is_admin=True)

        mock_repository.create_tag.assert_not_called()

    def test_create_tag_hook_modifies_name(self, mock_repository, mock_plugin_registry):
        """Test that hook can modify tag name."""
        context = HookContext(
            hook_name="tag.before_create",
            plugin_id="test",
            data={"name": "Modified Name"}
        )
        mock_plugin_registry.execute_hook.return_value = (context, [])
        mock_repository.create_tag.return_value = Tag(
            id="tag-new",
            name="Modified Name",
            type=TagType.MODEL,
            user_id=None,
            created_at=datetime.utcnow()
        )

        request = CreateTagRequest(name="Original", type=TagType.MODEL)
        operations.create_tag(mock_repository, mock_plugin_registry, request, "user-123", is_admin=True)

        mock_repository.create_tag.assert_called_once_with(
            name="Modified Name", type="MODEL", user_id=None
        )

    def test_create_tag_failure(self, mock_repository, mock_plugin_registry):
        """Test that failed creation raises error."""
        mock_repository.create_tag.return_value = None

        request = CreateTagRequest(name="Test", type=TagType.MODEL)

        with pytest.raises(ValueError, match="Failed to create tag"):
            operations.create_tag(mock_repository, mock_plugin_registry, request, "user-123", is_admin=True)

    def test_after_create_hook_called(self, mock_repository, mock_plugin_registry, sample_model_tag):
        """Test that after_create hook is called on successful creation."""
        mock_repository.create_tag.return_value = sample_model_tag

        request = CreateTagRequest(name="Portrait", type=TagType.MODEL)
        operations.create_tag(mock_repository, mock_plugin_registry, request, "user-123", is_admin=True)

        # Check that execute_hook was called twice (before and after)
        assert mock_plugin_registry.execute_hook.call_count == 2
        call_args = mock_plugin_registry.execute_hook.call_args_list[1]
        assert "tag.after_create" in str(call_args)


class TestUpdateTag:
    def test_update_model_tag(self, mock_repository, mock_plugin_registry, sample_model_tag):
        """Test updating a MODEL tag."""
        updated_tag = Tag(
            id="tag-123",
            name="Updated Name",
            type=TagType.MODEL,
            user_id=None,
            created_at=sample_model_tag.created_at
        )
        mock_repository.get_tag_by_id.side_effect = [sample_model_tag, updated_tag]
        mock_repository.update_tag.return_value = True

        request = UpdateTagRequest(name="Updated Name")
        result = operations.update_tag(mock_repository, mock_plugin_registry, "tag-123", request, "user-123", is_admin=True)

        mock_repository.update_tag.assert_called_once_with("tag-123", "Updated Name")
        assert result.name == "Updated Name"

    def test_update_generation_tag_own_tag(self, mock_repository, mock_plugin_registry, sample_generation_tag):
        """Test updating own GENERATION tag succeeds."""
        updated_tag = Tag(
            id="tag-456",
            name="Updated Favorites",
            type=TagType.GENERATION,
            user_id="user-123",
            created_at=sample_generation_tag.created_at
        )
        mock_repository.get_tag_by_id.side_effect = [sample_generation_tag, updated_tag]
        mock_repository.update_tag.return_value = True

        request = UpdateTagRequest(name="Updated Favorites")
        result = operations.update_tag(mock_repository, mock_plugin_registry, "tag-456", request, "user-123")

        assert result.name == "Updated Favorites"

    def test_update_generation_tag_other_user_fails(self, mock_repository, mock_plugin_registry, sample_generation_tag):
        """Test updating another user's GENERATION tag fails."""
        mock_repository.get_tag_by_id.return_value = sample_generation_tag

        request = UpdateTagRequest(name="Hacked")

        with pytest.raises(ValueError, match="Tag not found or access denied"):
            operations.update_tag(mock_repository, mock_plugin_registry, "tag-456", request, "other-user")

        mock_repository.update_tag.assert_not_called()

    def test_update_tag_not_found(self, mock_repository, mock_plugin_registry):
        """Test updating non-existent tag raises error."""
        mock_repository.get_tag_by_id.return_value = None

        request = UpdateTagRequest(name="Test")

        with pytest.raises(ValueError, match="Tag not found or access denied"):
            operations.update_tag(mock_repository, mock_plugin_registry, "nonexistent", request, "user-123")

    def test_update_tag_blocked_by_hook(self, mock_repository, mock_plugin_registry, sample_model_tag):
        """Test that hook can block tag update."""
        mock_repository.get_tag_by_id.return_value = sample_model_tag

        context = HookContext(
            hook_name="tag.before_update",
            plugin_id="test",
            data={"blocked": True, "block_reason": "Update not allowed"}
        )
        mock_plugin_registry.execute_hook.return_value = (context, [])

        request = UpdateTagRequest(name="New Name")

        with pytest.raises(ValueError, match="Update not allowed"):
            operations.update_tag(mock_repository, mock_plugin_registry, "tag-123", request, "user-123", is_admin=True)

        mock_repository.update_tag.assert_not_called()

    def test_after_update_hook_called(self, mock_repository, mock_plugin_registry, sample_model_tag):
        """Test that after_update hook is called on successful update."""
        updated_tag = Tag(
            id="tag-123",
            name="Updated",
            type=TagType.MODEL,
            user_id=None,
            created_at=sample_model_tag.created_at
        )
        mock_repository.get_tag_by_id.side_effect = [sample_model_tag, updated_tag]
        mock_repository.update_tag.return_value = True

        request = UpdateTagRequest(name="Updated")
        operations.update_tag(mock_repository, mock_plugin_registry, "tag-123", request, "user-123", is_admin=True)

        assert mock_plugin_registry.execute_hook.call_count == 2
        call_args = mock_plugin_registry.execute_hook.call_args_list[1]
        assert "tag.after_update" in str(call_args)


class TestDeleteTag:
    def test_delete_model_tag(self, mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository, sample_model_tag):
        """Test deleting a MODEL tag."""
        mock_repository.get_tag_by_id.return_value = sample_model_tag
        mock_repository.delete_tag.return_value = True

        result = operations.delete_tag(
            mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository,
            "tag-123", "user-123", is_admin=True
        )

        mock_repository.delete_tag.assert_called_once_with("tag-123")
        assert result is True

    def test_delete_generation_tag_own_tag(self, mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository, sample_generation_tag):
        """Test deleting own GENERATION tag succeeds."""
        mock_repository.get_tag_by_id.return_value = sample_generation_tag
        mock_repository.delete_tag.return_value = True

        result = operations.delete_tag(
            mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository,
            "tag-456", "user-123"
        )

        assert result is True

    def test_delete_generation_tag_other_user_fails(self, mock_repository, mock_database_preset_repository, mock_file_preset_repository, sample_generation_tag):
        """Test deleting another user's GENERATION tag fails."""
        mock_repository.get_tag_by_id.return_value = sample_generation_tag

        with pytest.raises(ValueError, match="Tag not found or access denied"):
            operations.delete_tag(
                mock_repository, Mock(), mock_database_preset_repository, mock_file_preset_repository,
                "tag-456", "other-user"
            )

        mock_repository.delete_tag.assert_not_called()

    def test_delete_tag_not_found(self, mock_repository, mock_database_preset_repository, mock_file_preset_repository):
        """Test deleting non-existent tag raises error."""
        mock_repository.get_tag_by_id.return_value = None

        with pytest.raises(ValueError, match="Tag not found or access denied"):
            operations.delete_tag(
                mock_repository, Mock(), mock_database_preset_repository, mock_file_preset_repository,
                "nonexistent", "user-123"
            )

    def test_delete_tag_blocked_by_hook(self, mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository, sample_model_tag):
        """Test that hook can block tag deletion."""
        mock_repository.get_tag_by_id.return_value = sample_model_tag

        context = HookContext(
            hook_name="tag.before_delete",
            plugin_id="test",
            data={"blocked": True, "block_reason": "Cannot delete system tag"}
        )
        mock_plugin_registry.execute_hook.return_value = (context, [])

        with pytest.raises(ValueError, match="Cannot delete system tag"):
            operations.delete_tag(
                mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository,
                "tag-123", "user-123", is_admin=True
            )

        mock_repository.delete_tag.assert_not_called()

    def test_delete_tag_failure(self, mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository, sample_model_tag):
        """Test that failed deletion raises error."""
        mock_repository.get_tag_by_id.return_value = sample_model_tag
        mock_repository.delete_tag.return_value = False

        with pytest.raises(ValueError, match="Failed to delete tag"):
            operations.delete_tag(
                mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository,
                "tag-123", "user-123", is_admin=True
            )

    def test_delete_tag_blocked_when_referenced_by_preset_configuration(
        self, mock_repository, mock_plugin_registry, mock_database_preset_repository,
        mock_file_preset_repository, sample_model_tag
    ):
        """A tag referenced by an installed preset's stored `configuration:` values
        can't be deleted - see docs/presets.md "Configuration (admin-set)"."""
        mock_repository.get_tag_by_id.return_value = sample_model_tag
        mock_database_preset_repository.find_presets_referencing_tag.return_value = [
            {"preset_id": "sdxl_realistic", "key": "checkpoint_tags"},
        ]
        preset_template = Mock()
        preset_template.name = "SDXL Realistic"
        mock_file_preset_repository.find_preset_by_id.return_value = preset_template

        with pytest.raises(TagInUseByPresetError) as exc_info:
            operations.delete_tag(
                mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository,
                "tag-123", "user-123", is_admin=True
            )

        assert exc_info.value.used_by == [{
            "preset_id": "sdxl_realistic",
            "preset_name": "SDXL Realistic",
            "key": "checkpoint_tags",
        }]
        mock_repository.delete_tag.assert_not_called()

    def test_after_delete_hook_called(self, mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository, sample_model_tag):
        """Test that after_delete hook is called on successful deletion."""
        mock_repository.get_tag_by_id.return_value = sample_model_tag
        mock_repository.delete_tag.return_value = True

        operations.delete_tag(
            mock_repository, mock_plugin_registry, mock_database_preset_repository, mock_file_preset_repository,
            "tag-123", "user-123", is_admin=True
        )

        assert mock_plugin_registry.execute_hook.call_count == 2
        call_args = mock_plugin_registry.execute_hook.call_args_list[1]
        assert "tag.after_delete" in str(call_args)
