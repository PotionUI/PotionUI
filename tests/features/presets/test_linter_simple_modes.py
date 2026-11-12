from __future__ import annotations

import pytest

from src.features.presets.linter import PresetLinter

PIPELINE = "pipeline: []\n"
FORM = (
    "name: custom\nfields:\n"
    "  - {name: seed, type: seed, default: -1}\n"
    "  - {name: duration, type: slider, default: 5, configuration: {min: 1, max: 15, step: 1}}\n"
    "  - {name: start_image, type: image}\n"
)


def write_preset(root, caps):
    preset_dir = root / "presets" / "native" / "Foo" / "std"
    preset_dir.mkdir(parents=True)
    (preset_dir / "preset.yml").write_text(
        'schema: 1\nid: "01SIMPLEMODESAAAAAAAAAAAAA"\nname: "Test"\nversion: "1.0.0"\n'
        'category: "video"\nengine: "native"\nmodes:\n  - txt2video\n  - img2video\n  - video\n'
        f"vars:\n  video_director:\n{caps}"
    )
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    for mode in ("txt2video", "img2video", "video"):
        mode_dir = preset_dir / "modes" / mode
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text(PIPELINE)
        (mode_dir / "form.yml").write_text(FORM)
    return preset_dir


def errors(root):
    return [i.message for i in PresetLinter([str(root)]).lint() if i.level in ("error", "warning") and "video_director" in i.message]


GOOD = (
    "    modes: {t2v: {}, i2v: {}}\n"
    "    preset_modes: [video]\n"
    "    simple_modes:\n"
    "      txt2video: {composition: t2v}\n"
    "      img2video: {composition: i2v, first: start_image}\n"
    "    preset_mode_overrides:\n"
    "      txt2video: {modes: {i2v: null}}\n"
    "      img2video: {modes: {t2v: null}}\n"
)


def test_a_wired_table_is_clean(tmp_path):
    write_preset(tmp_path, GOOD)
    assert errors(tmp_path) == []


@pytest.mark.parametrize("caps,fragment", [
    (GOOD.replace("txt2video: {composition: t2v}", "ghost: {composition: t2v}"), "not a mode of this preset"),
    (GOOD.replace("[video]", "[video, txt2video]"), "both a simple mode and a Director editor mode"),
    (GOOD.replace("first: start_image", "first: no_such_field"), "form field 'no_such_field' does not exist"),
    (GOOD.replace("composition: i2v, first: start_image", "composition: flf, first: start_image, last: start_image"), "composition 'flf' is not declared"),
    (GOOD.replace("composition: t2v", "composition: bogus"), "composition must be one of"),
    (GOOD.replace("      txt2video: {modes: {i2v: null}}\n", "      txt2video: {modes: {director: null}}\n"), "leaves ['i2v'] enabled"),
])
def test_a_broken_table_is_reported(tmp_path, caps, fragment):
    write_preset(tmp_path, caps)
    assert any(fragment in message for message in errors(tmp_path)), errors(tmp_path)
