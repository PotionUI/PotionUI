from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import torch

from src.pipelines.contracts import IOType, PipeConfigSpec, PipeOutputSpec
from src.pipelines.pipes._shared.generation.loader_helpers import path_of as _path_of
from src.pipelines.pipes.model_loader.pixal3d.bundle import Pixal3DModelBundle
from src.pipelines.pipes.model_loader.trellis2.main import ModelLoaderTrellis2Pipe
from src.pipelines.pipes.model_loader.trellis2.weights import prefix_size_gb
from src.platform.runtime.native.arch.pixal3d import load as pixal3d_load
from src.platform.runtime.native.arch.pixal3d.config import (
    BUNDLE_MODES,
    DEFAULT_TIER,
    MULTIVIEW,
    PIXAL3D_TIERS,
    SINGLE_VIEW,
    bundle_mode_of,
)
from src.platform.runtime.native.arch.pixal3d.naf import NAF_PREFIX
from src.platform.runtime.native.arch.trellis2.detect import FLOW_PREFIXES
from src.platform.runtime.native.engine import NativeModel

_TRELLIS2_FLOW_KEYS = (
    "native/trellis2/ss_flow/",
    "native/trellis2/shape_flow_512/",
    "native/trellis2/shape_flow_1024/",
    "native/trellis2/tex_flow_",
)


class ModelLoaderPixal3DPipe(ModelLoaderTrellis2Pipe):
    name = "model_loader"
    description = "Load a native Pixal3D set (4 pixel-aligned flow DiTs + TRELLIS.2 decoders + DINOv3 with NAF)"

    _model_types = (
        ("diffusion_model", "pixal3d_dit"),
        ("shape_vae", "trellis2_shape_vae"),
        ("texture_vae", "trellis2_texture_vae"),
        ("image_encoder", "pixal3d_image_encoder"),
        ("matting_model", "matting"),
    )

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {**super().get_default_config(), "resolution_tier": DEFAULT_TIER, "bundle_mode": SINGLE_VIEW}

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        specs = [spec for spec in super().configuration() if spec.name not in ("diffusion_model", "image_encoder", "resolution_tier")]
        return [
            PipeConfigSpec("diffusion_model", dict, None,
                           "Pixal3D transformer bundle (all four flow DiTs). The single-view and multi-view "
                           "bundles share one key layout and are told apart by file name.", required=True),
            PipeConfigSpec("image_encoder", dict, None,
                           "DINOv3 ViT-L/16 image encoder with the NAF upsampler bundled (naf.* keys)",
                           required=True),
            *specs,
            PipeConfigSpec("resolution_tier", str, DEFAULT_TIER,
                           "Reconstruction cascade. Pixal3D has no single-pass 512 tier; 1536 is upstream's "
                           "default and degrades toward 1024 on the token budget.",
                           required=False, choices=list(PIXAL3D_TIERS)),
            PipeConfigSpec("bundle_mode", str, SINGLE_VIEW,
                           "Which bundle the mode expects: single (one image) or multiview (the front/left/"
                           "back/right rig). A mismatched file is refused.",
                           required=False, choices=list(BUNDLE_MODES)),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("model", IOType.MODEL, "Pixal3D model bundle", is_array=False)]

    def progress_message(self) -> str:
        path = _path_of(self.config.get("diffusion_model")) or "?"
        return f"Loading Pixal3D model <<MODEL:{Path(path).stem}>>"

    def _tier(self) -> str:
        tier = str(self.config.get("resolution_tier", DEFAULT_TIER))
        if tier not in PIXAL3D_TIERS:
            raise ValueError(
                f"unknown Pixal3D resolution tier {tier!r}; expected one of {list(PIXAL3D_TIERS)}"
            )
        return tier

    def _bundle_mode(self) -> str:
        mode = str(self.config.get("bundle_mode", SINGLE_VIEW))
        if mode not in BUNDLE_MODES:
            raise ValueError(f"unknown Pixal3D bundle mode {mode!r}; expected one of {list(BUNDLE_MODES)}")
        return mode

    def _weight_paths(self) -> Dict[str, str]:
        paths = super()._weight_paths()
        expected = self._bundle_mode()
        actual = bundle_mode_of(paths["diffusion_model"])
        name = Path(paths["diffusion_model"]).name
        if expected == MULTIVIEW and actual != MULTIVIEW:
            raise ValueError(
                f"{name} is the single-view Pixal3D bundle, but this mode reconstructs from several views "
                "and needs the multi-view one (pixal3d_multiview_bf16.safetensors). The two bundles have "
                "identical keys, so the file name tells them apart: it must contain 'multiview'."
            )
        if expected == SINGLE_VIEW and actual != SINGLE_VIEW:
            raise ValueError(
                f"{name} is the multi-view Pixal3D bundle (its name marks it multiview), but this mode "
                "reconstructs from one image and needs the single-view one (pixal3d_bf16.safetensors)."
            )
        return paths

    def _plan(self, paths: Dict[str, str], tier: str, dtype: torch.dtype) -> list:
        shared = [entry for entry in super()._plan(paths, tier, dtype) if not entry[1].startswith(_TRELLIS2_FLOW_KEYS)]
        dit, encoder = paths["diffusion_model"], paths["image_encoder"]
        return [
            *shared,
            ("NAF upsampler", f"native/pixal3d/naf/{encoder}",
             lambda: NativeModel("text_encoder", pixal3d_load.load_pixal3d_naf(encoder)),
             prefix_size_gb(encoder, NAF_PREFIX)),
            ("sparse-structure flow", f"native/pixal3d/ss_flow/{dit}",
             lambda: NativeModel("diffusion_model", pixal3d_load.load_pixal3d_ss_flow(dit, dtype=dtype)),
             prefix_size_gb(dit, FLOW_PREFIXES["structure"])),
            ("shape flow", f"native/pixal3d/shape_flow_512/{dit}",
             lambda: NativeModel("diffusion_model", pixal3d_load.load_pixal3d_shape_flow(dit, "512", dtype=dtype)),
             prefix_size_gb(dit, FLOW_PREFIXES["shape_512"])),
            ("high-resolution shape flow", f"native/pixal3d/shape_flow_1024/{dit}",
             lambda: NativeModel("diffusion_model", pixal3d_load.load_pixal3d_shape_flow(dit, "1024", dtype=dtype)),
             prefix_size_gb(dit, FLOW_PREFIXES["shape_1024"])),
            ("texture flow", f"native/pixal3d/tex_flow_1024/{dit}",
             lambda: NativeModel("diffusion_model", pixal3d_load.load_pixal3d_tex_flow(dit, dtype=dtype)),
             prefix_size_gb(dit, FLOW_PREFIXES["texture"])),
        ]

    def _bundle(self, loaded: Dict[str, Any], paths: Dict[str, str], tier: str, device: str):
        matting_path = _path_of(self.config.get("matting_model"))
        dit, shape_vae, encoder = paths["diffusion_model"], paths["shape_vae"], paths["image_encoder"]
        return Pixal3DModelBundle(
            conditioner=loaded[f"native/trellis2/dino/{encoder}"],
            naf=loaded[f"native/pixal3d/naf/{encoder}"],
            ss_flow=loaded[f"native/pixal3d/ss_flow/{dit}"],
            ss_vae=loaded[f"native/trellis2/ss_vae/{shape_vae}"],
            shape_flow_lr=loaded[f"native/pixal3d/shape_flow_512/{dit}"],
            shape_flow_hr=loaded[f"native/pixal3d/shape_flow_1024/{dit}"],
            shape_decoder=loaded[f"native/trellis2/shape_decoder/{shape_vae}"],
            tex_flow=loaded[f"native/pixal3d/tex_flow_1024/{dit}"],
            tex_decoder=loaded[f"native/trellis2/tex_decoder/{paths['texture_vae']}"],
            matting=loaded.get(f"native/matting/{matting_path}") if matting_path else None,
            tier=tier,
            device=device,
            variant=self._bundle_mode(),
        )
