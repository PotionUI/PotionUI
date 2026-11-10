from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.presets.fixed_prompt import FixedPrompt
from src.features.presets.fixed_prompt_repository import PresetFixedPromptRepository
from src.features.presets.fixed_prompt_routes import build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.features.content_safety.fakes import build

EMPTY = {"prefix": "", "suffix": "", "negative": "", "modes": None}
FILM = {"prefix": "kodachrome still,", "suffix": "warm grain", "negative": "cartoonish", "modes": ["txt2img"]}


class _Loader:
    def __init__(self, templates):
        self.templates = templates

    def get(self, preset_id):
        return self.templates.get(preset_id)


def _template():
    return SimpleNamespace(
        modes={"txt2img": object(), "edit": object(), "upscale": object()},
        vars={"promptless_modes": ["upscale"]},
    )


def _user(account_type):
    return User(
        id="u1", username="u1", email="u1@example.com", password_hash="$2b$12$x",
        account_type=account_type, created_at=datetime.utcnow(), last_login=None,
    )


@pytest.fixture
def presets(mock_db):
    with mock_db.get_cursor() as cursor:
        cursor.execute("INSERT INTO presets (id, preset_id) VALUES ('row-1', 'film')")
        cursor.execute("INSERT INTO presets (id, preset_id) VALUES ('row-2', 'orphan')")
    repository = PresetFixedPromptRepository()
    repository.set("orphan", FixedPrompt(prefix="kept"))
    return repository


def _client(repository, account_type=AccountType.ADMIN, content_safety=None):
    loader = _Loader({"film": _template(), "shipped": _template()})
    container = SimpleNamespace(
        preset_template_loader=loader, preset_fixed_prompt_repository=repository, content_safety=content_safety,
    )
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: _user(account_type)
    return TestClient(app)


def _error(response):
    return response.json()["detail"]


class TestAdminOnly:
    def test_a_regular_user_cannot_read_it(self, presets):
        response = _client(presets, AccountType.USER).get("/api/presets/film/fixed-prompt")

        assert response.status_code == 403

    def test_a_regular_user_cannot_change_it(self, presets):
        response = _client(presets, AccountType.USER).put("/api/presets/film/fixed-prompt", json=FILM)

        assert response.status_code == 403
        assert presets.get("film") == FixedPrompt()


class TestReading:
    def test_an_installed_preset_starts_empty(self, presets):
        response = _client(presets).get("/api/presets/film/fixed-prompt")

        assert response.status_code == 200
        assert response.json()["data"] == EMPTY

    def test_a_preset_that_is_not_installed_reads_empty(self, presets):
        response = _client(presets).get("/api/presets/shipped/fixed-prompt")

        assert response.status_code == 200
        assert response.json()["data"] == EMPTY

    def test_a_row_whose_files_are_gone_still_reads_its_text(self, presets):
        response = _client(presets).get("/api/presets/orphan/fixed-prompt")

        assert response.json()["data"] == {**EMPTY, "prefix": "kept"}

    def test_an_unknown_preset_is_not_found(self, presets):
        response = _client(presets).get("/api/presets/nothing/fixed-prompt")

        assert response.status_code == 404
        assert _error(response)["error"] == "preset_not_found"


class TestSaving:
    def test_an_admin_saves_and_reads_it_back(self, presets):
        client = _client(presets)

        saved = client.put("/api/presets/film/fixed-prompt", json=FILM)

        assert saved.status_code == 200
        assert saved.json()["data"] == FILM
        assert client.get("/api/presets/film/fixed-prompt").json()["data"] == FILM
        assert presets.get("film") == FixedPrompt(
            prefix="kodachrome still,", suffix="warm grain", negative="cartoonish", modes=["txt2img"],
        )

    def test_null_modes_means_every_mode(self, presets):
        response = _client(presets).put("/api/presets/film/fixed-prompt", json={**FILM, "modes": None})

        assert response.json()["data"]["modes"] is None

    def test_repeated_modes_are_stored_once(self, presets):
        response = _client(presets).put("/api/presets/film/fixed-prompt", json={**FILM, "modes": ["edit", "edit"]})

        assert response.json()["data"]["modes"] == ["edit"]

    def test_an_empty_body_clears_it(self, presets):
        client = _client(presets)
        client.put("/api/presets/film/fixed-prompt", json=FILM)

        response = client.put("/api/presets/film/fixed-prompt", json=EMPTY)

        assert response.json()["data"] == EMPTY
        assert presets.get("film").is_empty

    def test_an_unknown_preset_is_not_found(self, presets):
        response = _client(presets).put("/api/presets/nothing/fixed-prompt", json=FILM)

        assert response.status_code == 404
        assert _error(response)["error"] == "preset_not_found"

    def test_a_preset_that_is_not_installed_is_refused(self, presets):
        response = _client(presets).put("/api/presets/shipped/fixed-prompt", json=FILM)

        assert response.status_code == 409
        assert _error(response)["error"] == "preset_not_installed"


class TestValidation:
    @pytest.mark.parametrize("body, message", [
        ({"prefix": "a", "suffix": "", "negative": ""}, "'modes' is required"),
        ({**FILM, "prefix": "x" * 2001}, "'prefix' is longer than 2000 characters"),
        ({**FILM, "suffix": "{warm|cold"}, "'suffix' has a syntax error"),
        ({**FILM, "negative": "<<if mood>>dark"}, "'negative' has a syntax error"),
        ({**FILM, "prefix": 7}, "'prefix' must be text"),
        ({**FILM, "modes": ["video"]}, "Unknown mode 'video'"),
        ({**FILM, "modes": ["upscale"]}, "Mode 'upscale' takes no prompt"),
        ({**FILM, "modes": []}, "'modes' must name at least one mode"),
        ({**FILM, "modes": "txt2img"}, "'modes' must be null or a list"),
        (["not", "an", "object"], "The body must be an object"),
    ], ids=[
        "missing key", "too long", "unclosed choice", "unclosed condition", "not text",
        "unknown mode", "promptless mode", "empty modes", "modes not a list", "not an object",
    ])
    def test_an_invalid_body_is_refused_and_nothing_is_stored(self, presets, body, message):
        response = _client(presets).put("/api/presets/film/fixed-prompt", json=body)

        assert response.status_code == 400
        error = _error(response)
        assert error["error"] == "invalid_fixed_prompt"
        assert any(entry.startswith(message) for entry in error["errors"]), error["errors"]
        assert presets.get("film") == FixedPrompt()

    def test_choices_variables_and_the_full_length_are_accepted(self, presets):
        body = {**FILM, "prefix": "{a|b} ${mood} " + "x" * 1986}

        response = _client(presets).put("/api/presets/film/fixed-prompt", json=body)

        assert response.status_code == 200


class TestBannedWords:
    BANNED = "'{}' contains a word on this instance's banned prompt list"

    def _put(self, presets, body, words=("kodachrome",)):
        manager, _, _ = build("allowed", words=list(words))
        return _client(presets, content_safety=manager).put("/api/presets/film/fixed-prompt", json=body)

    @pytest.mark.parametrize("key, text", [
        ("prefix", "Kodachrome still,"),
        ("suffix", "shot on {warm|kodachrome} stock"),
        ("prefix", "kodach{rome} still,"),
    ], ids=["prefix", "suffix choice", "only after expansion"])
    def test_a_banned_word_in_the_prompt_text_is_refused_at_save_time(self, presets, key, text):
        response = self._put(presets, {**FILM, key: text})

        assert response.status_code == 400
        error = _error(response)
        assert error["error"] == "invalid_fixed_prompt"
        assert any(entry.startswith(self.BANNED.format(key)) for entry in error["errors"]), error["errors"]
        assert presets.get("film") == FixedPrompt()

    def test_a_banned_word_in_the_negative_is_kept_because_it_never_refuses_a_generation(self, presets):
        response = self._put(presets, {**FILM, "prefix": "", "negative": "kodachrome"})

        assert response.status_code == 200
        assert presets.get("film").negative == "kodachrome"

    def test_an_empty_banned_list_checks_nothing(self, presets):
        response = self._put(presets, FILM | {"prefix": "kodachrome still,"}, words=())

        assert response.status_code == 200

    def test_clean_text_is_saved(self, presets):
        response = self._put(presets, {**FILM, "prefix": "film still,", "suffix": "warm grain"})

        assert response.status_code == 200
