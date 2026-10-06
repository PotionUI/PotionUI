from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.features.filters.plugin_ops import load_op_impl, load_op_impls, plugin_filter_ops, plugin_filter_roots
from src.platform.imaging.filters import ColourOp, SpatialOp
from src.platform.plugins.manifest import PluginManifestSchema

OP = {
    "id": "demo-pack.warmth",
    "label": "Warmth",
    "kind": "colour",
    "params": [{"id": "amount", "label": "Amount", "type": "int", "min": 0, "max": 100, "default": 10}],
    "python": "ops.py:Warmth",
}


def manifest(**extra):
    base = {
        "id": "demo-pack",
        "name": "Demo",
        "version": "1.0.0",
        "description": "d",
        "author": "a",
        "type": "backend-only",
    }
    base.update(extra)
    return PluginManifestSchema.model_validate(base)


def test_filters_root_and_ops_parse_into_dicts():
    parsed = manifest(filters=[{"path": "filters"}], filter_ops=[OP])

    assert [f.model_dump() for f in parsed.filters] == [{"path": "filters"}]
    dumped = parsed.filter_ops[0].model_dump()
    assert dumped["id"] == "demo-pack.warmth" and dumped["python"] == "ops.py:Warmth"
    assert dumped["params"][0]["default"] == 10


def test_defaults_are_empty():
    parsed = manifest()

    assert parsed.filters == [] and parsed.filter_ops == []


@pytest.mark.parametrize("path", ["/abs", "../up", "a/../b", "C:/x", ""])
def test_filters_root_must_stay_inside_the_plugin(path):
    with pytest.raises(ValidationError):
        manifest(filters=[{"path": path}])


def test_op_ids_must_start_with_the_plugin_id():
    with pytest.raises(ValidationError, match="must start with 'demo-pack.'"):
        manifest(filter_ops=[dict(OP, id="other-pack.warmth")])


@pytest.mark.parametrize("op_id", ["warmth", "demo-pack.Warm", "demo-pack.", "tone"])
def test_op_id_shape(op_id):
    with pytest.raises(ValidationError):
        manifest(filter_ops=[dict(OP, id=op_id)])


def test_duplicate_op_ids_are_rejected():
    with pytest.raises(ValidationError, match="declared twice"):
        manifest(filter_ops=[OP, OP])


def test_param_ranges_are_checked():
    bad = dict(OP, params=[{"id": "amount", "label": "A", "min": 10, "max": 5}])
    with pytest.raises(ValidationError, match="max must be >= min"):
        manifest(filter_ops=[bad])
    out_of_range = dict(OP, params=[{"id": "amount", "label": "A", "min": 0, "max": 5, "default": 9}])
    with pytest.raises(ValidationError, match="default must be within"):
        manifest(filter_ops=[out_of_range])


def test_unknown_kind_and_python_path_traversal_are_rejected():
    with pytest.raises(ValidationError):
        manifest(filter_ops=[dict(OP, kind="magic")])
    with pytest.raises(ValidationError):
        manifest(filter_ops=[dict(OP, python="../ops.py:Warmth")])
    with pytest.raises(ValidationError):
        manifest(filter_ops=[dict(OP, python="ops:Warmth")])


def test_the_loader_record_carries_both_sections():
    from src.platform.plugins.loader import PluginManifest

    fields = PluginManifest.__dataclass_fields__

    assert "filters" in fields and "filter_ops" in fields


def test_roots_resolve_inside_the_plugin_only(tmp_path):
    (tmp_path / "filters").mkdir()
    entry = SimpleNamespace(id="demo-pack", plugin_dir=str(tmp_path), filters=[{"path": "filters"}, {"path": "../x"}])

    roots = plugin_filter_roots([entry])

    assert [(r.plugin_id, r.path) for r in roots] == [("demo-pack", (tmp_path / "filters").resolve())]


def test_op_specs_are_built_from_manifest_entries():
    entry = SimpleNamespace(id="demo-pack", filter_ops=[manifest(filter_ops=[OP]).filter_ops[0].model_dump()])

    ops = plugin_filter_ops([entry])

    spec = ops["demo-pack.warmth"]
    assert (spec.kind, spec.source, spec.plugin_id, spec.python) == ("colour", "plugin", "demo-pack", "ops.py:Warmth")
    assert spec.params[0].to_dict()["max"] == 100


PLUGIN_CODE = """
from src.plugin_api.filters import ColourOp, SpatialOp


class Warmth(ColourOp):
    def map(self, rgb, params):
        return rgb


class Scan(SpatialOp):
    def apply(self, image, params, amount):
        return image


class NotAnOp:
    pass
"""


def test_python_counterparts_load_from_the_plugin_directory(tmp_path):
    (tmp_path / "ops.py").write_text(PLUGIN_CODE, encoding="utf-8")

    assert isinstance(load_op_impl(tmp_path, "ops.py:Warmth"), ColourOp)
    assert isinstance(load_op_impl(tmp_path, "ops.py:Scan"), SpatialOp)


def test_a_counterpart_that_is_not_an_op_class_is_refused(tmp_path):
    (tmp_path / "ops.py").write_text(PLUGIN_CODE, encoding="utf-8")

    assert load_op_impl(tmp_path, "ops.py:NotAnOp") is None
    assert load_op_impl(tmp_path, "ops.py:Missing") is None


def test_a_counterpart_outside_the_plugin_is_refused(tmp_path):
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    (tmp_path / "evil.py").write_text(PLUGIN_CODE, encoding="utf-8")

    assert load_op_impl(plugin_dir, "../evil.py:Warmth") is None
    assert load_op_impl(plugin_dir, "ops.py:Warmth") is None


def test_a_counterpart_that_raises_on_import_is_refused(tmp_path):
    (tmp_path / "ops.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    assert load_op_impl(tmp_path, "ops.py:Warmth") is None


def test_impls_load_only_for_the_ops_asked_for(tmp_path):
    (tmp_path / "ops.py").write_text(PLUGIN_CODE, encoding="utf-8")
    entry = SimpleNamespace(
        id="demo-pack",
        plugin_dir=str(tmp_path),
        filter_ops=[
            {"id": "demo-pack.warmth", "python": "ops.py:Warmth"},
            {"id": "demo-pack.scan", "python": "ops.py:Scan"},
            {"id": "demo-pack.editor_only"},
        ],
    )

    impls = load_op_impls([entry], ["demo-pack.warmth", "demo-pack.editor_only"])

    assert list(impls) == ["demo-pack.warmth"]
