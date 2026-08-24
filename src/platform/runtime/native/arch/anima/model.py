# Derived from: comfy/ldm/cosmos/predict2.py (MiniTrainDIT) + comfy/ldm/anima/
# model.py (LLMAdapter) — ComfyUI, GPL-3.0. The block-level building blocks
# moved to vendor/gpl/comfyui/anima/layers.py verbatim (see that
# file's header for the per-class dual-ancestry breakdown against NVIDIA's
# actual cosmos-predict2 upstream — most MiniTrainDIT layers are faithful
# ports of Apache-2.0 NVIDIA code, but the RoPE construction/application and
# all of LLMAdapter are ComfyUI-original). This class stays in src because it
# extends NativeArchModule (PotionUI's own loader contract) and orchestrates
# FBCache step-skipping + the text-fusion call, neither of which has a
# ComfyUI or NVIDIA equivalent.

"""Anima DiT — ``Anima`` (``NativeArchModule``).

Vendored from ComfyUI ``comfy/ldm/cosmos/predict2.py`` (the ``MiniTrainDIT``
backbone + its ``VideoRopePosition3DEmb`` positional embedding) and
``comfy/ldm/anima/model.py`` (the ``LLMAdapter`` text-fusion head), adapted to
the native ``operations`` seam. The block-level building blocks live in
``vendor/gpl/comfyui/anima/layers.py``; this module keeps the top-level
``Anima`` class and its own forward-pass orchestration (FBCache, text fusion).
The two vendored pieces are kept numerically verbatim (only ``torch.nn.*`` ->
``operations.*`` for the parameterised layers, and ``comfy`` helpers
reimplemented locally); the attention math and the RoPE construction are
unchanged so the arch reproduces ComfyUI bit-for-bit.

What Anima IS
-------------
An adaLN-modulated 3D DiT (self-attn with 3D RoPE + cross-attn to a text context
+ gated MLP, ``use_adaln_lora``) that predicts flow-matching velocity for a Wan21
16-channel causal-3D latent. For a still image T == 1. The novel part is the
in-model ``LLMAdapter``: it embeds a set of T5 token ids (its own
``Embedding(32128, 1024)``) as a target sequence and cross-attends that to the
Qwen3-0.6B hidden state, producing the DiT's cross-attention context. That fusion
runs inside :meth:`preprocess_text_embeds` and is invoked from :meth:`forward`.

Forward-call contract (for the generator adapter)
-------------------------------------------------
``forward(x, timestep, context, y=None, guidance=None, attention_mask=None,
          t5xxl_ids=None, t5xxl_weights=None, **kwargs)``

  * ``x``        — Wan21 latent ``(B, 16, T, H, W)`` (5D; T == 1 for images).
  * ``timestep`` — ``(B,)`` flow-matching sigma in ``[0, 1]``, used AS-IS. Anima
                   is a ``ModelType.FLOW`` model but its ``sampling_settings`` set
                   ``multiplier = 1.0`` (supported_models.py), so
                   ``ModelSamplingDiscreteFlow.timestep(sigma) == sigma`` — the DiT
                   gets the raw sigma and the ``Timesteps`` embedding must NOT
                   rescale it (unlike Flux/Qwen, which bake an x1000 into the
                   embedding). ComfyUI ``model_base._apply_model`` passes
                   ``model_sampling.timestep(sigma)`` straight to the DiT.
  * ``context``  — Qwen3-0.6B last hidden state ``(B, S_qwen, 1024)`` (the LLM
                   adapter's cross-attention *source*).
  * ``t5xxl_ids``     — ``(B, S_t5)`` long: the adapter's target token sequence.
  * ``t5xxl_weights`` — ``(B, S_t5)`` float: per-t5-token prompt weights.
  * ``y`` / ``guidance`` / ``attention_mask`` — accepted for the generic engine
    ``model_forward`` signature and ignored (Anima has no vector input, uses true
    CFG rather than embedded guidance, and cross-attends over the zero-padded
    fused context without a mask, matching ComfyUI).

Returns velocity ``(B, out_channels=16, T, H, W)``.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange

from vendor.gpl.comfyui.anima.layers import (
    VideoRopePosition3DEmb,
    _Block,
    _FinalLayer,
    _LLMAdapter,
    _PatchEmbed,
    _TimestepEmbedding,
    _Timesteps,
    set_attention_backend,
)

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule
from .config import AnimaConfig

Tensor = torch.Tensor

# vendor/gpl/comfyui/anima/layers.py must not import src (layering guard) —
# this is the one module that constructs Anima (and therefore the _Block/
# _AdapterBlock instances that call the injected attention backend), so
# wiring it here guarantees it's set before any forward() runs.
set_attention_backend(_dispatch_attention)


# ---------------------------------------------------------------------------
# Local reimplementations of the comfy helpers the vendored code imported.
# ---------------------------------------------------------------------------
def _pad_to_patch_size(img: Tensor, patch_size: Tuple[int, int, int]) -> Tensor:
    """Circular-pad the trailing (T, H, W) dims up to a patch multiple.

    Mirrors ``comfy.ldm.common_dit.pad_to_patch_size`` (circular padding). For a
    T == 1 image at a resolution divisible by ``patch_spatial`` this is a no-op.
    """
    pad = ()
    for i in range(img.ndim - 2):
        pad = (0, (patch_size[i] - img.shape[i + 2] % patch_size[i]) % patch_size[i]) + pad
    if not any(pad):
        return img
    return F.pad(img, pad, mode="circular")


# ---------------------------------------------------------------------------
# The Anima module.
# ---------------------------------------------------------------------------
class Anima(NativeArchModule):
    """Anima DiT: MiniTrainDIT backbone + in-model LLMAdapter text fusion."""

    def __init__(self, config: AnimaConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.config = config
        self.patch_size = config.patch_spatial          # engine helpers read `.patch_size`
        self.patch_spatial = config.patch_spatial
        self.patch_temporal = config.patch_temporal
        self.out_channels = config.out_channels
        self.concat_padding_mask = config.concat_padding_mask
        mc = config.model_channels

        self.pos_embedder = VideoRopePosition3DEmb(
            head_dim=config.head_dim,
            len_h=config.max_img_h // config.patch_spatial,
            len_w=config.max_img_w // config.patch_spatial,
            len_t=config.max_frames // config.patch_temporal,
            h_extrapolation_ratio=config.rope_h_extrapolation_ratio,
            w_extrapolation_ratio=config.rope_w_extrapolation_ratio,
            t_extrapolation_ratio=config.rope_t_extrapolation_ratio,
            enable_fps_modulation=config.rope_enable_fps_modulation,
        )

        self.t_embedder = nn.Sequential(
            _Timesteps(mc),
            _TimestepEmbedding(mc, mc, config.use_adaln_lora, operations, device=device, dtype=dtype),
        )
        in_channels = config.in_channels + 1 if config.concat_padding_mask else config.in_channels
        self.x_embedder = _PatchEmbed(config.patch_spatial, config.patch_temporal, in_channels, mc, operations, device=device, dtype=dtype)
        self.blocks = nn.ModuleList([
            _Block(mc, config.crossattn_emb_channels, config.num_heads, config.mlp_ratio,
                   config.use_adaln_lora, config.adaln_lora_dim, operations, device=device, dtype=dtype)
            for _ in range(config.num_blocks)
        ])
        self.final_layer = _FinalLayer(mc, config.patch_spatial, config.patch_temporal, config.out_channels,
                                       config.use_adaln_lora, config.adaln_lora_dim, operations, device=device, dtype=dtype)
        self.t_embedding_norm = operations.RMSNorm(mc, eps=1e-6, device=device, dtype=dtype)
        self.llm_adapter = _LLMAdapter(
            config.llm_source_dim, config.llm_target_dim, config.llm_model_dim,
            config.llm_num_layers, config.llm_num_heads, config.llm_vocab_size,
            operations, device=device, dtype=dtype,
        )

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "Anima":
        return cls(AnimaConfig.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        """Recompute the non-persistent RoPE buffers meta-construction left as
        garbage: the DiT's 3D-RoPE range tables and the LLMAdapter's ``inv_freq``.
        This is the first native family whose ``post_load`` does real recompute
        work — both range tables and ``inv_freq`` are ``__init__``-computed
        (never in the checkpoint), so without this they load as meta/garbage and
        the base-class no-meta assert (correctly) fires."""
        device = self.t_embedding_norm.weight.device
        self.pos_embedder.recompute_buffers(device)
        self.llm_adapter.rotary_emb.recompute_inv_freq(device)

    # -- text fusion --------------------------------------------------------

    def preprocess_text_embeds(self, source_hidden_states: Tensor, target_input_ids: Tensor) -> Tensor:
        """Run the in-model LLMAdapter: Qwen3 hidden + T5 ids -> cross-attn context."""
        return self.llm_adapter(source_hidden_states, target_input_ids)

    # -- forward ------------------------------------------------------------

    def forward(self, x: Tensor, timestep: Tensor, context: Tensor, y: Optional[Tensor] = None,
                guidance: Optional[Tensor] = None, attention_mask: Optional[Tensor] = None,
                t5xxl_ids: Optional[Tensor] = None, t5xxl_weights: Optional[Tensor] = None,
                **kwargs) -> Tensor:
        # Fuse the text context through the LLMAdapter (ComfyUI's extra_conds).
        crossattn = context
        if t5xxl_ids is not None:
            crossattn = self.preprocess_text_embeds(context.to(dtype=x.dtype), t5xxl_ids.to(device=x.device))
            if t5xxl_weights is not None:
                crossattn = crossattn * t5xxl_weights.unsqueeze(-1).to(crossattn)
            if crossattn.shape[1] < 512:
                crossattn = F.pad(crossattn, (0, 0, 0, 512 - crossattn.shape[1]))

        # Anima overrides ModelSamplingDiscreteFlow's default multiplier to 1.0
        # (supported_models.py: sampling_settings["multiplier"] == 1.0), so
        # timestep(sigma) == sigma — the DiT receives the RAW sigma in [0, 1] and
        # the MiniTrainDIT `Timesteps` embedding must NOT rescale it (ComfyUI
        # model_base._apply_model passes model_sampling.timestep(sigma) straight
        # through). This is unlike Flux/Qwen, whose embedding bakes in an x1000.
        return self._dit_forward(x, timestep, crossattn, step_cache=kwargs.get("step_cache"))

    def _dit_forward(self, x: Tensor, timesteps: Tensor, crossattn_emb: Tensor, step_cache=None) -> Tensor:
        orig_shape = list(x.shape)
        x = _pad_to_patch_size(x, (self.patch_temporal, self.patch_spatial, self.patch_spatial))

        # Concat the padding-mask channel (all-zero for a full frame), then patchify.
        if self.concat_padding_mask:
            mask = torch.zeros(x.shape[0], 1, x.shape[3], x.shape[4], dtype=x.dtype, device=x.device)
            x = torch.cat([x, mask.unsqueeze(1).repeat(1, 1, x.shape[2], 1, 1)], dim=1)
        x_B_T_H_W_D = self.x_embedder(x)
        rope_emb = self.pos_embedder(x_B_T_H_W_D, fps=None, device=x.device)

        if timesteps.ndim == 1:
            timesteps = timesteps.unsqueeze(1)
        t_emb, adaln_lora = self.t_embedder[1](self.t_embedder[0](timesteps).to(x_B_T_H_W_D.dtype))
        t_emb = self.t_embedding_norm(t_emb)

        rope_emb = rope_emb.unsqueeze(1).unsqueeze(0)
        # FBCache: block-0's output is the change proxy; a skip reuses the last
        # computed output and bypasses blocks 1..N + final_layer.
        probe = None
        for i, block in enumerate(self.blocks):
            x_B_T_H_W_D = block(x_B_T_H_W_D, t_emb, crossattn_emb, rope_emb, adaln_lora)
            if i == 0 and step_cache is not None:
                probe = x_B_T_H_W_D
                if step_cache.should_skip(probe):
                    return step_cache.record_skip()

        x_B_T_H_W_O = self.final_layer(x_B_T_H_W_D, t_emb, adaln_lora)
        out = rearrange(
            x_B_T_H_W_O,
            "b t h w (p1 p2 tt c) -> b c (t tt) (h p1) (w p2)",
            p1=self.patch_spatial, p2=self.patch_spatial, tt=self.patch_temporal,
        )
        result = out[:, :, : orig_shape[-3], : orig_shape[-2], : orig_shape[-1]]
        if step_cache is not None and probe is not None:
            step_cache.record_compute(probe, result)
        return result
