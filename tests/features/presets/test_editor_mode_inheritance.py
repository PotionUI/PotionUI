from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.generation.history_query import GenerationHistoryQuery
from src.features.presets.name_resolver import PresetNameResolver
from src.features.presets.templates import (
    EditorModeSpec,
    FormTemplate,
    ModeTemplate,
    PresetTemplate,
    inherited_mode_key,
)
from src.features.prompt.resources import mode_prompt_resources
from src.features.sessions.generate import SessionRequests

RESOURCES = [{"field": "references", "kind": "image", "token": "pic"}]
SYNTAX = [{"syntax": "weights"}]


def spec(name, from_mode):
    return EditorModeSpec(name=name, task="edit_region", region="crop", inputs={"source": "picture"}, size={}, from_mode=from_mode)


def template():
    mode = lambda **kw: ModeTemplate(forms=[FormTemplate(name="custom", fields=[])], pipes=[], **kw)
    return PresetTemplate(
        id="P1",
        name="Preset",
        version="1",
        path="/p",
        modes={"edit": mode(), "txt2img": mode()},
        editor_modes={"edit": mode(editor=spec("edit", "edit")), "alone": mode(editor=spec("alone", None))},
        prompt_resources={"edit": RESOURCES},
        prompt_syntax={"edit": SYNTAX},
    )


def test_a_derived_editor_key_resolves_to_its_from_mode_and_everything_else_to_itself():
    subject = template()

    assert inherited_mode_key(subject, "editor:edit") == "edit"
    assert inherited_mode_key(subject, "editor:alone") == "editor:alone"
    assert inherited_mode_key(subject, "editor:missing") == "editor:missing"
    assert inherited_mode_key(subject, "edit") == "edit"
    assert inherited_mode_key(subject, None) is None
    assert inherited_mode_key(None, "editor:edit") == "editor:edit"


def test_prompt_resources_of_the_from_mode_apply_to_its_editor_mode_and_not_to_a_standalone_one():
    subject = template()

    assert mode_prompt_resources(subject, "edit") == RESOURCES
    assert mode_prompt_resources(subject, "editor:edit") == RESOURCES
    assert mode_prompt_resources(subject, "editor:alone") == []
    assert mode_prompt_resources(subject, "txt2img") == []


def requests_with(stored, subject):
    service = object.__new__(SessionRequests)
    service.preset_db = SimpleNamespace(get_preset_form_overrides=lambda preset_id: stored)
    service.preset_loader = SimpleNamespace(load_preset_by_id=lambda preset_id: subject)
    return service


def test_the_admin_locks_of_the_from_mode_apply_to_its_editor_mode():
    stored = {"edit": {"steps": {"default": 9, "editable": False}}, "txt2img": {"cfg": {"visible": False}}}
    service = requests_with(stored, template())

    assert service._locks("P1", "editor:edit") == {"steps": {"default": 9, "editable": False}}
    assert service._locks("P1", "edit") == {"steps": {"default": 9, "editable": False}}
    assert service._locks("P1", "txt2img") == {"cfg": {"visible": False}}


def test_a_standalone_editor_mode_has_no_inherited_locks_and_a_lookup_failure_means_none():
    service = requests_with({"edit": {"steps": {"editable": False}}}, template())

    assert service._locks("P1", "editor:alone") == {}
    broken = object.__new__(SessionRequests)
    broken.preset_db = SimpleNamespace(get_preset_form_overrides=Mock(side_effect=RuntimeError("down")))
    broken.preset_loader = SimpleNamespace(load_preset_by_id=lambda preset_id: None)
    assert broken._locks("P1", "edit") == {}


def test_the_editor_modes_own_locks_win_over_the_inherited_ones():
    stored = {"edit": {"seed": {"editable": False}}, "editor:edit": {"seed": {"editable": True}}}
    service = requests_with(stored, template())

    assert service._locks("P1", "editor:edit") == {"seed": {"editable": False}}


def resolver_for(subject):
    loader = SimpleNamespace(get=lambda preset_id: subject if preset_id == "P1" else None, all=lambda: [subject])
    return PresetNameResolver(loader)


@pytest.mark.parametrize(
    "mode, expected",
    [("txt2img", "txt2img"), ("edit", "edit"), ("editor:edit", "edit"), ("editor:alone", None), ("editor:gone", None)],
)
def test_history_reuse_opens_the_from_mode_and_is_hidden_for_a_standalone_one(mode, expected):
    assert resolver_for(template()).reuse_mode("P1", mode) == expected


def test_history_reuse_for_an_editor_key_of_a_missing_preset_is_hidden():
    assert resolver_for(template()).reuse_mode("GONE", "editor:edit") is None


def history_for(resolver):
    history = object.__new__(GenerationHistoryQuery)
    history.preset_name_resolver = resolver
    return history


def test_the_history_payload_carries_the_reuse_mode_for_every_kind_of_mode():
    history = history_for(resolver_for(template()))

    plain = SimpleNamespace(mode="txt2img", preset_id="P1")
    derived = SimpleNamespace(mode="editor:edit", preset_id="P1")
    standalone = SimpleNamespace(mode="editor:alone", preset_id="P1")

    assert history._reuse_mode(plain) == "txt2img"
    assert history._reuse_mode(derived) == "edit"
    assert history._reuse_mode(standalone) is None


def test_without_a_resolver_an_editor_generation_cannot_be_reused_and_a_plain_one_is_unchanged():
    history = history_for(None)

    assert history._reuse_mode(SimpleNamespace(mode="editor:edit", preset_id="P1")) is None
    assert history._reuse_mode(SimpleNamespace(mode="edit", preset_id="P1")) == "edit"


def test_serializing_history_adds_the_reuse_mode_beside_the_stored_mode():
    history = history_for(resolver_for(template()))
    history._attach_system_tags = lambda items: None
    history._apply_content_ledger = lambda items, viewer_id: items
    history._preset_name_map = lambda: {"P1": "Preset"}

    def generation(mode):
        return SimpleNamespace(
            mode=mode,
            preset_id="P1",
            to_dict=lambda include_files=False, include_tags=False: {"id": "g", "mode": mode},
        )

    items = history.serialize_generations([generation("editor:edit"), generation("editor:alone"), generation("txt2img")], False, viewer_id="u1")

    assert [(item["mode"], item["reuse_mode"]) for item in items] == [
        ("editor:edit", "edit"),
        ("editor:alone", None),
        ("txt2img", "txt2img"),
    ]
