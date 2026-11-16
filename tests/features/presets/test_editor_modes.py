import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.features.presets.editor_modes import editor_overrides, input_field_names
from src.features.presets.extensions import walk_fields
from src.features.presets.form_overrides import apply_overrides_to_fields
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.overlays import PresetOverlays, RowOverlay, apply_overlays
from src.features.presets.templates import editor_key, find_mode, is_editor_key

MARKETPLACE = Path("content/presets/marketplace")
KREA2 = "Krea2"
SDXL = "SDXL"
KREA2_ID = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"

KREA_EDITOR = {
    "schema_version": 1,
    "task": "edit_region",
    "from": "edit",
    "region": "crop",
    "inputs": {"source": "source_image"},
    "fixed": {"match_source": True, "edit_task": "edit", "fit_mode": "fit", "ref_position": "aligned"},
    "fields": {
        "hide": ["source_image_b", "ref_position_a", "ref_boost_a"],
        "change": {"resolution": {"label": "Detail", "ai_hint": "The pixel budget of the marked area."}},
        "add": [
            {
                "into": {"tab": "advanced"},
                "fields": [
                    {
                        "name": "region_context",
                        "type": "select",
                        "label": "Surroundings",
                        "audience": "advanced",
                        "default": "0.5",
                        "configuration": {"options": [{"label": "Tight", "value": "0.25"}, {"label": "Wide", "value": "1.0"}]},
                    }
                ],
            }
        ],
    },
}

SDXL_INPAINT_EDITOR = {
    "schema_version": 1,
    "task": "edit_region",
    "from": "inpaint",
    "region": "mask",
    "inputs": {"source": "source_image", "mask": "source_image_inpaint_mask"},
}


def _copy_preset(name: str, root: Path) -> Path:
    destination = root / name
    shutil.copytree(MARKETPLACE / name, destination, ignore=shutil.ignore_patterns("public", "tests.yml", "styles.yml"))
    return destination


def _write_editor_mode(preset_dir: Path, name: str, spec, files: dict = None) -> Path:
    directory = preset_dir / "editor_modes" / name
    directory.mkdir(parents=True)
    text = spec if isinstance(spec, str) else yaml.safe_dump(spec, sort_keys=False)
    (directory / "editor_mode.yml").write_text(text)
    for relative, content in (files or {}).items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if isinstance(content, str) else yaml.safe_dump(content, sort_keys=False))
    return directory


def _registry(manifests):
    return SimpleNamespace(get_enabled_plugins=lambda: list(manifests))


def _load(root: Path, manifests=()):
    loader = PresetTemplateLoader([str(root)], plugin_registry=_registry(manifests))
    loader.load_presets()
    return loader


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "presets"
    krea = _copy_preset(KREA2, root)
    sdxl = _copy_preset(SDXL, root)
    return SimpleNamespace(root=root, krea=krea, sdxl=sdxl, tmp=tmp_path)


def _names(fields):
    return [f.name for f, _ in walk_fields(fields) if f.name]


def _field(mode, name):
    return next(f for form in mode.forms for f, _ in walk_fields(form.fields) if f.name == name)


def _krea(loader):
    return loader.get(KREA2_ID)


def _errors_text(loader):
    return " | ".join(f"{key}: {msg}" for key, msgs in loader.load_errors.items() for msg in msgs)


def _editor_errors(loader):
    return {key: msgs for key, msgs in loader.load_errors.items() if "editor_modes" in key}


def test_a_derived_mode_is_the_from_form_minus_fixed_hidden_and_inputs_with_changes_and_additions(tree):
    _write_editor_mode(tree.krea, "edit", KREA_EDITOR)
    loader = _load(tree.root)
    template = _krea(loader)
    assert loader.load_errors == {}
    mode = template.editor_modes["edit"]
    source = template.modes["edit"]

    spec = mode.editor
    owned = set(spec.fixed) | set(spec.hide) | set(input_field_names(spec))
    shown = _names(apply_overrides_to_fields(mode.forms[0].fields, editor_overrides(spec, with_inputs=True)))
    expected = [name for name in _names(source.forms[0].fields) if name not in owned]
    expected.append("region_context")

    assert sorted(shown) == sorted(expected)
    assert "region_context" in shown
    assert not owned & set(shown)
    assert {"match_source", "fit_mode", "source_image_b", "source_image"} <= owned
    assert "region_context" not in _names(source.forms[0].fields)


def test_a_derived_mode_keeps_hidden_and_fixed_fields_in_the_template_for_binding(tree):
    _write_editor_mode(tree.krea, "edit", KREA_EDITOR)
    mode = _krea(_load(tree.root)).editor_modes["edit"]
    names = _names(mode.forms[0].fields)
    assert {"match_source", "fit_mode", "edit_task", "source_image_b", "ref_position_a"} <= set(names)
    overrides = editor_overrides(mode.editor)
    assert overrides["fit_mode"] == {"default": "fit", "visible": False}
    assert overrides["match_source"] == {"default": True, "visible": False}
    assert overrides["ref_position_a"] == {"visible": False}
    assert "source_image" not in overrides
    assert editor_overrides(mode.editor, with_inputs=True)["source_image"] == {"visible": False}


def test_change_rewrites_the_named_attributes_and_leaves_the_from_mode_alone(tree):
    _write_editor_mode(tree.krea, "edit", KREA_EDITOR)
    template = _krea(_load(tree.root))
    changed = _field(template.editor_modes["edit"], "resolution")
    original = _field(template.modes["edit"], "resolution")
    assert changed.label == "Detail"
    assert changed.ai_hint == "The pixel budget of the marked area."
    assert original.label != "Detail"
    assert changed.type == original.type
    assert changed.default == original.default


def test_change_can_set_a_default(tree):
    spec = {
        **KREA_EDITOR,
        "fields": {"change": {"ref_boost": {"default": 3.0}}},
        "fixed": {},
    }
    _write_editor_mode(tree.krea, "edit", spec)
    template = _krea(_load(tree.root))
    assert _field(template.editor_modes["edit"], "ref_boost").default == 3.0
    assert _field(template.modes["edit"], "ref_boost").default != 3.0


def test_the_editor_mode_is_marked_and_the_generation_modes_are_untouched(tree):
    baseline = _krea(_load(tree.root))
    _write_editor_mode(tree.krea, "edit", KREA_EDITOR)
    template = _krea(_load(tree.root))
    assert set(template.modes) == set(baseline.modes)
    assert all(mode.editor is None for mode in template.modes.values())
    mode = template.editor_modes["edit"]
    assert mode.editor.name == "edit"
    assert mode.editor.task == "edit_region"
    assert mode.editor.from_mode == "edit"
    assert mode.editor.region == "crop"
    assert mode.editor.inputs["source"] == "source_image"
    assert mode.editor.size["area"] == 1048576
    assert mode.source_plugin is None
    assert mode.mode_label == (template.modes["edit"].mode_label or "Edit")
    assert [pipe.name for pipe in mode.pipes] == [pipe.name for pipe in template.modes["edit"].pipes]


def test_a_label_in_the_file_names_the_mode(tree):
    _write_editor_mode(tree.krea, "edit", {**KREA_EDITOR, "label": "Edit area", "order": 5})
    mode = _krea(_load(tree.root)).editor_modes["edit"]
    assert mode.mode_label == "Edit area"
    assert mode.editor.order == 5


def test_pipeline_operations_patch_the_derived_pipeline_only(tree):
    spec = {
        **KREA_EDITOR,
        "pipeline": [{"patch": "generator/krea2_edit", "configuration_add": {"region_pad": 8}}],
    }
    _write_editor_mode(tree.krea, "edit", spec)
    loader = _load(tree.root)
    template = _krea(loader)
    assert loader.load_errors == {}
    patched = next(p for p in template.editor_modes["edit"].pipes if p.name == "generator/krea2_edit")
    original = next(p for p in template.modes["edit"].pipes if p.name == "generator/krea2_edit")
    assert patched.configuration["region_pad"] == 8
    assert "region_pad" not in original.configuration


def test_a_pipeline_operation_that_adds_a_pipe_inserts_it(tree):
    spec = {
        **KREA_EDITOR,
        "pipeline": [
            {
                "add": {"name": "gallery", "id": "second_gallery", "input": [["image", "generator/krea2_edit", "image"]]},
                "after": "generator/krea2_edit",
            }
        ],
    }
    _write_editor_mode(tree.krea, "edit", spec)
    template = _krea(_load(tree.root))
    ids = [pipe.id for pipe in template.editor_modes["edit"].pipes]
    assert ids.index("second_gallery") == [p.name for p in template.editor_modes["edit"].pipes].index("generator/krea2_edit") + 1
    assert "second_gallery" not in [pipe.id for pipe in template.modes["edit"].pipes]


def test_a_pipeline_yml_replaces_the_inherited_pipeline(tree):
    pipeline = {"pipeline": [{"name": "gallery", "id": "only_gallery", "input": [["image", "x", "image"]]}]}
    _write_editor_mode(tree.krea, "edit", KREA_EDITOR, {"pipeline.yml": pipeline})
    loader = _load(tree.root)
    template = _krea(loader)
    assert loader.load_errors == {}
    assert [pipe.id for pipe in template.editor_modes["edit"].pipes] == ["only_gallery"]
    assert len(template.modes["edit"].pipes) > 1


def test_pipeline_operations_beside_a_pipeline_yml_are_refused(tree):
    spec = {**KREA_EDITOR, "pipeline": [{"patch": "generator/krea2_edit", "configuration_add": {"region_pad": 8}}]}
    pipeline = {"pipeline": [{"name": "gallery", "id": "only_gallery"}]}
    _write_editor_mode(tree.krea, "edit", spec, {"pipeline.yml": pipeline})
    loader = _load(tree.root)
    assert _krea(loader).editor_modes == {}
    assert "not allowed beside a pipeline.yml" in _errors_text(loader)


def test_a_derived_sdxl_inpaint_mode_with_nothing_else_equals_the_from_mode(tree):
    _write_editor_mode(tree.sdxl, "inpaint", SDXL_INPAINT_EDITOR)
    loader = _load(tree.root)
    sdxl = next(p for p in loader.presets if "inpaint" in p.modes)
    assert loader.load_errors == {}
    mode = sdxl.editor_modes["inpaint"]
    shown = _names(apply_overrides_to_fields(mode.forms[0].fields, editor_overrides(mode.editor)))
    assert shown == _names(sdxl.modes["inpaint"].forms[0].fields)
    assert mode.editor.region == "mask"
    assert mode.editor.inputs["mask"] == "source_image_inpaint_mask"
    assert mode.mode_label == (sdxl.modes["inpaint"].mode_label or "Inpaint")


STANDALONE_FORM = {
    "name": "remove",
    "fields": [
        {
            "type": "tabs",
            "children": [
                {
                    "type": "tab",
                    "anchor": "inpainting",
                    "label": "Remove",
                    "children": "{{ paths.preset }}/modes/inpaint/tabs/inpainting.yml",
                },
                {
                    "type": "tab",
                    "anchor": "advanced",
                    "label": "Advanced",
                    "children": "{{ paths.preset }}/modes/inpaint/tabs/advanced.yml",
                },
            ],
        }
    ],
}


def _standalone_files(tree):
    pipeline = (tree.sdxl / "modes" / "inpaint" / "pipeline.yml").read_text()
    return {"form.yml": STANDALONE_FORM, "pipeline.yml": pipeline}


def test_a_standalone_mode_loads_its_own_form_and_pipeline_with_tab_files_of_a_generation_mode(tree):
    spec = {
        "schema_version": 1,
        "task": "remove_object",
        "label": "Remove",
        "region": "crop",
        "inputs": {"source": "source_image", "mask": "source_image_inpaint_mask"},
        "fixed": {"denoise": 1.0},
    }
    _write_editor_mode(tree.sdxl, "remove", spec, _standalone_files(tree))
    loader = _load(tree.root)
    sdxl = next(p for p in loader.presets if "inpaint" in p.modes)
    assert loader.load_errors == {}
    mode = sdxl.editor_modes["remove"]
    assert mode.editor.from_mode is None
    assert mode.mode_label == "Remove"
    assert mode.editor.fixed == {"denoise": 1.0}
    included = set(_names(sdxl.modes["inpaint"].forms[0].fields))
    own = _names(mode.forms[0].fields)
    assert own and set(own) <= included
    assert [p.name for p in mode.pipes] == [p.name for p in sdxl.modes["inpaint"].pipes]
    assert "remove" not in sdxl.modes


def test_a_standalone_mode_needs_its_form_and_pipeline(tree):
    spec = {"schema_version": 1, "task": "remove_object", "region": "crop", "inputs": {"source": "source_image"}}
    _write_editor_mode(tree.sdxl, "remove", spec)
    loader = _load(tree.root)
    assert "form.yml: file not found" in _errors_text(loader)
    assert "pipeline.yml: file not found" in _errors_text(loader)
    assert next(p for p in loader.presets if "inpaint" in p.modes).editor_modes == {}


def test_a_standalone_mode_refuses_derivation_keys(tree):
    spec = {
        "schema_version": 1,
        "task": "remove_object",
        "region": "crop",
        "inputs": {"source": "source_image"},
        "fields": {"hide": ["seed"]},
    }
    _write_editor_mode(tree.sdxl, "remove", spec, _standalone_files(tree))
    loader = _load(tree.root)
    assert "only valid with `from`" in _errors_text(loader)


def test_a_derived_mode_refuses_a_form_yml(tree):
    _write_editor_mode(tree.sdxl, "inpaint", SDXL_INPAINT_EDITOR, {"form.yml": STANDALONE_FORM})
    loader = _load(tree.root)
    assert "form.yml is not allowed with `from`" in _errors_text(loader)


def test_find_mode_resolves_plain_and_editor_keys(tree):
    _write_editor_mode(tree.krea, "edit", KREA_EDITOR)
    template = _krea(_load(tree.root))
    assert find_mode(template, "edit") is template.modes["edit"]
    assert find_mode(template, "editor:edit") is template.editor_modes["edit"]
    assert find_mode(template, editor_key("edit")) is template.editor_modes["edit"]
    assert find_mode(template, "editor:nope") is None
    assert find_mode(template, "nope") is None
    assert find_mode(template, "") is None
    assert find_mode(template, None) is None
    assert find_mode(None, "edit") is None
    assert is_editor_key("editor:edit") and not is_editor_key("edit") and not is_editor_key(None)


def test_a_plain_key_never_reaches_an_editor_mode_and_an_editor_key_never_a_generation_mode(tree):
    _write_editor_mode(tree.krea, "refine", {**KREA_EDITOR, "from": "edit"})
    template = _krea(_load(tree.root))
    assert find_mode(template, "refine") is None
    assert find_mode(template, "editor:txt2img") is None


BROKEN_CASES = {
    "bad_yaml": "schema_version: 1\ntask: [unclosed\n",
    "not_a_mapping": "- just\n- a list\n",
    "unknown_from": {**KREA_EDITOR, "from": "no_such_mode", "fields": {}},
    "hide_missing_field": {**KREA_EDITOR, "fields": {"hide": ["no_such_field"]}, "fixed": {}},
    "hide_required_without_value": {**KREA_EDITOR, "fields": {"hide": ["edit_lora"]}, "fixed": {}},
    "change_missing_field": {**KREA_EDITOR, "fields": {"change": {"no_such_field": {"label": "x"}}}, "fixed": {}},
    "fixed_missing_field": {**KREA_EDITOR, "fixed": {"no_such_field": 1}, "fields": {}},
    "name_in_two_places": {**KREA_EDITOR, "fixed": {"fit_mode": "fit"}, "fields": {"hide": ["fit_mode"]}},
    "missing_schema_version": {k: v for k, v in KREA_EDITOR.items() if k != "schema_version"},
    "wrong_schema_version": {**KREA_EDITOR, "schema_version": 2},
    "unknown_key": {**KREA_EDITOR, "surprise": True},
    "bad_task": {**KREA_EDITOR, "task": "Edit Region"},
    "bad_region": {**KREA_EDITOR, "region": "sideways"},
    "size_out_of_range": {**KREA_EDITOR, "size": {"area": 10}},
    "add_into_missing_anchor": {
        **KREA_EDITOR,
        "fields": {"add": [{"into": {"tab": "nowhere"}, "fields": [{"name": "x1", "type": "checkbox"}]}]},
    },
    "add_existing_field": {
        **KREA_EDITOR,
        "fields": {"add": [{"into": {"tab": "advanced"}, "fields": [{"name": "cfg", "type": "checkbox"}]}]},
    },
    "pipeline_patch_unknown_pipe": {**KREA_EDITOR, "pipeline": [{"patch": "nothing", "configuration_add": {"a": 1}}]},
}


@pytest.mark.parametrize("case", sorted(BROKEN_CASES))
def test_a_broken_editor_mode_is_skipped_and_reported_while_the_preset_and_a_good_mode_load(tree, case):
    baseline = _krea(_load(tree.root))
    _write_editor_mode(tree.krea, "broken", BROKEN_CASES[case])
    _write_editor_mode(tree.krea, "good", {**KREA_EDITOR, "fixed": {}, "fields": {}})
    loader = _load(tree.root)
    template = _krea(loader)

    assert template is not None
    assert set(template.modes) == set(baseline.modes)
    assert set(template.editor_modes) == {"good"}
    errors = _editor_errors(loader)
    assert len(errors) == 1
    ((key, messages),) = errors.items()
    assert key.endswith("broken")
    assert messages and all(message for message in messages)
    assert not any(key.endswith("good") for key in loader.load_errors)


def test_the_error_names_the_problem(tree):
    _write_editor_mode(tree.krea, "a", BROKEN_CASES["hide_missing_field"])
    _write_editor_mode(tree.krea, "b", BROKEN_CASES["hide_required_without_value"])
    _write_editor_mode(tree.krea, "c", BROKEN_CASES["unknown_from"])
    loader = _load(tree.root)
    by_name = {Path(key).name: " ".join(msgs) for key, msgs in _editor_errors(loader).items()}
    assert "no_such_field" in by_name["a"]
    assert "required" in by_name["b"] and "edit_lora" in by_name["b"] and "fixed" in by_name["b"]
    assert "no_such_mode" in by_name["c"]


def test_a_directory_without_an_editor_mode_file_or_with_variants_or_a_bad_name_is_reported(tree):
    (tree.krea / "editor_modes" / "empty").mkdir(parents=True)
    _write_editor_mode(tree.krea, "with_variants", KREA_EDITOR, {"variants/alt/form.yml": "fields: []\n"})
    _write_editor_mode(tree.krea, "Bad Name", KREA_EDITOR)
    loader = _load(tree.root)
    assert _krea(loader).editor_modes == {}
    text = _errors_text(loader)
    assert "editor_mode.yml: file not found" in text
    assert "variants/ is not allowed" in text
    assert "name must match" in text


def _plugin(tmp, plugin_id, files: dict, target=KREA2_ID, modes_root="contributed"):
    plugin_dir = tmp / "plugins" / plugin_id
    root = plugin_dir / modes_root
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else yaml.safe_dump(content, sort_keys=False))
    return SimpleNamespace(
        id=plugin_id,
        plugin_dir=plugin_dir,
        preset_modes=[{"target": target, "modes_root": modes_root}],
        preset_extensions=[],
    )


def test_a_plugin_contributes_an_editor_mode_to_a_preset_it_does_not_own(tree):
    plugin = _plugin(tree.tmp, "relight-plugin", {"editor_modes/relight/editor_mode.yml": {**KREA_EDITOR, "fixed": {}, "fields": {}}})
    loader = _load(tree.root, [plugin])
    template = _krea(loader)
    assert loader.load_errors == {}
    mode = template.editor_modes["relight"]
    assert mode.source_plugin == "relight-plugin"
    assert mode.editor.from_mode == "edit"
    assert "relight" not in template.modes
    assert template.origin["plugin_id"] is None


def test_a_contributed_editor_mode_may_derive_from_a_contributed_generation_mode(tree):
    files = {
        "modes/extra/pipeline.yml": (tree.krea / "modes" / "edit" / "pipeline.yml").read_text(),
        "modes/extra/form.yml": "fields:\n  - name: strength\n    type: slider\n    default: 1\n    configuration: {min: 0, max: 2, step: 0.1}\n",
        "editor_modes/extra/editor_mode.yml": {
            "schema_version": 1,
            "task": "edit_region",
            "from": "extra",
            "region": "crop",
            "inputs": {"source": "strength"},
        },
    }
    loader = _load(tree.root, [_plugin(tree.tmp, "p", files)])
    template = _krea(loader)
    assert loader.load_errors == {}
    assert template.modes["extra"].source_plugin == "p"
    assert _names(template.editor_modes["extra"].forms[0].fields) == ["strength"]


def test_a_contributed_editor_mode_colliding_with_a_core_one_is_rejected_and_reported(tree):
    _write_editor_mode(tree.krea, "edit", {**KREA_EDITOR, "label": "Core"})
    plugin = _plugin(
        tree.tmp, "p", {"editor_modes/edit/editor_mode.yml": {**KREA_EDITOR, "label": "Plugin", "fixed": {}, "fields": {}}}
    )
    loader = _load(tree.root, [plugin])
    mode = _krea(loader).editor_modes["edit"]
    assert mode.mode_label == "Core"
    assert mode.source_plugin is None
    errors = _editor_errors(loader)
    assert len(errors) == 1
    ((key, messages),) = errors.items()
    assert key.startswith("plugin 'p' preset_modes")
    assert "core editor mode wins" in messages[0]


def test_two_plugins_contributing_the_same_editor_mode_name_first_by_plugin_id_wins(tree):
    mode = {**KREA_EDITOR, "fixed": {}, "fields": {}}
    first = _plugin(tree.tmp, "a-plugin", {"editor_modes/shared/editor_mode.yml": {**mode, "label": "A"}})
    second = _plugin(tree.tmp, "b-plugin", {"editor_modes/shared/editor_mode.yml": {**mode, "label": "B"}})
    loader = _load(tree.root, [second, first])
    built = _krea(loader).editor_modes["shared"]
    assert built.mode_label == "A" and built.source_plugin == "a-plugin"
    assert any("already contributed by plugin 'a-plugin'" in text for msgs in _editor_errors(loader).values() for text in msgs)


def test_a_broken_contributed_editor_mode_is_reported_under_the_plugin_and_the_preset_loads(tree):
    plugin = _plugin(
        tree.tmp, "p", {"editor_modes/bad/editor_mode.yml": {**KREA_EDITOR, "from": "nowhere", "fields": {}}}
    )
    loader = _load(tree.root, [plugin])
    assert _krea(loader).editor_modes == {}
    ((key, messages),) = _editor_errors(loader).items()
    assert key.startswith("plugin 'p' preset_modes (target '%s')" % KREA2_ID)
    assert "nowhere" in messages[0]


def test_a_contribution_with_neither_modes_nor_editor_modes_is_still_an_error(tree):
    plugin = _plugin(tree.tmp, "p", {"README.md": "x"})
    loader = _load(tree.root, [plugin])
    assert any("has no modes/ directory" in msg for msgs in loader.load_errors.values() for msg in msgs)


def test_contributions_to_an_absent_preset_add_nothing(tree):
    plugin = _plugin(
        tree.tmp, "p", {"editor_modes/x/editor_mode.yml": {**KREA_EDITOR, "fixed": {}, "fields": {}}}, target="not-installed"
    )
    loader = _load(tree.root, [plugin])
    assert loader.load_errors == {}
    assert _krea(loader).editor_modes == {}


def test_a_plugin_extension_reaches_the_generation_mode_but_not_a_derived_editor_mode(tree):
    _write_editor_mode(tree.krea, "edit", {**KREA_EDITOR, "fixed": {}, "fields": {}})
    plugin_dir = tree.tmp / "plugins" / "ext-plugin"
    (plugin_dir / "extensions").mkdir(parents=True)
    extension = {
        "fields": [
            {
                "into": {"tab": "advanced"},
                "fields": [{"name": "plugin_only", "type": "checkbox", "default": False}],
            }
        ],
        "pipes": [{"patch": "generator/krea2_edit", "configuration_add": {"plugin_flag": True}}],
    }
    (plugin_dir / "extensions" / "ext.yml").write_text(yaml.safe_dump(extension))
    manifest = SimpleNamespace(
        id="ext-plugin",
        plugin_dir=plugin_dir,
        preset_modes=[],
        preset_extensions=[{"target": KREA2_ID, "modes": ["edit"], "file": "extensions/ext.yml"}],
    )
    loader = _load(tree.root, [manifest])
    template = _krea(loader)
    assert loader.load_errors == {}
    assert "plugin_only" in _names(template.modes["edit"].forms[0].fields)
    editor = template.editor_modes["edit"]
    assert "plugin_only" not in _names(editor.forms[0].fields)
    generator = next(p for p in editor.pipes if p.name == "generator/krea2_edit")
    assert "plugin_flag" not in generator.configuration
    assert not any(p.source_plugin for p in editor.pipes)


def _mirror_inputs(tree):
    _write_editor_mode(tree.krea, "edit", {**KREA_EDITOR, "fixed": {}, "fields": {}})
    spec = {
        "schema_version": 1,
        "task": "remove_object",
        "region": "crop",
        "inputs": {"source": "source_image"},
    }
    files = {
        "form.yml": (tree.krea / "modes" / "enhance" / "form.yml").read_text(),
        "pipeline.yml": (tree.krea / "modes" / "enhance" / "pipeline.yml").read_text(),
    }
    _write_editor_mode(tree.krea, "alone", spec, files)
    loader = _load(tree.root)
    return _krea(loader)


def _mirror(source, offered):
    row = RowOverlay(preset_id="mirror-1", source_preset_id=source.id, offered_modes=tuple(offered))
    result = apply_overlays([source], PresetOverlays(rows={row.preset_id: row}), {})
    return next(p for p in result.presets if p.id == "mirror-1")


def test_a_mirror_keeps_a_derived_editor_mode_only_when_its_from_mode_is_offered_and_a_standalone_one_always(tree):
    source = _mirror_inputs(tree)
    assert set(source.editor_modes) == {"edit", "alone"}

    with_edit = _mirror(source, ["txt2img", "edit"])
    assert set(with_edit.editor_modes) == {"edit", "alone"}

    without_edit = _mirror(source, ["txt2img"])
    assert set(without_edit.editor_modes) == {"alone"}
    assert set(without_edit.modes) == {"txt2img"}
    assert without_edit.editor_modes["alone"] is source.editor_modes["alone"]


def test_a_display_override_keeps_every_editor_mode(tree):
    source = _mirror_inputs(tree)
    result = apply_overlays(
        [source], PresetOverlays(rows={source.id: RowOverlay(preset_id=source.id, display_name="Renamed")}), {}
    )
    assert set(result.presets[0].editor_modes) == {"edit", "alone"}


def test_the_shipped_presets_have_no_editor_modes_and_report_no_editor_errors():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    assert loader.presets
    assert all(preset.editor_modes == {} for preset in loader.presets)
    assert _editor_errors(loader) == {}
    assert all(mode.editor is None for preset in loader.presets for mode in preset.modes.values())


def test_a_preset_without_editor_modes_loads_identically_to_one_with_an_unrelated_directory(tree):
    before = _krea(_load(tree.root))
    (tree.krea / "editor_modes").mkdir()
    after = _krea(_load(tree.root))
    assert after.editor_modes == {}
    assert after.modes == before.modes


def test_fields_a_plugin_adds_to_its_editor_mode_carry_the_plugin_and_those_of_a_core_mode_do_not(tree):
    _write_editor_mode(tree.krea, "core", KREA_EDITOR)
    plugin = _plugin(tree.tmp, "p", {"editor_modes/theirs/editor_mode.yml": {**KREA_EDITOR, "fixed": {}}})
    loader = _load(tree.root, [plugin])
    template = _krea(loader)
    assert loader.load_errors == {}
    theirs = _field(template.editor_modes["theirs"], "region_context")
    core = _field(template.editor_modes["core"], "region_context")
    assert theirs.source_plugin == "p"
    assert core.source_plugin is None
