"""
Unit tests for src.features.plugins.operations.
"""
import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime

from src.features.plugins import operations
from src.features.plugins.dto import (
    PluginResponse,
    PluginScanResult,
)
from src.features.plugins.mappers import plugin_to_response
from src.features.plugins.records import Plugin, PluginSetting, PluginHook
from src.platform.plugins.registry import PluginState
from src.platform.plugins.loader import PluginManifest
from src.platform.plugins.hooks import hooks_registry
from pathlib import Path


@pytest.fixture
def mock_plugin_repo():
    """Mock PluginRepository"""
    return Mock()


@pytest.fixture
def mock_plugin_registry():
    """Mock PluginRegistry"""
    registry = Mock()
    # Default: no manifest loaded for a plugin (matches real get_plugin() behavior
    # when a plugin's manifest failed to load / isn't registered). Individual tests
    # override this with a concrete PluginManifest/MagicMock when they need
    # manifest-derived enrichment (category, tags, hook_count, settings_count, ...).
    registry.get_plugin.return_value = None
    return registry


@pytest.fixture
def sample_plugin():
    """Sample plugin for testing"""
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
        updated_at=datetime(2024, 1, 1, 12, 0, 0)
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


@pytest.fixture
def sample_plugin_setting():
    """Sample plugin setting for testing"""
    return PluginSetting(
        id=1,
        plugin_id="test-plugin-1",
        setting_key="max_iterations",
        setting_value="10",
        user_id=None,
        is_secret=False
    )


@pytest.fixture
def sample_plugin_hook():
    """Sample plugin hook for testing"""
    return PluginHook(
        id=1,
        plugin_id="test-plugin-1",
        hook_name="workbench.image_modal",
        hook_type="frontend",
        handler_path=None,
        component_path="/plugins/test-plugin/ImageModal.svelte",
        position="center",
        sort_order=0
    )


# ========== enable_plugin Tests ==========

def test_enable_plugin_success(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """Test enabling a plugin successfully"""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.enable_plugin.return_value = True
    mock_plugin_registry.enable_plugin.return_value = True
    mock_plugin_registry.get_plugin_state.return_value = PluginState.ENABLED
    mock_plugin_registry.get_plugin_error.return_value = None

    # Act
    result = operations.enable_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")

    # Assert
    assert isinstance(result, PluginResponse)
    mock_plugin_repo.enable_plugin.assert_called_once_with("test-plugin-1")
    mock_plugin_registry.enable_plugin.assert_called_once_with("test-plugin-1")


def test_enable_plugin_fires_enable_then_boot(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """A runtime enable owes the plugin both lifecycle events, in that order:
    `enable` is the transition, `boot` the per-process init it would otherwise
    only get on the next restart."""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.enable_plugin.return_value = True
    mock_plugin_registry.enable_plugin.return_value = True
    mock_plugin_registry.get_plugin_state.return_value = PluginState.ENABLED
    mock_plugin_registry.get_plugin_error.return_value = None

    # Act
    operations.enable_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")

    # Assert
    lifecycle_calls = [
        call for call in mock_plugin_registry.mock_calls
        if call[0] in ("hook_chain.execute", "run_boot_hook")
    ]
    assert [call[0] for call in lifecycle_calls] == ["hook_chain.execute", "run_boot_hook"]
    assert lifecycle_calls[0][1][0] == "plugin.lifecycle.enable"
    assert lifecycle_calls[0][2]["initial_data"] == {"plugin_id": "test-plugin-1"}
    assert lifecycle_calls[1][1] == ("test-plugin-1",)


def test_disable_plugin_does_not_fire_boot(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """Disabling is unchanged - `disable` only, no boot."""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_registry.disable_plugin.return_value = True
    mock_plugin_repo.disable_plugin.return_value = True
    mock_plugin_registry.get_plugin_state.return_value = PluginState.DISABLED
    mock_plugin_registry.get_plugin_error.return_value = None

    # Act
    operations.disable_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")

    # Assert
    mock_plugin_registry.run_boot_hook.assert_not_called()
    mock_plugin_registry.hook_chain.execute.assert_called_once()
    assert mock_plugin_registry.hook_chain.execute.call_args[0][0] == "plugin.lifecycle.disable"


def test_enable_plugin_not_found_raises(mock_plugin_repo, mock_plugin_registry):
    """Test that enable_plugin raises ValueError when plugin not found"""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = None

    # Act & Assert
    with pytest.raises(ValueError) as exc_info:
        operations.enable_plugin(mock_plugin_repo, mock_plugin_registry, "non-existent")

    assert "not found" in str(exc_info.value).lower()


def test_enable_plugin_rollback_on_registry_failure(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """Test that enable_plugin rolls back DB change when registry fails"""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.enable_plugin.return_value = True
    mock_plugin_registry.enable_plugin.return_value = False
    mock_plugin_registry.get_plugin_error.return_value = "Missing dependencies"
    mock_plugin_repo.disable_plugin.return_value = True

    # Act & Assert
    with pytest.raises(ValueError) as exc_info:
        operations.enable_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")

    assert "Missing dependencies" in str(exc_info.value)
    mock_plugin_repo.disable_plugin.assert_called_once_with("test-plugin-1")


def test_enable_plugin_db_failure_raises(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """Test that enable_plugin raises ValueError when DB enable fails"""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.enable_plugin.return_value = False

    # Act & Assert
    with pytest.raises(ValueError) as exc_info:
        operations.enable_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")

    assert "database" in str(exc_info.value).lower()


def test_enable_plugin_declares_provides_hooks_object_form(
    mock_plugin_repo, mock_plugin_registry, sample_plugin
):
    """Test that enable_plugin declares provides_hooks (string and object form) into the registry"""
    manifest = PluginManifest(
        id="test-plugin-1",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        author="Test Author",
        plugin_type="full-stack",
        manifest_path=Path("/content/plugins/local/test-plugin/manifest.yml"),
        plugin_dir=Path("/content/plugins/local/test-plugin"),
        source="local",
        provides_hooks=[
            {"name": "test_plugin.simple_event", "description": "", "payload": {}, "mutable": [], "use_when": [], "example": ""},
            {
                "name": "test_plugin.custom_event",
                "description": "Fires on a custom condition",
                "payload": {"value": {"type": "int", "description": "The value"}},
                "mutable": ["value"],
                "use_when": ["Adjust the value before it is used"],
                "example": "example snippet",
            },
        ],
    )

    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.enable_plugin.return_value = True
    mock_plugin_registry.enable_plugin.return_value = True
    mock_plugin_registry.get_plugin.return_value = manifest
    mock_plugin_registry.get_plugin_state.return_value = PluginState.ENABLED
    mock_plugin_registry.get_plugin_error.return_value = None

    operations.enable_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")

    simple_spec = hooks_registry.get("test_plugin.simple_event")
    assert simple_spec is not None
    assert simple_spec.type == "backend"

    custom_spec = hooks_registry.get("test_plugin.custom_event")
    assert custom_spec is not None
    assert custom_spec.description == "Fires on a custom condition"
    assert custom_spec.payload == {"value": {"type": "int", "description": "The value"}}
    assert custom_spec.mutable == ("value",)
    assert custom_spec.use_when == ("Adjust the value before it is used",)
    assert custom_spec.example == "example snippet"


# ========== get_hooks_catalog Tests ==========

def test_get_hooks_catalog_shape():
    """Test that get_hooks_catalog returns the stable, fully-keyed shape"""
    hooks_registry.declare_one(
        "catalog_test.documented_hook",
        "backend",
        description="A documented hook",
        payload={"value": {"type": "int", "description": "The value"}},
        mutable=["value"],
        use_when=["Do the thing"],
        example="ex",
    )
    hooks_registry.declare_one("catalog_test.bare_hook", "frontend")

    catalog = operations.get_hooks_catalog()
    by_name = {entry["name"]: entry for entry in catalog}

    documented = by_name["catalog_test.documented_hook"]
    assert documented["type"] == "backend"
    assert documented["description"] == "A documented hook"
    assert documented["payload"] == {"value": {"type": "int", "description": "The value"}}
    assert documented["mutable"] == ["value"]
    assert documented["use_when"] == ["Do the thing"]
    assert documented["example"] == "ex"

    bare = by_name["catalog_test.bare_hook"]
    assert bare["type"] == "frontend"
    assert bare["description"] == ""
    assert bare["payload"] == {}
    assert bare["mutable"] == []
    assert bare["use_when"] == []
    assert bare["example"] == ""


# ========== disable_plugin Tests ==========

def test_disable_plugin_success(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """Test disabling a plugin successfully"""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_registry.disable_plugin.return_value = True
    mock_plugin_repo.disable_plugin.return_value = True
    mock_plugin_registry.get_plugin_state.return_value = PluginState.DISABLED
    mock_plugin_registry.get_plugin_error.return_value = None

    # Act
    result = operations.disable_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")

    # Assert
    assert isinstance(result, PluginResponse)
    mock_plugin_registry.disable_plugin.assert_called_once_with("test-plugin-1")
    mock_plugin_repo.disable_plugin.assert_called_once_with("test-plugin-1")


def test_disable_plugin_not_found_raises(mock_plugin_repo, mock_plugin_registry):
    """Test that disable_plugin raises ValueError when plugin not found"""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = None

    # Act & Assert
    with pytest.raises(ValueError) as exc_info:
        operations.disable_plugin(mock_plugin_repo, mock_plugin_registry, "non-existent")

    assert "not found" in str(exc_info.value).lower()


def test_disable_plugin_registry_failure_raises(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """Test that disable_plugin raises ValueError when registry disable fails"""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_registry.disable_plugin.return_value = False

    # Act & Assert
    with pytest.raises(ValueError) as exc_info:
        operations.disable_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")

    assert "registry" in str(exc_info.value).lower()


# ========== delete_plugin Tests ==========

def test_delete_plugin_success(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """Test deleting a plugin successfully"""
    # Arrange
    sample_plugin.enabled = False
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.delete_plugin.return_value = True

    # Act
    result = operations.delete_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")

    # Assert
    assert result == "Test Plugin"
    mock_plugin_repo.delete_plugin.assert_called_once_with("test-plugin-1")


def test_delete_plugin_disables_if_enabled(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """Test that delete_plugin disables the plugin first if it's enabled"""
    # Arrange
    sample_plugin.enabled = True
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.delete_plugin.return_value = True

    # Act
    result = operations.delete_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")

    # Assert
    assert result == "Test Plugin"
    mock_plugin_registry.disable_plugin.assert_called_once_with("test-plugin-1")
    mock_plugin_repo.delete_plugin.assert_called_once_with("test-plugin-1")


def test_delete_plugin_not_found_raises(mock_plugin_repo, mock_plugin_registry):
    """Test that delete_plugin raises ValueError when plugin not found"""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = None

    # Act & Assert
    with pytest.raises(ValueError) as exc_info:
        operations.delete_plugin(mock_plugin_repo, mock_plugin_registry, "non-existent")

    assert "not found" in str(exc_info.value).lower()


# ========== scan_plugins Tests ==========

def test_scan_plugins_discovers_new(mock_plugin_repo, mock_plugin_registry, sample_plugin_manifest):
    """Test that scan_plugins discovers new plugins"""
    # Arrange
    mock_plugin_registry.get_all_plugins.return_value = [sample_plugin_manifest]
    mock_plugin_repo.get_all_plugins.return_value = []  # No existing plugins

    created_plugin = Plugin(
        id=sample_plugin_manifest.id,
        name=sample_plugin_manifest.name,
        version=sample_plugin_manifest.version,
        type=sample_plugin_manifest.plugin_type,
        enabled=False,
        manifest_path=str(sample_plugin_manifest.manifest_path),
        description=sample_plugin_manifest.description,
        author=sample_plugin_manifest.author,
        installed_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    mock_plugin_repo.create_plugin.return_value = created_plugin
    mock_plugin_registry.get_plugin_state.return_value = None
    mock_plugin_registry.get_plugin_error.return_value = None

    # Act
    result = operations.scan_plugins(mock_plugin_repo, mock_plugin_registry)

    # Assert
    assert isinstance(result, PluginScanResult)
    assert len(result.new_plugins) == 1
    assert len(result.updated_plugins) == 0
    assert result.total_discovered == 1
    mock_plugin_registry.discover_plugins.assert_called_once()
    mock_plugin_repo.create_plugin.assert_called_once()


def test_scan_plugins_updates_version(mock_plugin_repo, mock_plugin_registry, sample_plugin, sample_plugin_manifest):
    """Test that scan_plugins detects version updates"""
    # Arrange
    sample_plugin.version = "0.9.0"  # Old version
    sample_plugin_manifest.version = "1.0.0"  # New version

    mock_plugin_registry.get_all_plugins.return_value = [sample_plugin_manifest]
    mock_plugin_repo.get_all_plugins.return_value = [sample_plugin]

    updated_plugin = Plugin(
        id=sample_plugin.id,
        name=sample_plugin.name,
        version="1.0.0",
        type=sample_plugin.type,
        enabled=sample_plugin.enabled,
        manifest_path=sample_plugin.manifest_path,
        description=sample_plugin.description,
        author=sample_plugin.author,
        installed_at=sample_plugin.installed_at,
        updated_at=datetime.utcnow()
    )
    mock_plugin_repo.update_plugin.return_value = updated_plugin
    mock_plugin_registry.get_plugin_state.return_value = PluginState.ENABLED
    mock_plugin_registry.get_plugin_error.return_value = None

    # Act
    result = operations.scan_plugins(mock_plugin_repo, mock_plugin_registry)

    # Assert
    assert len(result.new_plugins) == 0
    assert len(result.updated_plugins) == 1
    assert result.updated_plugins[0].version == "1.0.0"
    mock_plugin_repo.update_plugin.assert_called_once()


def test_scan_plugins_refreshes_hooks(mock_plugin_repo, mock_plugin_registry, sample_plugin, sample_plugin_manifest):
    """Test that scan_plugins always refreshes hooks for existing plugins"""
    # Arrange
    sample_plugin.version = sample_plugin_manifest.version  # Same version

    mock_plugin_registry.get_all_plugins.return_value = [sample_plugin_manifest]
    mock_plugin_repo.get_all_plugins.return_value = [sample_plugin]

    # Act
    result = operations.scan_plugins(mock_plugin_repo, mock_plugin_registry)

    # Assert
    # Even with same version, hooks should be refreshed
    mock_plugin_repo.clear_plugin_hooks.assert_called_once_with(sample_plugin_manifest.id)
    assert len(result.updated_plugins) == 0  # No version change


# ========== Settings Tests ==========

def test_update_settings_serializes_complex_types(
    mock_plugin_repo, mock_plugin_registry, sample_plugin, sample_plugin_setting
):
    """Test that update_plugin_settings serializes dict and list values"""
    # Arrange
    # A manifest has to be present: without one the operation cannot tell a
    # credential from an ordinary setting and refuses the save outright.
    manifest = Mock()
    manifest.settings = []
    mock_plugin_registry.get_plugin.return_value = manifest
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.set_plugin_setting.return_value = sample_plugin_setting

    settings = {
        "colors": ["red", "blue", "green"],
        "config": {"enabled": True, "timeout": 30}
    }

    # Act
    result = operations.update_plugin_settings(mock_plugin_repo, mock_plugin_registry, "test-plugin-1", settings)

    # Assert
    assert len(result) == 2
    calls = mock_plugin_repo.set_plugin_setting.call_args_list
    # Check that lists and dicts are JSON serialized
    for call in calls:
        _, kwargs = call
        value = kwargs.get('setting_value') or call[0][2]  # Handle both positional and keyword args
        assert isinstance(value, str)


def test_update_settings_not_found_raises(mock_plugin_repo, mock_plugin_registry):
    """Test that update_plugin_settings raises ValueError when plugin not found"""
    # Arrange
    mock_plugin_repo.get_plugin_by_id.return_value = None

    # Act & Assert
    with pytest.raises(ValueError) as exc_info:
        operations.update_plugin_settings(mock_plugin_repo, mock_plugin_registry, "non-existent", {"key": "value"})

    assert "not found" in str(exc_info.value).lower()


def test_get_frontend_extensions_from_enabled_plugin(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """Enabled plugin's manifest renderers/contributions are returned, tagged with plugin_id."""
    # Arrange
    mock_plugin_repo.get_enabled_plugins.return_value = [sample_plugin]
    manifest = PluginManifest(
        id="test-plugin-1",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        author="Test Author",
        plugin_type="full-stack",
        renderers=[{"kind": "history.artifact", "key": "fake_artifact", "component": "FakeArtifact.svelte"}],
        contributions=[{"slot": "admin.tabs", "component": "AdminTab.svelte", "label": "Fake Tab", "order": 50}],
    )
    mock_plugin_registry.get_plugin.return_value = manifest

    # Act
    result = operations.get_frontend_extensions(mock_plugin_repo, mock_plugin_registry)

    # Assert
    assert result["renderers"] == [
        {"plugin_id": "test-plugin-1", "kind": "history.artifact", "key": "fake_artifact", "component": "FakeArtifact.svelte"}
    ]
    assert len(result["contributions"]) == 1
    contribution = result["contributions"][0]
    assert contribution["plugin_id"] == "test-plugin-1"
    assert contribution["slot"] == "admin.tabs"
    assert contribution["order"] == 50


def test_get_frontend_extensions_excludes_disabled_plugins(mock_plugin_repo, mock_plugin_registry):
    """Only plugins returned by get_enabled_plugins() contribute extensions."""
    # Arrange
    mock_plugin_repo.get_enabled_plugins.return_value = []

    # Act
    result = operations.get_frontend_extensions(mock_plugin_repo, mock_plugin_registry)

    # Assert
    assert result == {"renderers": [], "contributions": []}


def test_get_frontend_extensions_sorts_contributions_by_order(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    """Contributions across plugins are sorted by order ascending."""
    # Arrange
    mock_plugin_repo.get_enabled_plugins.return_value = [sample_plugin]
    manifest = PluginManifest(
        id="test-plugin-1",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        author="Test Author",
        plugin_type="full-stack",
        contributions=[
            {"slot": "nav.primary", "component": "B.svelte", "order": 200},
            {"slot": "nav.primary", "component": "A.svelte", "order": 5},
        ],
    )
    mock_plugin_registry.get_plugin.return_value = manifest

    # Act
    result = operations.get_frontend_extensions(mock_plugin_repo, mock_plugin_registry)

    # Assert
    assert [c["component"] for c in result["contributions"]] == ["A.svelte", "B.svelte"]


# ========== preset/pipe rescan on enable/disable/delete ==========


@pytest.fixture
def mock_preset_loader():
    return Mock()


@pytest.fixture
def mock_pipe_catalog():
    return Mock()


@pytest.fixture
def mock_recipe_catalog():
    return Mock()


def _arrange_enable_success(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.enable_plugin.return_value = True
    mock_plugin_registry.enable_plugin.return_value = True
    mock_plugin_registry.get_plugin_state.return_value = PluginState.ENABLED
    mock_plugin_registry.get_plugin_error.return_value = None


def _arrange_disable_success(mock_plugin_repo, mock_plugin_registry, sample_plugin):
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_registry.disable_plugin.return_value = True
    mock_plugin_repo.disable_plugin.return_value = True
    mock_plugin_registry.get_plugin_state.return_value = PluginState.DISABLED
    mock_plugin_registry.get_plugin_error.return_value = None


def test_enable_plugin_rescans_presets_and_pipes(
    mock_plugin_repo, mock_plugin_registry, mock_preset_loader, mock_pipe_catalog, mock_recipe_catalog, sample_plugin
):
    _arrange_enable_success(mock_plugin_repo, mock_plugin_registry, sample_plugin)
    operations.enable_plugin(
        mock_plugin_repo, mock_plugin_registry, "test-plugin-1",
        preset_loader=mock_preset_loader, pipe_catalog=mock_pipe_catalog, recipe_catalog=mock_recipe_catalog,
    )
    mock_preset_loader.reload.assert_called_once()
    mock_pipe_catalog.rescan_plugin_pipes.assert_called_once()


def test_enable_plugin_rescans_recipes(
    mock_plugin_repo, mock_plugin_registry, mock_recipe_catalog, sample_plugin
):
    _arrange_enable_success(mock_plugin_repo, mock_plugin_registry, sample_plugin)
    operations.enable_plugin(
        mock_plugin_repo, mock_plugin_registry, "test-plugin-1", recipe_catalog=mock_recipe_catalog,
    )
    mock_recipe_catalog.reload.assert_called_once()


def test_disable_plugin_rescans_recipes(
    mock_plugin_repo, mock_plugin_registry, mock_recipe_catalog, sample_plugin
):
    _arrange_disable_success(mock_plugin_repo, mock_plugin_registry, sample_plugin)
    operations.disable_plugin(
        mock_plugin_repo, mock_plugin_registry, "test-plugin-1", recipe_catalog=mock_recipe_catalog,
    )
    mock_recipe_catalog.reload.assert_called_once()


def test_recipe_rescan_failure_does_not_fail_the_enable_call(
    mock_plugin_repo, mock_plugin_registry, mock_recipe_catalog, mock_pipe_catalog, sample_plugin
):
    """Best-effort, same as the preset/pipe rescans: a broken recipe rescan
    must not be reported as an enable failure."""
    _arrange_enable_success(mock_plugin_repo, mock_plugin_registry, sample_plugin)
    mock_recipe_catalog.reload.side_effect = RuntimeError("disk on fire")

    result = operations.enable_plugin(
        mock_plugin_repo, mock_plugin_registry, "test-plugin-1",
        pipe_catalog=mock_pipe_catalog, recipe_catalog=mock_recipe_catalog,
    )

    assert isinstance(result, PluginResponse)
    mock_pipe_catalog.rescan_plugin_pipes.assert_called_once()  # still ran despite the recipe failure


def test_disable_plugin_rescans_presets_and_pipes(
    mock_plugin_repo, mock_plugin_registry, mock_preset_loader, mock_pipe_catalog, sample_plugin
):
    _arrange_disable_success(mock_plugin_repo, mock_plugin_registry, sample_plugin)
    operations.disable_plugin(
        mock_plugin_repo, mock_plugin_registry, "test-plugin-1",
        preset_loader=mock_preset_loader, pipe_catalog=mock_pipe_catalog,
    )
    mock_preset_loader.reload.assert_called_once()
    mock_pipe_catalog.rescan_plugin_pipes.assert_called_once()


def test_delete_enabled_plugin_rescans_presets_and_pipes(
    mock_plugin_repo, mock_plugin_registry, mock_preset_loader, mock_pipe_catalog, sample_plugin
):
    sample_plugin.enabled = True
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.delete_plugin.return_value = True

    operations.delete_plugin(
        mock_plugin_repo, mock_plugin_registry, "test-plugin-1",
        preset_loader=mock_preset_loader, pipe_catalog=mock_pipe_catalog,
    )

    mock_preset_loader.reload.assert_called_once()
    mock_pipe_catalog.rescan_plugin_pipes.assert_called_once()


def test_delete_already_disabled_plugin_does_not_rescan(
    mock_plugin_repo, mock_plugin_registry, mock_preset_loader, mock_pipe_catalog, sample_plugin
):
    """A disabled plugin's presets/pipes are already absent from the
    catalogues - no rescan needed (and none of the enable/disable code paths
    that would trigger one run for a plugin that was never enabled)."""
    sample_plugin.enabled = False
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.delete_plugin.return_value = True

    operations.delete_plugin(
        mock_plugin_repo, mock_plugin_registry, "test-plugin-1",
        preset_loader=mock_preset_loader, pipe_catalog=mock_pipe_catalog,
    )

    mock_preset_loader.reload.assert_not_called()
    mock_pipe_catalog.rescan_plugin_pipes.assert_not_called()


def test_enable_plugin_failure_does_not_rescan(
    mock_plugin_repo, mock_plugin_registry, mock_preset_loader, mock_pipe_catalog, sample_plugin
):
    mock_plugin_repo.get_plugin_by_id.return_value = sample_plugin
    mock_plugin_repo.enable_plugin.return_value = True
    mock_plugin_registry.enable_plugin.return_value = False
    mock_plugin_registry.get_plugin_error.return_value = "boom"
    mock_plugin_repo.disable_plugin.return_value = True

    with pytest.raises(ValueError):
        operations.enable_plugin(
            mock_plugin_repo, mock_plugin_registry, "test-plugin-1",
            preset_loader=mock_preset_loader, pipe_catalog=mock_pipe_catalog,
        )

    mock_preset_loader.reload.assert_not_called()
    mock_pipe_catalog.rescan_plugin_pipes.assert_not_called()


def test_rescan_failure_does_not_fail_the_enable_call(
    mock_plugin_repo, mock_plugin_registry, mock_preset_loader, mock_pipe_catalog, sample_plugin
):
    """Best-effort: the registry state change already succeeded, so a broken
    rescan must not be reported as an enable failure."""
    _arrange_enable_success(mock_plugin_repo, mock_plugin_registry, sample_plugin)
    mock_preset_loader.reload.side_effect = RuntimeError("disk on fire")

    result = operations.enable_plugin(
        mock_plugin_repo, mock_plugin_registry, "test-plugin-1",
        preset_loader=mock_preset_loader, pipe_catalog=mock_pipe_catalog,
    )

    assert isinstance(result, PluginResponse)
    mock_pipe_catalog.rescan_plugin_pipes.assert_called_once()  # still ran despite the preset failure


def test_enable_plugin_without_rescan_collaborators_does_not_crash(
    mock_plugin_repo, mock_plugin_registry, sample_plugin
):
    """No preset_loader/pipe_catalog/recipe_catalog passed (all default to
    None) - must be a true no-op, not an AttributeError."""
    _arrange_enable_success(mock_plugin_repo, mock_plugin_registry, sample_plugin)
    result = operations.enable_plugin(mock_plugin_repo, mock_plugin_registry, "test-plugin-1")
    assert isinstance(result, PluginResponse)


# ========== scan_plugins against a real database (invalid manifest) ==========
#
# The tests above mock PluginRepository/PluginRegistry, so they can't catch a
# schema/DB mismatch: `operations.scan_plugins()` persists a discovered
# manifest by inserting `manifest.plugin_type` straight into the `plugins`
# table's `type` column, which is `CHECK (type IN ('frontend-only',
# 'backend-only', 'full-stack'))`. These tests run the real PluginLoader,
# PluginRegistry, and PluginRepository against a migrated scratch DB and real
# fixture plugin directories on disk, so an invalid manifest's placeholder
# type actually reaches that constraint.

import tempfile
import shutil
import yaml
from pathlib import Path

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.plugins.repository import PluginRepository
from src.platform.plugins.registry import PluginRegistry


class TestScanPluginsWithInvalidManifest(PersistenceTestBase):
    """scan_plugins() against real plugin directories, one with a broken manifest."""

    def setUp(self):
        super().setUp()

        self.plugins_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.plugins_dir / "marketplace"
        self.local_dir = self.plugins_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self._write_manifest(self.marketplace_dir / "valid-plugin", {
            "id": "valid-plugin",
            "name": "Valid Plugin",
            "version": "1.0.0",
            "description": "A well-formed plugin",
            "author": "Test Author",
            "type": "backend-only",
        })

        # Missing the required `author` field - fails PluginManifestSchema
        # validation, so the loader returns an error-tagged placeholder
        # manifest instead of a parsed one.
        self._write_manifest(self.marketplace_dir / "broken-plugin", {
            "id": "broken-plugin",
            "name": "Broken Plugin",
            "version": "1.0.0",
            "description": "Missing the author field",
            "type": "backend-only",
        })

        self.registry = PluginRegistry(
            marketplace_dir=str(self.marketplace_dir),
            local_dir=str(self.local_dir),
        )
        self.repo = PluginRepository()

    def tearDown(self):
        shutil.rmtree(self.plugins_dir)
        super().tearDown()

    @staticmethod
    def _write_manifest(plugin_dir: Path, manifest_data: dict) -> None:
        plugin_dir.mkdir(parents=True)
        with open(plugin_dir / "manifest.yml", "w") as f:
            yaml.dump(manifest_data, f)

    def test_scan_persists_valid_plugin_and_reports_invalid_one_as_error(self):
        result = operations.scan_plugins(self.repo, self.registry)

        assert result.total_discovered == 2
        new_ids = {p.id for p in result.new_plugins}
        assert new_ids == {"valid-plugin", "broken-plugin"}

        responses = {
            p.id: plugin_to_response(p, self.registry) for p in self.repo.get_all_plugins()
        }
        assert responses["valid-plugin"].state == "discovered"
        assert responses["valid-plugin"].error is None

        assert responses["broken-plugin"].state == "error"
        assert responses["broken-plugin"].error is not None
        assert "author" in responses["broken-plugin"].error

        # The broken plugin's DB row must carry a type value the `plugins`
        # table's CHECK constraint actually accepts.
        broken_row = self.repo.get_plugin_by_id("broken-plugin")
        assert broken_row.type in ("frontend-only", "backend-only", "full-stack")
