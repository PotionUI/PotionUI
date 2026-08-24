# Forked from diffusers 0.39.0's transformer_krea2.py
# (https://github.com/huggingface/diffusers/blob/v0.39.0/src/diffusers/models/transformers/transformer_krea2.py),
# Copyright 2026 Krea AI and The HuggingFace Team. Licensed Apache-2.0
# (http://www.apache.org/licenses/LICENSE-2.0).
#
# Local modifications (this file replaces a Wan2GP-derived port, which
# is deleted, not kept alongside):
#   * ``Krea2Transformer2DModel`` -> ``Krea2``. Every submodule renamed for
#     native-checkpoint parity: ``img_in`` -> ``first``, ``time_embed``'s
#     sinusoidal-embed step -> the free function ``temb()``, its MLP ->
#     ``tmlp`` (an ``nn.Sequential`` mirroring
#     ``linear_2(gelu(linear_1(emb)))`` exactly), ``time_mod_proj`` (preceded
#     by its own gelu) -> ``tproj``, ``text_fusion`` -> ``txtfusion``,
#     ``txt_in`` (``Krea2TextProjection``: norm + linear_1 + gelu + linear_2)
#     -> ``txtmlp`` (an ``nn.Sequential`` with the identical 4-step
#     structure), ``rotary_emb`` -> ``posemb``, ``transformer_blocks`` ->
#     ``blocks``, ``final_layer`` -> ``last``. See ``layers.py``'s header for
#     the per-class renames inside those submodules.
#   * diffusers' one flat ``forward(hidden_states, encoder_hidden_states,
#     timestep, position_ids, encoder_attention_mask)`` is split into this
#     module's existing helper-method contract
#     (``build_stream_inputs``/``prepare_timestep``/``prepare_context``/
#     ``run_blocks``) that ``NativeGenerator.sample`` already drives — the
#     INTERNAL ORDER OF OPERATIONS is unchanged (time embed + time-mod
#     projection; text fusion then text projection; image projection then
#     concat; rotary embed; block loop; slice off the text prefix; final
#     layer) and ``forward`` still composes them into the same flat call the
#     sampler expects, so this is a packaging difference, not a behavioral
#     one. The text+image key-padding mask diffusers builds inline
#     (``cat([encoder_attention_mask, image_mask])``) is built once in
#     ``build_stream_inputs`` instead, for the same reason.
#   * every ``nn.Linear`` is the injected ``operations.Linear`` seam.
#   * FBCache's block-0 probe/skip (``run_blocks``, ``step_cache``) and the
#     ref_latents in-context edit hook (``build_stream_inputs``/
#     ``run_blocks``/``forward``) are additive — diffusers' upstream has
#     neither. See ``layers.py``'s header for why the F32-modulation-cast fix
#     is re-applied and why rotary embeddings keep the vendored Flux/ComfyUI
#     convention (``vendor/gpl/comfyui/flux/math_ops.py``, GPL-3.0, imported
#     by reference) rather than diffusers' own — no Wan2GP-derived expression
#     remains in either file.
#
# The DiT needs an external **Qwen3-VL-4B** text encoder (12-layer hidden
# states, dim 2560) and the **Qwen-Image VAE** (``AutoencoderKLQwenImage``,
# 16ch) — neither is this module's responsibility.

"""Krea-2 SingleStream MMDiT — ``Krea2`` (``NativeArchModule``).

Forward-call contract (for the generator / sampling agents)
-----------------------------------------------------------
Unlike Flux, Krea-2's DiT does the text fusion and expects pre-patchified image
tokens, so the generator drives it through helper methods:

  1. ``img_tokens, pos, mask = model.build_stream_inputs(latent, txt_len, txt_mask)``
     patchifies ``latent`` ``(B, 16, H, W)`` -> ``(B, H/2*W/2, 64)`` (channels*patch^2)
     and builds the 3-axis positional ids (text tokens at pos 0, image tokens at
     ``(0, row, col)``) plus the combined key-padding mask.
  2. ``t_emb, tvec = model.prepare_timestep(timestep, dtype)`` — ``timestep`` is
     ``(B,)`` flow-matching t in ``[0, 1]``; ``dtype`` is the compute dtype
     (the latent's).
  3. ``context = model.prepare_context(te_hidden, mask)`` — ``te_hidden`` is the
     text encoder's **per-layer** hidden states ``(B, L_txt, txtlayers=12, 2560)``
     (layers axis at position 2); ``txtfusion`` attends across the 12 layers,
     collapses them, then projects to model width.
  4. ``out = model(img_tokens, context, t_emb, tvec, pos, mask)`` -> ``(B, L_img, 64)``
  5. ``latent = model.unpatchify(out, H//2, W//2)`` -> ``(B, 16, H, W)`` velocity.

CFG (raw model) is the sampler's job — this module runs a single stream.
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
from einops import rearrange, repeat
from torch import Tensor

from ...base import NativeArchModule
from .config import Krea2Config
from .layers import (
    Attention,
    DoubleSharedModulation,
    LastLayer,
    PositionalEncoding,
    RMSNorm,
    SimpleModulation,
    SingleStreamBlock,
    SwiGLU,
    TextFusionTransformer,
    _nag_active,
    ref_attn_bias,
    temb,
)


class Krea2(NativeArchModule):
    """Krea-2 single-stream MMDiT (turbo + raw share this structure)."""

    def __init__(self, config: Krea2Config, operations, dtype=None, device=None):
        super().__init__()
        self.config = config
        # NativeGenerator._image_seq_len reads `patch_size` off every DiT (Flux and
        # QwenImage expose it); Krea2's config calls the same thing `patch`.
        self.patch_size = config.patch
        f = config.features
        self.posemb = PositionalEncoding(config.rope_axes, theta=config.theta, ntk=1.0)
        self.first = operations.Linear(config.channels * config.patch**2, f, bias=True, dtype=dtype, device=device)
        self.blocks = nn.ModuleList([
            SingleStreamBlock(f, config.heads, config.multiplier, config.bias, config.kvheads,
                              operations=operations, dtype=dtype, device=device)
            for _ in range(config.layers)
        ])
        self.tmlp = nn.Sequential(
            operations.Linear(config.tdim, f, dtype=dtype, device=device),
            nn.GELU(approximate="tanh"),
            operations.Linear(f, f, dtype=dtype, device=device),
        )
        self.txtfusion = TextFusionTransformer(
            config.txtlayers, config.txtdim, config.txtheads, config.multiplier, config.bias,
            config.txtkvheads, operations=operations, dtype=dtype, device=device,
        )
        self.txtmlp = nn.Sequential(
            RMSNorm(config.txtdim),
            operations.Linear(config.txtdim, f, dtype=dtype, device=device),
            nn.GELU(approximate="tanh"),
            operations.Linear(f, f, dtype=dtype, device=device),
        )
        self.last = LastLayer(f, config.patch, config.channels, operations=operations, dtype=dtype, device=device)
        self.tproj = nn.Sequential(
            nn.GELU(approximate="tanh"),
            operations.Linear(f, f * 6, dtype=dtype, device=device),
        )

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "Krea2":
        """Build empty-weight from a detected Krea-2 config dict.

        Wrap in ``with torch.device("meta"):`` to construct without allocating
        the ~9GB of weights.
        """
        return cls(Krea2Config.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        """Krea-2 has no computed buffers (RoPE frequencies and the sinusoidal
        timestep embedding are recomputed per forward from ids/t), but naive
        all-fp8 checkpoints store even the RAW parameters — norm scales and
        modulation tables, declared f32 by design — in fp8, which supports no
        arithmetic (`scale + 1.0`, `vec + lin` crash). Only ops-managed Linear
        weights may stay in a quantized storage dtype (their cast-on-forward
        handles it); raw params are upcast back to their declared f32 here.
        Plain cast is the correct dequant: these files carry no per-tensor
        scales for them."""
        fp8 = (torch.float8_e4m3fn, torch.float8_e5m2)
        for mod in self.modules():
            if isinstance(mod, (RMSNorm, SimpleModulation, DoubleSharedModulation)):
                for p in mod.parameters(recurse=False):
                    if p.dtype in fp8:
                        p.data = p.data.to(torch.float32)

    # -- generator-facing helpers -------------------------------------------

    def build_stream_inputs(
        self, latent: Tensor, txt_len: int, txt_mask: Tensor | None = None,
        ref_latents: Tensor | list[Tensor] | None = None,
    ) -> tuple[Tensor, Tensor, Tensor | None]:
        """Patchify ``latent`` (B, C, H, W) and build (img_tokens, pos, mask).

        Image tokens get 3-axis ids ``(0, row, col)``; the ``txt_len`` text
        tokens (prepended in ``forward``) get all-zero ids. ``pos`` covers the
        full ``[text; image]`` sequence — matches diffusers' inline
        ``cat([encoder_attention_mask, image_mask])`` mask construction and
        its ``position_ids`` contract (text rows all-zero, image rows the
        latent-grid coordinates), just built once here instead of inline in
        ``forward``.

        The returned key-padding ``mask`` also spans ``[text; image]`` (image
        tokens are always valid; only text tokens can be padded). It is **None**
        when every text token is real — the common single-prompt case. An
        all-True mask is a no-op numerically, but a non-None ``attn_mask`` forces
        torch SDPA off its fast fused (flash/cuDNN) kernel onto the mem-efficient/
        math path and disables the accelerated sage/flash backends entirely, so
        short-circuiting to None keeps the fast attention path (mirrors Flux's
        ``_expand_attention_mask``). A genuinely padded batch still gets the joint
        bool mask.

        ``ref_latents`` (Krea-2 instruction edit — not in diffusers'
        upstream): one clean SOURCE latent, or a list of several, prepended
        as extra tokens ahead of the target image tokens at RoPE frame
        ``i + 1`` (0-indexed source ``i``) — the only signal that marks a
        token "clean reference" rather than "noisy target" (Krea-2 has no
        separate per-token timestep; the whole sequence shares one). A ref
        latent at exactly THIS ``(h, w)`` grid (the default, the "crop"
        fit) gets the same (row, col) ids as the target; a SMALLER ref grid
        (the blur-proof "fit", a source resampled to the target density
        at a smaller grid) is placed at a centered integer offset inside the
        target grid (``_imgids_offset``). A ref grid LARGER than the target
        raises — fitting an oversized source is the caller's job (the plugin's
        edit pipe), not this arch module's. ``None`` (the default) is
        BYTE-IDENTICAL to the code path below. Derived from:
        github.com/lbouaraba/comfyui-krea2edit (Apache-2.0).
        """
        b, _, h, w = latent.shape
        p = self.config.patch
        h_, w_ = h // p, w // p
        img = rearrange(latent, "b c (h ph) (w pw) -> b (h w) (c ph pw)", ph=p, pw=p)

        imgids = torch.zeros((h_, w_, 3), device=latent.device, dtype=torch.float32)
        imgids[..., 1] = torch.arange(h_, device=latent.device, dtype=torch.float32)[:, None]
        imgids[..., 2] = torch.arange(w_, device=latent.device, dtype=torch.float32)[None, :]
        imgpos = repeat(imgids, "h w three -> b (h w) three", b=b)
        txtpos = torch.zeros(b, txt_len, 3, device=latent.device, dtype=torch.float32)

        if ref_latents is None:
            pos = torch.cat((txtpos, imgpos), dim=1)
            if txt_mask is None:
                return img, pos, None
            # coerce to bool so the joint key-padding mask stays a valid sdpa dtype
            # (the tokenizer emits int64; cat with a bool would promote to long).
            txt_mask = txt_mask.to(torch.bool)
            if txt_mask.all():
                return img, pos, None
            imgmask = torch.ones(b, h_ * w_, device=latent.device, dtype=torch.bool)
            mask = torch.cat((txt_mask, imgmask), dim=1)
            return img, pos, mask

        # Derived from: github.com/lbouaraba/comfyui-krea2edit __init__.py
        # krea2_edit_forward (Apache-2.0) — the [text | source(frame=i+1) |
        # target(frame=0)] sequence assembly and per-source RoPE frame index.
        refs = ref_latents if isinstance(ref_latents, (list, tuple)) else [ref_latents]
        ref_tokens: list[Tensor] = []
        ref_pos: list[Tensor] = []
        for i, ref in enumerate(refs):
            _, _, rh, rw = ref.shape
            if rh > h or rw > w:
                raise ValueError(
                    f"krea2 ref_latents[{i}] grid {(rh, rw)} exceeds the target grid "
                    f"{(h, w)} -- fit the source to at most the target resolution before "
                    "calling forward (geometry lives in the edit pipe, not "
                    "this arch module)"
                )
            gh, gw = rh // p, rw // p
            ref_tokens.append(rearrange(ref, "b c (h ph) (w pw) -> b (h w) (c ph pw)", ph=p, pw=p))
            # Centered integer offset (matching comfyui-krea2edit's
            # `_imgids_offset`): a "fit" source is resampled to the target grid
            # DENSITY at a smaller grid, so its stride-1 position ids sit at a
            # centered offset inside the target grid. When the ref grid equals
            # the target grid (the default), off_h == off_w == 0 and this
            # is BYTE-IDENTICAL to the plain (row, col) ids.
            off_h, off_w = (h_ - gh) // 2, (w_ - gw) // 2
            refids = torch.zeros((gh, gw, 3), device=latent.device, dtype=torch.float32)
            refids[..., 0] = i + 1
            refids[..., 1] = (torch.arange(gh, device=latent.device, dtype=torch.float32) + off_h)[:, None]
            refids[..., 2] = (torch.arange(gw, device=latent.device, dtype=torch.float32) + off_w)[None, :]
            ref_pos.append(repeat(refids, "h w three -> b (h w) three", b=b))

        img = torch.cat(ref_tokens + [img], dim=1)
        pos = torch.cat([txtpos] + ref_pos + [imgpos], dim=1)

        if txt_mask is None:
            return img, pos, None
        txt_mask = txt_mask.to(torch.bool)
        if txt_mask.all():
            return img, pos, None
        reflen = sum(rt.shape[1] for rt in ref_tokens)
        refmask = torch.ones(b, reflen, device=latent.device, dtype=torch.bool)
        imgmask = torch.ones(b, h_ * w_, device=latent.device, dtype=torch.bool)
        mask = torch.cat((txt_mask, refmask, imgmask), dim=1)
        return img, pos, mask

    def unpatchify(self, out: Tensor, h_: int, w_: int) -> Tensor:
        """Inverse of the patchify in :meth:`build_stream_inputs`."""
        p = self.config.patch
        return rearrange(out, "b (h w) (c ph pw) -> b c (h ph) (w pw)", h=h_, w=w_, ph=p, pw=p)

    def prepare_timestep(self, t: Tensor, dtype: torch.dtype) -> tuple[Tensor, Tensor]:
        """Return ``(t_emb, tvec)`` from a ``(B,)`` timestep.

        Matches diffusers' ``self.time_embed(...)`` (sinusoidal embed -> MLP,
        here ``temb()`` + ``tmlp``) followed by
        ``self.time_mod_proj(F.gelu(temb))`` (here ``tproj``, which already
        includes its own leading GELU) — same two-step order, same math.

        ``dtype`` is the compute dtype (the latent's), never a weight's:
        on quantized checkpoints ``tmlp[0].weight.dtype`` is the fp8/nvfp4
        STORAGE dtype, and building the embedding in it makes the cast-on-
        forward ops keep the whole path fp8 (addmm_cuda has no fp8 kernel).
        """
        t_emb = self.tmlp(temb(t, self.config.tdim, device=t.device, dtype=dtype))
        return t_emb, self.tproj(t_emb)

    def prepare_context(self, te_hidden: Tensor, mask: Tensor | None = None) -> Tensor:
        """Fuse per-layer TE hidden states -> ``(B, L_seq, features)``.

        ``te_hidden`` is ``(B, L_seq, txtlayers, txtdim)`` — the layers axis sits
        at position 2 because ``txtfusion`` first runs attention *across the
        layers* (per sequence position) then its ``projector`` (``Linear(txtlayers,
        1)``) collapses them, before the refiner attends over the sequence.
        Matches diffusers' ``self.text_fusion(...)`` then ``self.txt_in(...)``.
        """
        txt_len = te_hidden.shape[1]
        txtmask = None
        if mask is not None:
            txtmask = mask[:, :txt_len][:, None, None, :]
        fused = self.txtfusion(te_hidden, mask=txtmask)
        return self.txtmlp(fused)

    # -- forward ------------------------------------------------------------

    def run_blocks(self, img: Tensor, context: Tensor, t: Tensor, tvec: Tensor,
                   pos: Tensor, mask: Tensor | None = None, step_cache=None,
                   ref_len: int = 0, attn_bias: Tensor | None = None,
                   nag_fused: Tensor | None = None, nag: dict | None = None,
                   nag_attention_mask: Tensor | None = None) -> Tensor:
        """Core DiT pass: image projection + concat + rotary embed + block
        loop + text-prefix slice + final layer — matches diffusers'
        ``self.img_in(...)``, ``cat([encoder_hidden_states, hidden_states])``,
        ``self.rotary_emb(position_ids)``, the ``transformer_blocks`` loop,
        ``hidden_states[:, text_seq_len:]``, ``self.final_layer(...)`` in the
        same order. See the module docstring for the full call sequence.

        ``img``: patchified image tokens ``(B, L_img, channels*patch^2)``.
        ``context``: fused text ``(B, L_txt, features)`` (from :meth:`prepare_context`).
        ``t``: timestep embedding for the final layer; ``tvec``: block modulation
        vector (both from :meth:`prepare_timestep`). ``pos``/``mask`` span the
        full ``[text; image]`` sequence. Returns image tokens ``(B, L_img, channels*patch^2)``.

        ``step_cache``/``ref_len`` are additive, not in diffusers' upstream:
        FBCache's block-0 probe/skip (``step_cache``, see
        ``tests/.../test_krea2_fbcache.py``) and the edit-mode leading-
        ref-token exclusion. When ``img`` is ``[ref tokens... | target
        tokens]`` (built by :meth:`build_stream_inputs` with ``ref_latents``),
        ``ref_len`` is the number of leading ref tokens to exclude from the
        returned output — the source tokens are only ever an INPUT to the
        joint attention, never denoised themselves. ``ref_len=0`` (the
        default) is BYTE-IDENTICAL to the plain slice.

        ``attn_bias`` (Krea-2 edit ref_boost — not in diffusers'
        upstream): an additive ``(1, 1, L, L)`` target->reference attention
        bias from :func:`layers.ref_attn_bias`, folded into the per-block
        attention mask. ``None`` (the default) is BYTE-IDENTICAL to the plain
        path INCLUDING the fast sage/flash attention dispatch; a non-None bias
        deliberately forces SDPA (a dense mask/bias is unsupported by the
        accelerated backends), so it is only ever built when a boost != 1.0 is
        active. Orthogonal to NAG below — a negative-guidance pass never sees
        ``attn_bias``.

        ``nag_fused``/``nag``/``nag_attention_mask`` (NAG — arXiv:
        2505.21179, not in diffusers' upstream): ``nag_fused`` is the negative
        prompt's fused text representation (already run through
        :meth:`prepare_context`, matching ``context``'s own shape). Its RoPE
        positions (``nag_freqs``) and joint key-padding mask (``nag_mask``)
        are built ONCE here (mirroring how ``freqs``/``attn_mask`` are built
        once for the positive path) and passed to every block unchanged.
        ``None`` (the default) is BYTE-IDENTICAL to the plain path.
        """
        img = self.first(img)
        txtlen, imglen = context.shape[1], img.shape[1]
        combined = torch.cat([context, img], dim=1)
        freqs = self.posemb(pos).to(combined.dtype)
        attn_mask = None if mask is None else mask[:, None, None, :]
        if attn_bias is not None:
            attn_bias = attn_bias.to(combined.dtype)
            if attn_mask is not None:
                keep = torch.zeros((), dtype=combined.dtype, device=combined.device)
                drop = torch.full((), float("-inf"), dtype=combined.dtype, device=combined.device)
                attn_mask = attn_bias + torch.where(attn_mask, keep, drop)
            else:
                attn_mask = attn_bias
        nag_freqs = None
        nag_mask = None
        if nag_fused is not None and _nag_active(nag):
            b = pos.shape[0]
            nag_txtpos = torch.zeros(b, nag_fused.shape[1], 3, device=pos.device, dtype=pos.dtype)
            nag_pos = torch.cat([nag_txtpos, pos[:, txtlen:]], dim=1)
            nag_freqs = self.posemb(nag_pos).to(combined.dtype)
            if nag_attention_mask is not None:
                nag_txt_mask = nag_attention_mask.to(torch.bool)
                if not nag_txt_mask.all():
                    nag_img_mask = torch.ones(b, imglen, device=combined.device, dtype=torch.bool)
                    nag_mask = torch.cat([nag_txt_mask, nag_img_mask], dim=1)[:, None, None, :]
        # FBCache: block-0's joint-sequence output is the change proxy; a skip
        # reuses the last computed output and bypasses blocks 1..N + self.last.
        probe = None
        for i, block in enumerate(self.blocks):
            combined = block(combined, tvec, freqs, attn_mask, nag_fused=nag_fused,
                              nag_freqs=nag_freqs, nag_mask=nag_mask, nag=nag, txt_len=txtlen)
            if i == 0 and step_cache is not None:
                probe = combined
                if step_cache.should_skip(probe):
                    return step_cache.record_skip()
        tgt_len = imglen - ref_len
        img_out = combined[:, txtlen + ref_len : txtlen + ref_len + tgt_len]
        out = self.last(img_out, t)
        if step_cache is not None and probe is not None:
            step_cache.record_compute(probe, out)
        return out

    def _maybe_ref_bias(self, refs: list[Tensor], txt_len: int, tgt_len: int,
                        ref_boost: float, ref_boost_a: float,
                        device: torch.device, dtype: torch.dtype,
                        ref_boost_mask: Tensor | None = None) -> Tensor | None:
        """Build the target->reference attention bias for the edit path, or
        return ``None`` when both dials are neutral (``1.0``).

        Boost alignment (matching comfyui-krea2edit): the LAST ref is
        the subject and takes ``ref_boost``; every earlier ref (the scene, in a
        multi-ref edit) takes ``ref_boost_a``. Returning ``None`` at the
        default keeps the fast sage/flash attention path — the load-bearing
        invariant the edit sequence's performance depends on.

        ``ref_boost_mask``: optional region mask restricting
        ``ref_boost`` (the last ref's dial only, matching upstream) to part of
        that ref; forwarded to :func:`layers.ref_attn_bias` with every ref's
        own token grid so it can area-interpolate the mask correctly. ``None``
        is BYTE-IDENTICAL to the plain scalar path.
        """
        if not refs:
            return None
        p = self.config.patch
        ref_lens = [(r.shape[-2] // p) * (r.shape[-1] // p) for r in refs]
        ref_grids = [(r.shape[-2] // p, r.shape[-1] // p) for r in refs]
        boosts = [ref_boost_a] * (len(refs) - 1) + [ref_boost]
        return ref_attn_bias(boosts, txt_len, ref_lens, tgt_len, device, dtype,
                             ref_grids=ref_grids, boost_mask=ref_boost_mask)

    def forward(self, x: Tensor, timestep: Tensor, context: Tensor, y: Tensor | None = None,
                guidance: Tensor | None = None, attention_mask: Tensor | None = None,
                ref_latents: Tensor | list[Tensor] | None = None,
                ref_boost: float = 1.0, ref_boost_a: float = 1.0,
                ref_boost_mask: Tensor | None = None,
                nag_context: Tensor | None = None, nag: dict | None = None,
                nag_attention_mask: Tensor | None = None, **kwargs) -> Tensor:
        """Flat denoise adapter so ``NativeGenerator.sample`` drives Krea-2 like
        Flux/Qwen (composes the helper contract internally, matching diffusers'
        single flat ``forward`` in effect if not in literal shape).

        ``x``: unpacked latent ``(B, 16, H, W)``. ``timestep``: flow-matching t in
        ``[0,1]``. ``context``: TE per-layer hidden states ``(B, L_txt, 12, txtdim)``.
        ``attention_mask``: ``(B, L_txt)`` text padding mask. ``y``/``guidance`` are
        unused (Krea-2 turbo is NoCFG, no pooled/embedded guidance). Returns the
        unpacked velocity ``(B, 16, H, W)``.

        Krea-2 uses the Qwen-Image causal-3D VAE, so ``NativeGenerator`` hands it a
        5D ``(B, 16, 1, H, W)`` latent; the DiT is 2D-image, so the singleton
        temporal axis is squeezed here and restored on the way out.

        ``ref_latents`` (Krea-2 Identity Edit, not in diffusers'
        upstream): optional clean SOURCE latent(s) fed through
        :meth:`build_stream_inputs`/:meth:`run_blocks`, pre-fit and
        pre-normalized (``process_latent_in``) by the caller — see those
        methods' docstrings. Each may itself be the causal-3D VAE's 5D shape
        and gets squeezed the same way ``x`` does. ``None`` (the default)
        takes the exact plain code path.

        ``ref_boost_mask``: optional region mask, forwarded to
        :meth:`_maybe_ref_bias`, restricting ``ref_boost`` to part of the last
        ref (e.g. a face). ``None`` (the default) is BYTE-IDENTICAL to the
        plain scalar-boost path.

        ``nag_context``/``nag``/``nag_attention_mask`` (NAG — arXiv:
        2505.21179, mirrors the Wan/LTX arch's ``nag_context``/``nag``
        contract, see ``src/platform/runtime/native/arch/wan/model.py``):
        ``nag_context`` is the NEGATIVE prompt's per-layer TE hidden states,
        same shape contract as ``context`` (fused here via
        :meth:`prepare_context`, once, like the positive path's ``fused``).
        ``nag`` is ``{"scale": float, "tau": float, "alpha": float}``;
        ``scale <= 1.0`` or ``nag_context is None`` is a no-op (byte-identical
        to pre-NAG code). ``nag_attention_mask`` is the negative prompt's own
        ``[B, S]`` key-padding mask — never derived from ``attention_mask``,
        since the two prompts generally tokenize to different lengths.
        """
        video_5d = x.ndim == 5
        if video_5d:
            x = x.squeeze(2)  # (B, 16, 1, H, W) -> (B, 16, H, W)
        b, _, h, w = x.shape
        t = timestep.reshape(-1) if timestep.ndim else timestep.reshape(1)
        if t.shape[0] == 1 and b > 1:
            t = t.expand(b)

        ref_len = 0
        refs: list[Tensor] = []
        if ref_latents is not None:
            refs = ref_latents if isinstance(ref_latents, (list, tuple)) else [ref_latents]
            refs = [r.squeeze(2) if r.ndim == 5 else r for r in refs]
            ref_len = sum((r.shape[-2] // self.config.patch) * (r.shape[-1] // self.config.patch) for r in refs)
            ref_latents = refs if isinstance(ref_latents, (list, tuple)) else refs[0]

        img_tokens, pos, mask = self.build_stream_inputs(
            x, txt_len=context.shape[1], txt_mask=attention_mask, ref_latents=ref_latents,
        )
        t_emb, tvec = self.prepare_timestep(t, x.dtype)
        fused = self.prepare_context(context, mask)
        nag_fused = None
        if nag_context is not None and _nag_active(nag):
            # ``prepare_context`` expects an already-bool mask (``build_stream_inputs``
            # coerces ``attention_mask`` before the plain path's own call above);
            # ``nag_attention_mask`` arrives raw from the caller (e.g. an int64
            # tokenizer mask), so coerce it here the same way.
            nag_mask_bool = nag_attention_mask.to(torch.bool) if nag_attention_mask is not None else None
            nag_fused = self.prepare_context(nag_context, nag_mask_bool)
        p = self.config.patch
        attn_bias = self._maybe_ref_bias(
            refs, context.shape[1], (h // p) * (w // p), ref_boost, ref_boost_a, x.device, x.dtype,
            ref_boost_mask=ref_boost_mask,
        )
        out = self.run_blocks(img_tokens, fused, t_emb, tvec, pos, mask,
                               step_cache=kwargs.get("step_cache"), ref_len=ref_len,
                               attn_bias=attn_bias, nag_fused=nag_fused, nag=nag,
                               nag_attention_mask=nag_attention_mask)
        latent = self.unpatchify(out, h // self.config.patch, w // self.config.patch)
        return latent.unsqueeze(2) if video_5d else latent
