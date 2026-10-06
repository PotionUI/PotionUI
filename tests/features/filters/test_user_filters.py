import pytest

from src.features.filters import operations
from src.features.filters.dto import MAX_USER_FILTERS, CreateFilterRequest, UpdateFilterRequest
from src.features.filters.errors import FilterError
from src.features.filters.records import UserFilter
from tests.features.filters.conftest import CUBE_2, plugin, registry, write_filter

STEPS = [{"op": "tone", "contrast": 12}, {"op": "vignette", "amount": 20}]


def create(collaborators, owner="user-1", **overrides):
    body = {"name": "My Ember", "steps": STEPS}
    body.update(overrides)
    return operations.create_filter(collaborators, owner, CreateFilterRequest(**body))


def code_of(call):
    with pytest.raises(FilterError) as caught:
        call()
    return caught.value.code, caught.value.status_code


def test_create_stores_the_recipe_for_the_owner(collaborators):
    created = create(collaborators, description="warm", intensity=80, group="Trips")

    assert created.owner_id == "user-1"
    assert (created.name, created.description, created.group_name, created.intensity) == ("My Ember", "warm", "Trips", 80)
    assert created.steps == STEPS
    assert created.schema_version == 1
    assert created.created_at.tzinfo is not None
    assert created.created_at == created.updated_at


def test_group_defaults_to_mine_and_name_is_trimmed(collaborators):
    created = create(collaborators, name="  Padded  ")

    assert created.group_name == "Mine"
    assert created.name == "Padded"


def test_list_returns_only_the_callers_filters_sorted_by_name(collaborators):
    create(collaborators, name="beta")
    create(collaborators, name="Alpha")
    create(collaborators, owner="user-2", name="Theirs")

    assert [f.name for f in operations.list_mine(collaborators, "user-1")] == ["Alpha", "beta"]
    assert [f.name for f in operations.list_mine(collaborators, "user-2")] == ["Theirs"]


def test_a_foreign_id_is_not_found_for_read_update_and_delete(collaborators):
    theirs = create(collaborators, owner="user-2")

    assert code_of(lambda: operations.get_mine(collaborators, "user-1", theirs.id)) == ("filter_not_found", 404)
    assert code_of(
        lambda: operations.update_filter(collaborators, "user-1", theirs.id, UpdateFilterRequest(name="Mine now"))
    ) == ("filter_not_found", 404)
    assert code_of(lambda: operations.delete_filter(collaborators, "user-1", theirs.id)) == ("filter_not_found", 404)
    assert operations.get_mine(collaborators, "user-2", theirs.id).name == "My Ember"


def test_unknown_id_is_not_found(collaborators):
    assert code_of(lambda: operations.delete_filter(collaborators, "user-1", "nope")) == ("filter_not_found", 404)


def test_the_public_prefixed_id_resolves_to_the_same_row(collaborators):
    created = create(collaborators)

    assert operations.get_mine(collaborators, "user-1", f"mine:{created.id}").id == created.id
    operations.delete_filter(collaborators, "user-1", f"mine:{created.id}")
    assert operations.list_mine(collaborators, "user-1") == []


def test_names_are_unique_per_owner_ignoring_case(collaborators):
    create(collaborators, name="Ember")

    assert code_of(lambda: create(collaborators, name="ember")) == ("filter_name_taken", 409)
    assert code_of(lambda: create(collaborators, name="EMBER")) == ("filter_name_taken", 409)
    create(collaborators, owner="user-2", name="ember")


def test_rename_to_a_taken_name_conflicts_but_renaming_to_itself_with_new_case_is_fine(collaborators):
    first = create(collaborators, name="First")
    create(collaborators, name="Second")

    assert code_of(
        lambda: operations.update_filter(collaborators, "user-1", first.id, UpdateFilterRequest(name="second"))
    ) == ("filter_name_taken", 409)
    renamed = operations.update_filter(collaborators, "user-1", first.id, UpdateFilterRequest(name="FIRST"))
    assert renamed.name == "FIRST"


def test_the_hundred_filter_limit_is_enforced_per_owner(collaborators):
    for index in range(MAX_USER_FILTERS):
        collaborators.repository.create(
            UserFilter(id="", owner_id="user-1", name=f"filter {index}", steps=STEPS), MAX_USER_FILTERS
        )

    assert code_of(lambda: create(collaborators, name="one too many")) == ("filter_limit_reached", 409)
    create(collaborators, owner="user-2", name="someone else is fine")
    operations.delete_filter(collaborators, "user-1", operations.list_mine(collaborators, "user-1")[0].id)
    create(collaborators, name="room again")


def test_saving_a_lut_filter_is_refused_with_a_clear_message(collaborators, filters_dir):
    write_filter(filters_dir / "local", "cubic", cube=CUBE_2, license="MIT")

    for extra in ({"lut": "lut.cube"}, {"has_lut": True}, {"source_id": "cubic"}):
        with pytest.raises(FilterError) as caught:
            create(collaborators, **extra)
        assert caught.value.code == "filter_lut_unsupported"
        assert caught.value.status_code == 422
        assert "LUT filters can't be copied yet" in caught.value.message
    assert operations.list_mine(collaborators, "user-1") == []


def test_a_step_only_source_filter_is_fine(collaborators, filters_dir):
    write_filter(filters_dir / "local", "plain")

    assert create(collaborators, source_id="plain").name == "My Ember"


def test_update_also_refuses_luts(collaborators):
    created = create(collaborators)

    assert code_of(
        lambda: operations.update_filter(collaborators, "user-1", created.id, UpdateFilterRequest(has_lut=True))
    ) == ("filter_lut_unsupported", 422)


@pytest.mark.parametrize(
    "steps,fragment",
    [
        ([], "at least one step"),
        ([{"op": "bogus"}], "unknown op 'bogus'"),
        ([{"op": "tone", "contrast": 500}], "outside -100..100"),
        ([{"op": "tone", "oops": 1}], "no param 'oops'"),
        ([{"op": "curves", "master": [[0, 0]]}], "needs 2 to 16 points"),
        ([{"op": "vignette"}, {"op": "tone"}], "colour steps come first"),
        ([{"op": "invert"}] * 65, "65 steps"),
    ],
)
def test_steps_are_validated_by_the_shared_validator(collaborators, steps, fragment):
    with pytest.raises(FilterError) as caught:
        create(collaborators, steps=steps)

    assert caught.value.code == "filter_invalid"
    assert caught.value.status_code == 422
    assert fragment in caught.value.message


def test_name_length_is_capped_at_24_after_trimming(collaborators):
    assert create(collaborators, name="x" * 24).name == "x" * 24
    assert code_of(lambda: create(collaborators, name="x" * 25)) == ("filter_invalid", 422)


def test_update_changes_only_what_it_is_given(collaborators):
    created = create(collaborators, description="old")

    updated = operations.update_filter(collaborators, "user-1", created.id, UpdateFilterRequest(intensity=40))

    assert updated.intensity == 40
    assert (updated.name, updated.description, updated.steps) == ("My Ember", "old", STEPS)
    assert updated.updated_at >= created.updated_at


def test_update_replaces_steps_and_can_clear_the_description(collaborators):
    created = create(collaborators, description="old")

    updated = operations.update_filter(
        collaborators,
        "user-1",
        created.id,
        UpdateFilterRequest(steps=[{"op": "grayscale"}], description=None),
    )

    assert updated.steps == [{"op": "grayscale"}]
    assert updated.description is None


def test_update_validates_steps_too(collaborators):
    created = create(collaborators)

    assert code_of(
        lambda: operations.update_filter(collaborators, "user-1", created.id, UpdateFilterRequest(steps=[{"op": "x"}]))
    ) == ("filter_invalid", 422)


def test_plugin_ops_are_accepted_only_while_their_plugin_is_enabled(connection, filters_dir, tmp_path):
    from src.features.filters.catalog import FilterCatalog
    from src.features.filters.collaborators import FilterCollaborators
    from src.features.filters.repository import UserFilterRepository

    declaring = plugin(
        "demo-pack",
        tmp_path,
        filter_ops=[{"id": "demo-pack.warmth", "label": "W", "kind": "colour", "params": [
            {"id": "amount", "label": "A", "type": "int", "min": 0, "max": 100, "default": 0}]}],
    )
    steps = [{"op": "demo-pack.warmth", "amount": 30}]
    on = FilterCollaborators(UserFilterRepository(), FilterCatalog(str(filters_dir), registry([declaring])))
    off = FilterCollaborators(UserFilterRepository(), FilterCatalog(str(filters_dir), registry([], [declaring])))

    assert create(on, steps=steps).steps == steps
    assert code_of(lambda: create(off, name="Other", steps=steps)) == ("filter_invalid", 422)


def test_deleting_the_user_cascades_to_their_filters(collaborators, connection):
    create(collaborators)
    create(collaborators, owner="user-2", name="Keep")

    connection.execute("DELETE FROM users WHERE id = 'user-1'")
    connection.commit()

    assert operations.list_mine(collaborators, "user-1") == []
    assert [f.name for f in operations.list_mine(collaborators, "user-2")] == ["Keep"]


def test_timestamps_are_utc_iso_strings_in_the_serialised_record(collaborators):
    created = create(collaborators)

    payload = created.to_dict()

    assert payload["created_at"].endswith("+00:00")
    assert payload["updated_at"].endswith("+00:00")
