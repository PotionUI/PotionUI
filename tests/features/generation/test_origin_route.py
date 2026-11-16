from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.forms.binding import BoundForm
from src.features.generation.routes import GenerationController, build_router
from src.platform.security import current_user
from src.platform.security.user import AccountType, User
from tests.features.generation.test_orchestrator_fixed_prompt import FILM, make_orchestrator

TOKEN = "a.session.jwt"


def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None, **_):
    return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])


def test_origin_posted_to_the_real_start_route_never_reaches_the_stored_generation():
    orchestrator, _, _ = make_orchestrator(FILM)
    orchestrator.preset_template_loader.load_preset_by_id = lambda preset_id: Mock(engine="native", vars={})
    controller = GenerationController(orchestrator, Mock(), Mock(), Mock())
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(_generation_controller=controller)))
    auth = Mock()
    auth.get_user_from_token.side_effect = lambda token: User(
        id="u1", username="u1", email="u1@example.com", password_hash="h", account_type=AccountType.USER
    ) if token == TOKEN else None
    previous = (current_user._auth, current_user._key_resolver)
    current_user.set_auth(auth)
    try:
        with patch("src.features.generation.orchestrator.bind_form", side_effect=passthrough), \
                patch("src.features.generation.orchestrator.generation_repo") as repo, \
                patch("src.features.generation.status_tracker.generation_repo", repo), \
                patch("src.features.generation.orchestrator.generate_ulid", return_value="g1"):
            repo.get_by_id = Mock(return_value=Mock(user_id="u1"))
            with TestClient(app) as client:
                response = client.post(
                    "/api/generations/start",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                    json={
                        "preset_id": "p", "mode": "edit", "prompt": "x", "form_data": {"origin": {"kind": "editor"}},
                        "origin": {"kind": "editor", "edit_id": "forged", "run_id": "er_forged"},
                    },
                )
    finally:
        current_user._auth, current_user._key_resolver = previous

    assert response.status_code == 200, response.text
    stored = repo.create.call_args.args[0]
    assert stored.origin is None
