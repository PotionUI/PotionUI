"""Tests for DeveloperController."""
from types import SimpleNamespace

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from src.features.developer.routes import DeveloperController, build_router
from src.platform.security.user import User, AccountType


def _route_handlers(controller):
    """Build the router for a stub container and return endpoint fns by name."""
    router = build_router(SimpleNamespace(developer_controller=controller))
    return {route.name: route.endpoint for route in router.routes}


class TestDeveloperController:
    """Tests for the DeveloperController class."""

    @pytest.fixture
    def mock_manager(self):
        """Create a mock DeveloperManager."""
        manager = Mock()
        manager.get_template_functions_documentation.return_value = {
            'functions': [{'name': 'path'}],
            'total': 1,
            'categories': ['Path Helpers']
        }
        return manager

    @pytest.fixture
    def controller(self, mock_manager):
        """Create a DeveloperController with mock manager."""
        return DeveloperController(mock_manager)

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

    def test_initialization(self, mock_manager):
        """Test controller initializes with manager."""
        controller = DeveloperController(mock_manager)
        assert controller.manager == mock_manager

    @pytest.mark.asyncio
    async def test_get_template_functions_documentation_success(self, controller, mock_manager):
        """Test successful template functions retrieval."""
        response = await controller.get_template_functions_documentation()

        assert response.success is True
        assert response.data['functions'] == [{'name': 'path'}]
        assert response.data['total'] == 1
        assert response.data['categories'] == ['Path Helpers']
        mock_manager.get_template_functions_documentation.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_template_functions_documentation_value_error(self, controller, mock_manager):
        """Test template functions with ValueError."""
        mock_manager.get_template_functions_documentation.side_effect = ValueError("Template error")

        response = await controller.get_template_functions_documentation()

        assert response.success is False
        assert response.error == "template_functions_failed"


class TestRouteHandlers:
    """Tests for the route handler functions, built via build_router with a stub container."""

    @pytest.fixture
    def mock_controller(self):
        """Create a mock-manager-backed controller."""
        mock_manager = Mock()
        mock_manager.get_template_functions_documentation.return_value = {
            'functions': [], 'total': 0, 'categories': []
        }
        return DeveloperController(mock_manager)

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
    def mock_manager(self):
        m = Mock()
        m.get_docs_lint.return_value = {"issues": [], "total_errors": 0, "total_warnings": 0}
        return m

    @pytest.fixture
    def controller(self, mock_manager):
        return DeveloperController(mock_manager)

    @pytest.mark.asyncio
    async def test_get_docs_lint_success(self, controller, mock_manager):
        response = await controller.get_docs_lint()
        assert response.success is True
        assert response.data == {"issues": [], "total_errors": 0, "total_warnings": 0}
        mock_manager.get_docs_lint.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_docs_lint_value_error(self, controller, mock_manager):
        mock_manager.get_docs_lint.side_effect = ValueError("boom")
        response = await controller.get_docs_lint()
        assert response.success is False
        assert response.error == "docs_lint_failed"

    @pytest.mark.asyncio
    async def test_docs_lint_route_admin(self):
        m = Mock()
        m.get_docs_lint.return_value = {"issues": [], "total_errors": 0, "total_warnings": 0}
        handlers = _route_handlers(DeveloperController(m))
        user = Mock(spec=User)
        user.account_type = AccountType.ADMIN
        response = await handlers["get_docs_lint"](current_user=user)
        assert response.success is True

    @pytest.mark.asyncio
    async def test_docs_lint_route_non_admin_403(self):
        from fastapi import HTTPException

        handlers = _route_handlers(DeveloperController(Mock()))
        user = Mock(spec=User)
        user.account_type = AccountType.USER
        with pytest.raises(HTTPException) as exc:
            await handlers["get_docs_lint"](current_user=user)
        assert exc.value.status_code == 403
