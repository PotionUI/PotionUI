"""Tests for PresetController - refactored version using PresetManager."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from fastapi import HTTPException

from src.features.presets.routes import PresetController
from src.platform.http.base_controller import APIResponse
from src.features.forms.exceptions import FormNotFoundException
from src.features.presets.manager import PresetManager
from src.features.presets.exceptions import (
    PresetNotFoundException,
    ModeNotFoundException,
    NoModesAvailableException,
    PresetNotInstalledException,
    PresetAlreadyInstalledException,
    PresetNotAssignedException,
    UserNotFoundException,
    InvalidUsersException,
    PermissionDeniedException,
)
from src.pipelines.graph import PipelineGraph
from src.platform.security.user import AccountType


class TestPresetController:
    """Comprehensive tests for PresetController with PresetManager."""

    @pytest.fixture
    def mock_preset_manager(self):
        """Mock PresetManager."""
        return Mock(spec=PresetManager)

    @pytest.fixture
    def mock_current_user(self):
        """Mock current user."""
        user = Mock()
        user.id = "test-user-123"
        user.username = "testuser"
        user.account_type = AccountType.USER
        return user

    @pytest.fixture
    def mock_admin_user(self):
        """Mock admin user."""
        user = Mock()
        user.id = "admin-user-123"
        user.username = "adminuser"
        user.account_type = AccountType.ADMIN
        return user

    @pytest.fixture
    def mock_backend_registry(self):
        """PresetController needs the registry to scope models to a preset's engine."""
        return Mock()

    @pytest.fixture
    def controller(self, mock_preset_manager, mock_backend_registry):
        """Create PresetController instance with mocked collaborators."""
        return PresetController(mock_preset_manager, mock_backend_registry)

    # ===== list_presets tests =====

    @pytest.mark.asyncio
    async def test_list_presets_success(self, controller, mock_preset_manager, mock_current_user):
        """Test successful preset listing."""
        mock_preset_manager.list_presets.return_value = [
            {"id": "preset-1", "name": "Preset 1"},
            {"id": "preset-2", "name": "Preset 2"},
        ]

        result = await controller.list_presets(mock_current_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert len(result.data) == 2
        mock_preset_manager.list_presets.assert_called_once_with(mock_current_user, False)

    @pytest.mark.asyncio
    async def test_list_presets_failure(self, controller, mock_preset_manager, mock_current_user):
        """Test preset listing failure."""
        mock_preset_manager.list_presets.side_effect = Exception("Database error")

        with pytest.raises(HTTPException) as exc_info:
            await controller.list_presets(mock_current_user)

        assert exc_info.value.status_code == 500
        assert "preset_list_failed" in str(exc_info.value.detail)

    # ===== get_preset tests =====

    @pytest.mark.asyncio
    async def test_get_preset_success(self, controller, mock_preset_manager):
        """Test successful preset retrieval."""
        mock_preset_manager.get_preset.return_value = {
            "id": "test-preset",
            "name": "Test Preset",
            "vars": {"key": "value"},
        }

        result = await controller.get_preset("test-preset")

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data["id"] == "test-preset"
        mock_preset_manager.get_preset.assert_called_once_with("test-preset")

    @pytest.mark.asyncio
    async def test_get_preset_not_found(self, controller, mock_preset_manager):
        """Test preset not found scenario."""
        mock_preset_manager.get_preset.side_effect = PresetNotFoundException("non-existent")

        result = await controller.get_preset("non-existent")

        assert isinstance(result, APIResponse)
        assert result.success is False
        assert result.error == "preset_not_found"

    # ===== get_available_modes tests =====

    @pytest.mark.asyncio
    async def test_get_available_modes_success(self, controller, mock_preset_manager):
        """Test successful mode retrieval."""
        mock_preset_manager.get_available_modes.return_value = {
            "preset_id": "test-preset",
            "modes": [
                {"name": "txt2img", "label": "Txt2img"},
                {"name": "img2img", "label": "Img2img"},
            ],
            "default_mode": "txt2img",
        }

        result = await controller.get_available_modes("test-preset")

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert len(result.data["modes"]) == 2

    @pytest.mark.asyncio
    async def test_get_available_modes_not_found(self, controller, mock_preset_manager):
        """Test mode retrieval for non-existent preset."""
        mock_preset_manager.get_available_modes.side_effect = PresetNotFoundException("non-existent")

        result = await controller.get_available_modes("non-existent")

        assert result.success is False
        assert result.error == "preset_not_found"

    # ===== get_preset_form_schema tests =====

    @pytest.mark.asyncio
    async def test_get_preset_form_schema_success(self, controller, mock_preset_manager):
        """Test successful form schema retrieval."""
        mock_preset_manager.get_form_schema.return_value = {
            "preset_id": "test-preset",
            "form_schema": {"fields": []},
            "debug_info": {},
        }

        result = await controller.get_preset_form_schema("test-preset", "txt2img")

        assert isinstance(result, APIResponse)
        assert result.success is True
        mock_preset_manager.get_form_schema.assert_called_once_with("test-preset", "txt2img", None)

    @pytest.mark.asyncio
    async def test_get_preset_form_schema_mode_not_found(self, controller, mock_preset_manager):
        """Test form schema retrieval with invalid mode."""
        mock_preset_manager.get_form_schema.side_effect = ModeNotFoundException("test-preset", "invalid-mode")

        result = await controller.get_preset_form_schema("test-preset", "invalid-mode")

        assert result.success is False
        assert result.error == "mode_not_found"

    @pytest.mark.asyncio
    async def test_get_preset_form_schema_form_not_found(self, controller, mock_preset_manager):
        """Test form schema retrieval with non-existent form."""
        mock_preset_manager.get_form_schema.side_effect = FormNotFoundException("test-preset", "txt2img", "custom")

        result = await controller.get_preset_form_schema("test-preset", "txt2img", "custom")

        assert result.success is False
        assert result.error == "form_not_found"

    @pytest.mark.asyncio
    async def test_get_preset_form_schema_no_modes(self, controller, mock_preset_manager):
        """Test form schema retrieval with no modes available."""
        mock_preset_manager.get_form_schema.side_effect = NoModesAvailableException("test-preset")

        result = await controller.get_preset_form_schema("test-preset")

        assert result.success is False
        assert result.error == "no_modes_available"

    # ===== get_pipes tests =====

    @pytest.mark.asyncio
    async def test_get_pipes_success(self, controller, mock_preset_manager):
        """Test successful pipeline retrieval."""
        mock_result = Mock(spec=PipelineGraph)
        mock_result.to_dict.return_value = {
            "preset_id": "test-preset",
            "mode": "txt2img",
            "nodes": [],
            "connections": [],
            "debug_info": {},
        }
        mock_preset_manager.get_pipeline.return_value = mock_result

        result = await controller.get_pipes("test-preset", "txt2img", {})

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data["preset_id"] == "test-preset"

    @pytest.mark.asyncio
    async def test_get_pipes_mode_not_found(self, controller, mock_preset_manager):
        """Test pipeline retrieval with invalid mode."""
        mock_preset_manager.get_pipeline.side_effect = ModeNotFoundException("test-preset", "invalid-mode")

        result = await controller.get_pipes("test-preset", "invalid-mode")

        assert result.success is False
        assert result.error == "mode_not_found"

    # ===== reload_preset tests =====

    @pytest.mark.asyncio
    async def test_reload_preset_success(self, controller, mock_preset_manager):
        """Test successful preset reload."""
        mock_preset_manager.reload_preset.return_value = {"id": "test-preset", "name": "Test"}

        result = await controller.reload_preset("test-preset")

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert "reloaded successfully" in result.message

    # ===== install_preset tests =====

    @pytest.mark.asyncio
    async def test_install_preset_success(self, controller, mock_preset_manager, mock_admin_user):
        """Test successful preset installation."""
        mock_preset_manager.install_preset.return_value = {"id": "installed-id"}

        result = await controller.install_preset("test-preset", mock_admin_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert "installed successfully" in result.message

    @pytest.mark.asyncio
    async def test_install_preset_permission_denied(self, controller, mock_preset_manager, mock_current_user):
        """Test preset installation without admin permission."""
        mock_preset_manager.install_preset.side_effect = PermissionDeniedException("install_preset")

        result = await controller.install_preset("test-preset", mock_current_user)

        assert result.success is False
        assert result.error == "permission_denied"

    @pytest.mark.asyncio
    async def test_install_preset_already_installed(self, controller, mock_preset_manager, mock_admin_user):
        """Test installing already installed preset."""
        mock_preset_manager.install_preset.side_effect = PresetAlreadyInstalledException("test-preset")

        result = await controller.install_preset("test-preset", mock_admin_user)

        assert result.success is False
        assert result.error == "preset_already_installed"

    # ===== uninstall_preset tests =====

    @pytest.mark.asyncio
    async def test_uninstall_preset_success(self, controller, mock_preset_manager, mock_admin_user):
        """Test successful preset uninstallation."""
        mock_preset_manager.uninstall_preset.return_value = "Preset uninstalled. Removed 3 assignments."

        result = await controller.uninstall_preset("test-preset", mock_admin_user)

        assert isinstance(result, APIResponse)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_uninstall_preset_not_installed(self, controller, mock_preset_manager, mock_admin_user):
        """Test uninstalling non-installed preset."""
        mock_preset_manager.uninstall_preset.side_effect = PresetNotInstalledException("test-preset")

        result = await controller.uninstall_preset("test-preset", mock_admin_user)

        assert result.success is False
        assert result.error == "preset_not_installed"

    # ===== assign_preset_to_users tests =====

    @pytest.mark.asyncio
    async def test_assign_preset_to_users_success(self, controller, mock_preset_manager, mock_admin_user):
        """Test successful preset assignment."""
        mock_preset_manager.assign_preset_to_users.return_value = {
            "preset_id": "test-preset",
            "assigned_count": 2,
            "assignments": [],
        }

        result = await controller.assign_preset_to_users("test-preset", ["user-1", "user-2"], mock_admin_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert "assigned to 2 users" in result.message

    @pytest.mark.asyncio
    async def test_assign_preset_invalid_users(self, controller, mock_preset_manager, mock_admin_user):
        """Test assigning preset with invalid users."""
        mock_preset_manager.assign_preset_to_users.side_effect = InvalidUsersException(["invalid-user"])

        result = await controller.assign_preset_to_users("test-preset", ["invalid-user"], mock_admin_user)

        assert result.success is False
        assert result.error == "invalid_users"

    # ===== unassign_preset_from_user tests =====

    @pytest.mark.asyncio
    async def test_unassign_preset_from_user_success(self, controller, mock_preset_manager, mock_admin_user):
        """Test successful preset unassignment."""
        mock_preset_manager.unassign_preset_from_user.return_value = "Preset unassigned from user"

        result = await controller.unassign_preset_from_user("test-preset", "user-1", mock_admin_user)

        assert isinstance(result, APIResponse)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_unassign_preset_user_not_found(self, controller, mock_preset_manager, mock_admin_user):
        """Test unassigning preset from non-existent user."""
        mock_preset_manager.unassign_preset_from_user.side_effect = UserNotFoundException("non-existent")

        result = await controller.unassign_preset_from_user("test-preset", "non-existent", mock_admin_user)

        assert result.success is False
        assert result.error == "user_not_found"

    @pytest.mark.asyncio
    async def test_unassign_preset_not_assigned(self, controller, mock_preset_manager, mock_admin_user):
        """Test unassigning preset that isn't assigned."""
        mock_preset_manager.unassign_preset_from_user.side_effect = PresetNotAssignedException("test-preset", "user-1")

        result = await controller.unassign_preset_from_user("test-preset", "user-1", mock_admin_user)

        assert result.success is False
        assert result.error == "preset_not_assigned"

    # ===== get_preset_assignments tests =====

    @pytest.mark.asyncio
    async def test_get_preset_assignments_success(self, controller, mock_preset_manager, mock_admin_user):
        """Test successful preset assignments retrieval."""
        mock_preset_manager.get_preset_assignments.return_value = {
            "installed": True,
            "total_assignments": 3,
            "assignments": [],
        }

        result = await controller.get_preset_assignments("test-preset", mock_admin_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data["total_assignments"] == 3

    @pytest.mark.asyncio
    async def test_get_preset_assignments_permission_denied(self, controller, mock_preset_manager, mock_current_user):
        """Test getting assignments without admin permission."""
        mock_preset_manager.get_preset_assignments.side_effect = PermissionDeniedException("get_preset_assignments")

        result = await controller.get_preset_assignments("test-preset", mock_current_user)

        assert result.success is False
        assert result.error == "permission_denied"

    # ===== get_form_overrides / set_form_overrides tests =====

    @pytest.mark.asyncio
    async def test_get_form_overrides_success(self, controller, mock_preset_manager, mock_admin_user):
        mock_preset_manager.get_form_overrides_inventory.return_value = {
            "preset_id": "test-preset",
            "mode": "txt2img",
            "modes": ["txt2img"],
            "fields": [],
        }

        result = await controller.get_form_overrides("test-preset", "txt2img", mock_admin_user)

        assert result.success is True
        assert result.data["mode"] == "txt2img"
        mock_preset_manager.get_form_overrides_inventory.assert_called_once_with(
            "test-preset", "txt2img", mock_admin_user
        )

    @pytest.mark.asyncio
    async def test_get_form_overrides_permission_denied(self, controller, mock_preset_manager, mock_current_user):
        mock_preset_manager.get_form_overrides_inventory.side_effect = PermissionDeniedException(
            "get_form_overrides_inventory"
        )

        result = await controller.get_form_overrides("test-preset", "txt2img", mock_current_user)

        assert result.success is False
        assert result.error == "permission_denied"

    @pytest.mark.asyncio
    async def test_get_form_overrides_mode_not_found(self, controller, mock_preset_manager, mock_admin_user):
        mock_preset_manager.get_form_overrides_inventory.side_effect = ModeNotFoundException(
            "test-preset", "not_a_mode"
        )

        result = await controller.get_form_overrides("test-preset", "not_a_mode", mock_admin_user)

        assert result.success is False
        assert result.error == "mode_not_found"

    @pytest.mark.asyncio
    async def test_set_form_overrides_success(self, controller, mock_preset_manager, mock_admin_user):
        mock_preset_manager.set_form_overrides.return_value = {
            "preset_id": "test-preset",
            "mode": "txt2img",
            "modes": ["txt2img"],
            "fields": [],
        }

        result = await controller.set_form_overrides(
            "test-preset", "txt2img", {"steps": {"editable": False}}, mock_admin_user
        )

        assert result.success is True
        mock_preset_manager.set_form_overrides.assert_called_once_with(
            "test-preset", "txt2img", {"steps": {"editable": False}}, mock_admin_user
        )

    @pytest.mark.asyncio
    async def test_set_form_overrides_permission_denied(self, controller, mock_preset_manager, mock_current_user):
        mock_preset_manager.set_form_overrides.side_effect = PermissionDeniedException("set_form_overrides")

        result = await controller.set_form_overrides(
            "test-preset", "txt2img", {"steps": {"editable": False}}, mock_current_user
        )

        assert result.success is False
        assert result.error == "permission_denied"

    @pytest.mark.asyncio
    async def test_set_form_overrides_invalid(self, controller, mock_preset_manager, mock_admin_user):
        from src.features.presets.exceptions import InvalidFormOverridesException

        mock_preset_manager.set_form_overrides.side_effect = InvalidFormOverridesException(
            "test-preset", "txt2img", ["unknown field 'bogus'"]
        )

        result = await controller.set_form_overrides(
            "test-preset", "txt2img", {"bogus": {"editable": False}}, mock_admin_user
        )

        assert result.success is False
        assert result.error == "invalid_form_overrides"


class TestPresetControllerModelAccessFiltering:
    """`get_preset_models` scopes results to the requesting user's model
    access when a `model_access_policy` was supplied."""

    @pytest.fixture
    def mock_preset_manager(self):
        manager = Mock()
        preset = Mock()
        preset.engine = "native"
        manager.preset_loader.load_preset_by_id = Mock(return_value=preset)
        return manager

    @pytest.fixture
    def mock_backend_registry(self):
        registry = Mock()
        registry.get_backends_for_engine.return_value = []
        return registry

    @pytest.fixture
    def mock_model_access_policy(self):
        return Mock()

    @pytest.fixture
    def controller(self, mock_preset_manager, mock_backend_registry, mock_model_access_policy):
        return PresetController(
            mock_preset_manager, mock_backend_registry,
            model_access_policy=mock_model_access_policy,
        )

    @pytest.fixture
    def admin_user(self):
        user = Mock()
        user.id = "admin-user-id"
        user.account_type = AccountType.ADMIN
        return user

    @pytest.fixture
    def regular_user(self):
        user = Mock()
        user.id = "regular-user-id"
        user.account_type = AccountType.USER
        return user

    @pytest.mark.asyncio
    async def test_admin_is_unrestricted(self, controller, mock_model_access_policy, admin_user):
        mock_model_access_policy.get_allowed_model_ids.return_value = None

        with patch("src.features.models.availability.models_for_engine") as mock_models_for_engine, \
             patch("src.features.models.availability_repository.model_availability_repo") as mock_repo:
            mock_models_for_engine.return_value = []
            mock_repo.any_indexed.return_value = False
            await controller.get_preset_models("test-preset", current_user=admin_user)

        mock_model_access_policy.get_allowed_model_ids.assert_called_once_with(admin_user, all_models=True)
        assert mock_models_for_engine.call_args.kwargs["user_allowed_model_ids"] is None

    @pytest.mark.asyncio
    async def test_regular_user_is_scoped_to_their_assigned_models(
        self, controller, mock_model_access_policy, regular_user,
    ):
        mock_model_access_policy.get_allowed_model_ids.return_value = ["m1"]

        with patch("src.features.models.availability.models_for_engine") as mock_models_for_engine, \
             patch("src.features.models.availability_repository.model_availability_repo") as mock_repo:
            mock_models_for_engine.return_value = []
            mock_repo.any_indexed.return_value = False
            await controller.get_preset_models("test-preset", current_user=regular_user)

        assert mock_models_for_engine.call_args.kwargs["user_allowed_model_ids"] == ["m1"]

    @pytest.mark.asyncio
    async def test_no_policy_wired_skips_filtering(self, mock_preset_manager, mock_backend_registry, regular_user):
        """Backward compatible: a controller built without a policy (e.g. an
        older test) behaves exactly as before - unfiltered."""
        controller_without_policy = PresetController(mock_preset_manager, mock_backend_registry)

        with patch("src.features.models.availability.models_for_engine") as mock_models_for_engine, \
             patch("src.features.models.availability_repository.model_availability_repo") as mock_repo:
            mock_models_for_engine.return_value = []
            mock_repo.any_indexed.return_value = False
            await controller_without_policy.get_preset_models("test-preset", current_user=regular_user)

        assert mock_models_for_engine.call_args.kwargs["user_allowed_model_ids"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
