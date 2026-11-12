from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.generation.dto import GenerationRequest
from src.features.sessions.dto import Session
from src.features.sessions.generate import SessionRequests
from src.features.sessions.repository import SessionRepository
from src.features.sessions.routes import build_router
from src.platform.http.base_controller import APIResponse
from tests.features.sessions.support import (
    FIXED,
    PRESET,
    Loader,
    Models,
    PresetDb,
    Settings,
    Users,
    Versions,
    entry,
    segment,
    template,
)
from src.platform.security.user import AccountType


class FakeGeneration:
    def __init__(self):
        self.calls = []

    async def start_generation(self, request, user):
        self.calls.append((request, user))
        return APIResponse(success=True, data={"generation_id": "g1", "status": {"status": "pending"}, "queue_position": None})


@pytest.fixture
def setup(world, tmp_path):
    ids = [world.alice.id, world.bob.id, world.root.id]
    service = SessionRequests(
        sessions=SessionRepository(),
        versions=Versions(),
        preset_loader=Loader(template()),
        preset_db=PresetDb({user_id: [PRESET] for user_id in ids}),
        users=Users(**{world.alice.id: AccountType.USER, world.bob.id: AccountType.USER, world.root.id: AccountType.ADMIN}),
        models=Models({"m1": "One"}, {user_id: ["m1"] for user_id in ids}),
        settings=Settings(str(tmp_path)),
        prompt_batch=lambda engine: True,
    )
    generation = FakeGeneration()
    container = SimpleNamespace(
        session_controller=Mock(), session_requests=service, settings=world.stack.settings, _generation_controller=generation
    )
    app = FastAPI()
    app.include_router(build_router(container))
    client = TestClient(app, raise_server_exceptions=False)
    repository = SessionRepository()
    for owner, session_id in ((world.alice, "alice-1"), (world.bob, "bob-1")):
        repository.create(Session(id=session_id, user_id=owner.id, preset_id=PRESET, name=f"{owner.id}-portrait", data={"txt2img": entry()}))
    return SimpleNamespace(client=client, generation=generation, repository=repository)


def key_for(world, owner, *scopes, days=None):
    world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "mcp", "admin"])
    world.stack.settings.set_setting("mcp_enabled", True)
    return world.bearer(world.mint(owner, scopes=scopes, days=days)[1])


def error_of(response):
    return response.json()["detail"]["error"]


class TestScopes:
    def test_no_key_and_no_login_reach_none_of_the_routes(self, setup):
        assert setup.client.get("/api/sessions").status_code == 401
        assert setup.client.get("/api/sessions/alice-1/request").status_code == 401
        assert setup.client.post("/api/sessions/alice-1/generate", json={}).status_code == 401
        assert setup.generation.calls == []

    def test_a_read_key_lists_and_describes_but_cannot_generate_or_dry_run(self, world, setup):
        headers = key_for(world, world.alice, "read")

        listed = setup.client.get("/api/sessions", headers=headers)
        described = setup.client.get("/api/sessions/alice-1/request", headers=headers)
        generated = setup.client.post("/api/sessions/alice-1/generate", json={}, headers=headers)
        dry = setup.client.post("/api/sessions/alice-1/generate", json={"dry_run": True}, headers=headers)

        assert (listed.status_code, described.status_code) == (200, 200)
        assert (generated.status_code, error_of(generated)) == (403, "insufficient_scope")
        assert generated.headers["www-authenticate"] == 'Bearer error="insufficient_scope", scope="generate"'
        assert dry.status_code == 403
        assert setup.generation.calls == []

    def test_a_generate_key_generates_but_cannot_read_the_listing_or_template(self, world, setup):
        headers = key_for(world, world.alice, "generate")

        generated = setup.client.post("/api/sessions/alice-1/generate", json={"overrides": {"prompt": "a fox"}}, headers=headers)
        dry = setup.client.post("/api/sessions/alice-1/generate", json={"dry_run": True}, headers=headers)
        listed = setup.client.get("/api/sessions", headers=headers)
        described = setup.client.get("/api/sessions/alice-1/request", headers=headers)

        assert (generated.status_code, dry.status_code) == (200, 200)
        assert (listed.status_code, described.status_code) == (403, 403)
        assert len(setup.generation.calls) == 1

    def test_a_content_key_cannot_generate(self, world, setup):
        headers = key_for(world, world.alice, "content")
        assert setup.client.post("/api/sessions/alice-1/generate", json={}, headers=headers).status_code == 403

    def test_a_logged_in_browser_session_needs_no_scope(self, world, setup):
        headers = world.session("alice")
        assert setup.client.get("/api/sessions", headers=headers).status_code == 200
        assert setup.client.post("/api/sessions/alice-1/generate", json={}, headers=headers).status_code == 200


class TestOwnership:
    def test_a_key_sees_only_its_owners_sessions(self, world, setup):
        headers = key_for(world, world.alice, "read", "generate")

        listed = setup.client.get("/api/sessions", headers=headers).json()["data"]

        assert [item["id"] for item in listed] == ["alice-1"]
        for path in ("/request",):
            assert setup.client.get(f"/api/sessions/bob-1{path}", headers=headers).status_code == 404
        assert setup.client.post("/api/sessions/bob-1/generate", json={}, headers=headers).status_code == 404

    def test_an_admin_key_still_gets_not_found_on_another_users_session(self, world, setup):
        headers = key_for(world, world.root, "read", "generate", "admin", days=30)

        assert setup.client.get("/api/sessions/alice-1/request", headers=headers).status_code == 404
        assert setup.client.post("/api/sessions/alice-1/generate", json={}, headers=headers).status_code == 404
        assert setup.generation.calls == []


class TestGenerate:
    def post(self, setup, world, body, session_id="alice-1", owner=None):
        headers = key_for(world, owner or world.alice, "read", "generate")
        return setup.client.post(f"/api/sessions/{session_id}/generate", json=body, headers=headers)

    def test_it_starts_through_the_generation_controller_with_the_resolved_request(self, world, setup):
        response = self.post(setup, world, {
            "overrides": {"prompt": "a glass bottle", "negative_prompt": "blurry", "seed": 9, "count": 3, "form": {"steps": 12}},
            "idempotency_key": "nightly-1",
            "tag_ids": ["t1"],
        })

        data = response.json()["data"]
        request, user = setup.generation.calls[0]
        assert response.status_code == 200
        assert isinstance(request, GenerationRequest) and user.id == world.alice.id
        assert request.prompts[0].positive == "a glass bottle" and request.prompts[0].negative == "blurry"
        assert request.form_data["seed"] == 9 and request.form_data["quantity"] == 3 and request.form_data["steps"] == 12
        assert request.idempotency_key == "nightly-1" and request.tag_ids == ["t1"]
        assert data["generation_id"] == "g1" and data["details_url"].endswith("/history?generation=g1")
        assert data["session"]["id"] == "alice-1" and data["session"]["mode"] == "txt2img"
        assert data["resolved_request"]["prompts"][0]["positive"] == "a glass bottle"
        assert "idempotency_key" not in data["resolved_request"] and "tab_id" not in data["resolved_request"]
        assert data["notes"] == []

    def test_the_resolved_request_can_be_replayed_to_start_unchanged(self, world, setup):
        data = self.post(setup, world, {"overrides": {"prompt": "replay me"}}).json()["data"]
        replay = GenerationRequest(**data["resolved_request"])
        original = setup.generation.calls[0][0]

        assert replay.prompts == original.prompts and replay.form_data == original.form_data and replay.mode == original.mode

    def test_dry_run_validates_everything_and_starts_nothing(self, world, setup):
        response = self.post(setup, world, {"dry_run": True, "overrides": {"prompt": "just checking"}})

        assert response.status_code == 200 and setup.generation.calls == []
        assert response.json()["data"]["dry_run"] is True
        assert response.json()["data"]["resolved_request"]["prompts"][0]["positive"] == "just checking"
        refused = self.post(setup, world, {"dry_run": True, "overrides": {"form": {"nope": 1}}})
        assert (refused.status_code, error_of(refused)) == (422, "unknown_field")

    @pytest.mark.parametrize("body,status,code", [
        ({"overrides": {"form": {"cfg": 7}}}, 422, "unknown_field"),
        ({"overrides": {"variables": {"x": "y"}}}, 422, "unknown_variable"),
        ({"overrides": {"form": {"steps": 900}}}, 422, "form_validation_failed"),
        ({"mode": "img2img"}, 404, "session_mode_not_found"),
    ])
    def test_refusals_keep_stable_codes_and_start_nothing(self, world, setup, body, status, code):
        response = self.post(setup, world, body)

        assert (response.status_code, error_of(response)) == (status, code)
        assert setup.generation.calls == []

    def test_unknown_override_keys_and_bad_shapes_are_rejected_by_the_schema(self, world, setup):
        assert self.post(setup, world, {"overrides": {"promt": "typo"}}).status_code == 422
        assert self.post(setup, world, {"overrides": {"count": 0}}).status_code == 422
        assert self.post(setup, world, {"surprise": 1}).status_code == 422
        assert setup.generation.calls == []

    def test_a_stale_settings_hash_is_a_409_with_the_new_hash(self, world, setup):
        response = self.post(setup, world, {"if_settings_hash": "0" * 64})
        detail = response.json()["detail"]
        assert (response.status_code, detail["error"]) == (409, "session_changed") and len(detail["settings_hash"]) == 64

    def test_several_modes_need_mode_and_a_director_mode_is_refused(self, world, setup):
        setup.repository.create(Session(id="two", user_id=world.alice.id, preset_id=PRESET, name="two", data={"txt2img": entry(), "other": entry()}))

        missing = self.post(setup, world, {}, session_id="two")

        assert (missing.status_code, error_of(missing)) == (400, "mode_required")

    def test_the_template_endpoint_matches_what_generate_accepts(self, world, setup):
        headers = key_for(world, world.alice, "read")
        data = setup.client.get("/api/sessions/alice-1/request?mode=txt2img", headers=headers).json()["data"]

        assert data["session"]["settings_hash"] and data["resolved_request"]["preset_id"] == PRESET
        assert {"seed": "seed", "count": "quantity"} == data["shortcuts"]
        body = {"overrides": {"form": {item["name"]: item["value"] for item in data["fields"] if "value" in item and item["value"] is not None and item["name"] != "image"}}}
        assert self.post(setup, world, body).status_code == 200

    def test_nothing_of_a_fixed_prompt_can_appear_in_any_response(self, world, setup):
        responses = [
            self.post(setup, world, {"overrides": {"prompt": "a fox"}}),
            self.post(setup, world, {"dry_run": True}),
            setup.client.get("/api/sessions/alice-1/request", headers=key_for(world, world.alice, "read")),
        ]
        assert all(FIXED not in response.text for response in responses)
        request = setup.generation.calls[0][0]
        assert FIXED not in repr(request.model_dump())

    def test_a_kit_citation_in_the_session_prompt_reaches_the_request_untouched(self, world, setup):
        cite = "@[references:kit:k1/i1]"
        setup.repository.create(Session(
            id="kit", user_id=world.alice.id, preset_id=PRESET, name="kit",
            data={"txt2img": entry(promptSegments=[segment("a", f"{cite} at dawn")], prompt="")},
        ))

        self.post(setup, world, {}, session_id="kit")

        assert setup.generation.calls[0][0].prompts[0].positive == f"{cite} at dawn"
