import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.elixirs.assistant import SessionWriter
from src.features.elixirs.errors import SessionNotFoundError as ElixirSessionNotFound
from src.features.elixirs.session_reader import SessionReader
from src.features.llm.tools.builtin.list_sessions_tool import ListSessionsTool
from src.features.phrasebook.preview_generator import PhrasebookPreviewGenerator
from src.features.presets.loader import PresetTemplateLoader
from src.features.sessions.routes import SessionController, build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType
from src.plugin_api import generation as plugin_generation
from tests.features.sessions.support import PRESET, Models, Users, build_service, entry, save, template
from tests.features.sessions.test_session_versions import conn, repos

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = REPO_ROOT / "content" / "presets" / "marketplace"

USER = "user-1"
OTHER = "user-2"

PAYLOAD_EXTRAS = {
    "prompt": "a lighthouse",
    "negativePrompt": "blur",
    "promptSegments": [{"id": "a", "type": "content", "content": "a lighthouse"}],
    "negativePromptSegments": [{"id": "n", "type": "content", "content": "blur"}],
    "variables": {"mood": "calm"},
    "videoDirector": {"shots": []},
    "musicDirector": {"on": True},
    "promptRelay": {"on": True},
    "layoutMode": "wide",
    "leftPanelCollapsed": True,
    "leftPanelWidth": 380,
    "sectionCollapsed": {"basic": True},
    "compare": {"a": 1},
    "selectedVariant": "default",
    "presetVersion": "1.4.0",
}


@pytest.fixture(scope="module")
def shipped():
    loader = PresetTemplateLoader([str(MARKETPLACE)])
    loader.load_presets()
    manager = EditorTaskManager(
        register_core_tasks(TaskRegistry()),
        lambda: loader.all(),
        lambda user: set(),
        lambda engine: True,
    )
    return SimpleNamespace(manager=manager, ids={Path(item.path).name: item.id for item in loader.all()})


@pytest.fixture
def world(repos, conn, tmp_path, shipped):
    sessions, versions = repos
    registry = Mock()
    context = Mock()
    context.data = {}
    registry.execute_hook.return_value = (context, [])
    controller = SessionController(
        session_repository=sessions,
        plugin_registry=registry,
        session_version_repository=versions,
        editor_owned_fields=shipped.manager.owned_fields,
        editor_mode_check=lambda preset, mode: (
            shipped.manager.mode_status(preset, mode) if preset in shipped.ids.values() else "ok"
        ),
    )
    requests = build_service(
        sessions, str(tmp_path), template(),
        available={USER: [PRESET], OTHER: [PRESET]},
        users=Users(**{USER: AccountType.USER, OTHER: AccountType.USER}),
        models=Models({"m1": "One"}, {USER: ["m1"], OTHER: ["m1"]}),
    )
    container = SimpleNamespace(session_controller=controller, session_requests=requests, settings=None)
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=USER, account_type=AccountType.USER)
    return SimpleNamespace(
        conn=conn, sessions=sessions, versions=versions, registry=registry, requests=requests, shipped=shipped,
        client=TestClient(app, raise_server_exceptions=False),
    )


def form(**values):
    return {"formData": values}


def post(world, name, data, kind=None, mode=None, preset=PRESET):
    body = {"preset_id": preset, "name": name, "data": data}
    if kind:
        body["kind"] = kind
    if mode:
        body["mode"] = mode
    return world.client.post("/api/sessions/save", json=body)


def ids(response):
    return [item["id"] for item in response.json()["data"]]


def two_kinds(world):
    generation = save(world.sessions, USER, name="Gen", session_id="gen")
    editor = save(world.sessions, USER, name="Ed", session_id="ed", kind="editor", data={"txt2img": form(seed=1)})
    return generation, editor


class TestKindOnWrite:
    def test_a_save_without_kind_creates_a_generation_session(self, world):
        response = post(world, "Plain", {"txt2img": entry()})

        assert response.status_code == 200
        assert response.json()["data"]["kind"] == "generation"
        assert world.sessions.get_by_id(response.json()["data"]["id"]).kind == "generation"

    def test_a_save_with_kind_editor_creates_an_editor_session(self, world):
        response = post(world, "Soft", {"txt2img": form(seed=3)}, kind="editor")

        assert response.json()["data"]["kind"] == "editor"
        assert world.sessions.get_by_id(response.json()["data"]["id"]).kind == "editor"

    def test_an_unknown_kind_is_refused(self, world):
        assert post(world, "Odd", {"txt2img": form()}, kind="video").status_code == 422

    def test_saving_again_keeps_the_kind(self, world):
        post(world, "Soft", {"txt2img": form(seed=3)}, kind="editor")

        again = post(world, "Soft", {"txt2img": form(seed=4)}, kind="editor")

        assert again.status_code == 200
        assert again.json()["data"]["kind"] == "editor"
        assert again.json()["data"]["data"] == {"editor:txt2img": form(seed=4)}

    def test_an_update_by_id_keeps_the_kind_whether_or_not_it_is_sent(self, world):
        created = post(world, "Soft", {"txt2img": form(seed=3)}, kind="editor").json()["data"]

        plain = world.client.put(f"/api/sessions/{created['id']}", json={"name": "Soft", "data": {"txt2img": form(seed=5)}})
        named = world.client.put(
            f"/api/sessions/{created['id']}", json={"name": "Soft", "data": {"txt2img": form(seed=6)}, "kind": "editor"}
        )

        assert (plain.status_code, named.status_code) == (200, 200)
        assert named.json()["data"]["kind"] == "editor"
        assert world.sessions.get_by_id(created["id"]).kind == "editor"

    def test_the_kind_never_changes_after_creation(self, world):
        generation, editor = two_kinds(world)

        to_editor = world.client.put(f"/api/sessions/{generation.id}", json={"name": "Gen", "data": {"txt2img": entry()}, "kind": "editor"})
        to_generation = world.client.put(f"/api/sessions/{editor.id}", json={"name": "Ed", "data": {"txt2img": form()}, "kind": "generation"})

        for response in (to_editor, to_generation):
            assert response.status_code == 409
            assert response.json()["detail"]["error"] == "session_kind_mismatch"
        assert world.sessions.get_by_id(generation.id).kind == "generation"
        assert world.sessions.get_by_id(editor.id).kind == "editor"


class TestNameClashAcrossKinds:
    def test_a_generation_save_over_an_editor_name_is_refused(self, world):
        post(world, "Same", {"txt2img": form(seed=1)}, kind="editor")

        clash = post(world, "Same", {"txt2img": entry()})

        assert clash.status_code == 409
        assert clash.json()["detail"]["error"] == "session_kind_mismatch"
        assert [s.kind for s in world.sessions.list_for_user(USER, kind="any")] == ["editor"]

    def test_an_editor_save_over_a_generation_name_is_refused(self, world):
        post(world, "Same", {"txt2img": entry()})

        clash = post(world, "Same", {"txt2img": form(seed=1)}, kind="editor")

        assert clash.status_code == 409
        assert clash.json()["detail"]["error"] == "session_kind_mismatch"
        assert [s.kind for s in world.sessions.list_for_user(USER, kind="any")] == ["generation"]

    def test_the_same_name_in_another_preset_is_not_a_clash(self, world):
        post(world, "Same", {"txt2img": entry()})

        assert post(world, "Same", {"txt2img": form()}, kind="editor", preset="other-preset").status_code == 200


class TestListingDefaultsToGeneration:
    def test_the_main_list_route_hides_editor_sessions_by_default(self, world):
        two_kinds(world)

        assert ids(world.client.get("/api/sessions")) == ["gen"]

    def test_the_main_list_route_opts_in_with_editor_and_any(self, world):
        two_kinds(world)

        assert ids(world.client.get("/api/sessions?kind=editor")) == ["ed"]
        assert sorted(ids(world.client.get("/api/sessions?kind=any"))) == ["ed", "gen"]
        assert ids(world.client.get("/api/sessions?kind=generation")) == ["gen"]

    def test_the_main_list_route_names_the_kind_of_each_item(self, world):
        two_kinds(world)

        listed = world.client.get("/api/sessions?kind=any").json()["data"]

        assert {item["id"]: item["kind"] for item in listed} == {"gen": "generation", "ed": "editor"}

    def test_the_main_list_route_filters_by_mode(self, world):
        save(world.sessions, USER, name="A", session_id="a", kind="editor", data={"inpaint": form(seed=1)})
        save(world.sessions, USER, name="B", session_id="b", kind="editor", data={"edit": form(seed=1)})

        assert ids(world.client.get("/api/sessions?kind=editor&mode=inpaint")) == ["a"]
        assert sorted(ids(world.client.get("/api/sessions?kind=editor"))) == ["a", "b"]

    def test_an_unknown_kind_query_is_refused(self, world):
        assert world.client.get("/api/sessions?kind=video").status_code == 422
        assert world.client.get(f"/api/sessions/preset/{PRESET}?kind=video").status_code == 422

    def test_the_preset_list_route_hides_editor_sessions_by_default(self, world):
        two_kinds(world)

        assert ids(world.client.get(f"/api/sessions/preset/{PRESET}")) == ["gen"]

    def test_the_preset_list_route_opts_in_with_editor_any_and_mode(self, world):
        two_kinds(world)

        assert ids(world.client.get(f"/api/sessions/preset/{PRESET}?kind=editor")) == ["ed"]
        assert sorted(ids(world.client.get(f"/api/sessions/preset/{PRESET}?kind=any"))) == ["ed", "gen"]
        assert ids(world.client.get(f"/api/sessions/preset/{PRESET}?kind=editor&mode=txt2img")) == ["ed"]
        assert ids(world.client.get(f"/api/sessions/preset/{PRESET}?kind=editor&mode=inpaint")) == []

    def test_the_repository_lists_generation_by_default(self, world):
        two_kinds(world)

        assert [s.id for s in world.sessions.list_for_user(USER)] == ["gen"]
        assert [s.id for s in world.sessions.get_by_user_and_preset(USER, PRESET)] == ["gen"]
        assert [s.id for s in world.sessions.list_for_user(USER, name="Ed")] == []

    def test_the_session_requests_listing_used_by_the_llm_and_mcp_tool_hides_editor_sessions(self, world):
        two_kinds(world)

        assert [item["id"] for item in world.requests.listing(USER)] == ["gen"]
        assert [item["id"] for item in world.requests.listing(USER, PRESET, None, "e")] == ["gen"]

    @pytest.mark.asyncio
    async def test_the_list_sessions_tool_hides_editor_sessions(self, world):
        two_kinds(world)
        context = SimpleNamespace(sessions=world.requests, user_id=USER, is_admin=False)

        result = await ListSessionsTool().execute(context)

        assert [item["id"] for item in json.loads(result.data)["sessions"]] == ["gen"]

    def test_another_users_editor_session_stays_invisible(self, world):
        save(world.sessions, OTHER, name="Theirs", session_id="theirs", kind="editor", data={"txt2img": form()})

        assert world.sessions.list_for_user(USER, kind="any") == []
        assert world.requests.listing(USER, kind="any") == []
        assert world.client.get("/api/sessions?kind=any").json()["data"] == []
        assert world.client.get("/api/sessions/theirs").json()["error"] == "session_access_denied"


class TestEditorSessionsAreNotRunnable:
    def test_generate_from_an_editor_session_answers_422(self, world):
        two_kinds(world)

        response = world.client.post("/api/sessions/ed/generate", json={})

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "session_is_editor"

    def test_a_dry_run_is_refused_the_same_way(self, world):
        two_kinds(world)

        assert world.client.post("/api/sessions/ed/generate", json={"dry_run": True}).status_code == 422

    def test_request_from_an_editor_session_answers_422(self, world):
        two_kinds(world)

        response = world.client.get("/api/sessions/ed/request")

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "session_is_editor"

    def test_a_generation_session_still_describes(self, world):
        two_kinds(world)

        assert world.client.get("/api/sessions/gen/request").status_code == 200

    def test_the_get_by_id_route_returns_an_editor_session_with_its_kind(self, world):
        two_kinds(world)

        got = world.client.get("/api/sessions/ed").json()["data"]

        assert (got["id"], got["kind"]) == ("ed", "editor")
        assert world.client.get("/api/sessions/gen").json()["data"]["kind"] == "generation"


class TestOtherReadersStayOnGeneration:
    def test_the_elixir_session_reader_does_not_see_editor_sessions(self, world):
        two_kinds(world)
        reader = SessionReader(world.sessions, world.versions)
        user = SimpleNamespace(id=USER)

        assert reader.get_session(user, "gen")["session_id"] == "gen"
        with pytest.raises(ElixirSessionNotFound):
            reader.get_session(user, "ed")
        with pytest.raises(ElixirSessionNotFound):
            reader.get_snapshot(user, "ed")

    def test_the_elixir_session_writer_does_not_find_editor_sessions_by_name(self, world):
        two_kinds(world)
        writer = SessionWriter(world.sessions, world.registry, None, None)

        assert writer.find(USER, PRESET, "Gen")["session_id"] == "gen"
        assert writer.find(USER, PRESET, "Ed") is None

    def test_the_plugin_api_session_read_does_not_return_editor_sessions(self, world):
        two_kinds(world)
        container = SimpleNamespace(session_repository=world.sessions)
        user = SimpleNamespace(id=USER)

        with patch.object(plugin_generation, "get_container", return_value=container):
            assert plugin_generation.get_session(user, "gen")["session_id"] == "gen"
            with pytest.raises(plugin_generation.SessionNotFoundError):
                plugin_generation.get_session(user, "ed")

    @pytest.mark.asyncio
    async def test_the_phrasebook_preview_does_not_start_from_an_editor_session(self, world):
        two_kinds(world)
        categories = Mock()
        categories.get_by_id.return_value = SimpleNamespace(id="c1")
        generator = PhrasebookPreviewGenerator(categories, Mock(), Mock())

        with patch("src.features.phrasebook.preview_generator.session_repo", world.sessions):
            with pytest.raises(ValueError, match="Session not found"):
                await generator.generate_previews("c1", "ed", "a << value >>", "txt2img", USER, AsyncMock())


class TestEditorStripping:
    def mode_payload(self, **form_values):
        return {**PAYLOAD_EXTRAS, "formData": form_values}

    def test_a_mask_mode_loses_the_prompt_layout_pictures_and_owned_fields(self, world):
        preset = world.shipped.ids["QwenImage-2.1"]
        owned = world.shipped.manager.owned_fields(preset, "editor:inpaint")
        assert {"source_image", "source_image_inpaint_mask", "quantity"} <= owned
        payload = self.mode_payload(
            source_image="up_1", source_image_inpaint_mask="mask_1", quantity=4, seed=7, steps=12
        )

        response = post(world, "Soft", {"editor:inpaint": payload}, kind="editor", preset=preset)

        assert response.json()["data"]["data"] == {
            "editor:inpaint": {"formData": {"seed": 7, "steps": 12}, "selectedVariant": "default", "presetVersion": "1.4.0"}
        }
        assert world.sessions.get_by_id(response.json()["data"]["id"]).data == response.json()["data"]["data"]

    def test_a_crop_mode_loses_the_size_field_the_editor_sets(self, world):
        preset = world.shipped.ids["Flux3"]
        payload = self.mode_payload(source_image="up_1", output_size="match", quantity=2, seed=9, steps=20)

        response = post(world, "Soft", {"editor:edit": payload}, kind="editor", preset=preset)

        assert response.json()["data"]["data"] == {
            "editor:edit": {"formData": {"seed": 9, "steps": 20}, "selectedVariant": "default", "presetVersion": "1.4.0"}
        }

    def test_the_mode_argument_form_is_stripped_too(self, world):
        preset = world.shipped.ids["Flux3"]

        response = post(world, "Soft", self.mode_payload(source_image="a", seed=1), kind="editor", mode="edit", preset=preset)

        assert response.json()["data"]["data"]["editor:edit"]["formData"] == {"seed": 1}
        assert "prompt" not in response.json()["data"]["data"]["editor:edit"]

    def test_an_update_by_id_and_a_save_by_name_strip_as_well(self, world):
        preset = world.shipped.ids["Flux3"]
        created = post(world, "Soft", {"editor:edit": self.mode_payload(seed=1)}, kind="editor", preset=preset).json()["data"]

        updated = world.client.put(
            f"/api/sessions/{created['id']}",
            json={"name": "Soft", "data": {"editor:edit": self.mode_payload(source_image="a", seed=2)}},
        )
        resaved = post(world, "Soft", {"editor:edit": self.mode_payload(output_size="match", seed=3)}, preset=preset, kind="editor")

        assert updated.json()["data"]["data"]["editor:edit"]["formData"] == {"seed": 2}
        assert resaved.json()["data"]["data"]["editor:edit"]["formData"] == {"seed": 3}
        assert "prompt" not in resaved.json()["data"]["data"]["editor:edit"]

    def test_saving_one_mode_keeps_the_others_stripped(self, world):
        preset = world.shipped.ids["QwenImage-2.1"]
        post(world, "Soft", {"editor:edit": self.mode_payload(seed=1)}, kind="editor", preset=preset)

        again = post(world, "Soft", {"editor:inpaint": self.mode_payload(seed=2, prompt="x")}, kind="editor", preset=preset)

        assert sorted(again.json()["data"]["data"]) == ["editor:edit", "editor:inpaint"]
        assert again.json()["data"]["data"]["editor:edit"]["formData"] == {"seed": 1}

    def test_the_stored_version_snapshots_are_stripped(self, world):
        preset = world.shipped.ids["Flux3"]
        created = post(
            world, "Soft", {"editor:edit": self.mode_payload(source_image="a", output_size="match", seed=1)}, kind="editor", preset=preset
        ).json()["data"]
        world.client.put(
            f"/api/sessions/{created['id']}", json={"name": "Soft", "data": {"editor:edit": self.mode_payload(quantity=3, seed=2)}}
        )

        first = world.client.get(f"/api/sessions/{created['id']}/versions/1").json()["data"]["data"]
        second = world.client.get(f"/api/sessions/{created['id']}/versions/2").json()["data"]["data"]

        assert first == {"editor:edit": {"formData": {"seed": 1}, "selectedVariant": "default", "presetVersion": "1.4.0"}}
        assert second["editor:edit"]["formData"] == {"seed": 2}
        assert "prompt" not in first["editor:edit"] and "prompt" not in second["editor:edit"]

    def test_a_generation_session_is_not_stripped(self, world):
        preset = world.shipped.ids["Flux3"]
        payload = self.mode_payload(source_image="up_1", output_size="match", quantity=2, seed=9)

        response = post(world, "Gen", {"edit": payload}, preset=preset)
        updated = world.client.put(
            f"/api/sessions/{response.json()['data']['id']}", json={"name": "Gen", "data": {"edit": payload}}
        )

        assert response.json()["data"]["data"] == {"edit": payload}
        assert updated.json()["data"]["data"] == {"edit": payload}
        version = world.client.get(f"/api/sessions/{response.json()['data']['id']}/versions/1").json()["data"]["data"]
        assert version == {"edit": payload}

    def test_a_mode_without_a_declaration_still_loses_the_non_form_keys(self, world):
        response = post(world, "Soft", {"txt2img": self.mode_payload(seed=1, quantity=2)}, kind="editor")

        assert response.json()["data"]["data"] == {
            "editor:txt2img": {"formData": {"seed": 1, "quantity": 2}, "selectedVariant": "default", "presetVersion": "1.4.0"}
        }


class TestEditorModeRefusals:
    def refused(self, response, status, code):
        assert response.status_code == status
        assert response.json()["detail"]["error"] == code

    def count(self, world):
        versions = world.conn.execute("SELECT COUNT(*) FROM session_versions").fetchone()[0]
        return len(world.sessions.list_for_user(USER, kind="any")), versions

    def test_a_flat_payload_without_a_mode_is_refused_and_nothing_is_written(self, world):
        preset = world.shipped.ids["Flux3"]

        self.refused(post(world, "Soft", {"prompt": "x", "seed": 1}, kind="editor", preset=preset), 422, "session_mode_required")
        self.refused(post(world, "Soft", {}, kind="editor", preset=preset), 422, "session_mode_required")
        self.refused(post(world, "Soft", {"formData": {"seed": 1}}, kind="editor", preset=preset), 422, "session_mode_unknown")
        assert self.count(world) == (0, 0)

    def test_a_mode_the_preset_does_not_have_is_refused(self, world):
        preset = world.shipped.ids["Flux3"]

        self.refused(post(world, "Soft", {"nope": form(seed=1)}, kind="editor", preset=preset), 422, "session_mode_unknown")
        self.refused(post(world, "Soft", form(seed=1), kind="editor", mode="nope", preset=preset), 422, "session_mode_unknown")
        assert self.count(world) == (0, 0)

    def test_a_mode_that_offers_no_editor_task_is_refused(self, world):
        preset = world.shipped.ids["Flux3"]

        self.refused(post(world, "Soft", {"txt2img": form(seed=1)}, kind="editor", preset=preset), 422, "session_mode_unknown")
        self.refused(post(world, "Soft", {"editor:txt2img": form(seed=1)}, kind="editor", preset=preset), 422, "session_mode_unknown")
        assert self.count(world) == (0, 0)

    def test_an_update_by_id_and_a_save_by_name_are_refused_the_same_way(self, world):
        preset = world.shipped.ids["Flux3"]
        created = post(world, "Soft", {"editor:edit": form(seed=1)}, kind="editor", preset=preset).json()["data"]
        before = self.count(world)

        flat = world.client.put(f"/api/sessions/{created['id']}", json={"name": "Soft", "data": {"prompt": "x"}})
        unknown = world.client.put(f"/api/sessions/{created['id']}", json={"name": "Soft", "data": {"nope": form()}})
        unoffered = post(world, "Soft", {"txt2img": form(seed=2)}, kind="editor", preset=preset)
        flat_named = post(world, "Soft", {"prompt": "x"}, kind="editor", preset=preset)

        self.refused(flat, 422, "session_mode_required")
        self.refused(unknown, 422, "session_mode_unknown")
        self.refused(unoffered, 422, "session_mode_unknown")
        self.refused(flat_named, 422, "session_mode_required")
        assert self.count(world) == before
        assert world.sessions.get_by_id(created["id"]).data["editor:edit"]["formData"] == {"seed": 1}

    def test_a_generation_session_still_saves_the_same_flat_payload(self, world):
        preset = world.shipped.ids["Flux3"]
        flat = {"prompt": "x", "formData": {"seed": 1}}

        saved = post(world, "Gen", flat, preset=preset)

        assert saved.status_code == 200
        assert saved.json()["data"]["data"] == flat

    def test_a_generation_sessions_full_data_saved_as_an_editor_session_comes_back_stripped(self, world):
        preset = world.shipped.ids["Flux3"]
        full = {"edit": {**PAYLOAD_EXTRAS, "formData": {"source_image": "a", "output_size": "match", "seed": 9}}}
        generation = post(world, "Gen", full, preset=preset)

        copy = post(world, "Gen as setup", generation.json()["data"]["data"], kind="editor", preset=preset)

        assert copy.json()["data"]["data"] == {
            "editor:edit": {"formData": {"seed": 9}, "selectedVariant": "default", "presetVersion": "1.4.0"}
        }


class TestUnprefixedKeysFromAnOlderClient:
    def mode_payload(self, **form_values):
        return {**PAYLOAD_EXTRAS, "formData": form_values}

    def test_a_key_without_the_prefix_is_stored_with_it_and_stripped(self, world):
        preset = world.shipped.ids["Flux3"]

        response = post(world, "Soft", {"edit": self.mode_payload(source_image="a", output_size="match", seed=3)}, kind="editor", preset=preset)

        assert list(response.json()["data"]["data"]) == ["editor:edit"]
        assert response.json()["data"]["data"]["editor:edit"]["formData"] == {"seed": 3}

    def test_the_mode_argument_without_the_prefix_lands_on_the_prefixed_key_without_a_twin(self, world):
        preset = world.shipped.ids["Flux3"]
        post(world, "Soft", {"editor:edit": self.mode_payload(seed=1)}, kind="editor", preset=preset)

        again = post(world, "Soft", self.mode_payload(seed=2, source_image="a"), kind="editor", mode="edit", preset=preset)

        assert list(again.json()["data"]["data"]) == ["editor:edit"]
        assert again.json()["data"]["data"]["editor:edit"]["formData"] == {"seed": 2}

    def test_an_update_by_id_with_the_old_key_does_not_leave_a_second_entry(self, world):
        preset = world.shipped.ids["Flux3"]
        created = post(world, "Soft", {"editor:edit": self.mode_payload(seed=1)}, kind="editor", preset=preset).json()["data"]

        updated = world.client.put(
            f"/api/sessions/{created['id']}", json={"name": "Soft", "data": {"edit": self.mode_payload(seed=5)}}
        )

        assert list(updated.json()["data"]["data"]) == ["editor:edit"]
        assert updated.json()["data"]["data"]["editor:edit"]["formData"] == {"seed": 5}

    def test_listing_by_the_old_name_finds_a_prefixed_session_and_an_old_unmigrated_one(self, world):
        save(world.sessions, USER, name="New", session_id="new", kind="editor", data={"editor:edit": form(seed=1)})
        save(world.sessions, USER, name="Old", session_id="old", kind="editor", data={"edit": form(seed=1)})

        by_old = ids(world.client.get(f"/api/sessions/preset/{PRESET}?kind=editor&mode=edit"))
        by_new = ids(world.client.get(f"/api/sessions/preset/{PRESET}?kind=editor&mode=editor:edit"))

        assert sorted(by_old) == ["new", "old"]
        assert by_new == ["new"]

    def test_saving_another_mode_prefixes_the_old_keys_already_stored(self, world):
        save(world.sessions, USER, name="Old", session_id="old", kind="editor", data={"edit": form(seed=1)})

        updated = world.client.put("/api/sessions/old", json={"name": "Old", "data": {"editor:other": form(seed=2)}})

        assert sorted(updated.json()["data"]["data"]) == ["editor:edit", "editor:other"]
        assert updated.json()["data"]["data"]["editor:edit"] == form(seed=1)

    def test_a_generation_session_keys_are_never_prefixed(self, world):
        response = post(world, "Gen", {"txt2img": form(seed=1)})

        assert list(response.json()["data"]["data"]) == ["txt2img"]


def test_a_mode_check_that_answers_not_offered_is_a_422_with_its_own_code():
    from src.features.sessions.editor_data import check_editor_modes
    from src.platform.http.errors import DomainError

    with pytest.raises(DomainError) as refused:
        check_editor_modes({"editor:x": {}}, None, "p", lambda preset, mode: "not_offered")

    assert refused.value.code == "session_mode_not_offered" and refused.value.status_code == 422
