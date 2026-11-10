from unittest.mock import Mock, patch

import pytest

from src.features.presets import operations
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.repository import DatabasePresetRepository
from src.features.user_groups.repository import UserGroupRepository
from src.platform.security.user import AccountType, User
from src.platform.util.ids import generate_ulid
from tests.fixtures.seed import seed_user

ASSIGNMENTS = {
    "native/both": (["ann", "bob"], ["artists"]),
    "native/groups-only": ([], ["artists", "editors"]),
    "native/users-only": (["ann"], []),
    "native/untouched": ([], []),
}
UNINSTALLED = "native/not-installed"


def _user(mock_db, name):
    user_id = generate_ulid()
    return seed_user(mock_db, user_id, username=name)


@pytest.fixture
def world(mock_db):
    presets, groups = DatabasePresetRepository(), UserGroupRepository()
    users = {name: _user(mock_db, name) for name in ("ann", "bob")}
    group_ids = {name: groups.create_group(name).id for name in ("artists", "editors")}
    for preset_id, (user_names, group_names) in ASSIGNMENTS.items():
        presets.install_preset(preset_id)
        for name in user_names:
            presets.assign_preset_to_user(preset_id, users[name])
        for name in group_names:
            groups.assign_preset_to_group(group_ids[name], preset_id)
    return presets, groups


def _old_loop(presets, groups):
    installed = {p.preset_id for p in presets.get_all_installed_presets()}
    rows = {}
    for preset_id in installed:
        summary = presets.get_preset_assignment_summary(preset_id)
        rows[preset_id] = (
            summary.get("preset_db_id"),
            summary["total_assignments"],
            groups.get_group_count_for_preset(preset_id),
        )
    return rows


def test_summaries_match_the_per_preset_queries(world):
    presets, groups = world

    summaries = presets.get_installed_preset_summaries()

    assert summaries == _old_loop(presets, groups)
    assert {preset_id: counts[1:] for preset_id, counts in summaries.items()} == {
        "native/both": (2, 1),
        "native/groups-only": (0, 2),
        "native/users-only": (1, 0),
        "native/untouched": (0, 0),
    }


def test_admin_list_carries_the_same_tuples_as_the_per_preset_queries(world):
    presets, groups = world
    expected = _old_loop(presets, groups)
    file_repo = Mock()
    file_repo.list_all_presets.return_value = [{"id": preset_id} for preset_id in [*ASSIGNMENTS, UNINSTALLED]]
    with patch("src.features.presets.collaborators.PresetFormSerializer"):
        collaborators = PresetCollaborators(
            preset_loader=Mock(), preset_processor=Mock(), template_processor=Mock(),
            file_repo=file_repo, db_repo=presets, user_repo=Mock(), group_repo=groups,
            pipeline_builder=Mock(), pipe_catalog=Mock(), plugins=Mock(), settings=Mock(),
        )
    admin = Mock(spec=User)
    admin.id = "admin"
    admin.account_type = AccountType.ADMIN

    with patch.object(presets, "get_preset_assignment_summary", side_effect=AssertionError("per-preset query")), \
         patch.object(groups, "get_group_count_for_preset", side_effect=AssertionError("per-preset query")):
        listed = operations.list_presets(collaborators, admin, include_uninstalled=True)

    by_id = {preset["id"]: preset for preset in listed}
    assert {
        preset_id: (preset["preset_db_id"], preset["assignment_count"], preset["group_count"])
        for preset_id, preset in by_id.items() if preset["installed"]
    } == expected
    assert by_id[UNINSTALLED]["installed"] is False
    assert not {"preset_db_id", "assignment_count", "group_count"} & by_id[UNINSTALLED].keys()


def test_a_mirror_row_has_its_own_assignment_counts(world, mock_db):
    presets, groups = world
    mirror = presets.create_mirror(
        "MIRROR", "native/both", "Anime", None, None, None, None, configuration={}, form_overrides={},
    )
    presets.assign_preset_to_user("MIRROR", _user(mock_db, "cara"))

    summaries = presets.get_installed_preset_summaries()

    assert summaries["MIRROR"] == (mirror.id, 1, 0)
    assert summaries["native/both"][1:] == (2, 1)
    assert [m.preset_id for m in presets.mirrors_of("native/both")] == ["MIRROR"]
