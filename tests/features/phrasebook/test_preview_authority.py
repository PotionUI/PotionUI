from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.features.phrasebook.dto import GeneratePreviewRequest
from src.platform.security.api_scopes import ApiScope
from src.platform.security.principal import KeyGrant
from src.platform.security.user import AccountType, User
from tests.features.phrasebook import test_routes as base

category_repository = base.category_repository
value_repository = base.value_repository
plugins = base.plugins
op_registry = base.op_registry
controller = base.controller
mock_operations = base.mock_operations


def person(account_type):
    return User(id="u1", username="u", email="u@example.com", password_hash="h", account_type=account_type)


def key_without_admin():
    grant = KeyGrant(key_id="k1", user_id="u1", scopes=frozenset({ApiScope.GENERATE}), display_prefix="pui_aaaaaaaa")
    return replace(person(AccountType.ADMIN), key=grant, account_type=AccountType.USER)


@pytest.mark.parametrize("label,caller,expected", [
    ("an admin session", person(AccountType.ADMIN), True),
    ("a plain user", person(AccountType.USER), False),
    ("a key without admin of an admin", key_without_admin(), False),
])
async def test_the_preview_run_is_started_with_the_callers_authority(controller, mock_operations, label, caller, expected):
    controller.preview_generator.generate_previews = AsyncMock(return_value={})
    request = GeneratePreviewRequest(session_id="s", prompt_template="<< value >>", mode="txt2img")

    await controller.generate_previews("cat-1", request, caller)

    assert controller.preview_generator.generate_previews.call_args.kwargs.get("acts_as_admin", False) is expected, label
