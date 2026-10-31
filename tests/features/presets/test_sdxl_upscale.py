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

MODE = "upscale"


@pytest.fixture(scope="module")
def sdxl():
    return load_sdxl()


def test_the_mode_is_registered_with_an_optional_prompt():
    assert MODE in preset_manifest()["modes"]
    form = mode_form(MODE)
    assert form["prompt"] == "optional"
    assert form["mode_label"] == "Upscale & refine"


def test_the_picture_is_upscaled_then_redetailed_then_saved(sdxl):
    pipes = render(sdxl, MODE)
    assert provider(pipe(pipes, "upscaler"), "image") == "media_loader"
    assert provider(pipe(pipes, "tiled_detailer"), "image") == "upscaler"
    assert provider(pipe(pipes, "gallery"), "image") == "tiled_detailer"


def test_scale_and_strength_reach_the_pipes(sdxl):
    pipes = render(sdxl, MODE, upscale_factor=3.5, td_denoise=0.4)
    assert pipe(pipes, "upscaler")["config"]["scale"] == 3.5
    assert pipe(pipes, "tiled_detailer")["config"]["denoise"] == 0.4


def test_turning_redetail_off_skips_the_checkpoint_entirely(sdxl):
    pipes = render(sdxl, MODE, refine=False)
    for pipe_id in ("checkpoint_loader", "prompt_encoder", "tiled_detailer"):
        assert not is_enabled(pipe(pipes, pipe_id))
    assert is_enabled(pipe(pipes, "upscaler"))
    assert provider(pipe(pipes, "gallery"), "image") == "upscaler"


def test_history_only_records_the_checkpoint_when_it_was_used(sdxl):
    on = pipe(render(sdxl, MODE), "param_emitter")["config"]["parameters"]
    off = pipe(render(sdxl, MODE, refine=False), "param_emitter")["config"]["parameters"]
    assert any("model.safetensors" in str(entry) and "upscale_model" not in str(entry) for entry in on)
    assert not any("/GOLDEN/models/model.safetensors" in str(entry) for entry in off)


def test_the_tile_options_map_to_the_detailer_switches(sdxl):
    default = pipe(render(sdxl, MODE), "tiled_detailer")["config"]
    assert default["enable_detail_conditioning"] is True and default["content_aware_denoise"] is True
    tuned = pipe(render(sdxl, MODE, td_adaptive_tiles=["content_aware_denoise"]), "tiled_detailer")["config"]
    assert tuned["enable_detail_conditioning"] is False and tuned["content_aware_denoise"] is True


def test_an_empty_prompt_gets_a_quality_prompt_and_a_written_one_keeps_its_words(sdxl):
    empty = pipe(render(sdxl, MODE, positive=""), "prompt_encoder")["config"]["p_prompt"]["output"]
    written = pipe(render(sdxl, MODE, positive="fine fur"), "prompt_encoder")["config"]["p_prompt"]["output"]
    assert empty == "best quality, highly detailed"
    assert written == "fine fur, best quality, highly detailed"


def test_the_scale_slider_covers_two_to_four_times_and_checkpoint_hides_without_redetail():
    fields = mode_fields(MODE, "generation")
    scale = fields["upscale_factor"]["configuration"]
    assert scale["min"] <= 2.0 and scale["max"] >= 4.0
    assert fields["model"]["required"] is True
    assert fields["model"]["reactions"][0]["when"] == {"field": "refine", "equals": False}
    assert fields["model"]["reactions"][0]["then"] == {"set_visibility": False}
