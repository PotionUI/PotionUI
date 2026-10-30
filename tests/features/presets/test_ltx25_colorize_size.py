import pytest

from .test_ltx25_ic_lora_captions import ltx25_template, process


def generator_pixels(template, **over):
    pipes = process(template, "colorize", "red tulips", **over)
    generator = next(pipe for key, pipe in pipes.items() if key.startswith("generator"))
    return generator["config"]["max_pixels"]


def test_colorize_defaults_to_the_544p_training_bucket(ltx25_template):
    assert generator_pixels(ltx25_template) == 557056


@pytest.mark.parametrize("size, pixels", [("544p", 557056), ("720p", 921600), ("1080p", 2088960)])
def test_each_colorize_size_reaches_the_generator(ltx25_template, size, pixels):
    assert generator_pixels(ltx25_template, output_size=size) == pixels
