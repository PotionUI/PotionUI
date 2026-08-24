# Derived from: diffusers `src/diffusers/models/transformers/transformer_minimax_music3.py`
# and `.../models/transformers/condition_embedder_minimax_music3.py` (Apache-2.0,
# "Copyright 2026 The MiniMax Team and The HuggingFace Team") for the architecture
# (Fourier timestep embedding, partial RoPE, prepended-timestep-token forward,
# concat-then-residual-conv wrapper, softmax layer-mix condition encoder). Module
# and parameter NAMES instead target the Comfy-Org single-file repack's own layout
# (`diffusion_transformer.transformer.layers.*`, `ff.ff.0.proj`/`ff.ff.2`, `pre_norm.
# gamma`/`beta`, top-level `cond_layer_logits`/`latent_conditioners.0`), verified
# against `ai/minimax_music3/minimax_music3_dit_fp16_header.json` (the real repack
# header, 374/374 keys) — a structural port, not a name-for-name one, same posture as
# `arch/minimax_h3/model.py`. The value-first/gate-second GLU chunk order was
# cross-checked against ComfyUI's (GPL-3.0) `comfy/ldm/minimax/dit.py` `GLU` class
# for agreement with diffusers' own `ff_in.weight.chunk(2)` convention (consult-only,
# not copied) — both land on the same order, so unlike MiniMax-H3's MLP this is not a
# per-repack fork.

"""MiniMax-Music3 flow-matching DiT + fused condition encoder — ``MiniMaxMusic3Model``
(``NativeArchModule``).

One file in the repack holds both halves of the flow-matching stage: the condition
encoder (top-level `cond_layer_logits` / `cond_layer_scale` / `latent_conditioners.0`,
no prefix) that projects the autoregressive stage's per-frame hidden states onto the
Flow-VAE latent timeline, and the 36-block DiT itself (`diffusion_transformer.*`) that
denoises those latents conditioned on it. They are wired as one ``NativeArchModule`` so
the loader treats the whole file as one placement unit — see
:mod:`.flow` for the windowed Euler loop that drives this module's ``forward`` and
``encode_condition`` across a song.

Forward: ``concat(latent, zeros_like(latent), condition.T)`` on the channel axis ->
residual 1x1 ``preprocess_conv`` -> ``project_in`` -> prepend the Fourier timestep
token -> 36 pre-norm blocks (fused ``to_qkv`` self-attention with partial RoPE on the
first 32 of 64 head dims, no q/k-norm; value-first/gate-second GLU feed-forward) ->
drop the timestep token -> ``project_out`` -> residual 1x1 ``postprocess_conv``. The
flow-matching ``timestep`` runs ``0`` (noise) to ``1`` (data) — the inverse of this
engine's usual descending-sigma convention (see :mod:`.flow`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule

MINIMAX_MUSIC3_DIT = "minimax_music3_dit"


@dataclass(frozen=True)
class MiniMaxMusic3DitConfig:
    """Fully-resolved MiniMax-Music3 DiT + condition-encoder hyperparameters.

    Every field is an architecture constant of the single released checkpoint (no
    tensor shape in this file's own state dict varies across a released variant the
    way MiniMax-H3's full/pruned split does) — see
    ``ai/minimax_music3/minimax_music3_dit_fp16_header.json``.
    """

    in_channels: int = 128
    condition_dim: int = 2048           # DiT-side conditioning width, post condition-encoder
    condition_hidden_dim: int = 4096    # AR-stage per-layer hidden width the condition encoder mixes
    num_condition_layers: int = 8       # 1 LLM hidden + 7 depth-decoder steps
    num_layers: int = 36
    num_attention_heads: int = 32
    attention_head_dim: int = 64
    ffn_inner_dim: int = 8192
    rotary_dim: int = 32                # of the 64-dim head; the rest passes through unrotated
    rope_theta: float = 1e4
    fourier_dim: int = 256
    norm_eps: float = 1e-5
    # Condition encoder's frame-rate -> latent-rate resampling ratio (see `latent_length`).
    latents_per_frame: float = 44100.0 / 24000.0 * 960.0 / 512.0

    @property
    def inner_dim(self) -> int:
        return self.num_attention_heads * self.attention_head_dim

    @property
    def concat_channels(self) -> int:
        return 2 * self.in_channels + self.condition_dim

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "MiniMaxMusic3DitConfig":
        if config.get("image_model") != MINIMAX_MUSIC3_DIT:
            raise ValueError(
                f"MiniMaxMusic3DitConfig: unsupported image_model {config.get('image_model')!r}"
            )
        return cls(
            in_channels=int(config.get("in_channels", 128)),
            condition_dim=int(config.get("condition_dim", 2048)),
            condition_hidden_dim=int(config.get("condition_hidden_dim", 4096)),
            num_condition_layers=int(config.get("num_condition_layers", 8)),
            num_layers=int(config.get("num_layers", 36)),
            num_attention_heads=int(config.get("num_attention_heads", 32)),
            attention_head_dim=int(config.get("attention_head_dim", 64)),
            ffn_inner_dim=int(config.get("ffn_inner_dim", 8192)),
            rotary_dim=int(config.get("rotary_dim", 32)),
            rope_theta=float(config.get("rope_theta", 1e4)),
            fourier_dim=int(config.get("fourier_dim", 256)),
            norm_eps=float(config.get("norm_eps", 1e-5)),
        )


def latent_length(num_frames: int, latents_per_frame: float = MiniMaxMusic3DitConfig.latents_per_frame) -> int:
    """Autoregressive frames -> Flow-VAE latent count (``floor``, minimum 1).

    ``latents_per_frame`` = ``output_sampling_rate/input_sampling_rate *
    input_hop_length/output_hop_length`` = ``44100/24000 * 960/512`` = ``441/128`` =
    ``3.4453125`` — the AR stage runs at 25 fps / 24 kHz-equivalent hop 960, the
    Flow-VAE latent grid at 44.1 kHz hop 512. Also used by :mod:`.flow` for the
    windowed loop's per-window latent counts and crop algebra.
    """
    return max(1, int(num_frames * latents_per_frame))


def _cast_to(x: Tensor, linear: nn.Module) -> Tensor:
    """Align an activation with a mixed-precision boundary Linear's own dtype."""
    return x.to(linear.weight.dtype)


class MiniMaxMusic3LayerNorm(nn.Module):
    """LayerNorm with the checkpoint's own ``gamma``/``beta`` naming (NOT
    ``nn.LayerNorm``'s ``weight``/``bias`` — a strict load needs the real keys).
    Registered as buffers, not parameters: inference-only, matching the repack's own
    convention. Computed at fp32 internally regardless of storage dtype, the standing
    high-precision-norm practice this engine applies to every family."""

    def __init__(self, dim: int, eps: float = 1e-5, dtype=None, device=None) -> None:
        super().__init__()
        self.eps = eps
        self.register_buffer("gamma", torch.empty(dim, dtype=dtype, device=device))
        self.register_buffer("beta", torch.empty(dim, dtype=dtype, device=device))

    def forward(self, x: Tensor) -> Tensor:
        out = F.layer_norm(x.float(), (x.shape[-1],), self.gamma.float(), self.beta.float(), self.eps)
        return out.to(x.dtype)


class MiniMaxMusic3FourierEmbedding(nn.Module):
    """Random Fourier features over the flow-matching time in ``[0, 1]``; the
    projection (``timestep_features.weight``, ``[dim // 2, 1]``) is a trained
    checkpoint weight, not a fixed sinusoid table. Computed at fp32 for the
    trig regardless of storage dtype, cast back to the caller's requested dtype."""

    def __init__(self, dim: int, dtype=None, device=None) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.empty(dim // 2, 1, dtype=dtype, device=device))

    def forward(self, timestep: Tensor) -> Tensor:
        angles = 2.0 * math.pi * timestep.float().unsqueeze(-1) @ self.weight.float().T
        return torch.cat((angles.cos(), angles.sin()), dim=-1)


def _apply_partial_rotary_emb(x: Tensor, cos: Tensor, sin: Tensor) -> Tensor:
    """Rotate the leading ``rotary_dim`` channels of every head; pass the rest
    through. ``x``: ``(B, S, H, D)``. ``cos``/``sin``: ``(S, rotary_dim)``."""
    rotary_dim = cos.shape[-1]
    x_rot, x_pass = x[..., :rotary_dim], x[..., rotary_dim:]
    cos = cos.to(x.dtype)[None, :, None, :]
    sin = sin.to(x.dtype)[None, :, None, :]
    x1, x2 = x_rot.chunk(2, dim=-1)
    x_rotated = torch.cat((-x2, x1), dim=-1)
    x_rot = x_rot * cos + x_rotated * sin
    return torch.cat((x_rot, x_pass), dim=-1)


class MiniMaxMusic3RotaryPosEmb(nn.Module):
    """Partial-RoPE frequency table. ``inv_freq`` is present in the checkpoint but
    recomputed in ``post_load`` regardless (this engine's standing rotary-buffer
    rule — a meta-constructed buffer plus an assign-load is never trusted)."""

    def __init__(self, rotary_dim: int, dtype=None, device=None) -> None:
        super().__init__()
        self.rotary_dim = rotary_dim
        self.register_buffer("inv_freq", torch.empty(rotary_dim // 2, dtype=torch.float32, device=device))

    def forward(self, seq_len: int, device: torch.device) -> tuple[Tensor, Tensor]:
        inv_freq = self.inv_freq.to(device=device, dtype=torch.float32)
        steps = torch.arange(seq_len, device=device, dtype=torch.float32)
        freqs = torch.outer(steps, inv_freq)
        freqs = torch.cat((freqs, freqs), dim=-1)
        return freqs.cos().contiguous(), freqs.sin().contiguous()


class MiniMaxMusic3Attention(nn.Module):
    """Self-attention with a fused ``to_qkv`` projection (kept fused — the int8
    repack quantizes the fused matrix; chunk the OUTPUT at forward time, not the
    stored weight). No q/k-norm anywhere in this checkpoint, unlike MiniMax-H3."""

    def __init__(self, dim: int, heads: int, head_dim: int, rotary_dim: int, operations,
                 dtype=None, device=None) -> None:
        super().__init__()
        self.heads = heads
        self.head_dim = head_dim
        self.rotary_dim = rotary_dim
        inner_dim = heads * head_dim
        self.to_qkv = operations.Linear(dim, 3 * inner_dim, bias=False, dtype=dtype, device=device)
        self.to_out = operations.Linear(inner_dim, dim, bias=False, dtype=dtype, device=device)

    def forward(self, x: Tensor, rotary_emb: tuple[Tensor, Tensor]) -> Tensor:
        b, s, _ = x.shape
        q, k, v = self.to_qkv(x).chunk(3, dim=-1)
        q = q.view(b, s, self.heads, self.head_dim)
        k = k.view(b, s, self.heads, self.head_dim)
        v = v.view(b, s, self.heads, self.head_dim)
        cos, sin = rotary_emb
        q = _apply_partial_rotary_emb(q, cos, sin)
        k = _apply_partial_rotary_emb(k, cos, sin)
        # No attention mask, ever: one packed 1-D sequence, nothing to hide.
        out = _dispatch_attention(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2),
                                   heads=self.heads, mask=None)
        out = out.transpose(1, 2).reshape(b, s, -1)
        return self.to_out(out)


class MiniMaxMusic3GLU(nn.Module):
    """``proj`` produces ``[value | gate]`` concatenated on the output axis;
    value-first/gate-second (see this module's provenance note at the top of the
    file — both diffusers and ComfyUI agree on this order for this checkpoint)."""

    def __init__(self, dim_in: int, dim_out: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.proj = operations.Linear(dim_in, dim_out * 2, bias=True, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        value, gate = self.proj(x).chunk(2, dim=-1)
        return value * F.silu(gate)


class MiniMaxMusic3FeedForward(nn.Module):
    """``ff.ff`` is a 3-slot ``Sequential`` in the checkpoint: the GLU projection
    (index 0, itself wrapping a ``.proj`` Linear), an inert dropout (index 1, no
    checkpoint keys), and the down-projection (index 2, a plain Linear) — the
    checkpoint's own nesting, not a naming choice made here."""

    def __init__(self, dim: int, ffn_inner_dim: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.ff = nn.Sequential(
            MiniMaxMusic3GLU(dim, ffn_inner_dim, operations, dtype=dtype, device=device),
            nn.Dropout(0.0),
            operations.Linear(ffn_inner_dim, dim, bias=True, dtype=dtype, device=device),
        )

    def forward(self, x: Tensor) -> Tensor:
        return self.ff(x)


class MiniMaxMusic3Block(nn.Module):
    """Pre-norm self-attention + GLU feed-forward, both residual."""

    def __init__(self, config: MiniMaxMusic3DitConfig, operations, dtype=None, device=None) -> None:
        super().__init__()
        dim = config.inner_dim
        self.pre_norm = MiniMaxMusic3LayerNorm(dim, config.norm_eps, dtype=dtype, device=device)
        self.self_attn = MiniMaxMusic3Attention(dim, config.num_attention_heads, config.attention_head_dim,
                                                 config.rotary_dim, operations, dtype=dtype, device=device)
        self.ff_norm = MiniMaxMusic3LayerNorm(dim, config.norm_eps, dtype=dtype, device=device)
        self.ff = MiniMaxMusic3FeedForward(dim, config.ffn_inner_dim, operations, dtype=dtype, device=device)

    def forward(self, x: Tensor, rotary_emb: tuple[Tensor, Tensor]) -> Tensor:
        x = x + self.self_attn(self.pre_norm(x), rotary_emb)
        x = x + self.ff(self.ff_norm(x))
        return x


class _MiniMaxMusic3Transformer(nn.Module):
    """The ``diffusion_transformer.transformer.*`` submodule: project_in/out,
    rotary table, and the block stack. A plain container, not a
    ``NativeArchModule`` itself — ``MiniMaxMusic3Model`` owns the load contract."""

    def __init__(self, config: MiniMaxMusic3DitConfig, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.project_in = operations.Linear(config.concat_channels, config.inner_dim, bias=False,
                                             dtype=dtype, device=device)
        self.project_out = operations.Linear(config.inner_dim, config.in_channels, bias=False,
                                              dtype=dtype, device=device)
        self.rotary_pos_emb = MiniMaxMusic3RotaryPosEmb(config.rotary_dim, dtype=dtype, device=device)
        self.layers = nn.ModuleList([
            MiniMaxMusic3Block(config, operations, dtype=dtype, device=device)
            for _ in range(config.num_layers)
        ])

    def forward(self, hidden_states: Tensor, temb: Tensor) -> Tensor:
        hidden_states = self.project_in(hidden_states)
        # The timestep embedding is prepended as one extra token, dropped after the
        # block stack — the checkpoint's own contract, not an engine convention.
        hidden_states = torch.cat((temb.unsqueeze(1), hidden_states), dim=1)
        rotary_emb = self.rotary_pos_emb(hidden_states.shape[1], hidden_states.device)
        for layer in self.layers:
            hidden_states = layer(hidden_states, rotary_emb)
        return self.project_out(hidden_states[:, 1:])


class _MiniMaxMusic3Dit(nn.Module):
    """The ``diffusion_transformer.*`` submodule: timestep embedding, the
    residual pre/post 1x1 convs, and the inner transformer."""

    def __init__(self, config: MiniMaxMusic3DitConfig, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.config = config
        self.preprocess_conv = operations.Conv1d(config.concat_channels, config.concat_channels, 1,
                                                   bias=False, dtype=dtype, device=device)
        self.postprocess_conv = operations.Conv1d(config.in_channels, config.in_channels, 1,
                                                    bias=False, dtype=dtype, device=device)
        self.timestep_features = MiniMaxMusic3FourierEmbedding(config.fourier_dim, dtype=dtype, device=device)
        self.to_timestep_embed = nn.Sequential(
            operations.Linear(config.fourier_dim, config.inner_dim, bias=True, dtype=dtype, device=device),
            nn.SiLU(),
            operations.Linear(config.inner_dim, config.inner_dim, bias=True, dtype=dtype, device=device),
        )
        self.transformer = _MiniMaxMusic3Transformer(config, operations, dtype=dtype, device=device)

    def forward(self, hidden_states: Tensor, timestep: Tensor, encoder_hidden_states: Tensor) -> Tensor:
        zeros = torch.zeros_like(hidden_states)
        hidden_states = torch.cat((hidden_states, zeros, encoder_hidden_states.transpose(1, 2)), dim=1)
        hidden_states = self.preprocess_conv(_cast_to(hidden_states, self.preprocess_conv)) + hidden_states
        hidden_states = hidden_states.transpose(1, 2)

        fourier = self.timestep_features(timestep)
        temb = self.to_timestep_embed(_cast_to(fourier, self.to_timestep_embed[0]))

        hidden_states = self.transformer(hidden_states, temb)
        hidden_states = hidden_states.transpose(1, 2)
        return self.postprocess_conv(_cast_to(hidden_states, self.postprocess_conv)) + hidden_states


class MiniMaxMusic3Model(NativeArchModule):
    """The whole flow-matching file: condition encoder (top-level keys, no
    prefix) + DiT (``diffusion_transformer.*``), one placement unit."""

    def __init__(self, config: MiniMaxMusic3DitConfig, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.config = config
        self.cond_layer_logits = nn.Parameter(
            torch.empty(config.num_condition_layers, dtype=dtype, device=device)
        )
        self.cond_layer_scale = nn.Parameter(torch.empty(1, dtype=dtype, device=device))
        self.latent_conditioners = nn.ModuleList([
            operations.Conv1d(config.condition_hidden_dim, config.condition_dim, 3, padding=1,
                               bias=True, dtype=dtype, device=device)
        ])
        self.diffusion_transformer = _MiniMaxMusic3Dit(config, operations, dtype=dtype, device=device)

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "MiniMaxMusic3Model":
        return cls(MiniMaxMusic3DitConfig.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        """Recompute ``rotary_pos_emb.inv_freq`` in fp32 (standing rotary-buffer
        rule — the checkpoint's own copy is not trusted). Nothing else is
        derived: the condition-encoder parameters and every block weight are
        loaded checkpoint values, not computed state."""
        device = self.cond_layer_logits.device
        rotary_dim = self.config.rotary_dim
        theta = self.config.rope_theta
        inv_freq = 1.0 / (theta ** (torch.arange(0, rotary_dim, 2, dtype=torch.float32, device=device) / rotary_dim))
        self.diffusion_transformer.transformer.rotary_pos_emb.inv_freq = inv_freq

    # -- condition encoder ----------------------------------------------------

    def encode_condition(self, frame_hiddens: Tensor) -> Tensor:
        """Per-frame AR hidden states -> latent-timeline conditioning.

        ``frame_hiddens``: ``(B, F, num_condition_layers * condition_hidden_dim)``.
        Returns ``(B, latent_length(F), condition_dim)``.
        """
        batch, num_frames, _ = frame_hiddens.shape
        num_layers = self.config.num_condition_layers
        h = frame_hiddens.transpose(1, 2)
        h = h.reshape(batch, num_layers, self.config.condition_hidden_dim, num_frames)
        weights = torch.softmax(self.cond_layer_logits.float(), dim=0).to(h.dtype)
        h = torch.einsum("blht,l->bht", h, weights)
        h = self.cond_layer_scale.to(h.dtype) * h
        conv = self.latent_conditioners[0]
        h = conv(_cast_to(h, conv))
        h = F.interpolate(h, size=latent_length(num_frames, self.config.latents_per_frame), mode="nearest")
        return h.transpose(1, 2)

    # -- DiT forward ------------------------------------------------------------

    def forward(self, hidden_states: Tensor, timestep: Tensor, encoder_hidden_states: Tensor) -> Tensor:
        """Predict flow-matching velocity.

        ``hidden_states``: ``(B, in_channels, T)`` noisy latents. ``timestep``:
        ``(B,)`` flow time in ``[0, 1]``, 0 = noise. ``encoder_hidden_states``:
        ``(B, T, condition_dim)``, latent-aligned conditioning from
        ``encode_condition`` — pass zeros for the unconditional CFG branch.
        """
        return self.diffusion_transformer(hidden_states, timestep, encoder_hidden_states)
