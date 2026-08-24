"""Tests for the color_key pipe: config/IO contract, the soft-ramp math,
despill, and the ONE test that is the whole point of this pipe -
`test_chrominance_beats_rgb_distance_on_lit_gradient` - a synthetic green
field with a strong luminance gradient plus a dark, hue-distinct subject,
where chrominance keying removes the WHOLE background while keeping the
subject, and Euclidean RGB-distance keying provably cannot do both at any
tolerance.
"""

import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes._shared.imaging.color import rgb_to_cbcr
from src.pipelines.pipes.color_key.main import ColorKeyPipe, MAX_CHROMA_DISTANCE, soft_alpha


# -- config / IO contract -----------------------------------------------------

def test_name_and_contract():
    assert ColorKeyPipe.name == "color_key"
    ins = {s.name: s.io_type for s in ColorKeyPipe.inputs()}
    outs = {s.name: s.io_type for s in ColorKeyPipe.outputs()}
    assert ins == {"image": IOType.IMAGE}
    assert outs == {"image": IOType.IMAGE}


def test_image_io_is_declared_as_an_array():
    """The upstream producer (`media_loader`) always emits a LIST and the
    downstream consumer (`gallery`) always iterates one - a scalar `image`
    spec here is exactly the `'Image' object is not iterable` bug."""
    ins = {s.name: s for s in ColorKeyPipe.inputs()}
    outs = {s.name: s for s in ColorKeyPipe.outputs()}
    assert ins["image"].is_array is True
    assert outs["image"].is_array is True


def test_config_spec_matches_contract():
    specs = {s.name: s for s in ColorKeyPipe.configuration()}
    assert set(specs) == {"key_mode", "key_color", "tolerance", "softness", "despill", "feather"}
    assert specs["key_mode"].default == "auto" and specs["key_mode"].choices == ["auto", "color"]
    assert specs["key_color"].default == "#00B140"
    assert specs["tolerance"].default == 25.0
    assert specs["softness"].default == 10.0
    assert specs["despill"].default is True
    assert specs["feather"].default == 0.0
    assert specs["tolerance"].min_value == 0.0 and specs["tolerance"].max_value == 100.0
    assert specs["softness"].min_value == 0.0 and specs["softness"].max_value == 100.0
    assert specs["feather"].min_value == 0.0 and specs["feather"].max_value == 16.0


def test_missing_image_raises():
    pipe = ColorKeyPipe(ColorKeyPipe.get_default_config())
    with pytest.raises(ValueError):
        pipe.process(PipeInput(input={}), lambda o: None)


def test_unknown_key_mode_raises():
    pipe = ColorKeyPipe({"key_mode": "bogus"})
    with pytest.raises(ValueError):
        pipe.process(PipeInput(input={"image": [Image.new("RGB", (10, 10))]}), lambda o: None)


def test_color_mode_without_key_color_raises():
    pipe = ColorKeyPipe({"key_mode": "color", "key_color": None})
    with pytest.raises(ValueError):
        pipe.process(PipeInput(input={"image": [Image.new("RGB", (10, 10))]}), lambda o: None)


# -- array contract: process EVERY image, never just the first --------------

def test_two_image_list_returns_two_images_bite_check():
    """Feed a two-image list (two different flat backgrounds); both must
    come back keyed independently, in order. Bite-check: reverting to
    unwrapping `image[0]` and returning a bare
    `PipeOutput(output={"image": result})` makes this go red."""
    green = Image.fromarray(np.full((10, 10, 3), (0, 255, 0), dtype=np.uint8), mode="RGB")
    red_subject = Image.fromarray(np.full((10, 10, 3), (200, 30, 30), dtype=np.uint8), mode="RGB")

    pipe = ColorKeyPipe({"key_mode": "color", "key_color": "#00ff00",
                         "tolerance": 10.0, "softness": 0.0, "despill": False})
    result = pipe.process(PipeInput(input={"image": [green, red_subject]}), lambda o: None)

    images = result.output["image"]
    assert isinstance(images, list) and len(images) == 2
    assert np.all(np.array(images[0])[..., 3] == 0)    # flat green -> fully keyed
    assert np.all(np.array(images[1])[..., 3] == 255)  # flat red -> fully kept


def test_bare_image_input_is_still_accepted_and_wrapped():
    image = Image.new("RGB", (10, 10), (0, 255, 0))
    pipe = ColorKeyPipe({"key_mode": "color", "key_color": "#00ff00"})
    result = pipe.process(PipeInput(input={"image": image}), lambda o: None)
    assert isinstance(result.output["image"], list)
    assert len(result.output["image"]) == 1


# -- soft_alpha ramp math -----------------------------------------------------

def test_soft_alpha_zero_softness_is_hard_binary():
    distance = np.array([0.0, 50.0, 100.0])
    alpha = soft_alpha(distance, threshold=60.0, ramp_half=0.0)
    assert list(alpha) == [0, 0, 255]


def test_soft_alpha_ramp_is_monotonic_and_bounded():
    distance = np.linspace(0, MAX_CHROMA_DISTANCE, 50)
    alpha = soft_alpha(distance, threshold=MAX_CHROMA_DISTANCE / 2, ramp_half=MAX_CHROMA_DISTANCE / 4)
    alpha_list = [int(a) for a in alpha]
    assert alpha_list == sorted(alpha_list)
    assert alpha_list[0] == 0
    assert alpha_list[-1] == 255


def test_soft_alpha_bite_check_ramp_produces_intermediate_values():
    """A binary-cut implementation (no ramp) would fail this: within the
    ramp band there must be alpha values strictly between 0 and 255."""
    distance = np.linspace(40.0, 80.0, 20)
    alpha = soft_alpha(distance, threshold=60.0, ramp_half=20.0)
    assert any(0 < int(a) < 255 for a in alpha)


# -- end-to-end: simple flat background -------------------------------------

def test_flat_green_background_is_fully_keyed_hard_cut():
    arr = np.full((20, 20, 3), (0, 255, 0), dtype=np.uint8)
    arr[5:15, 5:15] = (200, 30, 30)  # red subject block, unambiguously off-hue
    image = Image.fromarray(arr, mode="RGB")

    pipe = ColorKeyPipe({"key_mode": "color", "key_color": "#00ff00",
                         "tolerance": 10.0, "softness": 0.0, "despill": False})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)
    alpha = np.array(result.output["image"][0])[..., 3]

    assert np.all(alpha[0:5, :] == 0)  # background rows above the subject
    assert np.all(alpha[5:15, 5:15] == 255)  # subject


def test_despill_suppresses_green_fringe_on_kept_pixels():
    arr = np.full((10, 10, 3), (10, 250, 10), dtype=np.uint8)  # near-gray with a green fringe
    image = Image.fromarray(arr, mode="RGB")

    no_despill = ColorKeyPipe({"key_mode": "color", "key_color": "#00ff00",
                               "tolerance": 0.0, "softness": 0.0, "despill": False})
    with_despill = ColorKeyPipe({"key_mode": "color", "key_color": "#00ff00",
                                 "tolerance": 0.0, "softness": 0.0, "despill": True})

    out_no = np.array(no_despill.process(PipeInput(input={"image": [image]}), lambda o: None).output["image"][0])
    out_yes = np.array(with_despill.process(PipeInput(input={"image": [image]}), lambda o: None).output["image"][0])

    assert out_no[0, 0, 1] == 250  # untouched
    assert out_yes[0, 0, 1] < out_no[0, 0, 1]  # green channel suppressed


def test_feather_softens_hard_edge():
    arr = np.full((20, 20, 3), (0, 255, 0), dtype=np.uint8)
    arr[:, 10:] = (200, 30, 30)
    image = Image.fromarray(arr, mode="RGB")

    pipe = ColorKeyPipe({"key_mode": "color", "key_color": "#00ff00",
                         "tolerance": 10.0, "softness": 0.0, "feather": 4.0})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)
    alpha = np.array(result.output["image"][0])[..., 3]
    edge_strip = alpha[10, 6:14]
    assert any(0 < int(v) < 255 for v in edge_strip)


# -- THE test: chrominance beats RGB-distance on an unevenly-lit key -----

_KEY_RGB = (0, 255, 0)
_K_MIN = 0.15          # darkest shadow the green backdrop falls to (85% attenuation)
_SUBJECT_GRAY = 85     # the RGB-distance-MINIMIZING neutral gray for this key -
                        # i.e. the best possible case for the RGB-distance keyer,
                        # and it still can't separate it from the shadowed backdrop.


def _lit_gradient_with_dark_subject(height=60, width=40):
    """A vertical green-screen gradient (row 0 = fully lit key colour, last
    row = `_K_MIN` fraction of it - deep shadow, still not pure black) with a
    solid neutral-gray subject patch away from every edge."""
    ks = np.linspace(1.0, _K_MIN, height)
    rgb = np.zeros((height, width, 3), dtype=np.float64)
    rgb[..., 1] = (ks * 255.0)[:, None]  # pure green, scaled by row-wise k

    subject_mask = np.zeros((height, width), dtype=bool)
    y0, y1 = height // 3, 2 * height // 3
    x0, x1 = width // 3, 2 * width // 3
    subject_mask[y0:y1, x0:x1] = True
    rgb[subject_mask] = (_SUBJECT_GRAY, _SUBJECT_GRAY, _SUBJECT_GRAY)

    bg_mask = ~subject_mask
    return rgb.astype(np.uint8), bg_mask, subject_mask


def _chroma_distance_pct(rgb, key_rgb):
    cbcr = rgb_to_cbcr(rgb.astype(np.float64))
    key_cbcr = rgb_to_cbcr(np.array(key_rgb, dtype=np.float64).reshape(1, 1, 3))[0, 0]
    distance = np.sqrt(np.sum((cbcr - key_cbcr) ** 2, axis=-1))
    return distance / MAX_CHROMA_DISTANCE * 100.0


def _rgb_distance_pct(rgb, key_rgb):
    """Deliberately naive Euclidean-RGB-distance reference, self-contained
    here (NOT the spritesheet plugin's keying.py) - this is the baseline the
    chrominance keyer must beat, not the implementation under test."""
    key = np.array(key_rgb, dtype=np.float64)
    distance = np.sqrt(np.sum((rgb.astype(np.float64) - key) ** 2, axis=-1))
    max_distance = (3 * 255.0 ** 2) ** 0.5
    return distance / max_distance * 100.0


def test_construction_sanity_chroma_gap_exists_but_rgb_gap_does_not():
    """The analytical property the rest of this test exploits: in
    chrominance terms the worst-case background pixel is strictly CLOSER to
    the key than the subject (a real separating tolerance exists); in RGB
    terms it is the other way around (no separating tolerance exists)."""
    rgb, bg_mask, subject_mask = _lit_gradient_with_dark_subject()

    chroma_pct = _chroma_distance_pct(rgb, _KEY_RGB)
    assert chroma_pct[bg_mask].max() < chroma_pct[subject_mask].min()

    rgb_pct = _rgb_distance_pct(rgb, _KEY_RGB)
    assert rgb_pct[bg_mask].max() >= rgb_pct[subject_mask].min()


def test_chrominance_beats_rgb_distance_on_lit_gradient():
    rgb, bg_mask, subject_mask = _lit_gradient_with_dark_subject()
    image = Image.fromarray(rgb, mode="RGB")

    chroma_pct = _chroma_distance_pct(rgb, _KEY_RGB)
    low = float(chroma_pct[bg_mask].max())
    high = float(chroma_pct[subject_mask].min())
    assert low < high, "construction invariant: a separating tolerance must exist"
    tolerance = (low + high) / 2.0

    pipe = ColorKeyPipe({"key_mode": "color", "key_color": "#00ff00",
                         "tolerance": tolerance, "softness": 0.0, "despill": False})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)
    alpha = np.array(result.output["image"][0])[..., 3]

    assert np.all(alpha[bg_mask] == 0), "chrominance keying must remove the ENTIRE lit/shadowed background"
    assert np.all(alpha[subject_mask] == 255), "chrominance keying must keep the dark subject fully intact"

    # And RGB-distance keying (the old algorithm) cannot do both at ANY
    # tolerance - swept exhaustively, not just at the chrominance-derived one.
    rgb_pct = _rgb_distance_pct(rgb, _KEY_RGB)
    bg_worst = rgb_pct[bg_mask]
    subject_worst = rgb_pct[subject_mask]
    for t in np.arange(0.0, 100.01, 0.25):
        removed = rgb_pct <= t
        bg_fully_removed = removed[bg_mask].all()
        subject_fully_kept = not removed[subject_mask].any()
        assert not (bg_fully_removed and subject_fully_kept), (
            f"RGB-distance tolerance={t:.2f}% unexpectedly separated background "
            f"from subject (bg max={bg_worst.max():.2f}%, subject min={subject_worst.min():.2f}%) "
            f"- the adversarial construction is supposed to make this impossible"
        )
