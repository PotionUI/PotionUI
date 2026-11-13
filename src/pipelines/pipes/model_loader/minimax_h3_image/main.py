from __future__ import annotations

from typing import List

from src.pipelines.contracts import IOType, PipeConfigSpec, PipeOutputSpec
from src.pipelines.pipes._shared.generation.config_specs import loras_config_spec
from src.pipelines.pipes._shared.generation.three_component_loader import ThreeComponentLoaderPipe
from src.pipelines.pipes.model_loader.minimax_h3_image.clip import MiniMaxH3ImageClipTextEncoder

_LOG_TAG = "MODEL LOADER H3 IMAGE"


class ModelLoaderMiniMaxH3ImagePipe(ThreeComponentLoaderPipe):
    name = "model_loader"
    description = "Load a native MiniMax-H3-Image checkpoint set (DiT + Qwen3-VL-32B TE + image VAE)"
    log_tag = _LOG_TAG
    family = "minimax_h3_image"
    display_name = "MiniMax-H3-Image"
    component_labels = (
        ("diffusion_model", "minimax_h3_image_dit"),
        ("text_encoder", "minimax_h3_qwen3vl_32b"),
        ("vae", "minimax_h3_image_vae"),
    )
    clip_cls = MiniMaxH3ImageClipTextEncoder
    te_vision = True
    sync_loras = False

    def _vision(self) -> bool:
        return True

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("diffusion_model", dict, None, "MiniMax-H3-Image DiT (int8 ConvRot or bf16)", required=True),
            PipeConfigSpec("text_encoder", dict, None, "Qwen3-VL-32B text encoder, shared with MiniMax-H3", required=True),
            PipeConfigSpec("vae", dict, None, "MiniMax-H3-Image VAE", required=True),
            loras_config_spec("DiT LoRAs (applied at load; a change reloads the DiT, not the text encoder or VAE)"),
            *cls.runtime_config_specs(),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("model", IOType.MODEL, "MiniMax-H3-Image model bundle (DiT + TE + VAE)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "Qwen3-VL-32B text encoder (ClipTextEncoder ABC)",
                           is_array=False),
        ]
