"""Tests for src.features.forms.operations.options."""
import pytest
import yaml
from unittest.mock import Mock, patch, mock_open

from src.features.forms.operations import options as options_module
from src.platform.plugins.field_types import FieldTypeRegistry
from src.platform.plugins.hooks import HookContext


class TestGetFieldOptions:
    """Tests for `get_field_options` (the `field_registry`/`plugin_registry`
    dispatch, hooks, and model-access scoping)."""

    @pytest.fixture
    def wired_registry(self):
        """The real builtin registration, so `select`/`model`/`checkbox_group`
        dispatch through the real loaders exactly as production does."""
        from src.features.fields.builtin import register_builtin_fields

        registry = FieldTypeRegistry()
        template_processor = Mock()
        register_builtin_fields(registry, template_processor=template_processor)
        return registry, template_processor

    @pytest.fixture
    def plugin_registry(self):
        """Mock PluginRegistry with default (unblocked, pass-through) hook behavior."""
        mock = Mock()

        def execute_hook_side_effect(hook_name, initial_data=None):
            context = HookContext(hook_name=hook_name, plugin_id="system", data=initial_data or {})
            return context, []
        mock.execute_hook.side_effect = execute_hook_side_effect
        return mock

    def test_get_field_options_select_success(self, wired_registry, plugin_registry):
        registry, _ = wired_registry
        config = {
            "options": [
                {"label": "Option 1", "value": "opt1", "example": "Example 1"},
                {"label": "Option 2", "value": "opt2"}
            ]
        }

        result = options_module.get_field_options(registry, plugin_registry, "select", config)

        assert len(result) == 2
        assert result[0]["label"] == "Option 1"
        assert result[0]["value"] == "opt1"
        assert result[0]["example"] == "Example 1"
        assert result[1]["label"] == "Option 2"
        assert result[1]["value"] == "opt2"

    def test_get_field_options_checkbox_group_success(self, wired_registry, plugin_registry):
        registry, _ = wired_registry
        config = {
            "options": [
                {"label": "Check 1", "value": "check1", "checked": True},
                {"label": "Check 2", "value": "check2", "checked": False}
            ]
        }

        result = options_module.get_field_options(registry, plugin_registry, "checkbox_group", config)

        assert len(result) == 2
        assert result[0]["label"] == "Check 1"
        assert result[0]["value"] == "check1"
        assert result[0]["checked"] is True
        assert result[1]["checked"] is False

    def test_get_field_options_model_dispatches_via_registry(self, wired_registry, plugin_registry):
        registry, _ = wired_registry
        with patch(
            "src.features.models.repository.model_repo.get_all",
            return_value=[]
        ) as mock_get_all:
            result = options_module.get_field_options(registry, plugin_registry, "model", {"model_type": "checkpoint"})

        assert result == []
        mock_get_all.assert_called_once_with(
            model_type="checkpoint", include_providers=True, limit=None, allowed_model_ids=None
        )

    def test_get_field_options_model_scoped_to_admin_is_unrestricted(self, wired_registry, plugin_registry):
        from src.platform.security.user import AccountType

        registry, _ = wired_registry
        admin = Mock()
        admin.account_type = AccountType.ADMIN

        with patch("src.features.models.repository.model_repo.get_all", return_value=[]) as mock_get_all:
            options_module.get_field_options(registry, plugin_registry, "model", {"model_type": "checkpoint"}, admin)

        assert mock_get_all.call_args.kwargs["allowed_model_ids"] is None

    def test_get_field_options_model_scoped_to_regular_user_is_strict(self, wired_registry, plugin_registry):
        from src.platform.security.user import AccountType

        registry, _ = wired_registry
        user = Mock()
        user.id = "user-1"
        user.account_type = AccountType.USER

        with patch(
            "src.features.models.repository.model_repo.get_available_model_ids_for_user",
            return_value=["m1"],
        ), patch("src.features.models.repository.model_repo.get_all", return_value=[]) as mock_get_all:
            options_module.get_field_options(registry, plugin_registry, "model", {"model_type": "checkpoint"}, user)

        assert mock_get_all.call_args.kwargs["allowed_model_ids"] == ["m1"]

    def test_get_field_options_model_without_user_context_is_unfiltered(self, wired_registry, plugin_registry):
        registry, _ = wired_registry
        with patch("src.features.models.repository.model_repo.get_all", return_value=[]) as mock_get_all:
            options_module.get_field_options(registry, plugin_registry, "model", {"model_type": "checkpoint"})

        assert mock_get_all.call_args.kwargs["allowed_model_ids"] is None

    def test_get_field_options_non_model_type_ignores_current_user(self, wired_registry, plugin_registry):
        from src.platform.security.user import AccountType

        registry, _ = wired_registry
        user = Mock()
        user.account_type = AccountType.USER

        result = options_module.get_field_options(
            registry, plugin_registry, "select", {"options": [{"label": "A", "value": "a"}]}, user
        )

        assert result == [{"label": "A", "value": "a", "example": None}]

    def test_get_field_options_unsupported_type(self, wired_registry, plugin_registry):
        registry, _ = wired_registry
        with pytest.raises(ValueError) as exc_info:
            options_module.get_field_options(registry, plugin_registry, "unsupported_type", {})

        assert "unsupported_type" in str(exc_info.value)
        assert "does not support dynamic options" in str(exc_info.value)

    def test_get_field_options_hook_blocked(self, wired_registry, plugin_registry):
        registry, _ = wired_registry

        def blocked_hook(hook_name, initial_data=None):
            context = HookContext(
                hook_name=hook_name, plugin_id="system",
                data={"blocked": True, "block_reason": "Test block reason"},
            )
            return context, []
        plugin_registry.execute_hook.side_effect = blocked_hook

        with pytest.raises(ValueError) as exc_info:
            options_module.get_field_options(registry, plugin_registry, "select", {"options": []})

        assert "Test block reason" in str(exc_info.value)


class TestGetSelectOptions:

    def test_static_only(self):
        config = {
            "options": [
                {"label": "Static 1", "value": "s1"},
                {"label": "Static 2", "value": "s2", "example": "Example"}
            ]
        }

        result = options_module.get_select_options(Mock(), config)

        assert len(result) == 2
        assert result[0]["label"] == "Static 1"
        assert result[0]["value"] == "s1"
        assert result[1]["example"] == "Example"

    def test_file_yaml(self):
        config = {"file": {"path": "/test/options.yml"}}
        file_content = [
            {"label": "File Option 1", "value": "f1"},
            {"label": "File Option 2", "value": "f2", "example": "File example"}
        ]
        template_processor = Mock()
        template_processor.process_template.return_value = "/processed/options.yml"

        with patch("builtins.open", mock_open(read_data=yaml.dump(file_content))):
            with patch("yaml.safe_load", return_value=file_content):
                result = options_module.get_select_options(template_processor, config)

        assert len(result) == 2
        assert result[0]["label"] == "File Option 1"
        assert result[1]["example"] == "File example"
        template_processor.process_template.assert_called_once_with("/test/options.yml", {})

    def test_file_not_found(self):
        config = {"file": {"path": "/test/nonexistent.yml"}}
        template_processor = Mock()
        template_processor.process_template.return_value = "/processed/nonexistent.yml"

        with patch("builtins.open", side_effect=FileNotFoundError()):
            result = options_module.get_select_options(template_processor, config)

        assert len(result) == 0

    def test_invalid_yaml(self):
        config = {"file": {"path": "/test/invalid.yml"}}
        template_processor = Mock()
        template_processor.process_template.return_value = "/processed/invalid.yml"

        with patch("builtins.open", mock_open(read_data="invalid: yaml: content:")):
            with patch("yaml.safe_load", side_effect=yaml.YAMLError()):
                result = options_module.get_select_options(template_processor, config)

        assert len(result) == 0

    def test_filesystem_scan(self):
        config = {"files": {"in": "/test/models"}}
        template_processor = Mock()
        template_processor.process_template.return_value = "/processed/models"
        mock_files = [
            "/processed/models/model1.safetensors",
            "/processed/models/model2.safetensors"
        ]

        with patch("glob.glob", return_value=mock_files):
            result = options_module.get_select_options(template_processor, config)

        assert len(result) == 2
        assert result[0]["label"] == "model1"
        assert result[0]["value"] == "model1.safetensors"
        assert result[1]["label"] == "model2"
        assert result[1]["value"] == "model2.safetensors"

    def test_filesystem_exception(self):
        config = {"files": {"in": "/test/models"}}
        template_processor = Mock()
        template_processor.process_template.return_value = "/processed/models"

        with patch("glob.glob", side_effect=Exception("Directory access error")):
            result = options_module.get_select_options(template_processor, config)

        assert len(result) == 0

    def test_combined(self):
        config = {
            "options": [{"label": "Static Option", "value": "static"}],
            "file": {"path": "/test/options.yml"},
            "files": {"in": "/test/models"},
        }
        file_content = [{"label": "File Option", "value": "file"}]
        mock_files = ["/processed/models/model.safetensors"]
        template_processor = Mock()
        template_processor.process_template.side_effect = ["/processed/options.yml", "/processed/models"]

        with patch("builtins.open", mock_open(read_data=yaml.dump(file_content))):
            with patch("yaml.safe_load", return_value=file_content):
                with patch("glob.glob", return_value=mock_files):
                    result = options_module.get_select_options(template_processor, config)

        assert len(result) == 3
        assert result[0]["label"] == "Static Option"
        assert result[1]["label"] == "File Option"
        assert result[2]["label"] == "model"


class TestGetCheckboxOptions:

    def test_success(self):
        config = {
            "options": [
                {"label": "Check 1", "value": "c1", "checked": True},
                {"label": "Check 2", "value": "c2", "checked": False},
                {"label": "Check 3", "value": "c3"}
            ]
        }

        result = options_module.get_checkbox_options(config)

        assert len(result) == 3
        assert result[0]["label"] == "Check 1"
        assert result[0]["checked"] is True
        assert result[1]["checked"] is False
        assert result[2]["checked"] is False  # Default value
