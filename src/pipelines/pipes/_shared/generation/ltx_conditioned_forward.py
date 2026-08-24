"""``model_forward`` over an LTX packed conditioned state.

Shared by ``generator/video_ltx`` (whole-clip conditioned generation and the
stage-2 refine) and ``generator/dfr_video_ltx`` (one call per temporal tile).
Both drive the same AV DiT over one packed state tensor
``[1, S_base + N_extra (+ T_a), 128]`` -- LTX's video token dim (128 latent
channels, patch 1) equals its packed audio dim (8 ch x 16 mel), so base video
tokens, appended conditioning tokens and audio tokens ride the same sampler
state.

Conditioned tokens are held to their ``clean`` values at every model call via
two mechanisms (diffusers ``pipeline_ltx2_condition`` denoising-loop
semantics):

1. **Model-input clamp**: before each DiT forward, conditioned positions in the
   sampler's ``x`` are replaced with ``clean``, so the DiT always sees the
   identity anchor there regardless of what noise the sampler carried in --
   critical for ancestral samplers, which inject fresh noise into ALL tokens
   each step and would otherwise corrupt the DiT's view of positions whose
   per-token timestep claims sigma=0. DFR's rounds run ancestral, so this is
   load-bearing for every tile's carried anchors.
2. **x0-space trajectory blend**: after the forward, the predicted x0 at
   conditioned positions is re-forced to ``clean`` (operating on the sampler's
   ORIGINAL ``x``, not the clamped model input), and the returned velocity is
   recomputed to point from that x toward the forced x0.

Mask polarity: ``ctx.prepared.mask`` is STRENGTH -- 1 = fully clean/pinned,
0 = free generation target -- and the per-token timestep is ``sigma * (1 -
mask)``. Only single-step samplers with no cross-step velocity HISTORY are
supported (``euler``, ``euler_ancestral``, ``euler_ancestral_cfg_pp``,
``euler_cfg_pp``): both mechanisms are re-derived fresh each step, so a
multistep integrator that mixes PAST (pre-clamp, pre-blend) velocities into its
update (``dpmpp_2m`` and friends) is unvalidated here and deliberately not
offered.

``ctx`` is duck-typed, not a fixed class: it needs ``prepared``, ``fps``,
``t_lat``/``h_lat``/``w_lat``, ``audio_tokens``, ``device`` and ``dtype``.
``generator/video_ltx`` passes its ``_VideoLtxCtx``; the DFR pipe passes a
per-tile view of the same shape.
"""

from __future__ import annotations

from typing import Any

import torch

Tensor = torch.Tensor


class ConditionedAVForward:
    """See the module docstring -- the packed-state ``model_forward`` itself."""

    def __init__(self, dit_module, ctx: Any):
        p = ctx.prepared
        self.dit = dit_module
        self.fps = ctx.fps
        self.t_lat, self.h_lat, self.w_lat = ctx.t_lat, ctx.h_lat, ctx.w_lat
        self.s_base = p.base_tokens
        self.n_extra = p.n_extra
        self.s_video = p.base_tokens + p.n_extra
        self.t_audio = ctx.audio_tokens
        self.mask = p.mask.to(device=ctx.device, dtype=ctx.dtype)              # [1, S_video]
        self.clean = p.clean.to(device=ctx.device, dtype=ctx.dtype)            # [1, S_video, C]
        self.extra_coords = (
            p.extra_coords.to(device=ctx.device) if p.extra_coords is not None else None
        )
        self.has_conditioning = self.mask.any().item()  # Skip clamp when mask is all-zeros

    def unpack_base(self, x: Tensor) -> Tensor:
        """Base video tokens ``[B, S_base, C]`` -> 5D ``[B, C, F, H, W]``."""
        b = x.shape[0]
        return (
            x[:, : self.s_base]
            .view(b, self.t_lat, self.h_lat, self.w_lat, -1)
            .permute(0, 4, 1, 2, 3)
            .contiguous()
        )

    def _repack(self, v5d: Tensor) -> Tensor:
        b, c, f, h, w = v5d.shape
        return v5d.permute(0, 2, 3, 4, 1).reshape(b, f * h * w, c)

    def __call__(self, x: Tensor, sigma: Tensor, conditioning: dict) -> Tensor:
        b = x.shape[0]

        # Clamp conditioned video tokens to their clean values at the MODEL INPUT
        # (diffusers invariant: the DiT sees `clean` at conditioned positions).
        # Ancestral samplers inject fresh noise into ALL tokens each step; without
        # this clamp the DiT would see `clean + accumulated ancestral noise` at
        # positions whose per-token timestep claims sigma=0, corrupting i2v identity.
        xv_sampler = x[:, : self.s_video]  # Sampler's state (for the trajectory blend below)
        if self.has_conditioning:
            m = self.mask.expand(b, -1).unsqueeze(-1)
            xv_model = xv_sampler * (1.0 - m) + self.clean.expand(b, -1, -1) * m
        else:
            xv_model = xv_sampler  # Pure t2v: no conditioning, skip the blend

        xv5d = self.unpack_base(xv_model)
        x_extra = xv_model[:, self.s_base: self.s_video] if self.n_extra else None
        xa = (
            x[:, self.s_video:].view(b, self.t_audio, 8, 16).permute(0, 2, 1, 3).contiguous()
            if self.t_audio
            else None
        )

        # Per-token video timestep: t * (1 - mask); schedule sigma passed
        # explicitly (token 0 may be fully conditioned = 0).
        sigma_b = sigma.reshape(b)
        v_ts = sigma_b.unsqueeze(-1) * (1.0 - self.mask.expand(b, -1))
        a_ts = sigma_b

        # NAG (mirrors txt2vid_ltx's model_forward / Wan's _ExpertRouter): the
        # reserved "nag_context"/"nag" keys ride the cond dict, injected by
        # _attach_nag in generate_one; absent -> no-op, same call shape as
        # before NAG existed.
        extra = {}
        nag_context = conditioning.get("nag_context")
        if nag_context is not None:
            extra["nag_context"] = nag_context
            extra["nag"] = conditioning.get("nag")
        # FBCache: same reserved-key read as txt2vid_ltx's model_forward /
        # _ExpertRouter (denoise_loop.py's _CachingGuidance always spreads a
        # fresh dict, never mutates the persistent conditioning) -- only
        # forward "step_cache" when present/non-None.
        step_cache = conditioning.get("step_cache")
        if step_cache is not None:
            extra["step_cache"] = step_cache

        # MultiModalGuider hooks: forward STG/modality flags to the DiT.
        stg_skip = conditioning.get("stg_skip_blocks")
        if stg_skip is not None:
            extra["stg_skip_blocks"] = stg_skip
        if conditioning.get("disable_cross_modal"):
            extra["disable_cross_modal"] = True

        model_x = [xv5d] if xa is None else [xv5d, xa]
        out = self.dit(
            model_x,
            (v_ts, a_ts),
            conditioning["context"],
            attention_mask=None,
            frame_rate=self.fps,
            sigma=sigma_b,
            audio_sigma=sigma_b,
            extra_video_tokens=x_extra,
            extra_video_pixel_coords=self.extra_coords if self.n_extra else None,
            **extra,
        )

        if self.n_extra:
            v5d_out, a_out, extra_v = out
        elif isinstance(out, list):
            v5d_out, a_out, extra_v = out[0], out[1], None
        else:
            v5d_out, a_out, extra_v = out, None, None

        v_tokens = self._repack(v5d_out)
        if extra_v is not None:
            v_tokens = torch.cat([v_tokens, extra_v], dim=1)

        # x0-space conditioning blend over ALL video tokens (base + extras):
        # x0 = x - sigma*v ; x0 <- x0*(1-m) + clean*m ; v <- (x - x0)/sigma.
        # CRITICAL: use the sampler's ORIGINAL x (xv_sampler), NOT the clamped
        # xv_model we fed to the DiT — this blend defines the trajectory update.
        sigma_e = sigma_b.view(b, 1, 1)
        m = self.mask.expand(b, -1).unsqueeze(-1)
        x0 = xv_sampler - sigma_e * v_tokens
        x0 = x0 * (1.0 - m) + self.clean.expand(b, -1, -1) * m
        v_tokens = (xv_sampler - x0) / sigma_e

        if a_out is not None:
            a_tokens = a_out.permute(0, 2, 1, 3).reshape(b, self.t_audio, 128)
            return torch.cat([v_tokens, a_tokens], dim=1)
        return v_tokens
