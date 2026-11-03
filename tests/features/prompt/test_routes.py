from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.features.models.exceptions import ModelNotFoundException
from src.features.prompt.resource_sources import prompt_resource_sources
from src.features.prompt.routes import PromptResourceSourceController, build_router
from src.platform.plugins.prompt_resource_sources import PromptResourceSource, PromptResourceSourceRegistry
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.fixtures.prompt_source_plugin import FAKE_SOURCE, fake_prompt_source


def _user(account_type=AccountType.USER):
    user = Mock(spec=User)
    user.id = "user-a"
    user.account_type = account_type
    return user


@pytest.fixture
def lookups(monkeypatch):
    calls = []

    async def get_model_by_id(collaborators, model_id, user=None, admin=False):
        calls.append({"model_id": model_id, "user": user, "admin": admin})
        if model_id == "someone-elses":
            raise ModelNotFoundException(f"Model '{model_id}' not found")
        return {"model": {"id": model_id}}

    monkeypatch.setattr("src.features.prompt.routes.model_operations.get_model_by_id", get_model_by_id)
    return calls


def _spy_registry(members):
    registry = PromptResourceSourceRegistry()
    registry.register(PromptResourceSource(source_id="spy", field_type="lora_picker", label="Spy", badge="Spy", members=members))
    return registry


async def _get(registry, user, url, model_file=None):
    container = Mock()
    container.prompt_resource_source_registry = registry
    container.model_locator.path_for_model = model_file or Mock()
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("account_type, admin", [(AccountType.USER, False), (AccountType.ADMIN, True)])
async def test_the_access_check_and_the_handler_run_as_the_caller(lookups, account_type, admin):
    members = Mock(return_value=[{"kind": "image", "name": "one"}])
    caller = _user(account_type)

    await PromptResourceSourceController(_spy_registry(members), Mock(), Mock()).get_members("spy", "model:01PAIR", caller)

    assert lookups == [{"model_id": "01PAIR", "user": caller, "admin": admin}]
    item_key, context = members.call_args.args
    assert (item_key, context.user_id, context.is_admin) == ("model:01PAIR", "user-a", admin)


@pytest.mark.asyncio
async def test_an_unreadable_item_is_a_422_without_the_handlers_text(lookups, tmp_path):
    members = Mock(side_effect=ValueError(f"cannot read {tmp_path / 'gone.safetensors'}"))
    controller = PromptResourceSourceController(_spy_registry(members), Mock(), Mock())

    with pytest.raises(HTTPException) as raised:
        await controller.get_members("spy", "model:01GONE", _user())

    assert raised.value.status_code == 422
    assert raised.value.detail["error"] == "prompt_resource_unreadable"
    assert str(tmp_path) not in str(raised.value.detail)


@pytest.mark.asyncio
async def test_an_item_the_user_cannot_see_is_404_and_the_handler_is_never_called(lookups):
    members = Mock()
    user = _user()

    response = await _get(_spy_registry(members), user, "/api/prompt-resources/sources/spy/members?item=model:someone-elses")

    assert response.status_code == 404
    assert lookups == [{"model_id": "someone-elses", "user": user, "admin": False}]
    members.assert_not_called()


@pytest.mark.asyncio
async def test_an_unknown_source_is_404_before_any_lookup(lookups):
    members = Mock()

    response = await _get(_spy_registry(members), _user(), "/api/prompt-resources/sources/nope/members?item=model:01PAIR")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "prompt_resource_source_not_found"
    assert lookups == []
    members.assert_not_called()


@pytest.mark.asyncio
async def test_an_item_that_is_not_a_model_reference_is_404(lookups):
    members = Mock()

    response = await _get(_spy_registry(members), _user(), "/api/prompt-resources/sources/spy/members?item=/etc/passwd")

    assert response.status_code == 404
    assert lookups == []
    members.assert_not_called()


@pytest.mark.asyncio
async def test_a_crashing_handler_is_a_clean_422(lookups):
    members = Mock(side_effect=RuntimeError("secret internals at /srv/x"))

    response = await _get(_spy_registry(members), _user(), "/api/prompt-resources/sources/spy/members?item=model:01PAIR")

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "prompt_resource_unreadable"
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_a_plugin_source_answers_through_the_generic_route(lookups, fake_prompt_source):
    response = await _get(
        prompt_resource_sources(), _user(), f"/api/prompt-resources/sources/{FAKE_SOURCE}/members?item=model:pair",
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"members": [{"kind": "image", "name": "pair image"}, {"kind": "audio", "name": "pair audio"}]}
