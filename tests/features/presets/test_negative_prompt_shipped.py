import itertools
from pathlib import Path

import pytest

from scripts.preset_render import (
    ROOT,
    build_fixture_form_data,
    build_generation_data,
    build_processor,
    load_all_presets,
)
from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets.negative_prompt import negative_applies_when

EXPECTED = {
    ("Krea2", "txt2img"), ("Krea2", "enhance"),
    ("Krea2", "edit"), ("Krea2", "upscale"),
    ("LTX-2", "video"), ("LTX-2", "upscale"), ("LTX-2", "motion_transfer"), ("LTX-2", "follow_motion"),
    ("LTX-2.5", "txt2video"), ("LTX-2.5", "img2video"), ("LTX-2.5", "first_last"),
    ("LTX-2.5", "video"), ("LTX-2.5", "upscale"), ("LTX-2.5", "restore"), ("LTX-2.5", "colorize"),
    ("LTX-2.5", "add_water"), ("LTX-2.5", "remove_object"), ("LTX-2.5", "layout_to_render"), ("LTX-2.5", "motion_transfer"), ("LTX-2.5", "extend"), ("LTX-2.5", "refine_faces"),
    ("LTX-2.5", "day_to_night"), ("LTX-2.5", "cinemagraph"), ("LTX-2.5", "transition"), ("LTX-2.5", "follow_motion"), ("LTX-2.5", "ingredients"),
    ("ZImage", "txt2img"), ("Wan", "video"),
    ("ZImage", "enhance"), ("ZImage", "control"), ("ZImage", "inpaint"),
    ("QwenImage-2.1", "inpaint"), ("MOSS-Image", "inpaint"),
    ("QwenImage", "txt2img"), ("QwenImage", "img2img"), ("QwenImage", "edit"), ("QwenImage", "camera"), ("QwenImage", "real"),
    ("QwenImage-2.1", "txt2img"), ("QwenImage-2.1", "edit"), ("QwenImage-2.1", "combine"), ("QwenImage-2.1", "text"), ("QwenImage-2.1", "restyle"), ("QwenImage-2.1", "control"),
    ("Anima", "txt2img"),
    ("Flux1", "txt2img"), ("Flux1", "img2img"), ("Flux2", "txt2img"), ("Flux2", "img2img"),
    ("Flux3", "txt2img"), ("Flux3", "edit"),
    ("MOSS-Image", "txt2img"),
    ("Anima", "img2img"), ("Anima", "refine_faces"), ("Anima", "upscale"),
    ("Flux1", "inpaint"), ("Flux1", "refine_faces"), ("Flux1", "upscale"),
    ("Flux2", "inpaint"), ("Flux2", "refine_faces"), ("Flux2", "upscale"),
    ("Krea2", "inpaint"), ("Krea2", "refine_faces"),
    ("MOSS-Image", "img2img"), ("MOSS-Image", "refine_faces"), ("MOSS-Image", "upscale"),
    ("QwenImage", "inpaint"), ("QwenImage", "refine_faces"), ("QwenImage", "upscale"),
    ("QwenImage-2.1", "img2img"), ("QwenImage-2.1", "refine_faces"), ("QwenImage-2.1", "upscale"),
    ("ZImage", "img2img"), ("ZImage", "refine_faces"), ("ZImage", "upscale"),
    ("MiniMax-H3-Image", "txt2img"), ("MiniMax-H3-Image", "edit"),
    ("MiniMax-H3", "txt2video"), ("MiniMax-H3", "img2video"), ("MiniMax-H3", "first_last"), ("MiniMax-H3", "video"),
    ("MiniMax-H3", "refs"), ("MiniMax-H3", "upscale"), ("MiniMax-H3", "refine_faces"), ("MiniMax-H3", "extend"),
    ("MiniMax-H3", "remix"), ("MiniMax-H3", "talk"), ("MiniMax-H3", "motion_transfer"), ("MiniMax-H3", "loop"),
    ("MiniMax-H3-Fast", "video"), ("MiniMax-H3-Fast", "refine_faces"), ("MiniMax-H3-Fast", "extend"),
    ("MiniMax-H3-VDN", "video"),
    ("MiniMax-Music3", "song"), ("SeedVR2", "upscale"), ("SeedVR2", "video_upscale"), ("YuE2", "song"),
}

NO_ENCODER = {("MiniMax-Music3", "song"), ("SeedVR2", "upscale"), ("SeedVR2", "video_upscale"), ("YuE2", "song")}


@pytest.fixture(scope="module")
def shipped():
    presets, _ = load_all_presets(presets_root=ROOT / "content" / "presets" / "marketplace", include_plugins=False)
    return presets, build_processor()


def _declared_modes(presets):
    return {
        (Path(preset.path).name, mode): (preset, mode)
        for preset in presets
        for mode in preset.modes
        if negative_applies_when(preset, mode) is not None
    }


def _grid(fixture, preset):
    axes = {
        "cfg": [1.0, 4.0],
        "nag_enabled": [False, True],
        "nag_scale": [1.0, 1.5],
        "speed_profile": list((preset.speed_profiles or {}).keys()),
    }
    present = {name: values for name, values in axes.items() if name in fixture and values}
    names = list(present)
    for combo in itertools.product(*(present[n] for n in names)):
        yield dict(zip(names, combo))


def test_every_expected_mode_declares(shipped):
    presets, _ = shipped
    assert set(_declared_modes(presets)) == EXPECTED


def test_declaration_matches_what_the_encoder_does(shipped):
    presets, processor = shipped
    checked = 0
    for (directory, mode), (preset, _) in sorted(_declared_modes(presets).items()):
        fixture = build_fixture_form_data(preset, mode)
        for overrides in _grid(fixture, preset):
            try:
                values = dict(bind_form(
                    preset, mode, form_name=None, raw_form_data={**fixture, **overrides},
                    user_id=None, storage_dir=None,
                ).values)
            except FormBindingError as e:
                assert "speed_profile" in str(e), (directory, mode, overrides, e)
                continue
            pipes = processor.process(preset, build_generation_data(mode, values, preset.modes[mode]))
            node = next((p for p in pipes if p["name"] == "prompt_encoder"), None)
            if (directory, mode) in NO_ENCODER:
                assert node is None, (directory, mode)
                continue
            encoder = node["config"]
            encoded = (
                float(encoder.get("guidance_scale", 7.5)) > 1.0
                or float(encoder.get("nag_scale", 1.0)) > 1.0
            )
            assert encoder["negative_applied"] is encoded, (directory, mode, overrides, encoder)
            checked += 1
    assert checked > 4 * (len(EXPECTED) - len(NO_ENCODER))
