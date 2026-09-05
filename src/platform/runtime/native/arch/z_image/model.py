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

from typing import Any, NamedTuple

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
from ...cache_identity import identity_usable, tensor_identity
from .config import ZImageConfig

# vendor/gpl/comfyui/z_image/layers.py must not import src (layering guard) —
# this is the one module that constructs ZImageDiT (and therefore the
# _JointTransformerBlock/_JointAttention instances that call the injected
# attention backend), so wiring it here guarantees it's set before any
# forward() runs.
set_attention_backend(_dispatch_attention)


class _RefinedCaption(NamedTuple):
    """A refined caption plus the caption tensor its key names.

    Holding the source is what makes ``data_ptr`` identity a sound key: while the
    entry lives its source cannot be freed, so no later tensor can be allocated at
    that address and be mistaken for it.
    """

    cap: Tensor
    context: Tensor


class _Geometry:
    """RoPE for one sequence shape, plus the captions refined against it.

    The refined captions hang off the geometry rather than off their own run-cache
    key so that one run occupies one entry: the geometry is shared by both
    guidance branches, and only the captions differ between them. Each caption
    carries the weight revision in its own key too, so a branch refined under
    superseded weights can never answer even while its geometry entry lives.

    ``context`` is pinned alongside each refined caption. ``tensor_identity``
    already rejects a stale entry after an in-place write; pinning additionally
    stops a freed caption's address being re-let to a new tensor that would then
    present the same identity.
    """

    __slots__ = ("cap_rope", "img_rope", "joint_rope", "captions")

    def __init__(self, cap_rope: Tensor, img_rope: Tensor) -> None:
        self.cap_rope = cap_rope
        self.img_rope = img_rope
        self.joint_rope = torch.cat((cap_rope, img_rope), dim=1)
        self.captions: dict[tuple, _RefinedCaption] = {}


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

    # -- per-run reuse ------------------------------------------------------

    # One refined caption per guidance branch; a CFG run presents no third one.
    _CAPTION_SLOTS = 2

    def _geometry(self, bsz: int, h_tok: int, w_tok: int, cap_len: int, device,
                  dtype: torch.dtype) -> "_Geometry":
        """RoPE for one ``(batch, token grid, caption length)`` shape.

        Position ids and the frequencies built from them are a property of the
        sequence shape alone — never of the timestep, the latent or the weights.
        The key carries the revision anyway, per ``RunCache``'s contract: the
        cache drops every entry when the revision moves, so leaving it out would
        not spare this entry a rebuild, and one rule for both keys is easier to
        hold than an exception.

        When the engine has attached a ``run_cache`` (``NativeGenerator.sample``)
        this is built once per run; without one every forward builds its own,
        exactly as before.
        """
        cache = getattr(self, "run_cache", None)
        key = None
        if cache is not None:
            key = ("z_image.geometry", cache.revision, bsz, h_tok, w_tok, cap_len, device, dtype)
            hit = cache.get(key)
            if hit is not None:
                return hit

        cap_rope = self.rope_embedder(self._cap_pos_ids(cap_len, bsz, device)).movedim(1, 2).to(dtype)
        img_pos = self._img_pos_ids(cap_len + 1, h_tok, w_tok, bsz, device)
        img_len = h_tok * w_tok + (-(h_tok * w_tok)) % self.pad_tokens_multiple
        if img_len != img_pos.shape[1]:  # x-pad tokens extend the position ids too
            img_pos = F.pad(img_pos, (0, 0, 0, img_len - img_pos.shape[1]))
        img_rope = self.rope_embedder(img_pos).movedim(1, 2).to(dtype)
        geometry = _Geometry(cap_rope, img_rope)
        if key is not None:
            cache.put(key, geometry)
        return geometry

    def _refined_caption(self, geometry: "_Geometry", context: Tensor, bsz: int,
                         cap_len: int, dtype: torch.dtype) -> Tensor:
        """Caption embed + learned pad + the whole ``context_refiner`` stack.

        The refiner blocks are built with ``modulation=False`` and are called with
        ``adaln_input=None``, so this output is a property of the guidance branch
        and the weights only — the timestep and the latent never reach it, and the
        joint stack reads the result without writing it, so handing every step the
        same tensor object is byte-for-byte the recompute.

        The weight revision is read fresh on every lookup and never held across
        steps: a step-windowed adapter applies and restores DURING a run, so a
        value captured once at run start would answer with a caption refined by
        weights that are no longer in effect.

        ``bsz``, ``cap_len``, the device and the compute dtype are absent from the
        key ON PURPOSE — the geometry entry these captions hang off is already
        keyed on all four. Move the captions out from under it and they have to
        come back.
        """
        cache = getattr(self, "run_cache", None)
        revision = cache.revision if cache is not None else 0
        identity = tensor_identity(context)
        key = (revision, identity) if identity_usable(identity) else None
        if key is not None:
            hit = geometry.captions.get(key)
            if hit is not None:
                return hit.cap

        cap = self.cap_embedder(context)
        cap = self._pad_tokens(cap, self.cap_pad_token)
        for layer in self.context_refiner:
            cap = layer(cap, geometry.cap_rope, None)
        if key is not None:
            while len(geometry.captions) >= self._CAPTION_SLOTS:
                del geometry.captions[next(iter(geometry.captions))]
            geometry.captions[key] = _RefinedCaption(cap, context)
        return cap

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

        # caption stream: embed -> pad -> context refiner. Reused across the steps
        # of a run (the whole stream is timestep- and latent-independent), as are
        # the position frequencies for this geometry.
        cap_len = context.shape[1] + (-context.shape[1]) % self.pad_tokens_multiple
        geometry = self._geometry(bsz, h_tok, w_tok, cap_len, device, x.dtype)
        cap = self._refined_caption(geometry, context, bsz, cap_len, x.dtype)

        # image stream: patchify -> embed -> pad -> noise refiner.
        img = x.view(bsz, self.in_channels, h_tok, p, w_tok, p).permute(0, 2, 4, 3, 5, 1).flatten(3).flatten(1, 2)
        img = self.x_embedder(img)
        img = self._pad_tokens(img, self.x_pad_token)
        for layer in self.noise_refiner:
            img = layer(img, geometry.img_rope, adaln_input)

        # joint stack over [caption ; image].
        joint = torch.cat((cap, img), dim=1)
        rope = geometry.joint_rope
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

        # Take the real image tokens (drop caption + trailing x-pad tokens) BEFORE
        # the head: _FinalLayer is token-local (per-token norm, broadcast adaLN
        # scale, per-token linear), so head work on the dropped rows is discarded.
        img_tokens = self.final_layer(joint[:, cap_len:cap_len + h_tok * w_tok], adaln_input)
        out = img_tokens.view(bsz, h_tok, w_tok, p, p, self.out_channels)
        out = out.permute(0, 5, 1, 3, 2, 4).reshape(bsz, self.out_channels, ph, pw)
        out_final = -out[:, :, :h, :w]
        if step_cache is not None and probe is not None:
            step_cache.record_compute(probe, out_final)
        return out_final
