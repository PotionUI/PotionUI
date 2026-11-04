import pytest

from src.features.elixirs.dto import (
    MAX_STORED_ASSETS,
    MAX_STORED_EDGES,
    MAX_STORED_NODES,
)
from src.features.elixirs.limits import (
    LIMIT_CAPS,
    Limits,
    limits_from_settings,
    stored_limits,
)
from src.platform.settings.records import SettingType
from src.platform.settings.repository import SettingRepository


def read_from(values):
    return lambda name: values.get(name)


def test_unset_settings_keep_the_defaults():
    assert limits_from_settings(read_from({})) == Limits()


@pytest.mark.parametrize("raw", ["", "   ", "many", "nan", "0", "-3", "0.4"])
def test_unusable_values_fall_back_to_the_default(raw):
    assert limits_from_settings(read_from({"max_nodes": raw})).max_nodes == Limits().max_nodes


def test_a_fraction_rounds_down():
    assert limits_from_settings(read_from({"max_edges": "12.7"})).max_edges == 12


@pytest.mark.parametrize("raw", ["inf", "-inf", "1e999"])
def test_infinite_values_do_not_break_the_read(raw):
    limits = limits_from_settings(read_from({"max_invocations": raw}))

    assert limits.max_invocations == Limits().max_invocations


def test_values_above_what_an_elixir_can_store_are_capped():
    limits = limits_from_settings(read_from({name: "100000000" for name in LIMIT_CAPS}))

    assert limits.max_nodes == MAX_STORED_NODES
    assert limits.max_edges == MAX_STORED_EDGES
    assert limits.max_branches == MAX_STORED_EDGES
    assert limits.max_media_per_source == MAX_STORED_ASSETS
    assert limits.max_outputs_per_connection == MAX_STORED_ASSETS
    assert limits.max_invocations == LIMIT_CAPS["max_invocations"]


def test_every_limit_has_a_cap():
    assert set(LIMIT_CAPS) == set(Limits.__dataclass_fields__)


def test_stored_limits_read_the_elixir_settings(scratch_db):
    repository = SettingRepository()
    current = stored_limits(repository)

    assert current() == Limits()

    repository.update_setting_value_by_key("elixirs_max_nodes", "7")
    repository.update_setting_value_by_key("elixirs_max_invocations", "1e999")
    repository.update_setting_value_by_key("elixirs_max_edges", "")

    limits = current()

    assert limits.max_nodes == 7
    assert limits.max_invocations == Limits().max_invocations
    assert limits.max_edges == Limits().max_edges


def test_the_seeded_settings_match_the_defaults(scratch_db):
    repository = SettingRepository()

    for name in LIMIT_CAPS:
        setting = repository.get_setting_by_key(f"elixirs_{name}")
        assert setting is not None and setting.type == SettingType.SYSTEM
        assert int(setting.value) == getattr(Limits(), name)
