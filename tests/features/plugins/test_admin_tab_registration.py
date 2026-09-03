"""
Tests for plugin-contributed Admin -> Plugins detail tabs (`admin_tabs[]`).

An `admin_tabs` entry is delivered to the frontend through the same
`hooks.frontend` pipeline as any other frontend hook: registered as a
`PluginHook` with `hook_name = "admin.plugin.tabs"`, `component_path` the
tab's component, `position` the tab's own `id`, and `label`/`require_role`
riding the columns added for exactly this purpose.
"""
import pytest
from unittest.mock import Mock

from pydantic import ValidationError

from src.platform.plugins.loader import PluginManifest, PluginLoader
from src.platform.plugins.manifest import PluginManifestSchema
from src.features.plugins.operations import scan as scan_ops
from src.features.plugins.records import PluginHook


# ========== Manifest schema ==========

class TestAdminTabSpec:
    def _minimal(self, **overrides):
        data = {
            'id': 'test-plugin',
            'name': 'Test Plugin',
            'version': '1.0.0',
            'description': 'A test plugin',
            'author': 'Test Author',
            'type': 'full-stack',
        }
        data.update(overrides)
        return data

    def test_admin_tabs_default_empty(self):
        schema = PluginManifestSchema.model_validate(self._minimal())
        assert schema.admin_tabs == []

    def test_admin_tabs_parses(self):
        data = self._minimal(admin_tabs=[
            {'id': 'import-workflow', 'label': 'Import workflow', 'component': 'ImportWorkflowTab.js', 'order': 10}
        ])
        schema = PluginManifestSchema.model_validate(data)
        assert len(schema.admin_tabs) == 1
        assert schema.admin_tabs[0].id == 'import-workflow'
        assert schema.admin_tabs[0].label == 'Import workflow'
        assert schema.admin_tabs[0].component == 'ImportWorkflowTab.js'
        assert schema.admin_tabs[0].order == 10
        assert schema.admin_tabs[0].require_role is None

    def test_admin_tabs_order_defaults_100(self):
        data = self._minimal(admin_tabs=[
            {'id': 'imported-presets', 'label': 'Imported presets', 'component': 'ImportedPresetsTab.js'}
        ])
        schema = PluginManifestSchema.model_validate(data)
        assert schema.admin_tabs[0].order == 100

    def test_admin_tabs_require_role_parses(self):
        data = self._minimal(admin_tabs=[
            {'id': 'danger-zone', 'label': 'Danger zone', 'component': 'DangerZone.js', 'require_role': 'ADMIN'}
        ])
        schema = PluginManifestSchema.model_validate(data)
        assert schema.admin_tabs[0].require_role == 'ADMIN'

    def test_admin_tabs_rejects_missing_required_field(self):
        data = self._minimal(admin_tabs=[{'id': 'x', 'component': 'X.js'}])  # missing label
        with pytest.raises(ValidationError):
            PluginManifestSchema.model_validate(data)

    def test_admin_tabs_rejects_unknown_field(self):
        data = self._minimal(admin_tabs=[
            {'id': 'x', 'label': 'X', 'component': 'X.js', 'unexpected': 'boom'}
        ])
        with pytest.raises(ValidationError):
            PluginManifestSchema.model_validate(data)


# ========== Loader parsing ==========

class TestPluginLoaderParsesAdminTabs:
    def test_load_manifest_parses_admin_tabs(self, tmp_path):
        manifest_content = """
id: test-plugin
name: Test Plugin
version: 1.0.0
description: A test plugin
author: Test Author
type: full-stack
admin_tabs:
  - id: import-workflow
    label: Import workflow
    component: ImportWorkflowTab.js
    order: 10
  - id: imported-presets
    label: Imported presets
    component: ImportedPresetsTab.js
"""
        manifest_file = tmp_path / "manifest.yml"
        manifest_file.write_text(manifest_content)

        loader = PluginLoader()
        manifest = loader._load_manifest(manifest_file, tmp_path, "local")

        assert manifest is not None
        assert len(manifest.admin_tabs) == 2
        assert manifest.admin_tabs[0]['id'] == 'import-workflow'
        assert manifest.admin_tabs[0]['label'] == 'Import workflow'
        assert manifest.admin_tabs[0]['component'] == 'ImportWorkflowTab.js'
        assert manifest.admin_tabs[0]['order'] == 10
        assert manifest.admin_tabs[1]['order'] == 100

    def test_load_manifest_missing_admin_tabs_defaults_empty(self, tmp_path):
        manifest_content = """
id: test-plugin
name: Test Plugin
version: 1.0.0
description: A test plugin
author: Test Author
type: full-stack
"""
        manifest_file = tmp_path / "manifest.yml"
        manifest_file.write_text(manifest_content)

        loader = PluginLoader()
        manifest = loader._load_manifest(manifest_file, tmp_path, "local")

        assert manifest is not None
        assert manifest.admin_tabs == []


# ========== PluginHook label/require_role ==========

class TestPluginHookLabelAndRole:
    def test_defaults_none(self):
        hook = PluginHook(id=1, plugin_id="p", hook_name="admin.plugin.tabs", hook_type="frontend")
        assert hook.label is None
        assert hook.require_role is None

    def test_from_row_reads_label_and_role(self):
        row = {
            'id': 1, 'plugin_id': 'p', 'hook_name': 'admin.plugin.tabs', 'hook_type': 'frontend',
            'handler_path': None, 'component_path': 'Tab.js', 'position': 'import-workflow',
            'sort_order': 10, 'label': 'Import workflow', 'require_role': 'ADMIN'
        }
        hook = PluginHook.from_row(row)
        assert hook.label == 'Import workflow'
        assert hook.require_role == 'ADMIN'

    def test_from_row_without_label_columns_is_none(self):
        """Legacy rows (pre-migration 008) lack `label`/`require_role` keys entirely."""
        row = {
            'id': 1, 'plugin_id': 'p', 'hook_name': 'workbench.actions', 'hook_type': 'frontend',
            'handler_path': None, 'component_path': 'Action.js', 'position': None, 'sort_order': 0
        }
        hook = PluginHook.from_row(row)
        assert hook.label is None
        assert hook.require_role is None

    def test_to_dict_includes_label_and_role(self):
        hook = PluginHook(
            id=1, plugin_id="p", hook_name="admin.plugin.tabs", hook_type="frontend",
            component_path="Tab.js", position="import-workflow", sort_order=10,
            label="Import workflow", require_role="ADMIN"
        )
        result = hook.to_dict()
        assert result['label'] == 'Import workflow'
        assert result['require_role'] == 'ADMIN'


# ========== scan._register_plugin_hooks ==========

@pytest.fixture
def mock_plugin_repo():
    return Mock()


class TestOperationsRegisterAdminTabs:
    def test_register_plugin_hooks_registers_admin_tabs(self, mock_plugin_repo):
        manifest = PluginManifest(
            id="comfyui-backend",
            name="ComfyUI Backend",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            admin_tabs=[
                {'id': 'import-workflow', 'label': 'Import workflow', 'component': 'ImportWorkflowTab.js', 'order': 10},
                {'id': 'imported-presets', 'label': 'Imported presets', 'component': 'ImportedPresetsTab.js', 'order': 20},
            ]
        )

        scan_ops._register_plugin_hooks(mock_plugin_repo, manifest)

        registered = [c.args[0] for c in mock_plugin_repo.register_hook.call_args_list]
        tabs = [h for h in registered if h.hook_name == "admin.plugin.tabs"]
        assert len(tabs) == 2

        first = tabs[0]
        assert isinstance(first, PluginHook)
        assert first.plugin_id == "comfyui-backend"
        assert first.hook_type == "frontend"
        assert first.component_path == "ImportWorkflowTab.js"
        assert first.position == "import-workflow"
        assert first.sort_order == 10
        assert first.label == "Import workflow"
        assert first.require_role is None

    def test_register_plugin_hooks_admin_tab_require_role(self, mock_plugin_repo):
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            admin_tabs=[
                {'id': 'danger-zone', 'label': 'Danger zone', 'component': 'DangerZone.js', 'require_role': 'ADMIN'}
            ]
        )

        scan_ops._register_plugin_hooks(mock_plugin_repo, manifest)

        registered = [c.args[0] for c in mock_plugin_repo.register_hook.call_args_list]
        tab = next(h for h in registered if h.hook_name == "admin.plugin.tabs")
        assert tab.require_role == "ADMIN"

    def test_register_plugin_hooks_no_admin_tabs_registers_none(self, mock_plugin_repo):
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
        )

        scan_ops._register_plugin_hooks(mock_plugin_repo, manifest)

        registered = [c.args[0] for c in mock_plugin_repo.register_hook.call_args_list]
        assert not any(h.hook_name == "admin.plugin.tabs" for h in registered)

    def test_refresh_plugin_hooks_reregisters_admin_tabs(self, mock_plugin_repo):
        """`_refresh_plugin_hooks` (the startup-refresh path) clears then re-registers,
        so an admin_tabs manifest change takes effect on restart without a rescan."""
        manifest = PluginManifest(
            id="comfyui-backend",
            name="ComfyUI Backend",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            admin_tabs=[
                {'id': 'import-workflow', 'label': 'Import workflow', 'component': 'ImportWorkflowTab.js'}
            ]
        )

        scan_ops._refresh_plugin_hooks(mock_plugin_repo, manifest)

        mock_plugin_repo.clear_plugin_hooks.assert_called_once_with("comfyui-backend")
        registered = [c.args[0] for c in mock_plugin_repo.register_hook.call_args_list]
        assert any(h.hook_name == "admin.plugin.tabs" for h in registered)
