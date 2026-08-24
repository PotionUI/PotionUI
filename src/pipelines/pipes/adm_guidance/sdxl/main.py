# Derived from: Fooocus modules/patch.py ADM scaler technique (GPL-3.0)
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
from src.pipelines.pipes.adm_guidance.sdxl.hook import ADMGuidanceHook


class ADMGuidancePipe(BasePipe):
    name = "adm_guidance"
    description = "ADM Guidance enhancement for SDXL (Fooocus technique)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "positive_scale": 1.5,
            "negative_scale": 0.8,
            "scaler_end": 0.3,
        }

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        model = pipe_input.input["model"]

        # Auto-tune based on model type if user hasn't set explicit config
        positive_scale, negative_scale, scaler_end = self._get_adm_params(model)

        hook = ADMGuidanceHook(
            positive_scale=positive_scale,
            negative_scale=negative_scale,
            scaler_end=scaler_end,
        )
        model.register_hook("adm_guidance", hook)

        logger.info(
            f"[ADM GUIDANCE PIPE] Registered hook: pos={positive_scale}, neg={negative_scale}, end={scaler_end}"
        )

        return PipeOutput(output={"model": model})

    def _get_adm_params(self, model):
        """Get ADM parameters, auto-tuning from model type if defaults unchanged."""
        defaults = self.get_default_config()

        positive_scale = float(self.config.get("positive_scale", defaults["positive_scale"]))
        negative_scale = float(self.config.get("negative_scale", defaults["negative_scale"]))
        scaler_end = float(self.config.get("scaler_end", defaults["scaler_end"]))

        # Check if user explicitly configured (differs from class defaults)
        user_configured = (
            positive_scale != defaults["positive_scale"]
            or negative_scale != defaults["negative_scale"]
            or scaler_end != defaults["scaler_end"]
        )

        if user_configured:
            logger.debug("[ADM GUIDANCE PIPE] Using user-configured ADM parameters")
            return positive_scale, negative_scale, scaler_end

        # Auto-tune from model type
        model_type_info = getattr(model, "model_type_info", None)
        if model_type_info is not None:
            if not model_type_info.recommended_adm_enabled:
                # For anime models, use neutral scales so it's effectively a no-op
                logger.debug(
                    f"[ADM GUIDANCE PIPE] Auto-tuning for {model_type_info.model_style} model (ADM disabled)"
                )
                return 1.0, 1.0, 0.0

            logger.debug(
                f"[ADM GUIDANCE PIPE] Auto-tuning for {model_type_info.model_style} model"
            )
            return (
                model_type_info.recommended_adm_positive_scale,
                model_type_info.recommended_adm_negative_scale,
                model_type_info.recommended_adm_scaler_end,
            )

        return positive_scale, negative_scale, scaler_end

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "SDXL model with hook registry", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("model", IOType.MODEL, "Model with ADM hook registered", is_array=False),
        ]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="positive_scale",
                param_type=float,
                default=1.5,
                description="ADM positive conditioning scale",
                required=False,
                min_value=1.0,
                max_value=2.0,
            ),
            PipeConfigSpec(
                name="negative_scale",
                param_type=float,
                default=0.8,
                description="ADM negative conditioning scale",
                required=False,
                min_value=0.5,
                max_value=1.0,
            ),
            PipeConfigSpec(
                name="scaler_end",
                param_type=float,
                default=0.3,
                description="Progress threshold to stop ADM",
                required=False,
                min_value=0.0,
                max_value=1.0,
            ),
        ]
