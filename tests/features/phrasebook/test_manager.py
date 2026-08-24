"""Tests for the PhrasebookManager class."""
import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock

from src.features.phrasebook.manager import PhrasebookManager
from src.features.phrasebook.dto import (
    PhrasebookCategory,
    PhrasebookValue,
    PhrasebookCategoryRequest,
    PhrasebookValueRequest,
    PhrasebookStateFilter,
)
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import HookContext


class TestPhrasebookManager:
    """Tests for PhrasebookManager."""

    @pytest.fixture
    def mock_category_repository(self):
        """Create a mock PhrasebookCategoryRepository."""
        return Mock(spec=PhrasebookCategoryRepository)

    @pytest.fixture
    def mock_value_repository(self):
        """Create a mock PhrasebookValueRepository."""
        return Mock(spec=PhrasebookValueRepository)

    @pytest.fixture
    def mock_plugin_registry(self):
        """Create a mock PluginRegistry."""
        registry = Mock(spec=PluginRegistry)
        # Default: no hooks block anything
        context = HookContext(hook_name="test", plugin_id="test", data={})
        registry.execute_hook.return_value = (context, [])
        return registry

    @pytest.fixture
    def manager(
        self,
        mock_category_repository,
        mock_value_repository,
        mock_plugin_registry
    ):
        """Create an PhrasebookManager with mocks."""
        return PhrasebookManager(
            category_repository=mock_category_repository,
            value_repository=mock_value_repository,
            plugin_registry=mock_plugin_registry
        )

    @pytest.fixture
    def sample_category(self):
        """Create a sample category."""
        return PhrasebookCategory(
            id="cat-123",
            name="Test Category",
            path="test.category",
            parent_id=None,
            description="Test description",
            is_active=True,
            user_id="user-123",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_value(self):
        """Create a sample value."""
        return PhrasebookValue(
            id="val-123",
            category_id="cat-123",
            label="Test Value",
            value="test value content",
            sort_order=0,
            is_active=True,
            preview_file_id=None,
            preview_generation_id=None,
            user_id="user-123",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    # ========== Category Tests ==========

    def test_toggle_category_active_deactivate(self, manager, mock_category_repository, sample_category):
        """Test deactivating a category."""
        mock_category_repository.get_by_id.return_value = sample_category
        mock_category_repository.update_active_state.return_value = True

        # Create deactivated version
        deactivated = PhrasebookCategory(
            id=sample_category.id,
            name=sample_category.name,
            path=sample_category.path,
            parent_id=sample_category.parent_id,
            description=sample_category.description,
            is_active=False,
            user_id=sample_category.user_id,
            created_at=sample_category.created_at,
            updated_at=datetime.utcnow()
        )
        mock_category_repository.get_by_id.side_effect = [sample_category, deactivated]

        result = manager.toggle_category_active("cat-123", False, "user-123")

        mock_category_repository.update_active_state.assert_called_once_with(
            "cat-123", "user-123", False
        )
        assert result.is_active is False

    def test_toggle_category_active_activate(self, manager, mock_category_repository, sample_category):
        """Test activating a category."""
        # Start with inactive category
        inactive_category = PhrasebookCategory(
            id=sample_category.id,
            name=sample_category.name,
            path=sample_category.path,
            parent_id=sample_category.parent_id,
            description=sample_category.description,
            is_active=False,
            user_id=sample_category.user_id,
            created_at=sample_category.created_at,
            updated_at=datetime.utcnow()
        )
        mock_category_repository.get_by_id.return_value = inactive_category
        mock_category_repository.update_active_state.return_value = True

        # Return activated version after update
        mock_category_repository.get_by_id.side_effect = [inactive_category, sample_category]

        result = manager.toggle_category_active("cat-123", True, "user-123")

        mock_category_repository.update_active_state.assert_called_once_with(
            "cat-123", "user-123", True
        )
        assert result.is_active is True

    def test_toggle_category_active_not_found(self, manager, mock_category_repository):
        """Test toggling active state for non-existent category."""
        mock_category_repository.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            manager.toggle_category_active("nonexistent", True, "user-123")

        assert "Category not found" in str(exc_info.value)

    def test_toggle_category_active_update_fails(self, manager, mock_category_repository, sample_category):
        """Test toggling active state when update fails."""
        mock_category_repository.get_by_id.return_value = sample_category
        mock_category_repository.update_active_state.return_value = False

        with pytest.raises(ValueError) as exc_info:
            manager.toggle_category_active("cat-123", False, "user-123")

        assert "Failed to update category active state" in str(exc_info.value)

    # ========== Value Tests ==========

    def test_toggle_value_active_deactivate(self, manager, mock_value_repository, sample_value):
        """Test deactivating a value."""
        mock_value_repository.get_by_id.return_value = sample_value
        mock_value_repository.update_active_state.return_value = True

        # Create deactivated version
        deactivated = PhrasebookValue(
            id=sample_value.id,
            category_id=sample_value.category_id,
            label=sample_value.label,
            value=sample_value.value,
            sort_order=sample_value.sort_order,
            is_active=False,
            preview_file_id=sample_value.preview_file_id,
            preview_generation_id=sample_value.preview_generation_id,
            user_id=sample_value.user_id,
            created_at=sample_value.created_at,
            updated_at=datetime.utcnow()
        )
        mock_value_repository.get_by_id.side_effect = [sample_value, deactivated]

        result = manager.toggle_value_active("val-123", False, "user-123")

        mock_value_repository.update_active_state.assert_called_once_with(
            "val-123", "user-123", False
        )
        assert result.is_active is False

    def test_toggle_value_active_activate(self, manager, mock_value_repository, sample_value):
        """Test activating a value."""
        # Start with inactive value
        inactive_value = PhrasebookValue(
            id=sample_value.id,
            category_id=sample_value.category_id,
            label=sample_value.label,
            value=sample_value.value,
            sort_order=sample_value.sort_order,
            is_active=False,
            preview_file_id=sample_value.preview_file_id,
            preview_generation_id=sample_value.preview_generation_id,
            user_id=sample_value.user_id,
            created_at=sample_value.created_at,
            updated_at=datetime.utcnow()
        )
        mock_value_repository.get_by_id.return_value = inactive_value
        mock_value_repository.update_active_state.return_value = True

        # Return activated version after update
        mock_value_repository.get_by_id.side_effect = [inactive_value, sample_value]

        result = manager.toggle_value_active("val-123", True, "user-123")

        mock_value_repository.update_active_state.assert_called_once_with(
            "val-123", "user-123", True
        )
        assert result.is_active is True

    def test_toggle_value_active_not_found(self, manager, mock_value_repository):
        """Test toggling active state for non-existent value."""
        mock_value_repository.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            manager.toggle_value_active("nonexistent", True, "user-123")

        assert "Value not found" in str(exc_info.value)

    def test_toggle_value_active_update_fails(self, manager, mock_value_repository, sample_value):
        """Test toggling active state when update fails."""
        mock_value_repository.get_by_id.return_value = sample_value
        mock_value_repository.update_active_state.return_value = False

        with pytest.raises(ValueError) as exc_info:
            manager.toggle_value_active("val-123", False, "user-123")

        assert "Failed to update value active state" in str(exc_info.value)

    def test_attach_preview_image(self, manager, mock_value_repository, sample_value):
        """Test attaching a preview image to a value."""
        mock_value_repository.get_by_id.return_value = sample_value
        mock_value_repository.update_preview_file.return_value = True

        # Create updated version with preview
        updated = PhrasebookValue(
            id=sample_value.id,
            category_id=sample_value.category_id,
            label=sample_value.label,
            value=sample_value.value,
            sort_order=sample_value.sort_order,
            is_active=sample_value.is_active,
            preview_file_id="file-123",
            preview_generation_id="gen-123",
            user_id=sample_value.user_id,
            created_at=sample_value.created_at,
            updated_at=datetime.utcnow()
        )
        mock_value_repository.get_by_id.side_effect = [sample_value, updated]

        result = manager.attach_preview_image(
            "val-123", "user-123", "file-123", "gen-123"
        )

        mock_value_repository.update_preview_file.assert_called_once_with(
            "val-123", "user-123", "file-123", "gen-123"
        )
        assert result.preview_file_id == "file-123"
        assert result.preview_generation_id == "gen-123"

    def test_attach_preview_image_clear(self, manager, mock_value_repository, sample_value):
        """Test clearing a preview image from a value."""
        # Start with value that has preview
        with_preview = PhrasebookValue(
            id=sample_value.id,
            category_id=sample_value.category_id,
            label=sample_value.label,
            value=sample_value.value,
            sort_order=sample_value.sort_order,
            is_active=sample_value.is_active,
            preview_file_id="file-123",
            preview_generation_id="gen-123",
            user_id=sample_value.user_id,
            created_at=sample_value.created_at,
            updated_at=datetime.utcnow()
        )
        mock_value_repository.get_by_id.return_value = with_preview
        mock_value_repository.update_preview_file.return_value = True

        # Return cleared version after update
        mock_value_repository.get_by_id.side_effect = [with_preview, sample_value]

        result = manager.attach_preview_image("val-123", "user-123", None, None)

        mock_value_repository.update_preview_file.assert_called_once_with(
            "val-123", "user-123", None, None
        )
        assert result.preview_file_id is None
        assert result.preview_generation_id is None

    def test_attach_preview_image_not_found(self, manager, mock_value_repository):
        """Test attaching preview to non-existent value."""
        mock_value_repository.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            manager.attach_preview_image("nonexistent", "user-123", "file-123", "gen-123")

        assert "Value not found" in str(exc_info.value)

    def test_attach_preview_image_update_fails(self, manager, mock_value_repository, sample_value):
        """Test attaching preview when update fails."""
        mock_value_repository.get_by_id.return_value = sample_value
        mock_value_repository.update_preview_file.return_value = False

        with pytest.raises(ValueError) as exc_info:
            manager.attach_preview_image("val-123", "user-123", "file-123", "gen-123")

        assert "Failed to update value preview image" in str(exc_info.value)

    # ========== Search Tests ==========

    def test_search_phrasebook_with_state_filter(self, manager, mock_category_repository, mock_value_repository, sample_category, sample_value):
        """Test search with state filter."""
        mock_category_repository.get_by_path.return_value = sample_category
        mock_category_repository.get_children.return_value = []
        mock_value_repository.get_by_category.return_value = [sample_value]

        result = manager.search_phrasebook(
            "test.category", "user-123", limit=50, state_filter=PhrasebookStateFilter.ACTIVE
        )

        mock_value_repository.get_by_category.assert_called_once_with(
            sample_category.id, "user-123", PhrasebookStateFilter.ACTIVE
        )
        assert result["current_category"] is not None
        assert len(result["values"]) == 1

    def test_search_phrasebook_filters_inactive_exact_category(self, manager, mock_category_repository, mock_value_repository, sample_category):
        """Test that inactive exact category is filtered when using ACTIVE filter."""
        # Create inactive category
        inactive_category = PhrasebookCategory(
            id=sample_category.id,
            name=sample_category.name,
            path=sample_category.path,
            parent_id=sample_category.parent_id,
            description=sample_category.description,
            is_active=False,
            user_id=sample_category.user_id,
            created_at=sample_category.created_at,
            updated_at=sample_category.updated_at
        )
        mock_category_repository.get_by_path.return_value = inactive_category
        mock_category_repository.search_by_path_prefix.return_value = []
        mock_value_repository.search_by_path_prefix.return_value = []

        result = manager.search_phrasebook(
            "test.category", "user-123", state_filter=PhrasebookStateFilter.ACTIVE
        )

        # When the exact category is inactive with ACTIVE filter, it should be treated as no match
        assert result["current_category"] is None

    def test_search_phrasebook_filters_inactive_children(self, manager, mock_category_repository, mock_value_repository, sample_category):
        """Test that inactive child categories are filtered when using ACTIVE filter."""
        # Create active parent
        mock_category_repository.get_by_path.return_value = sample_category

        # Create mix of active and inactive children
        active_child = PhrasebookCategory(
            id="child-active",
            name="Active Child",
            path="test.category.active",
            parent_id=sample_category.id,
            description="Active",
            is_active=True,
            user_id="user-123",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        inactive_child = PhrasebookCategory(
            id="child-inactive",
            name="Inactive Child",
            path="test.category.inactive",
            parent_id=sample_category.id,
            description="Inactive",
            is_active=False,
            user_id="user-123",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        mock_category_repository.get_children.return_value = [active_child, inactive_child]
        mock_value_repository.get_by_category.return_value = []

        result = manager.search_phrasebook(
            "test.category", "user-123", state_filter=PhrasebookStateFilter.ACTIVE
        )

        # Only active child should be in results
        assert len(result["child_categories"]) == 1
        assert result["child_categories"][0]["id"] == "child-active"

    def test_search_phrasebook_default_state_filter(self, manager, mock_category_repository, mock_value_repository, sample_category, sample_value):
        """Test search uses ACTIVE as default state filter."""
        mock_category_repository.get_by_path.return_value = sample_category
        mock_category_repository.get_children.return_value = []
        mock_value_repository.get_by_category.return_value = [sample_value]

        result = manager.search_phrasebook("test.category", "user-123")

        # Default should be ACTIVE
        mock_value_repository.get_by_category.assert_called_once_with(
            sample_category.id, "user-123", PhrasebookStateFilter.ACTIVE
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
