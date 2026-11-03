from __future__ import annotations

from typing import Any, List, Optional, Tuple

import numpy as np
import torch

from src.pipelines.contracts import PipeConfigSpec
from src.pipelines.pipes._shared.video.tubes.detailer import (
    TubeClip,
    VideoTubeDetailerPipe,
    tube_detailer_configuration,
)
from src.pipelines.pipes.detailer.video_minimax_h3.refine import fit_h3_canvas, refine_tube_pixels
from src.pipelines.pipes.generator.video_minimax_h3.samplers import SAMPLERS
from src.pipelines.pipes.generator.video_minimax_h3.schedule import SCHEDULERS, SIMPLE_SCHEDULER


class DetailerVideoMinimaxH3Pipe(VideoTubeDetailerPipe):
    description = ("Refine faces and hands in a MiniMax-H3 video as stabilized "
                   "spatiotemporal tubes (same model, light denoise, feathered paste-back)")
    model_family = "minimax_h3"
    model_label = "MiniMax-H3"
    pipe_label = "detailer/video_minimax_h3"

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return tube_detailer_configuration() + [
            PipeConfigSpec("refine_steps", int, 6, "Denoising steps per tube", required=False,
                           min_value=1, max_value=30),
            PipeConfigSpec("refine_video_sigma_shift", float, 1.0,
                           "Video sigma shift for the tube refine (1.0 makes strength the starting sigma)",
                           required=False, min_value=0.1, max_value=50.0),
            PipeConfigSpec("refine_sampler", str, "euler", "Solver for the tube refine", required=False,
                           choices=list(SAMPLERS)),
            PipeConfigSpec("refine_scheduler", str, SIMPLE_SCHEDULER, "Knot placement for the tube refine",
                           required=False, choices=list(SCHEDULERS)),
        ]

    def own_models(self, bundle: Any) -> Tuple[Any, ...]:
        return (getattr(bundle, "video_vae", None), getattr(bundle, "audio_vae", None), getattr(bundle, "dit", None))

    def working_resolution(self, window: Any) -> Tuple[int, int]:
        return fit_h3_canvas(*super().working_resolution(window))

    def refine_tube(
        self, bundle: Any, cond_model: Any, pixels: torch.Tensor, clip: TubeClip, *,
        strength: str, device: str, seed: int, generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> np.ndarray:
        return refine_tube_pixels(
            bundle, cond_model, pixels, clip, strength=strength, device=device, seed=seed,
            generation_outputs=generation_outputs, is_cancelled=is_cancelled,
            steps=int(self.config.get("refine_steps", 6)),
            video_sigma_shift=float(self.config.get("refine_video_sigma_shift", 1.0)),
            sampler=str(self.config.get("refine_sampler", "euler")),
            scheduler=str(self.config.get("refine_scheduler", SIMPLE_SCHEDULER)),
        )
