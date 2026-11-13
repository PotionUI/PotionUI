import copy
from types import SimpleNamespace

import pytest

from src.features.fields.builtin import register_builtin_fields
from src.features.sessions.generate import (
    FIELD_KINDS,
    KIND_CONTAINER,
    KIND_DISPLAY,
    KIND_MEDIA,
    KIND_MODEL,
    Overrides,
    SessionRequestError,
    field_kind,
)
from src.features.sessions.request import settings_hash
from src.platform.plugins.field_types import FieldTypeRegistry
from tests.features.sessions.support import (
    OTHER_PRESET,
    PRESET,
    Models,
    build_service,
    entry,
    field,
    memory_sessions,
    save,
    segment,
    template,
)


@pytest.fixture
def world(tmp_path):
    with memory_sessions() as (sessions, connection):
        yield SimpleNamespace(sessions=sessions, connection=connection, storage=str(tmp_path))


def refusal(call, *args, **kwargs):
    with pytest.raises(SessionRequestError) as caught:
        call(*args, **kwargs)
    return caught.value


def prepare(service, session_id="s1", mode=None, user="u1", admin=False, **overrides):
    return service.prepare(user, admin, session_id, mode, Overrides(**overrides))


def test_every_registered_field_type_is_classified_so_a_new_one_cannot_slip_by():
    registry = FieldTypeRegistry()
    register_builtin_fields(registry)
    registered = {definition.type_name for definition in registry.all()}
    assert registered - set(FIELD_KINDS) == set()
    assert set(FIELD_KINDS) - registered == set()
    containers = {definition.type_name for definition in registry.all() if definition.container}
    assert containers <= {name for name, kind in FIELD_KINDS.items() if kind == KIND_CONTAINER}


def test_containers_and_display_fields_are_not_offered_to_scripts():
    assert field_kind("tabs") == KIND_CONTAINER
    assert field_kind("alert") == KIND_DISPLAY
    assert field_kind("image") == KIND_MEDIA
    assert field_kind("lora_picker") == KIND_MODEL


class TestListing:
    def test_lists_only_the_callers_sessions_across_presets_with_filters(self, world):
        save(world.sessions, "u1", PRESET, "Portrait", session_id="s1")
        save(world.sessions, "u1", OTHER_PRESET, "Portrait", session_id="s2", data={"txt2img": entry(), "img2img": entry()})
        save(world.sessions, "u1", PRESET, "Landscape", session_id="s3")
        save(world.sessions, "u2", PRESET, "Portrait", session_id="s4")
        service = build_service(world.sessions, world.storage, template(), template(OTHER_PRESET, "Wan Test"))

        every = service.listing("u1")
        by_preset = service.listing("u1", preset_id=OTHER_PRESET)
        by_name = service.listing("u1", name="Portrait")
        by_query = service.listing("u1", query="land")

        assert {item["id"] for item in every} == {"s1", "s2", "s3"}
        assert [item["id"] for item in by_preset] == ["s2"]
        assert by_preset[0]["modes"] == ["txt2img", "img2img"] and by_preset[0]["preset_name"] == "Wan Test"
        assert {item["id"] for item in by_name} == {"s1", "s2"}
        assert [item["id"] for item in by_query] == ["s3"]
        assert by_query[0]["prompts"] == {"txt2img": "a lighthouse"}

    def test_a_percent_in_the_query_is_literal(self, world):
        save(world.sessions, name="100% fox", session_id="s1")
        save(world.sessions, name="plain", session_id="s2")
        service = build_service(world.sessions, world.storage)

        assert [item["id"] for item in service.listing("u1", query="%")] == ["s1"]


class TestOwnership:
    def test_someone_elses_session_is_not_found_everywhere(self, world):
        save(world.sessions, "u2", session_id="theirs")
        service = build_service(world.sessions, world.storage)

        for call in (
            lambda: service.describe("u1", False, "theirs", None),
            lambda: prepare(service, "theirs"),
            lambda: service.describe("root", True, "theirs", None),
            lambda: service.describe("u1", False, "missing", None),
        ):
            error = refusal(call)
            assert (error.status, error.code) == (404, "session_not_found")


class TestMode:
    def test_one_mode_is_used_without_naming_it(self, world):
        save(world.sessions)
        assert prepare(build_service(world.sessions, world.storage)).session["mode"] == "txt2img"

    def test_several_modes_need_a_choice_and_the_error_lists_them(self, world):
        save(world.sessions, data={"txt2img": entry(), "img2img": entry()})
        service = build_service(world.sessions, world.storage, template(modes=("txt2img", "img2img")))

        error = refusal(prepare, service)

        assert (error.status, error.code, error.detail["modes"]) == (400, "mode_required", ["txt2img", "img2img"])
        assert prepare(service, mode="img2img").session["mode"] == "img2img"
        assert refusal(prepare, service, mode="inpaint").code == "session_mode_not_found"

    def test_a_mode_the_preset_dropped_is_named(self, world):
        save(world.sessions, data={"gone": entry()})
        error = refusal(prepare, build_service(world.sessions, world.storage))
        assert (error.status, error.code) == (422, "mode_not_available")

    def test_a_retired_mode_names_its_replacement(self, world):
        save(world.sessions, data={"long": entry()})
        service = build_service(world.sessions, world.storage, template(modes=("video",), vars={"retired_modes": {"long": "video"}}))

        error = refusal(prepare, service)

        assert (error.status, error.code, error.detail) == (422, "mode_not_available", {"replaced_by": "video"})
        assert "Mode 'long' was replaced by 'video'." in error.message

    def test_a_mode_that_was_never_retired_names_no_replacement(self, world):
        save(world.sessions, data={"gone": entry()})
        service = build_service(world.sessions, world.storage, template(vars={"retired_modes": {"long": "video"}}))

        error = refusal(prepare, service)

        assert (error.code, error.detail) == ("mode_not_available", {})
        assert "replaced by" not in error.message

    def test_asking_for_a_retired_mode_the_session_never_saved_names_its_replacement(self, world):
        save(world.sessions, data={"video": entry()})
        service = build_service(world.sessions, world.storage, template(modes=("video",), vars={"retired_modes": {"long": "video"}}))

        error = refusal(prepare, service, mode="long")

        assert (error.status, error.code, error.detail) == (404, "session_mode_not_found", {"modes": ["video"], "replaced_by": "video"})
        assert error.message.endswith("Mode 'long' was replaced by 'video'.")

    def test_the_template_lists_a_retired_mode_as_a_problem_naming_its_replacement(self, world):
        save(world.sessions, data={"long": entry()})
        service = build_service(world.sessions, world.storage, template(modes=("video",), vars={"retired_modes": {"long": "video"}}))

        problems = service.describe("u1", False, "s1", None)["problems"]

        assert [p["code"] for p in problems] == ["mode_not_available"]
        assert "Mode 'long' was replaced by 'video'." in problems[0]["message"]


class TestOverrides:
    def test_prompt_replaces_the_sessions_segments_and_keeps_the_rest(self, world):
        save(world.sessions)
        prepared = prepare(build_service(world.sessions, world.storage), prompt="a glass bottle")

        request = prepared.request
        assert request.prompts[0].positive == "a glass bottle"
        assert request.prompts[0].negative == "blur"
        assert request.form_data == {"seed": 5, "quantity": 2, "steps": 30}
        assert request.mode == "txt2img" and request.preset_id == PRESET

    def test_prompt_drops_prompt_tabs_so_a_batch_session_runs_the_new_prompt(self, world):
        tabs = [
            {"id": "p1", "prompt": "one", "promptSegments": [segment("x", "one")], "negativePromptSegments": []},
            {"id": "p2", "prompt": "two", "promptSegments": [segment("y", "two")], "negativePromptSegments": []},
        ]
        save(world.sessions, data={"txt2img": entry(promptTabs=tabs, activePromptTab=1)})

        prepared = prepare(build_service(world.sessions, world.storage), prompt="only this")

        assert [pair.positive for pair in prepared.request.prompts] == ["only this"]
        assert prepared.request.batch_prompts is False

    def test_negative_prompt_replaces_the_negative_side_only(self, world):
        save(world.sessions)
        prepared = prepare(build_service(world.sessions, world.storage), negative_prompt="noise")
        assert (prepared.request.prompts[0].positive, prepared.request.prompts[0].negative) == ("a lighthouse", "noise")

    def test_prompts_make_a_batch_and_both_prompt_forms_cannot_mix(self, world):
        save(world.sessions)
        service = build_service(world.sessions, world.storage)

        batch = prepare(service, prompts=[{"positive": "red fox"}, {"positive": "blue fox", "negative": "dull"}])

        assert batch.request.batch_prompts is True
        assert [(p.positive, p.negative) for p in batch.request.prompts] == [("red fox", "blur"), ("blue fox", "dull")] or [
            (p.positive, p.negative) for p in batch.request.prompts
        ] == [("red fox", ""), ("blue fox", "dull")]
        assert refusal(prepare, service, prompt="x", prompts=[{"positive": "y"}]).code == "conflicting_overrides"

    def test_a_batch_on_a_preset_that_runs_one_prompt_at_a_time_is_refused(self, world):
        save(world.sessions)
        service = build_service(world.sessions, world.storage, batch=False)
        error = refusal(prepare, service, prompts=[{"positive": "a"}, {"positive": "b"}])
        assert (error.status, error.code) == (422, "batch_not_supported")

    def test_too_many_prompts_are_refused(self, world):
        save(world.sessions)
        error = refusal(prepare, build_service(world.sessions, world.storage), prompts=[{"positive": str(n)} for n in range(21)])
        assert error.code == "too_many_prompts"

    def test_variables_set_a_known_variable_and_name_an_unknown_one(self, world):
        variables = {"mood": {"type": "choice", "options": ["noir", "sunlit"], "mode": "shuffle", "pinnedIndex": None}}
        save(world.sessions, data={"txt2img": entry(promptSegments=[segment("a", "city in ${mood} light")], variables=variables)})
        service = build_service(world.sessions, world.storage)

        prepared = prepare(service, variables={"mood": "dusk"})
        error = refusal(prepare, service, variables={"tone": "x"})

        assert prepared.request.variables == {"mood": "dusk"}
        assert (error.status, error.code, error.detail["names"]) == (422, "unknown_variable", ["tone"])

    def test_seed_and_count_set_the_forms_seed_and_quantity_fields(self, world):
        save(world.sessions)
        prepared = prepare(build_service(world.sessions, world.storage), seed=-1, count=4)
        assert prepared.request.form_data["seed"] == -1 and prepared.request.form_data["quantity"] == 4

    def test_seed_and_count_say_when_the_form_has_no_such_field(self, world):
        save(world.sessions, data={"txt2img": entry(formData={"steps": 10})})
        service = build_service(world.sessions, world.storage, template(fields=[field("steps", "slider", default=20)]))

        assert refusal(prepare, service, seed=3).code == "seed_not_supported"
        assert refusal(prepare, service, count=3).code == "count_not_supported"

    def test_form_changes_a_named_field_and_leaves_the_rest(self, world):
        save(world.sessions)
        prepared = prepare(build_service(world.sessions, world.storage), form={"resolution": "1216x832", "steps": 12})
        assert prepared.request.form_data == {"seed": 5, "quantity": 2, "steps": 12, "resolution": "1216x832"}

    def test_a_form_field_the_preset_does_not_have_is_named_not_dropped(self, world):
        save(world.sessions)
        error = refusal(prepare, build_service(world.sessions, world.storage), form={"cfg": 7})
        assert (error.status, error.code, error.detail["names"]) == (422, "unknown_field", ["cfg"])
        assert "steps" in error.detail["known"] and "note" not in error.detail["known"]

    def test_a_locked_or_hidden_field_is_refused_not_silently_ignored(self, world):
        save(world.sessions)
        overrides = {PRESET: {"txt2img": {"steps": {"editable": False, "default": 20}, "resolution": {"visible": False}}}}
        service = build_service(world.sessions, world.storage, overrides=overrides)

        error = refusal(prepare, service, form={"steps": 8, "resolution": "1216x832"})

        assert (error.status, error.code, error.detail["names"]) == (422, "field_locked", ["resolution", "steps"])

    def test_a_value_the_form_rejects_comes_back_as_field_errors(self, world):
        save(world.sessions)
        error = refusal(prepare, build_service(world.sessions, world.storage), form={"steps": 500})
        assert (error.status, error.code) == (422, "form_validation_failed")
        assert "steps" in error.detail["field_errors"]

    def test_tag_and_collection_ids_ride_along(self, world):
        save(world.sessions)
        prepared = prepare(build_service(world.sessions, world.storage), tag_ids=["t1"], collection_ids=["c1"], idempotency_key="nightly")
        assert (prepared.request.tag_ids, prepared.request.collection_ids, prepared.request.idempotency_key) == (["t1"], ["c1"], "nightly")

    def test_the_prompt_the_session_has_is_used_when_nothing_overrides_it(self, world):
        save(world.sessions)
        prepared = prepare(build_service(world.sessions, world.storage))
        assert (prepared.request.prompts[0].positive, prepared.request.prompts[0].negative) == ("a lighthouse", "blur")

    def test_a_session_with_no_prompt_asks_for_one(self, world):
        save(world.sessions, data={"txt2img": entry(prompt="", promptSegments=[], negativePrompt="", negativePromptSegments=[])})
        service = build_service(world.sessions, world.storage)

        assert refusal(prepare, service).code == "no_prompt"
        assert prepare(service, prompt="now it has one").request.prompts[0].positive == "now it has one"

    def test_saved_fields_the_form_lost_are_reported_in_notes(self, world):
        save(world.sessions, data={"txt2img": entry(formData={"steps": 10, "refmod_strength": 0.4}, compare={"enabled": True})})
        prepared = prepare(build_service(world.sessions, world.storage))
        assert any("refmod_strength" in note for note in prepared.notes)
        assert any("compare" in note for note in prepared.notes)


class TestAccess:
    def test_a_preset_the_owner_lost_is_named(self, world):
        save(world.sessions)
        service = build_service(world.sessions, world.storage, available={"u1": []})

        error = refusal(prepare, service)

        assert (error.status, error.code) == (403, "session_preset_unavailable")
        assert error.detail["preset_id"] == PRESET and "Flux Test" in error.message

    def test_a_preset_removed_from_disk_is_not_found(self, world):
        save(world.sessions)
        service = build_service(world.sessions, world.storage, template("another"), available={"u1": [PRESET]})
        assert refusal(prepare, service).code == "preset_not_found"

    def test_an_admin_owner_keeps_every_preset(self, world):
        save(world.sessions, "root", session_id="adm")
        service = build_service(world.sessions, world.storage, available={})
        assert prepare(service, "adm", user="root", admin=True).session["id"] == "adm"

    def test_a_model_that_is_gone_or_out_of_scope_is_named(self, world):
        gone = entry(formData={"checkpoint": "model:vanished"})
        denied = entry(formData={"checkpoint": "model:m2"})
        save(world.sessions, session_id="gone", name="Gone", data={"txt2img": gone})
        save(world.sessions, session_id="denied", name="Denied", data={"txt2img": denied})
        service = build_service(world.sessions, world.storage)

        for session_id, model in (("gone", "vanished"), ("denied", "m2")):
            error = refusal(prepare, service, session_id)
            assert (error.status, error.code) == (403, "session_model_unavailable")
            assert error.detail["models"][0]["field"] == "checkpoint" and error.detail["models"][0]["model_id"] == model

    def test_overriding_the_model_with_an_allowed_one_clears_it(self, world):
        save(world.sessions, data={"txt2img": entry(formData={"checkpoint": "model:vanished"})})
        service = build_service(world.sessions, world.storage)
        assert prepare(service, form={"checkpoint": "model:m1"}).request.form_data["checkpoint"] == "model:m1"

    def test_an_admin_pinned_model_is_not_held_against_the_user(self, world):
        save(world.sessions, data={"txt2img": entry(formData={"checkpoint": "model:m2"})})
        overrides = {PRESET: {"txt2img": {"checkpoint": {"editable": False, "default": "model:m2"}}}}
        assert prepare(build_service(world.sessions, world.storage, overrides=overrides)).session["id"] == "s1"


class TestInputs:
    def test_a_missing_input_file_is_named_and_an_override_fixes_it(self, world, tmp_path):
        save(world.sessions, data={"txt2img": entry(formData={"image": "uploads/gone.png"})})
        service = build_service(world.sessions, world.storage)

        error = refusal(prepare, service)

        assert (error.status, error.code) == (422, "input_missing")
        assert error.detail["inputs"] == [{"field": "image", "path": "uploads/gone.png"}]
        (tmp_path / "uploads").mkdir()
        (tmp_path / "uploads" / "new.png").write_bytes(b"x")
        assert prepare(service, form={"image": "uploads/new.png"}).request.form_data["image"] == "uploads/new.png"

    def test_an_existing_input_travels_with_the_session(self, world, tmp_path):
        (tmp_path / "uploads").mkdir()
        (tmp_path / "uploads" / "a.png").write_bytes(b"x")
        save(world.sessions, data={"txt2img": entry(formData={"image": "uploads/a.png"})})
        assert prepare(build_service(world.sessions, world.storage)).request.form_data["image"] == "uploads/a.png"


class TestDirectors:
    def test_a_director_session_is_refused_with_what_is_supported(self, world):
        vars_ = {"video_director": {"modes": {"t2v": {}}, "preset_modes": ["txt2img"]}}
        save(world.sessions, data={"txt2img": entry(videoDirector={"mode": "t2v"})})
        service = build_service(world.sessions, world.storage, template(vars=vars_))

        error = refusal(prepare, service)
        described = service.describe("u1", False, "s1", None)

        assert (error.status, error.code) == (422, "session_needs_editor")
        assert "Supported:" in error.message and "plain modes" in error.message
        assert described["editor_only"]["supported"] and described["fields"] == [] and described["resolved_request"] is None

    def test_a_music_director_session_is_refused_the_same_way(self, world):
        vars_ = {"music_director": {"modes": {"song": {}}}}
        save(world.sessions, data={"song": entry()})
        service = build_service(world.sessions, world.storage, template(vars=vars_, modes=("song",)))
        assert refusal(prepare, service).code == "session_needs_editor"


class TestSettingsHash:
    def test_a_matching_hash_passes_and_a_stale_one_is_a_conflict_naming_changed_fields(self, world):
        old = {"txt2img": entry()}
        new = {"txt2img": entry(prompt="changed", promptSegments=[segment("a", "changed")], formData={"seed": 5, "quantity": 2, "steps": 31})}
        save(world.sessions, data=new)
        history = [SimpleNamespace(version_number=1, data=old)]
        service = build_service(world.sessions, world.storage, history=history)
        stale = settings_hash({k: v for k, v in old["txt2img"].items() if k != "leftPanelWidth"})
        current = settings_hash({k: v for k, v in new["txt2img"].items() if k != "leftPanelWidth"})

        assert prepare(service, if_settings_hash=current).session["settings_hash"] == current
        error = refusal(prepare, service, if_settings_hash=stale)

        assert (error.status, error.code) == (409, "session_changed")
        assert error.detail["settings_hash"] == current and error.detail["changed_fields"] == ["prompt", "steps"]

    def test_layout_only_changes_do_not_change_the_hash(self, world):
        save(world.sessions, data={"txt2img": entry(leftPanelWidth=500, layoutMode="three")})
        service = build_service(world.sessions, world.storage)
        first = service.describe("u1", False, "s1", None)["session"]["settings_hash"]
        world.connection.execute("UPDATE sessions SET data = ?", ('{"txt2img": %s}' % __import__("json").dumps(entry(leftPanelWidth=320)),))
        assert service.describe("u1", False, "s1", None)["session"]["settings_hash"] == first


class TestTemplate:
    def test_describes_fields_values_options_and_shortcuts(self, world):
        save(world.sessions)
        described = build_service(world.sessions, world.storage).describe("u1", False, "s1", None)

        names = [item["name"] for item in described["fields"]]
        by_name = {item["name"]: item for item in described["fields"]}
        assert "note" not in names and names[:3] == ["seed", "quantity", "steps"]
        assert described["shortcuts"] == {"seed": "seed", "count": "quantity"}
        assert by_name["steps"]["value"] == 30 and by_name["steps"]["min"] == 1 and by_name["steps"]["max"] == 60
        assert by_name["resolution"]["options"] == ["1024x1024", "1216x832"] and by_name["resolution"]["value"] == "1024x1024"
        assert by_name["image"]["media"] is True and by_name["steps"]["media"] is False
        assert described["prompt"]["positive"] == "a lighthouse" and described["prompt"]["negative"] == "blur"
        assert described["session"]["modes"] == ["txt2img"] and described["problems"] == []
        assert described["resolved_request"]["prompts"][0]["positive"] == "a lighthouse"

    def test_locked_fields_are_marked_and_carry_no_value(self, world):
        save(world.sessions)
        overrides = {PRESET: {"txt2img": {"steps": {"editable": False, "default": 99}}}}
        described = build_service(world.sessions, world.storage, overrides=overrides).describe("u1", False, "s1", None)

        steps = next(item for item in described["fields"] if item["name"] == "steps")
        assert steps["locked"] is True and "value" not in steps and "default" not in steps
        assert any(p["code"] == "field_locked" and p["field"] == "steps" for p in described["problems"])
        assert "99" not in str(described["fields"])

    def test_problems_come_back_with_200_data_instead_of_failing(self, world):
        save(world.sessions, data={"txt2img": entry(formData={"checkpoint": "model:m2", "image": "uploads/gone.png"})})
        described = build_service(world.sessions, world.storage, available={"u1": []}).describe("u1", False, "s1", None)
        codes = {problem["code"] for problem in described["problems"]}
        assert {"session_preset_unavailable", "session_model_unavailable", "input_missing"} <= codes

    def test_a_session_that_needs_a_prompt_says_so_instead_of_a_request(self, world):
        save(world.sessions, data={"txt2img": entry(prompt="", promptSegments=[], negativePrompt="", negativePromptSegments=[])})
        described = build_service(world.sessions, world.storage).describe("u1", False, "s1", None)
        assert described["resolved_request"] is None and any(p["code"] == "no_prompt" for p in described["problems"])

    def test_prompt_variables_are_listed_with_their_options(self, world):
        variables = {"mood": {"type": "choice", "options": ["noir", "", "sunlit"], "mode": "shuffle", "pinnedIndex": None}, "n": {"type": "number", "mode": "pin", "min": 0, "max": 1, "step": 0.5, "value": 0.5}}
        save(world.sessions, data={"txt2img": entry(variables=variables)})
        listed = build_service(world.sessions, world.storage).describe("u1", False, "s1", None)["prompt"]["variables"]
        assert listed[0] == {"name": "mood", "type": "choice", "options": ["noir", "sunlit"], "mode": "shuffle"}
        assert listed[1]["name"] == "n" and listed[1]["value"] == 0.5


class TestTheSessionIsNeverWritten:
    def test_twenty_calls_leave_the_row_byte_identical(self, world):
        save(world.sessions)
        service = build_service(world.sessions, world.storage, available={"u1": [PRESET]})

        def row():
            return tuple(world.connection.execute("SELECT * FROM sessions ORDER BY id").fetchall()[0])

        before = row()
        for index in range(5):
            service.listing("u1")
            service.describe("u1", False, "s1", None)
            prepare(service, prompt=f"p{index}", count=3, form={"steps": 10})
            refusal(prepare, service, form={"nope": 1})
        assert row() == before
        assert world.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    def test_the_saved_entry_is_not_mutated_by_overrides(self, world):
        save(world.sessions)
        service = build_service(world.sessions, world.storage)
        snapshot = copy.deepcopy(world.sessions.get_by_id("s1").data)
        prepare(service, prompt="x", negative_prompt="y", form={"steps": 1})
        assert world.sessions.get_by_id("s1").data == snapshot


LOCK_A = 17
LOCK_B = 40


def locked_world(world, saved=LOCK_A):
    save(world.sessions, data={"txt2img": entry(formData={"seed": 5, "quantity": 2, "steps": saved})})
    overrides = {PRESET: {"txt2img": {"steps": {"editable": False, "default": LOCK_B}}}}
    return build_service(world.sessions, world.storage, overrides=overrides)


class TestLockedValuesNeverComeBack:
    def test_neither_value_is_in_the_template_the_snapshot_or_the_errors(self, world):
        service = locked_world(world)

        described = service.describe("u1", False, "s1", None)
        prepared = prepare(service)
        bad = refusal(prepare, service, form={"resolution": "nope"})

        assert "steps" not in described["resolved_request"]["form_data"]
        assert "steps" not in prepared.resolved_request["form_data"]
        assert str(LOCK_A) not in str(described["fields"]) and str(LOCK_B) not in str(described["fields"])
        assert "steps" not in bad.detail.get("field_errors", {})
        assert prepared.request.form_data["steps"] == LOCK_A

    def test_a_missing_file_in_a_locked_media_field_is_not_reported(self, world):
        save(world.sessions, data={"txt2img": entry(formData={"image": "uploads/secret-name.png"})})
        overrides = {PRESET: {"txt2img": {"image": {"editable": False, "default": None}}}}
        service = build_service(world.sessions, world.storage, overrides=overrides)

        described = service.describe("u1", False, "s1", None)

        assert "secret-name" not in str(described)
        assert prepare(service).session["id"] == "s1"

    def test_a_model_override_the_user_cannot_use_is_refused(self, world):
        save(world.sessions)
        error = refusal(prepare, build_service(world.sessions, world.storage), form={"checkpoint": "model:m2"})
        assert (error.status, error.code) == (403, "session_model_unavailable")


class TestStorage:
    def test_a_storage_root_that_cannot_be_resolved_refuses_instead_of_skipping_checks(self, world):
        save(world.sessions)
        service = build_service(world.sessions, world.storage)

        def broken(user_id):
            raise OSError("disk gone")

        service.settings.get_file_storage_directory = broken

        error = refusal(prepare, service)
        described = service.describe("u1", False, "s1", None)

        assert (error.status, error.code) == (503, "storage_unavailable")
        assert any(problem["code"] == "storage_unavailable" for problem in described["problems"])

    def test_a_media_override_outside_the_callers_storage_is_refused_here(self, world, tmp_path_factory):
        outside = tmp_path_factory.mktemp("elsewhere") / "private.png"
        outside.write_bytes(b"x")
        save(world.sessions)
        service = build_service(world.sessions, world.storage)

        for path in (str(outside), "../../etc/passwd", "uploads/../../outside.png"):
            error = refusal(prepare, service, form={"image": path})
            assert error.status == 422 and error.code in ("form_validation_failed", "input_missing"), path


def test_sessions_are_owner_only_and_carry_no_sharing_field():
    from src.features.sessions.dto import Session

    assert not [name for name in Session.model_fields if "share" in name or "member" in name or "workspace" in name]
