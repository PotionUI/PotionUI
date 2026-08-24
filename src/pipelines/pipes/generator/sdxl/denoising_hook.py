"""
Denoising Hook System for SDXL

Provides an extensible hook-based architecture for the SDXL denoising process.
Enhancement features (ADM Guidance, Sharpness, SAG) register hooks that are called
at specific points during each denoising step, rather than being baked into the
model wrapper.

Hook lifecycle in apply_model():
    1. ControlNet (core - stays in model wrapper)
    2. on_pre_unet hooks (e.g., ADM modifies conditioning)
    3. UNet forward pass
    4. on_post_unet hooks (e.g., SAG, Sharpness modify noise_pred before CFG)
    5. CFG application
    6. on_post_cfg hooks (post-CFG modifications)
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Dict
import torch


@dataclass
class DenoisingContext:
    """State passed to hooks at each denoising step.

    This context is mutable - hooks modify fields in-place and return
    the updated context. The model wrapper reads back modified values.
    """
    # Core state
    unet: Any
    latent_model_input: torch.Tensor
    timestep: torch.Tensor
    noise_pred: Optional[torch.Tensor]
    current_step: int
    total_steps: int
    progress: float  # 0.0 to 1.0

    # Conditioning
    prompt_embeds: torch.Tensor
    add_text_embeds: torch.Tensor
    add_time_ids: torch.Tensor
    cross_attention_kwargs: Optional[Dict[str, Any]]

    # Guidance state
    do_cfg: bool
    guidance_scale: float

    # Noise schedule
    alphas_cumprod: torch.Tensor

    # ControlNet residuals (filled by ControlNet in model wrapper)
    down_block_res_samples: Optional[Any] = None
    mid_block_res_sample: Optional[torch.Tensor] = None

    # Inpaint head feature (for hooks that need to re-run UNet, e.g. SAG)
    inpaint_head_feature: Optional[torch.Tensor] = None

    # Original latent (before CFG doubling, needed by sharpness)
    original_latent: Optional[torch.Tensor] = None

    # Scratch space for hooks to carry state from on_post_unet to on_post_cfg
    # (e.g. SAG stashes the recorded attention map + uncond eps here, since
    # on_post_cfg only sees the already-combined noise_pred).
    sag_state: Optional[Dict[str, Any]] = None


class DenoisingHook:
    """Base class for hooks into the denoising process.

    Subclasses override one or more of the lifecycle methods to modify
    the denoising context at specific points during each step.

    Attributes:
        name: Human-readable name for logging/debugging.
        priority: Execution order (lower = runs first).
    """
    name: str = "unnamed"
    priority: int = 0

    def on_pre_unet(self, ctx: DenoisingContext) -> DenoisingContext:
        """Called before UNet forward pass.

        Use this to modify conditioning (add_text_embeds, add_time_ids,
        prompt_embeds) before they are fed to the UNet.

        Args:
            ctx: Current denoising context. noise_pred is None at this point.

        Returns:
            Modified denoising context.
        """
        return ctx

    def on_post_unet(self, ctx: DenoisingContext) -> DenoisingContext:
        """Called after UNet forward pass, before CFG split.

        Use this to modify noise_pred while it still contains both
        unconditional and conditional predictions (if CFG is enabled).

        Args:
            ctx: Current denoising context. noise_pred contains raw UNet output.

        Returns:
            Modified denoising context.
        """
        return ctx

    def on_post_cfg(self, ctx: DenoisingContext) -> DenoisingContext:
        """Called after CFG application.

        Use this to modify the final noise prediction after guidance
        has been applied.

        Args:
            ctx: Current denoising context. noise_pred is the guided prediction.

        Returns:
            Modified denoising context.
        """
        return ctx

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', priority={self.priority})"
