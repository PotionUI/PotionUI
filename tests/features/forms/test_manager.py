"""Tests for FormManager."""
import pytest
import yaml
from unittest.mock import Mock, MagicMock, patch, mock_open

from src.features.forms import FormManager
from src.platform.templating import TemplateProcessor
from src.platform.settings.settings import SettingsManager
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import HookContext, execute_hook
from src.features.forms.hooks import FORM_HOOKS


class TestFormManager:
    """Comprehensive tests for FormManager."""

    @pytest.fixture
    def mock_template_processor(self):
        """Mock TemplateProcessor."""
        mock = Mock(spec=TemplateProcessor)
        mock.process_template.return_value = "processed_value"
        return mock

    @pytest.fixture
    def mock_model_manager(self):
        """Mock ModelManager."""
        mock = Mock()
        mock.get_models_by_type = Mock(return_value=[])
        return mock

    @pytest.fixture
    def mock_settings_manager(self):
        """Mock SettingsManager."""
        return Mock(spec=SettingsManager)

    @pytest.fixture
    def mock_plugin_registry(self):
        """Mock PluginRegistry with default hook behavior."""
        mock = Mock(spec=PluginRegistry)
        # Default: return context with data passed in and empty results
        def execute_hook_side_effect(hook_name, initial_data=None):
            context = HookContext(
                hook_name=hook_name,
                plugin_id="system",
                data=initial_data or {}
            )
            return context, []
        mock.execute_hook.side_effect = execute_hook_side_effect
        return mock

    @pytest.fixture
    def manager(self, mock_template_processor, mock_model_manager,
                mock_settings_manager, mock_plugin_registry):
        """Create FormManager instance with mocked dependencies."""
        return FormManager(
            mock_template_processor,
            mock_model_manager,
            mock_settings_manager,
            mock_plugin_registry
        )

    # ========== Test get_field_options ==========

    def test_get_field_options_select_success(self, manager):
        """Test successful get_field_options for select field with static options."""
        config = {
            "options": [
                {"label": "Option 1", "value": "opt1", "example": "Example 1"},
                {"label": "Option 2", "value": "opt2"}
            ]
        }

        result = manager.get_field_options("select", config)

        assert len(result) == 2
        assert result[0]["label"] == "Option 1"
        assert result[0]["value"] == "opt1"
        assert result[0]["example"] == "Example 1"
        assert result[1]["label"] == "Option 2"
        assert result[1]["value"] == "opt2"

    def test_get_field_options_checkbox_group_success(self, manager):
        """Test successful get_field_options for checkbox_group field."""
        config = {
            "options": [
                {"label": "Check 1", "value": "check1", "checked": True},
                {"label": "Check 2", "value": "check2", "checked": False}
            ]
        }

        result = manager.get_field_options("checkbox_group", config)

        assert len(result) == 2
        assert result[0]["label"] == "Check 1"
        assert result[0]["value"] == "check1"
        assert result[0]["checked"] is True
        assert result[1]["checked"] is False

    def test_get_field_options_model_dispatches_via_registry(self, manager):
        """Test get_field_options('model', ...) dispatches through the field
        type registry to the database-backed model loader (not the deleted
        `model_select`/ModelManager path)."""
        with patch(
            "src.features.models.repository.model_repo.get_all",
            return_value=[]
        ) as mock_get_all:
            result = manager.get_field_options("model", {"model_type": "checkpoint"})

        assert result == []
        mock_get_all.assert_called_once_with(
            model_type="checkpoint", include_providers=True, limit=None, allowed_model_ids=None
        )

    def test_get_field_options_model_scoped_to_admin_is_unrestricted(self, manager):
        """An admin gets allowed_model_ids=None (unrestricted)."""
        from src.platform.security.user import AccountType

        admin = Mock()
        admin.account_type = AccountType.ADMIN

        with patch("src.features.models.repository.model_repo.get_all", return_value=[]) as mock_get_all:
            manager.get_field_options("model", {"model_type": "checkpoint"}, admin)

        assert mock_get_all.call_args.kwargs["allowed_model_ids"] is None

    def test_get_field_options_model_scoped_to_regular_user_is_strict(self, manager):
        """A non-admin's options are restricted to their assigned models,
        even when they have none (STRICT: empty list, not unfiltered)."""
        from src.platform.security.user import AccountType

        user = Mock()
        user.id = "user-1"
        user.account_type = AccountType.USER

        with patch(
            "src.features.models.repository.model_repo.get_available_model_ids_for_user",
            return_value=["m1"],
        ), patch("src.features.models.repository.model_repo.get_all", return_value=[]) as mock_get_all:
            manager.get_field_options("model", {"model_type": "checkpoint"}, user)

        assert mock_get_all.call_args.kwargs["allowed_model_ids"] == ["m1"]

    def test_get_field_options_model_without_user_context_is_unfiltered(self, manager):
        """No `current_user` (e.g. a caller with no request context) skips
        scoping entirely - matches the earlier behavior."""
        with patch("src.features.models.repository.model_repo.get_all", return_value=[]) as mock_get_all:
            manager.get_field_options("model", {"model_type": "checkpoint"})

        assert mock_get_all.call_args.kwargs["allowed_model_ids"] is None

    def test_get_field_options_non_model_type_ignores_current_user(self, manager):
        """Scoping only applies to `model`/`models` field types - a `select`
        field must never be routed through the model-access machinery."""
        from src.platform.security.user import AccountType

        user = Mock()
        user.account_type = AccountType.USER

        result = manager.get_field_options("select", {"options": [{"label": "A", "value": "a"}]}, user)

        assert result == [{"label": "A", "value": "a", "example": None}]

    def test_get_field_options_unsupported_type(self, manager):
        """Test get_field_options with unsupported field type."""
        with pytest.raises(ValueError) as exc_info:
            manager.get_field_options("unsupported_type", {})

        assert "unsupported_type" in str(exc_info.value)
        assert "does not support dynamic options" in str(exc_info.value)

    def test_get_field_options_hook_blocked(self, manager, mock_plugin_registry):
        """Test get_field_options when blocked by plugin hook."""
        def blocked_hook(hook_name, initial_data=None):
            context = HookContext(
                hook_name=hook_name,
                plugin_id="system",
                data={"blocked": True, "block_reason": "Test block reason"}
            )
            return context, []
        mock_plugin_registry.execute_hook.side_effect = blocked_hook

        with pytest.raises(ValueError) as exc_info:
            manager.get_field_options("select", {"options": []})

        assert "Test block reason" in str(exc_info.value)

    # ========== Test _get_select_options ==========

    def test_get_select_options_static_only(self, manager):
        """Test _get_select_options with static options only."""
        config = {
            "options": [
                {"label": "Static 1", "value": "s1"},
                {"label": "Static 2", "value": "s2", "example": "Example"}
            ]
        }

        result = manager._get_select_options(config)

        assert len(result) == 2
        assert result[0]["label"] == "Static 1"
        assert result[0]["value"] == "s1"
        assert result[1]["example"] == "Example"

    def test_get_select_options_file_yaml(self, manager, mock_template_processor):
        """Test _get_select_options with YAML file."""
        config = {
            "file": {
                "path": "/test/options.yml"
            }
        }
        file_content = [
            {"label": "File Option 1", "value": "f1"},
            {"label": "File Option 2", "value": "f2", "example": "File example"}
        ]
        mock_template_processor.process_template.return_value = "/processed/options.yml"

        with patch("builtins.open", mock_open(read_data=yaml.dump(file_content))):
            with patch("yaml.safe_load", return_value=file_content):
                result = manager._get_select_options(config)

        assert len(result) == 2
        assert result[0]["label"] == "File Option 1"
        assert result[1]["example"] == "File example"
        mock_template_processor.process_template.assert_called_once_with("/test/options.yml", {})

    def test_get_select_options_file_not_found(self, manager, mock_template_processor):
        """Test _get_select_options with non-existent file."""
        config = {
            "file": {
                "path": "/test/nonexistent.yml"
            }
        }
        mock_template_processor.process_template.return_value = "/processed/nonexistent.yml"

        with patch("builtins.open", side_effect=FileNotFoundError()):
            result = manager._get_select_options(config)

        assert len(result) == 0

    def test_get_select_options_invalid_yaml(self, manager, mock_template_processor):
        """Test _get_select_options with invalid YAML file."""
        config = {
            "file": {
                "path": "/test/invalid.yml"
            }
        }
        mock_template_processor.process_template.return_value = "/processed/invalid.yml"

        with patch("builtins.open", mock_open(read_data="invalid: yaml: content:")):
            with patch("yaml.safe_load", side_effect=yaml.YAMLError()):
                result = manager._get_select_options(config)

        assert len(result) == 0

    def test_get_select_options_filesystem_scan(self, manager, mock_template_processor):
        """Test _get_select_options with filesystem scanning."""
        config = {
            "files": {
                "in": "/test/models"
            }
        }
        mock_template_processor.process_template.return_value = "/processed/models"
        mock_files = [
            "/processed/models/model1.safetensors",
            "/processed/models/model2.safetensors"
        ]

        with patch("glob.glob", return_value=mock_files):
            result = manager._get_select_options(config)

        assert len(result) == 2
        assert result[0]["label"] == "model1"
        assert result[0]["value"] == "model1.safetensors"
        assert result[1]["label"] == "model2"
        assert result[1]["value"] == "model2.safetensors"

    def test_get_select_options_filesystem_exception(self, manager, mock_template_processor):
        """Test _get_select_options with filesystem scanning exception."""
        config = {
            "files": {
                "in": "/test/models"
            }
        }
        mock_template_processor.process_template.return_value = "/processed/models"

        with patch("glob.glob", side_effect=Exception("Directory access error")):
            result = manager._get_select_options(config)

        assert len(result) == 0

    def test_get_select_options_combined(self, manager, mock_template_processor):
        """Test _get_select_options with static + file + filesystem options."""
        config = {
            "options": [
                {"label": "Static Option", "value": "static"}
            ],
            "file": {
                "path": "/test/options.yml"
            },
            "files": {
                "in": "/test/models"
            }
        }
        file_content = [{"label": "File Option", "value": "file"}]
        mock_files = ["/processed/models/model.safetensors"]
        mock_template_processor.process_template.side_effect = ["/processed/options.yml", "/processed/models"]

        with patch("builtins.open", mock_open(read_data=yaml.dump(file_content))):
            with patch("yaml.safe_load", return_value=file_content):
                with patch("glob.glob", return_value=mock_files):
                    result = manager._get_select_options(config)

        assert len(result) == 3
        assert result[0]["label"] == "Static Option"
        assert result[1]["label"] == "File Option"
        assert result[2]["label"] == "model"

    # ========== Test _get_checkbox_options ==========

    def test_get_checkbox_options_success(self, manager):
        """Test _get_checkbox_options successful execution."""
        config = {
            "options": [
                {"label": "Check 1", "value": "c1", "checked": True},
                {"label": "Check 2", "value": "c2", "checked": False},
                {"label": "Check 3", "value": "c3"}
            ]
        }

        result = manager._get_checkbox_options(config)

        assert len(result) == 3
        assert result[0]["label"] == "Check 1"
        assert result[0]["checked"] is True
        assert result[1]["checked"] is False
        assert result[2]["checked"] is False  # Default value

    # ========== Test validate_form_data ==========

    def test_validate_form_data_success(self, manager):
        """Test validate_form_data with valid data."""
        form_schema = {
            "required": ["prompt", "steps"],
            "properties": {
                "prompt": {"type": "string"},
                "steps": {"type": "integer", "minimum": 1, "maximum": 100},
                "cfg": {"type": "number", "minimum": 0.1, "maximum": 20.0}
            }
        }
        form_data = {
            "prompt": "A beautiful landscape",
            "steps": 20,
            "cfg": 7.5
        }

        result = manager.validate_form_data(form_schema, form_data)

        assert result["valid"] is True

    def test_validate_form_data_missing_required(self, manager):
        """Test validate_form_data with missing required fields."""
        form_schema = {
            "required": ["prompt", "steps"],
            "properties": {
                "prompt": {"type": "string"},
                "steps": {"type": "integer"}
            }
        }
        form_data = {
            "prompt": "A beautiful landscape"
        }

        with pytest.raises(ValueError) as exc_info:
            manager.validate_form_data(form_schema, form_data)

        assert "steps" in str(exc_info.value)
        assert "required" in str(exc_info.value)

    def test_validate_form_data_invalid_type(self, manager):
        """Test validate_form_data with invalid field types."""
        form_schema = {
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string"}
            }
        }
        form_data = {
            "prompt": 123  # Should be string
        }

        with pytest.raises(ValueError) as exc_info:
            manager.validate_form_data(form_schema, form_data)

        assert "prompt" in str(exc_info.value)
        assert "invalid type" in str(exc_info.value)

    def test_validate_form_data_range_validation(self, manager):
        """Test validate_form_data with out-of-range values."""
        form_schema = {
            "required": ["steps"],
            "properties": {
                "steps": {"type": "integer", "minimum": 1, "maximum": 100}
            }
        }
        form_data = {
            "steps": 150  # Maximum is 100
        }

        with pytest.raises(ValueError) as exc_info:
            manager.validate_form_data(form_schema, form_data)

        assert "steps" in str(exc_info.value)
        assert "at most 100" in str(exc_info.value)

    def test_validate_form_data_hook_blocked(self, manager, mock_plugin_registry):
        """Test validate_form_data when blocked by plugin hook."""
        def blocked_hook(hook_name, initial_data=None):
            context = HookContext(
                hook_name=hook_name,
                plugin_id="system",
                data={"blocked": True, "block_reason": "Validation blocked by plugin"}
            )
            return context, []
        mock_plugin_registry.execute_hook.side_effect = blocked_hook

        with pytest.raises(ValueError) as exc_info:
            manager.validate_form_data({}, {})

        assert "Validation blocked by plugin" in str(exc_info.value)

    # ========== Test _validate_field_type ==========

    def test_validate_field_type_string(self, manager):
        """Test _validate_field_type for string type."""
        assert manager._validate_field_type("hello", "string") is True
        assert manager._validate_field_type(123, "string") is False

    def test_validate_field_type_number(self, manager):
        """Test _validate_field_type for number type."""
        assert manager._validate_field_type(123, "number") is True
        assert manager._validate_field_type(123.45, "number") is True
        assert manager._validate_field_type("123", "number") is False

    def test_validate_field_type_integer(self, manager):
        """Test _validate_field_type for integer type."""
        assert manager._validate_field_type(123, "integer") is True
        assert manager._validate_field_type(123.45, "integer") is False

    def test_validate_field_type_boolean(self, manager):
        """Test _validate_field_type for boolean type."""
        assert manager._validate_field_type(True, "boolean") is True
        assert manager._validate_field_type(False, "boolean") is True
        assert manager._validate_field_type("true", "boolean") is False

    def test_validate_field_type_array(self, manager):
        """Test _validate_field_type for array type."""
        assert manager._validate_field_type([1, 2, 3], "array") is True
        assert manager._validate_field_type([], "array") is True
        assert manager._validate_field_type("not array", "array") is False

    def test_validate_field_type_object(self, manager):
        """Test _validate_field_type for object type."""
        assert manager._validate_field_type({"key": "value"}, "object") is True
        assert manager._validate_field_type({}, "object") is True
        assert manager._validate_field_type([1, 2, 3], "object") is False

    def test_validate_field_type_unknown(self, manager):
        """Test _validate_field_type for unknown type."""
        assert manager._validate_field_type("anything", "unknown_type") is True

    # ========== Test get_form_defaults ==========

    def test_get_form_defaults_success(self, manager):
        """Test get_form_defaults successful execution."""
        result = manager.get_form_defaults("test-preset-123")

        assert result == {}  # Currently returns empty defaults

    # ========== Test initialization ==========

    def test_manager_initialization(self, mock_template_processor, mock_model_manager,
                                    mock_settings_manager, mock_plugin_registry):
        """Test manager initialization."""
        manager = FormManager(
            mock_template_processor,
            mock_model_manager,
            mock_settings_manager,
            mock_plugin_registry
        )

        assert manager.template_processor == mock_template_processor
        assert manager.model_manager == mock_model_manager
        assert manager.settings_manager == mock_settings_manager
        assert manager.plugins == mock_plugin_registry

    # ========== Test execute_hook ==========

    def test_execute_hook_returns_data_and_blocked_status(self, manager, mock_plugin_registry):
        """Test execute_hook returns context data and blocked status."""

        def custom_hook(hook_name, initial_data=None):
            context = HookContext(
                hook_name=hook_name,
                plugin_id="system",
                data={"custom_key": "custom_value", "blocked": False}
            )
            return context, []
        mock_plugin_registry.execute_hook.side_effect = custom_hook

        data, blocked = execute_hook(manager.plugins, FORM_HOOKS.before_get_options, {"test": "data"})

        assert data["custom_key"] == "custom_value"
        assert blocked is False

    def test_execute_hook_detects_blocked(self, manager, mock_plugin_registry):
        """Test execute_hook correctly detects blocked status."""

        def blocked_hook(hook_name, initial_data=None):
            context = HookContext(
                hook_name=hook_name,
                plugin_id="system",
                data={"blocked": True, "block_reason": "Blocked by test"}
            )
            return context, []
        mock_plugin_registry.execute_hook.side_effect = blocked_hook

        data, blocked = execute_hook(manager.plugins, FORM_HOOKS.before_validate, {})

        assert blocked is True
        assert data["block_reason"] == "Blocked by test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
