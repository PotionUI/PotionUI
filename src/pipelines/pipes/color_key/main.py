"""Chroma-key a flat background colour out to alpha.

This keys on CHROMINANCE - distance in the (Cb, Cr) plane (ITU-R BT.601),
luma (Y) discarded - not Euclidean RGB distance. RGB distance conflates
luminance with chrominance: a Euclidean-distance keyer
(`content/plugins/marketplace/spritesheet/backend/imaging/keying.py::_binary_alpha`)
fails on an unevenly-lit green screen because a shadowed patch of the SAME
green is far from the sampled bright-green colour in plain RGB terms, so it
survives at low tolerance; raising tolerance to also catch it then starts
eating dark SUBJECT pixels too, because in raw RGB space "darker" moves a
colour toward the origin regardless of its hue, so a shadowed background
pixel and a merely-dark subject pixel can end up EQUALLY close to a bright
key colour - no tolerance separates them.

Chrominance keying sidesteps this because a neutral gray/black `(v, v, v)`
maps to EXACTLY `(128, 128)` in (Cb, Cr) for every `v` (see
`_shared.imaging.color.rgb_to_cbcr`) - luminance never moves a pixel's
position in that plane on its own. A pure hue that only gets darker
(multiplicative shadow: `(0, k*255, 0)` for the key `(0, 255, 0)`, `k` in
`(0, 1]`) moves LINEARLY toward that same `(128, 128)` neutral point as `k`
falls, bounded by the key colour's own distance from neutral - so an
unevenly-lit but still GREEN background never travels father from the key in
chrominance space than "fully desaturated", while a subject with a distinct
hue sits somewhere else in the plane regardless of how dark it is. See
`tests/pipelines/pipes/color_key/test_color_key.py::test_chrominance_beats_rgb_distance_on_lit_gradient`
for a worked, provably-adversarial-for-RGB-distance example (an analytically
derived tolerance gap where chrominance keying removes a full lit/shadowed
green gradient while keeping a dark, hue-distinct subject fully intact, and
NO Euclidean-RGB tolerance can do both at once).

Produces a SOFT alpha ramp (a smoothstep, not a binary cut) between a low and
a high chrominance-distance threshold derived from `tolerance` (center of the
ramp) and `softness` (half-width of the ramp, as a fraction of the maximum
possible chrominance distance). `despill` (default on) mirrors
`keying.py::_despill`: it clamps the key colour's dominant channel toward the
average of the other two on every pixel, suppressing the classic
green/blue-screen colour fringe on the kept subject's edges.

`key_mode: "auto"` samples the dominant (modal) colour across a thin ring at
the image's border (`_shared.imaging.color.border_ring_color`) and keys
against that - no explicit `key_color` needed.
"""

from typing import Any, Dict, List

import numpy as np
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
from src.pipelines.outputs import Icon, ImageGenerationOutput, ProgressGenerationOutput
from src.pipelines.pipes._shared.imaging.alpha import feather_alpha
from src.pipelines.pipes._shared.imaging.color import (
    border_ring_color,
    despill as despill_rgb,
    parse_hex_color,
    rgb_to_cbcr,
)
from src.pipelines.pipes._shared.imaging.io import as_image_list

#: sqrt(2) * 255 -- the largest possible Euclidean distance between two
#: points in the (Cb, Cr) plane (each channel spans 0-255).
MAX_CHROMA_DISTANCE = (2 * 255.0 ** 2) ** 0.5


def soft_alpha(distance: np.ndarray, threshold: float, ramp_half: float) -> np.ndarray:
    """Smoothstep alpha ramp: 0 (transparent) at/under `threshold - ramp_half`,
    255 (opaque) at/over `threshold + ramp_half`, smoothstep in between.
    `ramp_half <= 0` collapses to a hard binary cut at `threshold`."""
    low = max(0.0, threshold - ramp_half)
    high = min(MAX_CHROMA_DISTANCE, threshold + ramp_half)
    if high <= low:
        return np.where(distance <= threshold, 0, 255).astype(np.uint8)

    t = np.clip((distance - low) / (high - low), 0.0, 1.0)
    smoothed = t * t * (3.0 - 2.0 * t)
    return np.round(smoothed * 255.0).astype(np.uint8)


class ColorKeyPipe(BasePipe):
    name = "color_key"
    description = "Chroma-key a flat background colour to alpha, keying on chrominance (brightness-invariant)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "key_mode": "auto",
            "key_color": "#00B140",
            "tolerance": 25.0,
            "softness": 10.0,
            "despill": True,
            "feather": 0.0,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("key_mode", str, "auto",
                           "'auto' (samples the dominant border colour) or 'color' (explicit key_color)",
                           required=False, choices=["auto", "color"]),
            PipeConfigSpec("key_color", str, "#00B140",
                           "Hex colour to key out (key_mode='color' only)", required=False),
            PipeConfigSpec("tolerance", float, 25.0,
                           "Chrominance distance from the key colour that counts as background (0-100)",
                           required=False, min_value=0.0, max_value=100.0),
            PipeConfigSpec("softness", float, 10.0,
                           "Width of the soft alpha ramp around the tolerance boundary (0-100)",
                           required=False, min_value=0.0, max_value=100.0),
            PipeConfigSpec("despill", bool, True,
                           "Suppress the key colour's dominant channel on every pixel", required=False),
            PipeConfigSpec("feather", float, 0.0,
                           "Gaussian-blur radius (px) applied to the alpha edge",
                           required=False, min_value=0.0, max_value=16.0),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Source image(s) with a flat/near-flat background", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "RGBA image(s) with the background keyed to alpha", is_array=True),
        ]

    @staticmethod
    def _key_one(image: Image.Image, key_mode: str, key_color, tolerance: float, softness: float,
                despill_on: bool, feather: float) -> Image.Image:
        rgba = np.array(image.convert("RGBA"))
        rgb = rgba[..., :3]

        key_rgb = parse_hex_color(key_color) if key_mode == "color" else border_ring_color(rgb)

        pixel_cbcr = rgb_to_cbcr(rgb)
        key_cbcr = rgb_to_cbcr(np.array(key_rgb, dtype=np.float64).reshape(1, 1, 3))[0, 0]
        distance = np.sqrt(np.sum((pixel_cbcr - key_cbcr) ** 2, axis=-1))

        threshold = (tolerance / 100.0) * MAX_CHROMA_DISTANCE
        ramp_half = (softness / 100.0) * (MAX_CHROMA_DISTANCE / 2.0)

        alpha = soft_alpha(distance, threshold, ramp_half)
        alpha = feather_alpha(alpha, feather)

        out_rgb = despill_rgb(rgb, key_rgb) if despill_on else rgb
        out = np.dstack([out_rgb, alpha]).astype(np.uint8)
        return Image.fromarray(out, mode="RGBA")

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        images = as_image_list(pipe_input.input.get("image"), "color_key")

        key_mode = str(self.config.get("key_mode", "auto"))
        if key_mode not in ("auto", "color"):
            raise ValueError(f"color_key: unknown key_mode {key_mode!r}")

        key_color = self.config.get("key_color")
        if key_mode == "color" and not key_color:
            raise ValueError("color_key requires 'key_color' when key_mode='color'")

        tolerance = max(0.0, min(100.0, float(self.config.get("tolerance", 25.0))))
        softness = max(0.0, min(100.0, float(self.config.get("softness", 10.0))))
        despill_on = bool(self.config.get("despill", True))
        feather = float(self.config.get("feather", 0.0))

        results = [
            self._key_one(image, key_mode, key_color, tolerance, softness, despill_on, feather)
            for image in images
        ]
        for result in results:
            generation_outputs(ImageGenerationOutput(image=result, temporary=True))

        generation_outputs(ProgressGenerationOutput(
            state=f"Chroma-keyed <<NUMBER:{len(results)}>> background(s)",
            icon=Icon(name="scissors"),
        ))
        return PipeOutput(output={"image": results})
