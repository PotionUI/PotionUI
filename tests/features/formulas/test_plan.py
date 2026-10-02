import dataclasses

import pytest

from src.features.formulas import operations
from src.features.formulas.dto import CreateFormulaRequest, PlanFormulaRequest
from src.features.formulas.errors import FormulaError
from src.features.formulas.sources import DeclaredGroup
from tests.features.formulas.conftest import make_user


def save(collaborators, groups, values, name="F", signatures=None):
    request = CreateFormulaRequest(
        preset_id="preset-a", mode="video", name=name,
        groups=[{"id": group} for group in groups], values=values, signatures=signatures or {},
    )
    return operations.create_formula(collaborators, "user-1", request)


def plan(collaborators, formula, current=None, lora_mode="replace", user=None):
    request = PlanFormulaRequest(form_name=None, current_values=current or {}, lora_mode=lora_mode)
    return operations.plan_formula(collaborators, user or make_user(), formula.id, request)


def skip_codes(result):
    return {(item["name"], item["code"]) for item in result["skips"]}


def change_names(result):
    return [item["name"] for item in result["changes"]]


def set_form(forms, **replacements):
    form = forms.forms[("preset-a", "video")]
    forms.forms[("preset-a", "video")] = dataclasses.replace(form, **replacements)


def test_changed_and_matching_values_are_classified(collaborators):
    formula = save(collaborators, ["speed"], {"speed_profile": "turbo", "steps": 4, "sampler": "euler"})

    result = plan(collaborators, formula, {"speed_profile": "quality", "steps": 4, "sampler": "euler"})

    assert change_names(result) == ["speed_profile"]
    assert [item["name"] for item in result["same"]] == ["steps", "sampler"]
    assert result["skips"] == []
    change = result["changes"][0]
    assert (change["old"], change["new"], change["label"], change["group_label"]) == (
        "quality", "turbo", "Speed profile", "Speed and sampling"
    )


def test_advanced_view_fields_are_marked(collaborators):
    formula = save(collaborators, ["speed"], {"speed_profile": "turbo", "steps": 4})

    changes = {item["name"]: item for item in plan(collaborators, formula)["changes"]}

    assert changes["steps"]["advanced"] is True
    assert changes["speed_profile"]["advanced"] is False


def test_a_missing_current_value_counts_as_a_change(collaborators):
    formula = save(collaborators, ["speed"], {"steps": 4})

    assert change_names(plan(collaborators, formula, {})) == ["steps"]


def test_a_second_plan_against_the_applied_values_is_all_same(collaborators):
    formula = save(collaborators, ["speed"], {"speed_profile": "turbo", "steps": 4})
    first = plan(collaborators, formula, {})
    applied = {item["name"]: item["new"] for item in first["changes"]}

    second = plan(collaborators, formula, applied)

    assert second["changes"] == [] and len(second["same"]) == 2


def test_a_select_value_no_longer_offered_is_skipped_and_the_rest_still_applies(collaborators, forms):
    formula = save(collaborators, ["speed"], {"speed_profile": "turbo", "sampler": "res_multistep", "steps": 4})
    form = forms.forms[("preset-a", "video")]
    fields = dict(form.fields)
    fields["sampler"] = {**fields["sampler"], "options": [{"value": "euler"}]}
    set_form(forms, fields=fields)

    result = plan(collaborators, formula)

    assert skip_codes(result) == {("sampler", "option_missing")}
    assert set(change_names(result)) == {"speed_profile", "steps"}


@pytest.mark.parametrize(
    "value,code",
    [(120, "out_of_range"), (1, "out_of_range"), (4.5, "out_of_step"), ("4", "invalid_value"), (True, "invalid_value")],
)
def test_slider_values_outside_the_current_range_are_skipped_not_clamped(collaborators, value, code):
    formula = save(collaborators, ["speed"], {"steps": 4, "speed_profile": "turbo"})
    stored = collaborators.repository.get_for_owner("user-1", formula.id)
    stored.values["steps"] = value
    collaborators.repository.update(stored)

    result = plan(collaborators, formula)

    assert skip_codes(result) == {("steps", code)}
    assert change_names(result) == ["speed_profile"]
    assert result["skips"][0]["reason"]


def test_a_narrowed_range_skips_a_previously_valid_value_and_reports_the_bounds(collaborators, forms):
    formula = save(collaborators, ["speed"], {"steps": 80})
    fields = dict(forms.forms[("preset-a", "video")].fields)
    fields["steps"] = {**fields["steps"], "maximum": 50}
    set_form(forms, fields=fields)

    result = plan(collaborators, formula)

    assert result["changes"] == []
    assert result["skips"][0]["detail"] == {"min": 2, "max": 50, "step": 1}


def test_a_step_that_no_longer_fits_is_skipped(collaborators, forms):
    formula = save(collaborators, ["speed"], {"steps": 5})
    fields = dict(forms.forms[("preset-a", "video")].fields)
    fields["steps"] = {**fields["steps"], "step": 2}
    set_form(forms, fields=fields)

    assert skip_codes(plan(collaborators, formula)) == {("steps", "out_of_step")}


def test_a_text_value_failing_the_pattern_is_skipped(collaborators):
    formula = save(collaborators, ["misc"], {"note": "abc", "warmup": True})
    stored = collaborators.repository.get_for_owner("user-1", formula.id)
    stored.values["note"] = "ABC 1"
    collaborators.repository.update(stored)

    result = plan(collaborators, formula)

    assert skip_codes(result) == {("note", "pattern_mismatch")}
    assert change_names(result) == ["warmup"]


def test_checkbox_group_keeps_offered_values_and_lists_the_dropped(collaborators):
    formula = save(collaborators, ["misc"], {"tags": ["a", "b"]})
    stored = collaborators.repository.get_for_owner("user-1", formula.id)
    stored.values["tags"] = ["a", "gone"]
    collaborators.repository.update(stored)

    result = plan(collaborators, formula)

    assert result["changes"][0]["new"] == ["a"]
    assert skip_codes(result) == {("tags", "option_missing")}
    assert result["skips"][0]["detail"] == {"value": "gone"}


def test_checkbox_group_with_nothing_offered_is_skipped(collaborators):
    formula = save(collaborators, ["misc"], {"tags": ["a"]})
    stored = collaborators.repository.get_for_owner("user-1", formula.id)
    stored.values["tags"] = ["gone"]
    collaborators.repository.update(stored)

    result = plan(collaborators, formula)

    assert result["changes"] == [] and skip_codes(result) == {("tags", "options_unavailable")}


def test_a_model_is_applied_when_it_resolves(collaborators, models):
    models.add("m1", model_type="checkpoint", tag_ids=["base-x"])
    formula = save(collaborators, ["misc"], {"checkpoint": "model:m1"})

    result = plan(collaborators, formula, {"checkpoint": "model:other"})

    assert result["changes"][0]["new"] == "model:m1"


@pytest.mark.parametrize(
    "model,code",
    [
        (None, "model_unavailable"),
        (("checkpoint", ["base-x"], False), "model_unavailable"),
        (("lora", ["base-x"], True), "model_wrong_type"),
        (("checkpoint", ["other"], True), "model_filtered"),
    ],
)
def test_a_model_that_cannot_be_used_is_skipped_with_no_substitute(collaborators, models, model, code):
    if model:
        models.add("m1", model_type=model[0], tag_ids=model[1], available=model[2])
    formula = save(collaborators, ["misc"], {"checkpoint": "model:m1"})

    result = plan(collaborators, formula, {"checkpoint": "model:current"})

    assert result["changes"] == []
    assert skip_codes(result) == {("checkpoint", code)}


def test_a_model_hidden_from_this_user_is_skipped_as_missing(collaborators, models):
    models.add("m1", model_type="checkpoint", tag_ids=["base-x"])
    models.hidden_from["m1"] = {"user-1"}
    formula = save(collaborators, ["misc"], {"checkpoint": "model:m1"})

    assert skip_codes(plan(collaborators, formula)) == {("checkpoint", "model_unavailable")}


def test_a_value_that_is_not_a_model_reference_is_skipped(collaborators):
    formula = save(collaborators, ["misc"], {"checkpoint": "model:m1"})
    stored = collaborators.repository.get_for_owner("user-1", formula.id)
    stored.values["checkpoint"] = "some/file.safetensors"
    collaborators.repository.update(stored)

    assert skip_codes(plan(collaborators, formula)) == {("checkpoint", "model_unavailable")}


def lora(model, strength=0.75, **extra):
    return {"model": model, "strength": strength, **extra}


def installed_loras(models, *ids):
    for model_id in ids:
        models.add(model_id, model_type="lora")


def test_lora_replace_uses_the_saved_rows_and_reports_each_row_status(collaborators, models):
    installed_loras(models, "l1", "l2", "l3")
    formula = save(collaborators, ["loras"], {"loras": [lora("model:l1"), lora("model:l2", 0.5)]})
    current = {"loras": [lora("model:l2", 1.0), lora("model:l3")]}

    result = plan(collaborators, formula, current, "replace")

    change = result["changes"][0]
    assert [row["model"] for row in change["new"]] == ["model:l1", "model:l2"]
    assert {row["model"]: row["status"] for row in change["rows"]} == {
        "model:l1": "added", "model:l2": "changed", "model:l3": "removed"
    }


def test_lora_add_keeps_current_rows_and_updates_a_shared_model_in_place(collaborators, models):
    installed_loras(models, "l1", "l2", "l3")
    formula = save(collaborators, ["loras"], {"loras": [lora("model:l1"), lora("model:l2", 0.5)]})
    current = {"loras": [lora("model:l2", 1.0), lora("model:l3")]}

    change = plan(collaborators, formula, current, "add")["changes"][0]

    assert [(row["model"], row["strength"]) for row in change["new"]] == [
        ("model:l2", 0.5), ("model:l3", 0.75), ("model:l1", 0.75)
    ]
    assert {row["model"]: row["status"] for row in change["rows"]}["model:l3"] == "same"


def test_lora_rows_not_installed_are_skipped_individually(collaborators, models):
    installed_loras(models, "l1")
    formula = save(collaborators, ["loras"], {"loras": [lora("model:l1"), lora("model:ghost")]})

    result = plan(collaborators, formula)

    assert [row["model"] for row in result["changes"][0]["new"]] == ["model:l1"]
    assert skip_codes(result) == {("loras", "lora_unavailable")}
    assert result["skips"][0]["detail"] == {"model": "model:ghost"}


def test_lora_with_no_usable_rows_is_skipped_instead_of_clearing_the_list(collaborators, models):
    formula = save(collaborators, ["loras"], {"loras": [lora("model:ghost")]})

    result = plan(collaborators, formula, {"loras": [lora("model:l1")]})

    assert result["changes"] == [] and skip_codes(result) == {("loras", "lora_none_available")}


def test_an_empty_saved_lora_list_clears_in_replace_mode(collaborators, models):
    installed_loras(models, "l1")
    formula = save(collaborators, ["loras"], {"loras": []})

    change = plan(collaborators, formula, {"loras": [lora("model:l1")]}, "replace")["changes"][0]

    assert change["new"] == [] and change["rows"][0]["status"] == "removed"


def test_lora_of_the_wrong_type_is_skipped(collaborators, models):
    models.add("c1", model_type="checkpoint")
    models.add("l1", model_type="lora")
    formula = save(collaborators, ["loras"], {"loras": [lora("model:l1"), lora("model:c1")]})

    assert skip_codes(plan(collaborators, formula)) == {("loras", "lora_wrong_type")}


def test_lora_strength_outside_the_range_is_skipped_not_clamped(collaborators, models):
    installed_loras(models, "l1", "l2")
    formula = save(collaborators, ["loras"], {"loras": [lora("model:l1"), lora("model:l2")]})
    stored = collaborators.repository.get_for_owner("user-1", formula.id)
    stored.values["loras"][1]["strength"] = 5
    collaborators.repository.update(stored)

    result = plan(collaborators, formula)

    assert [row["model"] for row in result["changes"][0]["new"]] == ["model:l1"]
    assert skip_codes(result) == {("loras", "lora_strength_out_of_range")}


def test_lora_rows_beyond_the_limit_are_reported(collaborators, models):
    installed_loras(models, "l1", "l2", "l3", "l4")
    formula = save(collaborators, ["loras"], {"loras": [lora(f"model:l{i}") for i in (1, 2, 3)]})

    result = plan(collaborators, formula, {"loras": [lora("model:l4")]}, "add")

    assert [row["model"] for row in result["changes"][0]["new"]] == ["model:l4", "model:l1", "model:l2"]
    assert skip_codes(result) == {("loras", "lora_over_limit")}
    assert result["skips"][0]["detail"] == {"model": "model:l3"}


def test_lora_duplicates_collapse_and_unknown_row_fields_are_dropped(collaborators, models):
    installed_loras(models, "l1")
    formula = save(collaborators, ["loras"], {"loras": []})
    stored = collaborators.repository.get_for_owner("user-1", formula.id)
    stored.values["loras"] = [lora("model:l1", 0.5, audio=True, junk=1), lora("model:l1", 0.9)]
    collaborators.repository.update(stored)

    new = plan(collaborators, formula)["changes"][0]["new"]

    assert new == [{"model": "model:l1", "strength": 0.9, "audio": True}]


def test_a_matching_lora_list_is_the_same(collaborators, models):
    installed_loras(models, "l1")
    formula = save(collaborators, ["loras"], {"loras": [lora("model:l1")]})

    result = plan(collaborators, formula, {"loras": [lora("model:l1")]})

    assert result["changes"] == [] and [item["name"] for item in result["same"]] == ["loras"]


def test_companion_keys_follow_their_field(collaborators, models):
    installed_loras(models, "l1")
    formula = save(collaborators, ["loras"], {"loras": [lora("model:l1")], "loras_tagFilters": ["x"]})

    result = plan(collaborators, formula)

    companion = [item for item in result["changes"] if item["name"] == "loras_tagFilters"][0]
    assert companion["companion_of"] == "loras" and companion["new"] == ["x"]


def test_companion_keys_are_dropped_when_their_field_is_skipped(collaborators):
    formula = save(collaborators, ["loras"], {"loras": [lora("model:ghost")], "loras_tagFilters": ["x"]})

    result = plan(collaborators, formula)

    assert result["changes"] == [] and skip_codes(result) == {("loras", "lora_none_available")}


def test_a_field_removed_from_the_form_is_skipped(collaborators, forms):
    formula = save(collaborators, ["speed"], {"speed_profile": "turbo", "steps": 4})
    fields = {key: value for key, value in forms.forms[("preset-a", "video")].fields.items() if key != "steps"}
    set_form(forms, fields=fields)

    result = plan(collaborators, formula)

    assert skip_codes(result) == {("steps", "field_removed")}
    assert change_names(result) == ["speed_profile"]


def test_a_field_changed_to_another_control_is_skipped(collaborators, forms):
    formula = save(collaborators, ["speed"], {"speed_profile": "turbo", "steps": 4})
    fields = dict(forms.forms[("preset-a", "video")].fields)
    fields["steps"] = {"type": "textbox", "name": "steps", "title": "Steps"}
    set_form(forms, fields=fields)

    assert skip_codes(plan(collaborators, formula)) == {("steps", "type_changed")}


def test_a_group_removed_from_the_declaration_skips_exactly_its_fields(collaborators, forms):
    formula = save(collaborators, ["speed", "size"], {"speed_profile": "turbo", "steps": 4, "resolution": "832x480"})
    groups = [group for group in forms.forms[("preset-a", "video")].groups if group.id != "size"]
    set_form(forms, groups=groups)

    result = plan(collaborators, formula)

    assert skip_codes(result) == {("resolution", "not_declared")}
    assert set(change_names(result)) == {"speed_profile", "steps"}


def test_a_field_moved_to_another_group_skips_exactly_that_field(collaborators, forms):
    formula = save(collaborators, ["speed", "size"], {"speed_profile": "turbo", "steps": 4, "resolution": "832x480"})
    groups = [
        DeclaredGroup("speed", "Speed and sampling", ["speed_profile", "steps", "resolution"]),
        DeclaredGroup("size", "Size", []),
        DeclaredGroup("loras", "LoRAs", ["loras"]),
    ]
    set_form(forms, groups=groups)

    result = plan(collaborators, formula)

    assert skip_codes(result) == {("resolution", "moved_group")}
    assert result["skips"][0]["detail"] == {"now_in": "speed"}
    assert set(change_names(result)) == {"speed_profile", "steps"}


def test_a_field_no_longer_in_any_group_is_skipped_when_the_declaration_is_gone(collaborators, forms):
    formula = save(collaborators, ["speed"], {"steps": 4})
    set_form(forms, groups=[])

    assert skip_codes(plan(collaborators, formula)) == {("steps", "not_declared")}


def test_live_labels_come_from_the_current_declaration(collaborators, forms):
    formula = save(collaborators, ["speed"], {"steps": 4})
    groups = [DeclaredGroup("speed", "Pace", ["speed_profile", "steps", "sampler"])]
    set_form(forms, groups=groups)

    assert plan(collaborators, formula)["changes"][0]["group_label"] == "Pace"


def test_a_field_newly_declared_after_saving_is_left_alone(collaborators, forms):
    formula = save(collaborators, ["speed"], {"steps": 4})
    groups = [DeclaredGroup("speed", "Speed and sampling", ["speed_profile", "steps", "sampler"])]
    set_form(forms, groups=groups)

    assert change_names(plan(collaborators, formula, {"sampler": "euler"})) == ["steps"]


def test_plan_loads_the_requested_form_variant(collaborators, forms):
    formula = save(collaborators, ["speed"], {"steps": 4})

    operations.plan_formula(collaborators, make_user(), formula.id, PlanFormulaRequest(form_name="alt", current_values={}))

    assert forms.requested[-1] == ("preset-a", "video", "alt")


def test_plan_for_a_form_that_cannot_load_is_refused(collaborators, forms):
    formula = save(collaborators, ["speed"], {"steps": 4})
    del forms.forms[("preset-a", "video")]

    with pytest.raises(FormulaError) as caught:
        plan(collaborators, formula)

    assert caught.value.code == "form_unavailable"


def test_plan_of_another_users_formula_is_not_found(collaborators):
    formula = save(collaborators, ["speed"], {"steps": 4})

    with pytest.raises(FormulaError) as caught:
        plan(collaborators, formula, user=make_user("user-2"))

    assert caught.value.code == "formula_not_found"


def test_plan_never_writes(collaborators, connection):
    formula = save(collaborators, ["speed"], {"steps": 4})
    before = collaborators.repository.get_for_owner("user-1", formula.id)

    plan(collaborators, formula, {"steps": 9})

    assert collaborators.repository.get_for_owner("user-1", formula.id) == before


@pytest.mark.parametrize(
    "current",
    [
        [{"model": []}],
        [{"model": {"a": 1}}, {"model": None}, "junk", 7],
        {"model": "model:l1"},
        "model:l1",
    ],
)
@pytest.mark.parametrize("lora_mode", ["replace", "add"])
def test_malformed_current_lora_rows_are_treated_as_an_empty_list(collaborators, models, current, lora_mode):
    installed_loras(models, "l1")
    formula = save(collaborators, ["loras"], {"loras": [lora("model:l1")]})

    change = plan(collaborators, formula, {"loras": current}, lora_mode)["changes"][0]

    assert [row["model"] for row in change["new"]] == ["model:l1"]
    assert [row["status"] for row in change["rows"]] == ["added"]


def store_values(collaborators, formula, **values):
    stored = collaborators.repository.get_for_owner("user-1", formula.id)
    stored.values.update(values)
    collaborators.repository.update(stored)


def add_field(forms, group_id, spec):
    form = forms.forms[("preset-a", "video")]
    fields = {**form.fields, spec["name"]: spec}
    groups = [*form.groups, DeclaredGroup(group_id, group_id.title(), [spec["name"]])]
    set_form(forms, fields=fields, groups=groups)


def test_cloud_options_must_be_a_mapping(collaborators, forms):
    add_field(forms, "cloud", {"type": "cloud_options", "name": "cloud", "title": "Cloud"})
    formula = save(collaborators, ["cloud"], {"cloud": {"quality": "hd"}})

    assert plan(collaborators, formula)["changes"][0]["new"] == {"quality": "hd"}

    store_values(collaborators, formula, cloud=["hd"])
    result = plan(collaborators, formula)

    assert result["changes"] == [] and skip_codes(result) == {("cloud", "invalid_value")}


def test_tags_field_keeps_a_list_and_rejects_anything_else(collaborators, forms):
    add_field(forms, "labels", {"type": "tags", "name": "labels", "title": "Labels"})
    formula = save(collaborators, ["labels"], {"labels": ["x", "y"]})

    assert plan(collaborators, formula)["changes"][0]["new"] == ["x", "y"]

    store_values(collaborators, formula, labels="x")

    assert skip_codes(plan(collaborators, formula)) == {("labels", "invalid_value")}


def test_tags_field_with_options_drops_the_unoffered_ones(collaborators, forms):
    spec = {"type": "tags", "name": "labels", "title": "Labels", "options": [{"value": "x"}]}
    add_field(forms, "labels", spec)
    formula = save(collaborators, ["labels"], {"labels": ["x", "y"]})

    result = plan(collaborators, formula)

    assert result["changes"][0]["new"] == ["x"]
    assert skip_codes(result) == {("labels", "option_missing")}


def test_plan_defaults_to_the_variant_the_formula_was_saved_for(collaborators, forms):
    request = CreateFormulaRequest(
        preset_id="preset-a", mode="video", name="V", variant="alt",
        groups=[{"id": "speed"}], values={"steps": 4},
    )
    formula = operations.create_formula(collaborators, "user-1", request)

    plan(collaborators, formula)

    assert forms.requested[-1] == ("preset-a", "video", "alt")


def test_a_field_missing_from_another_variant_is_skipped_as_a_variant_change(collaborators, forms):
    request = CreateFormulaRequest(
        preset_id="preset-a", mode="video", name="V", variant="alt",
        groups=[{"id": "speed"}], values={"steps": 4, "speed_profile": "turbo"},
    )
    formula = operations.create_formula(collaborators, "user-1", request)
    fields = {key: value for key, value in forms.forms[("preset-a", "video")].fields.items() if key != "steps"}
    set_form(forms, fields=fields)

    other = operations.plan_formula(
        collaborators, make_user(), formula.id, PlanFormulaRequest(form_name="other", current_values={})
    )
    same = plan(collaborators, formula)

    assert skip_codes(other) == {("steps", "variant_changed")}
    assert skip_codes(same) == {("steps", "field_removed")}
    assert change_names(other) == ["speed_profile"]


@pytest.mark.parametrize(
    "companion,accepted",
    [
        ("image_inpaint_mask", True),
        ("image__origin", True),
        ("image_tagFilters", False),
        ("checkpoint_tagFilters", True),
        ("checkpoint_inpaint_mask", False),
        ("checkpoint__origin", False),
        ("steps_tagFilters", False),
        ("steps__anything", False),
    ],
)
def test_a_companion_is_applied_only_for_owner_types_that_take_it(collaborators, models, forms, companion, accepted):
    models.add("m1", model_type="checkpoint", tag_ids=["base-x"])
    form = forms.forms[("preset-a", "video")]
    fields = {**form.fields, "image": {"type": "image", "name": "image", "title": "Image"}}
    set_form(forms, fields=fields, groups=[DeclaredGroup("all", "All", ["image", "checkpoint", "steps"])])
    formula = save(collaborators, ["all"], {"image": "a.png", "checkpoint": "model:m1", "steps": 4, companion: "v"})

    names = change_names(plan(collaborators, formula))

    assert (companion in names) is accepted
