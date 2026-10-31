"""Model loader for the native Qwen-Image family (txt2img).

Qwen-Image is a single-checkpoint MMDiT plus ONE text encoder (Qwen2.5-VL, no
CLIP-L) and the Wan-2.1 causal-3D VAE. The native engine detects the DiT variant
from the checkpoint, so this pipe just supplies the three component files.

Like the Flux/Wan loaders, each heavy component (text encoder, VAE, DiT) is
acquired under its OWN ``MODELS`` cache key: a shared Qwen2.5-VL / VAE is reused
across Qwen presets, and a LoRA change re-acquires ONLY the DiT (LoRAs are in the
DiT fingerprint, not the TE/VAE ones).
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.pipelines.contracts import (
    IOType,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.pipes._shared.generation.three_component_loader import ThreeComponentLoaderPipe
from src.pipelines.pipes.model_loader.qwen.edit_layout import qwen_edit_vision_layout
from src.pipelines.pipes.model_loader.qwen.qwen_clip import QwenClipTextEncoder
from src.pipelines.pipes._shared.generation.config_specs import loras_config_spec


class ModelLoaderQwenPipe(ThreeComponentLoaderPipe):
    name = "model_loader"
    description = "Load a native Qwen-Image checkpoint set (MMDiT + Qwen2.5-VL TE + causal-3D VAE)"
    log_tag = "MODEL LOADER QWEN"
    family = "qwen"
    display_name = "Qwen-Image"
    component_labels = (
        ("diffusion_model", "qwen_dit"),
        ("text_encoder", "qwen_text_encoder"),
        ("vae", "qwen_vae"),
    )
    clip_cls = QwenClipTextEncoder
    te_vision = True

    # -- declaration -------------------------------------------------------

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("diffusion_model", dict, None, "Qwen-Image MMDiT checkpoint", required=True),
            PipeConfigSpec("text_encoder", dict, None, "Qwen2.5-VL text encoder (single TE, no CLIP-L)", required=True),
            PipeConfigSpec("vae", dict, None, "Qwen-Image (Wan-2.1 causal-3D) VAE", required=True),
            loras_config_spec("LoRA adapters (busts only the DiT cache)"),
            *cls.runtime_config_specs(),
            # Edit mode only: keep+load the Qwen2.5-VL vision tower so the prompt
            # encoder can condition on the source image, not just its text
            # description. Off by default (txt2img/img2img memory footprint
            # unaffected) -- folded into te_fp below, NOT just passed to the
            # loader, so a text-only and a vision-enabled load of the same TE
            # file never alias to the same model-lifecycle cache entry.
            PipeConfigSpec("vision", bool, False, "Load the vision tower for image-conditioned editing", required=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("model", IOType.MODEL, "Qwen-Image model bundle (DiT + TE + VAE)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "Qwen2.5-VL text encoder (ClipTextEncoder ABC)", is_array=False),
        ]

    def _clip_kwargs(self, dit_model) -> Dict[str, Any]:
        if not self._vision():
            return {}
        return {"vision_layout": qwen_edit_vision_layout(getattr(dit_model, "module", None))}
