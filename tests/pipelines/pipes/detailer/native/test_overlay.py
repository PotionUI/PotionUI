import math

import numpy as np
from PIL import Image

from src.pipelines.pipes.detailer.native.mask import build_face_mask
from src.pipelines.pipes.detailer.native.overlay import (
    DIM_STRENGTH,
    SIGNAL,
    OverlayFace,
    face_label,
    place_label,
    render_overlay,
    step_suffix,
    wrap_label,
)

BASE_COLOUR = (160, 140, 120)
BOX = (100, 100, 260, 260)
FAR_BOX = (320, 100, 460, 240)


def _oval_mask(size=(160, 160)):
    cx, cy = size[0] / 2, size[1] / 2
    polygon = [
        (cx + 0.38 * size[0] * math.cos(a), cy + 0.42 * size[1] * math.sin(a))
        for a in np.linspace(0, 2 * math.pi, 32, endpoint=False)
    ]
    return build_face_mask(size, polygon=polygon, dilate=6, feather=10)


def _base(size=(512, 512)):
    return Image.new("RGB", size, BASE_COLOUR)


def _dimmed_value():
    return tuple(int(round(c * (1.0 - DIM_STRENGTH))) for c in BASE_COLOUR)


def test_face_label_shows_the_confidence_when_present():
    assert face_label(1, 0.9312, (412, 412), "oval") == "FACE 1 · 0.93 · 412x412 · OVAL"


def test_face_label_shows_a_dash_when_the_backend_has_no_score():
    assert face_label(2, None, (256, 256), "box") == "FACE 2 · - · 256x256 · BOX"


def test_step_suffix_appends_the_live_step():
    assert step_suffix("FACE 1", 4, 12) == "FACE 1 · step 4/12"
    assert step_suffix("FACE 1", 0, 0) == "FACE 1"


def test_wrap_label_splits_on_the_separator():
    assert wrap_label("FACE 1 · 0.93 · 110x110 · OVAL") == [
        "FACE 1 · 0.93", "110x110 · OVAL",
    ]
    assert wrap_label("FACE 1") == ["FACE 1"]


def test_place_label_prefers_above_the_box():
    assert place_label((512, 512), BOX, (120, 20)) == (100, 100 - 20 - 4)


def test_place_label_flips_below_when_there_is_no_room_above():
    top_box = (100, 0, 260, 160)
    assert place_label((512, 512), top_box, (120, 20)) == (100, 160 + 4)


def test_place_label_moves_aside_when_neighbours_block_above_and_below():
    above = (60, 40, 300, 98)
    below = (60, 262, 300, 340)
    placed = place_label((512, 512), BOX, (120, 20), obstacles=(above, below))
    assert placed == (260 + 4, 100)


def test_place_label_gives_up_when_nothing_fits():
    crowded = [(0, 0, 512, 512)]
    assert place_label((512, 512), BOX, (120, 20), obstacles=crowded) is None


def test_light_mode_has_no_dim_and_no_pill():
    face = OverlayFace(box=BOX, mask=_oval_mask(), tag="1")
    other = OverlayFace(box=FAR_BOX, mask=_oval_mask((140, 140)), tag="2")
    out = np.asarray(render_overlay(_base(), [face, other]))

    assert tuple(out[10, 10]) == BASE_COLOUR
    assert tuple(out[180, 180]) == BASE_COLOUR
    band = out[: BOX[1] - 2, BOX[0]: BOX[2]]
    assert band.min() > 100


def test_light_brackets_are_thin_and_translucent():
    face = OverlayFace(box=BOX, mask=_oval_mask(), tag="1")
    out = np.asarray(render_overlay(_base(), [face]))

    bracket = out[BOX[1] + 2, BOX[2] - 10]
    assert tuple(bracket) != BASE_COLOUR
    assert tuple(bracket) != SIGNAL
    assert int(bracket[2]) > BASE_COLOUR[2]
    assert tuple(out[BOX[1] + 2, (BOX[0] + BOX[2]) // 2]) == BASE_COLOUR


def test_index_tag_sits_inside_the_box():
    face = OverlayFace(box=BOX, mask=_oval_mask(), tag="1")
    out = np.asarray(render_overlay(_base(), [face]))

    inside = out[BOX[1] + 2: BOX[1] + 24, BOX[0] + 2: BOX[0] + 24]
    assert inside.min() < 60


def test_focus_mode_dims_the_background_but_never_another_face():
    focused = OverlayFace(box=BOX, mask=_oval_mask(), label="", focus=True)
    quiet = OverlayFace(box=FAR_BOX, mask=_oval_mask((140, 140)))
    out = np.asarray(render_overlay(_base(), [focused, quiet]))

    assert tuple(out[180, 180]) == BASE_COLOUR
    assert tuple(out[10, 10]) == _dimmed_value()
    assert tuple(out[170, 390]) == BASE_COLOUR


def test_a_quiet_face_draws_no_marker_at_all():
    focused = OverlayFace(box=BOX, mask=_oval_mask(), label="", focus=True)
    quiet = OverlayFace(box=FAR_BOX, mask=_oval_mask((140, 140)))
    out = np.asarray(render_overlay(_base(), [focused, quiet]))

    region = out[FAR_BOX[1]:FAR_BOX[3], FAR_BOX[0]:FAR_BOX[2]]
    assert not np.any(np.all(region == np.array(SIGNAL), axis=-1))
    assert region.min() >= min(_dimmed_value())
    assert tuple(out[170, 390]) == BASE_COLOUR


def test_focus_brackets_draw_only_inside_the_padded_box():
    focused = OverlayFace(box=BOX, mask=_oval_mask(), label="", focus=True)
    out = np.asarray(render_overlay(_base(), [focused]))

    outside = np.ones(out.shape[:2], dtype=bool)
    outside[BOX[1]:BOX[3], BOX[0]:BOX[2]] = False
    assert np.array_equal(np.unique(out[outside].reshape(-1, 3), axis=0), np.array([_dimmed_value()]))
    assert tuple(out[BOX[1] + 3, BOX[0] + 6]) == SIGNAL


def test_focus_label_pill_moves_below_a_neighbouring_face():
    focused = OverlayFace(box=BOX, mask=_oval_mask(), label="FACE 1 · 0.93 · 160x160 · OVAL", focus=True)
    above = OverlayFace(box=(60, 40, 300, 98), mask=_oval_mask((240, 58)), tag="2")
    out = np.asarray(render_overlay(_base(), [focused, above]))

    assert out[264:288, 100:260].min() < 40
    assert out[76:96, 100:260].min() > 40


def test_done_face_shows_a_check_tag_instead_of_an_index():
    done = OverlayFace(box=BOX, mask=_oval_mask(), tag="1", done=True)
    out = np.asarray(render_overlay(_base(), [done]))

    corner = out[BOX[1] + 2: BOX[1] + 26, BOX[0] + 2: BOX[0] + 26]
    assert corner.min() < 60
    assert np.any(np.all(corner == np.array(SIGNAL), axis=-1))


def test_skipped_face_is_grey_and_never_dims_the_frame():
    skipped = OverlayFace(box=(300, 300, 360, 360), tag="skip", skipped=True)
    out = np.asarray(render_overlay(_base(), [skipped]))

    assert tuple(out[330, 330]) == BASE_COLOUR
    assert not np.any(np.all(out == np.array(SIGNAL), axis=-1))
