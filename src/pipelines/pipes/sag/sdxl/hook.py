# Derived from: ComfyUI comfy_extras/nodes_sag.py (GPL-3.0) — reworked implementation
"""
Self-Attention Guidance (SAG) for SDXL.

Reworked to match ComfyUI's `SelfAttentionGuidance` node
(comfy_extras/nodes_sag.py: SelfAttentionGuidance + create_blur_map), which is
itself the reference implementation of Hong et al., "Improving Sample Quality
of Diffusion Models Using Self-Attention Guidance". The previous
implementation in this file blurred post-softmax attention WEIGHTS after
collapsing the query dimension (destroying per-position structure), installed
a custom processor on every attention layer (incl. cross-attention), reran
both CFG branches, and mixed the correction before CFG. None of that matches
the paper or ComfyUI's reference.

The real mechanism:
  1. During the single normal UNet forward, record the explicit self-attention
     probabilities of ONE attention layer (the mid-block's `attn1`), but only
     for the UNCOND half of the CFG batch.
  2. After CFG combination, build a "blur map": per-position attention
     magnitude, thresholded into a binary mask, upsampled to latent
     resolution.
  3. Gaussian-blur the uncond x0 prediction where the mask is set, leaving it
     unchanged elsewhere -> "degraded" x0.
  4. Re-noise the degraded x0 back to the current noise level, keeping the
     SAME noise realization as the uncond branch (only the signal component
     changes).
  5. Run ONE extra UNet forward on the re-noised latent, uncond conditioning
     only.
  6. Mix AFTER CFG: result = cfg_result + (degraded_x0 - sag_x0) * sag_scale,
     translated into this wrapper's eps-space via the same alpha/sigma
     round-trip the sharpness hook uses (`AnisotropicSharpness`).

Batch order for CFG in this codebase is always [uncond, cond]
(see src/pipelines/pipes/generator/sdxl/conditioning_builder.py:137-143), so the uncond
half is always the FIRST half of any doubled batch.

BREAKING CHANGE: the previous `threshold` config key gated SAG on/off by
*generation progress* (an invented product knob with no equivalent in the
reference implementation). It has been replaced by `sag_threshold`, which
matches the reference's attention-magnitude threshold (default 1.0) used to
binarize the blur mask. SAG is no longer step-gated - once enabled (scale>0)
it runs on every step, exactly like ComfyUI's node.
"""
import math

import torch
import torch.nn.functional as F
from diffusers.models.attention_processor import AttnProcessor
from diffusers.utils import logging

from src.pipelines.pipes.generator.sdxl.denoising_hook import DenoisingHook, DenoisingContext
from src.pipelines.pipes._shared.models.sdxl.kdiff_math import (
    eps_to_x0,
    x0_to_eps,
    alpha_for_timestep as get_alpha_for_timestep,
)

logger = logging.get_logger(__name__)


class _SAGRecordingAttnProcessor(AttnProcessor):
    """Legacy (explicit-softmax) AttnProcessor that additionally records the
    self-attention probabilities for the uncond half of the batch.

    We can't recover attention weights from the fused SDPA path, so this
    reimplements diffusers' pre-2.0 `AttnProcessor.__call__` (explicit
    QK^T -> softmax -> bmm(V)) and stashes the probabilities before
    returning - functionally identical output to the default processor,
    just with a side channel for the mask builder. Installed on exactly one
    module (the mid-block's attn1) for the duration of a single forward pass.
    """

    def __init__(self, uncond_batch: int):
        super().__init__()
        self.uncond_batch = uncond_batch
        self.recorded = None

    def __call__(self, attn, hidden_states, encoder_hidden_states=None, attention_mask=None, temb=None, *args, **kwargs):
        residual = hidden_states
        input_ndim = hidden_states.ndim
        if input_ndim == 4:
            batch_size, channel, height, width = hidden_states.shape
            hidden_states = hidden_states.view(batch_size, channel, height * width).transpose(1, 2)

        batch_size, sequence_length, _ = (
            hidden_states.shape if encoder_hidden_states is None else encoder_hidden_states.shape
        )
        attention_mask = attn.prepare_attention_mask(attention_mask, sequence_length, batch_size)

        if attn.group_norm is not None:
            hidden_states = attn.group_norm(hidden_states.transpose(1, 2)).transpose(1, 2)

        query = attn.to_q(hidden_states)
        if encoder_hidden_states is None:
            encoder_hidden_states = hidden_states
        elif attn.norm_cross:
            encoder_hidden_states = attn.norm_encoder_hidden_states(encoder_hidden_states)
        key = attn.to_k(encoder_hidden_states)
        value = attn.to_v(encoder_hidden_states)

        query = attn.head_to_batch_dim(query)
        key = attn.head_to_batch_dim(key)
        value = attn.head_to_batch_dim(value)

        attention_probs = attn.get_attention_scores(query, key, attention_mask)

        # attention_probs shape: [batch*heads, seq_q, seq_k]. Batch order
        # matches hidden_states' incoming batch (uncond first, per
        # conditioning_builder.py), so the first uncond_batch*heads rows are
        # the uncond self-attention we want for the blur mask.
        n_slices = attn.heads * self.uncond_batch
        self.recorded = attention_probs[:n_slices].detach()

        hidden_states = torch.bmm(attention_probs, value)
        hidden_states = attn.batch_to_head_dim(hidden_states)

        hidden_states = attn.to_out[0](hidden_states)
        hidden_states = attn.to_out[1](hidden_states)

        if input_ndim == 4:
            hidden_states = hidden_states.transpose(-1, -2).reshape(batch_size, channel, height, width)
        if attn.residual_connection:
            hidden_states = hidden_states + residual
        hidden_states = hidden_states / attn.rescale_output_factor
        return hidden_states


def get_target_attn_module(unet):
    """The single self-attention layer SAG records from.

    Matches ComfyUI's target: `unet.mid_block.attentions[0].transformer_blocks[0].attn1`
    (see comfy_extras/nodes_sag.py's `set_model_attn1_replace(..., "middle", 0, 0)`).
    """
    return unet.mid_block.attentions[0].transformer_blocks[0].attn1


def gaussian_blur_2d(img: torch.Tensor, kernel_size: int, sigma: float) -> torch.Tensor:
    """Separable Gaussian blur via a 2D depthwise conv, matching ComfyUI's
    `gaussian_blur_2d` in comfy_extras/nodes_sag.py exactly (reflect padding)."""
    ksize_half = (kernel_size - 1) * 0.5
    x = torch.linspace(-ksize_half, ksize_half, steps=kernel_size)
    pdf = torch.exp(-0.5 * (x / sigma).pow(2))
    x_kernel = (pdf / pdf.sum()).to(device=img.device, dtype=img.dtype)
    kernel2d = torch.mm(x_kernel[:, None], x_kernel[None, :])
    kernel2d = kernel2d.expand(img.shape[-3], 1, kernel2d.shape[0], kernel2d.shape[1])
    padding = [kernel_size // 2] * 4
    img = F.pad(img, padding, mode="reflect")
    return F.conv2d(img, kernel2d, groups=img.shape[-3])


def build_blur_map(x0: torch.Tensor, attn: torch.Tensor, sigma: float, threshold: float) -> torch.Tensor:
    """Build the SAG-degraded x0: Gaussian-blur x0 wherever the recorded
    self-attention magnitude exceeds `threshold`, leave it untouched
    elsewhere. Direct port of ComfyUI's `create_blur_map`.

    Args:
        x0: predicted clean image for the uncond branch [B, C, H, W]
        attn: recorded self-attention probs [B*heads, seq_q, seq_k]
        sigma: Gaussian blur sigma
        threshold: attention-magnitude cutoff for the binary mask
    """
    _, hw1, hw2 = attn.shape
    b, _, lh, lw = x0.shape
    attn = attn.reshape(b, -1, hw1, hw2)
    # Global average pool over heads and query positions -> per-key-position magnitude
    mask = attn.mean(1, keepdim=False).sum(1, keepdim=False) > threshold

    total = mask.shape[-1]
    x = round(math.sqrt((lh / lw) * total))
    xx = None
    for i in range(0, math.floor(math.sqrt(total) / 2)):
        for j in (x + i, max(1, x - i)):
            if total % j == 0:
                xx = j
                break
        if xx is not None:
            break
    x = xx if xx is not None else max(1, round(math.sqrt(total)))
    y = total // x

    mask = mask.reshape(b, x, y).unsqueeze(1).type(attn.dtype)
    mask = F.interpolate(mask, (lh, lw))

    blurred = gaussian_blur_2d(x0, kernel_size=9, sigma=sigma)
    return blurred * mask + x0 * (1.0 - mask)


class SAGHook(DenoisingHook):
    """Self-Attention Guidance for SDXL, ComfyUI-parity implementation.

    Off by default (scale=0.0 is a no-op). When enabled, adds one extra UNet
    forward per step (uncond-only, so cheaper than the previous
    implementation's full extra forward with both branches).
    """

    name = "sag"
    priority = 45  # After ADM (10, pre-unet), before sharpness (50, post-unet)

    def __init__(self, scale: float = 0.75, sigma: float = 2.0, sag_threshold: float = 1.0):
        self.scale = scale
        self.sigma = sigma
        self.sag_threshold = sag_threshold
        self._processor = None
        self._original_processor = None
        self._target_module = None

    def _enabled(self, ctx: DenoisingContext) -> bool:
        return ctx.do_cfg and self.scale != 0.0

    def on_pre_unet(self, ctx: DenoisingContext) -> DenoisingContext:
        if not self._enabled(ctx):
            return ctx

        try:
            target_module = get_target_attn_module(ctx.unet)
        except (AttributeError, IndexError):
            logger.warning("[SAG] Could not locate mid-block attn1 - skipping this step")
            self._target_module = None
            return ctx

        uncond_batch = ctx.latent_model_input.shape[0] // 2
        self._target_module = target_module
        self._original_processor = target_module.processor
        self._processor = _SAGRecordingAttnProcessor(uncond_batch=uncond_batch)
        target_module.set_processor(self._processor)
        return ctx

    def on_post_unet(self, ctx: DenoisingContext) -> DenoisingContext:
        if not self._enabled(ctx) or self._target_module is None:
            return ctx

        # We only needed the custom processor for the single recording
        # forward pass that just completed - restore immediately so the
        # extra uncond-only forward (in on_post_cfg) uses the fast path.
        self._target_module.set_processor(self._original_processor)
        recorded_attn = self._processor.recorded
        self._target_module = None
        self._processor = None
        self._original_processor = None

        if recorded_attn is None:
            return ctx
        if min(ctx.original_latent.shape[2:]) <= 4:
            # Too small to meaningfully pad/blur - skip, matching ComfyUI's guard.
            return ctx

        batch = ctx.noise_pred.shape[0] // 2
        ctx.sag_state = {
            "uncond_eps": ctx.noise_pred[:batch].detach(),
            "attn": recorded_attn,
        }
        return ctx

    def on_post_cfg(self, ctx: DenoisingContext) -> DenoisingContext:
        sag_state = ctx.sag_state
        if not self._enabled(ctx) or sag_state is None:
            return ctx
        ctx.sag_state = None

        latent = ctx.original_latent
        # ctx.timestep is expanded across the (possibly CFG-doubled) batch
        # with a single repeated value (see model_wrapper.py's
        # `t.expand(latent_model_input.shape[0])`) - take one entry so alpha_t
        # broadcasts against the true batch, same idiom as the sharpness hook.
        timestep_scalar = ctx.timestep[:1] if ctx.timestep.ndim > 0 else ctx.timestep
        alpha_t = get_alpha_for_timestep(timestep_scalar, ctx.alphas_cumprod).to(latent.device, latent.dtype)

        uncond_eps = sag_state["uncond_eps"]
        uncond_x0 = eps_to_x0(latent, uncond_eps, alpha_t)
        degraded_x0 = build_blur_map(uncond_x0, sag_state["attn"], self.sigma, self.sag_threshold)

        # Re-noise the degraded x0 to the current noise level, keeping the
        # SAME noise realization as the uncond branch - only the signal
        # component changes.
        sqrt_alpha_t = torch.sqrt(alpha_t)
        sqrt_one_minus_alpha_t = torch.sqrt(1.0 - alpha_t)
        degraded_noised = sqrt_alpha_t * degraded_x0 + sqrt_one_minus_alpha_t * uncond_eps

        batch = latent.shape[0]
        added_cond_kwargs = {
            "text_embeds": ctx.add_text_embeds[:batch],
            "time_ids": ctx.add_time_ids[:batch],
        }
        model_dtype = next(ctx.unet.parameters()).dtype
        timestep = ctx.timestep[:batch] if ctx.timestep.ndim > 0 else ctx.timestep
        with torch.no_grad():
            sag_eps = ctx.unet(
                degraded_noised.to(model_dtype),
                timestep,
                encoder_hidden_states=ctx.prompt_embeds[:batch],
                cross_attention_kwargs=ctx.cross_attention_kwargs,
                added_cond_kwargs=added_cond_kwargs,
                return_dict=False,
            )[0]

        sag_x0 = eps_to_x0(degraded_noised, sag_eps, alpha_t)

        cfg_x0 = eps_to_x0(latent, ctx.noise_pred, alpha_t)
        result_x0 = cfg_x0 + (degraded_x0 - sag_x0) * self.scale
        ctx.noise_pred = x0_to_eps(latent, result_x0, alpha_t)

        return ctx
