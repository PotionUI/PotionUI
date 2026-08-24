"""
Tests for UserGroupManager.

Tests business logic for user groups including CRUD, member management,
and resource assignments with plugin hooks.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

from src.features.user_groups import UserGroupManager
from src.features.user_groups.dto import (
    GroupCreate,
    GroupUpdate,
    UserGroupDTO,
    GroupWithCountsDTO,
    UserGroupMemberDTO,
    UserGroupPresetDTO,
    UserGroupLLMDTO,
    UserGroupModelDTO,
)
from src.platform.security.user import User, AccountType
from src.features.user_groups.records import (
    UserGroup,
    UserGroupMember,
    UserGroupPreset,
    UserGroupLLM,
    UserGroupModel,
)
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import HookContext


class TestUserGroupManager:
    """Tests for UserGroupManager"""

    @pytest.fixture
    def mock_repository(self):
        """Mock user group repository"""
        return Mock()

    @pytest.fixture
    def mock_plugin_registry(self):
        """Mock plugin registry"""
        registry = Mock(spec=PluginRegistry)
        # Default: hooks don't block
        context = Mock()
        context.data = {}
        registry.execute_hook.return_value = (context, [])
        return registry

    @pytest.fixture
    def manager(self, mock_repository, mock_plugin_registry):
        """UserGroupManager instance with mocked dependencies"""
        return UserGroupManager(
            user_group_repository=mock_repository,
            plugin_registry=mock_plugin_registry
        )

    @pytest.fixture
    def admin_user(self):
        """Sample admin user"""
        return User(
            id="admin-123",
            username="admin",
            email="admin@example.com",
            password_hash="hash",
            account_type=AccountType.ADMIN,
            created_at=datetime.utcnow(),
            last_login=None
        )

    @pytest.fixture
    def regular_user(self):
        """Sample regular user"""
        return User(
            id="user-123",
            username="testuser",
            email="test@example.com",
            password_hash="hash",
            account_type=AccountType.USER,
            created_at=datetime.utcnow(),
            last_login=None
        )

    @pytest.fixture
    def sample_group(self):
        """Sample user group"""
        return UserGroup(
            id="group-123",
            name="Test Group",
            description="A test group",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_member(self):
        """Sample group member"""
        return UserGroupMember(
            id="member-123",
            group_id="group-123",
            user_id="user-456",
            assigned_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_preset_assignment(self):
        """Sample preset assignment"""
        return UserGroupPreset(
            id="gp-123",
            group_id="group-123",
            preset_id="preset-123",
            assigned_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_llm_assignment(self):
        """Sample LLM assignment"""
        return UserGroupLLM(
            id="gl-123",
            group_id="group-123",
            llm_config_id="llm-123",
            assigned_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_model_assignment(self):
        """Sample model assignment"""
        return UserGroupModel(
            id="gm-123",
            group_id="group-123",
            model_id="model-123",
            assigned_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    # ========== Admin Permission Tests ==========

    def test_require_admin_success(self, manager, admin_user):
        """Test admin permission passes for admin user"""
        # Should not raise
        manager._require_admin(admin_user)

    def test_require_admin_raises_for_regular_user(self, manager, regular_user):
        """Test admin permission raises for regular user"""
        with pytest.raises(ValueError) as exc_info:
            manager._require_admin(regular_user)
        assert "Admin access required" in str(exc_info.value)

    def test_get_all_groups_non_admin_forbidden(self, manager, regular_user):
        """Test non-admin cannot list groups"""
        with pytest.raises(ValueError) as exc_info:
            manager.get_all_groups(regular_user)
        assert "Admin access required" in str(exc_info.value)

    def test_create_group_non_admin_forbidden(self, manager, regular_user):
        """Test non-admin cannot create groups"""
        request = GroupCreate(name="Test")
        with pytest.raises(ValueError) as exc_info:
            manager.create_group(request, regular_user)
        assert "Admin access required" in str(exc_info.value)

    # ========== Group Existence Tests ==========

    def test_require_group_exists_success(self, manager, mock_repository, sample_group):
        """Test group existence check passes"""
        mock_repository.get_group_by_id.return_value = sample_group
        result = manager._require_group_exists("group-123")
        assert result.id == "group-123"

    def test_require_group_exists_raises(self, manager, mock_repository):
        """Test group existence check raises for missing group"""
        mock_repository.get_group_by_id.return_value = None
        with pytest.raises(ValueError) as exc_info:
            manager._require_group_exists("nonexistent")
        assert "User group not found" in str(exc_info.value)

    # ========== Group CRUD Tests ==========

    def test_get_all_groups_success(self, manager, mock_repository, admin_user, sample_group):
        """Test successful retrieval of all groups"""
        mock_repository.get_all_groups.return_value = [sample_group]
        mock_repository.get_group_member_count.return_value = 3
        mock_repository.get_group_preset_count.return_value = 2
        mock_repository.get_group_llm_count.return_value = 1
        mock_repository.get_group_model_count.return_value = 4

        result = manager.get_all_groups(admin_user)

        assert len(result) == 1
        assert result[0].name == "Test Group"
        assert result[0].member_count == 3
        assert result[0].preset_count == 2
        assert result[0].llm_count == 1
        assert result[0].model_count == 4

    def test_get_all_groups_empty(self, manager, mock_repository, admin_user):
        """Test retrieval when no groups exist"""
        mock_repository.get_all_groups.return_value = []

        result = manager.get_all_groups(admin_user)

        assert len(result) == 0

    def test_create_group_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test successful group creation"""
        mock_repository.get_group_by_name.return_value = None
        mock_repository.create_group.return_value = sample_group

        request = GroupCreate(name="Test Group", description="A test group")
        result = manager.create_group(request, admin_user)

        assert result.name == "Test Group"
        assert result.description == "A test group"
        mock_repository.create_group.assert_called_once_with(
            name="Test Group",
            description="A test group"
        )

    def test_create_group_duplicate_name(self, manager, mock_repository, admin_user, sample_group):
        """Test creating group with duplicate name fails"""
        mock_repository.get_group_by_name.return_value = sample_group

        request = GroupCreate(name="Test Group")
        with pytest.raises(ValueError) as exc_info:
            manager.create_group(request, admin_user)
        assert "already exists" in str(exc_info.value)

    def test_create_group_blocked_by_hook(self, manager, mock_repository, mock_plugin_registry, admin_user):
        """Test group creation blocked by plugin hook"""
        mock_repository.get_group_by_name.return_value = None

        # Configure hook to block
        context = Mock()
        context.data = {"blocked": True, "block_reason": "Blocked by test plugin"}
        mock_plugin_registry.execute_hook.return_value = (context, [])

        request = GroupCreate(name="Test Group")
        with pytest.raises(ValueError) as exc_info:
            manager.create_group(request, admin_user)
        assert "Blocked by test plugin" in str(exc_info.value)

    def test_get_group_success(self, manager, mock_repository, admin_user, sample_group):
        """Test successful group retrieval"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.get_group_member_count.return_value = 5
        mock_repository.get_group_preset_count.return_value = 3
        mock_repository.get_group_llm_count.return_value = 2
        mock_repository.get_group_model_count.return_value = 1

        result = manager.get_group("group-123", admin_user)

        assert result.id == "group-123"
        assert result.member_count == 5

    def test_get_group_not_found(self, manager, mock_repository, admin_user):
        """Test getting non-existent group"""
        mock_repository.get_group_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            manager.get_group("nonexistent", admin_user)
        assert "User group not found" in str(exc_info.value)

    def test_update_group_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test successful group update"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.get_group_by_name.return_value = None
        updated = UserGroup(
            id="group-123",
            name="Updated Name",
            description="Updated desc",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        mock_repository.update_group.return_value = updated

        request = GroupUpdate(name="Updated Name", description="Updated desc")
        result = manager.update_group("group-123", request, admin_user)

        assert result.name == "Updated Name"

    def test_update_group_duplicate_name(self, manager, mock_repository, admin_user, sample_group):
        """Test update with name already used by another group"""
        mock_repository.get_group_by_id.return_value = sample_group
        other_group = UserGroup(
            id="other-123",
            name="Other Group",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        mock_repository.get_group_by_name.return_value = other_group

        request = GroupUpdate(name="Other Group")
        with pytest.raises(ValueError) as exc_info:
            manager.update_group("group-123", request, admin_user)
        assert "already exists" in str(exc_info.value)

    def test_delete_group_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test successful group deletion"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.delete_group.return_value = True

        result = manager.delete_group("group-123", admin_user)

        assert result == "Test Group"
        mock_repository.delete_group.assert_called_once_with("group-123")

    def test_delete_group_not_found(self, manager, mock_repository, admin_user):
        """Test deleting non-existent group"""
        mock_repository.get_group_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            manager.delete_group("nonexistent", admin_user)
        assert "User group not found" in str(exc_info.value)

    def test_delete_group_system_protected(self, manager, mock_repository, admin_user):
        """Built-in groups (is_system=True) refuse deletion with a specific error type."""
        from src.features.user_groups.manager import SystemGroupProtectedError

        system_group = UserGroup(
            id="all_users", name="All Users", description="Everyone",
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(), is_system=True,
        )
        mock_repository.get_group_by_id.return_value = system_group

        with pytest.raises(SystemGroupProtectedError) as exc_info:
            manager.delete_group("all_users", admin_user)
        assert "All Users" in str(exc_info.value)
        mock_repository.delete_group.assert_not_called()

    # ========== Member Tests ==========

    def test_get_group_members_success(self, manager, mock_repository, admin_user, sample_group, sample_member):
        """Test successful retrieval of group members"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.get_group_members.return_value = [sample_member]

        result = manager.get_group_members("group-123", admin_user)

        assert len(result) == 1
        assert result[0].user_id == "user-456"

    def test_add_members_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group, sample_member):
        """Test successful member addition"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.add_user_to_group.return_value = sample_member

        result = manager.add_members("group-123", ["user-456"], admin_user)

        assert len(result) == 1
        mock_repository.add_user_to_group.assert_called_once_with("group-123", "user-456")

    def test_add_members_duplicate_skipped(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test duplicate member addition is skipped"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.add_user_to_group.return_value = None  # duplicate

        result = manager.add_members("group-123", ["user-456"], admin_user)

        assert len(result) == 0

    def test_remove_member_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test successful member removal"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.remove_user_from_group.return_value = True

        result = manager.remove_member("group-123", "user-456", admin_user)

        assert result is True
        mock_repository.remove_user_from_group.assert_called_once_with("group-123", "user-456")

    def test_remove_member_not_found(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test removing non-existent member"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.remove_user_from_group.return_value = False

        with pytest.raises(ValueError) as exc_info:
            manager.remove_member("group-123", "user-999", admin_user)
        assert "not a member" in str(exc_info.value)

    def test_get_user_groups_success(self, manager, mock_repository, admin_user, sample_group):
        """Test getting groups for a user"""
        mock_repository.get_user_groups.return_value = [sample_group]

        result = manager.get_user_groups("user-456", admin_user)

        assert len(result) == 1
        assert result[0].name == "Test Group"

    # ========== Preset Tests ==========

    def test_get_group_presets_success(self, manager, mock_repository, admin_user, sample_group, sample_preset_assignment):
        """Test getting presets assigned to a group"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.get_group_presets.return_value = [sample_preset_assignment]

        result = manager.get_group_presets("group-123", admin_user)

        assert len(result) == 1
        assert result[0].preset_id == "preset-123"

    def test_assign_presets_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group, sample_preset_assignment):
        """Test assigning presets to a group"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.assign_preset_to_group.return_value = sample_preset_assignment

        result = manager.assign_presets("group-123", ["preset-123"], admin_user)

        assert len(result) == 1
        mock_repository.assign_preset_to_group.assert_called_once_with("group-123", "preset-123")

    def test_unassign_preset_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test unassigning a preset from a group"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.unassign_preset_from_group.return_value = True

        result = manager.unassign_preset("group-123", "preset-123", admin_user)

        assert result is True

    def test_unassign_preset_not_assigned(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test unassigning a preset that was not assigned"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.unassign_preset_from_group.return_value = False

        with pytest.raises(ValueError) as exc_info:
            manager.unassign_preset("group-123", "preset-999", admin_user)
        assert "not assigned" in str(exc_info.value)

    # ========== LLM Tests ==========

    def test_get_group_llms_success(self, manager, mock_repository, admin_user, sample_group, sample_llm_assignment):
        """Test getting LLMs assigned to a group"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.get_group_llms.return_value = [sample_llm_assignment]

        result = manager.get_group_llms("group-123", admin_user)

        assert len(result) == 1
        assert result[0].llm_config_id == "llm-123"

    def test_assign_llms_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group, sample_llm_assignment):
        """Test assigning LLMs to a group"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.assign_llm_to_group.return_value = sample_llm_assignment

        result = manager.assign_llms("group-123", ["llm-123"], admin_user)

        assert len(result) == 1
        mock_repository.assign_llm_to_group.assert_called_once_with("group-123", "llm-123")

    def test_unassign_llm_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test unassigning an LLM from a group"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.unassign_llm_from_group.return_value = True

        result = manager.unassign_llm("group-123", "llm-123", admin_user)

        assert result is True

    def test_unassign_llm_not_assigned(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test unassigning an LLM that was not assigned"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.unassign_llm_from_group.return_value = False

        with pytest.raises(ValueError) as exc_info:
            manager.unassign_llm("group-123", "llm-999", admin_user)
        assert "not assigned" in str(exc_info.value)

    # ========== Model Tests ==========

    def test_get_group_models_success(self, manager, mock_repository, admin_user, sample_group, sample_model_assignment):
        """Test getting models assigned to a group"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.get_group_models.return_value = [sample_model_assignment]

        result = manager.get_group_models("group-123", admin_user)

        assert len(result) == 1
        assert result[0].model_id == "model-123"

    def test_assign_models_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group, sample_model_assignment):
        """Test assigning models to a group"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.assign_model_to_group.return_value = sample_model_assignment

        result = manager.assign_models("group-123", ["model-123"], admin_user)

        assert len(result) == 1
        mock_repository.assign_model_to_group.assert_called_once_with("group-123", "model-123")

    def test_unassign_model_success(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test unassigning a model from a group"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.unassign_model_from_group.return_value = True

        result = manager.unassign_model("group-123", "model-123", admin_user)

        assert result is True

    def test_unassign_model_not_assigned(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test unassigning a model that was not assigned"""
        mock_repository.get_group_by_id.return_value = sample_group
        mock_repository.unassign_model_from_group.return_value = False

        with pytest.raises(ValueError) as exc_info:
            manager.unassign_model("group-123", "model-999", admin_user)
        assert "not assigned" in str(exc_info.value)

    # ========== Hook Tests ==========

    def test_hook_can_modify_group_name(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test that hooks can modify group data"""
        mock_repository.get_group_by_name.return_value = None

        # Configure hook to modify name
        context = Mock()
        context.data = {"name": "Modified by hook", "description": "Modified desc"}
        mock_plugin_registry.execute_hook.return_value = (context, [])

        modified_group = UserGroup(
            id="group-123",
            name="Modified by hook",
            description="Modified desc",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        mock_repository.create_group.return_value = modified_group

        request = GroupCreate(name="Original Name", description="Original desc")
        result = manager.create_group(request, admin_user)

        # Verify the modified values were used
        mock_repository.create_group.assert_called_once_with(
            name="Modified by hook",
            description="Modified desc"
        )

    def test_add_member_blocked_by_hook(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test that hook can block member addition"""
        mock_repository.get_group_by_id.return_value = sample_group

        # Configure hook to block
        context = Mock()
        context.data = {"blocked": True, "block_reason": "Member blocked by policy"}
        mock_plugin_registry.execute_hook.return_value = (context, [])

        result = manager.add_members("group-123", ["user-456"], admin_user)

        # Member should not be added
        assert len(result) == 0
        mock_repository.add_user_to_group.assert_not_called()

    def test_remove_member_blocked_by_hook(self, manager, mock_repository, mock_plugin_registry, admin_user, sample_group):
        """Test that hook can block member removal"""
        mock_repository.get_group_by_id.return_value = sample_group

        # Configure hook to block
        context = Mock()
        context.data = {"blocked": True, "block_reason": "Cannot remove this member"}
        mock_plugin_registry.execute_hook.return_value = (context, [])

        with pytest.raises(ValueError) as exc_info:
            manager.remove_member("group-123", "user-456", admin_user)
        assert "Cannot remove this member" in str(exc_info.value)
