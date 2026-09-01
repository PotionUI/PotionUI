"""Model loader for the native TRELLIS.2 family (image to 3D).

TRELLIS.2 does not fit the one-file-one-model shape the generic native loader
assumes, so ``NativeEngineLoader._load_dit`` refuses the bundle by design and
this pipe calls ``arch.trellis2.load``'s prefixed readers directly. Four files
carry eight models:

=====================  =========================================================
file                   models read out of it
=====================  =========================================================
``diffusion_model``    sparse-structure flow, shape flow (512 and 1024 tiers),
                       texture flow — four prefixes in one checkpoint
``shape_vae``          sparse-structure decoder + FlexiDualGrid shape decoder
``texture_vae``        the texture decoder
``image_encoder``      DINOv3 ViT-L/16
=====================  =========================================================

Each is acquired under its own ``MODELS`` key, keyed by the component rather
than the file. That is what makes the resolution tier cheap to change: the
512 tier and the cascades share the conditioner, both VAEs, the
sparse-structure flow and the low-resolution shape flow, and differ only in
which high-resolution flow (if any) and which texture-flow variant they need,
so switching tiers re-acquires one or two components instead of re-reading an
8GB file. ``estimated_vram_gb`` comes from the checkpoint header's own byte
range for that prefix (see ``weights.py``) — the file size would over-report
each flow model roughly fourfold and evict models that would have fit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import torch

from src.pipelines.contracts import (
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import ModelGenerationOutput, ModelsGenerationOutput
from src.pipelines.pipes._shared.generation.loader_base import BaseModelLoaderPipe
from src.pipelines.pipes._shared.generation.loader_helpers import (
    ComponentProgress,
    path_of as _path_of,
    vram_budget as _vram_budget_fn,
)
from src.pipelines.pipes.model_loader.trellis2.bundle import Trellis2ModelBundle
from src.pipelines.pipes.model_loader.trellis2.weights import prefix_size_gb
from src.platform.runtime.model_lifecycle.lifecycle import file_size_gb
from src.platform.runtime.native.arch.trellis2 import load as trellis2_load
from src.platform.runtime.native.arch.trellis2.detect import (
    FLOW_PREFIXES,
    SHAPE_DECODER_PREFIX,
    STRUCTURE_DECODER_PREFIX,
    TEXTURE_DECODER_PREFIX,
)
from src.platform.runtime.native.arch.trellis2.image_to_mesh import TIERS
from src.platform.runtime.native.engine import NativeModel

_DTYPES = {
    "bfloat16": torch.bfloat16,
    "float16": torch.float16,
    "float32": torch.float32,
}

#: config key -> what the user is picking, for the "not selected" message.
_WEIGHT_INPUTS = {
    "diffusion_model": "TRELLIS.2 transformer",
    "shape_vae": "shape VAE",
    "texture_vae": "texture VAE",
    "image_encoder": "DINOv3 image encoder",
}


class ModelLoaderTrellis2Pipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native TRELLIS.2 set (4 flow DiTs + 3 decoders + DINOv3 encoder)"

    # -- declaration -------------------------------------------------------

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "diffusion_model": None,
            "shape_vae": None,
            "texture_vae": None,
            "image_encoder": None,
            "matting_model": None,
            "resolution_tier": "1024",
            "device": "cuda",
            "dtype": "bfloat16",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("diffusion_model", dict, None,
                           "TRELLIS.2 transformer bundle (all four flow DiTs)", required=True),
            PipeConfigSpec("shape_vae", dict, None,
                           "Shape VAE (sparse-structure + FlexiDualGrid decoders)", required=True),
            PipeConfigSpec("texture_vae", dict, None, "Texture VAE decoder", required=True),
            PipeConfigSpec("image_encoder", dict, None, "DINOv3 ViT-L/16 image encoder", required=True),
            PipeConfigSpec("matting_model", dict, None,
                           "BiRefNet checkpoint for background removal on an opaque image. "
                           "Optional — an image that already has a transparent background "
                           "needs none.", required=False),
            PipeConfigSpec("resolution_tier", str, "1024",
                           "Reconstruction path. 512 is a single shape pass; 1024 and 1536 "
                           "are cascades and load a second shape flow.",
                           required=False, choices=sorted(TIERS)),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False,
                           choices=["cuda", "cpu"]),
            PipeConfigSpec("dtype", str, "bfloat16", "Compute dtype", required=False,
                           choices=sorted(_DTYPES)),
            PipeConfigSpec("vram_limit_gb", float, None, "VRAM budget hint (backend-injected)",
                           required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service for per-component reuse", is_array=False),
            PipeInputSpec("GPU", IOType.SERVICE, False, "GPU manager for the VRAM budget",
                          is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("model", IOType.MODEL, "TRELLIS.2 model bundle", is_array=False),
        ]

    # -- BaseModelLoaderPipe hooks -----------------------------------------

    def progress_message(self) -> str:
        path = _path_of(self.config.get("diffusion_model")) or "?"
        return f"Loading TRELLIS.2 model <<MODEL:{Path(path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        described = []
        for key, model_type in (
            ("diffusion_model", "trellis2_dit"),
            ("shape_vae", "trellis2_shape_vae"),
            ("texture_vae", "trellis2_texture_vae"),
            ("image_encoder", "trellis2_image_encoder"),
            ("matting_model", "matting"),
        ):
            component = self.config.get(key)
            path = _path_of(component)
            if path:
                described.append(
                    ModelGenerationOutput(name=component.get("name") or Path(path).stem, type=model_type)
                )
        return described

    # -- multi-component load ----------------------------------------------

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        """Emit progress/models, then acquire each of the eight components.

        Overrides the single-acquire base flow: this family caches per
        component, not per file, so the tier can change without re-reading the
        checkpoint every shared component came out of.
        """
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        paths = self._weight_paths()
        tier = self._tier()
        _ss_grid, shape_tier, tex_tier, is_cascade = TIERS[tier]
        dtype_name = self.config.get("dtype", "bfloat16")
        dtype = _DTYPES[dtype_name]
        device = self.config.get("device", "cuda")
        matting_path = _path_of(self.config.get("matting_model"))

        # Recorded, not consumed: this family places one model at a time and
        # returns it to CPU before the next arrives, so there is no streaming
        # decision to make against a budget — but the number is what a 1536-tier
        # run that ran out of VRAM has to be diagnosed from.
        self._vram_budget(pipe_input)

        dit, shape_vae = paths["diffusion_model"], paths["shape_vae"]
        plan = [
            ("image encoder", f"native/trellis2/dino/{paths['image_encoder']}",
             lambda: NativeModel("text_encoder", trellis2_load.load_dino_conditioner(
                 paths["image_encoder"], dtype=dtype)),
             file_size_gb(paths["image_encoder"])),
            ("sparse-structure flow", f"native/trellis2/ss_flow/{dit}",
             lambda: NativeModel("diffusion_model", trellis2_load.load_ss_flow(dit, dtype=dtype)),
             prefix_size_gb(dit, FLOW_PREFIXES["structure"])),
            ("sparse-structure decoder", f"native/trellis2/ss_vae/{shape_vae}",
             lambda: NativeModel("vae", trellis2_load.load_ss_vae_decoder(shape_vae, dtype=dtype)),
             prefix_size_gb(shape_vae, STRUCTURE_DECODER_PREFIX)),
            ("shape flow", f"native/trellis2/shape_flow_512/{dit}",
             lambda: NativeModel("diffusion_model", trellis2_load.load_shape_slat_flow(
                 dit, "512", dtype=dtype)),
             prefix_size_gb(dit, FLOW_PREFIXES["shape_512"])),
            ("shape decoder", f"native/trellis2/shape_decoder/{shape_vae}",
             lambda: NativeModel("vae", trellis2_load.load_shape_slat_decoder(shape_vae, dtype=dtype)),
             prefix_size_gb(shape_vae, SHAPE_DECODER_PREFIX)),
            (f"texture flow ({tex_tier})", f"native/trellis2/tex_flow_{tex_tier}/{dit}",
             lambda: NativeModel("diffusion_model", trellis2_load.load_tex_slat_flow(
                 dit, tex_tier, dtype=dtype)),
             prefix_size_gb(dit, FLOW_PREFIXES["texture"])),
            ("texture decoder", f"native/trellis2/tex_decoder/{paths['texture_vae']}",
             lambda: NativeModel("vae", trellis2_load.load_tex_slat_decoder(
                 paths["texture_vae"], dtype=dtype)),
             prefix_size_gb(paths["texture_vae"], TEXTURE_DECODER_PREFIX)),
        ]
        if is_cascade:
            plan.append((
                "high-resolution shape flow", f"native/trellis2/shape_flow_1024/{dit}",
                lambda: NativeModel("diffusion_model", trellis2_load.load_shape_slat_flow(
                    dit, shape_tier, dtype=dtype)),
                prefix_size_gb(dit, FLOW_PREFIXES["shape_1024"]),
            ))
        if matting_path:
            plan.append((
                "matting model", f"native/matting/{matting_path}",
                lambda: _load_matting(matting_path), file_size_gb(matting_path),
            ))

        models = pipe_input.input.get("MODELS", None)
        progress = ComponentProgress(generation_outputs, models, self.progress_message(), len(plan))

        loaded = {}
        for label, key, loader, estimated_gb in plan:
            progress.advance(label, key)
            fingerprint = f"{key}|{dtype_name}"
            if models is not None:
                loaded[key] = models.acquire(
                    key=key, fingerprint=fingerprint, loader=loader, estimated_vram_gb=estimated_gb
                )
            else:
                # No MODELS service injected (e.g. isolated pipe test) — load
                # directly, with no cross-generation reuse.
                loaded[key] = loader()

        bundle = Trellis2ModelBundle(
            conditioner=loaded[f"native/trellis2/dino/{paths['image_encoder']}"],
            ss_flow=loaded[f"native/trellis2/ss_flow/{dit}"],
            ss_vae=loaded[f"native/trellis2/ss_vae/{shape_vae}"],
            shape_flow_lr=loaded[f"native/trellis2/shape_flow_512/{dit}"],
            shape_flow_hr=loaded.get(f"native/trellis2/shape_flow_1024/{dit}"),
            shape_decoder=loaded[f"native/trellis2/shape_decoder/{shape_vae}"],
            tex_flow=loaded[f"native/trellis2/tex_flow_{tex_tier}/{dit}"],
            tex_decoder=loaded[f"native/trellis2/tex_decoder/{paths['texture_vae']}"],
            matting=loaded.get(f"native/matting/{matting_path}") if matting_path else None,
            tier=tier,
            device=device,
        )
        return PipeOutput(output={"model": bundle})

    # -- helpers -----------------------------------------------------------

    def _tier(self) -> str:
        tier = str(self.config.get("resolution_tier", "1024"))
        if tier not in TIERS:
            raise ValueError(
                f"unknown resolution tier {tier!r}; expected one of {sorted(TIERS)}"
            )
        return tier

    def _weight_paths(self) -> Dict[str, str]:
        """The four checkpoints, checked for selection before anything loads."""
        paths, missing = {}, []
        for key, label in _WEIGHT_INPUTS.items():
            path = _path_of(self.config.get(key))
            if not path:
                missing.append(f"{label} ({key})")
            paths[key] = path

        if missing:
            raise ValueError(
                f"model_loader/trellis2 needs {len(missing)} more model file(s) selected: "
                + ", ".join(missing)
                + ". TRELLIS.2 loads four checkpoints: the transformer "
                "(diffusion_models/), the shape and texture VAEs (vae/), and the "
                "DINOv3 image encoder (text_encoders/)."
            )
        return paths

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None),
                               "MODEL LOADER TRELLIS2")


def _load_matting(path: str):
    """The BiRefNet matting model, with a load failure named rather than raw."""
    from src.platform.runtime.native.matting import BackgroundMattingModel

    try:
        return BackgroundMattingModel.from_checkpoint(path)
    except ValueError as exc:
        raise ValueError(f"the matting model could not be loaded: {exc}") from exc
