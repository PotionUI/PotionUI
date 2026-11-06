from __future__ import annotations

from typing import List, Tuple

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
from src.pipelines.pipes._shared.imaging.fit import FIT_MODES, fit_image
from src.platform.runtime.native.resolution import snap_to_multiple


class ImageFitPipe(BasePipe):
    name = "image_fit"
    description = "Crop or pad the first picture to a resolution so every consumer sees the same framing"

    def target_size(self) -> Tuple[int, int]:
        width, height = (int(v) for v in str(self.config.get("resolution", "1024x1024")).lower().split("x"))
        granularity = int(self.config.get("granularity", 16))
        return snap_to_multiple(width, granularity), snap_to_multiple(height, granularity)

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        images = list(pipe_input.input.get("image") or [])
        fit = str(self.config.get("fit") or "match").strip().lower()
        if fit not in FIT_MODES or not images or not isinstance(images[0], Image.Image):
            return PipeOutput(output={"image": images})
        target = self.target_size()
        first = images[0]
        images[0] = fit_image(first, target, fit)
        logger.info("[IMAGE FIT] %s %dx%d -> %dx%d", fit, *first.size, *target)
        return PipeOutput(output={"image": images})

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [PipeInputSpec("image", IOType.IMAGE, True, "Pictures; the first one is fitted", is_array=True)]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("image", IOType.IMAGE, "Pictures with the first one fitted", is_array=True)]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("fit", str, "match", "match leaves the pictures as they are; crop or pad fits the first to the resolution",
                           required=False, choices=["match", *FIT_MODES]),
            PipeConfigSpec("resolution", str, "1024x1024", "Target size (WxH)", required=False),
            PipeConfigSpec("granularity", int, 16, "Pixel grid the target snaps to; match the generator's", required=False,
                           min_value=1, max_value=128),
        ]
