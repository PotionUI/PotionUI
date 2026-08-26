"""Tests for ModelController's model-library endpoints (favorite / library-name).

There's no pre-existing test_model_controller.py; this covers the new
favorite/custom-name surface added for the user-level model library feature,
not the full pre-existing controller (indexing, providers, tags, etc.).
"""
import pytest
from unittest.mock import Mock

from src.features.models.routes import ModelController
from src.features.models.dto import ModelFavoriteRequest, ModelLibraryNameRequest
from src.features.model_library.records.user_model_meta import UserModelMeta
from src.platform.security.user import User


class TestParseRef:
    """`ModelController._parse_ref` - resolves a recommendation's opaque, provider-
    native `ref` string to the `(provider_model_id, provider_version_id)` pair
    `MarketplaceProviderBase.get_download_url` expects. See docs/presets.md
    "recommendations" for the full convention list."""

    def test_already_final_pair_passthrough(self):
        assert ModelController._parse_ref(
            '{"provider_model_id": "x", "provider_version_id": "y"}'
        ) == ("x", "y")

    def test_civitai_generic_shape(self):
        assert ModelController._parse_ref(
            '{"model_id": "12345", "version_id": "67890"}'
        ) == ("12345", "67890")

    def test_huggingface_shape_with_explicit_revision(self):
        assert ModelController._parse_ref(
            '{"repo": "org/name", "file": "a.safetensors", "revision": "v2"}'
        ) == ("org/name", "v2@a.safetensors")

    def test_huggingface_shape_defaults_revision_to_main(self):
        assert ModelController._parse_ref(
            '{"repo": "org/name", "file": "a.safetensors"}'
        ) == ("org/name", "main@a.safetensors")

    def test_huggingface_shape_without_file_has_no_version(self):
        assert ModelController._parse_ref('{"repo": "org/name"}') == ("org/name", None)

    def test_bare_string_is_provider_model_id_with_no_version(self):
        assert ModelController._parse_ref("bare-id-string") == ("bare-id-string", None)

    def test_none_ref(self):
        assert ModelController._parse_ref(None) == (None, None)

    def test_empty_ref(self):
        assert ModelController._parse_ref("") == (None, None)


class TestModelControllerLibraryEndpoints:
    """Tests for the favorite/custom-name endpoints backed by UserModelMetaRepository."""

    @pytest.fixture
    def mock_index_manager(self):
        return Mock()

    @pytest.fixture
    def mock_user_model_meta_repository(self):
        return Mock()

    @pytest.fixture
    def controller(self, mock_index_manager, mock_user_model_meta_repository):
        return ModelController(mock_index_manager, mock_user_model_meta_repository, Mock())

    @pytest.fixture
    def user_a(self):
        user = Mock(spec=User)
        user.id = "user-a"
        return user

    @pytest.fixture
    def user_b(self):
        user = Mock(spec=User)
        user.id = "user-b"
        return user

    # ========== Favorite ==========

    @pytest.mark.asyncio
    async def test_set_favorite_success(self, controller, mock_user_model_meta_repository, user_a):
        meta = UserModelMeta(user_id="user-a", model_id="model-1", is_favorite=True)
        mock_user_model_meta_repository.set_favorite.return_value = meta

        request = ModelFavoriteRequest(is_favorite=True)
        result = await controller.set_model_favorite("model-1", request, user_a)

        assert result.success is True
        assert result.data["meta"]["is_favorite"] is True
        mock_user_model_meta_repository.set_favorite.assert_called_once_with("user-a", "model-1", True)

    @pytest.mark.asyncio
    async def test_set_favorite_scoped_per_user(self, controller, mock_user_model_meta_repository, user_a, user_b):
        """Two users favoriting the same model must each call with their own user_id."""
        mock_user_model_meta_repository.set_favorite.return_value = UserModelMeta(
            user_id="user-a", model_id="model-1", is_favorite=True
        )
        await controller.set_model_favorite("model-1", ModelFavoriteRequest(is_favorite=True), user_a)

        mock_user_model_meta_repository.set_favorite.return_value = UserModelMeta(
            user_id="user-b", model_id="model-1", is_favorite=False
        )
        await controller.set_model_favorite("model-1", ModelFavoriteRequest(is_favorite=False), user_b)

        mock_user_model_meta_repository.set_favorite.assert_any_call("user-a", "model-1", True)
        mock_user_model_meta_repository.set_favorite.assert_any_call("user-b", "model-1", False)

    @pytest.mark.asyncio
    async def test_set_favorite_error(self, controller, mock_user_model_meta_repository, user_a):
        mock_user_model_meta_repository.set_favorite.side_effect = ValueError("boom")

        result = await controller.set_model_favorite("model-1", ModelFavoriteRequest(is_favorite=True), user_a)

        assert result.success is False
        assert result.error == "set_favorite_failed"

    # ========== Custom name ==========

    @pytest.mark.asyncio
    async def test_set_library_name_success(self, controller, mock_user_model_meta_repository, user_a):
        meta = UserModelMeta(user_id="user-a", model_id="model-1", custom_name="My Checkpoint")
        mock_user_model_meta_repository.set_custom_name.return_value = meta

        request = ModelLibraryNameRequest(name="My Checkpoint")
        result = await controller.set_model_library_name("model-1", request, user_a)

        assert result.success is True
        assert result.data["meta"]["custom_name"] == "My Checkpoint"
        mock_user_model_meta_repository.set_custom_name.assert_called_once_with("user-a", "model-1", "My Checkpoint")

    @pytest.mark.asyncio
    async def test_set_library_name_clear(self, controller, mock_user_model_meta_repository, user_a):
        meta = UserModelMeta(user_id="user-a", model_id="model-1", custom_name=None)
        mock_user_model_meta_repository.set_custom_name.return_value = meta

        request = ModelLibraryNameRequest(name=None)
        result = await controller.set_model_library_name("model-1", request, user_a)

        assert result.success is True
        assert result.data["meta"]["custom_name"] is None

    @pytest.mark.asyncio
    async def test_set_library_name_error(self, controller, mock_user_model_meta_repository, user_a):
        mock_user_model_meta_repository.set_custom_name.side_effect = ValueError("boom")

        result = await controller.set_model_library_name("model-1", ModelLibraryNameRequest(name="x"), user_a)

        assert result.success is False
        assert result.error == "set_library_name_failed"


class TestModelControllerAssignmentEndpoints:
    """Tests for the model-assignment summary/read endpoints backed by ModelIndexCollaborators."""

    @pytest.fixture
    def mock_index_manager(self):
        return Mock()

    @pytest.fixture
    def mock_user_model_meta_repository(self):
        return Mock()

    @pytest.fixture
    def controller(self, mock_index_manager, mock_user_model_meta_repository):
        return ModelController(mock_index_manager, mock_user_model_meta_repository, Mock())

    @pytest.mark.asyncio
    async def test_get_model_assignments_success(self, controller, mock_index_manager):
        mock_index_manager.assignments.get_model_assignments.return_value = {
            "model_id": "model-1",
            "assignments": [{"user_id": "user-a", "model_id": "model-1"}]
        }

        result = await controller.get_model_assignments("model-1")

        assert result.success is True
        assert result.data["assignments"][0]["user_id"] == "user-a"
        mock_index_manager.assignments.get_model_assignments.assert_called_once_with("model-1")

    @pytest.mark.asyncio
    async def test_get_model_assignments_error(self, controller, mock_index_manager):
        mock_index_manager.assignments.get_model_assignments.side_effect = Exception("boom")

        result = await controller.get_model_assignments("model-1")

        assert result.success is False
        assert result.error == "get_assignments_failed"

    @pytest.mark.asyncio
    async def test_get_model_assignment_summary_success(self, controller, mock_index_manager):
        mock_index_manager.assignments.get_assignment_summary.return_value = {
            "model-1": {"assignment_count": 2, "group_count": 1},
            "model-2": {"assignment_count": 0, "group_count": 0}
        }

        result = await controller.get_model_assignment_summary()

        assert result.success is True
        assert result.data["model-1"] == {"assignment_count": 2, "group_count": 1}
        assert result.data["model-2"] == {"assignment_count": 0, "group_count": 0}


class TestRouteOrder:
    """FastAPI dispatches in registration order, so every static GET sibling
    must be registered before the catch-all `GET /{model_id}` - otherwise it
    is swallowed as a model id ("Model 'location' not found")."""

    def test_static_get_paths_register_before_the_model_id_catch_all(self):
        from unittest.mock import Mock
        from fastapi.routing import APIRoute
        from src.features.models.routes import build_router

        router = build_router(Mock())
        get_paths = [
            r.path for r in router.routes
            if isinstance(r, APIRoute) and "GET" in r.methods
        ]
        catch_all = get_paths.index("/api/models/{model_id}")
        for static in (
            "/api/models/location", "/api/models/stats", "/api/models/types",
            "/api/models/assignment-summary", "/api/models/unindexed-count",
        ):
            assert get_paths.index(static) < catch_all, (
                f"{static} is registered after /{{model_id}} and can never match"
            )
