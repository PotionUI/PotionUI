"""Tests for developer manager."""
import pytest
from unittest.mock import Mock, MagicMock, patch
from src.features.developer.manager import DeveloperManager


class TestDeveloperManager:
    """Test suite for DeveloperManager."""

    @pytest.fixture
    def mock_pipe_catalog(self):
        """Create a mock pipe registry."""
        registry = Mock()
        registry.pipes = {}
        registry.discover_pipes = Mock()
        registry.get_pipe_status = Mock()
        return registry

    @pytest.fixture
    def mock_preset_loader(self):
        """Create a mock preset loader."""
        return Mock()

    @pytest.fixture
    def mock_template_processor(self):
        """Create a mock template processor."""
        return Mock()

    @pytest.fixture
    def manager(self, mock_pipe_catalog, mock_preset_loader, mock_template_processor):
        """Create a developer manager with mocked dependencies."""
        return DeveloperManager(
            mock_pipe_catalog,
            mock_preset_loader,
            mock_template_processor
        )

    def test_initialization(self, mock_pipe_catalog, mock_preset_loader, mock_template_processor):
        """Test manager can be initialized."""
        manager = DeveloperManager(
            mock_pipe_catalog,
            mock_preset_loader,
            mock_template_processor
        )

        assert manager is not None
        assert manager.pipe_catalog == mock_pipe_catalog
        assert manager.preset_loader == mock_preset_loader
        assert manager.template_processor == mock_template_processor
        assert manager.field_factory is not None

    def test_initialization_creates_documenters(self, manager):
        """Test initialization creates all documenters."""
        assert hasattr(manager, '_pipes_documenter')
        assert hasattr(manager, '_fields_documenter')
        assert hasattr(manager, '_io_types_documenter')
        assert hasattr(manager, '_template_functions_documenter')

    def test_get_pipes_documentation_success(self, manager):
        """Test getting pipes documentation successfully."""
        expected_result = {
            'pipes': [
                {'name': 'test_pipe', 'description': 'Test pipe'}
            ],
            'total': 1
        }

        with patch.object(manager._pipes_documenter, 'generate_documentation', return_value=expected_result):
            result = manager.get_pipes_documentation()
            assert result == expected_result

    def test_get_pipes_documentation_error(self, manager):
        """Documenter errors propagate as-is - the manager does not swallow the type."""
        with patch.object(manager._pipes_documenter, 'generate_documentation', side_effect=Exception("Test error")):
            with pytest.raises(Exception, match="Test error"):
                manager.get_pipes_documentation()

    def test_get_fields_documentation_success(self, manager):
        """Test getting fields documentation successfully."""
        expected_result = {
            'fields': [
                {'type': 'text', 'description': 'Text field'}
            ],
            'total': 1
        }

        with patch.object(manager._fields_documenter, 'generate_documentation', return_value=expected_result):
            result = manager.get_fields_documentation()
            assert result == expected_result

    def test_get_fields_documentation_error(self, manager):
        """Documenter errors propagate as-is - the manager does not swallow the type."""
        with patch.object(manager._fields_documenter, 'generate_documentation', side_effect=Exception("Test error")):
            with pytest.raises(Exception, match="Test error"):
                manager.get_fields_documentation()

    def test_get_io_types_success(self, manager):
        """Test getting IO types successfully."""
        expected_result = {
            'io_types': [
                {'name': 'IMAGE', 'value': 'IMAGE', 'description': 'PIL Image object'}
            ],
            'total': 1
        }

        with patch.object(manager._io_types_documenter, 'generate_documentation', return_value=expected_result):
            result = manager.get_io_types()
            assert result == expected_result

    def test_get_io_types_error(self, manager):
        """Documenter errors propagate as-is - the manager does not swallow the type."""
        with patch.object(manager._io_types_documenter, 'generate_documentation', side_effect=Exception("Test error")):
            with pytest.raises(Exception, match="Test error"):
                manager.get_io_types()

    def test_get_template_functions_documentation_success(self, manager):
        """Test getting template functions documentation successfully."""
        expected_result = {
            'functions': [
                {'name': 'path', 'description': 'Path helper function'}
            ],
            'total': 1,
            'categories': ['Path Helpers']
        }

        with patch.object(manager._template_functions_documenter, 'generate_documentation', return_value=expected_result):
            result = manager.get_template_functions_documentation()
            assert result == expected_result

    def test_get_template_functions_documentation_error(self, manager):
        """Documenter errors propagate as-is - the manager does not swallow the type."""
        with patch.object(manager._template_functions_documenter, 'generate_documentation', side_effect=Exception("Test error")):
            with pytest.raises(Exception, match="Test error"):
                manager.get_template_functions_documentation()

    def test_all_methods_return_dict(self, manager):
        """Test all documentation methods return dictionary results."""
        with patch.object(manager._pipes_documenter, 'generate_documentation', return_value={}):
            assert isinstance(manager.get_pipes_documentation(), dict)

        with patch.object(manager._fields_documenter, 'generate_documentation', return_value={}):
            assert isinstance(manager.get_fields_documentation(), dict)

        with patch.object(manager._io_types_documenter, 'generate_documentation', return_value={}):
            assert isinstance(manager.get_io_types(), dict)

        with patch.object(manager._template_functions_documenter, 'generate_documentation', return_value={}):
            assert isinstance(manager.get_template_functions_documentation(), dict)

    def test_field_factory_created_with_correct_dependencies(self, manager, mock_preset_loader, mock_template_processor):
        """Test field factory is created with correct dependencies."""
        assert manager.field_factory is not None
        # The field factory should have been created with the loader and processor
        # We can't easily test the internal state, but we can verify it exists

    def test_documenters_use_correct_dependencies(self, manager, mock_pipe_catalog):
        """Test documenters are initialized with correct dependencies."""
        # Pipes documenter should use pipe registry
        assert manager._pipes_documenter.pipe_catalog == mock_pipe_catalog

        # Fields documenter should use field factory
        assert manager._fields_documenter.field_factory == manager.field_factory

        # IO types and template functions documenters don't need dependencies
        assert manager._io_types_documenter is not None
        assert manager._template_functions_documenter is not None


class TestGetPresetsLint:
    """`get_presets_lint` (`GET /api/developer/presets/lint`) must feed the
    preset_loader's enabled plugins into `PresetLinter`, so a
    plugin `preset_modes:` collision surfaces here without a separate code
    path from `scripts/preset_lint.py`."""

    @staticmethod
    def _write_preset(root, preset_id, modes):
        import yaml

        preset_dir = root / preset_id
        preset_dir.mkdir(parents=True)
        (preset_dir / "preset.yml").write_text(yaml.dump({
            "schema": 1, "id": preset_id, "name": preset_id, "version": "1.0.0",
            "category": "image", "engine": "native", "modes": modes,
        }))
        for mode in modes:
            mode_dir = preset_dir / "modes" / mode
            mode_dir.mkdir(parents=True)
            (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
        return preset_dir

    @staticmethod
    def _write_modes_root(plugin_dir, mode_name):
        mode_dir = plugin_dir / "contributed" / "modes" / mode_name
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
        return plugin_dir / "contributed"

    def _manager_with_real_loader(self, tmp_path, plugin_registry):
        from types import SimpleNamespace
        from src.features.presets.loader import PresetTemplateLoader

        core_root = tmp_path / "presets"
        self._write_preset(core_root, "target-preset", ["txt2img"])
        loader = PresetTemplateLoader([str(core_root)], plugin_registry=plugin_registry)

        return DeveloperManager(Mock(), loader, Mock())

    def test_no_plugin_registry_lints_without_crashing(self, tmp_path):
        manager = self._manager_with_real_loader(tmp_path, plugin_registry=None)
        result = manager.get_presets_lint()
        assert result["load_errors"] == {}

    def test_plugin_registry_collision_surfaces_as_lint_issue(self, tmp_path):
        from types import SimpleNamespace

        plugin_dir = tmp_path / "plugin"
        self._write_modes_root(plugin_dir, "txt2img")  # collides with the target's core mode
        manifest = SimpleNamespace(
            id="some-plugin", plugin_dir=plugin_dir,
            preset_modes=[{"target": "target-preset", "modes_root": "contributed"}],
        )
        registry = SimpleNamespace(get_enabled_plugins=lambda: [manifest])

        manager = self._manager_with_real_loader(tmp_path, plugin_registry=registry)
        result = manager.get_presets_lint()

        assert any(
            issue["level"] == "error" and "collides with a core mode" in issue["message"]
            for issue in result["lint_issues"]
        )
