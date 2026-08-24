"""Trim an image to its alpha bounding box plus a margin.

Meant to run right after `matting/birefnet` or `color_key`: those leave a
full-canvas RGBA image whose subject may occupy only a fraction of it. This
pipe finds the smallest opaque-enough rectangle (`alpha_threshold` gates what
counts as "opaque") and crops to it, padded by `margin` px on every side and
clamped to the canvas.

An empty/fully-transparent input (no pixel above `alpha_threshold`) is NOT an
error: `_shared.imaging.alpha.alpha_bbox` returns `None` for it, and this
pipe passes that particular image through UNCHANGED rather than crashing or
producing a zero-size image - a broken upstream matte on one frame should not
also break the crop step, or the rest of the batch. `generation_outputs`
still reports it (a progress line, not a hard error) and the `cropped`
output flags each image `False`/`True` for anything downstream that wants to
distinguish "nothing to crop" from a real crop.

`image` is a LIST in and out (like every pipe in this family - see
`_shared.imaging.io.as_image_list`'s docstring for why a bare `Image` input
is still accepted defensively): every image in it is processed and returned,
never just the first - a media_loader step upstream can hand over more than
one file.
"""

from typing import Any, Dict, List, Tuple

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
from src.pipelines.pipes._shared.imaging.alpha import alpha_bbox
from src.pipelines.pipes._shared.imaging.io import as_image_list


class CropSubjectPipe(BasePipe):
    name = "crop_subject"
    description = "Crop to the alpha bounding box (plus margin); passes through unchanged when an image has no opaque subject"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "margin": 0,
            "alpha_threshold": 16,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("margin", int, 0,
                           "Padding (px) added around the alpha bounding box on every side",
                           required=False, min_value=0),
            PipeConfigSpec("alpha_threshold", int, 16,
                           "Alpha value (0-255) a pixel must exceed to count toward the subject bbox",
                           required=False, min_value=0, max_value=255),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Source image(s) (an RGBA cutout is expected, but any image works)", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Cropped image(s), or the input unchanged where no subject was found", is_array=True),
            PipeOutputSpec("cropped", IOType.BOOL, "Whether a crop was actually applied, per image", is_array=True),
        ]

    @staticmethod
    def _crop_one(image: Image.Image, margin: int, alpha_threshold: int) -> Tuple[Image.Image, bool]:
        bbox = alpha_bbox(image, alpha_threshold)
        if bbox is None:
            return image, False

        rgba = image.convert("RGBA")
        width, height = rgba.size
        x, y, w, h = bbox

        x0 = max(0, x - margin)
        y0 = max(0, y - margin)
        x1 = min(width, x + w + margin)
        y1 = min(height, y + h + margin)

        return rgba.crop((x0, y0, x1, y1)), True

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        images = as_image_list(pipe_input.input.get("image"), "crop_subject")

        margin = int(self.config.get("margin", 0))
        alpha_threshold = int(self.config.get("alpha_threshold", 16))

        results = []
        cropped_flags = []
        for image in images:
            cropped, was_cropped = self._crop_one(image, margin, alpha_threshold)
            results.append(cropped)
            cropped_flags.append(was_cropped)

            if was_cropped:
                generation_outputs(ProgressGenerationOutput(
                    state=f"Cropped to subject <<RESOLUTION:{cropped.size[0]}x{cropped.size[1]}>>",
                    icon=Icon(name="crop"),
                ))
            else:
                generation_outputs(ProgressGenerationOutput(
                    state="crop_subject: no pixel above the alpha threshold - passing this image through unchanged",
                    icon=Icon(name="alert-triangle"),
                ))

        return PipeOutput(output={"image": results, "cropped": cropped_flags})
