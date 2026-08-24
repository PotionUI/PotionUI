"""Detection-box visualization must accept the colour shape config actually holds.

`box_color` reaches this code as a **list** - that is what a pipe's defaults
declare (`PipeConfigSpec("box_color", list, [0, 0, 0], ...)`), what the video
detailer has always used, and the only thing a value can be once it has been
through YAML, JSON, or an execution package. PIL does not accept a list:
`ImageDraw.rectangle(outline=[0, 0, 0])` raises
`TypeError: color must be int or tuple`. Nothing caught that before, because
this path only imports where cv2 does.
"""

import numpy as np
import pytest
from PIL import Image

pytest.importorskip(
    "cv2",
    reason="cv2 required by ultralytics (detailer helper dependency)",
    exc_type=ImportError,
)

from src.pipelines.pipes._shared.detection.detailer_helper import DetailerHelper


def helper(box_color):
    return DetailerHelper({
        "detections": {
            "face": {"padding": 4, "box_color": box_color, "box_thickness": 2},
        }
    })


@pytest.fixture
def image():
    return Image.new("RGB", (64, 64), (10, 10, 10))


@pytest.fixture
def boxes():
    return [np.array([8, 8, 40, 40])]


def test_a_list_colour_draws(image, boxes):
    result = helper([255, 0, 255]).visualize_detections(image, boxes, "face")

    assert result.size == image.size
    assert result is not image


def test_a_tuple_colour_still_draws(image, boxes):
    result = helper((255, 0, 255)).visualize_detections(image, boxes, "face")

    assert result.size == image.size


def test_list_and_tuple_colours_render_identically(image, boxes):
    """The list is a spelling of the same colour, not a different one."""
    from_list = helper([255, 0, 255]).visualize_detections(image, boxes, "face")
    from_tuple = helper((255, 0, 255)).visualize_detections(image, boxes, "face")

    assert list(from_list.getdata()) == list(from_tuple.getdata())


def test_the_drawn_colour_is_the_configured_one(image, boxes):
    result = helper([255, 0, 255]).visualize_detections(image, boxes, "face")

    assert (255, 0, 255) in set(result.getdata())
