from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from src.features.fields.section import Section
from src.features.presets.extensions import plugin_preset_extensions, walk_fields
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.schema import FieldSpec
from src.platform.plugins.manifest import PluginManifestSchema

PRESET_ID = "target-preset"

FORM = {
    "name": "custom",
    "fields": [
        {
            "type": "tabs",
            "children": [
                {
                    "type": "tab",
                    "anchor": "main",
                    "label": "Main",
                    "children": [
                        {"name": "steps", "type": "slider", "default": 8, "configuration": {"min": 1, "max": 50, "step": 1}},
                        {"name": "cfg", "type": "slider", "default": 1.0, "configuration": {"min": 1, "max": 9, "step": 0.5}},
                        {
                            "type": "section",
                            "anchor": "sampling",
                            "label": "Sampling",
                            "children": [{"name": "denoise", "type": "checkbox", "default": False}],
                        },
                    ],
                },
                {"type": "tab", "anchor": "refs", "label": "References", "children": [
                    {"name": "references", "type": "image", "configuration": {"multi": True}},
                ]},
            ],
        }
    ],
}

PIPELINE = {
    "pipeline": [
        {"name": "loader", "id": "loader"},
        {"name": "generator", "id": "generator", "input": [["model", "loader", "model"]], "configuration": {"steps": 8}},
        {"name": "gallery", "input": [["images", "generator", "images"]]},
    ]
}


def _registry(manifests):
    return SimpleNamespace(get_enabled_plugins=lambda: list(manifests))


def _write_preset(root: Path, modes=("txt2img",), extra_manifest=None, variant=True) -> Path:
    preset_dir = root / PRESET_ID
    preset_dir.mkdir(parents=True)
    manifest = {
        "schema": 1, "id": PRESET_ID, "name": PRESET_ID, "version": "1.0.0",
        "category": "image", "engine": "native", "modes": list(modes),
    }
    manifest.update(extra_manifest or {})
    (preset_dir / "preset.yml").write_text(yaml.safe_dump(manifest))
    for mode in modes:
        mode_dir = preset_dir / "modes" / mode
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text(yaml.safe_dump(PIPELINE))
        (mode_dir / "form.yml").write_text(yaml.safe_dump(FORM))
        if variant:
            variant_dir = mode_dir / "variants" / "alt"
            variant_dir.mkdir(parents=True)
            (variant_dir / "form.yml").write_text(yaml.safe_dump({**FORM, "name": "alt"}))
    return preset_dir


def _plugin(tmp_path: Path, plugin_id: str, extension: dict, modes=("txt2img",), target=PRESET_ID, extra_files=None):
    plugin_dir = tmp_path / "plugins" / plugin_id
    (plugin_dir / "extensions").mkdir(parents=True)
    (plugin_dir / "extensions" / "ext.yml").write_text(yaml.safe_dump(extension))
    for name, content in (extra_files or {}).items():
        path = plugin_dir / "extensions" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(content))
    return SimpleNamespace(
        id=plugin_id,
        plugin_dir=plugin_dir,
        preset_extensions=[{"target": target, "modes": list(modes), "file": "extensions/ext.yml"}],
    )


def _load(tmp_path: Path, manifests, **preset_kwargs):
    root = tmp_path / "presets"
    if not root.exists():
        _write_preset(root, **preset_kwargs)
    loader = PresetTemplateLoader([str(root)], plugin_registry=_registry(manifests))
    loader.load_presets()
    return loader, loader.get(PRESET_ID)


def _tab(form, anchor):
    return next(f for f, _ in walk_fields(form.fields) if f.type == "tab" and f.anchor == anchor)


def _section(form, anchor):
    return next(f for f, _ in walk_fields(form.fields) if f.type == "section" and f.anchor == anchor)


def _names(fields):
    return [f.name for f in fields]


def _all_errors(loader):
    return " | ".join(f"{key}: {msg}" for key, msgs in loader.load_errors.items() for msg in msgs)


def _field_ext(**group):
    base = {"into": {"tab": "main"}, "fields": [{"name": "strength", "type": "slider", "default": 0.5,
                                                 "configuration": {"min": 0, "max": 1, "step": 0.1}}]}
    base.update(group)
    return {"fields": [base]}


def _assert_rejected(loader, template, plugin_id, fragment):
    errors = _all_errors(loader)
    assert f"plugin '{plugin_id}' preset_extensions" in errors
    assert fragment in errors
    for mode in template.modes.values():
        for form in mode.forms:
            assert not any(f.source_plugin for f, _ in walk_fields(form.fields))
        assert not any(p.source_plugin for p in mode.pipes)


def test_manifest_accepts_preset_extensions():
    schema = PluginManifestSchema.model_validate({
        "id": "p", "name": "P", "version": "1.0.0", "description": "d", "author": "a", "type": "backend-only",
        "preset_extensions": [{"target": "x", "modes": ["txt2img", "edit"], "file": "ext/a.yml"}],
    })
    assert [(e.target, e.modes, e.file) for e in schema.preset_extensions] == [("x", ["txt2img", "edit"], "ext/a.yml")]


@pytest.mark.parametrize("entry", [
    {"target": "x", "modes": ["a"], "file": "../outside.yml"},
    {"target": "x", "modes": ["a"], "file": "/abs.yml"},
    {"target": "x", "modes": [], "file": "a.yml"},
    {"target": "x", "modes": ["a", "a"], "file": "a.yml"},
    {"target": "", "modes": ["a"], "file": "a.yml"},
    {"target": "x", "modes": ["a"], "file": "a.yml", "bogus": 1},
])
def test_manifest_rejects_bad_preset_extension_entries(entry):
    with pytest.raises(ValidationError):
        PluginManifestSchema.model_validate({
            "id": "p", "name": "P", "version": "1.0.0", "description": "d", "author": "a",
            "type": "backend-only", "preset_extensions": [entry],
        })


def test_extensions_ordered_by_plugin_id_then_declaration(tmp_path):
    b = SimpleNamespace(id="b", plugin_dir=tmp_path / "b", preset_extensions=[{"target": "t", "modes": ["m"], "file": "x.yml"}])
    a = SimpleNamespace(id="a", plugin_dir=tmp_path / "a", preset_extensions=[
        {"target": "t2", "modes": ["m"], "file": "y.yml"},
        {"target": "t1", "modes": ["m"], "file": "z.yml"},
    ])
    resolved = plugin_preset_extensions([b, a])
    assert [(e.plugin_id, e.target_preset_id) for e in resolved] == [("a", "t2"), ("a", "t1"), ("b", "t")]
    assert resolved[0].file == (tmp_path / "a" / "y.yml").resolve()


def test_anchor_only_on_tab_and_section():
    FieldSpec.model_validate({"type": "tab", "anchor": "main"})
    FieldSpec.model_validate({"type": "section", "anchor": "sampling"})
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"type": "slider", "name": "x", "anchor": "main"})
    with pytest.raises(ValidationError):
        FieldSpec.model_validate({"type": "tab", "anchor": "Main Tab"})


def test_duplicate_anchor_in_one_form_is_a_load_error(tmp_path):
    root = tmp_path / "presets"
    preset_dir = _write_preset(root, variant=False)
    form = yaml.safe_load((preset_dir / "modes/txt2img/form.yml").read_text())
    form["fields"][0]["children"][1]["anchor"] = "main"
    (preset_dir / "modes/txt2img/form.yml").write_text(yaml.safe_dump(form))
    loader, template = _load(tmp_path, [])
    assert template is None
    assert "tab anchor 'main' is declared more than once" in _all_errors(loader)


def test_fields_append_into_tab_in_every_variant_with_provenance(tmp_path):
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-a", _field_ext())])
    assert loader.load_errors == {}
    forms = template.modes["txt2img"].forms
    assert len(forms) == 2
    for form in forms:
        tab = _tab(form, "main")
        assert _names(tab.children) == ["steps", "cfg", None, "strength"]
        assert tab.children[-1].source_plugin == "ext-a"
        assert tab.source_plugin is None


def test_fields_after_and_before_a_named_field(tmp_path):
    ext = {"fields": [
        {"into": {"tab": "main"}, "after": "steps", "fields": [{"name": "after_steps", "type": "checkbox"}]},
        {"into": {"tab": "main"}, "before": "steps", "fields": [{"name": "before_steps", "type": "checkbox"}]},
    ]}
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-a", ext)])
    assert loader.load_errors == {}
    tab = _tab(template.modes["txt2img"].forms[0], "main")
    assert _names(tab.children) == ["before_steps", "steps", "after_steps", "cfg", None]


def test_fields_into_section_and_after_a_nested_field(tmp_path):
    ext = {"fields": [
        {"into": {"section": "sampling"}, "fields": [{"name": "eta", "type": "checkbox"}]},
        {"into": {"tab": "main"}, "after": "denoise", "fields": [{"name": "nested", "type": "checkbox"}]},
    ]}
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-a", ext)])
    assert loader.load_errors == {}
    section = _section(template.modes["txt2img"].forms[0], "sampling")
    assert _names(section.children) == ["denoise", "nested", "eta"]


def test_fields_from_a_file_with_their_own_reactions(tmp_path):
    fragment = {"fields": [{"name": "mix", "type": "slider", "default": 0.2,
                            "configuration": {"min": 0, "max": 1, "step": 0.1},
                            "reactions": [{"when": {"field": "cfg", "greater_than": 2}, "then": {"set_value": 0.5}}]}]}
    ext = {"fields": [{"into": {"tab": "refs"}, "file": "fields/mix.yml"}]}
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-a", ext, extra_files={"fields/mix.yml": fragment})])
    assert loader.load_errors == {}
    added = _tab(template.modes["txt2img"].forms[0], "refs").children[-1]
    assert added.name == "mix"
    assert added.reactions[0]["then"]["set_value"] == 0.5


def test_serializer_emits_source_plugin(tmp_path):
    ext = {"fields": [{"into": {"tab": "main"}, "fields": [
        {"type": "section", "anchor": "extra", "label": "Extra", "children": [{"name": "x", "type": "checkbox"}]},
    ]}]}
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-a", ext)])
    added = _tab(template.modes["txt2img"].forms[0], "main").children[-1]
    schema = Section(preset_loader=loader).output(added)
    assert schema["source_plugin"] == "ext-a"
    core = Section(preset_loader=loader).output(_section(template.modes["txt2img"].forms[0], "sampling"))
    assert "source_plugin" not in core


def test_pipes_add_and_patch(tmp_path):
    ext = {"pipes": [
        {"add": {"name": "refiner", "id": "refiner", "input": [["images", "generator", "images"]]}, "after": "generator"},
        {"add": {"name": "probe", "id": "probe"}, "before": "loader"},
        {"add": {"name": "notify", "id": "notify"}},
        {"patch": "generator", "input_add": [["extra", "loader", "clip"]], "configuration_add": {"strength": 0.4}},
        {"patch": "gallery", "input_add": [["refined", "refiner", "images"]]},
    ]}
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-a", ext)])
    assert loader.load_errors == {}
    pipes = template.modes["txt2img"].pipes
    assert [p.id or p.name for p in pipes] == ["probe", "loader", "generator", "refiner", "gallery", "notify"]
    generator = pipes[2]
    assert generator.input == [["model", "loader", "model"], ["extra", "loader", "clip"]]
    assert generator.configuration == {"steps": 8, "strength": 0.4}
    assert pipes[4].input[-1] == ["refined", "refiner", "images"]
    assert {p.id for p in pipes if p.source_plugin == "ext-a"} == {"refiner", "probe", "notify"}


def test_prompt_resources_appended_per_mode(tmp_path):
    ext = {"prompt_resources": [{"field": "references", "kind": "image", "token": "<image @>"}]}
    manifest = {"prompt_resources": {"txt2img": [{"field": "references", "kind": "video", "token": "<video @>"}]}}
    loader, template = _load(
        tmp_path, [_plugin(tmp_path, "ext-a", ext, modes=("txt2img", "edit"))],
        modes=("txt2img", "edit"), extra_manifest=manifest,
    )
    assert loader.load_errors == {}
    assert [e["kind"] for e in template.prompt_resources["txt2img"]] == ["video", "image"]
    assert [e["kind"] for e in template.prompt_resources["edit"]] == ["image"]


def test_director_reference_fields_appended(tmp_path):
    director = {"vars": {"video_director": {
        "family": "x", "preset_modes": ["txt2img", "edit"], "reference_fields": ["references"],
        "preset_mode_overrides": {"edit": {"reference_fields": ["steps"]}},
    }}}
    ext = {"fields": [{"into": {"tab": "refs"}, "fields": [{"name": "mods", "type": "image"}]}],
           "director_reference_fields": ["mods"]}
    loader, template = _load(
        tmp_path, [_plugin(tmp_path, "ext-a", ext, modes=("txt2img", "edit"))],
        modes=("txt2img", "edit"), extra_manifest=director,
    )
    assert loader.load_errors == {}
    overrides = template.vars["video_director"]["preset_mode_overrides"]
    assert overrides["txt2img"]["reference_fields"] == ["references", "mods"]
    assert overrides["edit"]["reference_fields"] == ["steps", "mods"]
    assert template.vars["video_director"]["reference_fields"] == ["references"]


def test_extension_targets_a_plugin_contributed_mode(tmp_path):
    contributor_dir = tmp_path / "plugins" / "contributor"
    mode_dir = contributor_dir / "contributed" / "modes" / "restyle"
    mode_dir.mkdir(parents=True)
    (mode_dir / "pipeline.yml").write_text(yaml.safe_dump(PIPELINE))
    (mode_dir / "form.yml").write_text(yaml.safe_dump(FORM))
    contributor = SimpleNamespace(
        id="zz-contributor", plugin_dir=contributor_dir,
        preset_modes=[{"target": PRESET_ID, "modes_root": "contributed"}],
    )
    extender = _plugin(tmp_path, "aa-extender", _field_ext(), modes=("restyle",))
    loader, template = _load(tmp_path, [extender, contributor])
    assert loader.load_errors == {}
    tab = _tab(template.modes["restyle"].forms[0], "main")
    assert tab.children[-1].source_plugin == "aa-extender"


def test_two_plugins_apply_in_plugin_id_order(tmp_path):
    first = _plugin(tmp_path, "aa-first", _field_ext(fields=[{"name": "one", "type": "checkbox"}]))
    second = _plugin(tmp_path, "bb-second", _field_ext(fields=[{"name": "two", "type": "checkbox"}]))
    loader, template = _load(tmp_path, [second, first])
    assert loader.load_errors == {}
    assert _names(_tab(template.modes["txt2img"].forms[0], "main").children)[-2:] == ["one", "two"]


def test_second_plugin_conflicting_with_first_is_rejected(tmp_path):
    first = _plugin(tmp_path, "aa-first", {"pipes": [{"patch": "generator", "configuration_add": {"strength": 1}}]})
    second = _plugin(tmp_path, "bb-second", {"pipes": [{"patch": "generator", "configuration_add": {"strength": 2}}],
                                            **_field_ext()})
    loader, template = _load(tmp_path, [second, first])
    assert template.modes["txt2img"].pipes[1].configuration["strength"] == 1
    errors = _all_errors(loader)
    assert "plugin 'bb-second'" in errors and "already sets ['strength']" in errors
    assert "plugin 'aa-first'" not in errors
    assert "strength" not in _names(_tab(template.modes["txt2img"].forms[0], "main").children)


def test_disabled_plugin_extension_disappears_on_reload(tmp_path):
    manifests = [_plugin(tmp_path, "ext-a", _field_ext())]
    root = tmp_path / "presets"
    _write_preset(root)
    loader = PresetTemplateLoader([str(root)], plugin_registry=SimpleNamespace(get_enabled_plugins=lambda: list(manifests)))
    loader.load_presets()
    assert "strength" in _names(_tab(loader.get(PRESET_ID).modes["txt2img"].forms[0], "main").children)
    manifests.clear()
    loader.reload()
    assert "strength" not in _names(_tab(loader.get(PRESET_ID).modes["txt2img"].forms[0], "main").children)


def test_missing_target_preset_is_absent_not_an_error(tmp_path):
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-a", _field_ext(), target="no-such-preset")])
    assert loader.load_errors == {}
    assert "strength" not in _names(_tab(template.modes["txt2img"].forms[0], "main").children)


@pytest.mark.parametrize("extension,fragment", [
    (_field_ext(into={"tab": "nope"}), "no tab with anchor 'nope'"),
    (_field_ext(into={"section": "main"}), "no section with anchor 'main'"),
    (_field_ext(after="nope"), "no field 'nope' in tab 'main'"),
    (_field_ext(after="references"), "no field 'references' in tab 'main'"),
    (_field_ext(fields=[{"name": "cfg", "type": "checkbox"}]), "field 'cfg' already exists"),
    (_field_ext(fields=[{"name": "d", "type": "checkbox"}, {"name": "d", "type": "checkbox"}]), "field 'd' is added more than once"),
    (_field_ext(fields=[{"type": "section", "anchor": "sampling", "label": "S"}]), "section anchor 'sampling' already exists"),
    (_field_ext(fields=[{"name": "x", "type": "checkbox", "bogus": 1}]), "Extra inputs are not permitted"),
    (_field_ext(fields=[{"name": "x", "type": "no_such_type"}]), "unregistered field type 'no_such_type'"),
    (_field_ext(fields=[{"name": "m", "type": "model", "configuration": {"filter_tags": "@config:nope"}}]), "'@config:nope'"),
    (_field_ext(fields=None, file="fields/missing.yml"), "file 'fields/missing.yml' not found"),
    (_field_ext(fields=None, file="../escape.yml"), "must be a relative path inside the plugin"),
    ({"fields": [{"into": {"tab": "main", "section": "sampling"}, "fields": []}]}, "exactly one of `tab` or `section`"),
    ({"bogus": []}, "Extra inputs are not permitted"),
    ({"pipes": [{"add": {"name": "x", "id": "generator"}}]}, "pipe id 'generator' is already used"),
    ({"pipes": [{"add": {"name": "x", "id": "gallery"}}]}, "pipe id 'gallery' is already used"),
    ({"pipes": [{"add": {"name": "x"}}]}, "needs an `id`"),
    ({"pipes": [{"add": {"name": "generator", "id": "gen2"}}]}, "ambiguous"),
    ({"pipes": [{"add": {"name": "x", "id": "x"}, "after": "nope"}]}, "no pipe 'nope'"),
    ({"pipes": [{"patch": "nope", "configuration_add": {"a": 1}}]}, "no pipe 'nope'"),
    ({"pipes": [{"patch": "generator", "input_add": [["model", "loader", "model"]]}]}, "already sets ['model']"),
    ({"pipes": [{"patch": "generator", "configuration_add": {"steps": 4}}]}, "already sets ['steps']"),
    ({"pipes": [{"patch": "generator"}]}, "adds nothing"),
    ({"pipes": [{"remove": "generator"}]}, "a pipe entry is `add:` or `patch:`"),
    ({"prompt_resources": [{"field": "nope", "kind": "image", "token": "<i @>"}]}, "has no field 'nope'"),
    ({"prompt_resources": [{"field": "references", "kind": "image", "token": "<i @>"},
                           {"field": "references", "kind": "image", "token": "<j @>"}]}, "mapped more than once"),
    ({"director_reference_fields": ["references"]}, "has no Video Director"),
])
def test_rejected_extensions_leave_the_preset_untouched(tmp_path, extension, fragment):
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-bad", extension)])
    _assert_rejected(loader, template, "ext-bad", fragment)


def test_anchor_missing_in_one_variant_rejects_every_variant(tmp_path):
    root = tmp_path / "presets"
    preset_dir = _write_preset(root)
    alt = yaml.safe_load((preset_dir / "modes/txt2img/variants/alt/form.yml").read_text())
    alt["fields"][0]["children"][0]["anchor"] = "other"
    (preset_dir / "modes/txt2img/variants/alt/form.yml").write_text(yaml.safe_dump(alt))
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-bad", _field_ext())])
    _assert_rejected(loader, template, "ext-bad", "form 'alt': no tab with anchor 'main'")


def test_missing_mode_rejects_the_whole_extension(tmp_path):
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-bad", _field_ext(), modes=("txt2img", "nope"))])
    _assert_rejected(loader, template, "ext-bad", "has no mode 'nope'")


def test_missing_extension_file_is_rejected(tmp_path):
    manifest = _plugin(tmp_path, "ext-bad", _field_ext())
    (manifest.plugin_dir / "extensions" / "ext.yml").unlink()
    loader, template = _load(tmp_path, [manifest])
    _assert_rejected(loader, template, "ext-bad", "file 'extensions/ext.yml' not found")


def test_one_bad_part_rejects_every_other_part(tmp_path):
    ext = {**_field_ext(), "pipes": [{"patch": "generator", "configuration_add": {"steps": 2}}],
           "prompt_resources": [{"field": "references", "kind": "image", "token": "<i @>"}]}
    loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-bad", ext)])
    _assert_rejected(loader, template, "ext-bad", "already sets ['steps']")
    assert template.prompt_resources == {}


@pytest.mark.parametrize("director,fragment", [
    ({"family": "x", "preset_modes": ["other"]}, "not a Video Director mode"),
    ({"family": "x", "reference_fields": ["references"]}, "already lists 'references'"),
    ({"family": "x"}, "has no field 'nope'"),
])
def test_director_reference_field_rejections(tmp_path, director, fragment):
    names = ["nope"] if "no field" in fragment else ["references"]
    loader, template = _load(
        tmp_path, [_plugin(tmp_path, "ext-bad", {"director_reference_fields": names})],
        extra_manifest={"vars": {"video_director": director}},
    )
    errors = _all_errors(loader)
    assert "plugin 'ext-bad'" in errors and fragment in errors
    assert template.vars["video_director"] == director
