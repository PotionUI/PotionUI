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
from src.pipelines.pipes.sharpness.sdxl.hook import SharpnessHook


class SharpnessPipe(BasePipe):
    name = "sharpness"
    description = "Anisotropic sharpness enhancement for SDXL"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {"strength": 0.0}

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        model = pipe_input.input["model"]
        strength = float(self.config.get("strength", 0.0))

        if strength > 0:
            hook = SharpnessHook(strength=strength)
            model.register_hook("sharpness", hook)
            logger.info(f"[SHARPNESS PIPE] Registered hook: strength={strength}")
        else:
            logger.info("[SHARPNESS PIPE] Disabled (strength=0)")

        return PipeOutput(output={"model": model})

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [PipeInputSpec("model", IOType.MODEL, True, "SDXL model with hook registry", is_array=False)]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("model", IOType.MODEL, "Model with sharpness hook registered", is_array=False)]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="strength",
                param_type=float,
                default=0.0,
                description="Sharpness strength (0=disabled)",
                required=False,
                min_value=0.0,
                max_value=30.0,
            ),
        ]
