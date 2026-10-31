from __future__ import annotations

from typing import List

from PIL import Image

from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    logger,
)
from src.pipelines.pipes.canvas_expand.geometry import (
    ANCHORS,
    FILLS,
    aspect_margins,
    build_canvas,
    build_mask,
    pixel_margins,
)


class CanvasExpandPipe(BasePipe):
    name = "canvas_expand"
    description = "Grow the canvas around a picture and mask the new area for outpainting"

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        image = pipe_input.input["image"]
        if not isinstance(image, Image.Image):
            raise ValueError("canvas_expand needs a picture")
        width, height = image.size
        aspect = str(self.config.get("aspect") or "").strip()
        if aspect:
            margins = aspect_margins(width, height, aspect, str(self.config.get("anchor", "center")))
        else:
            margins = pixel_margins(
                self.config.get("left", 0), self.config.get("top", 0),
                self.config.get("right", 0), self.config.get("bottom", 0),
            )
        canvas = build_canvas(image, margins, str(self.config.get("fill", "blur")))
        mask = build_mask(image.size, margins, int(self.config.get("overlap", 16)))
        logger.info("[CANVAS EXPAND] %dx%d -> %dx%d, margins %s", width, height, *canvas.size, margins)
        return PipeOutput(output={"image": canvas, "images": [canvas], "mask": mask})

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [PipeInputSpec("image", IOType.IMAGE, True, "Picture to extend", is_array=False)]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Picture on the larger canvas", is_array=False),
            PipeOutputSpec("images", IOType.IMAGE, "Picture on the larger canvas as a one-item list", is_array=True),
            PipeOutputSpec("mask", IOType.IMAGE, "White over the new area", is_array=False),
        ]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        side = dict(param_type=int, default=0, required=False, min_value=0, max_value=8192)
        return [
            PipeConfigSpec(name="left", description="Pixels to add on the left", **side),
            PipeConfigSpec(name="top", description="Pixels to add on the top", **side),
            PipeConfigSpec(name="right", description="Pixels to add on the right", **side),
            PipeConfigSpec(name="bottom", description="Pixels to add on the bottom", **side),
            PipeConfigSpec(
                name="aspect", param_type=str, default="", required=False,
                description="Target shape as W:H; grows the canvas to it instead of using the sides",
            ),
            PipeConfigSpec(
                name="anchor", param_type=str, default="center", required=False, choices=list(ANCHORS),
                description="Where the picture sits when growing to a target shape",
            ),
            PipeConfigSpec(
                name="fill", param_type=str, default="blur", required=False, choices=list(FILLS),
                description="What the new area starts as before it is repainted",
            ),
            PipeConfigSpec(
                name="overlap", param_type=int, default=16, required=False, min_value=0, max_value=128,
                description="Pixels of the original edge that are repainted too, so the seam blends",
            ),
        ]
