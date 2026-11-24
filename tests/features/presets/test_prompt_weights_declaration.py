from pathlib import Path

import pytest

from scripts.preset_render import ROOT, load_all_presets

NO_WEIGHTS = {
    "Flux1", "Flux3", "MOSS-Image", "QwenImage-2.1",
    "MiniMax-H3", "MiniMax-H3-Fast", "MiniMax-H3-Image", "MiniMax-H3-VDN",
    "Wan", "LTX-2", "LTX-2.5",
}
WEIGHTED = {"SDXL", "Anima", "ZImage", "QwenImage", "Krea2"}


@pytest.fixture(scope="module")
def templates():
    presets, _ = load_all_presets(presets_root=ROOT / "content" / "presets" / "marketplace", include_plugins=False)
    return {Path(preset.path).name: preset for preset in presets}


@pytest.mark.parametrize("name", sorted(NO_WEIGHTS))
def test_presets_without_weighting_declare_it(templates, name):
    assert (templates[name].vars or {}).get("supports_prompt_weights") is False


@pytest.mark.parametrize("name", sorted(WEIGHTED))
def test_presets_with_weighting_do_not_declare_it(templates, name):
    assert "supports_prompt_weights" not in (templates[name].vars or {})


def test_declaration_is_served_with_the_preset(templates):
    from src.features.presets.file_repository import FilePresetRepository

    repository = FilePresetRepository.__new__(FilePresetRepository)
    served = repository.preset_to_info(templates["Flux1"])
    assert served.vars["supports_prompt_weights"] is False
    assert "supports_prompt_weights" not in repository.preset_to_info(templates["SDXL"]).vars


PICTURE_FIELDS = {
    "QwenImage": {"edit": ["source_image"], "real": ["source_image"]},
    "Krea2": {"edit": ["source_image", "source_image_b"]},
}


@pytest.mark.parametrize("name", sorted(PICTURE_FIELDS))
def test_picture_conditioned_modes_declare_ignored_weights(templates, name):
    declared = (templates[name].vars or {})["prompt_weights_ignored_with_images"]
    assert {entry["mode"]: entry["fields"] for entry in declared} == PICTURE_FIELDS[name]


def test_picture_declaration_is_served_with_the_preset(templates):
    from src.features.presets.file_repository import FilePresetRepository

    repository = FilePresetRepository.__new__(FilePresetRepository)
    served = repository.preset_to_info(templates["Krea2"])
    assert served.vars["prompt_weights_ignored_with_images"][0]["mode"] == "edit"
