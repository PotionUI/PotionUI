"""
Tests for UserGroupController.

Tests the thin controller layer that delegates to UserGroupManager.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from datetime import datetime

from src.features.user_groups.routes import (
    UserGroupController,
    build_router,
)
from src.features.user_groups.dto import (
    GroupCreate,
    GroupUpdate,
    MemberIds,
    PresetIds,
    LLMConfigIds,
    ModelIds,
    UserGroupDTO,
    GroupWithCountsDTO,
    UserGroupMemberDTO,
    UserGroupPresetDTO,
    UserGroupLLMDTO,
    UserGroupModelDTO,
)
from src.platform.http.base_controller import APIResponse
from src.platform.security.user import User, AccountType
from src.features.user_groups import UserGroupManager


class TestUserGroupController:
    """Tests for UserGroupController"""

    @pytest.fixture
    def mock_manager(self):
        """Mock UserGroupManager"""
        return Mock(spec=UserGroupManager)

    @pytest.fixture
    def admin_user(self):
        """Sample admin user"""
        return User(
            id="admin-user-123",
            username="admin",
            email="admin@example.com",
            password_hash="$2b$12$admin.hash",
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
            password_hash="$2b$12$test.hash",
            account_type=AccountType.USER,
            created_at=datetime.utcnow(),
            last_login=None
        )

    @pytest.fixture
    def sample_group_dto(self):
        """Sample UserGroupDTO"""
        return UserGroupDTO(
            id="group-123",
            name="Test Group",
            description="A test group",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_group_with_counts(self):
        """Sample GroupWithCountsDTO"""
        return GroupWithCountsDTO(
            id="group-123",
            name="Test Group",
            description="A test group",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            member_count=3,
            preset_count=2,
            llm_count=1,
            model_count=4
        )

    @pytest.fixture
    def sample_member_dto(self):
        """Sample UserGroupMemberDTO"""
        return UserGroupMemberDTO(
            id="member-123",
            group_id="group-123",
            user_id="user-456",
            assigned_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_preset_dto(self):
        """Sample UserGroupPresetDTO"""
        return UserGroupPresetDTO(
            id="gp-123",
            group_id="group-123",
            preset_id="preset-123",
            assigned_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_llm_dto(self):
        """Sample UserGroupLLMDTO"""
        return UserGroupLLMDTO(
            id="gl-123",
            group_id="group-123",
            llm_config_id="llm-123",
            assigned_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_model_dto(self):
        """Sample UserGroupModelDTO"""
        return UserGroupModelDTO(
            id="gm-123",
            group_id="group-123",
            model_id="model-123",
            assigned_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def controller(self, mock_manager):
        """UserGroupController instance with mocked manager"""
        return UserGroupController(mock_manager)

    # ========== Controller Initialization ==========

    def test_controller_initialization(self, controller):
        """Test controller initializes correctly"""
        assert controller is not None
        assert controller.manager is not None

    def test_router_prefix(self, controller):
        """Test router has correct prefix"""
        router = build_router(SimpleNamespace(user_group_controller=controller))
        assert router.prefix == "/api/user-groups"
        assert "user-groups" in router.tags

    # ========== Group CRUD Tests ==========

    @pytest.mark.asyncio
    async def test_get_all_groups_success(self, controller, mock_manager, admin_user, sample_group_with_counts):
        """Test successful retrieval of all groups"""
        mock_manager.get_all_groups.return_value = [sample_group_with_counts]

        response = await controller.get_all_groups(admin_user)

        assert response.success is True
        assert len(response.data) == 1
        assert response.data[0]['name'] == "Test Group"
        assert response.data[0]['member_count'] == 3
        mock_manager.get_all_groups.assert_called_once_with(admin_user)

    @pytest.mark.asyncio
    async def test_get_all_groups_non_admin(self, controller, mock_manager, regular_user):
        """Test non-admin user gets error"""
        mock_manager.get_all_groups.side_effect = ValueError("Admin access required")

        response = await controller.get_all_groups(regular_user)

        assert response.success is False
        assert "Admin access required" in response.message

    @pytest.mark.asyncio
    async def test_get_all_groups_empty(self, controller, mock_manager, admin_user):
        """Test retrieval when no groups exist"""
        mock_manager.get_all_groups.return_value = []

        response = await controller.get_all_groups(admin_user)

        assert response.success is True
        assert len(response.data) == 0
        assert "0 groups" in response.message

    @pytest.mark.asyncio
    async def test_create_group_success(self, controller, mock_manager, admin_user, sample_group_dto):
        """Test successful group creation"""
        mock_manager.create_group.return_value = sample_group_dto

        data = GroupCreate(name="Test Group", description="A test group")
        response = await controller.create_group(data, admin_user)

        assert response.success is True
        assert response.data['name'] == "Test Group"
        mock_manager.create_group.assert_called_once_with(data, admin_user)

    @pytest.mark.asyncio
    async def test_create_group_duplicate_name(self, controller, mock_manager, admin_user):
        """Test creating group with duplicate name"""
        mock_manager.create_group.side_effect = ValueError("A group with this name already exists")

        data = GroupCreate(name="Duplicate Group")
        response = await controller.create_group(data, admin_user)

        assert response.success is False
        assert "already exists" in response.message

    @pytest.mark.asyncio
    async def test_get_group_success(self, controller, mock_manager, admin_user, sample_group_with_counts):
        """Test successful group retrieval"""
        mock_manager.get_group.return_value = sample_group_with_counts

        response = await controller.get_group("group-123", admin_user)

        assert response.success is True
        assert response.data['id'] == "group-123"
        assert response.data['member_count'] == 3
        mock_manager.get_group.assert_called_once_with("group-123", admin_user)

    @pytest.mark.asyncio
    async def test_get_group_not_found(self, controller, mock_manager, admin_user):
        """Test getting a non-existent group"""
        mock_manager.get_group.side_effect = ValueError("User group not found")

        response = await controller.get_group("nonexistent", admin_user)

        assert response.success is False
        assert "not found" in response.message

    @pytest.mark.asyncio
    async def test_update_group_success(self, controller, mock_manager, admin_user, sample_group_dto):
        """Test successful group update"""
        updated_dto = UserGroupDTO(
            id="group-123",
            name="Updated Name",
            description="Updated desc",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        mock_manager.update_group.return_value = updated_dto

        data = GroupUpdate(name="Updated Name", description="Updated desc")
        response = await controller.update_group("group-123", data, admin_user)

        assert response.success is True
        assert response.data['name'] == "Updated Name"
        mock_manager.update_group.assert_called_once_with("group-123", data, admin_user)

    @pytest.mark.asyncio
    async def test_update_group_not_found(self, controller, mock_manager, admin_user):
        """Test updating a non-existent group"""
        mock_manager.update_group.side_effect = ValueError("User group not found")

        data = GroupUpdate(name="New Name")
        response = await controller.update_group("nonexistent", data, admin_user)

        assert response.success is False
        assert "not found" in response.message

    @pytest.mark.asyncio
    async def test_delete_group_success(self, controller, mock_manager, admin_user):
        """Test successful group deletion"""
        mock_manager.delete_group.return_value = "Test Group"

        response = await controller.delete_group("group-123", admin_user)

        assert response.success is True
        assert "deleted successfully" in response.message
        mock_manager.delete_group.assert_called_once_with("group-123", admin_user)

    @pytest.mark.asyncio
    async def test_delete_group_not_found(self, controller, mock_manager, admin_user):
        """Test deleting a non-existent group"""
        mock_manager.delete_group.side_effect = ValueError("User group not found")

        response = await controller.delete_group("nonexistent", admin_user)

        assert response.success is False
        assert "not found" in response.message

    @pytest.mark.asyncio
    async def test_delete_group_system_protected_raises_409(self, controller, mock_manager, admin_user):
        """Deleting a built-in group (All Users / All Admins) is a 409, not a normal error response."""
        from fastapi import HTTPException
        from src.features.user_groups.manager import SystemGroupProtectedError

        mock_manager.delete_group.side_effect = SystemGroupProtectedError("All Users")

        with pytest.raises(HTTPException) as exc_info:
            await controller.delete_group("all_users", admin_user)

        assert exc_info.value.status_code == 409
        assert "All Users" in str(exc_info.value.detail)

    # ========== Members Tests ==========

    @pytest.mark.asyncio
    async def test_get_group_members_success(self, controller, mock_manager, admin_user, sample_member_dto):
        """Test successful retrieval of group members"""
        mock_manager.get_group_members.return_value = [sample_member_dto]

        response = await controller.get_group_members("group-123", admin_user)

        assert response.success is True
        assert len(response.data) == 1
        assert response.data[0]['user_id'] == "user-456"

    @pytest.mark.asyncio
    async def test_get_group_members_group_not_found(self, controller, mock_manager, admin_user):
        """Test getting members of non-existent group"""
        mock_manager.get_group_members.side_effect = ValueError("User group not found")

        response = await controller.get_group_members("nonexistent", admin_user)

        assert response.success is False
        assert "not found" in response.message

    @pytest.mark.asyncio
    async def test_add_members_success(self, controller, mock_manager, admin_user, sample_member_dto):
        """Test successful member addition"""
        mock_manager.add_members.return_value = [sample_member_dto]

        data = MemberIds(user_ids=["user-456"])
        response = await controller.add_members("group-123", data, admin_user)

        assert response.success is True
        assert len(response.data) == 1
        mock_manager.add_members.assert_called_once_with("group-123", ["user-456"], admin_user)

    @pytest.mark.asyncio
    async def test_add_members_duplicate_skipped(self, controller, mock_manager, admin_user):
        """Test adding duplicate member is skipped"""
        mock_manager.add_members.return_value = []

        data = MemberIds(user_ids=["user-456"])
        response = await controller.add_members("group-123", data, admin_user)

        assert response.success is True
        assert len(response.data) == 0
        assert "0 members" in response.message

    @pytest.mark.asyncio
    async def test_remove_member_success(self, controller, mock_manager, admin_user):
        """Test successful member removal"""
        mock_manager.remove_member.return_value = True

        response = await controller.remove_member("group-123", "user-456", admin_user)

        assert response.success is True
        mock_manager.remove_member.assert_called_once_with("group-123", "user-456", admin_user)

    @pytest.mark.asyncio
    async def test_remove_member_not_found(self, controller, mock_manager, admin_user):
        """Test removing a user who is not a member"""
        mock_manager.remove_member.side_effect = ValueError("User is not a member of this group")

        response = await controller.remove_member("group-123", "user-999", admin_user)

        assert response.success is False
        assert "not a member" in response.message

    # ========== User Groups Tests ==========

    @pytest.mark.asyncio
    async def test_get_user_groups_success(self, controller, mock_manager, admin_user, sample_group_dto):
        """Test getting groups for a specific user"""
        mock_manager.get_user_groups.return_value = [sample_group_dto]

        response = await controller.get_user_groups("user-456", admin_user)

        assert response.success is True
        assert len(response.data) == 1
        assert response.data[0]['name'] == "Test Group"

    # ========== Presets Tests ==========

    @pytest.mark.asyncio
    async def test_get_group_presets_success(self, controller, mock_manager, admin_user, sample_preset_dto):
        """Test getting presets assigned to a group"""
        mock_manager.get_group_presets.return_value = [sample_preset_dto]

        response = await controller.get_group_presets("group-123", admin_user)

        assert response.success is True
        assert len(response.data) == 1
        assert response.data[0]['preset_id'] == "preset-123"

    @pytest.mark.asyncio
    async def test_assign_presets_success(self, controller, mock_manager, admin_user, sample_preset_dto):
        """Test assigning presets to a group"""
        mock_manager.assign_presets.return_value = [sample_preset_dto]

        data = PresetIds(preset_ids=["preset-123"])
        response = await controller.assign_presets("group-123", data, admin_user)

        assert response.success is True
        assert len(response.data) == 1
        mock_manager.assign_presets.assert_called_once_with("group-123", ["preset-123"], admin_user)

    @pytest.mark.asyncio
    async def test_assign_presets_duplicate_skipped(self, controller, mock_manager, admin_user):
        """Test assigning duplicate preset is skipped"""
        mock_manager.assign_presets.return_value = []

        data = PresetIds(preset_ids=["preset-123"])
        response = await controller.assign_presets("group-123", data, admin_user)

        assert response.success is True
        assert len(response.data) == 0

    @pytest.mark.asyncio
    async def test_unassign_preset_success(self, controller, mock_manager, admin_user):
        """Test unassigning a preset from a group"""
        mock_manager.unassign_preset.return_value = True

        response = await controller.unassign_preset("group-123", "preset-123", admin_user)

        assert response.success is True

    @pytest.mark.asyncio
    async def test_unassign_preset_not_assigned(self, controller, mock_manager, admin_user):
        """Test unassigning a preset that was not assigned"""
        mock_manager.unassign_preset.side_effect = ValueError("Preset is not assigned to this group")

        response = await controller.unassign_preset("group-123", "preset-999", admin_user)

        assert response.success is False
        assert "not assigned" in response.message

    # ========== LLMs Tests ==========

    @pytest.mark.asyncio
    async def test_get_group_llms_success(self, controller, mock_manager, admin_user, sample_llm_dto):
        """Test getting LLMs assigned to a group"""
        mock_manager.get_group_llms.return_value = [sample_llm_dto]

        response = await controller.get_group_llms("group-123", admin_user)

        assert response.success is True
        assert len(response.data) == 1
        assert response.data[0]['llm_config_id'] == "llm-123"

    @pytest.mark.asyncio
    async def test_assign_llms_success(self, controller, mock_manager, admin_user, sample_llm_dto):
        """Test assigning LLMs to a group"""
        mock_manager.assign_llms.return_value = [sample_llm_dto]

        data = LLMConfigIds(llm_config_ids=["llm-123"])
        response = await controller.assign_llms("group-123", data, admin_user)

        assert response.success is True
        assert len(response.data) == 1
        mock_manager.assign_llms.assert_called_once_with("group-123", ["llm-123"], admin_user)

    @pytest.mark.asyncio
    async def test_assign_llms_duplicate_skipped(self, controller, mock_manager, admin_user):
        """Test assigning duplicate LLM is skipped"""
        mock_manager.assign_llms.return_value = []

        data = LLMConfigIds(llm_config_ids=["llm-123"])
        response = await controller.assign_llms("group-123", data, admin_user)

        assert response.success is True
        assert len(response.data) == 0

    @pytest.mark.asyncio
    async def test_unassign_llm_success(self, controller, mock_manager, admin_user):
        """Test unassigning an LLM from a group"""
        mock_manager.unassign_llm.return_value = True

        response = await controller.unassign_llm("group-123", "llm-123", admin_user)

        assert response.success is True

    @pytest.mark.asyncio
    async def test_unassign_llm_not_assigned(self, controller, mock_manager, admin_user):
        """Test unassigning an LLM that was not assigned"""
        mock_manager.unassign_llm.side_effect = ValueError("LLM configuration is not assigned to this group")

        response = await controller.unassign_llm("group-123", "llm-999", admin_user)

        assert response.success is False
        assert "not assigned" in response.message

    # ========== Models Tests ==========

    @pytest.mark.asyncio
    async def test_get_group_models_success(self, controller, mock_manager, admin_user, sample_model_dto):
        """Test getting models assigned to a group"""
        mock_manager.get_group_models.return_value = [sample_model_dto]

        response = await controller.get_group_models("group-123", admin_user)

        assert response.success is True
        assert len(response.data) == 1
        assert response.data[0]['model_id'] == "model-123"

    @pytest.mark.asyncio
    async def test_assign_models_success(self, controller, mock_manager, admin_user, sample_model_dto):
        """Test assigning models to a group"""
        mock_manager.assign_models.return_value = [sample_model_dto]

        data = ModelIds(model_ids=["model-123"])
        response = await controller.assign_models("group-123", data, admin_user)

        assert response.success is True
        assert len(response.data) == 1
        mock_manager.assign_models.assert_called_once_with("group-123", ["model-123"], admin_user)

    @pytest.mark.asyncio
    async def test_assign_models_duplicate_skipped(self, controller, mock_manager, admin_user):
        """Test assigning duplicate model is skipped"""
        mock_manager.assign_models.return_value = []

        data = ModelIds(model_ids=["model-123"])
        response = await controller.assign_models("group-123", data, admin_user)

        assert response.success is True
        assert len(response.data) == 0

    @pytest.mark.asyncio
    async def test_unassign_model_success(self, controller, mock_manager, admin_user):
        """Test unassigning a model from a group"""
        mock_manager.unassign_model.return_value = True

        response = await controller.unassign_model("group-123", "model-123", admin_user)

        assert response.success is True

    @pytest.mark.asyncio
    async def test_unassign_model_not_assigned(self, controller, mock_manager, admin_user):
        """Test unassigning a model that was not assigned"""
        mock_manager.unassign_model.side_effect = ValueError("Model is not assigned to this group")

        response = await controller.unassign_model("group-123", "model-999", admin_user)

        assert response.success is False
        assert "not assigned" in response.message

    # ========== DTO Tests ==========

    def test_group_create_dto(self):
        """Test GroupCreate DTO"""
        data = GroupCreate(name="Test", description="Desc")
        assert data.name == "Test"
        assert data.description == "Desc"

    def test_group_create_no_description(self):
        """Test GroupCreate with no description"""
        data = GroupCreate(name="Test")
        assert data.name == "Test"
        assert data.description is None

    def test_group_update_dto(self):
        """Test GroupUpdate DTO"""
        data = GroupUpdate(name="New Name", description="New Desc")
        assert data.name == "New Name"
        assert data.description == "New Desc"

    def test_group_update_partial(self):
        """Test GroupUpdate with partial fields"""
        data = GroupUpdate(name="New Name")
        assert data.name == "New Name"
        assert data.description is None

    def test_member_ids_dto(self):
        """Test MemberIds DTO"""
        data = MemberIds(user_ids=["u1", "u2", "u3"])
        assert len(data.user_ids) == 3

    def test_preset_ids_dto(self):
        """Test PresetIds DTO"""
        data = PresetIds(preset_ids=["p1", "p2"])
        assert len(data.preset_ids) == 2

    def test_llm_config_ids_dto(self):
        """Test LLMConfigIds DTO"""
        data = LLMConfigIds(llm_config_ids=["l1"])
        assert len(data.llm_config_ids) == 1

    def test_model_ids_dto(self):
        """Test ModelIds DTO"""
        data = ModelIds(model_ids=["m1", "m2"])
        assert len(data.model_ids) == 2
