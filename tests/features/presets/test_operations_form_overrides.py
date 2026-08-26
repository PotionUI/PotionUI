"""Tests for the presets operations module's form-overrides operations:
get_form_overrides_inventory / set_form_overrides."""

import pytest
from unittest.mock import Mock, patch

from src.features.presets import operations
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.exceptions import (
    PresetNotFoundException,
    ModeNotFoundException,
    NoModesAvailableException,
    PresetNotInstalledException,
    PermissionDeniedException,
    InvalidFormOverridesException,
)
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate
from src.platform.security.user import User, AccountType


def _field(name, type_="string", default=None, configuration=None):
    return FieldTemplate(type=type_, name=name, default=default, configuration=configuration)


@pytest.fixture
def mock_file_repo():
    return Mock()


@pytest.fixture
def mock_db_repo():
    return Mock()


@pytest.fixture
def collaborators(mock_file_repo, mock_db_repo):
    with patch('src.features.presets.collaborators.PresetFormSerializer'):
        return PresetCollaborators(
            preset_loader=Mock(),
            preset_processor=Mock(),
            template_processor=Mock(),
            file_repo=mock_file_repo,
            db_repo=mock_db_repo,
            user_repo=Mock(),
            group_repo=Mock(),
            pipeline_builder=Mock(),
            pipe_catalog=Mock(),
            plugins=Mock(),
            settings_manager=Mock(),
        )


@pytest.fixture
def admin_user():
    user = Mock(spec=User)
    user.id = "admin-user-id"
    user.account_type = AccountType.ADMIN
    return user


@pytest.fixture
def regular_user():
    user = Mock(spec=User)
    user.id = "regular-user-id"
    user.account_type = AccountType.USER
    return user


@pytest.fixture
def preset_template():
    forms = [FormTemplate(
        name="custom",
        fields=[_field("steps", "slider", default=20, configuration={"min": 1, "max": 100}), _field("checkpoint")],
        default=True,
    )]
    return PresetTemplate(
        id="test-preset",
        name="Test Preset",
        version="1.0.0",
        path="/presets/test-preset",
        modes={"txt2img": ModeTemplate(forms=forms, pipes=[])},
    )


class TestGetFormOverridesInventory:
    def test_admin_only(self, collaborators, regular_user):
        with pytest.raises(PermissionDeniedException):
            operations.get_form_overrides_inventory(collaborators, "test-preset", "txt2img", regular_user)

    def test_preset_not_found(self, collaborators, mock_file_repo, admin_user):
        mock_file_repo.find_preset_by_id.return_value = None
        with pytest.raises(PresetNotFoundException):
            operations.get_form_overrides_inventory(collaborators, "test-preset", "txt2img", admin_user)

    def test_no_modes(self, collaborators, mock_file_repo, admin_user):
        template = Mock()
        template.modes = {}
        mock_file_repo.find_preset_by_id.return_value = template
        with pytest.raises(NoModesAvailableException):
            operations.get_form_overrides_inventory(collaborators, "test-preset", None, admin_user)

    def test_unknown_mode(self, collaborators, mock_file_repo, preset_template, admin_user):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        with pytest.raises(ModeNotFoundException):
            operations.get_form_overrides_inventory(collaborators, "test-preset", "img2img", admin_user)

    def test_mode_defaults_to_first_when_omitted(self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.get_preset_form_overrides.return_value = {}
        result = operations.get_form_overrides_inventory(collaborators, "test-preset", None, admin_user)
        assert result["mode"] == "txt2img"

    def test_returns_fields_and_modes(self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.get_preset_form_overrides.return_value = {"txt2img": {"steps": {"editable": False}}}

        result = operations.get_form_overrides_inventory(collaborators, "test-preset", "txt2img", admin_user)

        assert result["preset_id"] == "test-preset"
        assert result["mode"] == "txt2img"
        assert result["modes"] == ["txt2img"]
        by_name = {f["name"]: f for f in result["fields"]}
        assert by_name["steps"]["override"] == {"editable": False}
        assert by_name["checkpoint"]["override"] is None


class TestSetFormOverrides:
    def test_admin_only(self, collaborators, regular_user):
        with pytest.raises(PermissionDeniedException):
            operations.set_form_overrides(collaborators, "test-preset", "txt2img", {}, regular_user)

    def test_preset_not_found(self, collaborators, mock_file_repo, admin_user):
        mock_file_repo.find_preset_by_id.return_value = None
        with pytest.raises(PresetNotFoundException):
            operations.set_form_overrides(collaborators, "test-preset", "txt2img", {}, admin_user)

    def test_unknown_mode(self, collaborators, mock_file_repo, preset_template, admin_user):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        with pytest.raises(ModeNotFoundException):
            operations.set_form_overrides(collaborators, "test-preset", "img2img", {}, admin_user)

    def test_not_installed(self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.is_preset_installed.return_value = False
        with pytest.raises(PresetNotInstalledException):
            operations.set_form_overrides(collaborators, "test-preset", "txt2img", {"steps": {"editable": False}}, admin_user)

    def test_unknown_field_rejected(self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.is_preset_installed.return_value = True
        with pytest.raises(InvalidFormOverridesException):
            operations.set_form_overrides(collaborators, "test-preset", "txt2img", {"nonexistent": {"editable": False}}, admin_user)

    def test_sets_and_returns_inventory(self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.is_preset_installed.return_value = True
        mock_db_repo.get_preset_form_overrides.return_value = {}

        result = operations.set_form_overrides(collaborators, 
            "test-preset", "txt2img", {"steps": {"default": 30, "editable": False}}, admin_user,
        )

        mock_db_repo.set_preset_form_overrides.assert_called_once()
        call_args = mock_db_repo.set_preset_form_overrides.call_args[0]
        assert call_args[0] == "test-preset"
        assert call_args[1] == {"txt2img": {"steps": {"default": 30, "editable": False}}}
        by_name = {f["name"]: f for f in result["fields"]}
        assert by_name["steps"]["override"] == {"default": 30, "editable": False}

    def test_merges_with_existing_overrides_for_other_fields(
        self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user,
    ):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.is_preset_installed.return_value = True
        mock_db_repo.get_preset_form_overrides.return_value = {
            "txt2img": {"checkpoint": {"visible": False}},
        }

        operations.set_form_overrides(collaborators, "test-preset", "txt2img", {"steps": {"editable": False}}, admin_user)

        stored = mock_db_repo.set_preset_form_overrides.call_args[0][1]
        assert stored["txt2img"]["checkpoint"] == {"visible": False}
        assert stored["txt2img"]["steps"] == {"editable": False}

    def test_empty_object_clears_a_field_override(
        self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user,
    ):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.is_preset_installed.return_value = True
        mock_db_repo.get_preset_form_overrides.return_value = {
            "txt2img": {"steps": {"editable": False}, "checkpoint": {"visible": False}},
        }

        operations.set_form_overrides(collaborators, "test-preset", "txt2img", {"steps": {}}, admin_user)

        stored = mock_db_repo.set_preset_form_overrides.call_args[0][1]
        assert "steps" not in stored["txt2img"]
        assert stored["txt2img"]["checkpoint"] == {"visible": False}

    def test_null_clears_a_field_override(
        self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user,
    ):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.is_preset_installed.return_value = True
        mock_db_repo.get_preset_form_overrides.return_value = {
            "txt2img": {"steps": {"editable": False}},
        }

        operations.set_form_overrides(collaborators, "test-preset", "txt2img", {"steps": None}, admin_user)

        stored = mock_db_repo.set_preset_form_overrides.call_args[0][1]
        assert "steps" not in stored.get("txt2img", {})

    def test_clearing_the_last_override_drops_the_mode_key(
        self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user,
    ):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.is_preset_installed.return_value = True
        mock_db_repo.get_preset_form_overrides.return_value = {
            "txt2img": {"steps": {"editable": False}},
        }

        operations.set_form_overrides(collaborators, "test-preset", "txt2img", {"steps": {}}, admin_user)

        stored = mock_db_repo.set_preset_form_overrides.call_args[0][1]
        assert "txt2img" not in stored

    def test_clearing_unknown_field_is_not_an_error(
        self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user,
    ):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.is_preset_installed.return_value = True
        mock_db_repo.get_preset_form_overrides.return_value = {}

        # Should not raise even though 'stale_field' isn't in the inventory.
        operations.set_form_overrides(collaborators, "test-preset", "txt2img", {"stale_field": None}, admin_user)

    def test_mode_defaults_to_first_when_omitted(
        self, collaborators, mock_file_repo, mock_db_repo, preset_template, admin_user,
    ):
        mock_file_repo.find_preset_by_id.return_value = preset_template
        mock_db_repo.is_preset_installed.return_value = True
        mock_db_repo.get_preset_form_overrides.return_value = {}

        result = operations.set_form_overrides(collaborators, "test-preset", None, {"steps": {"editable": False}}, admin_user)

        assert result["mode"] == "txt2img"
