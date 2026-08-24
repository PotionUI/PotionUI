# Derived from: Fooocus modules/patch.py ADM scaler technique (GPL-3.0)
import torch
from src.pipelines.pipes.generator.sdxl.denoising_hook import DenoisingHook, DenoisingContext
from src.pipelines.pipes._shared.models.sdxl.kdiff_math import timestep_progress


class ADMGuidanceHook(DenoisingHook):
    """Fooocus-style ADM guidance for SDXL.

    Scales the WIDTH/HEIGHT (original-size) components of `add_time_ids` —
    positive conditioning up (default 1.5x), negative down (default 0.8x) —
    during the early structure-forming steps (default first 30%). This matches
    Fooocus's `patched_encode_adm`, which scales only the resolution values fed
    into the ADM embedding.

    It must NOT touch the pooled CLIP embeddings (`add_text_embeds`):
    scaling those distorts the text conditioning itself and produces
    burnt/oversaturated colors, especially on anime models whose pooled
    vector carries heavy quality-tag conditioning.
    """

    name = "adm_guidance"
    priority = 10  # Runs early

    def __init__(self, positive_scale: float, negative_scale: float, scaler_end: float):
        if not 0.0 <= scaler_end <= 1.0:
            raise ValueError(f"scaler_end must be in [0.0, 1.0], got {scaler_end}")
        self.positive_scale = positive_scale
        self.negative_scale = negative_scale
        self.scaler_end = scaler_end

    def should_apply_at_timestep(self, timestep: torch.Tensor, alphas_cumprod: torch.Tensor) -> bool:
        """Gate the cutover on actual noise-schedule progress, not step index.

        Matches Fooocus's `timed_adm` (modules/patch.py:330-337): ADM is scaled
        only while `1 - t/(num_train_timesteps-1) < scaler_end`, i.e. while the
        timestep itself is still early in the schedule. This differs from a
        step-index gate whenever the sampler uses a non-uniform sigma schedule
        (e.g. Karras), where step index and timestep progress diverge.
        """
        progress = timestep_progress(timestep, alphas_cumprod)
        return progress < self.scaler_end

    def on_pre_unet(self, ctx: DenoisingContext) -> DenoisingContext:
        if not self.should_apply_at_timestep(ctx.timestep, ctx.alphas_cumprod):
            return ctx
        if ctx.add_time_ids is None:
            return ctx

        # add_time_ids layout (diffusers SDXL):
        # [orig_height, orig_width, crop_top, crop_left, target_height, target_width]
        time_ids = ctx.add_time_ids.clone()
        if ctx.do_cfg:
            batch = time_ids.shape[0] // 2
            time_ids[:batch, 0:2] *= self.negative_scale
            time_ids[batch:, 0:2] *= self.positive_scale
        else:
            time_ids[:, 0:2] *= self.positive_scale
        ctx.add_time_ids = time_ids

        return ctx
