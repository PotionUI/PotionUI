"""Tests for the crop_subject pipe: the LIST-in/LIST-out array contract
(matching the `media_loader` producer / `gallery` consumer this family sits
between), bbox+margin crop math, clamping at the canvas edge, and the
empty-alpha pass-through discipline (must not crash or produce a zero-size
image)."""

import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.crop_subject.main import CropSubjectPipe


def _rgba_with_opaque_rect(size=(40, 40), rect=(10, 10, 20, 20), alpha=255):
    arr = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    x, y, w, h = rect
    arr[y:y + h, x:x + w, 3] = alpha
    arr[y:y + h, x:x + w, :3] = (200, 50, 50)
    return Image.fromarray(arr, mode="RGBA")


# -- contract ------------------------------------------------------------

def test_name_and_contract():
    assert CropSubjectPipe.name == "crop_subject"
    ins = {s.name: s for s in CropSubjectPipe.inputs()}
    outs = {s.name: s for s in CropSubjectPipe.outputs()}
    assert set(ins) == {"image"}
    assert ins["image"].io_type == IOType.IMAGE
    assert outs["image"].io_type == IOType.IMAGE
    assert outs["cropped"].io_type == IOType.BOOL


def test_image_and_cropped_io_are_declared_as_arrays():
    """The upstream producer (`media_loader`) always emits a LIST and the
    downstream consumer (`gallery`) always iterates one - a scalar `image`
    spec here is exactly the `'Image' object is not iterable` bug."""
    ins = {s.name: s for s in CropSubjectPipe.inputs()}
    outs = {s.name: s for s in CropSubjectPipe.outputs()}
    assert ins["image"].is_array is True
    assert outs["image"].is_array is True
    assert outs["cropped"].is_array is True


def test_config_spec_matches_contract():
    specs = {s.name: s for s in CropSubjectPipe.configuration()}
    assert set(specs) == {"margin", "alpha_threshold"}
    assert specs["margin"].default == 0
    assert specs["alpha_threshold"].default == 16
    assert specs["alpha_threshold"].min_value == 0 and specs["alpha_threshold"].max_value == 255


def test_missing_image_raises():
    pipe = CropSubjectPipe(CropSubjectPipe.get_default_config())
    with pytest.raises(ValueError):
        pipe.process(PipeInput(input={}), lambda o: None)


# -- array contract: process EVERY image, never just the first --------------

def test_two_image_list_returns_two_images_bite_check():
    """Feed a two-image list; both must come back, cropped independently, in
    order. Bite-check: reverting to unwrapping `image[0]` and returning a
    bare `PipeOutput(output={"image": cropped, "cropped": True})` makes this
    go red (a one-element result / a scalar `cropped` instead of a list)."""
    image_a = _rgba_with_opaque_rect(size=(40, 40), rect=(10, 10, 20, 20))
    image_b = _rgba_with_opaque_rect(size=(40, 40), rect=(5, 5, 10, 10))
    pipe = CropSubjectPipe({"margin": 0, "alpha_threshold": 16})

    result = pipe.process(PipeInput(input={"image": [image_a, image_b]}), lambda o: None)

    images = result.output["image"]
    cropped_flags = result.output["cropped"]
    assert isinstance(images, list) and len(images) == 2
    assert isinstance(cropped_flags, list) and len(cropped_flags) == 2
    assert images[0].size == (20, 20)
    assert images[1].size == (10, 10)
    assert cropped_flags == [True, True]


def test_bare_image_input_is_still_accepted_and_wrapped():
    image = _rgba_with_opaque_rect()
    pipe = CropSubjectPipe({"margin": 0, "alpha_threshold": 16})
    result = pipe.process(PipeInput(input={"image": image}), lambda o: None)
    assert isinstance(result.output["image"], list)
    assert len(result.output["image"]) == 1


# -- crop math -------------------------------------------------------------

def test_crops_to_bbox_with_no_margin():
    image = _rgba_with_opaque_rect(size=(40, 40), rect=(10, 10, 20, 20))
    pipe = CropSubjectPipe({"margin": 0, "alpha_threshold": 16})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)

    assert result.output["cropped"] == [True]
    cropped = result.output["image"][0]
    assert cropped.size == (20, 20)
    assert np.all(np.array(cropped)[..., 3] == 255)


def test_margin_expands_the_crop():
    image = _rgba_with_opaque_rect(size=(40, 40), rect=(10, 10, 20, 20))
    pipe = CropSubjectPipe({"margin": 5, "alpha_threshold": 16})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)

    cropped = result.output["image"][0]
    # bbox (10,10,20,20) + 5px margin on every side -> 30x30
    assert cropped.size == (30, 30)


def test_margin_bite_check_removing_it_shrinks_crop():
    """Pin the exact math: margin must ADD px on every side, not zero out."""
    image = _rgba_with_opaque_rect(size=(40, 40), rect=(10, 10, 20, 20))
    no_margin = CropSubjectPipe({"margin": 0, "alpha_threshold": 16}).process(
        PipeInput(input={"image": [image]}), lambda o: None
    ).output["image"][0]
    with_margin = CropSubjectPipe({"margin": 5, "alpha_threshold": 16}).process(
        PipeInput(input={"image": [image]}), lambda o: None
    ).output["image"][0]
    assert with_margin.size[0] > no_margin.size[0]
    assert with_margin.size[1] > no_margin.size[1]


def test_margin_clamps_at_canvas_edge_without_crashing():
    image = _rgba_with_opaque_rect(size=(40, 40), rect=(0, 0, 5, 5))
    pipe = CropSubjectPipe({"margin": 1000, "alpha_threshold": 16})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)
    cropped = result.output["image"][0]
    # Margin blown out past every edge clamps back to the full canvas.
    assert cropped.size == (40, 40)


def test_alpha_threshold_excludes_soft_edge_pixels():
    arr = np.zeros((30, 30, 4), dtype=np.uint8)
    arr[10:20, 10:20, 3] = 255
    arr[9, 10:20, 3] = 10  # a soft-edge row just outside, below threshold
    image = Image.fromarray(arr, mode="RGBA")

    pipe = CropSubjectPipe({"margin": 0, "alpha_threshold": 16})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)
    assert result.output["image"][0].size == (10, 10)  # the soft row is excluded

    pipe_low_threshold = CropSubjectPipe({"margin": 0, "alpha_threshold": 5})
    result_low = pipe_low_threshold.process(PipeInput(input={"image": [image]}), lambda o: None)
    assert result_low.output["image"][0].size == (10, 11)  # now the soft row counts


# -- empty-alpha pass-through discipline ------------------------------------

def test_empty_alpha_passes_through_unchanged_not_crash():
    arr = np.zeros((25, 30, 4), dtype=np.uint8)  # fully transparent
    image = Image.fromarray(arr, mode="RGBA")
    pipe = CropSubjectPipe({"margin": 0, "alpha_threshold": 16})

    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)

    assert result.output["cropped"] == [False]
    assert result.output["image"][0] is image
    assert result.output["image"][0].size == (30, 25)


def test_mixed_batch_one_empty_one_with_subject():
    """A broken matte on one frame of a batch must not also break the crop
    for its siblings."""
    empty = Image.fromarray(np.zeros((20, 20, 4), dtype=np.uint8), mode="RGBA")
    with_subject = _rgba_with_opaque_rect(size=(20, 20), rect=(5, 5, 5, 5))

    pipe = CropSubjectPipe({"margin": 0, "alpha_threshold": 16})
    result = pipe.process(PipeInput(input={"image": [empty, with_subject]}), lambda o: None)

    assert result.output["cropped"] == [False, True]
    assert result.output["image"][0].size == (20, 20)
    assert result.output["image"][1].size == (5, 5)


def test_empty_alpha_bite_check_would_zero_size_without_guard():
    """Confirms `alpha_bbox` really does return None for this input (the
    condition the pass-through branch depends on) rather than an accidental
    degenerate non-None bbox that would happen to also work."""
    from src.pipelines.pipes._shared.imaging.alpha import alpha_bbox

    arr = np.zeros((25, 30, 4), dtype=np.uint8)
    image = Image.fromarray(arr, mode="RGBA")
    assert alpha_bbox(image, 16) is None
