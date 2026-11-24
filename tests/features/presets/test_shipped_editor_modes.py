import json
from pathlib import Path

import pytest
import yaml

from scripts.preset_render import EDITOR_FORMS_DIR, editor_form_record, golden_filename
from src.features.presets.extensions import walk_fields
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.templates import find_mode
from tests.features.presets.editor_mode_trees import KREA2_ID, KREA_EDITOR, copy_preset, write_editor_mode

ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = ROOT / "content" / "presets" / "marketplace"

NINE = {
    ("Flux3", "edit"): ("edit", "crop"),
    ("Krea2", "edit"): ("edit", "crop"),
    ("MOSS-Image", "inpaint"): ("inpaint", "mask"),
    ("MiniMax-H3-Image", "edit"): ("edit", "crop"),
    ("QwenImage-2.1", "edit"): ("edit", "crop"),
    ("QwenImage-2.1", "inpaint"): ("inpaint", "mask"),
    ("QwenImage", "edit"): ("edit", "crop"),
    ("SDXL", "inpaint"): ("inpaint", "mask"),
    ("ZImage", "inpaint"): ("inpaint", "mask"),
    ("MOSS-Image", "expand"): ("inpaint", "mask"),
    ("QwenImage-2.1", "expand"): ("inpaint", "mask"),
    ("SDXL", "expand"): ("inpaint", "mask"),
    ("Krea2", "expand"): ("inpaint", "mask"),
    ("Krea2", "fix_faces"): ("refine_faces", "crop"),
    ("Krea2", "upscale"): ("upscale", "whole"),
    ("Anima", "fix_faces"): ("refine_faces", "crop"),
    ("Anima", "upscale"): ("upscale", "whole"),
    ("ZImage", "expand"): ("inpaint", "mask"),
    ("ZImage", "fix_faces"): ("refine_faces", "crop"),
    ("ZImage", "upscale"): ("upscale", "whole"),
    ("QwenImage", "expand"): ("inpaint", "mask"),
    ("QwenImage", "fix_faces"): ("refine_faces", "crop"),
    ("QwenImage", "upscale"): ("upscale", "whole"),
    ("QwenImage-2.1", "fix_faces"): ("refine_faces", "crop"),
    ("QwenImage-2.1", "upscale"): ("upscale", "whole"),
    ("MOSS-Image", "fix_faces"): ("refine_faces", "crop"),
    ("MOSS-Image", "upscale"): ("upscale", "whole"),
    ("Flux1", "expand"): ("inpaint", "mask"),
    ("Flux1", "fix_faces"): ("refine_faces", "crop"),
    ("Flux1", "upscale"): ("upscale", "whole"),
    ("Flux2", "expand"): ("inpaint", "mask"),
    ("Flux2", "fix_faces"): ("refine_faces", "crop"),
    ("Flux2", "upscale"): ("upscale", "whole"),
    ("SDXL", "fix_faces"): ("refine_faces", "crop"),
    ("SDXL", "upscale"): ("upscale", "whole"),
    ("SeedVR2", "upscale"): ("upscale", "whole"),
}

ONE_CLICK = {
    (folder, task)
    for folder in ("ZImage", "QwenImage-2.1", "QwenImage", "Krea2")
    for task in ("remove_object", "replace_background")
} | {("ImageTools", "find_subject")}


@pytest.fixture(scope="module")
def loader():
    loader = PresetTemplateLoader([str(MARKETPLACE)])
    loader.load_presets()
    return loader


def template_of(loader, directory):
    resolved = (MARKETPLACE / directory).resolve()
    return next(t for t in loader.presets if Path(t.path).resolve() == resolved)


def test_every_shipped_preset_loads_without_errors(loader):
    assert loader.load_errors == {}


def test_exactly_the_nine_editor_modes_and_the_one_click_ones_ship(loader):
    shipped = {
        (Path(t.path).relative_to(MARKETPLACE).parts[0], name)
        for t in loader.presets
        for name, mode in t.editor_modes.items()
        if mode.editor.region != "range"
    }
    assert shipped == set(NINE) | ONE_CLICK


@pytest.mark.parametrize("directory, name", sorted(NINE))
def test_each_editor_mode_resolves_through_find_mode(loader, directory, name):
    template = template_of(loader, directory)
    mode = find_mode(template, f"editor:{name}")
    from_mode, region = NINE[(directory, name)]
    assert mode is not None
    assert mode.editor.from_mode == from_mode
    assert mode.editor.region == region
    assert mode.editor.task == (name if name in ("expand", "fix_faces", "upscale") else "edit_region")
    assert from_mode in template.modes


def test_krea2_no_longer_shows_the_fields_that_break_an_edit(loader):
    template = template_of(loader, "Krea2")
    mode = template.editor_modes["edit"]
    shown = {item["name"] for item in editor_form_record(template, "edit")["shown"]}
    assert {"edit_task", "fit_mode", "ref_position", "ref_position_a", "ref_boost_a"} & shown == set()
    assert mode.editor.fixed["edit_task"] == "edit"
    assert mode.editor.fixed["fit_mode"] == "fit"
    assert mode.editor.fixed["ref_position"] == "aligned"
    assert "resolution" in shown


def test_qwen_image_21_edit_hands_over_a_crop_and_inpaint_keeps_its_mask(loader):
    template = template_of(loader, "QwenImage-2.1")
    assert template.editor_modes["edit"].editor.region == "crop"
    assert template.editor_modes["edit"].editor.inputs["mask"] is None
    assert template.editor_modes["inpaint"].editor.region == "mask"
    assert template.editor_modes["inpaint"].editor.inputs["mask"] == "source_image_inpaint_mask"


def test_flux3_gains_the_surroundings_field_and_the_others_do_not(loader):
    template = template_of(loader, "Flux3")
    shown = {item["name"] for item in editor_form_record(template, "edit")["shown"]}
    generation = {f.name for form in template.modes["edit"].forms for f, _ in walk_fields(form.fields) if f.name}
    assert "region_context" in shown
    assert "region_context" not in generation


def shown_names(record):
    return [item["name"] for item in record["shown"]]


def diff_message(expected, actual):
    old, new = shown_names(expected), shown_names(actual)
    lines = [f"{actual['preset_name']} ({actual['preset_id']}) editor mode '{actual['editor_mode']}':"]
    lines += [f"  + now shown: {name}" for name in new if name not in old]
    lines += [f"  - no longer shown: {name}" for name in old if name not in new]
    if not lines[1:]:
        lines.append("  same fields, a type, label or order changed")
    lines.append(
        "If this is intended, regenerate with:\n"
        "  PYTHONPATH=./venv/lib/python3.12/site-packages:. python scripts/preset_render.py --golden-all "
        "--root content/presets/marketplace"
    )
    return "\n".join(lines)


def all_records(loader):
    return [
        (template, name, editor_form_record(template, name))
        for template in sorted(loader.presets, key=lambda t: t.id)
        for name in sorted(template.editor_modes)
    ]


def test_every_editor_mode_has_a_shown_fields_golden_and_none_is_stale(loader):
    expected = {golden_filename(template, f"editor:{name}") for template, name, _ in all_records(loader)}
    actual = {path.name for path in EDITOR_FORMS_DIR.glob("*.json")}
    assert expected == actual


def test_the_shown_fields_of_each_editor_mode_match_its_golden(loader):
    mismatches = []
    for template, name, record in all_records(loader):
        path = EDITOR_FORMS_DIR / golden_filename(template, f"editor:{name}")
        expected = json.loads(path.read_text(encoding="utf-8"))
        if expected != record:
            mismatches.append(diff_message(expected, record))
    assert not mismatches, "\n\n".join(mismatches)


def test_a_field_added_to_the_from_mode_shows_up_in_the_golden_diff(tmp_path):
    root = tmp_path / "presets"
    krea = copy_preset("Krea2", root)
    write_editor_mode(krea, "edit", KREA_EDITOR)

    def record():
        loader = PresetTemplateLoader([str(root)])
        loader.load_presets()
        template = loader.get(KREA2_ID)
        return editor_form_record(template, "edit")

    before = record()
    tab = krea / "modes" / "edit" / "tabs" / "advanced.yml"
    document = yaml.safe_load(tab.read_text(encoding="utf-8"))
    document["fields"][0]["children"].append({"name": "brand_new_field", "type": "checkbox", "default": False})
    tab.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    after = record()
    assert before != after
    message = diff_message(before, after)
    assert "+ now shown: brand_new_field" in message
    assert "--golden-all" in message
