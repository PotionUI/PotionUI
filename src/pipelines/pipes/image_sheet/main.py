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
from src.pipelines.pipes._shared.imaging.sheet import MAX_PANELS, compose_sheet, fit_sheet
from src.platform.runtime.native.resolution import snap_to_multiple


class ImageSheetPipe(BasePipe):
    name = "image_sheet"
    description = "Lay pictures out as one reference sheet at the generation size, or fit a ready-made sheet to it"

    def target_size(self) -> Tuple[int, int]:
        width, height = (int(v) for v in str(self.config.get("resolution", "768x448")).lower().split("x"))
        granularity = int(self.config.get("granularity", 32))
        return snap_to_multiple(width, granularity), snap_to_multiple(height, granularity)

    def split(self, images: List[Image.Image]) -> Tuple[List[Image.Image], List[Image.Image]]:
        panels = int(self.config.get("pictures", len(images)))
        return images[:panels], images[panels:]

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        images = [image for image in (pipe_input.input.get("image") or []) if isinstance(image, Image.Image)]
        pictures, sheets = self.split(images)
        if pictures and sheets:
            raise ValueError("Give either pictures or one ready-made reference sheet, not both.")
        if not pictures and not sheets:
            raise ValueError("Add pictures of your characters and props, or one ready-made reference sheet.")
        if len(pictures) > MAX_PANELS:
            raise ValueError(
                f"A reference sheet holds up to {MAX_PANELS} pictures and you added {len(pictures)}. "
                f"Remove {len(pictures) - MAX_PANELS} or combine some into one picture.")
        target = self.target_size()
        sheet = compose_sheet(pictures, target) if pictures else fit_sheet(sheets[0], target)
        logger.info("[IMAGE SHEET] %s -> %dx%d", f"{len(pictures)} picture(s)" if pictures else "ready sheet", *target)
        return PipeOutput(output={"image": [sheet]})

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [PipeInputSpec("image", IOType.IMAGE, False, "Panel pictures first, then an optional ready-made sheet", is_array=True)]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("image", IOType.IMAGE, "The one reference sheet", is_array=True)]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("resolution", str, "768x448", "Sheet size (WxH)", required=False),
            PipeConfigSpec("granularity", int, 32, "Pixel grid the size snaps to; match the generator's", required=False,
                           min_value=1, max_value=128),
            PipeConfigSpec("pictures", int, 0,
                           "How many leading pictures are panels; any after them are a ready-made sheet. "
                           "0 treats every picture as a ready-made sheet", required=False, min_value=0, max_value=64),
        ]
