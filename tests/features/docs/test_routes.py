"""Tests for DocsController."""
import pytest
from unittest.mock import Mock

from src.features.docs.routes import DocsController
from src.features.docs.manager import DocForbiddenError, DocIsLiveError, DocNotFoundError
from src.platform.security.user import User, AccountType


class TestDocsController:
    """Tests for the DocsController class."""

    @pytest.fixture
    def mock_docs_manager(self):
        manager = Mock()
        manager.build_tree.return_value = {
            "sections": [{"id": "user", "title": "User Guide", "items": []}]
        }
        manager.get_content.return_value = {
            "id": "user/doc", "title": "Doc", "markdown": "Body"
        }
        return manager

    @pytest.fixture
    def mock_developer_manager(self):
        manager = Mock()
        manager.get_pipes_documentation.return_value = {"pipes": [{"name": "test_pipe"}], "total": 1}
        return manager

    @pytest.fixture
    def controller(self, mock_docs_manager, mock_developer_manager):
        return DocsController(mock_docs_manager, mock_developer_manager)

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
    async def test_get_tree_for_regular_user_requests_non_admin_tree(self, controller, mock_docs_manager):
        response = await controller.get_tree(is_admin=False)

        assert response.success is True
        mock_docs_manager.build_tree.assert_called_once_with(False)
        assert response.data["sections"][0]["id"] == "user"

    @pytest.mark.asyncio
    async def test_get_tree_for_admin_requests_admin_tree(self, controller, mock_docs_manager):
        mock_docs_manager.build_tree.return_value = {
            "sections": [
                {"id": "user", "title": "User Guide", "items": []},
                {"id": "developer", "title": "Developer", "items": []},
            ]
        }

        response = await controller.get_tree(is_admin=True)

        assert response.success is True
        mock_docs_manager.build_tree.assert_called_once_with(True)
        assert [s["id"] for s in response.data["sections"]] == ["user", "developer"]

    @pytest.mark.asyncio
    async def test_get_tree_handles_exception(self, controller, mock_docs_manager):
        mock_docs_manager.build_tree.side_effect = Exception("boom")

        response = await controller.get_tree(is_admin=False)

        assert response.success is False
        assert response.error == "docs_tree_failed"

    # ---- content 200/403/404/400 paths ----

    @pytest.mark.asyncio
    async def test_get_content_success(self, controller, mock_docs_manager):
        response = await controller.get_content("user/doc", is_admin=False)

        assert response.success is True
        assert response.data["markdown"] == "Body"
        mock_docs_manager.get_content.assert_called_once_with("user/doc", False)

    @pytest.mark.asyncio
    async def test_get_content_not_found_raises_404(self, controller, mock_docs_manager):
        from fastapi import HTTPException

        mock_docs_manager.get_content.side_effect = DocNotFoundError("Unknown doc id: 'user/nope'")

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_content("user/nope", is_admin=False)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_content_forbidden_raises_403(self, controller, mock_docs_manager):
        from fastapi import HTTPException

        mock_docs_manager.get_content.side_effect = DocForbiddenError("requires admin access")

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_content("dev/architecture", is_admin=False)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_content_live_doc_raises_400(self, controller, mock_docs_manager):
        from fastapi import HTTPException

        mock_docs_manager.get_content.side_effect = DocIsLiveError("no markdown content")

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_content("live/hooks", is_admin=True)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_get_content_handles_unexpected_exception(self, controller, mock_docs_manager):
        mock_docs_manager.get_content.side_effect = Exception("boom")

        response = await controller.get_content("user/doc", is_admin=False)

        assert response.success is False
        assert response.error == "doc_content_failed"

    # ---- live pipes / output types ----

    @pytest.mark.asyncio
    async def test_get_live_pipes_success(self, controller, mock_developer_manager):
        response = await controller.get_live_pipes()

        assert response.success is True
        assert response.data["pipes"] == [{"name": "test_pipe"}]
        mock_developer_manager.get_pipes_documentation.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_live_pipes_handles_exception(self, controller, mock_developer_manager):
        mock_developer_manager.get_pipes_documentation.side_effect = Exception("boom")

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
