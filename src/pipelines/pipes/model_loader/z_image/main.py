"""Model loader for the native Z-Image family (txt2img).

Z-Image is a single-checkpoint NextDiT (Lumina-Image-2.0 backbone at dim 3840)
plus ONE text encoder (Qwen3-4B, no CLIP-L) and the Flux-style 2D AE. The native
engine detects the DiT variant from the checkpoint, so this pipe just supplies
the three component files.

Like the Qwen loader, each heavy component (text encoder, VAE, DiT) is acquired
under its OWN ``MODELS`` cache key: a shared Qwen3 / VAE is reused across presets,
and a LoRA change re-acquires ONLY the DiT. The text encoder is loaded via
``load_text_encoder(..., te_variant="zimage")`` directly (not the generic
``NativeEngineLoader.load`` TE path) because Z-Image's Qwen3-4B is structurally
identical to Klein's but consumes a different hidden state (penultimate layer),
so the encode contract must be selected by this caller.
"""

from __future__ import annotations

from typing import List

from src.pipelines.contracts import (
    IOType,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.pipes._shared.generation.three_component_loader import ThreeComponentLoaderPipe
from src.pipelines.pipes._shared.generation.three_component_loader import LoaderContext
from src.pipelines.pipes._shared.generation.loader_helpers import path_of as _path_of
from src.platform.runtime.model_lifecycle.lifecycle import file_size_gb
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.text_encoders.loader import load_text_encoder
from src.pipelines.pipes.model_loader.z_image.z_image_clip import ZImageClipTextEncoder
from src.pipelines.pipes._shared.generation.config_specs import loras_config_spec


class ModelLoaderZImagePipe(ThreeComponentLoaderPipe):
    name = "model_loader"
    description = "Load a native Z-Image checkpoint set (NextDiT + Qwen3-4B TE + 2D AE)"
    log_tag = "MODEL LOADER Z-IMAGE"
    family = "z_image"
    display_name = "Z-Image"
    component_labels = (
        ("diffusion_model", "z_image_dit"),
        ("text_encoder", "z_image_text_encoder"),
        ("vae", "z_image_vae"),
        ("control_model", "z_image_fun_control"),
    )
    clip_cls = ZImageClipTextEncoder
    te_suffix = "|zimage"

    # -- declaration -------------------------------------------------------

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("diffusion_model", dict, None, "Z-Image NextDiT checkpoint", required=True),
            PipeConfigSpec("text_encoder", dict, None, "Qwen3-4B text encoder (single TE, no CLIP-L)", required=True),
            PipeConfigSpec("vae", dict, None, "Z-Image (Flux-style 2D) VAE", required=True),
            loras_config_spec("LoRA adapters (busts only the DiT cache)"),
            *cls.runtime_config_specs(),
            PipeConfigSpec("control_model", dict, None, "Fun ControlNet Union 2.1 patch loaded with the DiT", required=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("model", IOType.MODEL, "Z-Image model bundle (DiT + TE + VAE)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "Qwen3-4B text encoder (ClipTextEncoder ABC)", is_array=False),
        ]




    def _load_te(self, ctx: LoaderContext) -> NativeModel:
        encoder = load_text_encoder(ctx.te_path, device="cpu", te_variant="zimage")
        return NativeModel("text_encoder", encoder)

    def _control_path(self):
        return _path_of(self.config.get("control_model"))

    def _dit_key(self, ctx: LoaderContext) -> str:
        control_path = self._control_path()
        key = super()._dit_key(ctx)
        return f"{key}+control={control_path}" if control_path else key

    def _dit_fingerprint(self, ctx: LoaderContext) -> str:
        control_path = self._control_path()
        fp = super()._dit_fingerprint(ctx)
        return f"{fp}|control={control_path}" if control_path else fp

    def _dit_size_gb(self, ctx: LoaderContext):
        control_path = self._control_path()
        dit_gb = super()._dit_size_gb(ctx)
        if control_path and dit_gb is not None:
            dit_gb += file_size_gb(control_path) or 0.0
        return dit_gb

    def _dit_load_kwargs(self, ctx: LoaderContext):
        control_path = self._control_path()
        return {"model_patch": control_path} if control_path else {}
