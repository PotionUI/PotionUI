from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.presets.routes import build_router as build_presets_router
from src.features.recipes.routes import build_router as build_recipes_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

ROUTES = [
    (build_recipes_router, "post", "/api/recipes/modes-vid/plan", {"preselect_modes": ["video"]}),
    (build_recipes_router, "get", "/api/recipes/modes-vid/modes/video/removal", None),
    (build_recipes_router, "post", "/api/recipes/modes-vid/modes/video/removal", {"model_ids": ["m1"]}),
    (build_recipes_router, "post", "/api/recipes/modes-vid/runs", {"preselect_modes": ["video"]}),
    (build_recipes_router, "post", "/api/recipes/runs/r1/consent/modes.plan", {"modes": ["video"], "target": "local"}),
    (build_presets_router, "get", "/api/presets/VID/requirements", None),
]


def _client(build_router, role=None):
    app = FastAPI()
    app.include_router(build_router(MagicMock()))
    if role is not None:
        app.dependency_overrides[get_current_active_user] = lambda: User(
            id="u1", username="u", email="u@example.com", password_hash="h", account_type=role,
        )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("build_router,method,path,body", ROUTES)
def test_a_regular_user_is_refused(build_router, method, path, body):
    response = getattr(_client(build_router, AccountType.USER), method)(path, **({"json": body} if body is not None else {}))
    assert response.status_code == 403


@pytest.mark.parametrize("build_router,method,path,body", ROUTES)
def test_an_anonymous_request_is_refused(build_router, method, path, body):
    response = getattr(_client(build_router), method)(path, **({"json": body} if body is not None else {}))
    assert response.status_code == 401


@pytest.mark.parametrize("build_router,method,path,body", ROUTES)
def test_an_admin_gets_past_the_gate(build_router, method, path, body):
    response = getattr(_client(build_router, AccountType.ADMIN), method)(path, **({"json": body} if body is not None else {}))
    assert response.status_code not in (401, 403)
