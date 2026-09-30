from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from src.features.model_layouts.catalog import (
    ModelLayoutCatalog,
    plugin_model_layout_roots,
    read_layout_file,
)
from src.features.model_layouts.schema import MAX_FILE_BYTES
from src.platform.plugins.manifest import PluginManifestSchema
from tests.features.model_layouts.helpers import layout_data, write_layout


class MutableRegistry:
    def __init__(self, enabled=()):
        self.enabled = list(enabled)

    def get_enabled_plugins(self):
        return list(self.enabled)


def _manifest(plugin_dir: Path, plugin_id="some-plugin", path="layouts"):
    return SimpleNamespace(id=plugin_id, plugin_dir=plugin_dir, model_layouts=[{"path": path}])


def test_scans_marketplace_and_local(tmp_path):
    write_layout(tmp_path / "marketplace", "alpha")
    write_layout(tmp_path / "local", "beta")
    catalog = ModelLayoutCatalog(str(tmp_path))
    layouts = catalog.list_layouts()
    assert [(l.id, l.source) for l in layouts] == [("alpha", "marketplace"), ("beta", "local")]
    assert catalog.load_errors == {}
    assert catalog.get_layout("beta").plugin_id is None


def test_missing_directory_yields_empty_catalog(tmp_path):
    catalog = ModelLayoutCatalog(str(tmp_path / "nope"))
    assert catalog.list_layouts() == []
    assert catalog.get_layout("x") is None


def test_marketplace_wins_over_local_duplicate(tmp_path):
    write_layout(tmp_path / "marketplace", "dup", label="Market")
    local = write_layout(tmp_path / "local", "dup", label="Local")
    catalog = ModelLayoutCatalog(str(tmp_path))
    assert catalog.get_layout("dup").label == "Market"
    assert list(catalog.load_errors) == [str(local)]
    assert "Duplicate layout id 'dup'" in catalog.load_errors[str(local)][0]


def test_broken_yaml_is_reported_and_others_still_load(tmp_path):
    root = tmp_path / "marketplace"
    write_layout(root, "good")
    (root / "bad.yml").write_text("id: [unclosed", encoding="utf-8")
    catalog = ModelLayoutCatalog(str(tmp_path))
    assert [l.id for l in catalog.list_layouts()] == ["good"]
    assert "Could not parse YAML" in catalog.load_errors[str(root / "bad.yml")][0]


def test_invalid_layout_is_reported_with_issues(tmp_path):
    root = tmp_path / "marketplace"
    root.mkdir()
    (root / "bad.yml").write_text(yaml.safe_dump(layout_data(id="bad", folders=[])), encoding="utf-8")
    catalog = ModelLayoutCatalog(str(tmp_path))
    assert catalog.list_layouts() == []
    assert any("needs folders" in issue for issue in catalog.load_errors[str(root / "bad.yml")])


def test_file_name_must_equal_id(tmp_path):
    root = tmp_path / "marketplace"
    root.mkdir()
    (root / "other.yml").write_text(yaml.safe_dump(layout_data(id="tool")), encoding="utf-8")
    layout, issues = read_layout_file(root / "other.yml", "marketplace")
    assert layout is None
    assert "must equal the layout id" in issues[0]


def test_oversized_file_is_rejected_without_parsing(tmp_path):
    root = tmp_path / "marketplace"
    root.mkdir()
    big = root / "big.yml"
    big.write_text("x: " + "a" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    layout, issues = read_layout_file(big, "marketplace")
    assert layout is None and "limit" in issues[0]


def test_empty_file_is_reported(tmp_path):
    root = tmp_path / "marketplace"
    root.mkdir()
    (root / "empty.yml").write_text("", encoding="utf-8")
    catalog = ModelLayoutCatalog(str(tmp_path))
    assert str(root / "empty.yml") in catalog.load_errors


def test_non_utf8_file_is_reported_not_raised(tmp_path):
    root = tmp_path / "marketplace"
    root.mkdir()
    (root / "latin.yml").write_bytes(b"label: \xe9\xff\n")
    catalog = ModelLayoutCatalog(str(tmp_path))
    assert str(root / "latin.yml") in catalog.load_errors


def test_reload_picks_up_new_files(tmp_path):
    catalog = ModelLayoutCatalog(str(tmp_path))
    assert catalog.list_layouts() == []
    write_layout(tmp_path / "local", "late")
    assert catalog.list_layouts() == []
    catalog.reload()
    assert [l.id for l in catalog.list_layouts()] == ["late"]


def test_plugin_root_appears_and_disappears_with_enable_state(tmp_path):
    plugin_dir = tmp_path / "plugin"
    write_layout(plugin_dir / "layouts", "from-plugin")
    registry = MutableRegistry()
    catalog = ModelLayoutCatalog(str(tmp_path / "core"), plugin_registry=registry)
    assert catalog.get_layout("from-plugin") is None

    registry.enabled = [_manifest(plugin_dir)]
    catalog.reload()
    layout = catalog.get_layout("from-plugin")
    assert layout.source == "plugin" and layout.plugin_id == "some-plugin"

    registry.enabled = []
    catalog.reload()
    assert catalog.get_layout("from-plugin") is None


def test_plugin_layout_colliding_with_core_reports_error_and_core_wins(tmp_path):
    write_layout(tmp_path / "marketplace", "dup", label="Core")
    plugin_dir = tmp_path / "plugin"
    plugin_file = write_layout(plugin_dir / "layouts", "dup", label="Plugin")
    catalog = ModelLayoutCatalog(str(tmp_path), plugin_registry=MutableRegistry([_manifest(plugin_dir)]))
    assert catalog.get_layout("dup").label == "Core"
    assert list(catalog.load_errors) == [str(plugin_file)]


def test_plugin_root_that_does_not_exist_is_ignored(tmp_path):
    catalog = ModelLayoutCatalog(
        str(tmp_path), plugin_registry=MutableRegistry([_manifest(tmp_path / "gone")])
    )
    assert catalog.list_layouts() == []


def test_plugin_model_layout_roots_resolve_against_plugin_dir():
    manifest = _manifest(Path("content/plugins/marketplace/some-plugin"))
    roots = plugin_model_layout_roots([manifest])
    assert [(r.plugin_id, r.path) for r in roots] == [
        ("some-plugin", Path("content/plugins/marketplace/some-plugin/layouts").resolve())
    ]


def test_manifest_without_layouts_contributes_no_roots():
    manifest = SimpleNamespace(id="p", model_layouts=[], plugin_dir=Path("x"))
    assert plugin_model_layout_roots([manifest]) == []
    assert plugin_model_layout_roots([SimpleNamespace(id="p", plugin_dir=Path("x"))]) == []


def test_manifest_schema_accepts_model_layouts_root():
    schema = PluginManifestSchema.model_validate({
        "id": "p", "name": "P", "version": "1.0.0", "description": "d",
        "author": "a", "type": "backend-only",
        "model_layouts": [{"path": "layouts"}],
    })
    assert [r.path for r in schema.model_layouts] == ["layouts"]


def test_manifest_schema_rejects_unknown_key_in_root():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PluginManifestSchema.model_validate({
            "id": "p", "name": "P", "version": "1.0.0", "description": "d",
            "author": "a", "type": "backend-only",
            "model_layouts": [{"path": "layouts", "extra": 1}],
        })


@pytest.mark.parametrize("bad", ["/abs", "../up", "a/../../up", "C:/x", "a\\..\\b", ""])
def test_manifest_rejects_escaping_layout_root(bad):
    with pytest.raises(ValidationError):
        PluginManifestSchema.model_validate({
            "id": "p", "name": "P", "version": "1.0.0", "description": "d",
            "author": "a", "type": "backend-only",
            "model_layouts": [{"path": bad}],
        })


def test_escaping_root_from_a_manifest_object_is_skipped(tmp_path):
    outside = tmp_path / "outside"
    write_layout(outside, "evil")
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    for path in ("../outside", str(outside)):
        manifest = SimpleNamespace(id="p", plugin_dir=plugin_dir, model_layouts=[{"path": path}])
        assert plugin_model_layout_roots([manifest]) == []
        catalog = ModelLayoutCatalog(str(tmp_path / "core"), plugin_registry=MutableRegistry([manifest]))
        assert catalog.get_layout("evil") is None


def test_symlink_escaping_the_plugin_dir_is_skipped(tmp_path):
    outside = tmp_path / "outside"
    write_layout(outside, "evil")
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    try:
        (plugin_dir / "layouts").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    manifest = SimpleNamespace(id="p", plugin_dir=plugin_dir, model_layouts=[{"path": "layouts"}])
    assert plugin_model_layout_roots([manifest]) == []
