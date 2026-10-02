import dataclasses

import pytest

from src.features.formulas import operations
from src.features.formulas.dto import MAX_FORMULAS_PER_SCOPE, CreateFormulaRequest, UpdateFormulaRequest
from src.features.formulas.errors import FormulaError
from src.features.formulas.repository import FormulaLimitReached
from src.features.formulas.signatures import field_signature


def create_request(**overrides):
    payload = {
        "preset_id": "preset-a",
        "mode": "video",
        "name": "Turbo",
        "groups": [{"id": "speed"}, {"id": "size"}],
        "values": {"speed_profile": "turbo", "steps": 4, "resolution": "832x480"},
    }
    payload.update(overrides)
    return CreateFormulaRequest(**payload)


def create(collaborators, owner="user-1", **overrides):
    return operations.create_formula(collaborators, owner, create_request(**overrides))


def error_code(call):
    with pytest.raises(FormulaError) as caught:
        call()
    return caught.value.code


def test_create_keeps_only_declared_group_fields(collaborators):
    formula = create(
        collaborators,
        groups=[{"id": "speed"}],
        values={
            "speed_profile": "turbo", "steps": 4, "prompt": "a cat", "seed": 7, "image": "x.png",
            "resolution": "832x480", "unknown": 1,
        },
    )

    assert formula.values == {"speed_profile": "turbo", "steps": 4}
    assert formula.groups == [{"id": "speed", "label": "Speed and sampling", "fields": ["speed_profile", "steps"]}]


def test_create_drops_companion_keys_of_fields_outside_the_selection(collaborators):
    formula = create(
        collaborators,
        groups=[{"id": "speed"}],
        values={"steps": 4, "loras_tagFilters": ["x"], "loras__extra": 1, "resolution_inpaint_mask": "m"},
    )

    assert formula.values == {"steps": 4}


def test_create_keeps_companion_keys_of_selected_fields(collaborators):
    formula = create(
        collaborators,
        groups=[{"id": "loras"}],
        values={"loras": [], "loras_tagFilters": ["x"], "loras__extra": 1},
    )

    assert set(formula.values) == {"loras", "loras_tagFilters", "loras__extra"}


def test_create_stores_groups_as_resolved_not_as_sent(collaborators):
    formula = create(
        collaborators,
        groups=[{"id": "size"}],
        values={"resolution": "832x480"},
    )

    assert formula.groups == [{"id": "size", "label": "Size", "fields": ["resolution"]}]


def test_create_rejects_an_unknown_group(collaborators):
    assert error_code(lambda: create(collaborators, groups=[{"id": "nope"}])) == "unknown_group"


def test_create_rejects_values_that_belong_to_no_selected_group(collaborators):
    code = error_code(lambda: create(collaborators, groups=[{"id": "size"}], values={"prompt": "x", "steps": 4}))

    assert code == "formula_empty"


def test_create_rejects_a_mode_without_declared_groups(collaborators, forms):
    forms.forms[("preset-a", "video")] = type(forms.forms[("preset-a", "video")])(
        fields={}, groups=[], preset_version="1"
    )

    assert error_code(lambda: create(collaborators)) == "formulas_not_declared"


def test_create_rejects_an_unknown_preset(collaborators):
    assert error_code(lambda: create(collaborators, preset_id="missing")) == "form_unavailable"


def test_create_fills_signatures_from_the_preset(collaborators, forms):
    formula = create(collaborators)

    assert formula.signatures["steps"] == field_signature(forms.forms[("preset-a", "video")].fields["steps"])
    assert formula.signatures["steps"] == {"type": "slider", "min": 2, "max": 100, "step": 1}


def test_create_accepts_matching_client_signatures(collaborators):
    formula = create(collaborators, signatures={"steps": {"type": "slider", "min": 2, "max": 100, "step": 1}})

    assert formula.signatures["steps"]["max"] == 100


def test_create_rejects_a_signature_that_disagrees_with_the_preset(collaborators):
    code = error_code(
        lambda: create(collaborators, signatures={"steps": {"type": "slider", "min": 2, "max": 150, "step": 1}})
    )

    assert code == "signature_mismatch"


def test_create_rejects_values_over_the_size_limit():
    with pytest.raises(ValueError):
        create_request(values={"steps": "x" * (64 * 1024)})


def test_create_rejects_the_hundred_and_first_formula(collaborators):
    for index in range(100):
        create(collaborators, name=f"F{index}")

    assert error_code(lambda: create(collaborators, name="one more")) == "formula_limit_reached"


def test_the_formula_limit_is_per_user_preset_and_mode(collaborators, forms):
    for index in range(100):
        create(collaborators, name=f"F{index}")
    forms.forms[("preset-a", "refs")] = forms.forms[("preset-a", "video")]

    create(collaborators, owner="user-2", name="F0")
    create(collaborators, mode="refs", name="F0")


def test_names_are_unique_per_owner_preset_and_mode(collaborators, forms):
    create(collaborators, name="Turbo")
    forms.forms[("preset-a", "refs")] = forms.forms[("preset-a", "video")]

    assert error_code(lambda: create(collaborators, name="Turbo")) == "formula_name_exists"
    create(collaborators, owner="user-2", name="Turbo")
    create(collaborators, mode="refs", name="Turbo")


def test_names_are_trimmed_and_cannot_be_blank():
    assert create_request(name="  Turbo  ").name == "Turbo"
    with pytest.raises(ValueError):
        create_request(name="   ")


def test_duplicate_names_the_copy_and_numbers_further_copies(collaborators):
    original = create(collaborators, name="Turbo")

    first = operations.duplicate_formula(collaborators, "user-1", original.id)
    second = operations.duplicate_formula(collaborators, "user-1", original.id)
    third = operations.duplicate_formula(collaborators, "user-1", original.id)

    assert [first.name, second.name, third.name] == ["Turbo copy", "Turbo copy 2", "Turbo copy 3"]
    assert first.values == original.values and first.id != original.id


def test_duplicate_respects_the_limit(collaborators):
    first = create(collaborators, name="F0")
    for index in range(1, 100):
        create(collaborators, name=f"F{index}")

    assert error_code(lambda: operations.duplicate_formula(collaborators, "user-1", first.id)) == "formula_limit_reached"


def test_update_renames_and_sets_the_note(collaborators):
    formula = create(collaborators)

    updated = operations.update_formula(
        collaborators, "user-1", formula.id, UpdateFormulaRequest(name="Fast", note="my note")
    )

    assert (updated.name, updated.note) == ("Fast", "my note")
    assert updated.values == formula.values


def test_update_clears_the_note_with_an_empty_string(collaborators):
    formula = create(collaborators, note="x")

    updated = operations.update_formula(collaborators, "user-1", formula.id, UpdateFormulaRequest(note=""))

    assert updated.note is None


def test_update_to_a_taken_name_is_refused(collaborators):
    create(collaborators, name="A")
    other = create(collaborators, name="B")

    code = error_code(lambda: operations.update_formula(collaborators, "user-1", other.id, UpdateFormulaRequest(name="A")))

    assert code == "formula_name_exists"


def test_update_replaces_content_through_the_same_filtering(collaborators):
    formula = create(collaborators)
    content = {
        "groups": [{"id": "size"}],
        "values": {"resolution": "1024x1024", "prompt": "no", "steps": 9},
    }

    updated = operations.update_formula(collaborators, "user-1", formula.id, UpdateFormulaRequest(content=content))

    assert updated.values == {"resolution": "1024x1024"}
    assert updated.groups == [{"id": "size", "label": "Size", "fields": ["resolution"]}]


def test_update_content_checks_signatures(collaborators):
    formula = create(collaborators)
    content = {
        "groups": [{"id": "speed"}],
        "values": {"steps": 4},
        "signatures": {"steps": {"type": "slider", "min": 0, "max": 100, "step": 1}},
    }

    code = error_code(
        lambda: operations.update_formula(collaborators, "user-1", formula.id, UpdateFormulaRequest(content=content))
    )

    assert code == "signature_mismatch"


def test_list_is_scoped_to_owner_preset_and_mode(collaborators, forms):
    forms.forms[("preset-a", "refs")] = forms.forms[("preset-a", "video")]
    mine = create(collaborators, name="Mine")
    create(collaborators, owner="user-2", name="Theirs")
    create(collaborators, mode="refs", name="Other mode")

    listed = operations.list_formulas(collaborators, "user-1", "preset-a", "video")

    assert [formula.id for formula in listed] == [mine.id]


@pytest.mark.parametrize(
    "call",
    [
        lambda c, fid: operations.get_formula(c, "user-2", fid),
        lambda c, fid: operations.update_formula(c, "user-2", fid, UpdateFormulaRequest(name="Stolen")),
        lambda c, fid: operations.duplicate_formula(c, "user-2", fid),
        lambda c, fid: operations.delete_formula(c, "user-2", fid),
    ],
)
def test_another_users_formula_is_not_found(collaborators, call):
    formula = create(collaborators)

    assert error_code(lambda: call(collaborators, formula.id)) == "formula_not_found"
    assert operations.get_formula(collaborators, "user-1", formula.id).name == "Turbo"


def test_delete_removes_the_formula(collaborators):
    formula = create(collaborators)

    operations.delete_formula(collaborators, "user-1", formula.id)

    assert error_code(lambda: operations.get_formula(collaborators, "user-1", formula.id)) == "formula_not_found"


def test_formulas_hold_no_session_reference_and_neither_side_deletes_the_other(collaborators, connection):
    connection.execute("INSERT INTO sessions VALUES ('s1', 'user-1', 'preset-a', 'Session', '{}')")
    connection.commit()
    formula = create(collaborators)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(formulas)")}

    connection.execute("DELETE FROM sessions")
    connection.commit()
    assert operations.get_formula(collaborators, "user-1", formula.id).name == "Turbo"

    connection.execute("INSERT INTO sessions VALUES ('s2', 'user-1', 'preset-a', 'Session', '{}')")
    connection.commit()
    operations.delete_formula(collaborators, "user-1", formula.id)

    assert not any("session" in column for column in columns)
    assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_the_cap_holds_inside_the_insert_itself(collaborators):
    first = create(collaborators, name="F0")
    for index in range(1, 100):
        create(collaborators, name=f"F{index}")

    with pytest.raises(FormulaLimitReached):
        collaborators.repository.create(dataclasses.replace(first, name="sneaked in"), MAX_FORMULAS_PER_SCOPE)

    assert len(collaborators.repository.names_for_owner("user-1", "preset-a", "video")) == 100


def test_names_differing_only_in_case_are_the_same_name(collaborators):
    create(collaborators, name="Fast")
    other = create(collaborators, name="Slow")

    assert error_code(lambda: create(collaborators, name="fast")) == "formula_name_exists"
    code = error_code(
        lambda: operations.update_formula(collaborators, "user-1", other.id, UpdateFormulaRequest(name="FAST"))
    )

    assert code == "formula_name_exists"


def test_update_with_an_undeclared_group_is_refused_and_leaves_the_row_alone(collaborators):
    formula = create(collaborators)
    content = {"groups": [{"id": "nope"}], "values": {"steps": 9}}

    code = error_code(
        lambda: operations.update_formula(
            collaborators, "user-1", formula.id, UpdateFormulaRequest(name="Renamed", content=content)
        )
    )

    stored = operations.get_formula(collaborators, "user-1", formula.id)
    assert code == "unknown_group"
    assert stored.name == "Turbo" and stored.values == formula.values and stored.groups == formula.groups
