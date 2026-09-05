# Derived from: comfy/ldm/qwen_image/model.py (ComfyUI, GPL-3.0). The
# block-level building blocks (timestep embedding, GELU-tanh feed-forward,
# dual-stream joint attention, transformer block, final layer) moved to
# vendor/gpl/comfyui/qwen_image/layers.py — they carry no FBCache/
# ref_latents/control kwarg of their own, only the optional
# ``timestep_zero_index`` int needed to split modulation across the
# [real tokens | ref tokens] span. This class stays in src because it extends
# NativeArchModule (PotionUI's own loader contract) and orchestrates FBCache
# step-skipping, the ref_latents edit-mode concat-and-slice (incl. deciding
# WHERE that span boundary falls), and the ControlNet residual seam.

"""Qwen-Image MMDiT — ``QwenImageDiT`` (``NativeArchModule``).

Vendored from ComfyUI ``comfy/ldm/qwen_image/model.py``, adapted to the native
``operations`` seam. Dual-stream (joint-attention) MMDiT: image and text tokens
each have their own modulation + MLP, and every block runs one joint attention
over the concatenated ``[text ; image]`` sequence. Reuses the already-vendored
flux RoPE (``EmbedND`` + ``apply_rope1``) and the shared attention dispatcher.
The block-level building blocks live in ``vendor/gpl/comfyui/qwen_image/layers.py``;
this module keeps the top-level ``QwenImageDiT`` class.

Stripped from the ComfyUI original: ``patcher_extension`` wrapper, ``patches`` /
``patches_replace`` block hooks. Kept: the ``ref_latents`` / ``index_timestep_zero``
edit path (needed by the 2511 checkpoint) and an optional ``control`` seam — both
dormant on the plain t2i path. ``index_timestep_zero``: reference tokens
are clean VAE-encoded latents, not noised samples, so they get timestep 0 while
the generated tokens keep the real step timestep — done by doubling the batch
dim of ``timestep`` (real rows + zeroed rows) before the timestep embedding,
then each block splits the resulting doubled ``temb`` back across the
[real tokens | ref tokens] span of the image sequence (see ``_Block._modulate``/
``_apply_gate`` in ``layers.py``). The plain "index" method (2512 t2i, and any
non-``index_timestep_zero`` ref usage) shares one timestep for every token, as
before.

Forward-call contract (for the generator / sampling agents)
-----------------------------------------------------------
``forward(x, timestep, context, attention_mask=None, ref_latents=None, **kwargs)``

  * ``x``        — UNPACKED latent ``(B, C, T, H, W)`` (5D; Qwen-Image is 3D-VAE,
                   ``T == 1`` for images), ``C == out_channels`` (16). Packed 2x2
                   internally to ``(B, T*H/2*W/2, in_channels=64)`` — the generator
                   passes plain latents.
  * ``timestep`` — ``(B,)`` flow-matching t in ``[0, 1]`` (scaled x1000 inside).
  * ``context``  — text embeddings ``(B, L_txt, joint_attention_dim=3584)`` from
                   Qwen2.5-VL (last hidden state).
  * ``attention_mask`` — ``(B, L_txt)`` text padding mask (1 = keep).
  * kwargs: ``ref_latents`` + ``ref_latents_method`` (kontext/edit),
            ``additional_t_cond`` (2-way t embedding, unused by 2512/2511),
            ``control`` (ControlNet residuals). All optional.

Returns UNPACKED velocity ``(B, out_channels=16, T, H, W)``.

Only ``x`` and ``timestep`` move between the steps of a run; the text stream,
the reference latents and the sequence geometry belong to the guidance branch.
Everything derived from the latter alone is hoisted into ``_PreparedFixed`` and
computed once per branch when the engine has attached a ``run_cache``. Without
one, every forward prepares its own — byte-identical either way.

Guidance is **true CFG** (the sampler runs cond/uncond) — the DiT has no embedded
guidance input. ``pack_latents`` / ``unpack_latents`` are exposed so the
generator does not re-derive the 2x2 packing + centred 3-axis positional ids.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import repeat
from torch import Tensor

from vendor.gpl.comfyui.flux.layers import EmbedND
from vendor.gpl.comfyui.qwen_image.layers import (
    QwenTimestepProjEmbeddings,
    _Block,
    _LastLayer,
    set_attention_backend,
)

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule
from ...cache_identity import identity_usable, tensor_identity
from .config import QwenImageConfig

# vendor/gpl/comfyui/qwen_image/layers.py must not import src (layering
# guard) — this is the one module that constructs QwenImageDiT (and therefore
# the _Block/_JointAttention instances that call the injected attention
# backend), so wiring it here guarantees it's set before any forward() runs.
set_attention_backend(_dispatch_attention)


def _pad_to_patch_size(x: Tensor, patch: int) -> Tensor:
    """Circular-pad the H/W of a 5D ``(B,C,T,H,W)`` latent up to a patch multiple."""
    h, w = x.shape[-2], x.shape[-1]
    pad = (0, (patch - w % patch) % patch, 0, (patch - h % patch) % patch)
    if pad[1] == 0 and pad[3] == 0:
        return x
    return F.pad(x, pad, mode="circular")


class _PreparedFixed(NamedTuple):
    """The part of a forward that every step of a run recomputes identically.

    The text stream, the reference latents and the sequence geometry are all
    properties of the guidance branch; only the noisy target tokens and the
    timestep move between steps. So the padding-mask analysis (three host syncs
    — ``any``/``max().item()``/``all`` — that stall the launch queue), the packed
    reference tokens, the RoPE table and the ``txt_norm``/``txt_in`` projection
    are computed once per branch and reused.

    ``context``, ``attention_mask`` and ``refs`` are the tensors the key names.
    Holding them closes the hole ``tensor_identity`` cannot see: a freed tensor's
    address can be re-let to a new allocation whose version counter starts over,
    and while an entry lives its sources cannot be freed.
    """

    mask: Optional[Tensor]
    encoder_hidden_states: Tensor
    ref_tokens: Optional[Tensor]
    rope: Tensor
    zero_timestep: bool
    context: Tensor
    attention_mask: Optional[Tensor]
    refs: tuple


class QwenImageDiT(NativeArchModule):
    """Qwen-Image dual-stream joint-attention MMDiT (2512 t2i + 2511 edit)."""

    def __init__(self, config: QwenImageConfig, operations, dtype=None, device=None):
        super().__init__()
        self.config = config
        d = config.inner_dim
        self.patch_size = config.patch_size
        self.pe_embedder = EmbedND(dim=config.attention_head_dim, theta=config.theta, axes_dim=list(config.axes_dims_rope))
        self.time_text_embed = QwenTimestepProjEmbeddings(d, config.use_additional_t_cond, operations, dtype=dtype, device=device)
        self.txt_norm = operations.RMSNorm(config.joint_attention_dim, eps=1e-6, dtype=dtype, device=device)
        self.img_in = operations.Linear(config.in_channels, d, bias=True, dtype=dtype, device=device)
        self.txt_in = operations.Linear(config.joint_attention_dim, d, bias=True, dtype=dtype, device=device)
        self.transformer_blocks = nn.ModuleList([
            _Block(d, config.num_attention_heads, config.attention_head_dim, operations, dtype=dtype, device=device)
            for _ in range(config.num_layers)
        ])
        if config.default_ref_method == "index_timestep_zero":
            # Persistent marker buffer present in the 2511/edit checkpoint.
            self.register_buffer("__index_timestep_zero__", torch.tensor([]))
        self.norm_out = _LastLayer(d, operations, dtype=dtype, device=device)
        self.proj_out = operations.Linear(d, config.patch_size * config.patch_size * config.out_channels, bias=True, dtype=dtype, device=device)

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "QwenImageDiT":
        """Build empty-weight from a detected config dict (wrap in
        ``with torch.device("meta")`` to avoid allocating the ~20B weights)."""
        return cls(QwenImageConfig.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        """No-op: Qwen-Image computes RoPE (EmbedND) and the sinusoidal timestep
        embedding per forward, so it has no derived buffers to recompute. The
        only registered buffer (``__index_timestep_zero__``, edit variant) is an
        empty persistent marker loaded from the checkpoint, not computed."""
        return None

    # -- latent packing helpers ---------------------------------------------

    def pack_latents(self, x: Tensor, index: int = 0, h_offset: int = 0, w_offset: int = 0):
        """Patchify ``(B, C, T, H, W)`` -> token seq ``(B, T*h*w, C*4)`` + centred
        3-axis ids ``(B, T*h*w, 3)`` + the padded ``orig_shape``."""
        hs, os = self._pack_tokens(x)
        return hs, self._pack_ids(os, x.device, index, h_offset, w_offset), os

    def _pack_tokens(self, x: Tensor) -> tuple[Tensor, torch.Size]:
        """The value half of :meth:`pack_latents` — tokens + the padded shape.

        Split out because the noisy target is repacked every step while its ids
        depend on the shape alone and are folded into the run-cached RoPE table.
        """
        x = _pad_to_patch_size(x, self.patch_size)
        os = x.shape
        bs, c, t = os[0], os[1], os[-3]
        h2, w2 = os[-2] // 2, os[-1] // 2
        return x.view(bs, c, t, h2, 2, w2, 2).permute(0, 2, 3, 5, 1, 4, 6).reshape(bs, t * h2 * w2, c * 4), os

    def _padded_shape(self, x: Tensor) -> torch.Size:
        """What :func:`_pad_to_patch_size` would make ``x.shape``, without padding it."""
        p = self.patch_size
        h, w = x.shape[-2], x.shape[-1]
        return torch.Size((*x.shape[:-2], h + (-h) % p, w + (-w) % p))

    def _pack_ids(self, os, device, index: int = 0, h_offset: int = 0, w_offset: int = 0) -> Tensor:
        """The position half of :meth:`pack_latents`, derived from the padded shape."""
        bs, t = os[0], os[-3]
        p = self.patch_size
        h_len = (os[-2] + (p // 2)) // p
        w_len = (os[-1] + (p // 2)) // p
        h_off = (h_offset + (p // 2)) // p
        w_off = (w_offset + (p // 2)) // p
        ids = torch.zeros((t, h_len, w_len, 3), device=device, dtype=torch.float32)
        if t > 1:
            ids[..., 0] = torch.linspace(0, t - 1, steps=t, device=device, dtype=torch.float32).view(t, 1, 1)
        else:
            ids[..., 0] = index
        ids[..., 1] = torch.linspace(h_off, h_len - 1 + h_off, steps=h_len, device=device, dtype=torch.float32).view(1, h_len, 1) - (h_len // 2)
        ids[..., 2] = torch.linspace(w_off, w_len - 1 + w_off, steps=w_len, device=device, dtype=torch.float32).view(1, 1, w_len) - (w_len // 2)
        return repeat(ids, "t h w c -> b (t h w) c", b=bs)

    def unpack_latents(self, hs: Tensor, orig_shape, out_h: int, out_w: int) -> Tensor:
        os = orig_shape
        hs = hs.view(os[0], os[-3], os[-2] // 2, os[-1] // 2, os[1], 2, 2)
        hs = hs.permute(0, 4, 1, 2, 5, 3, 6)
        return hs.reshape(os)[:, :, :, :out_h, :out_w]

    # -- step-invariant preparation -----------------------------------------

    def _fixed_inputs(self, x: Tensor, context: Tensor, attention_mask: Tensor | None,
                      ref_latents, ref_method: str) -> _PreparedFixed:
        """:meth:`_prepare_fixed`, once per guidance branch when a run cache is up.

        When the engine has attached a ``run_cache`` (``NativeGenerator.sample``)
        the prepared bundle is reused for the rest of the run; without one every
        forward prepares its own, exactly as before.

        ``cache.revision`` is read here, per lookup, and never hoisted across
        steps: a step-windowed LoRA applies and restores at step boundaries
        WITHIN a run, so a projection computed at step 1 must not answer a lookup
        at step 3 under different weights.
        """
        cache = getattr(self, "run_cache", None)
        key = None
        if cache is not None:
            refs = tuple(ref_latents) if ref_latents is not None else ()
            ids = (tensor_identity(context), tensor_identity(attention_mask),
                   *(tensor_identity(ref) for ref in refs))
            if identity_usable(*ids):
                key = ("qwen_image.fixed", cache.revision, tuple(x.shape), x.dtype, x.device,
                       ref_method if refs else None, *ids)
                hit = cache.get(key)
                if hit is not None:
                    return hit

        prepared = self._prepare_fixed(x, context, attention_mask, ref_latents, ref_method)
        if key is not None:
            cache.put(key, prepared)
        return prepared

    def _prepare_fixed(self, x: Tensor, context: Tensor, attention_mask: Tensor | None,
                       ref_latents, ref_method: str) -> _PreparedFixed:
        """Padding-mask analysis, reference packing, RoPE and the text projection."""
        mask = attention_mask
        # Boolean/int key-padding mask: trim the text stream to the longest real
        # (non-padded) prompt in the batch instead of masking the padding away.
        # A dense attention mask forces every block's attention onto the sdpa
        # path (sage/flash take no mask) — same class of fix as Krea-2's
        # build_stream_inputs, but here we shrink the sequence rather than
        # zero the mask, so it also saves compute on the padded tail. Text and
        # image tokens are joined into one sequence downstream (txt_ids/rope
        # are derived from context.shape[1] below), so trimming ``context``
        # here is sufficient — everything else follows the shorter length.
        #
        # Trim to the *exact* real length (no multiple-of-N rounding): unlike
        # the image stream, text tokens aren't patchified and RoPE (EmbedND)
        # takes arbitrary per-token ids, so there's no architectural alignment
        # requirement on the text length. Rounding up would leave residual
        # padding rows inside the trimmed window and keep the dense mask alive
        # for the common batch=1 case — exactly the sdpa fallback this is
        # meant to avoid.
        if mask is not None and not torch.is_floating_point(mask):
            # Normalise once to a boolean keep-mask: an integer mask arrives as
            # 0/1, but arithmetic on a genuinely boolean mask (`mask - 1`)
            # raises, so every subsequent step works off `bool_mask` alone and
            # the additive mask is built via masked_fill, never subtraction.
            bool_mask = mask.bool()
            # Trimming to `[:txt_len]` from the sequence START only preserves every
            # real token when padding trails (right-padded: real tokens form a
            # prefix). Detect the opposite — a real token immediately following a
            # padding token anywhere in any row (left-padded, or padding stuck mid-
            # sequence) — and skip the trim entirely in that case: real tokens must
            # never be silently dropped just to win back sage/flash eligibility.
            row_has_real_after_pad = (
                bool((~bool_mask[:, :-1] & bool_mask[:, 1:]).any()) if bool_mask.shape[1] > 1 else False
            )
            if not row_has_real_after_pad:
                real_len = max(int(bool_mask.sum(dim=1).max().item()), 1)
                txt_len = min(real_len, context.shape[1])
                context = context[:, :txt_len]
                bool_mask = bool_mask[:, :txt_len]
                if bool(bool_mask.all()):
                    mask = None  # no padding left in the trimmed window: sage/flash eligible
                else:
                    mask = torch.zeros(bool_mask.shape, dtype=x.dtype, device=x.device).masked_fill(
                        ~bool_mask, torch.finfo(x.dtype).min
                    )
            else:
                mask = torch.zeros(bool_mask.shape, dtype=x.dtype, device=x.device).masked_fill(
                    ~bool_mask, torch.finfo(x.dtype).min
                )

        img_ids = self._pack_ids(self._padded_shape(x), x.device)

        # Reference latents are clean VAE encodings, fixed for the whole run, so
        # both their tokens and their ids are packed once and joined in a single
        # concatenation rather than one per reference per step.
        ref_tokens = None
        zero_timestep = False
        refs = tuple(ref_latents) if ref_latents is not None else ()
        if refs:
            packed, ref_ids = [], []
            for index, ref in enumerate(refs, start=1):
                kontext, ref_shape = self._pack_tokens(ref)
                packed.append(kontext)
                ref_ids.append(self._pack_ids(ref_shape, ref.device, index=index))
            ref_tokens = torch.cat(packed, dim=1)
            img_ids = torch.cat([img_ids, *ref_ids], dim=1)
            zero_timestep = ref_method == "index_timestep_zero"

        # text ids sit on the shared diagonal offset (ComfyUI txt_start).
        txt_start = round(max(((x.shape[-1] + (self.patch_size // 2)) // self.patch_size) // 2,
                              ((x.shape[-2] + (self.patch_size // 2)) // self.patch_size) // 2))
        txt_ids = torch.arange(txt_start, txt_start + context.shape[1], device=x.device).reshape(1, -1, 1).repeat(x.shape[0], 1, 3)
        ids = torch.cat((txt_ids, img_ids), dim=1)
        rope = self.pe_embedder(ids).to(x.dtype)

        return _PreparedFixed(
            mask=mask,
            encoder_hidden_states=self.txt_in(self.txt_norm(context)),
            ref_tokens=ref_tokens,
            rope=rope,
            zero_timestep=zero_timestep,
            context=context,
            attention_mask=attention_mask,
            refs=refs,
        )

    # -- forward ------------------------------------------------------------

    def forward(self, x: Tensor, timestep: Tensor, context: Tensor, attention_mask: Tensor | None = None,
                ref_latents=None, additional_t_cond=None, **kwargs) -> Tensor:
        ref_method = kwargs.get("ref_latents_method", self.config.default_ref_method)
        prepared = self._fixed_inputs(x, context, attention_mask, ref_latents, ref_method)
        mask, rope = prepared.mask, prepared.rope
        control = kwargs.get("control")

        hidden_states, orig_shape = self._pack_tokens(x)
        num_embeds = hidden_states.shape[1]
        if prepared.ref_tokens is not None:
            hidden_states = torch.cat([hidden_states, prepared.ref_tokens], dim=1)

        timestep_zero_index = None
        if prepared.zero_timestep:
            # The 2511 edit checkpoint's method: reference tokens are clean
            # (not noised), so they get timestep 0 while the generated
            # tokens keep the real step timestep. Doubling the batch dim of
            # `timestep` produces two temb rows per real batch item — the
            # real-timestep row and the zero-timestep row — which each
            # block splits back out across the [real tokens | ref tokens]
            # span (see _Block._modulate/_apply_gate in layers.py).
            timestep = torch.cat([timestep, timestep * 0], dim=0)
            timestep_zero_index = num_embeds

        hidden_states = self.img_in(hidden_states)
        encoder_hidden_states = prepared.encoder_hidden_states
        temb = self.time_text_embed(timestep, hidden_states, additional_t_cond)

        # FBCache: block-0's image-stream output is the change proxy; a skip reuses
        # the last computed output and bypasses blocks 1..N + proj_out. None-safe
        # (byte-identical default). See src/platform/runtime/native/sampling/step_cache.py.
        # Bypassed entirely when a ControlNet payload is present: the probe can't
        # see residuals applied at blocks 1..N (control["input"][1:]), so a
        # changed residual between calls with a stable block-0 would go
        # undetected and reuse a stale cached output — see the matching guard
        # in arch/flux/model.py.
        step_cache = kwargs.get("step_cache")
        if control is not None:
            step_cache = None
        probe = None
        for i, block in enumerate(self.transformer_blocks):
            encoder_hidden_states, hidden_states = block(
                hidden_states, encoder_hidden_states, mask, temb, rope,
                timestep_zero_index=timestep_zero_index,
            )
            if control is not None:
                ci = control.get("input")
                if ci is not None and i < len(ci) and ci[i] is not None:
                    hidden_states[:, : ci[i].shape[1]] += ci[i]
            if i == 0 and step_cache is not None:
                probe = hidden_states
                if step_cache.should_skip(probe):
                    return step_cache.record_skip()

        if timestep_zero_index is not None:
            # Final norm modulates every token (real + ref) uniformly; the ref
            # tokens' output is discarded by the :num_embeds slice below
            # regardless, so it always uses the real-timestep half.
            temb = temb.chunk(2, dim=0)[0]
        hidden_states = self.proj_out(self.norm_out(hidden_states, temb))
        hidden_states = hidden_states[:, :num_embeds]
        out = self.unpack_latents(hidden_states, orig_shape, x.shape[-2], x.shape[-1])
        if step_cache is not None and probe is not None:
            step_cache.record_compute(probe, out)
        return out
