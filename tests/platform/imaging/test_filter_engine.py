import numpy as np
import pytest
from PIL import Image

from src.platform.imaging.filters import (
    CORE_OPS,
    ColourOp,
    Cube,
    CubeError,
    FilterOpUnavailable,
    OpSpec,
    ParamSpec,
    SpatialOp,
    apply_filter,
    apply_lut,
    clipped_fraction,
    compile_lut,
    identity_lut,
    is_noop,
    lattice,
    merged_ops,
    parse_cube,
    resolve_params,
    split_steps,
    validate_steps,
)
from src.platform.imaging.filters.colour import COLOUR_OPS, pchip


def random_image(height=40, width=50, seed=4):
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)


def direct(rgb, steps):
    c = rgb.astype(np.float64).reshape(-1, 3) / 255
    for step in split_steps(steps)[0]:
        c = np.clip(COLOUR_OPS[step["op"]](resolve_params(CORE_OPS[step["op"]], step))(c), 0.0, 1.0)
    return c


SMOOTH_STEPS = [
    {"op": "white_balance", "temperature": 30, "tint": -10},
    {"op": "curves", "master": [[0, 0.02], [0.25, 0.22], [0.75, 0.8], [1, 0.98]]},
    {"op": "vibrance", "amount": 40},
    {"op": "fade", "black_lift": 5, "white_cap": 3},
]


def test_compiled_lut_stores_the_direct_result_at_every_node():
    lut = compile_lut(SMOOTH_STEPS)
    c = lattice(33)
    for step in SMOOTH_STEPS:
        c = np.clip(COLOUR_OPS[step["op"]](resolve_params(CORE_OPS[step["op"]], step))(c), 0.0, 1.0)
    assert lut.size == 33
    assert np.abs(lut.data.astype(np.float64) - c).max() < 1e-6


def test_applied_lut_is_close_to_evaluating_the_ops_per_pixel():
    image = random_image(60, 60)
    out = apply_lut(image, compile_lut(SMOOTH_STEPS))
    expected = np.floor(direct(image, SMOOTH_STEPS) * 255 + 0.5).reshape(image.shape)
    assert np.abs(out.astype(int) - expected).max() <= 3


def test_identity_lattice_leaves_every_level_untouched():
    levels = np.arange(256, dtype=np.uint8)
    image = np.stack([levels, levels[::-1], (levels * 7) % 256], axis=1).reshape(16, 16, 3)
    assert np.array_equal(apply_lut(image, identity_lut()), image)


def test_tone_map_matches_the_legacy_float_formula_on_a_known_pixel():
    c = np.array([[0.4, 0.5, 0.6]])
    out = COLOUR_OPS["tone"](resolve_params(CORE_OPS["tone"], {"op": "tone", "brightness": 50}))(c)
    assert out[0] == pytest.approx([0.6, 0.75, 0.9])


def test_intensity_zero_is_the_input_and_full_is_the_recipe():
    image = random_image()
    steps = [{"op": "tone", "contrast": 30}, {"op": "vignette", "amount": 60}]
    assert np.array_equal(apply_filter(image, steps, 0), image)
    full = apply_filter(image, steps, 100)
    half = apply_filter(image, steps, 50)
    assert not np.array_equal(full, image)
    assert np.abs(half.astype(int) - image).max() < np.abs(full.astype(int) - image).max()


def test_colour_intensity_is_a_blend_with_the_original():
    image = random_image()
    steps = [{"op": "white_balance", "temperature": 60}]
    half = apply_filter(image, steps, 50).astype(int)
    full = apply_filter(image, steps, 100).astype(int)
    assert np.abs(half - (image.astype(int) + full) / 2).max() <= 2


def test_alpha_is_never_touched_and_pil_images_round_trip():
    pixels = np.dstack([random_image(8, 8), np.full((8, 8), 77, dtype=np.uint8)])
    out = apply_filter(pixels, [{"op": "invert"}], 100)
    assert (out[..., 3] == 77).all()
    pil = apply_filter(Image.fromarray(pixels, "RGBA"), [{"op": "invert"}], 100)
    assert pil.mode == "RGBA" and np.array_equal(np.asarray(pil), out)
    rgb = apply_filter(Image.fromarray(random_image(4, 4), "RGB"), [{"op": "invert"}], 100)
    assert rgb.mode == "RGB"


def test_disabled_steps_are_skipped():
    image = random_image()
    assert np.array_equal(apply_filter(image, [{"op": "invert", "enabled": False}], 100), image)


def test_input_is_not_modified():
    image = random_image()
    before = image.copy()
    apply_filter(image, [{"op": "invert"}, {"op": "grain", "amount": 50}], 100)
    assert np.array_equal(image, before)


def test_spatial_steps_scale_with_intensity_and_vignette_darkens_corners_most():
    image = np.full((60, 80, 3), 200, dtype=np.uint8)
    out = apply_filter(image, [{"op": "vignette", "amount": 80, "midpoint": 10}], 100)
    assert out[0, 0, 0] < out[30, 40, 0] == 200
    lighter = apply_filter(image, [{"op": "vignette", "amount": -80, "midpoint": 10}], 100)
    assert lighter[0, 0, 0] > 200


def test_grain_is_deterministic_and_seeded():
    image = np.full((50, 50, 3), 128, dtype=np.uint8)
    steps = [{"op": "grain", "amount": 80, "seed": 1}]
    first = apply_filter(image, steps, 100)
    assert np.array_equal(first, apply_filter(image, steps, 100))
    assert not np.array_equal(first, apply_filter(image, [{"op": "grain", "amount": 80, "seed": 2}], 100))
    assert (first[..., 0] == first[..., 1]).all()


def test_lut_filter_compiles_at_the_cube_size_when_it_is_larger():
    size = 40
    m = size - 1
    index = np.arange(size ** 3)
    r, g, b = index % size, (index // size) % size, index // (size * size)
    data = np.stack([np.minimum(1, r / m * 0.9 + 0.05), np.minimum(1, g / m * 0.8 + 0.1 * b / m), 1 - b / m], axis=1)
    cube = Cube(size=size, data=data.astype(np.float32))
    lut = compile_lut([], cube)
    assert lut.size == 40
    assert np.array_equal(lut.data, cube.data)


def test_small_cube_is_upsampled_to_the_base_lattice_and_steps_run_on_top():
    text = "LUT_3D_SIZE 2\n" + "\n".join(
        f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)
    )
    cube = parse_cube(text)
    assert compile_lut([], cube).size == 33
    inverted = compile_lut([{"op": "invert"}], cube)
    assert np.allclose(inverted.data, 1 - lattice(33), atol=1e-6)


def test_cube_domain_remaps_the_input():
    text = (
        "LUT_3D_SIZE 2\nDOMAIN_MIN 0.2 0.2 0.2\nDOMAIN_MAX 0.8 0.8 0.8\n"
        + "\n".join(f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1))
    )
    out = compile_lut([], parse_cube(text))
    assert out.data[0].tolist() == [0, 0, 0]
    assert out.data[-1].tolist() == [1, 1, 1]
    mid = int(33 * 33 * 16 + 33 * 16 + 16)
    assert out.data[mid].tolist() == pytest.approx([0.5, 0.5, 0.5], abs=1e-6)


def test_cube_parser_accepts_comments_and_titles():
    cube = parse_cube('# note\nTITLE "demo"\n\nLUT_3D_SIZE 2 # trailing\n' + "0 0 0\n" * 8)
    assert cube.size == 2 and cube.title == "demo" and cube.data.shape == (8, 3)


@pytest.mark.parametrize(
    "text,message",
    [
        ("LUT_3D_SIZE 2\n0 0 0\n", "expected 8 data rows"),
        ("LUT_1D_SIZE 2\n", "LUT_1D_SIZE"),
        ("LUT_3D_SIZE 1\n", "outside 2..65"),
        ("LUT_3D_SIZE x\n", "one whole number"),
        ("FOO 1\n", "unknown keyword"),
        ("DOMAIN_MIN 0 0\n", "three numbers"),
    ],
)
def test_cube_parser_errors(text, message):
    with pytest.raises(CubeError, match=message):
        parse_cube(text)


def test_pchip_hits_its_points_and_extends_linearly():
    f = pchip([[0, 0.1], [0.5, 0.6], [1, 0.9]])
    assert f(np.array([0.0, 0.5, 1.0])).tolist() == pytest.approx([0.1, 0.6, 0.9])
    assert f(np.array([1.2]))[0] > 0.9
    assert f(np.array([-0.2]))[0] < 0.1


def test_pchip_is_monotone_through_a_flat_section():
    f = pchip([[0, 0], [0.3, 0.6], [0.6, 0.6], [1, 1]])
    xs = np.linspace(0, 1, 200)
    assert (np.diff(f(xs)) >= -1e-12).all()


class Warmth(ColourOp):
    def map(self, rgb, params):
        out = rgb.copy()
        out[:, 0] = out[:, 0] + params["amount"] / 200
        return out


class Flatten(SpatialOp):
    def apply(self, image, params, amount):
        return image * (1 - amount)


PLUGIN_OPS = merged_ops(
    {
        "demo.warmth": OpSpec(
            "demo.warmth", "Warmth", "colour", (ParamSpec("amount", "Amount", "int", 0, 0, 100),), "plugin", "demo", "ops.py:Warmth"
        ),
        "demo.flatten": OpSpec("demo.flatten", "Flatten", "spatial", (), "plugin", "demo", "ops.py:Flatten"),
    }
)


def test_plugin_colour_op_folds_into_the_lut():
    lut = compile_lut([{"op": "demo.warmth", "amount": 20}], None, 100, PLUGIN_OPS, {"demo.warmth": Warmth()})
    expected = np.clip(lattice(33) + np.array([0.1, 0, 0]), 0, 1)
    assert np.abs(lut.data.astype(np.float64) - expected).max() < 1e-6


def test_plugin_spatial_op_receives_the_intensity_as_its_amount():
    image = np.full((4, 4, 3), 200, dtype=np.uint8)
    out = apply_filter(image, [{"op": "demo.flatten"}], 50, ops=PLUGIN_OPS, impls={"demo.flatten": Flatten()})
    assert out[0, 0, 0] == 100


def test_plugin_op_without_an_implementation_is_refused_by_name():
    with pytest.raises(FilterOpUnavailable, match="demo.warmth"):
        compile_lut([{"op": "demo.warmth"}], None, 100, PLUGIN_OPS, {})
    with pytest.raises(FilterOpUnavailable, match="demo.flatten"):
        apply_filter(random_image(4, 4), [{"op": "demo.flatten"}], 100, ops=PLUGIN_OPS)


def test_clipped_fraction_flags_blown_ranges_but_not_gentle_looks():
    assert clipped_fraction([{"op": "tone", "contrast": 10}]) == 0
    assert clipped_fraction([{"op": "exposure", "stops": 2}]) > 0.05


class TestValidation:
    def rules(self, steps, ops=None):
        return [issue.rule for issue in validate_steps(steps, ops)]

    def test_valid_steps_have_no_issues(self):
        assert validate_steps([{"op": "tone", "contrast": 10}, {"op": "vignette", "amount": 5}]) == []

    def test_unknown_op_and_param(self):
        assert self.rules([{"op": "nope"}]) == ["op_unknown"]
        assert self.rules([{"op": "tone", "bright": 1}]) == ["param_unknown"]

    def test_out_of_range_and_wrong_type(self):
        assert self.rules([{"op": "tone", "contrast": 101}]) == ["param_range"]
        assert self.rules([{"op": "tone", "contrast": 1.5}]) == ["schema"]
        assert self.rules([{"op": "tone", "contrast": "10"}]) == ["schema"]
        assert self.rules([{"op": "tone", "contrast": True}]) == ["schema"]
        assert self.rules([{"op": "exposure", "stops": 2.5}]) == ["param_range"]

    def test_curve_points(self):
        assert self.rules([{"op": "curves", "master": [[0, 0]]}]) == ["curve_points"]
        assert self.rules([{"op": "curves", "master": [[0.5, 0], [0.5, 1]]}]) == ["curve_points"]
        assert self.rules([{"op": "curves", "master": [[0, 0], [1, 1.5]]}]) == ["curve_points"]
        assert self.rules([{"op": "curves", "master": [[0, 0, 1], [1, 1]]}]) == ["curve_points"]
        assert self.rules([{"op": "curves", "master": [[i / 17, 0.5] for i in range(18)]}]) == ["curve_points"]

    def test_colour_after_spatial_is_rejected(self):
        assert self.rules([{"op": "vignette"}, {"op": "tone"}]) == ["step_order"]

    def test_step_limit(self):
        assert "step_count" in self.rules([{"op": "invert"}] * 65)

    def test_steps_must_be_a_list_of_objects(self):
        assert self.rules("tone") == ["schema"]
        assert self.rules(["tone"]) == ["schema"]
        assert self.rules([{}]) == ["schema"]

    def test_enabled_must_be_boolean(self):
        assert self.rules([{"op": "invert", "enabled": "no"}]) == ["schema"]

    def test_plugin_ops_validate_only_when_declared(self):
        assert self.rules([{"op": "demo.warmth", "amount": 5}]) == ["op_unknown"]
        assert self.rules([{"op": "demo.warmth", "amount": 5}], PLUGIN_OPS) == []
        assert self.rules([{"op": "demo.warmth", "amount": 500}], PLUGIN_OPS) == ["param_range"]

    def test_noop_detection(self):
        assert is_noop(CORE_OPS["tone"], {"op": "tone"})
        assert not is_noop(CORE_OPS["tone"], {"op": "tone", "hue": 3})
        assert not is_noop(CORE_OPS["grayscale"], {"op": "grayscale"})
        assert is_noop(CORE_OPS["curves"], {"op": "curves", "master": [[0, 0], [1, 1]]})
