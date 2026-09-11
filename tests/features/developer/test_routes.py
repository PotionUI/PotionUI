"""Tests for DeveloperController.

`get_template_functions_documentation` reads straight from the held
`TemplateFunctionsDocumenter` (no logic beyond delegation); `get_presets_lint`/
`get_docs_lint` delegate to `src.features.developer.operations` -
`mock_operations` patches the `operations` module as imported into
`routes.py`, exactly like the previous manager mock (see
tests/features/user_groups/test_routes.py for the established pattern)."""
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, Mock

from src.features.developer import routes as routes_module
from src.features.developer.dto import RenderPresetStylesRequest
from src.features.developer.routes import DeveloperController, build_router
from src.platform.security.user import User, AccountType


def _route_handlers(controller):
    """Build the router for a stub container and return endpoint fns by name."""
    router = build_router(SimpleNamespace(developer_controller=controller))
    return {route.name: route.endpoint for route in router.routes}


class TestDeveloperController:
    """Tests for the DeveloperController class."""

    @pytest.fixture
    def mock_template_functions_documenter(self):
        documenter = Mock()
        documenter.generate_documentation.return_value = {
            'functions': [{'name': 'path'}],
            'total': 1,
            'categories': ['Path Helpers']
        }
        return documenter

    @pytest.fixture
    def controller(self, mock_template_functions_documenter):
        """Create a DeveloperController with a mock documenter."""
        return DeveloperController(mock_template_functions_documenter, Mock(), Mock())

    @pytest.fixture
    def admin_user(self):
        """Create an admin user fixture."""
        user = Mock(spec=User)
        user.account_type = AccountType.ADMIN
        return user

    @pytest.fixture
    def regular_user(self):
        """Create a regular user fixture."""
        user = Mock(spec=User)
        user.account_type = AccountType.USER
        return user

    def test_initialization(self, mock_template_functions_documenter):
        """Test controller initializes with its collaborators."""
        preset_loader = Mock()
        controller = DeveloperController(mock_template_functions_documenter, preset_loader, Mock())
        assert controller.template_functions_documenter == mock_template_functions_documenter
        assert controller.preset_loader == preset_loader

    @pytest.mark.asyncio
    async def test_get_template_functions_documentation_success(self, controller, mock_template_functions_documenter):
        """Test successful template functions retrieval."""
        response = await controller.get_template_functions_documentation()

        assert response.success is True
        assert response.data['functions'] == [{'name': 'path'}]
        assert response.data['total'] == 1
        assert response.data['categories'] == ['Path Helpers']
        mock_template_functions_documenter.generate_documentation.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_template_functions_documentation_value_error(self, controller, mock_template_functions_documenter):
        """Test template functions with ValueError."""
        mock_template_functions_documenter.generate_documentation.side_effect = ValueError("Template error")

        response = await controller.get_template_functions_documentation()

        assert response.success is False
        assert response.error == "template_functions_failed"


class TestRouteHandlers:
    """Tests for the route handler functions, built via build_router with a stub container."""

    @pytest.fixture
    def mock_controller(self):
        """Create a documenter-backed controller."""
        documenter = Mock()
        documenter.generate_documentation.return_value = {
            'functions': [], 'total': 0, 'categories': []
        }
        return DeveloperController(documenter, Mock(), Mock())

    @pytest.fixture
    def handlers(self, mock_controller):
        return _route_handlers(mock_controller)

    @pytest.fixture
    def admin_user(self):
        """Create an admin user fixture."""
        user = Mock(spec=User)
        user.account_type = AccountType.ADMIN
        return user

    @pytest.fixture
    def regular_user(self):
        """Create a regular user fixture."""
        user = Mock(spec=User)
        user.account_type = AccountType.USER
        return user

    @pytest.mark.asyncio
    async def test_get_template_functions_route_admin(self, handlers, admin_user):
        """Test template functions route with admin user."""
        response = await handlers["get_template_functions"](current_user=admin_user)
        assert response.success is True

    @pytest.mark.asyncio
    async def test_get_template_functions_route_non_admin(self, handlers, regular_user):
        """Test template functions route with non-admin user raises 403."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await handlers["get_template_functions"](current_user=regular_user)

        assert exc_info.value.status_code == 403


class TestDocsLint:
    """Docs 2.0 lint endpoint (GET /api/developer/docs/lint)."""

    @pytest.fixture
    def mock_operations(self, monkeypatch):
        """Patch the `operations` module as seen by routes.py."""
        mock = Mock()
        mock.get_docs_lint.return_value = {"issues": [], "total_errors": 0, "total_warnings": 0}
        monkeypatch.setattr(routes_module, "operations", mock)
        return mock

    @pytest.fixture
    def controller(self, mock_operations):
        return DeveloperController(Mock(), Mock(), Mock())

    @pytest.mark.asyncio
    async def test_get_docs_lint_success(self, controller, mock_operations):
        response = await controller.get_docs_lint()
        assert response.success is True
        assert response.data == {"issues": [], "total_errors": 0, "total_warnings": 0}
        mock_operations.get_docs_lint.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_docs_lint_value_error(self, controller, mock_operations):
        mock_operations.get_docs_lint.side_effect = ValueError("boom")
        response = await controller.get_docs_lint()
        assert response.success is False
        assert response.error == "docs_lint_failed"

    @pytest.mark.asyncio
    async def test_docs_lint_route_admin(self, mock_operations):
        handlers = _route_handlers(DeveloperController(Mock(), Mock(), Mock()))
        user = Mock(spec=User)
        user.account_type = AccountType.ADMIN
        response = await handlers["get_docs_lint"](current_user=user)
        assert response.success is True

    @pytest.mark.asyncio
    async def test_docs_lint_route_non_admin_403(self):
        from fastapi import HTTPException

        handlers = _route_handlers(DeveloperController(Mock(), Mock(), Mock()))
        user = Mock(spec=User)
        user.account_type = AccountType.USER
        with pytest.raises(HTTPException) as exc:
            await handlers["get_docs_lint"](current_user=user)
        assert exc.value.status_code == 403


class TestRenderPresetStyles:
    """POST /api/developer/presets/{preset_id}/styles/render -
    DeveloperController.render_preset_styles delegates to `self.style_renderer`."""

    @pytest.fixture
    def mock_style_renderer(self):
        renderer = Mock()
        renderer.render_styles = AsyncMock(return_value={"rendered": ["retro-90s-cel"], "failed": []})
        return renderer

    @pytest.fixture
    def controller(self, mock_style_renderer):
        return DeveloperController(Mock(), Mock(), mock_style_renderer)

    @pytest.mark.asyncio
    async def test_render_success_delegates_with_request_fields(self, controller, mock_style_renderer):
        request = RenderPresetStylesRequest(style_ids=["retro-90s-cel"], long_edge=512, seed=7)

        response = await controller.render_preset_styles("Anima", request, "user-1")

        assert response.success is True
        assert response.data == {"rendered": ["retro-90s-cel"], "failed": []}
        mock_style_renderer.render_styles.assert_awaited_once_with(
            preset_id="Anima", user_id="user-1", style_ids=["retro-90s-cel"], long_edge=512, seed=7
        )

    @pytest.mark.asyncio
    async def test_render_value_error_becomes_error_response(self, controller, mock_style_renderer):
        mock_style_renderer.render_styles.side_effect = ValueError("Unknown style id(s): bogus")

        response = await controller.render_preset_styles("Anima", RenderPresetStylesRequest(), "user-1")

        assert response.success is False
        assert response.error == "preset_styles_render_failed"
        assert "bogus" in response.message

    @pytest.mark.asyncio
    async def test_render_route_non_admin_403(self):
        from fastapi import HTTPException

        handlers = _route_handlers(DeveloperController(Mock(), Mock(), Mock()))
        user = Mock(spec=User)
        user.account_type = AccountType.USER

        with pytest.raises(HTTPException) as exc:
            await handlers["render_preset_styles"](
                preset_id="Anima", request=RenderPresetStylesRequest(), current_user=user
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_render_route_admin_delegates(self, mock_style_renderer):
        handlers = _route_handlers(DeveloperController(Mock(), Mock(), mock_style_renderer))
        user = Mock(spec=User)
        user.account_type = AccountType.ADMIN
        user.id = "admin-1"

        response = await handlers["render_preset_styles"](
            preset_id="Anima", request=RenderPresetStylesRequest(), current_user=user
        )

        assert response.success is True
        mock_style_renderer.render_styles.assert_awaited_once_with(
            preset_id="Anima", user_id="admin-1", style_ids=None, long_edge=320, seed=1
        )
