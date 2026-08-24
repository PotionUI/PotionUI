import torch
from src.pipelines.pipes.generator.sdxl.denoising_hook import DenoisingHook, DenoisingContext
from src.pipelines.pipes.generator.sdxl.sharpness_filter import AnisotropicSharpness
from src.pipelines.pipes._shared.models.sdxl.kdiff_math import timestep_progress


class SharpnessHook(DenoisingHook):
    name = "sharpness"
    priority = 50  # Runs after SAG

    def __init__(self, strength: float):
        self.sharpness = AnisotropicSharpness(strength=strength)

    def on_post_unet(self, ctx: DenoisingContext) -> DenoisingContext:
        if not self.sharpness.is_enabled() or not ctx.do_cfg:
            return ctx

        # Split noise predictions for CFG
        uncond, text = ctx.noise_pred.chunk(2)

        # Calculate progress from timestep
        progress = timestep_progress(ctx.timestep, ctx.alphas_cumprod)

        # Apply sharpness filter to text prediction only
        # original_latent is the latent before CFG doubling
        latent = ctx.original_latent if ctx.original_latent is not None else ctx.latent_model_input[:text.shape[0]]

        text = self.sharpness.apply_during_denoising(
            noise_pred=text,
            latent=latent,
            timestep=ctx.timestep[:1] if ctx.timestep.ndim > 0 else ctx.timestep,
            alphas_cumprod=ctx.alphas_cumprod,
            progress=progress,
        )

        ctx.noise_pred = torch.cat([uncond, text])
        return ctx
