from __future__ import annotations

import pytest
from PIL import Image

from src.platform.media_tools import (
    InvalidToolParams,
    ToolCancelled,
    ToolContext,
    probe,
    run_chain,
    validate_params,
)
from src.platform.media_tools.ops import slice as slice_op
from src.platform.media_tools.ops.slice import MAX_SLICE_CELLS

BG = (240, 230, 210)


def sheet(path, size=(200, 100), color=BG, mode="RGB"):
    image = Image.new(mode, size, color)
    image.paste((200, 30, 30, 255) if mode == "RGBA" else (200, 30, 30), (20, 10, 80, 70))
    image.paste((30, 30, 200, 255) if mode == "RGBA" else (30, 30, 200), (120, 20, 180, 90))
    image.save(path)
    return probe(path)


def cell(x=0.0, y=0.0, w=0.5, h=1.0, **extra):
    return {"x": x, "y": y, "w": w, "h": h, **extra}


def run(media, ctx, rects, **params):
    spec = slice_op.spec()
    return run_chain([(spec, {"rects": rects, **params})], [media], ctx)


def pixels(media):
    with Image.open(media.path) as image:
        return image.convert("RGBA")


@pytest.fixture
def source(tmp_path):
    return sheet(tmp_path / "sheet.png")


class TestRun:
    def test_makes_one_image_per_cell_in_order(self, source, ctx):
        outputs = run(source, ctx, [cell(0, 0, 0.5, 1), cell(0.5, 0, 0.5, 1)])
        assert [(m.kind, m.width, m.height) for m in outputs] == [("image", 100, 100)] * 2
        assert pixels(outputs[0]).getpixel((50, 40))[:3] == (200, 30, 30)
        assert pixels(outputs[1]).getpixel((50, 50))[:3] == (30, 30, 200)

    def test_rect_math_maps_fractions_to_exact_pixels(self, source, ctx):
        (out,) = run(source, ctx, [cell(0.05, 0.05, 0.3, 0.6)])
        assert (out.width, out.height) == (60, 60)
        assert pixels(out).getpixel((0, 0))[:3] == BG
        assert pixels(out).getpixel((10, 5))[:3] == (200, 30, 30)

    def test_overlapping_cells_are_allowed(self, source, ctx):
        outputs = run(source, ctx, [cell(0, 0, 0.7, 1), cell(0.3, 0, 0.7, 1)])
        assert [m.width for m in outputs] == [140, 140]

    def test_trim_shrinks_to_the_content(self, source, ctx):
        (out,) = run(source, ctx, [cell(0, 0, 0.5, 1)], trim=True)
        assert (out.width, out.height) == (60, 60)
        assert pixels(out).getpixel((0, 0))[:3] == (200, 30, 30)

    def test_trim_ignores_background_noise_inside_the_tolerance(self, tmp_path, ctx):
        image = Image.new("RGB", (200, 100), BG)
        image.paste((200, 30, 30), (20, 10, 80, 70))
        image.putpixel((150, 50), (BG[0] - 8, BG[1], BG[2]))
        image.save(tmp_path / "noisy.png")
        media = probe(tmp_path / "noisy.png")
        (tight,) = run(media, ctx, [cell(0, 0, 1, 1)], trim=True, tolerance=12)
        (loose,) = run(media, ctx, [cell(0, 0, 1, 1)], trim=True, tolerance=4)
        assert (tight.width, tight.height) == (60, 60)
        assert loose.width > tight.width

    def test_a_cell_with_no_content_keeps_its_whole_area_when_trimmed(self, source, ctx):
        (out,) = run(source, ctx, [cell(0.92, 0, 0.08, 1)], trim=True)
        assert (out.width, out.height) == (16, 100)

    def test_padding_surrounds_the_cell_with_the_sheet_colour(self, source, ctx):
        (out,) = run(source, ctx, [cell(0, 0, 0.5, 1)], trim=True, padding=5)
        assert (out.width, out.height) == (70, 70)
        assert pixels(out).getpixel((0, 0))[:3] == BG
        assert pixels(out).getpixel((5, 5))[:3] == (200, 30, 30)

    def test_square_aspect_pads_and_never_crops(self, source, ctx):
        (out,) = run(source, ctx, [cell(0.5, 0, 0.5, 1)], trim=True, aspect="square")
        assert out.width == out.height == 70
        assert pixels(out).getpixel((35, 35))[:3] == (30, 30, 200)

    def test_portrait_aspect_is_two_by_three(self, source, ctx):
        (out,) = run(source, ctx, [cell(0, 0, 1, 0.5)], aspect="portrait")
        assert (out.width, out.height) == (200, 300)
        (tall,) = run(source, ctx, [cell(0, 0, 0.2, 1)], aspect="portrait")
        assert (tall.width, tall.height) == (67, 100)

    def test_explicit_fill_colour(self, source, ctx):
        (out,) = run(source, ctx, [cell(0, 0, 0.5, 1)], trim=True, padding=3, fill="#00ff00")
        assert pixels(out).getpixel((0, 0))[:3] == (0, 255, 0)

    def test_transparent_fill_makes_an_alpha_image(self, source, ctx):
        (out,) = run(source, ctx, [cell(0, 0, 0.5, 1)], trim=True, padding=3, fill="transparent")
        assert pixels(out).getpixel((0, 0))[3] == 0
        assert out.has_alpha is True

    def test_a_transparent_sheet_trims_on_alpha_and_pads_with_transparency(self, tmp_path, ctx):
        media = sheet(tmp_path / "alpha.png", color=(0, 0, 0, 0), mode="RGBA")
        (out,) = run(media, ctx, [cell(0, 0, 0.5, 1)], trim=True, padding=4)
        assert (out.width, out.height) == (68, 68)
        assert pixels(out).getpixel((0, 0))[3] == 0
        assert pixels(out).getpixel((4, 4))[3] == 255

    def test_opaque_output_has_no_alpha_channel(self, source, ctx):
        (out,) = run(source, ctx, [cell(0, 0, 0.5, 1)])
        assert out.has_alpha is False

    def test_progress_reports_each_cell(self, source, tmp_path):
        seen = []
        ctx = ToolContext(tmp_path / "scratch", progress=lambda fraction, label: seen.append((fraction, label)))
        run(source, ctx, [cell(0, 0, 0.5, 1), cell(0.5, 0, 0.5, 1)])
        labels = [label for _, label in seen if label.startswith("Cell")]
        assert "Cell 1 of 2" in labels and "Cell 2 of 2" in labels
        assert (0.0, "Cell 1 of 2") in seen and (0.5, "Cell 2 of 2") in seen and (1.0, "Cell 2 of 2") in seen
        fractions = [fraction for fraction, label in seen if label.startswith("Cell")]
        assert fractions == sorted(fractions)
        assert 0.5 in fractions

    def test_cancel_between_cells_removes_the_cells_already_written(self, source, tmp_path):
        scratch = tmp_path / "scratch"
        calls = {"n": 0}

        def cancelled():
            calls["n"] += 1
            return calls["n"] > 3

        ctx = ToolContext(scratch, is_cancelled=cancelled)
        with pytest.raises(ToolCancelled):
            run(source, ctx, [cell(0, 0, 0.25, 1), cell(0.25, 0, 0.25, 1), cell(0.5, 0, 0.25, 1), cell(0.75, 0, 0.25, 1)])
        assert [p for p in scratch.glob("*.png")] == []

    def test_a_failure_part_way_removes_the_cells_already_written(self, source, ctx, monkeypatch):
        written = []
        real = slice_op.save_image

        def flaky(image, dest):
            if written:
                raise OSError("disk full")
            written.append(dest)
            return real(image, dest)

        monkeypatch.setattr(slice_op, "save_image", flaky)
        with pytest.raises(OSError):
            run(source, ctx, [cell(0, 0, 0.5, 1), cell(0.5, 0, 0.5, 1)])
        assert not written[0].exists()


class TestValidation:
    @pytest.mark.parametrize(
        "rects",
        [
            [],
            "auto",
            None,
            [cell()] * (MAX_SLICE_CELLS + 1),
            ["nope"],
            [{"x": 0, "y": 0, "w": 0.5}],
            [cell(w=0)],
            [cell(h=-0.1)],
            [cell(x=-0.01)],
            [cell(x=0.6, w=0.5)],
            [cell(y=0.9, h=0.2)],
            [cell(x=float("nan"))],
            [cell(w=float("inf"))],
            [cell(x=True)],
            [cell(x="0.1")],
            [cell(name=5)],
            [cell(name="n" * 121)],
        ],
    )
    def test_bad_rects_are_refused_with_the_field(self, rects):
        with pytest.raises(InvalidToolParams) as raised:
            validate_params(slice_op.spec(), {"rects": rects})
        assert raised.value.field == "rects"

    def test_the_limit_is_inclusive(self):
        resolved = validate_params(slice_op.spec(), {"rects": [cell(0, 0, 0.01, 0.01)] * MAX_SLICE_CELLS})
        assert len(resolved["rects"]) == MAX_SLICE_CELLS

    def test_rects_are_required(self):
        with pytest.raises(InvalidToolParams) as raised:
            validate_params(slice_op.spec(), {})
        assert raised.value.field == "rects"

    @pytest.mark.parametrize(
        "params, field",
        [
            ({"aspect": "wide"}, "aspect"),
            ({"padding": -1}, "padding"),
            ({"padding": 513}, "padding"),
            ({"fill": "red"}, "fill"),
            ({"trim": True, "tolerance": 65}, "tolerance"),
            ({"nope": 1}, "nope"),
        ],
    )
    def test_bad_scalars_are_refused(self, params, field):
        with pytest.raises(InvalidToolParams) as raised:
            validate_params(slice_op.spec(), {"rects": [cell()], **params})
        assert raised.value.field == field

    def test_defaults(self):
        resolved = validate_params(slice_op.spec(), {"rects": [cell()]})
        assert (resolved["trim"], resolved["padding"], resolved["aspect"], resolved["fill"]) == (False, 0, "keep", "auto")

    def test_a_cell_under_eight_pixels_is_refused_at_run(self, source, ctx):
        with pytest.raises(InvalidToolParams) as raised:
            run(source, ctx, [cell(0, 0, 0.03, 1)])
        assert raised.value.field == "rects"
        assert "Cell 1" in raised.value.message

    def test_output_pixel_limit_is_refused(self, source, ctx, monkeypatch):
        monkeypatch.setattr(slice_op, "MAX_CELL_OUTPUT_PIXELS", 1000)
        with pytest.raises(InvalidToolParams) as raised:
            run(source, ctx, [cell(0, 0, 1, 1)])
        assert "megapixels" in raised.value.message

    def test_padding_counts_toward_the_output_limit(self, source, ctx, monkeypatch):
        monkeypatch.setattr(slice_op, "MAX_CELL_OUTPUT_PIXELS", 110 * 110)
        run(source, ctx, [cell(0, 0, 0.5, 1)], padding=5)
        with pytest.raises(InvalidToolParams):
            run(source, ctx, [cell(0, 0, 0.5, 1)], padding=20)

    def test_total_output_limit_is_refused(self, source, ctx, monkeypatch):
        monkeypatch.setattr(slice_op, "MAX_TOTAL_OUTPUT_PIXELS", 15000)
        with pytest.raises(InvalidToolParams):
            run(source, ctx, [cell(0, 0, 0.5, 1), cell(0.5, 0, 0.5, 1)])

    def test_an_oversized_source_is_refused_without_a_path(self, source, ctx, monkeypatch):
        monkeypatch.setattr(slice_op, "MAX_SOURCE_PIXELS", 100)
        with pytest.raises(Exception) as raised:
            run(source, ctx, [cell()])
        assert str(source.path.parent) not in str(raised.value)

    def test_names_are_cleaned_of_separators_and_control_characters(self):
        cells = slice_op.parse_cells([cell(name="../a\\b\x00c\n"), cell(name="  "), cell()])
        assert [item.name for item in cells] == ["..abc", None, None]

    def test_spec_is_a_terminal_editor_only_image_fan_out(self):
        spec = slice_op.spec()
        assert (spec.accepts, spec.produces, spec.cardinality) == (("image",), "image", "one_to_many")
        assert spec.scopes == ("editor",) and spec.editor == "slice"
        assert spec.selection == {"min": 1, "max": 1}

    def test_still_returns_the_picture_unchanged(self):
        image = Image.new("RGB", (4, 4))
        assert slice_op.spec().impl.still(image, {}, None) is image
