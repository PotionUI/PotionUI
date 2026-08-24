from typing import Dict, Any, List

from src.pipelines.outputs import CompareImagesGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)


class ArtifactPipe(BasePipe):
    name = "artifact"
    description = "This pipe will generate artifacts"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "compare",
            "left": "original_image",
            "right": "final_image",
            "output": "right"
        }

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        if self.config['mode'] == "compare":
            before_image = pipe_input.input.get("before_image")
            after_image = pipe_input.input.get("after_image")

            if not before_image or not after_image:
                logger.error("[ARTIFACT] Expected 2 images for comparison; passing through the side that exists")

                present = after_image or before_image
                return PipeOutput(output={
                    "image": present if isinstance(present, list) else [present]
                })

            before_images = before_image if isinstance(before_image, list) else [before_image]
            after_images = after_image if isinstance(after_image, list) else [after_image]

            if len(before_images) != len(after_images):
                logger.error(
                    f"[ARTIFACT] before/after image counts differ "
                    f"({len(before_images)} vs {len(after_images)}); comparing the overlapping prefix"
                )

            for index, (before, after) in enumerate(zip(before_images, after_images)):
                generation_outputs(CompareImagesGenerationOutput(
                    index=index,
                    compare=(self.config['left'], before),
                    to=(self.config['right'], after)
                ))

            return PipeOutput({
                "image": before_images if self.config['output'] == 'left' else after_images
            })

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """Artifact requires images for comparison"""
        return [
            PipeInputSpec("before_image", IOType.IMAGE, True, "Images for comparison or processing", is_array=True),
            PipeInputSpec("after_image", IOType.IMAGE, True, "Images for comparison or processing", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """Artifact produces processed images"""
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Processed or selected images", is_array=True),
        ]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Artifact configuration parameters"""
        return [
            PipeConfigSpec(
                name="mode",
                param_type=str,
                default="compare",
                description="Artifact processing mode",
                required=True,
                choices=["compare"]
            ),
            PipeConfigSpec(
                name="left",
                param_type=str,
                default="original_image",
                description="Left side image identifier for comparison",
                required=True
            ),
            PipeConfigSpec(
                name="right",
                param_type=str,
                default="final_image",
                description="Right side image identifier for comparison",
                required=True
            ),
            PipeConfigSpec(
                name="output",
                param_type=str,
                default="right",
                description="Which image to output",
                required=True,
                choices=["left", "right"]
            ),
        ]




