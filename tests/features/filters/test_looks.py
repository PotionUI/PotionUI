import numpy as np

from src.features.filters.catalog import FilterCatalog
from src.features.filters.looks import FilterLooks
from src.features.filters.records import UserFilter
from src.features.filters.repository import UserFilterRepository
from src.platform.media_tools.effects.looks import look_table
from tests.features.filters.conftest import CUBE_2, plugin, registry, write_filter

WARM_OP = {"id": "glow.warm", "kind": "colour", "python": "ops.py:Warm", "params": []}
DRAFT_OP = {"id": "glow.draft", "kind": "colour", "params": []}
OPS_PY = """import numpy as np
from src.plugin_api.filters import ColourOp


class Warm(ColourOp):
    def map(self, rgb, params):
        out = np.array(rgb, copy=True)
        out[:, 0] = np.clip(out[:, 0] + 0.2, 0, 1)
        return out
"""


def own(user_id, name, steps, intensity=70):
    return UserFilterRepository().create(UserFilter(id="", owner_id=user_id, name=name, steps=steps, intensity=intensity), 50)


def test_shared_looks_resolve_for_anyone_and_your_own_only_for_you(connection, filters_dir):
    write_filter(filters_dir / "marketplace", "noir", cube=CUBE_2, steps=[{"op": "grayscale"}, {"op": "grain", "amount": 20}], intensity=90, license="CC0-1.0")
    looks = FilterLooks(FilterCatalog(str(filters_dir)), UserFilterRepository())
    row = own("user-1", "Punchy", [{"op": "tone", "contrast": 40}])

    shared = looks.resolve("noir", None)
    mine = looks.resolve(f"mine:{row.id}", "user-1")

    assert (shared.id, shared.name, shared.intensity, shared.cube.size) == ("noir", "Noir", 90.0, 2)
    assert (mine.id, mine.name, mine.group, mine.intensity) == (f"mine:{row.id}", "Punchy", "Mine", 70.0)
    assert looks.resolve(f"mine:{row.id}", "user-2") is None
    assert looks.resolve(f"mine:{row.id}", None) is None
    assert looks.resolve("missing", "user-1") is None
    assert [look.id for look in looks.list("user-1")] == ["noir", f"mine:{row.id}"]
    assert [look.id for look in looks.list("user-2")] == ["noir"]
    assert [look.id for look in looks.list(None)] == ["noir"]


def test_a_look_the_server_cannot_render_is_left_out(connection, tmp_path, filters_dir):
    plugin_dir = tmp_path / "glow"
    plugin_dir.mkdir()
    manifest = plugin("glow", plugin_dir, filter_ops=[DRAFT_OP])
    write_filter(filters_dir / "marketplace", "drafty", steps=[{"op": "glow.draft"}])
    write_filter(filters_dir / "marketplace", "plain")
    looks = FilterLooks(FilterCatalog(str(filters_dir), plugin_registry=registry([manifest])), UserFilterRepository())

    assert looks.resolve("drafty", None) is None
    assert [look.id for look in looks.list(None)] == ["plain"]


def test_a_plugin_colour_op_is_loaded_once_and_folded_into_the_table(connection, tmp_path, filters_dir):
    plugin_dir = tmp_path / "glow"
    plugin_dir.mkdir()
    (plugin_dir / "ops.py").write_text(OPS_PY, encoding="utf-8")
    manifest = plugin("glow", plugin_dir, filter_ops=[WARM_OP])
    write_filter(filters_dir / "marketplace", "warmer", steps=[{"op": "glow.warm"}])
    looks = FilterLooks(FilterCatalog(str(filters_dir), plugin_registry=registry([manifest])), UserFilterRepository())

    first = looks.resolve("warmer", None)
    again = looks.resolve("warmer", None)

    assert list(first.impls) == ["glow.warm"] and again.impls["glow.warm"] is first.impls["glow.warm"]
    table = look_table(first)
    assert np.isclose(table.data[0], [0.2, 0, 0], atol=1e-6).all()


def test_a_plugin_op_from_a_disabled_plugin_makes_the_look_unavailable(connection, tmp_path, filters_dir):
    plugin_dir = tmp_path / "glow"
    plugin_dir.mkdir()
    (plugin_dir / "ops.py").write_text(OPS_PY, encoding="utf-8")
    manifest = plugin("glow", plugin_dir, filter_ops=[WARM_OP])
    row = own("user-1", "Warm mine", [{"op": "glow.warm"}])
    looks = FilterLooks(FilterCatalog(str(filters_dir), plugin_registry=registry([], [manifest])), UserFilterRepository())

    assert looks.resolve(f"mine:{row.id}", "user-1") is None
    assert looks.list("user-1") == []


def test_a_plugin_op_whose_code_does_not_load_makes_the_look_unavailable(connection, tmp_path, filters_dir):
    plugin_dir = tmp_path / "glow"
    plugin_dir.mkdir()
    (plugin_dir / "ops.py").write_text("class Warm:\n    pass\n", encoding="utf-8")
    manifest = plugin("glow", plugin_dir, filter_ops=[WARM_OP])
    write_filter(filters_dir / "marketplace", "warmer", steps=[{"op": "glow.warm"}])
    looks = FilterLooks(FilterCatalog(str(filters_dir), plugin_registry=registry([manifest])), UserFilterRepository())

    assert looks.resolve("warmer", None) is None
