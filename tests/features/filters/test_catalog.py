import os
import time

from src.features.filters.catalog import FilterCatalog
from src.features.filters.payload import file_item
from src.features.filters.schema import SOURCE_BUILTIN, SOURCE_LOCAL, SOURCE_PLUGIN
from tests.features.filters.conftest import CUBE_2, REPO_ROOT, plugin, registry, write_filter

WARM_OP = {
    "id": "demo-pack.warmth",
    "label": "Warmth",
    "kind": "colour",
    "params": [{"id": "amount", "label": "Amount", "type": "int", "min": 0, "max": 100, "default": 0}],
    "python": "ops.py:Warmth",
}


def tree(root):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*"))


def test_scans_marketplace_and_local(filters_dir, catalog):
    write_filter(filters_dir / "marketplace", "alpha")
    write_filter(filters_dir / "local", "beta")

    by_id = {f.public_id: f for f in catalog.list_filters()}

    assert set(by_id) == {"alpha", "beta"}
    assert by_id["alpha"].source == SOURCE_BUILTIN
    assert by_id["beta"].source == SOURCE_LOCAL
    assert catalog.load_errors == {}


def test_local_filter_with_the_same_id_overrides_the_marketplace_one(filters_dir, catalog):
    write_filter(filters_dir / "marketplace", "ember", name="Ember", steps=[{"op": "invert"}])
    write_filter(filters_dir / "local", "ember", name="My Ember", steps=[{"op": "grayscale"}])

    [only] = catalog.list_filters()

    assert only.name == "My Ember"
    assert only.source == SOURCE_LOCAL
    assert only.overrides is True
    assert only.steps == [{"op": "grayscale"}]


def test_the_marketplace_one_does_not_claim_to_override_anything(filters_dir, catalog):
    write_filter(filters_dir / "marketplace", "ember")

    assert catalog.list_filters()[0].overrides is False


def test_plugin_filters_get_namespaced_ids_and_cannot_collide(tmp_path, filters_dir):
    plugin_dir = tmp_path / "plug"
    write_filter(plugin_dir / "filters", "ember", name="Plug Ember")
    write_filter(filters_dir / "marketplace", "ember", name="Core Ember")
    catalog = FilterCatalog(str(filters_dir), registry([plugin("demo-pack", plugin_dir, ["filters"])]))

    by_id = {f.public_id: f for f in catalog.list_filters()}

    assert set(by_id) == {"ember", "demo-pack:ember"}
    assert by_id["demo-pack:ember"].source == SOURCE_PLUGIN
    assert by_id["demo-pack:ember"].plugin_id == "demo-pack"
    assert by_id["demo-pack:ember"].id == "ember"
    assert by_id["ember"].name == "Core Ember"


def test_a_disabled_plugins_filters_are_not_scanned(tmp_path, filters_dir):
    plugin_dir = tmp_path / "plug"
    write_filter(plugin_dir / "filters", "ember")
    disabled = plugin("demo-pack", plugin_dir, ["filters"])
    catalog = FilterCatalog(str(filters_dir), registry([], [disabled]))

    assert catalog.list_filters() == []


def test_enabling_a_plugin_picks_its_filters_up_without_a_restart(tmp_path, filters_dir):
    plugin_dir = tmp_path / "plug"
    write_filter(plugin_dir / "filters", "ember")
    enabled = []
    catalog = FilterCatalog(str(filters_dir), registry(enabled, [plugin("demo-pack", plugin_dir, ["filters"])]))
    assert catalog.list_filters() == []

    enabled.append(plugin("demo-pack", plugin_dir, ["filters"]))

    assert [f.public_id for f in catalog.list_filters()] == ["demo-pack:ember"]


def test_plugin_root_escaping_the_plugin_directory_is_ignored(tmp_path, filters_dir):
    outside = tmp_path / "outside"
    write_filter(outside, "sneaky")
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    catalog = FilterCatalog(str(filters_dir), registry([plugin("demo-pack", plugin_dir, ["../outside"])]))

    assert catalog.list_filters() == []


def test_plugin_declared_ops_are_known_and_enabled_only_while_the_plugin_is(tmp_path, filters_dir):
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    declaring = plugin("demo-pack", plugin_dir, filter_ops=[WARM_OP])
    on = FilterCatalog(str(filters_dir), registry([declaring]))
    off = FilterCatalog(str(filters_dir), registry([], [declaring]))

    assert "demo-pack.warmth" in on.enabled_ops()
    assert "demo-pack.warmth" not in off.enabled_ops()
    assert "demo-pack.warmth" in off.known_ops()
    assert on.enabled_ops()["demo-pack.warmth"].plugin_id == "demo-pack"


def test_a_local_filter_using_a_disabled_plugin_op_stays_listed_and_flagged_unavailable(tmp_path, filters_dir):
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    declaring = plugin("demo-pack", plugin_dir, filter_ops=[WARM_OP])
    write_filter(filters_dir / "local", "toasty", steps=[{"op": "demo-pack.warmth", "amount": 20}])
    off = FilterCatalog(str(filters_dir), registry([], [declaring]))
    on = FilterCatalog(str(filters_dir), registry([declaring]))

    [definition] = off.list_filters()
    dimmed = file_item(definition, off.known_ops(), off.enabled_ops())
    [definition_on] = on.list_filters()
    live = file_item(definition_on, on.known_ops(), on.enabled_ops())

    assert dimmed["unavailable_ops"] == ["demo-pack.warmth"]
    assert dimmed["needs_plugin"] == "demo-pack"
    assert dimmed["backend_ok"] is False
    assert live["unavailable_ops"] == []
    assert live["needs_plugin"] is None
    assert live["backend_ok"] is True


def test_a_plugin_op_without_a_python_counterpart_is_editor_only(tmp_path, filters_dir):
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    editor_only = dict(WARM_OP)
    editor_only.pop("python")
    declaring = plugin("demo-pack", plugin_dir, filter_ops=[editor_only])
    write_filter(filters_dir / "local", "toasty", steps=[{"op": "demo-pack.warmth", "amount": 20}])
    catalog = FilterCatalog(str(filters_dir), registry([declaring]))

    [definition] = catalog.list_filters()
    item = file_item(definition, catalog.known_ops(), catalog.enabled_ops())

    assert item["unavailable_ops"] == []
    assert item["backend_ok"] is False


def test_a_bad_filter_lands_in_load_errors_and_the_rest_still_load(filters_dir, catalog):
    write_filter(filters_dir / "marketplace", "good")
    write_filter(filters_dir / "local", "bad-one", steps=[{"op": "foo"}])

    assert [f.public_id for f in catalog.list_filters()] == ["good"]
    assert list(catalog.load_errors) == ["local/bad-one"]
    assert catalog.load_errors["local/bad-one"] == ["steps[0].op: unknown op 'foo'"]


def test_load_error_keys_never_expose_server_paths(filters_dir, catalog):
    write_filter(filters_dir / "local", "bad-one", steps=[{"op": "foo"}])

    for key, messages in catalog.load_errors.items():
        assert str(filters_dir) not in key
        assert all(str(filters_dir) not in message for message in messages)


def test_directory_name_must_equal_the_id(filters_dir, catalog):
    directory = write_filter(filters_dir / "local", "real")
    directory.rename(filters_dir / "local" / "other")

    assert catalog.list_filters() == []
    assert "must equal the filter id" in catalog.load_errors["local/other"][0]


def test_ids_with_a_colon_are_reserved(filters_dir, catalog):
    write_filter(filters_dir / "local", "mine:abc")

    assert catalog.list_filters() == []
    assert "reserved" in catalog.load_errors["local/mine:abc"][0]


def test_oversized_filter_file_is_rejected(filters_dir, catalog):
    directory = write_filter(filters_dir / "local", "big")
    (directory / "filter.yml").write_text("description: " + "x" * 70000, encoding="utf-8")

    assert catalog.list_filters() == []
    assert "limit" in catalog.load_errors["local/big"][0]


def test_unparseable_yaml_is_a_load_error(filters_dir, catalog):
    directory = write_filter(filters_dir / "local", "broken")
    (directory / "filter.yml").write_text("a: [", encoding="utf-8")

    assert catalog.list_filters() == []
    assert "Could not parse YAML" in catalog.load_errors["local/broken"][0] or "could not parse YAML" in catalog.load_errors["local/broken"][0]


def test_lut_filter_reports_its_size_and_a_content_derived_revision(filters_dir, catalog):
    write_filter(filters_dir / "local", "cubic", cube=CUBE_2, license="MIT")

    [definition] = catalog.list_filters()
    item = file_item(definition, catalog.known_ops(), catalog.enabled_ops())

    assert item["has_lut"] is True
    assert item["lut_size"] == 2
    assert item["lut_url"] == "/api/filters/cubic/lut"
    assert len(item["revision"]) == 8
    other = CUBE_2.replace("1 1 1", "1 1 0.5")
    write_filter(filters_dir / "local", "cubic", cube=other, license="MIT")
    assert catalog.list_filters()[0].revision != item["revision"]


def test_lut_filter_needs_a_license(filters_dir, catalog):
    write_filter(filters_dir / "local", "cubic", cube=CUBE_2)

    assert catalog.list_filters() == []
    assert "license" in catalog.load_errors["local/cubic"][0]


def test_missing_and_broken_luts_are_load_errors(filters_dir, catalog):
    write_filter(filters_dir / "local", "missing", license="MIT", lut="lut.cube")
    write_filter(filters_dir / "local", "broken", cube="LUT_3D_SIZE 2\n0 0 0\n", license="MIT")

    assert catalog.list_filters() == []
    assert "does not exist" in catalog.load_errors["local/missing"][0]
    assert "expected 8 data rows" in catalog.load_errors["local/broken"][0]


def test_lut_path_cannot_escape_the_filter_directory(filters_dir, catalog):
    (filters_dir / "secret.cube").write_text(CUBE_2, encoding="utf-8")
    write_filter(filters_dir / "local", "sneaky", license="MIT", lut="../../secret.cube")

    assert catalog.list_filters() == []
    assert "plain file name" in catalog.load_errors["local/sneaky"][0]


def test_changes_on_disk_are_picked_up_on_the_next_call(filters_dir, catalog):
    write_filter(filters_dir / "local", "first")
    assert [f.public_id for f in catalog.list_filters()] == ["first"]

    write_filter(filters_dir / "local", "second")
    assert {f.public_id for f in catalog.list_filters()} == {"first", "second"}

    path = filters_dir / "local" / "first" / "filter.yml"
    path.write_text(path.read_text().replace("First", "Renamed"), encoding="utf-8")
    os.utime(path, ns=(time.time_ns() + 5_000_000_000, time.time_ns() + 5_000_000_000))
    assert {f.name for f in catalog.list_filters()} == {"Renamed", "Second"}


def test_the_catalog_never_writes_to_disk(filters_dir, catalog):
    write_filter(filters_dir / "marketplace", "alpha")
    write_filter(filters_dir / "local", "bad", steps=[{"op": "foo"}])
    before = tree(filters_dir)

    catalog.list_filters()
    catalog.load_errors
    catalog.reload()
    catalog.list_filters()

    assert tree(filters_dir) == before


def test_groups_list_builtin_groups_first_then_the_rest(filters_dir, catalog):
    write_filter(filters_dir / "marketplace", "a", group="Film")
    write_filter(filters_dir / "marketplace", "b", group="Colour")
    write_filter(filters_dir / "local", "c", group="Zebra")

    assert catalog.groups() == ["Colour", "Film", "Zebra"]
    assert catalog.groups(extra=["Mine"]) == ["Colour", "Film", "Zebra", "Mine"]


def test_filters_are_ordered_by_order_then_name(filters_dir, catalog):
    write_filter(filters_dir / "marketplace", "b", name="Bravo", order=5)
    write_filter(filters_dir / "marketplace", "a", name="Alpha", order=5)
    write_filter(filters_dir / "marketplace", "z", name="Zulu", order=1)

    assert [f.public_id for f in catalog.list_filters()] == ["z", "a", "b"]


def test_the_shipped_marketplace_filters_all_load_with_no_errors():
    catalog = FilterCatalog(str(REPO_ROOT / "content" / "filters"))

    ids = {f.public_id for f in catalog.list_filters()}

    assert catalog.load_errors == {}
    assert ids == {"pop", "ember", "honey", "frost", "tidal", "sage", "prism", "matte", "dusk", "reel", "noir", "silver"}
    assert {f.group for f in catalog.list_filters()} == {"Colour", "Film", "Black & white"}
    assert all(f.lut is None for f in catalog.list_filters())
