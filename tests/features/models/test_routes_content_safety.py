from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.content_safety.restrict import provider_flags_nsfw, restrict_model_list, strip_model_media
from src.features.models.routes import ModelController
from src.platform.security.user import AccountType, User


def user():
    return User(
        id="kid", username="kid", email="k@example.com", password_hash="h", account_type=AccountType.USER,
    )


def model(model_id, nsfw=False):
    return {
        "id": model_id,
        "name": model_id,
        "preview_media": {"url": "/api/media/models/x.png"},
        "files": [{"url": "/api/media/models/x.png"}],
        "providers": [{"provider": "civitai", "nsfw": nsfw}],
    }


def controller(restricted, full_model=None):
    safety = SimpleNamespace(
        is_restricted=lambda user_id: restricted,
        viewable_generation_ids=lambda user_id, ids: {"gen-safe"},
    )
    collaborators = Mock()
    collaborators.catalog.model_repo.get_by_id.return_value = full_model
    return ModelController(collaborators, Mock(), Mock(), content_safety=safety)


def test_nsfw_flag_is_read_from_dicts_and_records():
    assert provider_flags_nsfw([{"nsfw": True}]) is True
    assert provider_flags_nsfw([SimpleNamespace(nsfw=True)]) is True
    assert provider_flags_nsfw([{"nsfw": False}, SimpleNamespace(nsfw=False)]) is False
    assert provider_flags_nsfw(None) is False


def test_restricted_listing_hides_nsfw_models_and_strips_media_from_the_rest():
    listing = restrict_model_list([model("a"), model("b", nsfw=True)])

    assert [m["id"] for m in listing] == ["a"]
    assert listing[0]["preview_media"] is None
    assert listing[0]["files"] == []


def test_stripping_media_does_not_mutate_the_source():
    original = model("a")

    strip_model_media(original)

    assert original["files"]


@pytest.mark.asyncio
async def test_restricted_list_endpoint_filters_models_and_adjusts_the_total():
    payload = {"models": [model("a"), model("b", nsfw=True)], "total": 2}
    with patch("src.features.models.routes.operations.list_models", AsyncMock(return_value=payload)):
        response = await controller(True).list_models(user())

    assert [m["id"] for m in response.data["models"]] == ["a"]
    assert response.data["total"] == 1
    assert response.data["models"][0]["files"] == []


@pytest.mark.asyncio
async def test_unrestricted_list_endpoint_is_untouched():
    payload = {"models": [model("a"), model("b", nsfw=True)], "total": 2}
    with patch("src.features.models.routes.operations.list_models", AsyncMock(return_value=payload)):
        response = await controller(False).list_models(user())

    assert len(response.data["models"]) == 2
    assert response.data["models"][0]["files"]


@pytest.mark.asyncio
async def test_restricted_detail_of_an_nsfw_model_is_not_found():
    full = SimpleNamespace(providers=[SimpleNamespace(nsfw=True)])
    with patch("src.features.models.routes.operations.get_model_by_id", AsyncMock(return_value={"model": model("b")})):
        response = await controller(True, full).get_model_by_id("b", user())

    assert response.success is False
    assert response.error == "model_not_found"


@pytest.mark.asyncio
async def test_restricted_detail_of_a_clean_model_loses_its_media():
    full = SimpleNamespace(providers=[SimpleNamespace(nsfw=False)])
    with patch("src.features.models.routes.operations.get_model_by_id", AsyncMock(return_value={"model": model("a")})):
        response = await controller(True, full).get_model_by_id("a", user())

    assert response.data["model"]["files"] == []
    assert response.data["model"]["preview_media"] is None


@pytest.mark.asyncio
async def test_restricted_preview_list_is_empty():
    previews = [{"id": "p1", "url": "/x.png"}]
    with patch(
        "src.features.models.routes.operations.list_model_previews_for_user", Mock(return_value=previews)
    ):
        response = await controller(True).list_model_previews("a", user())

    assert response.data == {"previews": []}


@pytest.mark.asyncio
async def test_restricted_model_generations_keep_only_fully_safe_ones():
    payload = {"generations": [{"id": "gen-safe"}, {"id": "gen-bad"}], "total": 2, "pagination": {}}
    with patch("src.features.models.routes.operations.get_model_generations", AsyncMock(return_value=payload)):
        response = await controller(True).get_model_generations("a", user())

    assert [g["id"] for g in response.data["generations"]] == ["gen-safe"]
    assert response.data["total"] == 1
