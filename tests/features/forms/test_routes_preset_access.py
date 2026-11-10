from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.forms.routes import FormController, build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

PRESET = "family-variant"


def _client(account_type, available):
    repo = Mock()
    repo.get_available_preset_ids_for_user.return_value = available
    controller = FormController(Mock(), Mock(), repo)
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(form_controller=controller)))
    user = User(id="u1", username="u1", email="u1@x.test", password_hash="h", account_type=account_type)
    app.dependency_overrides[get_current_active_user] = lambda: user
    return TestClient(app)


def test_defaults_of_an_unassigned_preset_are_a_404():
    response = _client(AccountType.USER, ["family-other"]).get(f"/api/form/defaults/{PRESET}")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "preset_not_found"


@pytest.mark.parametrize("account_type,available", [(AccountType.USER, [PRESET]), (AccountType.ADMIN, [])])
def test_defaults_of_an_assigned_preset_or_for_an_admin_are_served(account_type, available):
    response = _client(account_type, available).get(f"/api/form/defaults/{PRESET}")

    assert response.status_code == 200
    assert response.json()["success"] is True
