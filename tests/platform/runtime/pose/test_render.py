import numpy as np

from src.platform.runtime.pose.layout import (
    BODY_COLORS,
    BODY_LIMBS,
    FACE,
    L_EAR,
    L_ELBOW,
    L_EYE,
    L_SHOULDER,
    L_WRIST,
    LEFT_HAND,
    R_EAR,
    R_ELBOW,
    R_EYE,
    R_SHOULDER,
    R_WRIST,
    RIGHT_HAND,
)
from src.platform.runtime.pose.render import HAND_COLORS, RenderOptions, render_pose, render_poses
from tests.fixtures.pose_skeletons import skeleton

W, H = 512, 512


def pixel(canvas, point):
    return tuple(int(v) for v in canvas[int(round(point[1])), int(round(point[0]))])


def midpoint(pose, a, b):
    return (pose[a, :2] + pose[b, :2]) / 2


def test_black_rgb_canvas_of_the_requested_size():
    canvas = render_pose(skeleton(), 640, 360)
    assert canvas.shape == (360, 640, 3)
    assert canvas.dtype == np.uint8


def test_empty_pose_renders_black():
    canvas = render_pose(np.zeros((134, 3), dtype=np.float32), W, H)
    assert canvas.max() == 0


def test_limb_is_filled_with_the_dimmed_palette_colour_of_its_index():
    pose = skeleton()
    canvas = render_pose(pose, W, H)
    index = BODY_LIMBS.index((3, 4))
    expected = tuple(int(c * 0.6) for c in BODY_COLORS[index])
    assert pixel(canvas, midpoint(pose, 3, 4)) == expected


def test_joint_dot_uses_the_full_palette_colour_of_the_joint_index():
    pose = skeleton(hands=False, face=False)
    canvas = render_pose(pose, W, H)
    assert pixel(canvas, pose[R_WRIST, :2]) == BODY_COLORS[R_WRIST]
    assert pixel(canvas, pose[R_ELBOW, :2]) == BODY_COLORS[R_ELBOW]


def test_ear_to_shoulder_pairs_are_not_limbs():
    assert len(BODY_LIMBS) == 17
    assert (R_EAR, R_SHOULDER) not in BODY_LIMBS and (L_EAR, L_SHOULDER) not in BODY_LIMBS
    assert len(BODY_COLORS) == 18


def test_low_confidence_point_drops_its_dot_and_limbs():
    pose = skeleton(hands=False, face=False)
    pose[R_WRIST, 2] = 0.2
    canvas = render_pose(pose, W, H)
    assert pixel(canvas, pose[R_WRIST, :2]) == (0, 0, 0)
    assert pixel(canvas, midpoint(pose, 3, 4)) == (0, 0, 0)
    assert pixel(canvas, pose[R_ELBOW, :2]) == BODY_COLORS[R_ELBOW]


def test_confidence_cut_follows_the_option():
    pose = skeleton(hands=False, face=False)
    pose[R_WRIST, 2] = 0.5
    strict = render_pose(pose, W, H, RenderOptions(min_confidence=0.6))
    loose = render_pose(pose, W, H, RenderOptions(min_confidence=0.4))
    assert pixel(strict, pose[R_WRIST, :2]) == (0, 0, 0)
    assert pixel(loose, pose[R_WRIST, :2]) == BODY_COLORS[R_WRIST]


def test_hand_edges_use_the_hue_ramp_and_dots_are_blue():
    pose = skeleton(face=False)
    canvas = render_pose(pose, W, H)
    hand = pose[RIGHT_HAND]
    assert pixel(canvas, hand[2, :2]) == (0, 0, 255)
    assert HAND_COLORS[0] == (255, 0, 0)
    assert len(HAND_COLORS) == 20


def test_face_points_are_white_and_include_the_two_eyes():
    pose = skeleton(hands=False)
    canvas = render_pose(pose, W, H)
    assert pixel(canvas, pose[FACE.start, :2]) == (255, 255, 255)
    assert pixel(canvas, pose[R_EYE, :2]) == (255, 255, 255)
    assert pixel(canvas, pose[L_EYE, :2]) == (255, 255, 255)


def test_flags_switch_whole_sections_off():
    pose = skeleton(angles={R_ELBOW: 180.0, R_WRIST: 180.0, L_ELBOW: 0.0, L_WRIST: 0.0})
    off = render_pose(pose, W, H, RenderOptions(body=False, hands=False, face=False))
    assert off.max() == 0
    no_body = render_pose(pose, W, H, RenderOptions(body=False))
    assert pixel(no_body, midpoint(pose, 3, 4)) == (0, 0, 0)
    assert pixel(no_body, pose[FACE.start, :2]) == (255, 255, 255)
    no_hands = render_pose(pose, W, H, RenderOptions(hands=False))
    assert pixel(no_hands, pose[LEFT_HAND.start + 19, :2]) == (0, 0, 0)
    assert pixel(render_pose(pose, W, H), pose[LEFT_HAND.start + 19, :2]) == (0, 0, 255)
    no_face = render_pose(pose, W, H, RenderOptions(face=False))
    assert pixel(no_face, pose[FACE.start, :2]) == (0, 0, 0)


def test_line_widths_scale_with_the_short_side():
    pose = skeleton(hands=False, face=False, neck=(300.0, 200.0))
    small = render_pose(pose, 512, 512)
    big_pose = pose.copy()
    big_pose[:, :2] *= 2
    big = render_pose(big_pose, 1024, 1024)
    assert int((small > 0).any(axis=2).sum()) * 3 < int((big > 0).any(axis=2).sum())


def test_line_scale_option_thickens_dots():
    pose = skeleton(hands=False, face=False)
    thin = render_pose(pose, W, H, RenderOptions(line_scale=1.0))
    thick = render_pose(pose, W, H, RenderOptions(line_scale=2.0))
    assert int((thick > 0).any(axis=2).sum()) > int((thin > 0).any(axis=2).sum())


def test_off_canvas_points_do_not_crash():
    pose = skeleton(neck=(5000.0, -3000.0))
    assert render_pose(pose, W, H).shape == (H, W, 3)
    huge = skeleton()
    huge[R_WRIST, :2] = (1e12, -1e12)
    assert render_pose(huge, W, H).shape == (H, W, 3)


def test_several_people_share_one_canvas():
    left = skeleton(neck=(120.0, 150.0), hands=False, face=False)
    right = skeleton(neck=(380.0, 150.0), hands=False, face=False)
    canvas = render_poses([left, right], W, H)
    assert pixel(canvas, left[R_WRIST, :2]) == BODY_COLORS[R_WRIST]
    assert pixel(canvas, right[R_WRIST, :2]) == BODY_COLORS[R_WRIST]
    assert render_poses([], W, H).max() == 0
