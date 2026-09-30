import pytest

from src.features.model_layouts.schema import (
    READER_KINDS,
    ModelLayoutError,
    parse_layout,
    validate_layout_dict,
)
from tests.features.model_layouts.helpers import layout_data


def _issues(**overrides):
    return validate_layout_dict(layout_data(**overrides))


def _has(issues, needle):
    return any(needle in issue for issue in issues)


def test_valid_layout_has_no_issues():
    assert validate_layout_dict(layout_data()) == []


def test_top_level_must_be_mapping():
    assert _has(validate_layout_dict(["x"]), "mapping")


@pytest.mark.parametrize("bad", [2, "1", None, True])
def test_schema_version_must_be_1(bad):
    data = layout_data()
    data["schema"] = bad
    assert _has(validate_layout_dict(data), "schema")


@pytest.mark.parametrize("bad", ["", "Tool", "-tool", "a" * 41, "has space", "under_score", 5])
def test_id_pattern(bad):
    assert _has(_issues(id=bad), "id")


def test_id_generic_is_reserved():
    assert _has(_issues(id="generic"), "reserved")


def test_unknown_top_level_field_rejected():
    assert _has(_issues(bogus=1), "bogus: unknown field")


@pytest.mark.parametrize("bad", ["", "  ", 3])
def test_label_required(bad):
    assert _has(_issues(label=bad), "label")


@pytest.mark.parametrize("key,bad", [
    ("priority", -1), ("priority", 101), ("priority", "x"), ("priority", True),
    ("min_marker_score", 0), ("min_folder_evidence", 0),
])
def test_integer_bounds(key, bad):
    assert _has(_issues(**{key: bad}), key)


def test_match_on_folders_must_be_bool():
    assert _has(_issues(match_on_folders="yes"), "match_on_folders")


@pytest.mark.parametrize("bad_path", [
    "/abs/path", "C:/win", "c:models", "back\\slash", "a/../b", "..", "a//b", "a/./b",
    "", "NUL", "a/con.txt", "a/b?", "a/b*", "a/b:c", 'a/"q"', "a/b|c", "trailing.", "trail /x",
    "e\u0301",
])
def test_folder_path_rules(bad_path):
    folders = [{"path": bad_path, "model_type": "lora"}]
    assert _has(_issues(folders=folders), "folders[0].path")


def test_folder_path_dot_rejected():
    assert _has(_issues(folders=[{"path": ".", "model_type": "lora"}]), "folders[0].path")


def test_dotted_marker_segment_allowed():
    markers = [{"path": "Data/.sm-portable", "kind": "file"}]
    assert _issues(markers=markers) == []


def test_models_root_and_install_dirs_allow_dot():
    assert _issues(models_root=["."], install_dirs=["."]) == []


@pytest.mark.parametrize("key", ["models_root", "install_dirs"])
@pytest.mark.parametrize("bad", [[], "models", ["../x"], ["a", "A"]])
def test_root_lists_rules(key, bad):
    assert _has(_issues(**{key: bad}), key)


def test_marker_rules():
    assert _has(_issues(markers=[{"path": "x", "kind": "socket"}]), "markers[0].kind")
    assert _has(_issues(markers=[{"path": "x", "weight": 0}]), "markers[0].weight")
    assert _has(_issues(markers=[{"path": "x", "weight": 11}]), "markers[0].weight")
    assert _has(_issues(markers=[{"path": "x", "extra": 1}]), "markers[0].extra")
    assert _has(_issues(markers=[{"path": "x"}, {"path": "X"}]), "duplicate marker")
    assert _has(_issues(markers="x"), "markers")
    assert _has(_issues(markers=["x"]), "markers[0]")


def test_unknown_model_type():
    assert _has(_issues(folders=[{"path": "x", "model_type": "banana"}]), "folders[0].model_type")


def test_scan_headers_only_for_header_types():
    ok = [{"path": "x", "model_type": "diffusion_model", "scan_headers": True}]
    assert _issues(folders=ok) == []
    bad = [{"path": "x", "model_type": "lora", "scan_headers": True}]
    assert _has(_issues(folders=bad), "folders[0].scan_headers")
    off = [{"path": "x", "model_type": "lora", "scan_headers": False}]
    assert _issues(folders=off) == []


def test_two_writers_for_a_type_rejected():
    folders = [
        {"path": "a", "model_type": "lora", "write": True},
        {"path": "b", "model_type": "lora", "write": True},
    ]
    assert _has(_issues(folders=folders), "more than one 'write: true'")


def test_writers_for_different_types_allowed():
    folders = [
        {"path": "a", "model_type": "lora", "write": True},
        {"path": "b", "model_type": "vae", "write": True},
    ]
    assert _issues(folders=folders) == []


def test_casefold_duplicate_folders_rejected():
    folders = [{"path": "VAE", "model_type": "vae"}, {"path": "vae", "model_type": "vae"}]
    assert _has(_issues(folders=folders), "collides")


def test_nested_folders_rejected_in_both_orders():
    child_last = [{"path": "a", "model_type": "lora"}, {"path": "a/b", "model_type": "vae"}]
    child_first = [{"path": "a/b", "model_type": "vae"}, {"path": "a", "model_type": "lora"}]
    assert _has(_issues(folders=child_last), "nested inside")
    assert _has(_issues(folders=child_first), "nested inside")


def test_sibling_folders_sharing_a_prefix_are_not_nested():
    folders = [{"path": "ultralytics/bbox", "model_type": "detection_bbox"},
               {"path": "ultralytics/segm", "model_type": "detection_segm"},
               {"path": "lora", "model_type": "lora"}, {"path": "lora2", "model_type": "lora"}]
    assert _issues(folders=folders) == []


def test_folders_on_different_bases_do_not_collide():
    folders = [{"path": "embeddings", "model_type": "embedding", "base": "install"},
               {"path": "embeddings", "model_type": "embedding"}]
    assert _issues(folders=folders) == []


def test_folder_field_rules():
    assert _has(_issues(folders=[{"path": "x", "model_type": "lora", "base": "cwd"}]), "folders[0].base")
    assert _has(_issues(folders=[{"path": "x", "model_type": "lora", "weight": 11}]), "folders[0].weight")
    assert _has(_issues(folders=[{"path": "x", "model_type": "lora", "write": "y"}]), "folders[0].write")
    assert _has(_issues(folders=[{"path": "x", "model_type": "lora", "key": ""}]), "folders[0].key")
    assert _has(_issues(folders=[{"path": "x", "model_type": "lora", "nope": 1}]), "folders[0].nope")
    assert _has(_issues(folders=["x"]), "folders[0]")
    assert _has(_issues(folders="x"), "folders")


def test_reader_kind_must_be_known():
    assert _has(_issues(config_readers=[{"kind": "nope"}]), "config_readers[0].kind")
    assert _has(_issues(config_readers=[{"kind": "comfyui_extra_model_paths", "file": "../x"}]), "config_readers[0].file")
    assert _has(_issues(config_readers=["x"]), "config_readers[0]")


@pytest.mark.parametrize("kind", sorted(READER_KINDS))
def test_every_known_reader_kind_validates(kind):
    assert _issues(config_readers=[{"kind": kind}]) == []


def test_variants_rules():
    ok = [{"label": "Forge", "markers": [{"path": "modules_forge", "kind": "dir"}]}]
    assert _issues(variants=ok) == []
    assert _has(_issues(variants=[{"label": "F", "markers": []}]), "variants[0].markers")
    assert _has(_issues(variants=[{"label": "", "markers": [{"path": "x"}]}]), "variants[0].label")
    assert _has(_issues(variants=["x"]), "variants[0]")


def test_sources_rules():
    assert _issues(sources=[{"title": "t", "url": "https://x"}]) == []
    assert _has(_issues(sources=[{"title": "t"}]), "sources[0].url")
    assert _has(_issues(sources=["x"]), "sources[0]")


def test_layout_needs_folders_and_markers():
    assert _has(_issues(folders=None), "needs folders")
    assert _has(_issues(folders=[]), "needs folders")
    assert _has(_issues(markers=None), "needs markers")
    assert _has(_issues(markers=[]), "needs markers")


def test_delegate_layout_needs_no_folders_or_markers():
    data = layout_data(folders=None, markers=None, delegate={"search": ["api/*/app", "*"], "max_candidates": 8})
    assert validate_layout_dict(data) == []


def test_delegate_rejected_alongside_folders():
    assert _has(_issues(delegate={"search": ["*"]}), "only allowed on a layout without folders")


def test_delegate_rules():
    base = dict(folders=None)
    assert _has(_issues(delegate={"search": []}, **base), "delegate.search")
    assert _has(_issues(delegate={"search": ["../x"]}, **base), "delegate.search[0]")
    assert _has(_issues(delegate={"search": ["/abs"]}, **base), "delegate.search[0]")
    assert _has(_issues(delegate={"search": ["*"], "max_candidates": 0}, **base), "delegate.max_candidates")
    assert _has(_issues(delegate={"search": ["*"], "zzz": 1}, **base), "delegate.zzz")
    assert _has(_issues(delegate="x", **base), "delegate")


def test_parse_applies_defaults():
    layout = parse_layout(layout_data(), source_path="/x/tool.yml")
    assert layout.id == "tool" and layout.source_path == "/x/tool.yml"
    by_path = {f.path: f for f in layout.folders}
    assert by_path["checkpoints"].key == "checkpoints"
    assert by_path["checkpoints"].scan_headers is True
    assert by_path["loras"].scan_headers is None
    assert by_path["LyCORIS"].label == "LyCORIS"
    assert by_path["LyCORIS"].base == "models" and by_path["LyCORIS"].weight == 1
    assert layout.min_folder_evidence == 3 and layout.match_on_folders is True


def test_parse_minimal_layout_uses_defaults():
    data = layout_data(priority=None, install_dirs=None, min_marker_score=None, models_root=None,
                       config_readers=None)
    layout = parse_layout(data)
    assert layout.priority == 50
    assert layout.install_dirs == (".",)
    assert layout.models_root == ("models",)
    assert layout.min_marker_score == 3
    assert layout.config_readers == ()


def test_parse_nested_label_defaults_to_last_segment():
    data = layout_data(folders=[{"path": "ultralytics/bbox", "model_type": "detection_bbox"}])
    assert parse_layout(data).folders[0].label == "bbox"


def test_parse_rejects_invalid_data():
    with pytest.raises(ModelLayoutError):
        parse_layout(layout_data(id="generic"))
