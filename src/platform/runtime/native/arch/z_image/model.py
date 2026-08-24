# Derived from: comfy/ldm/lumina/model.py (Alpha-VLLM NextDiT; ComfyUI,
# GPL-3.0) — the block-level building blocks (timestep embedder, joint
# attention, SwiGLU FFN, sandwich-norm transformer block, final layer) moved
# to vendor/gpl/comfyui/z_image/layers.py verbatim; this class stays
# in src because it extends NativeArchModule (PotionUI's own loader contract)
# and orchestrates FBCache step-skipping + the learned pad-token bookkeeping,
# neither of which has a ComfyUI equivalent.

"""Z-Image NextDiT — ``ZImageDiT`` (``NativeArchModule``).

Vendored from ComfyUI ``comfy/ldm/lumina/model.py`` (Alpha-VLLM NextDiT), adapted
to the native ``operations`` seam and trimmed to the Z-Image text-to-image path.
The block-level building blocks live in ``vendor/gpl/comfyui/z_image/layers.py``;
this module keeps the top-level ``ZImageDiT`` class and its own forward-pass
orchestration (FBCache, pad-token bookkeeping).

Dropped from the ComfyUI original (all dormant on plain t2i): the ``omni`` /
``ref_latents`` / ``siglip`` editing branches, the ``clip_text_pooled`` /
``time_text_embed`` NewBie path, ``timestep_zero_index`` (only set when omni),
and the ``patcher_extension`` / ``patches`` hooks. Kept faithfully: the sandwich
double-norm blocks with tanh-gated adaLN, the caption/image refiner stacks, the
learned ``cap_pad_token`` / ``x_pad_token`` padding to ``pad_tokens_multiple``,
and the 3-axis RoPE. Reuses the already-vendored flux ``EmbedND`` + ``apply_rope``.

Forward-call contract (generator / sampling side)
-------------------------------------------------
``forward(x, timestep, context, y=None, guidance=None, attention_mask=None)``

  * ``x``        — latent ``(B, 16, H, W)`` (2D; Z-Image uses the Flux-style 2D
                   AE). Patchified 2x2 internally to ``(B, h*w, 64)``.
  * ``timestep`` — ``(B,)`` flow-matching t in ``[0, 1]``; internally becomes
                   ``t_embedder((1 - t) * time_scale)`` (``time_scale == 1000``).
  * ``context``  — caption embeddings ``(B, L, cap_feat_dim=2560)`` from Qwen3-4B
                   (penultimate hidden state).
  * ``y`` / ``guidance`` — unused (Z-Image has no pooled vector and no embedded
                   guidance). ``attention_mask`` is accepted but IGNORED: the DiT
                   pads the caption with a learned ``cap_pad_token`` and attends
                   without a mask, exactly like ComfyUI's NextDiT (``cap_mask =
                   None``). The generator encodes one prompt per forward (batch 1),
                   so the caption carries no tokenizer padding to mask anyway.

Returns velocity ``(B, 16, H, W)`` — the ComfyUI NextDiT ``-img`` sign, which is
exactly the ``v`` our Euler loop wants (``denoised = x - sigma*v`` reproduces
ComfyUI's flow ``denoised = x - model_output*sigma``).
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from vendor.gpl.comfyui.flux.layers import EmbedND
from vendor.gpl.comfyui.z_image.layers import (
    _FinalLayer,
    _JointTransformerBlock,
    _TimestepEmbedder,
    set_attention_backend,
)

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule
from .config import ZImageConfig

# vendor/gpl/comfyui/z_image/layers.py must not import src (layering guard) —
# this is the one module that constructs ZImageDiT (and therefore the
# _JointTransformerBlock/_JointAttention instances that call the injected
# attention backend), so wiring it here guarantees it's set before any
# forward() runs.
set_attention_backend(_dispatch_attention)


class ZImageDiT(NativeArchModule):
    """Z-Image NextDiT (Lumina-Image-2.0 backbone at dim 3840, z_image variant)."""

    def __init__(self, cfg: ZImageConfig, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.patch_size = cfg.patch_size
        self.in_channels = cfg.in_channels
        self.out_channels = cfg.in_channels
        self.time_scale = cfg.time_scale
        self.pad_tokens_multiple = cfg.pad_tokens_multiple
        dim = cfg.dim

        self.x_embedder = operations.Linear(
            cfg.patch_size * cfg.patch_size * cfg.in_channels, dim, bias=True, dtype=dtype, device=device
        )
        self.noise_refiner = nn.ModuleList(
            [_JointTransformerBlock(cfg, operations, modulation=True, dtype=dtype, device=device)
             for _ in range(cfg.n_refiner_layers)]
        )
        self.context_refiner = nn.ModuleList(
            [_JointTransformerBlock(cfg, operations, modulation=False, dtype=dtype, device=device)
             for _ in range(cfg.n_refiner_layers)]
        )
        self.t_embedder = _TimestepEmbedder(min(dim, 1024), 256, operations, dtype=dtype, device=device)
        self.cap_embedder = nn.Sequential(
            operations.RMSNorm(cfg.cap_feat_dim, eps=cfg.norm_eps, elementwise_affine=True, dtype=dtype, device=device),
            operations.Linear(cfg.cap_feat_dim, dim, bias=True, dtype=dtype, device=device),
        )
        self.layers = nn.ModuleList(
            [_JointTransformerBlock(cfg, operations, modulation=True, dtype=dtype, device=device)
             for _ in range(cfg.n_layers)]
        )
        self.final_layer = _FinalLayer(cfg, operations, dtype=dtype, device=device)
        self.x_pad_token = nn.Parameter(torch.empty((1, dim), device=device, dtype=dtype))
        self.cap_pad_token = nn.Parameter(torch.empty((1, dim), device=device, dtype=dtype))

        assert cfg.head_dim == sum(cfg.axes_dims)
        self.rope_embedder = EmbedND(dim=cfg.head_dim, theta=int(cfg.rope_theta), axes_dim=list(cfg.axes_dims))

        # latent_shape_for (engine.py) reads ``.params.in_channels`` for the 2D-VAE
        # families; expose it so Z-Image resolves (B,16,H//8,W//8) like Flux1.
        from types import SimpleNamespace
        self.params = SimpleNamespace(in_channels=cfg.in_channels)

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "ZImageDiT":
        return cls(ZImageConfig.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        """No-op: RoPE (EmbedND) and the sinusoidal timestep embedding are computed
        per forward from ``ids``/``t``, so there are no derived buffers to rebuild.
        The learned ``x_pad_token`` / ``cap_pad_token`` are checkpoint parameters,
        not computed state."""
        return None

    # -- embedding helpers --------------------------------------------------

    def _pad_tokens(self, feats: Tensor, pad_token: Tensor) -> Tensor:
        pad_extra = (-feats.shape[1]) % self.pad_tokens_multiple
        if pad_extra == 0:
            return feats
        pad = pad_token.to(device=feats.device, dtype=feats.dtype).unsqueeze(0).repeat(feats.shape[0], pad_extra, 1)
        return torch.cat((feats, pad), dim=1)

    def _cap_pos_ids(self, length: int, bsz: int, device) -> Tensor:
        ids = torch.zeros(bsz, length, 3, dtype=torch.float32, device=device)
        ids[:, :, 0] = torch.arange(length, dtype=torch.float32, device=device) + 1.0
        return ids

    def _img_pos_ids(self, start_t: int, h_tok: int, w_tok: int, bsz: int, device) -> Tensor:
        ids = torch.zeros((bsz, h_tok * w_tok, 3), dtype=torch.float32, device=device)
        ids[:, :, 0] = start_t
        ids[:, :, 1] = torch.arange(h_tok, dtype=torch.float32, device=device).view(-1, 1).repeat(1, w_tok).flatten()
        ids[:, :, 2] = torch.arange(w_tok, dtype=torch.float32, device=device).view(1, -1).repeat(h_tok, 1).flatten()
        return ids

    # -- forward ------------------------------------------------------------

    def forward(self, x: Tensor, timestep: Tensor, context: Tensor, y=None, guidance=None,
                attention_mask=None, **kwargs) -> Tensor:
        bsz, _, h, w = x.shape
        p = self.patch_size
        # Circular-pad H/W up to a patch multiple (standard resolutions are already
        # divisible, so this is usually a no-op).
        pad_h = (p - h % p) % p
        pad_w = (p - w % p) % p
        if pad_h or pad_w:
            x = F.pad(x, (0, pad_w, 0, pad_h), mode="circular")
        _, _, ph, pw = x.shape
        h_tok, w_tok = ph // p, pw // p
        device = x.device

        # timestep -> adaLN vector. ComfyUI: t = 1 - sigma, embed(t * time_scale).
        t = 1.0 - timestep
        adaln_input = self.t_embedder(t * self.time_scale, dtype=x.dtype)

        # caption stream: embed -> pad -> context refiner.
        cap = self.cap_embedder(context)
        cap = self._pad_tokens(cap, self.cap_pad_token)
        cap_len = cap.shape[1]
        cap_pos = self._cap_pos_ids(cap_len, bsz, device)
        cap_rope = self.rope_embedder(cap_pos).movedim(1, 2).to(x.dtype)
        for layer in self.context_refiner:
            cap = layer(cap, cap_rope, None)

        # image stream: patchify -> embed -> pad -> noise refiner.
        img = x.view(bsz, self.in_channels, h_tok, p, w_tok, p).permute(0, 2, 4, 3, 5, 1).flatten(3).flatten(1, 2)
        img = self.x_embedder(img)
        img_pos = self._img_pos_ids(cap_len + 1, h_tok, w_tok, bsz, device)
        img = self._pad_tokens(img, self.x_pad_token)
        if img.shape[1] != img_pos.shape[1]:  # x-pad tokens extend the position ids too
            extra = img.shape[1] - img_pos.shape[1]
            img_pos = F.pad(img_pos, (0, 0, 0, extra))
        img_rope = self.rope_embedder(img_pos).movedim(1, 2).to(x.dtype)
        img_len = img.shape[1]
        for layer in self.noise_refiner:
            img = layer(img, img_rope, adaln_input)

        # joint stack over [caption ; image].
        joint = torch.cat((cap, img), dim=1)
        rope = torch.cat((cap_rope, img_rope), dim=1)
        # FBCache: block-0's joint-sequence output is the change proxy; a skip
        # reuses the last computed output and bypasses layers 1..N + final_layer.
        step_cache = kwargs.get("step_cache")
        probe = None
        for i, layer in enumerate(self.layers):
            joint = layer(joint, rope, adaln_input)
            if i == 0 and step_cache is not None:
                probe = joint
                if step_cache.should_skip(probe):
                    return step_cache.record_skip()

        joint = self.final_layer(joint, adaln_input)
        # unpatchify the real image tokens (drop caption + trailing x-pad tokens).
        img_tokens = joint[:, cap_len:cap_len + h_tok * w_tok]
        out = img_tokens.view(bsz, h_tok, w_tok, p, p, self.out_channels)
        out = out.permute(0, 5, 1, 3, 2, 4).reshape(bsz, self.out_channels, ph, pw)
        out_final = -out[:, :, :h, :w]
        if step_cache is not None and probe is not None:
            step_cache.record_compute(probe, out_final)
        return out_final
