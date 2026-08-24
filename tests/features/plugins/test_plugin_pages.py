"""
Tests for plugin pages, sidebar items, and API routes extension.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
from datetime import datetime

from src.platform.plugins.loader import PluginManifest, PluginLoader
from src.features.plugins.manager import PluginManager
from src.features.plugins.records import PluginPage, Plugin
from src.features.plugins.dto import PluginPageResponse


# ========== PluginManifest New Fields Tests ==========

class TestPluginManifestNewFields:
    """Test that PluginManifest supports pages, api_routes, and sidebar_items fields"""

    def test_manifest_defaults_empty_pages(self):
        """Test that PluginManifest defaults to empty lists for new fields"""
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack"
        )
        assert manifest.pages == []
        assert manifest.api_routes == []
        assert manifest.sidebar_items == []

    def test_manifest_with_pages(self):
        """Test creating a manifest with pages"""
        pages = [
            {"route": "/my-page", "component": "pages/MyPage.svelte", "label": "My Page"}
        ]
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            pages=pages
        )
        assert len(manifest.pages) == 1
        assert manifest.pages[0]["route"] == "/my-page"

    def test_manifest_with_sidebar_items(self):
        """Test creating a manifest with sidebar items"""
        sidebar = [
            {"label": "My Page", "icon": "<svg>...</svg>", "route": "/my-page", "order": 50}
        ]
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            sidebar_items=sidebar
        )
        assert len(manifest.sidebar_items) == 1
        assert manifest.sidebar_items[0]["order"] == 50

    def test_manifest_with_api_routes(self):
        """Test creating a manifest with api_routes"""
        api_routes = {"module": "api/routes.py"}
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            api_routes=api_routes
        )
        assert manifest.api_routes["module"] == "api/routes.py"


class TestPluginLoaderParsingNewFields:
    """Test that _load_manifest parses new YAML sections"""

    def test_load_manifest_parses_pages(self, tmp_path):
        """Test that _load_manifest parses pages section"""
        manifest_content = """
id: test-plugin
name: Test Plugin
version: 1.0.0
description: A test plugin
author: Test Author
type: full-stack
pages:
  - route: /editor
    component: pages/Editor.svelte
    label: Video Editor
  - route: /viewer
    component: pages/Viewer.svelte
    label: Media Viewer
"""
        manifest_file = tmp_path / "manifest.yml"
        manifest_file.write_text(manifest_content)

        loader = PluginLoader()
        manifest = loader._load_manifest(manifest_file, tmp_path, "local")

        assert manifest is not None
        assert len(manifest.pages) == 2
        assert manifest.pages[0]["route"] == "/editor"
        assert manifest.pages[0]["component"] == "pages/Editor.svelte"
        assert manifest.pages[0]["label"] == "Video Editor"

    def test_load_manifest_parses_sidebar(self, tmp_path):
        """Test that _load_manifest parses sidebar section"""
        manifest_content = """
id: test-plugin
name: Test Plugin
version: 1.0.0
description: A test plugin
author: Test Author
type: full-stack
sidebar:
  - label: My Page
    icon: "<svg>icon</svg>"
    route: /my-page
    order: 50
"""
        manifest_file = tmp_path / "manifest.yml"
        manifest_file.write_text(manifest_content)

        loader = PluginLoader()
        manifest = loader._load_manifest(manifest_file, tmp_path, "local")

        assert manifest is not None
        assert len(manifest.sidebar_items) == 1
        assert manifest.sidebar_items[0]["label"] == "My Page"
        assert manifest.sidebar_items[0]["order"] == 50

    def test_load_manifest_parses_api(self, tmp_path):
        """Test that _load_manifest parses api section"""
        manifest_content = """
id: test-plugin
name: Test Plugin
version: 1.0.0
description: A test plugin
author: Test Author
type: full-stack
api:
  module: api/routes.py
"""
        manifest_file = tmp_path / "manifest.yml"
        manifest_file.write_text(manifest_content)

        loader = PluginLoader()
        manifest = loader._load_manifest(manifest_file, tmp_path, "local")

        assert manifest is not None
        assert manifest.api_routes["module"] == "api/routes.py"

    def test_load_manifest_missing_new_sections_defaults_empty(self, tmp_path):
        """Test that missing pages/sidebar/api sections default to empty"""
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
        assert manifest.pages == []
        assert manifest.sidebar_items == []
        assert manifest.api_routes == {}


# ========== PluginPage Model Tests ==========

class TestPluginPageModel:
    """Test the PluginPage dataclass"""

    def test_create_plugin_page(self):
        """Test creating a PluginPage instance"""
        page = PluginPage(
            id=1,
            plugin_id="test-plugin",
            route="/editor",
            component_path="pages/Editor.svelte",
            label="Video Editor",
            icon_svg="<svg>...</svg>",
            sidebar_order=50,
            show_in_sidebar=True,
            created_at=datetime(2024, 1, 1, 12, 0, 0)
        )
        assert page.id == 1
        assert page.plugin_id == "test-plugin"
        assert page.route == "/editor"
        assert page.component_path == "pages/Editor.svelte"
        assert page.label == "Video Editor"
        assert page.sidebar_order == 50
        assert page.show_in_sidebar is True
        assert page.require_role is None

    def test_plugin_page_with_require_role(self):
        """Test creating a PluginPage with require_role"""
        page = PluginPage(
            id=1,
            plugin_id="test-plugin",
            route="/admin-panel",
            component_path="pages/AdminPanel.svelte",
            label="Admin Panel",
            require_role="ADMIN"
        )
        assert page.require_role == "ADMIN"

    def test_plugin_page_defaults(self):
        """Test PluginPage default values"""
        page = PluginPage(
            id=1,
            plugin_id="test-plugin",
            route="/editor",
            component_path="pages/Editor.svelte",
            label="Video Editor"
        )
        assert page.icon_svg is None
        assert page.sidebar_order == 100
        assert page.show_in_sidebar is True
        assert page.require_role is None
        assert page.created_at is None

    def test_plugin_page_from_row(self):
        """Test creating PluginPage from database row"""
        row = {
            'id': 1,
            'plugin_id': 'test-plugin',
            'route': '/editor',
            'component_path': 'pages/Editor.svelte',
            'label': 'Video Editor',
            'icon_svg': '<svg>icon</svg>',
            'sidebar_order': 50,
            'show_in_sidebar': 1,
            'require_role': 'ADMIN',
            'created_at': '2024-01-01T12:00:00'
        }
        page = PluginPage.from_row(row)
        assert page.id == 1
        assert page.plugin_id == 'test-plugin'
        assert page.route == '/editor'
        assert page.sidebar_order == 50
        assert page.show_in_sidebar is True
        assert page.require_role == 'ADMIN'
        assert page.created_at == datetime(2024, 1, 1, 12, 0, 0)

    def test_plugin_page_from_row_without_require_role(self):
        """Test creating PluginPage from row without require_role column (legacy rows)"""
        row = {
            'id': 1,
            'plugin_id': 'test-plugin',
            'route': '/editor',
            'component_path': 'pages/Editor.svelte',
            'label': 'Video Editor',
            'icon_svg': '<svg>icon</svg>',
            'sidebar_order': 50,
            'show_in_sidebar': 1,
            'created_at': '2024-01-01T12:00:00'
        }
        page = PluginPage.from_row(row)
        assert page.require_role is None

    def test_plugin_page_from_row_nullable_fields(self):
        """Test creating PluginPage from row with nullable fields"""
        row = {
            'id': 1,
            'plugin_id': 'test-plugin',
            'route': '/editor',
            'component_path': 'pages/Editor.svelte',
            'label': 'Video Editor',
            'icon_svg': None,
            'sidebar_order': None,
            'show_in_sidebar': 0,
            'require_role': None,
            'created_at': None
        }
        page = PluginPage.from_row(row)
        assert page.icon_svg is None
        assert page.sidebar_order == 100
        assert page.show_in_sidebar is False
        assert page.require_role is None
        assert page.created_at is None

    def test_plugin_page_to_dict(self):
        """Test converting PluginPage to dictionary"""
        page = PluginPage(
            id=1,
            plugin_id="test-plugin",
            route="/editor",
            component_path="pages/Editor.svelte",
            label="Video Editor",
            icon_svg="<svg>icon</svg>",
            sidebar_order=50,
            show_in_sidebar=True,
            created_at=datetime(2024, 1, 1, 12, 0, 0)
        )
        result = page.to_dict()
        assert result['id'] == 1
        assert result['plugin_id'] == 'test-plugin'
        assert result['route'] == '/editor'
        assert result['component_path'] == 'pages/Editor.svelte'
        assert result['label'] == 'Video Editor'
        assert result['icon_svg'] == '<svg>icon</svg>'
        assert result['sidebar_order'] == 50
        assert result['show_in_sidebar'] is True
        assert result['require_role'] is None
        assert result['created_at'] == '2024-01-01T12:00:00'

    def test_plugin_page_to_dict_with_require_role(self):
        """Test converting PluginPage with require_role to dictionary"""
        page = PluginPage(
            id=1,
            plugin_id="test-plugin",
            route="/admin-panel",
            component_path="pages/AdminPanel.svelte",
            label="Admin Panel",
            require_role="ADMIN"
        )
        result = page.to_dict()
        assert result['require_role'] == 'ADMIN'


# Note: plugin.page.register / plugin.sidebar.register / plugin.api.register hooks
# had zero call sites and no manifest references - dropped during the hook system
# cleanup rather than re-declared (see src/core/plugins/hooks.py).


# ========== PluginManager Page Methods Tests ==========

@pytest.fixture
def mock_plugin_repo():
    """Mock PluginRepository"""
    return Mock()


@pytest.fixture
def mock_plugin_registry():
    """Mock PluginRegistry"""
    return Mock()


@pytest.fixture
def manager(mock_plugin_repo, mock_plugin_registry):
    """Create PluginManager instance with mocked dependencies"""
    return PluginManager(
        plugin_repository=mock_plugin_repo,
        plugin_registry=mock_plugin_registry
    )


@pytest.fixture
def sample_plugin_pages():
    """Sample plugin pages"""
    return [
        PluginPage(
            id=1,
            plugin_id="video-editor",
            route="/video-editor",
            component_path="pages/VideoEditor.svelte",
            label="Video Editor",
            icon_svg="<svg>video</svg>",
            sidebar_order=50,
            show_in_sidebar=True,
            created_at=datetime(2024, 1, 1, 12, 0, 0)
        ),
        PluginPage(
            id=2,
            plugin_id="video-editor",
            route="/video-settings",
            component_path="pages/VideoSettings.svelte",
            label="Video Settings",
            icon_svg=None,
            sidebar_order=100,
            show_in_sidebar=False,
            created_at=datetime(2024, 1, 1, 12, 0, 0)
        ),
    ]


class TestPluginManagerPages:
    """Test PluginManager page-related methods"""

    def test_get_active_pages_returns_all_pages(self, manager, mock_plugin_repo, sample_plugin_pages):
        """Test that get_active_pages returns all pages from enabled plugins"""
        mock_plugin_repo.get_all_active_pages.return_value = sample_plugin_pages

        result = manager.get_active_pages()

        assert len(result) == 2
        assert all(isinstance(p, PluginPageResponse) for p in result)
        assert result[0].route == "/video-editor"
        assert result[1].route == "/video-settings"
        mock_plugin_repo.get_all_active_pages.assert_called_once()

    def test_get_active_pages_empty(self, manager, mock_plugin_repo):
        """Test get_active_pages when no active pages exist"""
        mock_plugin_repo.get_all_active_pages.return_value = []

        result = manager.get_active_pages()

        assert result == []

    def test_get_sidebar_items_filters_show_in_sidebar(self, manager, mock_plugin_repo, sample_plugin_pages):
        """Test that get_sidebar_items filters by show_in_sidebar"""
        mock_plugin_repo.get_all_active_pages.return_value = sample_plugin_pages

        result = manager.get_sidebar_items()

        assert len(result) == 1
        assert result[0].route == "/video-editor"
        assert result[0].show_in_sidebar is True

    def test_get_active_pages_includes_require_role(self, manager, mock_plugin_repo):
        """Test that get_active_pages includes require_role in response"""
        admin_page = PluginPage(
            id=1,
            plugin_id="admin-plugin",
            route="/admin-panel",
            component_path="pages/AdminPanel.svelte",
            label="Admin Panel",
            require_role="ADMIN",
            show_in_sidebar=True
        )
        mock_plugin_repo.get_all_active_pages.return_value = [admin_page]

        result = manager.get_active_pages()

        assert len(result) == 1
        assert result[0].require_role == "ADMIN"

    def test_get_sidebar_items_includes_require_role(self, manager, mock_plugin_repo):
        """Test that get_sidebar_items includes require_role in response"""
        admin_page = PluginPage(
            id=1,
            plugin_id="admin-plugin",
            route="/admin-panel",
            component_path="pages/AdminPanel.svelte",
            label="Admin Panel",
            require_role="ADMIN",
            show_in_sidebar=True
        )
        mock_plugin_repo.get_all_active_pages.return_value = [admin_page]

        result = manager.get_sidebar_items()

        assert len(result) == 1
        assert result[0].require_role == "ADMIN"

    def test_get_sidebar_items_empty_when_none_visible(self, manager, mock_plugin_repo):
        """Test get_sidebar_items when no pages have show_in_sidebar=True"""
        hidden_page = PluginPage(
            id=1,
            plugin_id="test",
            route="/hidden",
            component_path="pages/Hidden.svelte",
            label="Hidden",
            show_in_sidebar=False
        )
        mock_plugin_repo.get_all_active_pages.return_value = [hidden_page]

        result = manager.get_sidebar_items()

        assert result == []


class TestPluginManagerRegisterPages:
    """Test that _register_plugin_hooks also registers pages"""

    def test_register_plugin_hooks_registers_pages(self, manager, mock_plugin_repo):
        """Test that _register_plugin_hooks creates pages from manifest"""
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            pages=[
                {"route": "/editor", "component": "pages/Editor.svelte", "label": "Editor"}
            ],
            sidebar_items=[
                {"route": "/editor", "icon": "<svg>icon</svg>", "order": 50}
            ]
        )

        manager._register_plugin_hooks(manifest)

        mock_plugin_repo.create_plugin_page.assert_called_once()
        call_args = mock_plugin_repo.create_plugin_page.call_args[0][0]
        assert isinstance(call_args, PluginPage)
        assert call_args.route == "/editor"
        assert call_args.component_path == "pages/Editor.svelte"
        assert call_args.label == "Editor"
        assert call_args.icon_svg == "<svg>icon</svg>"
        assert call_args.sidebar_order == 50
        assert call_args.show_in_sidebar is True

    def test_register_plugin_hooks_registers_pages_with_require_role(self, manager, mock_plugin_repo):
        """Test that _register_plugin_hooks creates pages with require_role from sidebar items"""
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            pages=[
                {"route": "/admin-panel", "component": "pages/AdminPanel.svelte", "label": "Admin Panel"}
            ],
            sidebar_items=[
                {"route": "/admin-panel", "icon": "<svg>icon</svg>", "order": 10, "require_role": "ADMIN"}
            ]
        )

        manager._register_plugin_hooks(manifest)

        mock_plugin_repo.create_plugin_page.assert_called_once()
        call_args = mock_plugin_repo.create_plugin_page.call_args[0][0]
        assert isinstance(call_args, PluginPage)
        assert call_args.route == "/admin-panel"
        assert call_args.require_role == "ADMIN"
        assert call_args.show_in_sidebar is True

    def test_register_plugin_hooks_page_without_sidebar(self, manager, mock_plugin_repo):
        """Test registering a page that has no matching sidebar entry"""
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            pages=[
                {"route": "/settings", "component": "pages/Settings.svelte", "label": "Settings"}
            ],
            sidebar_items=[]
        )

        manager._register_plugin_hooks(manifest)

        call_args = mock_plugin_repo.create_plugin_page.call_args[0][0]
        assert call_args.icon_svg is None
        assert call_args.sidebar_order == 100
        assert call_args.show_in_sidebar is False

    def test_register_plugin_hooks_skips_incomplete_page(self, manager, mock_plugin_repo):
        """Test that incomplete page definitions are skipped"""
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            pages=[
                {"route": "/editor"}  # Missing component and label
            ]
        )

        manager._register_plugin_hooks(manifest)

        mock_plugin_repo.create_plugin_page.assert_not_called()

    def test_refresh_plugin_hooks_clears_pages(self, manager, mock_plugin_repo):
        """Test that _refresh_plugin_hooks clears existing pages"""
        manifest = PluginManifest(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            description="Test",
            author="Test Author",
            plugin_type="full-stack",
            pages=[]
        )

        manager._refresh_plugin_hooks(manifest)

        mock_plugin_repo.delete_plugin_pages.assert_called_once_with("test-plugin")
        mock_plugin_repo.clear_plugin_hooks.assert_called_once_with("test-plugin")


# ========== PluginPageResponse DTO Tests ==========

class TestPluginPageResponseDTO:
    """Test the PluginPageResponse DTO"""

    def test_create_page_response(self):
        """Test creating a PluginPageResponse"""
        response = PluginPageResponse(
            plugin_id="test-plugin",
            route="/editor",
            component_path="pages/Editor.svelte",
            label="Video Editor",
            icon_svg="<svg>icon</svg>",
            sidebar_order=50,
            show_in_sidebar=True
        )
        assert response.plugin_id == "test-plugin"
        assert response.route == "/editor"
        assert response.component_path == "pages/Editor.svelte"
        assert response.label == "Video Editor"
        assert response.require_role is None

    def test_create_page_response_with_require_role(self):
        """Test creating a PluginPageResponse with require_role"""
        response = PluginPageResponse(
            plugin_id="test-plugin",
            route="/admin-panel",
            component_path="pages/AdminPanel.svelte",
            label="Admin Panel",
            require_role="ADMIN"
        )
        assert response.require_role == "ADMIN"

    def test_page_response_defaults(self):
        """Test PluginPageResponse default values"""
        response = PluginPageResponse(
            plugin_id="test-plugin",
            route="/editor",
            component_path="pages/Editor.svelte",
            label="Video Editor"
        )
        assert response.icon_svg is None
        assert response.sidebar_order == 100
        assert response.show_in_sidebar is True
        assert response.require_role is None

    def test_page_response_model_dump(self):
        """Test that PluginPageResponse serializes correctly"""
        response = PluginPageResponse(
            plugin_id="test-plugin",
            route="/editor",
            component_path="pages/Editor.svelte",
            label="Video Editor",
            sidebar_order=50,
            show_in_sidebar=True
        )
        data = response.model_dump()
        assert data["plugin_id"] == "test-plugin"
        assert data["route"] == "/editor"
        assert data["sidebar_order"] == 50
        assert data["show_in_sidebar"] is True
        assert data["require_role"] is None

    def test_page_response_model_dump_with_require_role(self):
        """Test that PluginPageResponse with require_role serializes correctly"""
        response = PluginPageResponse(
            plugin_id="test-plugin",
            route="/admin-panel",
            component_path="pages/AdminPanel.svelte",
            label="Admin Panel",
            require_role="ADMIN"
        )
        data = response.model_dump()
        assert data["require_role"] == "ADMIN"
