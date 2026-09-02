"""
Tests for PluginController.

The controller calls `src.features.plugins.operations` functions directly
(module-level, no injected manager) for mutations and for the manifest/
registry-derived reads (quick actions, sidebar widgets, frontend extensions,
hooks catalog); pure reads (list/get/settings/frontend hooks) go straight to
PluginRepository/PluginRegistry and the plugins mappers. `mock_operations`
patches the `operations` module as imported into `routes.py`, so tests assert
against it exactly like the previous manager mock, without the controller
holding a stateful collaborator it doesn't need.
"""
import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime

from src.features.plugins import routes as routes_module
from src.features.plugins.routes import PluginController
from src.features.plugins.dto import (
    PluginResponse,
    PluginDetailResponse,
    PluginHookResponse,
    PluginSettingResponse,
    PluginScanResult,
    PluginSettingsUpdateRequest,
)
from src.features.plugins.records import Plugin, PluginHook, PluginSetting
from src.platform.plugins.registry import PluginState
from src.platform.plugins.loader import PluginManifest
from pathlib import Path


@pytest.fixture
def mock_operations(monkeypatch):
    """Patch the `operations` module as seen by routes.py."""
    mock = Mock()
    monkeypatch.setattr(routes_module, "operations", mock)
    return mock


@pytest.fixture
def mock_plugin_repository():
    """Mock PluginRepository"""
    return Mock()


@pytest.fixture
def mock_plugin_registry():
    """Mock PluginRegistry"""
    registry = Mock()
    # Default: no manifest loaded for a plugin - individual tests override
    # this with a concrete PluginManifest/MagicMock when they need
    # manifest-derived enrichment.
    registry.get_plugin.return_value = None
    return registry


@pytest.fixture
def controller(mock_operations, mock_plugin_repository, mock_plugin_registry):
    """Create PluginController instance with mocked dependencies"""
    return PluginController(
        plugin_repository=mock_plugin_repository,
        plugin_registry=mock_plugin_registry,
    )


@pytest.fixture
def sample_plugin():
    """Sample Plugin database record for testing"""
    return Plugin(
        id="test-plugin-1",
        name="Test Plugin",
        version="1.0.0",
        type="full-stack",
        enabled=True,
        manifest_path="/content/plugins/local/test-plugin/manifest.yml",
        description="A test plugin",
        author="Test Author",
        installed_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=datetime(2024, 1, 1, 12, 0, 0),
    )


@pytest.fixture
def sample_plugin_hook():
    """Sample PluginHook database record for testing"""
    return PluginHook(
        id=1,
        plugin_id="test-plugin-1",
        hook_name="workbench.image_modal",
        hook_type="frontend",
        handler_path=None,
        component_path="/plugins/test-plugin/ImageModal.svelte",
        position="center",
        sort_order=0,
    )


@pytest.fixture
def sample_plugin_setting():
    """Sample PluginSetting database record for testing"""
    return PluginSetting(
        id=1,
        plugin_id="test-plugin-1",
        setting_key="max_iterations",
        setting_value="10",
        user_id=None,
        is_secret=False,
    )


@pytest.fixture
def sample_plugin_response():
    """Sample PluginResponse for testing"""
    return PluginResponse(
        id="test-plugin-1",
        name="Test Plugin",
        version="1.0.0",
        type="full-stack",
        enabled=True,
        manifest_path="/content/plugins/local/test-plugin/manifest.yml",
        description="A test plugin",
        author="Test Author",
        installed_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=datetime(2024, 1, 1, 12, 0, 0),
        state="enabled",
        error=None
    )


@pytest.fixture
def sample_plugin_hook_response():
    """Sample PluginHookResponse for testing"""
    return PluginHookResponse(
        id=1,
        plugin_id="test-plugin-1",
        hook_name="workbench.image_modal",
        hook_type="frontend",
        handler_path=None,
        component_path="/plugins/test-plugin/ImageModal.svelte",
        position="center",
        sort_order=0,
        plugin_name="Test Plugin",
        plugin_version="1.0.0"
    )


@pytest.fixture
def sample_plugin_manifest():
    """Sample PluginManifest for testing"""
    return PluginManifest(
        id="test-plugin-1",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        author="Test Author",
        plugin_type="full-stack",
        capabilities=["image-enhancement"],
        hooks={"pre_generation": "hooks/pre_generation.py"},
        manifest_path=Path("/content/plugins/local/test-plugin/manifest.yml"),
        plugin_dir=Path("/content/plugins/local/test-plugin"),
        source="local"
    )


# ========== Plugin Management Tests ==========

@pytest.mark.asyncio
async def test_list_plugins_success(controller, mock_plugin_repository, mock_plugin_registry, sample_plugin):
    """Test listing all plugins successfully"""
    # Arrange
    mock_plugin_repository.get_all_plugins.return_value = [sample_plugin]
    mock_plugin_registry.get_plugin_state.return_value = PluginState.ENABLED
    mock_plugin_registry.get_plugin_error.return_value = None

    # Act
    response = await controller.list_plugins()

    # Assert
    assert response.success is True
    assert len(response.data) == 1
    assert response.data[0]['id'] == "test-plugin-1"
    assert response.data[0]['state'] == "enabled"
    mock_plugin_repository.get_all_plugins.assert_called_once()


@pytest.mark.asyncio
async def test_list_plugins_with_error_state(controller, mock_plugin_repository, mock_plugin_registry, sample_plugin):
    """Test listing plugins when one is in error state"""
    # Arrange
    sample_plugin.enabled = False
    mock_plugin_repository.get_all_plugins.return_value = [sample_plugin]
    mock_plugin_registry.get_plugin_state.return_value = PluginState.ERROR
    mock_plugin_registry.get_plugin_error.return_value = "Failed to load hook handler"

    # Act
    response = await controller.list_plugins()

    # Assert
    assert response.success is True
    assert len(response.data) == 1
    assert response.data[0]['state'] == "error"
    assert response.data[0]['error'] == "Failed to load hook handler"


@pytest.mark.asyncio
async def test_list_plugins_exception(controller, mock_plugin_repository):
    """Test list plugins handles exceptions"""
    # Arrange
    mock_plugin_repository.get_all_plugins.side_effect = Exception("Database error")

    # Act & Assert
    with pytest.raises(Exception):
        await controller.list_plugins()


@pytest.mark.asyncio
async def test_get_plugin_success(
    controller, mock_plugin_repository, mock_plugin_registry, sample_plugin, sample_plugin_hook
):
    """Test getting a single plugin successfully"""
    # Arrange
    mock_plugin_repository.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_registry.get_plugin_state.return_value = PluginState.ENABLED
    mock_plugin_registry.get_plugin_error.return_value = None
    mock_plugin_repository.get_plugin_hooks.return_value = [sample_plugin_hook]

    mock_manifest = MagicMock()
    mock_manifest.settings = [{"name": "api_key", "type": "string", "label": "API Key"}]
    mock_manifest.category = "other"
    mock_manifest.tags = []
    mock_manifest.capabilities = []
    mock_manifest.source = "local"
    mock_manifest.homepage = None
    mock_manifest.repository = None
    mock_manifest.hooks = {}
    mock_manifest.frontend_hooks = []
    mock_plugin_registry.get_plugin.return_value = mock_manifest
    mock_plugin_repository.get_plugin_settings.return_value = []

    # Act
    response = await controller.get_plugin("test-plugin-1")

    # Assert
    assert response.success is True
    assert response.data['id'] == "test-plugin-1"
    assert response.data['state'] == "enabled"
    assert len(response.data['hooks']) == 1
    assert 'settings_schema' in response.data
    assert 'settings_values' in response.data
    mock_plugin_repository.get_plugin_by_id.assert_called_once_with("test-plugin-1")


@pytest.mark.asyncio
async def test_get_plugin_not_found(controller, mock_plugin_repository):
    """Test getting a non-existent plugin"""
    from fastapi import HTTPException

    # Arrange
    mock_plugin_repository.get_plugin_by_id.return_value = None

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await controller.get_plugin("non-existent")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "plugin_not_found"


@pytest.mark.asyncio
async def test_enable_plugin_success(
    controller, mock_operations, mock_plugin_repository, mock_plugin_registry, sample_plugin_response
):
    """Test enabling a plugin successfully"""
    # Arrange
    mock_operations.enable_plugin.return_value = sample_plugin_response

    # Act
    response = await controller.enable_plugin("test-plugin-1")

    # Assert
    assert response.success is True
    assert "enabled successfully" in response.message
    mock_operations.enable_plugin.assert_called_once_with(
        mock_plugin_repository, mock_plugin_registry, "test-plugin-1",
        preset_loader=None, pipe_catalog=None, recipe_catalog=None,
    )


@pytest.mark.asyncio
async def test_enable_plugin_not_found(controller, mock_operations):
    """Test enabling a non-existent plugin"""
    from fastapi import HTTPException

    # Arrange
    mock_operations.enable_plugin.side_effect = ValueError("Plugin 'non-existent' not found")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await controller.enable_plugin("non-existent")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "plugin_not_found"


@pytest.mark.asyncio
async def test_enable_plugin_registry_failure(controller, mock_operations):
    """Test enabling plugin when registry fails"""
    from fastapi import HTTPException

    # Arrange
    mock_operations.enable_plugin.side_effect = ValueError("Failed to enable plugin in registry: Missing dependencies")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await controller.enable_plugin("test-plugin-1")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "plugin_enable_failed"
    assert "Missing dependencies" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_disable_plugin_success(
    controller, mock_operations, mock_plugin_repository, mock_plugin_registry, sample_plugin_response
):
    """Test disabling a plugin successfully"""
    # Arrange
    sample_plugin_response.enabled = False
    sample_plugin_response.state = "disabled"
    mock_operations.disable_plugin.return_value = sample_plugin_response

    # Act
    response = await controller.disable_plugin("test-plugin-1")

    # Assert
    assert response.success is True
    assert "disabled successfully" in response.message
    mock_operations.disable_plugin.assert_called_once_with(
        mock_plugin_repository, mock_plugin_registry, "test-plugin-1",
        preset_loader=None, pipe_catalog=None, recipe_catalog=None,
    )


@pytest.mark.asyncio
async def test_disable_plugin_not_found(controller, mock_operations):
    """Test disabling a non-existent plugin"""
    from fastapi import HTTPException

    # Arrange
    mock_operations.disable_plugin.side_effect = ValueError("Plugin 'non-existent' not found")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await controller.disable_plugin("non-existent")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "plugin_not_found"


@pytest.mark.asyncio
async def test_delete_plugin_success(controller, mock_operations, mock_plugin_repository, mock_plugin_registry):
    """Test deleting a plugin successfully"""
    # Arrange
    mock_operations.delete_plugin.return_value = "Test Plugin"

    # Act
    response = await controller.delete_plugin("test-plugin-1")

    # Assert
    assert response.success is True
    assert "deleted successfully" in response.message
    mock_operations.delete_plugin.assert_called_once_with(
        mock_plugin_repository, mock_plugin_registry, "test-plugin-1",
        preset_loader=None, pipe_catalog=None, recipe_catalog=None,
    )


@pytest.mark.asyncio
async def test_delete_plugin_not_found(controller, mock_operations):
    """Test deleting a non-existent plugin"""
    from fastapi import HTTPException

    # Arrange
    mock_operations.delete_plugin.side_effect = ValueError("Plugin 'non-existent' not found")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await controller.delete_plugin("non-existent")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "plugin_not_found"


@pytest.mark.asyncio
async def test_scan_plugins_new_plugin(controller, mock_operations, sample_plugin_response):
    """Test scanning for plugins and discovering a new one"""
    # Arrange
    scan_result = PluginScanResult(
        new_plugins=[sample_plugin_response],
        updated_plugins=[],
        total_discovered=1
    )
    mock_operations.scan_plugins.return_value = scan_result

    # Act
    response = await controller.scan_plugins()

    # Assert
    assert response.success is True
    assert len(response.data['new_plugins']) == 1
    assert len(response.data['updated_plugins']) == 0
    assert response.data['total_discovered'] == 1
    mock_operations.scan_plugins.assert_called_once()


@pytest.mark.asyncio
async def test_scan_plugins_updated_version(controller, mock_operations, sample_plugin_response):
    """Test scanning for plugins and detecting version update"""
    # Arrange
    updated_plugin = sample_plugin_response.model_copy()
    updated_plugin.version = "1.1.0"
    scan_result = PluginScanResult(
        new_plugins=[],
        updated_plugins=[updated_plugin],
        total_discovered=1
    )
    mock_operations.scan_plugins.return_value = scan_result

    # Act
    response = await controller.scan_plugins()

    # Assert
    assert response.success is True
    assert len(response.data['new_plugins']) == 0
    assert len(response.data['updated_plugins']) == 1
    assert response.data['updated_plugins'][0]['version'] == "1.1.0"


# ========== Plugin Settings Tests ==========

@pytest.mark.asyncio
async def test_get_plugin_settings_success(
    controller, mock_plugin_repository, sample_plugin, sample_plugin_setting
):
    """Test getting plugin settings successfully"""
    # Arrange
    mock_plugin_repository.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repository.get_plugin_settings.return_value = [sample_plugin_setting]

    # Act
    response = await controller.get_plugin_settings("test-plugin-1", user_id=None)

    # Assert
    assert response.success is True
    assert len(response.data) == 1
    assert response.data[0]['setting_key'] == "max_iterations"
    mock_plugin_repository.get_plugin_settings.assert_called_once_with("test-plugin-1", None)


@pytest.mark.asyncio
async def test_get_plugin_settings_user_specific(
    controller, mock_plugin_repository, sample_plugin, sample_plugin_setting
):
    """Test getting user-specific plugin settings"""
    # Arrange
    sample_plugin_setting.user_id = "user-123"
    mock_plugin_repository.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repository.get_plugin_settings.return_value = [sample_plugin_setting]

    # Act
    response = await controller.get_plugin_settings("test-plugin-1", user_id="user-123")

    # Assert
    assert response.success is True
    assert len(response.data) == 1
    mock_plugin_repository.get_plugin_settings.assert_called_once_with("test-plugin-1", "user-123")


@pytest.mark.asyncio
async def test_get_plugin_settings_plugin_not_found(controller, mock_plugin_repository):
    """Test getting settings for non-existent plugin"""
    from fastapi import HTTPException

    # Arrange
    mock_plugin_repository.get_plugin_by_id.return_value = None

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await controller.get_plugin_settings("non-existent")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "plugin_not_found"


@pytest.mark.asyncio
async def test_update_plugin_settings_success(controller, mock_operations, mock_plugin_repository, mock_plugin_registry):
    """Test updating plugin settings successfully"""
    # Arrange
    setting_response = PluginSettingResponse(
        id=1,
        plugin_id="test-plugin-1",
        setting_key="max_iterations",
        setting_value="10",
        user_id=None,
        is_secret=False
    )
    mock_operations.update_plugin_settings.return_value = [setting_response, setting_response]

    settings_request = PluginSettingsUpdateRequest(
        settings={
            "max_iterations": "20",
            "enable_feature": "true"
        }
    )

    # Act
    response = await controller.update_plugin_settings("test-plugin-1", settings_request, user_id=None)

    # Assert
    assert response.success is True
    assert len(response.data) == 2
    assert "Updated 2 settings" in response.message
    mock_operations.update_plugin_settings.assert_called_once_with(
        mock_plugin_repository,
        mock_plugin_registry,
        "test-plugin-1",
        {"max_iterations": "20", "enable_feature": "true"},
        None,
        actor_user_id=None,
        actor_username=None
    )


@pytest.mark.asyncio
async def test_update_plugin_settings_with_complex_types(controller, mock_operations):
    """Test updating plugin settings with dict and list values"""
    # Arrange
    setting_response = PluginSettingResponse(
        id=1,
        plugin_id="test-plugin-1",
        setting_key="max_iterations",
        setting_value="10",
        user_id=None,
        is_secret=False
    )
    mock_operations.update_plugin_settings.return_value = [setting_response, setting_response]

    settings_request = PluginSettingsUpdateRequest(
        settings={
            "colors": ["red", "blue", "green"],
            "config": {"enabled": True, "timeout": 30}
        }
    )

    # Act
    response = await controller.update_plugin_settings("test-plugin-1", settings_request)

    # Assert
    assert response.success is True
    # Manager receives the complex types and serializes them
    mock_operations.update_plugin_settings.assert_called_once()


@pytest.mark.asyncio
async def test_update_plugin_settings_plugin_not_found(controller, mock_operations):
    """Test updating settings for non-existent plugin"""
    from fastapi import HTTPException

    # Arrange
    mock_operations.update_plugin_settings.side_effect = ValueError("Plugin 'non-existent' not found")

    settings_request = PluginSettingsUpdateRequest(
        settings={"key": "value"}
    )

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await controller.update_plugin_settings("non-existent", settings_request)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "plugin_not_found"


# ========== Hooks Catalog Tests ==========

@pytest.mark.asyncio
async def test_get_hooks_catalog_success(controller, mock_operations):
    """Test that the catalog endpoint returns the manager's list under `data`"""
    mock_operations.get_hooks_catalog.return_value = [
        {
            "name": "generation.before_start",
            "type": "backend",
            "description": "Fires before generation starts",
            "payload": {"generation_id": {"type": "str", "description": "The generation id"}},
            "mutable": ["form_data"],
            "use_when": ["Rewrite form data before the pipeline is built"],
            "example": "",
        },
        {
            "name": "workbench.actions",
            "type": "frontend",
            "description": "",
            "payload": {},
            "mutable": [],
            "use_when": [],
            "example": "",
        },
    ]

    response = await controller.get_hooks_catalog()

    assert response.success is True
    assert len(response.data) == 2
    entry = response.data[0]
    assert set(entry.keys()) == {"name", "type", "description", "payload", "mutable", "use_when", "example"}
    assert entry["mutable"] == ["form_data"]


@pytest.mark.asyncio
async def test_get_hooks_catalog_exception(controller, mock_operations):
    """Test that the catalog endpoint handles manager exceptions gracefully"""
    from fastapi import HTTPException

    mock_operations.get_hooks_catalog.side_effect = Exception("boom")

    with pytest.raises(HTTPException) as exc_info:
        await controller.get_hooks_catalog()

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["error"] == "hooks_catalog_get_failed"


# ========== Frontend Hooks Tests ==========

@pytest.mark.asyncio
async def test_get_frontend_hooks_success(controller, mock_plugin_repository, sample_plugin, sample_plugin_hook):
    """Test getting frontend hooks successfully"""
    # Arrange
    plugin2 = Plugin(
        id="test-plugin-2",
        name="Another Plugin",
        version="2.0.0",
        type="full-stack",
        enabled=True,
        manifest_path="/content/plugins/local/test-plugin-2/manifest.yml"
    )
    hook2 = PluginHook(
        id=2,
        plugin_id="test-plugin-2",
        hook_name="workbench.image_modal",
        hook_type="frontend",
        handler_path=None,
        component_path="/plugins/test-plugin-2/AnotherModal.svelte",
        position="bottom",
        sort_order=1,
    )

    mock_plugin_repository.get_hooks_by_type.return_value = [sample_plugin_hook, hook2]
    mock_plugin_repository.get_plugin_by_id.side_effect = [sample_plugin, plugin2]

    # Act
    response = await controller.get_frontend_hooks()

    # Assert
    assert response.success is True
    assert "workbench.image_modal" in response.data
    assert len(response.data["workbench.image_modal"]) == 2
    assert response.data["workbench.image_modal"][0]['plugin_name'] == "Test Plugin"
    assert response.data["workbench.image_modal"][1]['plugin_name'] == "Another Plugin"


@pytest.mark.asyncio
async def test_get_frontend_hooks_grouped_by_name(
    controller, mock_plugin_repository, sample_plugin, sample_plugin_hook
):
    """Test that frontend hooks are properly grouped by hook name"""
    # Arrange
    panel_hook = PluginHook(
        id=2,
        plugin_id="test-plugin-1",
        hook_name="generation.panel",
        hook_type="frontend",
        handler_path=None,
        component_path="/plugins/test-plugin/Panel.svelte",
        position="left",
        sort_order=0,
    )

    mock_plugin_repository.get_hooks_by_type.return_value = [sample_plugin_hook, panel_hook]
    mock_plugin_repository.get_plugin_by_id.return_value = sample_plugin

    # Act
    response = await controller.get_frontend_hooks()

    # Assert
    assert response.success is True
    assert "workbench.image_modal" in response.data
    assert "generation.panel" in response.data
    assert len(response.data["workbench.image_modal"]) == 1
    assert len(response.data["generation.panel"]) == 1


@pytest.mark.asyncio
async def test_get_frontend_hooks_empty(controller, mock_plugin_repository):
    """Test getting frontend hooks when none exist"""
    # Arrange
    mock_plugin_repository.get_hooks_by_type.return_value = []

    # Act
    response = await controller.get_frontend_hooks()

    # Assert
    assert response.success is True
    assert response.data == {}


@pytest.mark.asyncio
async def test_get_frontend_hooks_exception(controller, mock_plugin_repository):
    """Test get frontend hooks handles exceptions"""
    # Arrange
    mock_plugin_repository.get_hooks_by_type.side_effect = Exception("Database error")

    # Act & Assert
    with pytest.raises(Exception):
        await controller.get_frontend_hooks()


# ========== Frontend Extensions Tests ==========

@pytest.mark.asyncio
async def test_get_frontend_extensions_success(controller, mock_operations):
    """Test getting frontend extensions (renderers + contributions) successfully"""
    # Arrange
    mock_operations.get_frontend_extensions.return_value = {
        "renderers": [
            {"plugin_id": "test-plugin-1", "kind": "history.artifact", "key": "fake_artifact", "component": "FakeArtifact.svelte"}
        ],
        "contributions": [
            {"plugin_id": "test-plugin-1", "slot": "admin.tabs", "component": "AdminTab.svelte",
             "label": "Fake Tab", "icon": None, "route": None, "order": 50, "require_role": None}
        ],
    }

    # Act
    response = await controller.get_frontend_extensions()

    # Assert
    assert response.success is True
    assert len(response.data["renderers"]) == 1
    assert response.data["renderers"][0]["kind"] == "history.artifact"
    assert len(response.data["contributions"]) == 1
    assert response.data["contributions"][0]["slot"] == "admin.tabs"


@pytest.mark.asyncio
async def test_get_frontend_extensions_empty(controller, mock_operations):
    """Test getting frontend extensions when no plugin declares any"""
    # Arrange
    mock_operations.get_frontend_extensions.return_value = {"renderers": [], "contributions": []}

    # Act
    response = await controller.get_frontend_extensions()

    # Assert
    assert response.success is True
    assert response.data == {"renderers": [], "contributions": []}


@pytest.mark.asyncio
async def test_get_frontend_extensions_exception(controller, mock_operations):
    """Test get frontend extensions handles exceptions"""
    # Arrange
    mock_operations.get_frontend_extensions.side_effect = Exception("Database error")

    # Act & Assert
    with pytest.raises(Exception):
        await controller.get_frontend_extensions()


# ========== Plugin Assets Tests ==========

@pytest.mark.asyncio
async def test_get_plugin_asset_success(controller, mock_plugin_registry, sample_plugin_manifest, tmp_path):
    """Test serving plugin asset successfully"""
    # Arrange
    # Create a temporary asset file
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    asset_file = dist_dir / "ImageModalAction.js"
    asset_file.write_text("export default class ImageModal {}")

    # Update manifest to point to temp directory
    sample_plugin_manifest.plugin_dir = tmp_path
    mock_plugin_registry.get_plugin.return_value = sample_plugin_manifest

    # Act
    response = await controller.get_plugin_asset("test-plugin-1", "ImageModalAction.js")

    # Assert
    assert response is not None
    # FileResponse doesn't have success attribute, but it should return a valid response
    from fastapi.responses import FileResponse
    assert isinstance(response, FileResponse)


@pytest.mark.asyncio
async def test_get_plugin_asset_is_revalidated_on_every_load(controller, mock_plugin_registry, sample_plugin_manifest, tmp_path):
    """A rebuilt bundle must reach the browser on the next page load - a
    module URL with no cache policy is heuristically cached for hours."""
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "TextImportModal.js").write_text("export default function () {}")
    sample_plugin_manifest.plugin_dir = tmp_path
    mock_plugin_registry.get_plugin.return_value = sample_plugin_manifest

    response = await controller.get_plugin_asset("test-plugin-1", "TextImportModal.js")

    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
async def test_get_plugin_asset_plugin_not_found(controller, mock_plugin_registry):
    """Test serving asset for non-existent plugin"""
    from fastapi import HTTPException

    # Arrange
    mock_plugin_registry.get_plugin.return_value = None

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await controller.get_plugin_asset("non-existent", "asset.js")

    assert exc_info.value.status_code == 404
    assert "not found" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_get_plugin_asset_file_not_found(controller, mock_plugin_registry, sample_plugin_manifest, tmp_path):
    """Test serving non-existent asset file"""
    from fastapi import HTTPException

    # Arrange
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)

    sample_plugin_manifest.plugin_dir = tmp_path
    mock_plugin_registry.get_plugin.return_value = sample_plugin_manifest

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await controller.get_plugin_asset("test-plugin-1", "NonExistent.js")

    assert exc_info.value.status_code == 404
    assert "not found" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_get_plugin_asset_directory_traversal_blocked(controller, mock_plugin_registry, sample_plugin_manifest, tmp_path):
    """Test that directory traversal attempts are blocked"""
    from fastapi import HTTPException

    # Arrange
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)

    sample_plugin_manifest.plugin_dir = tmp_path
    mock_plugin_registry.get_plugin.return_value = sample_plugin_manifest

    # Act & Assert - Try to access file outside dist directory
    with pytest.raises(HTTPException) as exc_info:
        await controller.get_plugin_asset("test-plugin-1", "../../etc/passwd")

    assert exc_info.value.status_code in [403, 404]  # Either forbidden or not found


@pytest.mark.asyncio
async def test_get_plugin_asset_correct_mime_types(controller, mock_plugin_registry, sample_plugin_manifest, tmp_path):
    """Test that correct MIME types are returned for different file types"""
    # Arrange
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)

    # Create different file types
    js_file = dist_dir / "component.js"
    js_file.write_text("export default {}")

    css_file = dist_dir / "styles.css"
    css_file.write_text("body { color: red; }")

    sample_plugin_manifest.plugin_dir = tmp_path
    mock_plugin_registry.get_plugin.return_value = sample_plugin_manifest

    # Act - Test JS file
    js_response = await controller.get_plugin_asset("test-plugin-1", "component.js")
    assert js_response.media_type == "application/javascript"

    # Act - Test CSS file
    css_response = await controller.get_plugin_asset("test-plugin-1", "styles.css")
    assert css_response.media_type == "text/css"


@pytest.mark.asyncio
async def test_get_plugin_asset_sibling_directory_prefix_is_blocked(
    controller, mock_plugin_registry, sample_plugin_manifest, tmp_path
):
    """A sibling whose name merely *extends* the allowed directory is not inside it.

    The containment check used to be `str(asset).startswith(str(allowed))`, which
    compares strings, not paths: for base `.../frontend/dist`, the resolved path
    `.../frontend/dist-private/secrets.json` starts with the base and sailed
    through. `../dist-private/secrets.json` reaches it without a single `..`
    surviving `resolve()`, so the existing traversal test - which only tries
    `../../etc/passwd`, a path that leaves the prefix entirely - never caught it.
    """
    from fastapi import HTTPException

    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)

    # A sibling directory whose name starts with the allowed one.
    sibling = tmp_path / "frontend" / "dist-private"
    sibling.mkdir()
    (sibling / "secrets.json").write_text('{"api_key": "sk-live-leaked"}')

    sample_plugin_manifest.plugin_dir = tmp_path
    mock_plugin_registry.get_plugin.return_value = sample_plugin_manifest

    with pytest.raises(HTTPException) as exc_info:
        await controller.get_plugin_asset("test-plugin-1", "../dist-private/secrets.json")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_plugin_asset_sibling_prefix_file_is_blocked(
    controller, mock_plugin_registry, sample_plugin_manifest, tmp_path
):
    """The same string-prefix hole one level up: a *file* named like the base."""
    from fastapi import HTTPException

    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (tmp_path / "frontend" / "dist.bak").write_text("stale bundle with a token")

    sample_plugin_manifest.plugin_dir = tmp_path
    mock_plugin_registry.get_plugin.return_value = sample_plugin_manifest

    with pytest.raises(HTTPException) as exc_info:
        await controller.get_plugin_asset("test-plugin-1", "../dist.bak")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_plugin_asset_absolute_path_is_blocked(
    controller, mock_plugin_registry, sample_plugin_manifest, tmp_path
):
    """`Path(base) / "/etc/passwd"` is `/etc/passwd` - the base is discarded."""
    from fastapi import HTTPException

    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)

    sample_plugin_manifest.plugin_dir = tmp_path
    mock_plugin_registry.get_plugin.return_value = sample_plugin_manifest

    with pytest.raises(HTTPException) as exc_info:
        await controller.get_plugin_asset("test-plugin-1", "/etc/passwd")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_plugin_asset_nested_legitimate_path_still_serves(
    controller, mock_plugin_registry, sample_plugin_manifest, tmp_path
):
    """Containment must not become "flat directory only" - real bundles nest."""
    from fastapi.responses import FileResponse

    nested = tmp_path / "frontend" / "dist" / "chunks"
    nested.mkdir(parents=True)
    (nested / "vendor.js").write_text("export default {}")

    sample_plugin_manifest.plugin_dir = tmp_path
    mock_plugin_registry.get_plugin.return_value = sample_plugin_manifest

    response = await controller.get_plugin_asset("test-plugin-1", "chunks/vendor.js")

    assert isinstance(response, FileResponse)


@pytest.mark.asyncio
async def test_update_settings_manifest_unavailable_is_409_not_404(
    controller, mock_operations
):
    """A missing manifest is not a missing plugin, and not a server bug either.

    409 tells the admin UI this is retryable after a reload, and distinguishes
    it from the 404 a genuinely unknown plugin id gets.
    """
    from src.features.plugins.operations import PluginManifestUnavailableError
    from src.features.plugins.dto import PluginSettingsUpdateRequest
    from fastapi import HTTPException

    mock_operations.update_plugin_settings.side_effect = (
        PluginManifestUnavailableError("no readable manifest")
    )

    with pytest.raises(HTTPException) as exc_info:
        await controller.update_plugin_settings(
            "acme-provider",
            PluginSettingsUpdateRequest(settings={"api_key": "sk-live-abc"}),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail['error'] == 'plugin_manifest_unavailable'
    assert "sk-live-abc" not in str(exc_info.value.detail)
