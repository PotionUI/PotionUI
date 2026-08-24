from typing import Dict, Any, List

from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
    IOType,
)
from src.pipelines.pipes.sag.sdxl.hook import SAGHook


class SAGPipe(BasePipe):
    name = "sag"
    description = "Self-Attention Guidance for SDXL (enhanced detail, ~15-20% overhead)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "scale": 0.75,
            "sigma": 2.0,
            "sag_threshold": 1.0,
        }

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        model = pipe_input.input["model"]

        scale = float(self.config.get("scale", 0.75))
        sigma = float(self.config.get("sigma", 2.0))
        sag_threshold = float(self.config.get("sag_threshold", 1.0))

        hook = SAGHook(scale=scale, sigma=sigma, sag_threshold=sag_threshold)
        model.register_hook("sag", hook)

        logger.info(f"[SAG PIPE] Registered hook: scale={scale}, sigma={sigma}, sag_threshold={sag_threshold}")

        return PipeOutput(output={"model": model})

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [PipeInputSpec("model", IOType.MODEL, True, "SDXL model with hook registry", is_array=False)]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("model", IOType.MODEL, "Model with SAG hook registered", is_array=False)]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="scale",
                param_type=float,
                default=0.75,
                description="SAG guidance strength",
                required=False,
                min_value=0.0,
                max_value=1.5,
            ),
            PipeConfigSpec(
                name="sigma",
                param_type=float,
                default=2.0,
                description="SAG blur sigma",
                required=False,
                min_value=0.5,
                max_value=10.0,
            ),
            PipeConfigSpec(
                name="sag_threshold",
                param_type=float,
                default=1.0,
                description="Attention-magnitude threshold for the blur mask (ComfyUI-parity; NOT a progress gate)",
                required=False,
                min_value=0.0,
                max_value=5.0,
            ),
        ]
