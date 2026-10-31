import pytest

from tests.features.presets._sdxl_modes import (
    load_sdxl,
    mode_fields,
    mode_form,
    pipe,
    preset_manifest,
    provider,
    render,
)

MODE = "restyle"


@pytest.fixture(scope="module")
def sdxl():
    return load_sdxl()


def test_the_mode_is_registered_and_requires_a_prompt():
    assert MODE in preset_manifest()["modes"]
    form = mode_form(MODE)
    assert "prompt" not in form
    assert form["mode_label"] == "Img2img / restyle"


def test_the_picture_feeds_the_generator_without_a_mask(sdxl):
    pipes = render(sdxl, MODE)
    generator = pipe(pipes, "generator")
    assert provider(generator, "image") == "media_loader"
    assert not any(edge["name"] == "mask" for edge in generator["input"])
    assert "inpaint_mode" not in generator["config"]
    assert provider(pipe(pipes, "gallery"), "image") == "generator/sdxl"


def test_strength_and_working_size_reach_the_generator(sdxl):
    config = pipe(render(sdxl, MODE, denoise=0.45, max_pixels=2097152), "generator")["config"]
    assert config["denoise"] == 0.45
    assert config["max_pixels"] == 2097152


def test_original_size_means_no_pixel_budget(sdxl):
    assert pipe(render(sdxl, MODE, max_pixels=0), "generator")["config"]["max_pixels"] == 0


def test_quantity_loads_one_source_copy_per_image(sdxl):
    pipes = render(sdxl, MODE, quantity=3)
    media = pipe(pipes, "media_loader")["config"]["media"]
    assert len(media) == 3
    assert {m["path"] for m in media} == {media[0]["path"]}
    assert pipe(pipes, "generator")["config"]["quantity"] == 3
    assert pipe(pipes, "seed_generator")["config"]["quantity"] == 3


def test_embeddings_and_loras_are_wired_like_txt2img(sdxl):
    pipes = render(sdxl, MODE)
    assert provider(pipe(pipes, "prompt_encoder"), "text_encoder") == "checkpoint_loader/sdxl"
    assert "positive_embeddings" in pipe(pipes, "prompt_encoder")["config"]
    assert "loras" in pipe(pipes, "checkpoint_loader")["config"]


def test_the_working_size_choices_are_integers_with_a_one_megapixel_default():
    field = mode_fields(MODE, "generation")["max_pixels"]
    values = [option["value"] for option in field["configuration"]["options"]]
    assert all(isinstance(v, int) for v in values)
    assert field["default"] == 1048576
    assert 0 in values
