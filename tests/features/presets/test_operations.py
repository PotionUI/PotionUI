"""Tests for the presets operations module."""

import pytest
from unittest.mock import Mock, MagicMock, patch

from src.features.presets import operations
from src.features.presets.collaborators import PresetCollaborators
from src.features.generation.pipeline_builder import PipelineBuilder, BuiltPipeline
from src.pipelines.graph import PipelineGraph
from src.features.forms.exceptions import FormNotFoundException
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
    InvalidModeDataException,
)
from src.features.presets.templates import ModeTemplate
from src.platform.security.user import User, AccountType


class TestPresetOperationsQuery:
    """Tests for query operations in src.features.presets.operations."""

    @pytest.fixture
    def mock_preset_loader(self):
        """Mock PresetTemplateLoader."""
        return Mock()

    @pytest.fixture
    def mock_preset_processor(self):
        """Mock PresetProcessor."""
        return Mock()

    @pytest.fixture
    def mock_template_processor(self):
        """Mock TemplateProcessor."""
        return Mock()

    @pytest.fixture
    def mock_file_repo(self):
        """Mock FilePresetRepository."""
        return Mock()

    @pytest.fixture
    def mock_db_repo(self):
        """Mock DatabasePresetRepository."""
        return Mock()

    @pytest.fixture
    def mock_user_repo(self):
        """Mock UserRepository."""
        return Mock()

    @pytest.fixture
    def mock_pipeline_builder(self):
        """Mock PipelineBuilder."""
        return Mock(spec=PipelineBuilder)

    @pytest.fixture
    def mock_pipe_catalog(self):
        """Mock PipeCatalog."""
        return Mock()

    @pytest.fixture
    def mock_plugin_registry(self):
        """Mock PluginRegistry."""
        mock = Mock()
        # Default to no hooks blocking
        mock.execute_hook.return_value = (Mock(data={"blocked": False}), [])
        return mock

    @pytest.fixture
    def collaborators(
        self,
        mock_preset_loader,
        mock_preset_processor,
        mock_template_processor,
        mock_file_repo,
        mock_db_repo,
        mock_user_repo,
        mock_pipeline_builder,
        mock_pipe_catalog,
        mock_plugin_registry,
    ):
        """Build a PresetCollaborators bundle with mocks."""
        with patch('src.features.presets.collaborators.PresetFormSerializer'):
            return PresetCollaborators(
                preset_loader=mock_preset_loader,
                preset_processor=mock_preset_processor,
                template_processor=mock_template_processor,
                file_repo=mock_file_repo,
                db_repo=mock_db_repo,
                user_repo=mock_user_repo,
                group_repo=Mock(),
                pipeline_builder=mock_pipeline_builder,
                pipe_catalog=mock_pipe_catalog,
                plugins=mock_plugin_registry,
                settings=Mock(),
            )

    @pytest.fixture
    def admin_user(self):
        """Create admin user mock."""
        user = Mock(spec=User)
        user.id = "admin-user-id"
        user.account_type = AccountType.ADMIN
        return user

    @pytest.fixture
    def regular_user(self):
        """Create regular user mock."""
        user = Mock(spec=User)
        user.id = "regular-user-id"
        user.account_type = AccountType.USER
        return user

    @pytest.fixture
    def mock_preset_template(self):
        """Create mock preset template."""
        template = Mock()
        template.id = "test-preset"
        template.name = "Test Preset"
        template.vars = {"key": "value"}
        template.llm = None

        mock_mode = Mock(spec=ModeTemplate)
        mock_form = Mock()
        mock_form.name = "default"
        mock_form.fields = []
        # Variant metadata (see docs/presets.md "Variants") - explicit so
        # sorted_forms()/default_form_name() (which compare .order/.name and
        # check .default) don't choke on an auto-generated Mock attribute.
        mock_form.label = None
        mock_form.description = None
        mock_form.examples = []
        mock_form.default = False
        mock_form.order = 0
        mock_mode.forms = [mock_form]
        mock_mode.pipes = []
        mock_mode.source_plugin = None

        template.modes = {
            "txt2img": mock_mode,
            "img2img": mock_mode,
        }
        template.form = mock_form  # Legacy form support
        return template

    # ===== list_presets tests =====

    def test_list_presets_admin_include_uninstalled(self, collaborators, admin_user, mock_file_repo, mock_db_repo):
        """Test listing all presets for admin with include_uninstalled=True."""
        mock_file_repo.list_all_presets.return_value = [
            {"id": "preset-1", "name": "Preset 1"},
            {"id": "preset-2", "name": "Preset 2"},
        ]

        mock_installed = [Mock(preset_id="preset-1")]
        mock_db_repo.get_all_installed_presets.return_value = mock_installed
        mock_db_repo.get_preset_assignment_summary.return_value = {
            "total_assignments": 5,
            "preset_db_id": "installed-preset-1",
        }
        collaborators.group_repo.get_group_count_for_preset.return_value = 2

        result = operations.list_presets(collaborators, admin_user, include_uninstalled=True)

        assert len(result) == 2
        assert result[0]["installed"] is True
        assert result[0]["assignment_count"] == 5
        assert result[0]["preset_db_id"] == "installed-preset-1"
        assert result[0]["group_count"] == 2
        assert result[1]["installed"] is False
        assert "preset_db_id" not in result[1]
        collaborators.group_repo.get_group_count_for_preset.assert_called_once_with("preset-1")

    def test_list_presets_regular_user(self, collaborators, regular_user, mock_file_repo, mock_db_repo):
        """Test listing presets for regular user."""
        mock_file_repo.list_all_presets.return_value = [
            {"id": "preset-1", "name": "Preset 1"},
            {"id": "preset-2", "name": "Preset 2"},
            {"id": "preset-3", "name": "Preset 3"},
        ]
        mock_db_repo.get_available_preset_ids_for_user.return_value = ["preset-1", "preset-3"]

        result = operations.list_presets(collaborators, regular_user, include_uninstalled=False)

        assert len(result) == 2
        assert result[0]["id"] == "preset-1"
        assert result[1]["id"] == "preset-3"

    # ===== get_preset tests =====

    def test_get_preset_success(self, collaborators, mock_file_repo, mock_preset_template):
        """Test getting a preset successfully."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template
        mock_preset_info = Mock()
        mock_preset_info.dict.return_value = {"id": "test-preset", "name": "Test Preset"}
        mock_file_repo.preset_to_info.return_value = mock_preset_info

        result = operations.get_preset(collaborators, "test-preset")

        assert result["id"] == "test-preset"
        assert result["vars"] == {"key": "value"}

    def test_get_preset_not_found(self, collaborators, mock_file_repo):
        """Test getting a non-existent preset."""
        mock_file_repo.find_preset_by_id.return_value = None

        with pytest.raises(PresetNotFoundException) as exc_info:
            operations.get_preset(collaborators, "non-existent")

        assert exc_info.value.preset_id == "non-existent"

    def test_get_preset_includes_llm_block(self, collaborators, mock_file_repo, mock_preset_template):
        """The `llm:` block (see docs/presets.md "LLM context") is surfaced on
        get_preset so LLM-facing consumers (get_preset_info, the chat workspace
        block) can see it without a second lookup."""
        mock_preset_template.llm = {"guide": "Use tags.", "context": {"form": "summary"}}
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template
        mock_preset_info = Mock()
        mock_preset_info.dict.return_value = {"id": "test-preset", "name": "Test Preset"}
        mock_file_repo.preset_to_info.return_value = mock_preset_info

        result = operations.get_preset(collaborators, "test-preset")

        assert result["llm"] == {"guide": "Use tags.", "context": {"form": "summary"}}

    def test_get_preset_includes_llm_modes_overrides(self, collaborators, mock_file_repo, mock_preset_template):
        """`llm.modes` per-mode guide overrides (see docs/presets.md "LLM context")
        pass through get_preset like the rest of the `llm:` block."""
        mock_preset_template.llm = {
            "guide": "Use tags.",
            "modes": {"refs": {"guide": "Six-section reference brief."}},
        }
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template
        mock_preset_info = Mock()
        mock_preset_info.dict.return_value = {"id": "test-preset", "name": "Test Preset"}
        mock_file_repo.preset_to_info.return_value = mock_preset_info

        result = operations.get_preset(collaborators, "test-preset")

        assert result["llm"]["modes"] == {"refs": {"guide": "Six-section reference brief."}}

    def test_get_preset_llm_defaults_to_empty_dict(self, collaborators, mock_file_repo, mock_preset_template):
        """A preset with no `llm:` block gets `{}`, not `None` - a stable shape
        for callers that read it without a None-check."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template
        mock_preset_info = Mock()
        mock_preset_info.dict.return_value = {"id": "test-preset", "name": "Test Preset"}
        mock_file_repo.preset_to_info.return_value = mock_preset_info

        result = operations.get_preset(collaborators, "test-preset")

        assert result["llm"] == {}

    # ===== get_available_modes tests =====

    def test_get_available_modes_success(self, collaborators, mock_file_repo, mock_preset_template):
        """Test getting available modes successfully."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template

        result = operations.get_available_modes(collaborators, "test-preset")

        assert result["preset_id"] == "test-preset"
        assert len(result["modes"]) == 2
        mode_names = [m["name"] for m in result["modes"]]
        assert "txt2img" in mode_names
        assert "img2img" in mode_names

    def test_get_available_modes_not_found(self, collaborators, mock_file_repo):
        """Test getting modes for non-existent preset."""
        mock_file_repo.find_preset_by_id.return_value = None

        with pytest.raises(PresetNotFoundException):
            operations.get_available_modes(collaborators, "non-existent")

    def test_get_available_modes_source_plugin_null_for_core_mode(
        self, collaborators, mock_file_repo, mock_preset_template
    ):
        """A mode the preset declares itself reports `source_plugin: null` -
        the provenance contract (see docs/presets.md "Plugin-contributed
        modes")."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template

        result = operations.get_available_modes(collaborators, "test-preset")

        for mode in result["modes"]:
            assert mode["source_plugin"] is None

    def test_get_available_modes_source_plugin_carries_contributing_plugin_id(
        self, collaborators, mock_file_repo, mock_preset_template
    ):
        """A mode merged in from a plugin's `preset_modes:` reports the
        contributing plugin's id."""
        mock_preset_template.modes["img2img"] = Mock(spec=ModeTemplate)
        mock_preset_template.modes["img2img"].forms = mock_preset_template.modes["txt2img"].forms
        mock_preset_template.modes["img2img"].pipes = []
        mock_preset_template.modes["img2img"].source_plugin = "some-plugin"
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template

        result = operations.get_available_modes(collaborators, "test-preset")
        img2img = next(m for m in result["modes"] if m["name"] == "img2img")

        assert img2img["source_plugin"] == "some-plugin"

    # ===== get_available_modes variants shape tests =====

    def test_get_available_modes_single_form_always_has_variants(
        self, collaborators, mock_file_repo, mock_preset_template
    ):
        """Even a mode with exactly one form must carry a `variants` list
        (fixed contract with the frontend - see docs/presets.md "Variants")."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template

        result = operations.get_available_modes(collaborators, "test-preset")

        for mode in result["modes"]:
            assert "variants" in mode
            assert len(mode["variants"]) == 1
            variant = mode["variants"][0]
            assert variant["name"] == "default"
            assert variant["default"] is True
            assert variant["label"] == "Default"
            assert variant["examples"] == []

    def test_get_available_modes_variants_sorted_by_order_then_name(
        self, collaborators, mock_file_repo, mock_preset_template
    ):
        """Variants sort by (order, name); with no `default: true` anywhere,
        the first after sorting is marked default in the response."""
        form_b = Mock()
        form_b.name = "beta"
        form_b.label = None
        form_b.description = None
        form_b.examples = []
        form_b.default = False
        form_b.order = 1

        form_a = Mock()
        form_a.name = "alpha"
        form_a.label = "Alpha Variant"
        form_a.description = "The alpha one"
        form_a.examples = ["public/a.png"]
        form_a.default = False
        form_a.order = 0

        mock_preset_template.modes["txt2img"].forms = [form_b, form_a]
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template

        result = operations.get_available_modes(collaborators, "test-preset")
        txt2img = next(m for m in result["modes"] if m["name"] == "txt2img")
        names_in_order = [v["name"] for v in txt2img["variants"]]

        assert names_in_order == ["alpha", "beta"]
        assert txt2img["variants"][0]["default"] is True
        assert txt2img["variants"][0]["label"] == "Alpha Variant"
        assert txt2img["variants"][0]["description"] == "The alpha one"
        assert txt2img["variants"][0]["examples"] == ["public/a.png"]
        assert txt2img["variants"][1]["default"] is False

    def test_get_available_modes_explicit_default_wins_over_order(
        self, collaborators, mock_file_repo, mock_preset_template
    ):
        """A form explicitly flagged `default: true` is the default variant
        even if another form sorts earlier by (order, name)."""
        form_first_by_order = Mock()
        form_first_by_order.name = "alpha"
        form_first_by_order.label = None
        form_first_by_order.description = None
        form_first_by_order.examples = []
        form_first_by_order.default = False
        form_first_by_order.order = 0

        form_explicit_default = Mock()
        form_explicit_default.name = "beta"
        form_explicit_default.label = None
        form_explicit_default.description = None
        form_explicit_default.examples = []
        form_explicit_default.default = True
        form_explicit_default.order = 1

        mock_preset_template.modes["txt2img"].forms = [form_first_by_order, form_explicit_default]
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template

        result = operations.get_available_modes(collaborators, "test-preset")
        txt2img = next(m for m in result["modes"] if m["name"] == "txt2img")
        defaults = [v["name"] for v in txt2img["variants"] if v["default"]]

        assert defaults == ["beta"]

    # ===== get_form_schema tests =====

    def test_get_form_schema_success(self, collaborators, mock_file_repo, mock_preset_template):
        """Test getting form schema successfully."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template
        collaborators.form_serializer.process_form_fields.return_value = {"fields": []}

        result = operations.get_form_schema(collaborators, "test-preset", "txt2img")

        assert result["preset_id"] == "test-preset"
        assert "form_schema" in result
        assert "debug_info" in result

    def test_get_form_schema_default_mode(self, collaborators, mock_file_repo, mock_preset_template):
        """Test getting form schema with default mode."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template
        collaborators.form_serializer.process_form_fields.return_value = {"fields": []}

        result = operations.get_form_schema(collaborators, "test-preset")

        assert result["preset_id"] == "test-preset"

    def test_get_form_schema_mode_not_found(self, collaborators, mock_file_repo, mock_preset_template):
        """Test getting form schema with invalid mode."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template

        with pytest.raises(ModeNotFoundException):
            operations.get_form_schema(collaborators, "test-preset", "invalid_mode")

    def test_get_form_schema_no_modes(self, collaborators, mock_file_repo):
        """Test getting form schema when no modes are defined."""
        template = Mock()
        template.id = "test-preset"
        template.modes = {}
        mock_file_repo.find_preset_by_id.return_value = template

        with pytest.raises(NoModesAvailableException):
            operations.get_form_schema(collaborators, "test-preset")

    # ===== get_pipeline tests =====

    def test_get_pipeline_success(self, collaborators, mock_file_repo, mock_preset_template, mock_pipeline_builder):
        """Test getting pipeline successfully."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template
        mock_pipeline_builder.build_pipeline.return_value = BuiltPipeline(
            generation_id="gen-1",
            preset_id="test-preset",
            preset_template=mock_preset_template,
            pipes=[],
        )

        result = operations.get_pipeline(collaborators, "test-preset", "txt2img", {})

        assert isinstance(result, PipelineGraph)
        assert result.preset_id == "test-preset"
        mock_pipeline_builder.build_pipeline.assert_called_once()

    def test_get_pipeline_not_found(self, collaborators, mock_file_repo):
        """Test getting pipeline for non-existent preset."""
        mock_file_repo.find_preset_by_id.return_value = None

        with pytest.raises(PresetNotFoundException):
            operations.get_pipeline(collaborators, "non-existent")

    def test_get_pipeline_mode_not_found(self, collaborators, mock_file_repo, mock_preset_template, mock_pipeline_builder):
        """Test getting pipeline with an invalid mode raises ModeNotFoundException
        before the pipeline builder is even invoked."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template

        with pytest.raises(ModeNotFoundException) as exc_info:
            operations.get_pipeline(collaborators, "test-preset", "invalid_mode", {})

        assert exc_info.value.preset_id == "test-preset"
        assert exc_info.value.mode == "invalid_mode"
        mock_pipeline_builder.build_pipeline.assert_not_called()

    def test_get_pipeline_invalid_mode_data(self, collaborators, mock_file_repo, mock_preset_template, mock_pipeline_builder):
        """Test getting pipeline when mode data is a malformed shape (neither
        ModeTemplate nor a list of pipes)."""
        mock_preset_template.modes["broken_mode"] = "not-valid-mode-data"
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template

        with pytest.raises(InvalidModeDataException):
            operations.get_pipeline(collaborators, "test-preset", "broken_mode", {})

        mock_pipeline_builder.build_pipeline.assert_not_called()

    def test_get_pipeline_string_mode_key(self, collaborators, mock_file_repo, mock_preset_template, mock_pipeline_builder):
        """Test pipeline building with string mode keys (YAML format)."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template
        mock_pipeline_builder.build_pipeline.return_value = BuiltPipeline(
            generation_id="gen-1",
            preset_id="test-preset",
            preset_template=mock_preset_template,
            pipes=[],
        )

        result = operations.get_pipeline(collaborators, "test-preset", "img2img", {})

        assert result.mode == "img2img"

    # ===== reload_preset tests =====

    def test_reload_preset_success(self, collaborators, mock_preset_loader, mock_file_repo, mock_preset_template):
        """Test reloading preset successfully."""
        mock_file_repo.find_preset_by_id.return_value = mock_preset_template
        mock_preset_info = Mock()
        mock_preset_info.dict.return_value = {"id": "test-preset"}
        mock_file_repo.preset_to_info.return_value = mock_preset_info

        result = operations.reload_preset(collaborators, "test-preset")

        mock_preset_loader.clear_cache.assert_called_once()
        mock_preset_loader.load_presets.assert_called_once()
        assert result["id"] == "test-preset"


class TestPresetOperationsInstallation:
    """Tests for installation operations in src.features.presets.operations."""

    @pytest.fixture
    def collaborators(self):
        """Build a PresetCollaborators bundle with mocks."""
        with patch('src.features.presets.collaborators.PresetFormSerializer'):
            mock_plugin_registry = Mock()
            mock_plugin_registry.execute_hook.return_value = (Mock(data={"blocked": False}), [])

            return PresetCollaborators(
                preset_loader=Mock(),
                preset_processor=Mock(),
                template_processor=Mock(),
                file_repo=Mock(),
                db_repo=Mock(),
                user_repo=Mock(),
                group_repo=Mock(),
                pipeline_builder=Mock(),
                pipe_catalog=Mock(),
                plugins=mock_plugin_registry,
                settings=Mock(),
            )

    @pytest.fixture
    def admin_user(self):
        """Create admin user mock."""
        user = Mock(spec=User)
        user.id = "admin-id"
        user.account_type = AccountType.ADMIN
        return user

    @pytest.fixture
    def regular_user(self):
        """Create regular user mock."""
        user = Mock(spec=User)
        user.id = "user-id"
        user.account_type = AccountType.USER
        return user

    # ===== install_preset tests =====

    def test_install_preset_success(self, collaborators, admin_user):
        """Test installing preset successfully."""
        collaborators.file_repo.find_preset_by_id.return_value = Mock(name="Test Preset")
        collaborators.db_repo.is_preset_installed.return_value = False
        mock_installed = Mock()
        mock_installed.id = "installed-id"
        mock_installed.to_dict.return_value = {"id": "installed-id"}
        collaborators.db_repo.install_preset.return_value = mock_installed

        result = operations.install_preset(collaborators, "test-preset", admin_user)

        assert result["id"] == "installed-id"
        collaborators.db_repo.install_preset.assert_called_once_with("test-preset")

    def test_install_preset_permission_denied(self, collaborators, regular_user):
        """Test installing preset as regular user."""
        with pytest.raises(PermissionDeniedException):
            operations.install_preset(collaborators, "test-preset", regular_user)

    def test_install_preset_not_found(self, collaborators, admin_user):
        """Test installing non-existent preset."""
        collaborators.file_repo.find_preset_by_id.return_value = None

        with pytest.raises(PresetNotFoundException):
            operations.install_preset(collaborators, "non-existent", admin_user)

    def test_install_preset_already_installed(self, collaborators, admin_user):
        """Test installing already installed preset."""
        collaborators.file_repo.find_preset_by_id.return_value = Mock()
        collaborators.db_repo.is_preset_installed.return_value = True

        with pytest.raises(PresetAlreadyInstalledException):
            operations.install_preset(collaborators, "test-preset", admin_user)

    def test_install_preset_blocked_by_hook(self, collaborators, admin_user):
        """Test installation blocked by hook."""
        collaborators.file_repo.find_preset_by_id.return_value = Mock(name="Test")
        collaborators.db_repo.is_preset_installed.return_value = False
        collaborators.plugins.execute_hook.return_value = (
            Mock(data={"blocked": True, "block_reason": "Test reason"}),
            [],
        )

        with pytest.raises(PermissionDeniedException):
            operations.install_preset(collaborators, "test-preset", admin_user)

    # ===== uninstall_preset tests =====

    def test_uninstall_preset_success(self, collaborators, admin_user):
        """Test uninstalling preset successfully."""
        collaborators.db_repo.is_preset_installed.return_value = True
        collaborators.db_repo.get_preset_assignment_summary.return_value = {"total_assignments": 3}
        collaborators.db_repo.uninstall_preset.return_value = True

        result = operations.uninstall_preset(collaborators, "test-preset", admin_user)

        assert "uninstalled successfully" in result
        assert "3 user assignments" in result

    def test_uninstall_preset_permission_denied(self, collaborators, regular_user):
        """Test uninstalling preset as regular user."""
        with pytest.raises(PermissionDeniedException):
            operations.uninstall_preset(collaborators, "test-preset", regular_user)

    def test_uninstall_preset_not_installed(self, collaborators, admin_user):
        """Test uninstalling non-installed preset."""
        collaborators.db_repo.is_preset_installed.return_value = False

        with pytest.raises(PresetNotInstalledException):
            operations.uninstall_preset(collaborators, "test-preset", admin_user)


class TestPresetOperationsAssignment:
    """Tests for assignment operations in src.features.presets.operations."""

    @pytest.fixture
    def collaborators(self):
        """Build a PresetCollaborators bundle with mocks."""
        with patch('src.features.presets.collaborators.PresetFormSerializer'):
            mock_plugin_registry = Mock()
            mock_plugin_registry.execute_hook.return_value = (Mock(data={"blocked": False}), [])

            return PresetCollaborators(
                preset_loader=Mock(),
                preset_processor=Mock(),
                template_processor=Mock(),
                file_repo=Mock(),
                db_repo=Mock(),
                user_repo=Mock(),
                group_repo=Mock(),
                pipeline_builder=Mock(),
                pipe_catalog=Mock(),
                plugins=mock_plugin_registry,
                settings=Mock(),
            )

    @pytest.fixture
    def admin_user(self):
        """Create admin user mock."""
        user = Mock(spec=User)
        user.id = "admin-id"
        user.account_type = AccountType.ADMIN
        return user

    # ===== assign_preset_to_users tests =====

    def test_assign_preset_to_users_success(self, collaborators, admin_user):
        """Test assigning preset to users successfully."""
        collaborators.db_repo.is_preset_installed.return_value = True
        collaborators.user_repo.get_by_id.return_value = Mock()
        mock_assignment = Mock()
        mock_assignment.to_dict.return_value = {"id": "assignment-1"}
        collaborators.db_repo.assign_preset_to_users.return_value = [mock_assignment]

        result = operations.assign_preset_to_users(collaborators, "test-preset", ["user-1", "user-2"], admin_user)

        assert result["preset_id"] == "test-preset"
        assert result["assigned_count"] == 1

    def test_assign_preset_to_users_invalid_users(self, collaborators, admin_user):
        """Test assigning preset with invalid user IDs."""
        collaborators.db_repo.is_preset_installed.return_value = True
        collaborators.user_repo.get_by_id.side_effect = [None, Mock()]

        with pytest.raises(InvalidUsersException) as exc_info:
            operations.assign_preset_to_users(collaborators, "test-preset", ["invalid-user", "valid-user"], admin_user)

        assert "invalid-user" in exc_info.value.invalid_user_ids

    def test_assign_preset_not_installed(self, collaborators, admin_user):
        """Test assigning non-installed preset."""
        collaborators.db_repo.is_preset_installed.return_value = False

        with pytest.raises(PresetNotInstalledException):
            operations.assign_preset_to_users(collaborators, "test-preset", ["user-1"], admin_user)

    # ===== unassign_preset_from_user tests =====

    def test_unassign_preset_from_user_success(self, collaborators, admin_user):
        """Test unassigning preset from user successfully."""
        collaborators.user_repo.get_by_id.return_value = Mock()
        collaborators.db_repo.is_preset_directly_assigned_to_user.return_value = True
        collaborators.db_repo.unassign_preset_from_user.return_value = True

        result = operations.unassign_preset_from_user(collaborators, "test-preset", "user-1", admin_user)

        assert "unassigned" in result

    def test_unassign_preset_user_not_found(self, collaborators, admin_user):
        """Test unassigning preset from non-existent user."""
        collaborators.user_repo.get_by_id.return_value = None

        with pytest.raises(UserNotFoundException):
            operations.unassign_preset_from_user(collaborators, "test-preset", "non-existent", admin_user)

    def test_unassign_preset_not_assigned(self, collaborators, admin_user):
        """Test unassigning preset that isn't assigned."""
        collaborators.user_repo.get_by_id.return_value = Mock()
        collaborators.db_repo.is_preset_directly_assigned_to_user.return_value = False

        with pytest.raises(PresetNotAssignedException):
            operations.unassign_preset_from_user(collaborators, "test-preset", "user-1", admin_user)

        collaborators.db_repo.unassign_preset_from_user.assert_not_called()

    # ===== get_preset_assignments tests =====

    def test_get_preset_assignments_success(self, collaborators, admin_user):
        """Test getting preset assignments successfully."""
        collaborators.db_repo.get_preset_assignment_summary.return_value = {
            "installed": True,
            "total_assignments": 2,
            "assignments": [
                {"user_id": "user-1"},
                {"user_id": "user-2"},
            ],
        }
        collaborators.user_repo.get_by_id.return_value = Mock(to_dict=lambda: {"id": "user-1"})

        result = operations.get_preset_assignments(collaborators, "test-preset", admin_user)

        assert result["installed"] is True
        assert result["total_assignments"] == 2

    def test_get_preset_assignments_not_installed(self, collaborators, admin_user):
        """Test getting assignments for non-installed preset."""
        collaborators.db_repo.get_preset_assignment_summary.return_value = {"installed": False}

        with pytest.raises(PresetNotInstalledException):
            operations.get_preset_assignments(collaborators, "test-preset", admin_user)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
