"""Scale an image and place it on a fixed canvas - the "zoom out so an
animator has headroom" step: shrink the subject and give it empty margin to
move/rotate/scale into on a spritesheet or animation frame.

``scale_percent`` definition (pinned by
``tests/pipelines/pipes/canvas_fit/test_canvas_fit.py::test_scale_percent_definition``):
it is a percentage of the CANVAS's SHORT side that the (aspect-preserved,
resized) image's LONG side should occupy.

    canvas_short = min(width, height)
    target_long  = canvas_short * (scale_percent / 100)
    factor       = target_long / max(orig_w, orig_h)
    new_w, new_h = orig_w * factor, orig_h * factor   # aspect ratio preserved

``scale_percent=100`` (the default) makes the image's long side exactly fill
the canvas's short side - a square image on a square canvas fills it
entirely; a landscape image on a square canvas is inscribed so its top/bottom
touch the canvas edges with side margins left over. Values below 100 leave
proportionally more headroom around the image; values above 100 let the
image overflow past the canvas on its long axis (still centered/anchored per
``anchor`` - PIL simply clips whatever falls outside the canvas, so this
never raises).

``anchor`` places the (possibly overflowing) resized image within the
canvas: any of the 4 edges, the 4 corners, or ``"center"`` (default).
``fill``: ``"transparent"`` keeps alpha (the resized image's own alpha, or
fully opaque if it has none) on an otherwise-transparent canvas; any other
value is parsed as a hex colour and the output is a fully opaque RGB image
composited onto that colour.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import Icon, ProgressGenerationOutput
from src.pipelines.pipes._shared.imaging.io import as_image_list

#: Every accepted spelling of an anchor position. Compound anchors are a
#: hyphen/underscore-joined pair of one entry from each axis (e.g.
#: "top-left"); a lone entry pins one axis and centers the other.
_ANCHOR_H = {"left": 0.0, "right": 1.0, "center": 0.5}
_ANCHOR_V = {"top": 0.0, "bottom": 1.0, "center": 0.5}

#: Exact literals the ImageTools preset's anchor select ships (underscore,
#: not hyphen - `parse_anchor` normalizes either, but this is what a
#: `PipeConfigSpec.choices` validation check and the frontend picker compare
#: against, so the spelling here is load-bearing).
ANCHOR_CHOICES = [
    "center", "top", "bottom", "left", "right",
    "top_left", "top_right", "bottom_left", "bottom_right",
]


def parse_anchor(anchor: str) -> Tuple[float, float]:
    """``anchor`` -> ``(h, v)`` fractions in ``[0, 1]`` -- 0 is the left/top
    edge, 1 the right/bottom edge, 0.5 centered on that axis."""
    normalized = (anchor or "center").strip().lower().replace("_", "-")
    parts = [p for p in normalized.split("-") if p]
    if not parts:
        parts = ["center"]

    h, v = 0.5, 0.5
    h_set = v_set = False
    for part in parts:
        if part in _ANCHOR_H and part != "center" and not h_set:
            h = _ANCHOR_H[part]
            h_set = True
        elif part in _ANCHOR_V and part != "center" and not v_set:
            v = _ANCHOR_V[part]
            v_set = True
        elif part == "center":
            continue
        else:
            raise ValueError(f"canvas_fit: unknown anchor {anchor!r}")
    return h, v


#: The `fill` field is a plain string (the field-type registry has no colour
#: picker type this preset could use), so it is parsed defensively - only
#: the two documented shapes are accepted, anything else is a clear
#: `ValueError` naming the problem rather than a silent fallback to some
#: default colour.
_HEX_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_fill(fill: str) -> Optional[Tuple[int, int, int]]:
    """`fill` -> `None` for `"transparent"`, or an `(r, g, b)` triple for a
    `#RRGGBB` hex string (`#` optional). Raises `ValueError` on anything
    else - no named colours, no shorthand `#RGB`, no `rgb(...)` - those are
    not part of this field's contract."""
    normalized = (fill or "").strip()
    if normalized.lower() == "transparent":
        return None

    match = _HEX_COLOR_RE.match(normalized)
    if not match:
        raise ValueError(
            f"canvas_fit: invalid 'fill' {fill!r} - expected 'transparent' or a "
            f"'#RRGGBB' hex colour"
        )
    hex_digits = match.group(1)
    return tuple(int(hex_digits[i:i + 2], 16) for i in (0, 2, 4))


def compute_scaled_size(orig_w: int, orig_h: int, canvas_w: int, canvas_h: int,
                        scale_percent: float) -> Tuple[int, int]:
    """See the module docstring for the exact `scale_percent` definition."""
    if orig_w <= 0 or orig_h <= 0:
        raise ValueError("canvas_fit: source image has zero size")

    canvas_short = min(canvas_w, canvas_h)
    target_long = canvas_short * (scale_percent / 100.0)
    orig_long = max(orig_w, orig_h)
    factor = target_long / orig_long

    new_w = max(1, round(orig_w * factor))
    new_h = max(1, round(orig_h * factor))
    return new_w, new_h


class CanvasFitPipe(BasePipe):
    name = "canvas_fit"
    description = "Scale an image (aspect-preserved) and place it on a fixed canvas"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "width": 1024,
            "height": 1024,
            "scale_percent": 100.0,
            "anchor": "center",
            "fill": "transparent",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("width", int, 1024, "Canvas width (px)", required=True, min_value=1),
            PipeConfigSpec("height", int, 1024, "Canvas height (px)", required=True, min_value=1),
            PipeConfigSpec("scale_percent", float, 100.0,
                           "Percentage of the CANVAS's short side that the image's long side should occupy",
                           required=False, min_value=0.0),
            PipeConfigSpec("anchor", str, "center", "Where to place the scaled image on the canvas",
                           required=False, choices=ANCHOR_CHOICES),
            PipeConfigSpec("fill", str, "transparent",
                           "'transparent' (keep alpha) or a hex colour (opaque RGB output)",
                           required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Source image(s) to scale and place", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Fixed-size canvas(es) with the scaled image placed on it", is_array=True),
        ]

    @staticmethod
    def _place_one(image: Image.Image, width: int, height: int, scale_percent: float,
                   h_anchor: float, v_anchor: float, color: Optional[Tuple[int, int, int]]) -> Image.Image:
        orig_w, orig_h = image.size
        new_w, new_h = compute_scaled_size(orig_w, orig_h, width, height, scale_percent)
        resized = image.resize((new_w, new_h), Image.LANCZOS)

        x = round((width - new_w) * h_anchor)
        y = round((height - new_h) * v_anchor)

        if color is None:
            canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            resized_rgba = resized.convert("RGBA")
            canvas.paste(resized_rgba, (x, y), resized_rgba)
            return canvas

        canvas = Image.new("RGB", (width, height), color)
        has_alpha = resized.mode in ("RGBA", "LA") or (
            resized.mode == "P" and "transparency" in resized.info
        )
        if has_alpha:
            resized_rgba = resized.convert("RGBA")
            canvas.paste(resized_rgba, (x, y), resized_rgba)
        else:
            canvas.paste(resized.convert("RGB"), (x, y))
        return canvas

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        images = as_image_list(pipe_input.input.get("image"), "canvas_fit")

        width = int(self.config.get("width") or 0)
        height = int(self.config.get("height") or 0)
        if width <= 0 or height <= 0:
            raise ValueError("canvas_fit requires positive 'width' and 'height'")

        scale_percent = float(self.config.get("scale_percent", 100.0))
        anchor = str(self.config.get("anchor", "center"))
        fill = str(self.config.get("fill", "transparent"))

        h_anchor, v_anchor = parse_anchor(anchor)
        color = parse_fill(fill)

        results = [
            self._place_one(image, width, height, scale_percent, h_anchor, v_anchor, color)
            for image in images
        ]

        generation_outputs(ProgressGenerationOutput(
            state=f"Placed <<NUMBER:{len(results)}>> on <<RESOLUTION:{width}x{height}>> canvas",
            icon=Icon(name="maximize"),
        ))
        return PipeOutput(output={"image": results})
