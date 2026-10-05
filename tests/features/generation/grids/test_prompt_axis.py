import random

from src.features.generation.dto import SegmentInput
from src.features.generation.grids.expansion import expand_cells
from tests.features.generation.grids.conftest import axis, base_request, field_index

INDEX = field_index(sampler="select", seed="seed")


def prompt_axis(*pairs):
    values = [{"find": find, "replace": replace} for find, replace, _ in pairs]
    return axis("__prompt__", values, type="prompt", labels=[label for _, _, label in pairs])


def expand(x_axis, request, y_axis=None):
    return expand_cells(
        request, x_axis, y_axis, True, INDEX, preset_id="p1", mode="txt2img", rng=random.Random(2)
    )


def test_find_replace_rewrites_the_positive_prompt_text_per_cell():
    request = base_request(prompt="a cat at dusk, dusk light")

    cells = expand(prompt_axis(("dusk", "dawn", "dawn"), ("dusk", "noon", "noon")), request)

    assert [c.request.prompts[0].positive for c in cells] == [
        "a cat at dawn, dawn light",
        "a cat at noon, noon light",
    ]
    assert [c.axis_values for c in cells] == [{"__prompt__": "dawn"}, {"__prompt__": "noon"}]


def test_a_null_replacement_keeps_the_original_prompt():
    request = base_request(prompt="a cat at dusk")

    cells = expand(prompt_axis(("dusk", None, "(keep original)"), ("dusk", "dawn", "dawn")), request)

    assert cells[0].request.prompts[0].positive == "a cat at dusk"
    assert cells[1].request.prompts[0].positive == "a cat at dawn"


def test_the_negative_prompt_is_never_rewritten():
    request = base_request(prompt="a cat at dusk", negative_prompt="dusk, blur")

    cells = expand(prompt_axis(("dusk", "dawn", "dawn")), request)

    assert cells[0].request.prompts[0].negative == "dusk, blur"


def test_segments_are_rewritten_in_the_positive_channel_only():
    request = base_request(
        prompt="a cat at dusk",
        segments=[
            SegmentInput(channel="positive", segment_index=0, text="a cat at dusk"),
            SegmentInput(channel="positive", segment_index=1, text="dusk haze", is_disabled=True),
            SegmentInput(channel="negative", segment_index=0, text="dusk"),
        ],
    )

    cells = expand(prompt_axis(("dusk", "dawn", "dawn")), request)

    segments = cells[0].request.segments
    assert segments[0].text == "a cat at dawn"
    assert segments[1].text == "dusk haze"
    assert segments[2].text == "dusk"
    assert request.segments[0].text == "a cat at dusk"


def test_a_prompt_axis_is_not_written_into_the_form_data():
    request = base_request(prompt="a cat at dusk")

    cells = expand(prompt_axis(("dusk", "dawn", "dawn")), request)

    assert "__prompt__" not in cells[0].request.form_data


def test_a_prompt_axis_combines_with_a_field_axis():
    request = base_request(prompt="a cat at dusk")

    cells = expand(prompt_axis(("dusk", "dawn", "dawn"), ("dusk", "noon", "noon")), request, axis("sampler", ["a", "b"]))

    assert [(c.x, c.y, c.request.prompts[0].positive, c.request.form_data["sampler"]) for c in cells] == [
        (0, 0, "a cat at dawn", "a"),
        (1, 0, "a cat at noon", "a"),
        (0, 1, "a cat at dawn", "b"),
        (1, 1, "a cat at noon", "b"),
    ]
