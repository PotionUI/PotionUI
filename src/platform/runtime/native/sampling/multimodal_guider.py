"""MultiModalGuider: Lightricks' first-party guidance recipe for LTX-2.3.

Ported from the Lightricks LTX-2 repository (Apache-2.0, rev a2c3f24):
  - packages/ltx-core/src/ltx_core/components/guiders.py
    (MultiModalGuiderParams, MultiModalGuider.calculate, rescale, skip_step)
  - packages/ltx-pipelines/src/ltx_pipelines/utils/denoisers.py
    (FactoryGuidedDenoiser: per-step pass collection, batched forward)
  - packages/ltx-pipelines/src/ltx_pipelines/utils/constants.py
    (LTX_2_3_PARAMS: video cfg=3.0 stg=1.0 rescale=0.7 modality=3.0
     stg_blocks=[28]; audio cfg=7.0; 30 steps)

Adapted for PotionUI's native sampling architecture: a ``GuidanceStrategy``
object called once per step by the sampler, operating over the PACKED state
tensor (video tokens ++ [extra] ++ audio tokens) that the LTX generator pipes
manage.  The pipe-side ``model_forward`` is called N times per step (pos,
neg, stg-perturbed, modality-off) with conditioning-dict flags the arch
forward knows how to route; this strategy orchestrates those calls and
combines them per the reference formula.

Design constraints:
  - CPU-only testable (no model weights needed -- the guider is pure math on
    the model outputs, not on the model itself).
  - Model-side hooks (STG skip-self-attn, modality-off) are conditioning-dict
    keys: ``"stg_skip_blocks"`` (list[int]) and ``"disable_cross_modal"``
    (bool).  The arch ``forward`` reads them and applies the reference-exact
    perturbation semantics: STG replaces self-attention output with value-
    projection passthrough (v = to_v(norm_input) through gating+to_out);
    modality-off disables a2v/v2a cross-attention (zero residual contribution).
    This decouples the guider (strategy) from the model (arch module).
  - FBCache / NAG: INCOMPATIBLE with this multi-pass strategy.  When active,
    step_cache and NAG are gated off with a log warning (no deep integration
    in this port).

Forward-count accounting per step (reference-matching):
  1. **cond** (positive forward): always.
  2. **uncond** (negative forward): only when cfg_scale != 1.0.
  3. **perturbed** (STG forward): only when stg_scale != 0.0.
  4. **modality-off** (cross-modal disabled): only when modality_scale != 1.0.
  Total: 1-4 forwards per step (typical quality recipe: 4).
  ``skip_step`` (reference: ``step % (skip_step+1) != 0``) reuses the previous
  step's output outright, paying ZERO forwards.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from typing import Sequence

import torch

from .cfg import GuidanceStrategy

logger = logging.getLogger(__name__)

Tensor = torch.Tensor


@dataclass(frozen=True)
class MultiModalGuiderParams:
    """Per-modality guidance parameters.

    Ported from ltx_core.components.guiders.MultiModalGuiderParams
    (Apache-2.0, rev a2c3f24).
    """

    cfg_scale: float = 1.0
    stg_scale: float = 0.0
    stg_blocks: list[int] = field(default_factory=list)
    rescale_scale: float = 0.0
    modality_scale: float = 1.0
    skip_step: int = 0


# LTX_2_3_PARAMS defaults (from constants.py, rev a2c3f24):
LTX_23_VIDEO_PARAMS = MultiModalGuiderParams(
    cfg_scale=3.0, stg_scale=1.0, rescale_scale=0.7,
    modality_scale=3.0, skip_step=0, stg_blocks=[28],
)
LTX_23_AUDIO_PARAMS = MultiModalGuiderParams(
    cfg_scale=7.0, stg_scale=1.0, rescale_scale=0.7,
    modality_scale=3.0, skip_step=0, stg_blocks=[28],
)


def _needs_uncond(p: MultiModalGuiderParams) -> bool:
    return not math.isclose(p.cfg_scale, 1.0)


def _needs_perturbed(p: MultiModalGuiderParams) -> bool:
    return not math.isclose(p.stg_scale, 0.0)


def _needs_modality_off(p: MultiModalGuiderParams) -> bool:
    return not math.isclose(p.modality_scale, 1.0)


def _neutralize_modality(p: MultiModalGuiderParams) -> MultiModalGuiderParams:
    """Params with modality_scale forced to 1.0 (neutral coefficient).

    Used when the counterpart modality is absent from the packed state, so
    the modality-off forward never runs and ``modality_off`` is the ``0.0``
    sentinel -- same reasoning as the ``stg``/``cfg`` sentinels, whose
    coefficient (``stg_scale`` / ``cfg_scale - 1``) is what makes the
    sentinel value irrelevant. ``modality_scale`` is decoupled from the
    "does the forward exist" gate (``has_audio``), so it must be neutralized
    explicitly rather than relying on ``modality_scale`` alone.
    """
    if math.isclose(p.modality_scale, 1.0):
        return p
    return replace(p, modality_scale=1.0)


def _should_skip_step(p: MultiModalGuiderParams, step: int) -> bool:
    """Reference: step % (skip_step + 1) != 0."""
    if p.skip_step == 0:
        return False
    return step % (p.skip_step + 1) != 0


def multimodal_combine(
    cond: Tensor,
    uncond: Tensor | float,
    perturbed: Tensor | float,
    modality_off: Tensor | float,
    params: MultiModalGuiderParams,
) -> Tensor:
    """Reference combination formula (guiders.py MultiModalGuider.calculate).

    pred = cond
         + (cfg_scale - 1) * (cond - uncond)
         + stg_scale * (cond - perturbed)
         + (modality_scale - 1) * (cond - modality_off)

    Then std-preserving rescale:
      factor = rescale_scale * (std(cond) / std(pred)) + (1 - rescale_scale)
      pred = pred * factor

    All terms whose scale disables them receive 0.0 as the uncond/perturbed/
    modality_off tensor (not computed), making their delta zero.
    """
    pred = (
        cond
        + (params.cfg_scale - 1) * (cond - uncond)
        + params.stg_scale * (cond - perturbed)
        + (params.modality_scale - 1) * (cond - modality_off)
    )

    if not math.isclose(params.rescale_scale, 0.0):
        # Reference: cond.std() / pred.std(), global (all dims), then
        # blend factor toward 1.0 by (1 - rescale_scale).
        factor = cond.std() / pred.std()
        factor = params.rescale_scale * factor + (1 - params.rescale_scale)
        pred = pred * factor

    return pred


class MultiModalGuidance:
    """GuidanceStrategy for LTX-2.3's MultiModalGuider (quality recipe).

    Manages up to 4 forward passes per step over the PACKED state tensor.
    The generator pipe supplies ``video_params``/``audio_params`` and the
    slice boundary (``video_tokens``) via the conditioning dict so this
    strategy knows where to split, apply per-modality combination, and
    repack.

    Conditioning-dict contract (set by the generator pipe):
      - ``"mm_video_params"``: MultiModalGuiderParams for the video slice
      - ``"mm_audio_params"``: MultiModalGuiderParams for the audio slice
        (absent when video-only)
      - ``"mm_video_tokens"``: int, number of video tokens in the packed state

    The ``cond`` dict also carries the usual ``"context"`` key for the text
    conditioning, and ``uncond`` (if non-None) carries the negative context.
    """

    def __init__(
        self,
        video_params: MultiModalGuiderParams,
        audio_params: MultiModalGuiderParams | None = None,
    ) -> None:
        self.video_params = video_params
        self.audio_params = audio_params or MultiModalGuiderParams()
        self._last_video: Tensor | None = None
        self._last_audio: Tensor | None = None
        # Protocol surface for denoise_loop compatibility:
        self.last_cond_v: Tensor | None = None
        self.last_uncond_v: Tensor | None = None
        self.zero_init_steps = 0

    def __call__(
        self,
        model_fn,
        x: Tensor,
        sigma: Tensor,
        cond: dict,
        uncond: dict | None,
        step_index: int,
    ) -> Tensor:
        vp = self.video_params
        ap = self.audio_params

        # Read slice boundary from cond dict (set by the generator pipe)
        v_tokens = cond.get("mm_video_tokens", x.shape[1])
        has_audio = v_tokens < x.shape[1]

        v_skip = _should_skip_step(vp, step_index)
        a_skip = _should_skip_step(ap, step_index) if has_audio else True

        if v_skip and a_skip:
            if self._last_video is not None:
                # Reuse last step's output
                out = self._last_video
                if self._last_audio is not None:
                    out = torch.cat([out, self._last_audio], dim=1)
                return out
            # First step can't skip -- fall through

        # 1. Conditioned (positive) forward -- always needed
        cond_v = model_fn(x, sigma, cond)
        self.last_cond_v = cond_v

        # Split into video / audio slices
        cond_video = cond_v[:, :v_tokens]
        cond_audio = cond_v[:, v_tokens:] if has_audio else None

        # 2. Unconditioned (negative) forward -- only when any modality needs CFG
        if (_needs_uncond(vp) or _needs_uncond(ap)) and uncond is not None:
            uncond_v = model_fn(x, sigma, uncond)
            self.last_uncond_v = uncond_v
            uncond_video = uncond_v[:, :v_tokens]
            uncond_audio = uncond_v[:, v_tokens:] if has_audio else None
        else:
            self.last_uncond_v = None
            uncond_video = 0.0
            uncond_audio = 0.0

        # 3. STG perturbed forward -- only when any modality needs STG
        if _needs_perturbed(vp) or _needs_perturbed(ap):
            stg_cond = {
                **cond,
                "stg_skip_blocks": list(set(
                    (vp.stg_blocks or []) + (ap.stg_blocks or [])
                )),
            }
            ptb_v = model_fn(x, sigma, stg_cond)
            ptb_video = ptb_v[:, :v_tokens]
            ptb_audio = ptb_v[:, v_tokens:] if has_audio else None
        else:
            ptb_video = 0.0
            ptb_audio = 0.0

        # 4. Modality-off forward -- only when any modality needs it
        if (has_audio and (_needs_modality_off(vp) or _needs_modality_off(ap))):
            mod_cond = {**cond, "disable_cross_modal": True}
            mod_v = model_fn(x, sigma, mod_cond)
            mod_video = mod_v[:, :v_tokens]
            mod_audio = mod_v[:, v_tokens:] if has_audio else None
        else:
            mod_video = 0.0
            mod_audio = 0.0

        # Combine per-modality
        if v_skip and self._last_video is not None:
            combined_video = self._last_video
        else:
            video_combine_params = vp if has_audio else _neutralize_modality(vp)
            combined_video = multimodal_combine(
                cond_video, uncond_video, ptb_video, mod_video, video_combine_params
            )
            self._last_video = combined_video

        if has_audio:
            if a_skip and self._last_audio is not None:
                combined_audio = self._last_audio
            else:
                combined_audio = multimodal_combine(
                    cond_audio, uncond_audio, ptb_audio, mod_audio, ap
                )
                self._last_audio = combined_audio
            return torch.cat([combined_video, combined_audio], dim=1)

        self._last_audio = None
        return combined_video

    def degraded_forward(self, model_fn, x, sigma, cond, step_index, skip_layers) -> Tensor:
        """SLG hook surface (not expected to be used with this strategy)."""
        return model_fn(x, sigma, {**cond, "skip_layers": skip_layers})
