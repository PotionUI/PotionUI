# Derived from: comfy/ldm/qwen_image21/model.py (ComfyUI, GPL-3.0); block-causal contract cross-checked against diffusers transformer_qwenimage21.py (Apache-2.0).

"""Qwen-Image-2.1 single-stream DiT — ``QwenImage21DiT`` (``NativeArchModule``).

Vendored from ComfyUI ``comfy/ldm/qwen_image21/model.py``, adapted to the
native ``operations`` seam. Single-stream: text and image tokens share ONE
sequence and ONE set of attention projections per block (unlike Qwen-Image
1.0's dual-stream design) — a single ``modulation`` MLP, shared across every
block, replaces per-block modulation parameters. Reuses the already-vendored
flux RoPE (``EmbedND`` + ``apply_rope1``) and the shared attention dispatcher.

Forward-call contract (for the generator / sampling agents)
-----------------------------------------------------------
``forward(x, timestep, context, attention_mask=None, ref_latents=None, image_slots=None, **kwargs)``

  * ``x``        — latent ``(B, C, H, W)`` (2D — 2.1 has no temporal axis),
                   ``C == in_channels`` (64, consumed UNPATCHED: no 2x2
                   packing, unlike 1.0).
  * ``timestep`` — ``(B,)`` flow-matching t in ``[0, 1]``.
  * ``context``  — text embeddings ``(B, L_txt, joint_attention_dim=4096)``
                   from Qwen3-VL-8B.
  * ``attention_mask`` — ``(B, L_txt)`` text padding mask (1 = keep).
  * ``ref_latents`` — optional list of ``(B, C, H, W)`` clean reference
                   latents (Qwen-Image-Edit), modulated from ``t = 0`` like
                   the text stream.
  * ``image_slots`` — optional list of int, one per ``ref_latents`` entry: the
                   index into ``context`` (as kept after the text encoder
                   dropped its own vision-pad tokens) where that reference's
                   image block is spliced INTO the text run, matching
                   ComfyUI's checkpoint ordering (the vision tower's grounding
                   position, not simply "after all the text"). A reference
                   past the end of ``image_slots`` (or every one, when it's
                   ``None``/empty) falls back to the text length — i.e.
                   appended after the whole text run, before the target.
  * ``step_cache`` — optional ``FirstBlockCache`` (FBCache step skipping);
                   ``None`` disables it, the byte-identical default. The
                   probe is the TARGET rows of block-0's output
                   (``hidden_states[:, prefix_len:]``) — prefix tokens are
                   pinned to ``t = 0`` regardless of trajectory step, so
                   including them would dilute the drift signal.

Returns velocity ``(B, out_channels=64, H, W)``.

Block-causal attention: the joint sequence is causal
(``q_idx >= kv_idx``), except every image block (each reference image, and
the target image) is internally bidirectional — a dense boolean mask (see
``_block_causal_mask``) for prefix (text + reference) query rows; the target
block's query rows always resolve to "every key", so ``_Attention`` runs them
unmasked (or key-padding-only) through the shared SDPA-capable dispatcher,
picking up an accelerated kernel where the prefix rows still fall back to
masked SDPA (no ``flex_attention`` dependency).

``causal_condition``: text and reference-image tokens are modulated from
``t = 0`` (not the sampled timestep) — cheap because ``time_text_embed`` only
ever computes ONE extra zero row per forward (batch-independent), shared by
every prefix token regardless of which sample it belongs to. Implemented by
doubling the batch dim of ``timestep`` with a single zero row (not one per
batch item) before the timestep embedding, then ``_split_rows`` gives every
block a ``(prefix_row, target_rows)`` pair for each of its four modulation
tensors.
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
from torch import Tensor

from vendor.gpl.comfyui.flux.layers import EmbedND
from vendor.gpl.comfyui.qwen_image21.layers import (
    QwenImage21TextProjection,
    QwenImage21TimestepProjEmbeddings,
    _Block,
    _LastLayer,
    set_attention_backend,
)

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule
from ...cache_identity import identity_usable, tensor_identity
from .config import QwenImage21Config

set_attention_backend(_dispatch_attention)


def convert_qwen_image21_state_dict(sd: dict[str, Tensor]) -> dict[str, Tensor]:
    """Fuse a diffusers-split ``img_mlp`` (``gate_layer``/``proj``) into this
    arch's own fused ``gate_up`` layout.

    A no-op on the Comfy-Org single file (the only shipping target today),
    which already stores the fused layout — this never renames a key the
    checkpoint already used the fused spelling for, it only combines a split
    checkpoint's two weights into the one this module expects.
    """
    out = dict(sd)
    i = 0
    while f"transformer_blocks.{i}.img_mlp.gate_layer.weight" in sd:
        prefix = f"transformer_blocks.{i}.img_mlp."
        gate = out.pop(prefix + "gate_layer.weight")
        up = out.pop(prefix + "proj.weight")
        out[prefix + "gate_up.weight"] = torch.cat([gate, up], dim=0)
        i += 1
    return out


def _split_rows(p: Tensor) -> tuple[Tensor, Tensor]:
    """Split a ``(B + 1, dim)`` modulation tensor into ``(prefix, target)``:
    the trailing ``t = 0`` row (shared by every prefix token, every sample)
    and the ``B`` real-timestep rows (one per sample's target tokens)."""
    return p[-1:].unsqueeze(1), p[:-1].unsqueeze(1)


def _block_causal_mask(token_kind: Tensor, key_valid: Tensor | None) -> Tensor:
    """Boolean attention mask: causal, except tokens sharing the same
    non-negative ``token_kind`` (an image block) attend to each other
    bidirectionally. ``token_kind`` is ``-1`` at text positions and a
    per-image-block id (0, 1, ... target last) at image positions.

    Returns a mask broadcastable to ``(B, H, N, N)`` — ``(1, 1, N, N)``
    without ``key_valid``, ``(B, 1, N, N)`` with it.
    """
    n = token_kind.shape[0]
    idx = torch.arange(n, device=token_kind.device)
    causal = idx[:, None] >= idx[None, :]
    same_block = (token_kind[:, None] == token_kind[None, :]) & (token_kind[:, None] >= 0)
    allowed = (causal | same_block).unsqueeze(0).unsqueeze(0)  # (1, 1, N, N)
    if key_valid is not None:
        allowed = allowed & key_valid[:, None, None, :]  # (B, 1, 1, N) -> (B, 1, N, N)
    return allowed


class QwenImage21DiT(NativeArchModule):
    """Qwen-Image-2.1 single-stream DiT."""

    def __init__(self, config: QwenImage21Config, operations, dtype=None, device=None):
        super().__init__()
        self.config = config
        self.patch_size = 1
        d = config.inner_dim
        self.pe_embedder = EmbedND(dim=config.attention_head_dim, theta=config.theta, axes_dim=list(config.axes_dims_rope))
        self.time_text_embed = QwenImage21TimestepProjEmbeddings(d, operations, dtype=dtype, device=device)
        self.txt_in = QwenImage21TextProjection(config.joint_attention_dim, d, operations, dtype=dtype, device=device)
        self.img_in = operations.Linear(config.in_channels, d, bias=False, dtype=dtype, device=device)
        self.modulation = nn.Sequential(nn.SiLU(), operations.Linear(d, 4 * d, bias=False, dtype=dtype, device=device))
        self.transformer_blocks = nn.ModuleList([
            _Block(d, config.num_attention_heads, config.attention_head_dim, operations,
                   mlp_ratio=config.mlp_ratio, dtype=dtype, device=device)
            for _ in range(config.num_layers)
        ])
        self.norm_out = _LastLayer(d, operations, dtype=dtype, device=device)
        self.proj_out = operations.Linear(d, config.out_channels, bias=False, dtype=dtype, device=device)


    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "QwenImage21DiT":
        """Build empty-weight from a detected config dict (wrap in
        ``with torch.device("meta")`` to avoid allocating the real weights)."""
        return cls(QwenImage21Config.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        """No-op: RoPE (EmbedND) and the sinusoidal timestep embedding are
        both computed per forward, so this arch has no derived buffer that
        needs recomputing after an assign-load."""
        return None


    def build_sequence(self, x: Tensor, context: Tensor, attention_mask: Tensor | None, ref_latents: list[Tensor],
                       image_slots: list[int] | None = None):
        """Each reference image spliced into the text run at its own
        ``image_slots`` entry (ComfyUI ordering), the target image last.

        A reference with no slot (``image_slots`` shorter than
        ``ref_latents``, or ``None``/empty entirely) falls back to the text
        length — appended after the whole text run, same as before this
        splicing was added, so a txt2img call (no refs) or a caller that
        never passes ``image_slots`` is byte-identical to the old
        text-then-every-ref-then-target layout.

        Returns ``(hidden_states, pe, prefix_len, token_kind, key_valid)``:
        ``prefix_len`` is the token count before the target image block
        (text + every reference image — all modulated from ``t = 0``);
        ``token_kind`` is ``-1`` at text positions and a per-image-block id
        at image positions (used by :func:`_block_causal_mask`); ``key_valid``
        is the padding-aware key mask over the full sequence, or ``None``
        when ``attention_mask`` is ``None``.
        """
        txt = self.txt_in(context)
        b, device = x.shape[0], x.device
        text_len = txt.shape[1]

        slots = (list(image_slots or []) + [text_len] * len(ref_latents))[:len(ref_latents)]
        bounds = [0] + slots + [text_len]

        parts, ids, kinds, masks = [], [], [], []
        pos, length, target_len = 0, 0, 0
        images = list(ref_latents) + [x]
        for block_index, ((start, end), img) in enumerate(zip(zip(bounds[:-1], bounds[1:]), images)):
            is_target = block_index == len(images) - 1
            n = end - start
            if n > 0:
                parts.append(txt[:, start:end])
                ids.append(torch.arange(pos, pos + n, device=device, dtype=torch.float32).view(n, 1).expand(n, 3))
                kinds.append(torch.full((n,), -1, device=device, dtype=torch.long))
                if attention_mask is not None:
                    masks.append(attention_mask[:, start:end].bool())
                pos += n
                length += n

            ih, iw = img.shape[-2], img.shape[-1]
            parts.append(self.img_in(img.flatten(2).transpose(1, 2)))
            hh = torch.arange(ih, device=device, dtype=torch.float32) - (ih - ih // 2)
            ww = torch.arange(iw, device=device, dtype=torch.float32) - (iw - iw // 2)
            ids.append(torch.stack([
                torch.full((ih, iw), float(pos), device=device, dtype=torch.float32),
                hh[:, None].expand(ih, iw),
                ww[None, :].expand(ih, iw),
            ], dim=-1).reshape(ih * iw, 3))
            kinds.append(torch.full((ih * iw,), block_index, device=device, dtype=torch.long))
            if attention_mask is not None:
                masks.append(attention_mask.new_ones(b, ih * iw, dtype=torch.bool))
            pos += max(ih, iw)
            length += ih * iw
            if is_target:
                target_len = ih * iw

        prefix_len = length - target_len
        hidden_states = torch.cat(parts, dim=1)
        ids_cat = torch.cat(ids, dim=0).unsqueeze(0).expand(b, -1, -1)
        pe = self.pe_embedder(ids_cat)
        token_kind = torch.cat(kinds, dim=0)
        key_valid = torch.cat(masks, dim=1) if attention_mask is not None else None

        return hidden_states, pe, prefix_len, token_kind, key_valid


    def _prefix_kv_cache(self, x: Tensor, context: Tensor, attention_mask: Tensor | None,
                        ref_latents: tuple[Tensor, ...], image_slots) -> tuple[Any, tuple | None]:
        cache = getattr(self, "run_cache", None)
        if cache is None:
            return None, None
        ids = (tensor_identity(context), tensor_identity(attention_mask),
               *(tensor_identity(ref) for ref in ref_latents))
        if not identity_usable(*ids):
            return cache, None
        key = ("qwen_image21.prefix_kv", cache.revision, tuple(x.shape), x.dtype, x.device,
               tuple(image_slots) if image_slots else (), *ids)
        return cache, key

    def forward(self, x: Tensor, timestep: Tensor, context: Tensor, attention_mask: Tensor | None = None,
                ref_latents=None, image_slots=None, **kwargs) -> Tensor:
        ref_latents = list(ref_latents or [])
        b, dtype = x.shape[0], x.dtype
        h, w = x.shape[-2], x.shape[-1]

        hidden_states, pe, prefix_len, token_kind, key_valid = self.build_sequence(
            x, context, attention_mask, ref_latents, image_slots)
        mask = _block_causal_mask(token_kind, key_valid)
        target_key_mask = None
        if key_valid is not None and not bool(key_valid.all()):
            target_key_mask = key_valid[:, None, None, :]

        t = ((timestep * 1000).to(dtype) / 1000).to(dtype)
        temb = self.time_text_embed(torch.cat([t, t.new_zeros(1)], dim=0), dtype)
        scale1, gate1, scale2, gate2 = self.modulation(temb).chunk(4, dim=-1)
        mod = (_split_rows(scale1), _split_rows(gate1.tanh()), _split_rows(scale2), _split_rows(gate2.tanh()))

        step_cache = kwargs.get("step_cache")
        prefix_cache, prefix_key = self._prefix_kv_cache(x, context, attention_mask, tuple(ref_latents), image_slots)
        cached_kvs = prefix_cache.get(prefix_key) if prefix_key is not None else None

        probe = None
        if cached_kvs is not None and prefix_len > 0:
            target = hidden_states[:, prefix_len:]
            target_pe = pe[:, :, prefix_len:]
            for i, block in enumerate(self.transformer_blocks):
                target = block(target, mod, target_pe, None, 0, target_key_mask, cached_kvs[i])
                if i == 0 and step_cache is not None:
                    probe = target
                    if step_cache.should_skip(probe):
                        return step_cache.record_skip()
            hidden_out = target
        else:
            capture = prefix_key is not None and prefix_len > 0
            captured_kvs = [] if capture else None
            for i, block in enumerate(self.transformer_blocks):
                if capture:
                    hidden_states, block_kv = block(hidden_states, mod, pe, mask, prefix_len, target_key_mask,
                                                      capture_prefix=True)
                    captured_kvs.append(block_kv)
                else:
                    hidden_states = block(hidden_states, mod, pe, mask, prefix_len, target_key_mask)
                if i == 0 and step_cache is not None:
                    probe = hidden_states[:, prefix_len:]
                    if step_cache.should_skip(probe):
                        return step_cache.record_skip()
            hidden_out = hidden_states[:, prefix_len:]
            if capture:
                prefix_cache.put(prefix_key, captured_kvs)

        hidden_out = self.norm_out(hidden_out, temb[:-1])
        hidden_out = self.proj_out(hidden_out)
        out = hidden_out.transpose(1, 2).reshape(b, self.config.out_channels, *x.shape[2:])
        if step_cache is not None and probe is not None:
            step_cache.record_compute(probe, out)
        return out
