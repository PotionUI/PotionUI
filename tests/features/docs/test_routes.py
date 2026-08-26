"""Tests for DocsController.

`get_tree`/`get_content` delegate to `src.features.docs.operations` against
the held `plugin_registry`/`base_docs_path` collaborators; `mock_operations`
patches the `operations` module as imported into `routes.py`, exactly like
the previous manager mock (see tests/features/user_groups/test_routes.py for
the established pattern)."""
import pytest
from unittest.mock import Mock

from src.features.docs import routes as routes_module
from src.features.docs.routes import DocsController
from src.features.docs.operations import DocForbiddenError, DocIsLiveError, DocNotFoundError
from src.platform.security.user import User, AccountType


class TestDocsController:
    """Tests for the DocsController class."""

    @pytest.fixture
    def mock_operations(self, monkeypatch):
        """Patch the `operations` module as seen by routes.py."""
        mock = Mock()
        mock.build_tree.return_value = {
            "sections": [{"id": "user", "title": "User Guide", "items": []}]
        }
        mock.get_content.return_value = {
            "id": "user/doc", "title": "Doc", "markdown": "Body"
        }
        monkeypatch.setattr(routes_module, "operations", mock)
        return mock

    @pytest.fixture
    def mock_pipes_documenter(self):
        documenter = Mock()
        documenter.generate_documentation.return_value = {"pipes": [{"name": "test_pipe"}], "total": 1}
        return documenter

    @pytest.fixture
    def controller(self, mock_operations, mock_pipes_documenter):
        return DocsController(Mock(), "docs", mock_pipes_documenter)

    @pytest.fixture
    def admin_user(self):
        user = Mock(spec=User)
        user.account_type = AccountType.ADMIN
        return user

    @pytest.fixture
    def regular_user(self):
        user = Mock(spec=User)
        user.account_type = AccountType.USER
        return user

    # ---- tree shape for admin vs regular user ----

    @pytest.mark.asyncio
    async def test_get_tree_for_regular_user_requests_non_admin_tree(self, controller, mock_operations):
        response = await controller.get_tree(is_admin=False)

        assert response.success is True
        mock_operations.build_tree.assert_called_once_with(controller.plugin_registry, controller.base_docs_path, False)
        assert response.data["sections"][0]["id"] == "user"

    @pytest.mark.asyncio
    async def test_get_tree_for_admin_requests_admin_tree(self, controller, mock_operations):
        mock_operations.build_tree.return_value = {
            "sections": [
                {"id": "user", "title": "User Guide", "items": []},
                {"id": "developer", "title": "Developer", "items": []},
            ]
        }

        response = await controller.get_tree(is_admin=True)

        assert response.success is True
        mock_operations.build_tree.assert_called_once_with(controller.plugin_registry, controller.base_docs_path, True)
        assert [s["id"] for s in response.data["sections"]] == ["user", "developer"]

    @pytest.mark.asyncio
    async def test_get_tree_handles_exception(self, controller, mock_operations):
        mock_operations.build_tree.side_effect = Exception("boom")

        response = await controller.get_tree(is_admin=False)

        assert response.success is False
        assert response.error == "docs_tree_failed"

    # ---- content 200/403/404/400 paths ----

    @pytest.mark.asyncio
    async def test_get_content_success(self, controller, mock_operations):
        response = await controller.get_content("user/doc", is_admin=False)

        assert response.success is True
        assert response.data["markdown"] == "Body"
        mock_operations.get_content.assert_called_once_with(
            controller.plugin_registry, controller.base_docs_path, "user/doc", False
        )

    @pytest.mark.asyncio
    async def test_get_content_not_found_raises_404(self, controller, mock_operations):
        from fastapi import HTTPException

        mock_operations.get_content.side_effect = DocNotFoundError("Unknown doc id: 'user/nope'")

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_content("user/nope", is_admin=False)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_content_forbidden_raises_403(self, controller, mock_operations):
        from fastapi import HTTPException

        mock_operations.get_content.side_effect = DocForbiddenError("requires admin access")

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_content("dev/architecture", is_admin=False)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_content_live_doc_raises_400(self, controller, mock_operations):
        from fastapi import HTTPException

        mock_operations.get_content.side_effect = DocIsLiveError("no markdown content")

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_content("live/hooks", is_admin=True)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_get_content_handles_unexpected_exception(self, controller, mock_operations):
        mock_operations.get_content.side_effect = Exception("boom")

        response = await controller.get_content("user/doc", is_admin=False)

        assert response.success is False
        assert response.error == "doc_content_failed"

    # ---- live pipes / output types ----

    @pytest.mark.asyncio
    async def test_get_live_pipes_success(self, controller, mock_pipes_documenter):
        response = await controller.get_live_pipes()

        assert response.success is True
        assert response.data["pipes"] == [{"name": "test_pipe"}]
        mock_pipes_documenter.generate_documentation.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_live_pipes_handles_exception(self, controller, mock_pipes_documenter):
        mock_pipes_documenter.generate_documentation.side_effect = Exception("boom")

        response = await controller.get_live_pipes()

        assert response.success is False
        assert response.error == "live_pipes_failed"

    @pytest.mark.asyncio
    async def test_get_live_output_types_success(self, controller):
        from src.features.generation.output_types import (
            OutputTypeRegistry,
            OutputTypeSpec,
        )
        from src.pipelines.outputs import GenerationOutput

        class FakeOutput(GenerationOutput):
            pass

        fake_registry = OutputTypeRegistry()
        fake_registry.register(OutputTypeSpec(
            output_cls=FakeOutput,
            key="fake",
            message_type="fake_update",
            serializer=lambda output, ctx: {},
            handler_cls=None,
        ))

        import src.features.docs.routes as docs_controller_module
        original_registry = docs_controller_module.output_type_registry
        docs_controller_module.output_type_registry = fake_registry
        try:
            response = await controller.get_live_output_types()
        finally:
            docs_controller_module.output_type_registry = original_registry

        assert response.success is True
        assert response.data["total"] == 1
        assert response.data["output_types"][0]["key"] == "fake"
        assert response.data["output_types"][0]["has_handler"] is False
