import itertools

import pytest

from scripts.preset_render import (
    ROOT,
    build_fixture_form_data,
    build_form_serializer,
    build_generation_data,
    build_processor,
    load_all_presets,
)
from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets.negative_prompt import negative_applies_when

EXPECTED = {
    ("Krea2", "txt2img"), ("Krea2", "enhance"),
    ("LTX-2", "video"), ("LTX-2", "upscale"),
    ("LTX-2.5", "video"), ("LTX-2.5", "upscale"),
    ("ZImage", "txt2img"), ("Wan", "video"),
    ("QwenImage", "txt2img"), ("QwenImage", "img2img"), ("QwenImage", "edit"),
    ("QwenImage-2.1", "txt2img"), ("QwenImage-2.1", "edit"), ("QwenImage-2.1", "control"),
    ("Anima", "txt2img"),
    ("Flux1", "txt2img"), ("Flux1", "img2img"), ("Flux2", "txt2img"), ("Flux2", "img2img"),
}


@pytest.fixture(scope="module")
def shipped():
    presets, _ = load_all_presets(presets_root=ROOT / "content" / "presets" / "marketplace", include_plugins=False)
    return presets, build_processor(), build_form_serializer()


def _declared_modes(presets):
    return {
        (preset.path.rstrip("/").split("/")[-1], mode): (preset, mode)
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
    presets, _, _ = shipped
    assert set(_declared_modes(presets)) == EXPECTED


def test_declaration_matches_what_the_encoder_does(shipped):
    presets, processor, form_serializer = shipped
    checked = 0
    for (directory, mode), (preset, _) in sorted(_declared_modes(presets).items()):
        fixture = build_fixture_form_data(form_serializer, preset, mode)
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
            encoder = next(p for p in pipes if p["name"] == "prompt_encoder")["config"]
            encoded = (
                float(encoder.get("guidance_scale", 7.5)) > 1.0
                or float(encoder.get("nag_scale", 1.0)) > 1.0
            )
            assert encoder["negative_applied"] is encoded, (directory, mode, overrides, encoder)
            checked += 1
    assert checked > 4 * len(EXPECTED)
