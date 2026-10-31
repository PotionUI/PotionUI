import pytest

from tests.features.presets._sdxl_modes import (
    is_enabled,
    load_sdxl,
    mode_fields,
    mode_form,
    pipe,
    preset_manifest,
    provider,
    render,
)

MODE = "control"
GUIDES = {
    "openpose": "xinsir-controlnet-openpose-sdxl.safetensors",
    "scribble": "xinsir-controlnet-scribble-sdxl.safetensors",
    "depth": "xinsir-controlnet-depth-sdxl.safetensors",
    "canny": "xinsir-controlnet-canny-sdxl.safetensors",
}


@pytest.fixture(scope="module")
def sdxl():
    return load_sdxl()


def test_the_mode_is_registered_as_structure():
    assert MODE in preset_manifest()["modes"]
    assert mode_form(MODE)["mode_label"] == "Structure"
    assert "control" in preset_manifest()["formulas"]["groups"]


@pytest.mark.parametrize("guide", sorted(GUIDES))
def test_each_guide_loads_its_own_controlnet_and_preprocessor(sdxl, guide):
    pipes = render(sdxl, MODE, guide=guide)
    (entry,) = pipe(pipes, "controlnet_loader")["config"]["controlnets"]
    assert entry["file_path"].endswith(f"cn_{guide}_model.safetensors")
    assert entry["type"] == guide
    assert entry["enabled"] is True
    (prep,) = pipe(pipes, "controlnet_preprocessor")["config"]["preprocessors"]
    assert prep["type"] == guide


def test_the_extracted_map_drives_the_generator(sdxl):
    pipes = render(sdxl, MODE)
    generator = pipe(pipes, "generator")
    assert provider(generator, "control_image") == "controlnet_preprocessor"
    assert provider(generator, "controlnet") == "controlnet_loader/sdxl"
    assert provider(pipe(pipes, "gallery"), "image") == "generator/sdxl"


def test_a_ready_made_map_skips_the_preprocessor(sdxl):
    pipes = render(sdxl, MODE, guide_extract=False)
    assert not is_enabled(pipe(pipes, "controlnet_preprocessor"))
    assert provider(pipe(pipes, "generator"), "control_image") == "guide_loader"
    assert not is_enabled(pipe(pipes, "guide_gallery"))


def test_saving_the_guide_adds_a_labelled_output_only_when_extracting(sdxl):
    saved = pipe(render(sdxl, MODE, guide="openpose", guide_save=True), "guide_gallery")
    assert is_enabled(saved)
    assert saved["config"]["label"] == "Guide: Pose"
    assert provider(saved, "image") == "controlnet_preprocessor"
    raw = pipe(render(sdxl, MODE, guide_extract=False, guide_save=True), "guide_gallery")
    assert not is_enabled(raw)


def test_strength_and_window_reach_the_controlnet(sdxl):
    pipes = render(sdxl, MODE, control_strength=0.6, control_start=0.1, control_end=0.9)
    (entry,) = pipe(pipes, "controlnet_loader")["config"]["controlnets"]
    assert entry["conditioning_scale"] == 0.6
    assert entry["control_guidance_start"] == 0.1
    assert entry["control_guidance_end"] == 0.9


def test_the_preprocessor_fails_loudly_instead_of_passing_the_photo_through(sdxl):
    config = pipe(render(sdxl, MODE), "controlnet_preprocessor")["config"]
    assert config["strict"] is True


def test_every_guide_recommends_a_hashed_apache_xinsir_controlnet():
    fields = mode_fields(MODE, "generation")
    for guide, filename in GUIDES.items():
        field = fields[f"cn_{guide}_model"]
        assert field["required"] is True
        assert field["configuration"]["model_type"] == "controlnet"
        (rec,) = field["configuration"]["recommendations"]
        assert rec["name"] == filename
        assert len(rec["sha256"]) == 64
        assert rec["link"].startswith(f"https://huggingface.co/xinsir/controlnet-{guide}-sdxl-1.0/")
        shown = [r for r in field["reactions"] if r["then"] == {"set_visibility": True}]
        assert [r["when"] for r in shown] == [{"field": "guide", "equals": guide}]


def test_only_the_default_guides_model_starts_visible():
    fields = mode_fields(MODE, "generation")
    default = fields["guide"]["default"]
    for guide in GUIDES:
        assert (fields[f"cn_{guide}_model"].get("visible", True) is True) == (guide == default)
