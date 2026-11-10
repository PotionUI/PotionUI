from unittest.mock import Mock

import pytest

from src.features.collections.dto import CreateCollectionRequest
from src.features.collections.routes import CollectionController
from src.platform.security.user import AccountType


def _user(account_type=AccountType.USER):
    user = Mock()
    user.id = "user-1"
    user.account_type = account_type
    return user


@pytest.fixture
def repository():
    return Mock()


@pytest.fixture
def controller(repository):
    return CollectionController(repository, {})


@pytest.mark.asyncio
async def test_validation_text_stays_user_facing(controller, repository):
    result = await controller.create_collection(CreateCollectionRequest(name=" ", scope="history"), _user())

    assert result.success is False
    assert result.error == "create_collection_failed"
    assert result.message == "Collection name is required"


@pytest.mark.asyncio
async def test_missing_collection_answers_its_typed_code(controller, repository):
    repository.delete.return_value = False

    result = await controller.delete_collection("missing", "history", _user())

    assert result.success is False
    assert result.error == "collection_not_found"


@pytest.mark.asyncio
async def test_unexpected_errors_are_sanitised_for_users_and_visible_to_admins(controller, repository):
    repository.list.side_effect = RuntimeError("db at /srv/app/secret.sqlite is locked")

    regular = await controller.list_collections("history", _user())
    admin = await controller.list_collections("history", _user(AccountType.ADMIN))

    assert regular.error == "list_collections_failed"
    assert "secret" not in regular.message
    assert regular.message.startswith("Failed to list collections")
    assert "locked" in admin.message
    assert "/srv/app" not in admin.message
