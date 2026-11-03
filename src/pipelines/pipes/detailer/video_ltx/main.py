from __future__ import annotations

from typing import Any, Optional, Tuple

import numpy as np
import torch

from src.pipelines.pipes._shared.video.tubes.detailer import TubeClip, VideoTubeDetailerPipe
from src.pipelines.pipes.detailer.video_ltx.refine import refine_tube_pixels


class DetailerVideoLtxPipe(VideoTubeDetailerPipe):
    description = ("Refine faces and hands in an LTX-2/2.3 video as stabilized "
                   "spatiotemporal tubes (same model, light denoise, feathered paste-back)")
    model_family = "ltx"
    model_label = "LTX-2/2.3"
    pipe_label = "detailer/video_ltx"

    def own_models(self, bundle: Any) -> Tuple[Any, ...]:
        return (getattr(bundle, "vae", None), getattr(bundle, "dit", None))

    def release_models(self, bundle: Any) -> None:
        bundle.dit.offload()
        bundle.vae.offload()

    def refine_tube(
        self, bundle: Any, cond_model: Any, pixels: torch.Tensor, clip: TubeClip, *,
        strength: str, device: str, seed: int, generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> np.ndarray:
        return refine_tube_pixels(
            bundle, cond_model, pixels, strength=strength, device=device, fps=clip.fps, seed=seed,
            is_cancelled=is_cancelled)
