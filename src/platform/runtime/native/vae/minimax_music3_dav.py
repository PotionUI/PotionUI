# Derived from: diffusers `minimax_music3_vocoder.py` (Apache-2.0, "Copyright
# 2026 The MiniMax Team and The HuggingFace Team") for the DAC-decoder math
# (Snake1d, residual units, upsample blocks, stereo fold/unfold), and
# ComfyUI's `model_detection.py` / the real Comfy-Org repack header
# (`ai/minimax_music3/minimax_music3_dav_header.json`, 121 keys, all F32) for
# the module's own key layout -- see "Repack key layout" below, which is a
# flat `nn.Sequential`-of-`nn.Sequential` shape diffusers' own
# attribute-named reference does NOT use.
"""MiniMax-Music3 DAV vocoder: a DAC-style, decode-only waveform decoder
(flow-matched latents in, stereo waveform out -- no encoder shipped).

**Repack key layout (write against this, not diffusers' attribute names).**
The real repack flattens everything into indexed `nn.Sequential` nesting:
`decoder.model.{0..6}` = conv_in -> 4 upsample blocks -> Snake1d -> conv_out;
each upsample block's `.block.{0..4}` = Snake1d -> ConvTranspose1d ->
3 residual units (dil 1/3/9); each residual unit's `.block.{0..3}` =
Snake1d -> Conv1d(k7) -> Snake1d -> Conv1d(k1). This module is built to that
exact shape (`_DAVResidualUnit`/`_DAVUpsampleBlock`/`_DAVDecoderStack` each
wrap their children in a `.block`/`.model` `nn.Sequential`) so a strict
load matches the checkpoint's actual attribute path, not a renamed one.

**`weight_g`/`weight_v` -- folded at load, NOT the H3 audio discrepancy.**
Unlike `minimax_h3_audio.py` (whose Comfy-Org repack ships
`remove_weight_norm()`-collapsed plain `weight` tensors), THIS repack keeps
the two-parameter `torch.nn.utils.weight_norm` spelling verbatim (verified:
121/121 header keys, every conv carries `weight_g`+`weight_v`, zero plain
`weight` keys among them -- only `dec_in_proj`, which the reference never
wraps in `weight_norm`, has a plain `weight`). `fold_weight_norm_conv` (this
module) computes what `remove_weight_norm()` computes --
`weight = weight_g * weight_v / ||weight_v||_{dim=(1,2)}` -- ONCE at load
time, in the vae-loader remap (`vae/loader.py`'s `load_minimax_music3_dav`),
before the state dict ever reaches `load_into_module`. The module itself
therefore has no weight_norm parametrization anywhere, matching the H3
sibling's plain-conv convention even though the two repacks arrived at that
shape from opposite directions. The fold is dim-agnostic: `weight_g`'s shape
mirrors `weight_v`'s dim-0 for BOTH `Conv1d` (dim 0 = out_channels) and
`ConvTranspose1d` (dim 0 = in_channels, per `torch.nn.utils.weight_norm`'s
default `dim=0`) -- reducing over dims (1, 2) and keeping dim 0 is correct
for every conv in this file without needing to special-case the transpose.

**fp32 compute ALWAYS** -- same DAC lineage and the same rule as
`minimax_h3_audio.py`'s "Precision" note (bf16 measurably degrades this kind
of decoder); the repack ships F32 already. `decode()` forces the input to
fp32 at its own entry point so every downstream `operations`-built layer's
`cast_bias_weight` (activation-drives-weight-cast, per the H3 sibling) keeps
the whole chain in fp32 with no per-submodule casting needed.

**Upsampling is 8*8*4*2 = 512x** (not 1024x -- an early research-note
arithmetic slip corrected in the port plan), so `hop_length = 512` and, at
44.1 kHz output, ~86.13 latent frames/second.

**Stereo fold is a straight channel-major reshape, not an interleave**:
`[B, 128, T] -> view [B*2, 64, T]` (first 64 channels of a batch item become
one decode item, the last 64 the other) -> decode -> `[B*2, 1, T*512]` ->
`view [B, 2, T*512]`. Both reshapes require a contiguous tensor; `decode()`
calls `.contiguous()` before each to make that safe regardless of how the
caller produced `latents`.

**Chunked (tiled) decode** -- a 6-minute song is tens of thousands of
latents; late-decoder activations at high channel counts get expensive.
`decode()` tiles along the latent axis in latent-domain windows with a
context overlap that is decoded and then DISCARDED (not cross-faded --
deterministic, and simpler than a per-sample blend), matching the port
plan's explicit choice over ComfyUI's admitted "small risk of seams"
overlap-add. Tiling is ON by default above `tile_latents` latents; set
`use_tiling = False` to always decode whole (what the equality test below
compares against).
"""

from __future__ import annotations

import math
from typing import Any

import torch
import torch.nn as nn

from ..base import NativeArchModule
from ..errors import NativeEngineUnsupportedError
from .minimax_h3_audio import _Snake1d

# -- fixed DAV geometry (single released variant, matches the header 1:1) ---

LATENT_CHANNELS = 128           # flow-matching latent channels (folds to 64/stream)
DECODER_INPUT_DIM = 1024
DECODER_HIDDEN_DIM = 1536
UPSAMPLING_RATIOS: tuple[int, ...] = (8, 8, 4, 2)
SAMPLE_RATE = 44100
HOP_LENGTH = math.prod(UPSAMPLING_RATIOS)  # 512 -> 86.13 latents/s at 44.1kHz

# Tiled-decode defaults (the official ComfyUI template's tile/overlap for this
# checkpoint) -- latent-domain units, see module docstring "Chunked decode".
DEFAULT_TILE_LATENTS = 1536
DEFAULT_TILE_OVERLAP_LATENTS = 64


class _DAVResidualUnit(nn.Module):
    """`.block` = Snake1d -> Conv1d(k7, dilated, same-padding) -> Snake1d ->
    Conv1d(k1). Same-padding conv preserves length exactly (no cropping,
    unlike the H3 audio sibling's residual unit -- the reference's own
    `forward` adds the residual straight back with no length check)."""

    def __init__(self, dim: int, dilation: int, *, operations: Any) -> None:
        super().__init__()
        pad = (7 - 1) * dilation // 2
        self.block = nn.Sequential(
            _Snake1d(dim),
            operations.Conv1d(dim, dim, kernel_size=7, dilation=dilation, padding=pad),
            _Snake1d(dim),
            operations.Conv1d(dim, dim, kernel_size=1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.block(x)


class _DAVUpsampleBlock(nn.Module):
    """`.block` = Snake1d -> ConvTranspose1d(k=2*stride, pad=ceil(stride/2))
    -> 3 residual units (dilation 1, 3, 9). The transpose padding exactly
    cancels the kernel/stride overshoot (`kernel = 2*stride`), so output
    length is exactly `input_length * stride` -- no cropping anywhere in this
    decoder, unlike the H3 audio sibling's replicate-pad upsampler."""

    def __init__(self, input_dim: int, output_dim: int, stride: int, *, operations: Any) -> None:
        super().__init__()
        self.block = nn.Sequential(
            _Snake1d(input_dim),
            operations.ConvTranspose1d(
                input_dim, output_dim, kernel_size=2 * stride, stride=stride, padding=math.ceil(stride / 2)
            ),
            _DAVResidualUnit(output_dim, dilation=1, operations=operations),
            _DAVResidualUnit(output_dim, dilation=3, operations=operations),
            _DAVResidualUnit(output_dim, dilation=9, operations=operations),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


class _DAVDecoderStack(nn.Module):
    """`.model` = conv_in(k7) -> N upsample blocks -> Snake1d -> conv_out(k7,
    bias). Flat `nn.Sequential` indexing matches the checkpoint's
    `decoder.model.{0..6}` naming 1:1."""

    def __init__(
        self, *, decoder_input_dim: int, decoder_hidden_dim: int, upsampling_ratios: tuple[int, ...], operations: Any,
    ) -> None:
        super().__init__()
        layers: list[nn.Module] = [
            operations.Conv1d(decoder_input_dim, decoder_hidden_dim, kernel_size=7, padding=3)
        ]
        dim = decoder_hidden_dim
        for stride in upsampling_ratios:
            out_dim = dim // 2
            layers.append(_DAVUpsampleBlock(dim, out_dim, stride, operations=operations))
            dim = out_dim
        layers.append(_Snake1d(dim))
        layers.append(operations.Conv1d(dim, 1, kernel_size=7, padding=3))
        self.model = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.model(x)


class MiniMaxMusic3DAV(NativeArchModule):
    def __init__(
        self, *,
        latent_channels: int = LATENT_CHANNELS,
        decoder_input_dim: int = DECODER_INPUT_DIM,
        decoder_hidden_dim: int = DECODER_HIDDEN_DIM,
        upsampling_ratios: tuple[int, ...] = UPSAMPLING_RATIOS,
        sample_rate: int = SAMPLE_RATE,
        tile_latents: int = DEFAULT_TILE_LATENTS,
        tile_overlap_latents: int = DEFAULT_TILE_OVERLAP_LATENTS,
        operations: Any,
    ) -> None:
        super().__init__()
        upsampling_ratios = tuple(int(r) for r in upsampling_ratios)
        if latent_channels % 2 != 0:
            raise NativeEngineUnsupportedError(
                f"MiniMax-Music3 DAV: latent_channels must be even for the stereo fold, got {latent_channels}"
            )
        self.latent_channels = latent_channels
        self.hop_length = math.prod(upsampling_ratios)
        self.sample_rate = sample_rate
        self.use_tiling = True
        self.tile_latents = tile_latents
        self.tile_overlap_latents = tile_overlap_latents

        self.dec_in_proj = operations.Conv1d(latent_channels // 2, decoder_input_dim, kernel_size=1)
        self.decoder = _DAVDecoderStack(
            decoder_input_dim=decoder_input_dim, decoder_hidden_dim=decoder_hidden_dim,
            upsampling_ratios=upsampling_ratios, operations=operations,
        )

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "MiniMaxMusic3DAV":
        return cls(
            latent_channels=config.get("latent_channels", LATENT_CHANNELS),
            decoder_input_dim=config.get("decoder_input_dim", DECODER_INPUT_DIM),
            decoder_hidden_dim=config.get("decoder_hidden_dim", DECODER_HIDDEN_DIM),
            upsampling_ratios=tuple(config.get("upsampling_ratios", UPSAMPLING_RATIOS)),
            sample_rate=config.get("sample_rate", SAMPLE_RATE),
            tile_latents=config.get("tile_latents", DEFAULT_TILE_LATENTS),
            tile_overlap_latents=config.get("tile_overlap_latents", DEFAULT_TILE_OVERLAP_LATENTS),
            operations=operations,
        )

    def post_load(self) -> None:
        # No computed/derived buffer in this module (Snake1d's `alpha` and
        # every conv weight are real checkpoint tensors) -- nothing to
        # recompute after assign-load.
        return None

    def _decode_core(self, latents: torch.Tensor) -> torch.Tensor:
        """`[B, latent_channels, T] -> [B, 2, T * hop_length]`, no tiling."""
        batch = latents.shape[0]
        folded = latents.contiguous().view(batch * 2, self.latent_channels // 2, latents.shape[-1])
        hidden = self.dec_in_proj(folded)
        waveform = torch.tanh(self.decoder(hidden))
        return waveform.contiguous().view(batch, 2, -1)

    def decode(self, latents: torch.Tensor) -> torch.Tensor:
        """Flow-matched latents `(B, latent_channels, T)` -> stereo waveform
        `(B, 2, T * hop_length)` in `[-1, 1]`. Tiles along `T` when
        `use_tiling` and `T > tile_latents` (see module docstring "Chunked
        decode"); identical latents decode to the same output whole or
        tiled, away from the discarded-overlap boundaries."""
        if latents.ndim != 3 or latents.shape[1] != self.latent_channels:
            raise NativeEngineUnsupportedError(
                f"MiniMax-Music3 DAV: expected [batch, {self.latent_channels}, length], got {tuple(latents.shape)}"
            )
        latents = latents.float()  # fp32 always -- see module docstring.

        total = latents.shape[-1]
        if not self.use_tiling or total <= self.tile_latents:
            return self._decode_core(latents)

        overlap = self.tile_overlap_latents
        chunks: list[torch.Tensor] = []
        start = 0
        while start < total:
            end = min(start + self.tile_latents, total)
            ctx_start = max(0, start - overlap)
            ctx_end = min(total, end + overlap)
            waveform = self._decode_core(latents[..., ctx_start:ctx_end])
            left_trim = (start - ctx_start) * self.hop_length
            right_trim = (ctx_end - end) * self.hop_length
            kept = waveform[..., left_trim: waveform.shape[-1] - right_trim if right_trim else None]
            chunks.append(kept)
            start = end
        return torch.cat(chunks, dim=-1)


def fold_weight_norm_conv(sd: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    """Fold every `<x>.weight_g` / `<x>.weight_v` pair in ``sd`` into a plain
    `<x>.weight` tensor -- exactly what `torch.nn.utils.parametrize.remove_weight_norm`
    computes, done once here so the module above never carries a weight_norm
    parametrization (see module docstring). Keys with no `_g`/`_v` pair
    (``dec_in_proj.*``) pass through unchanged.
    """
    bases = {key[: -len(".weight_v")] for key in sd if key.endswith(".weight_v")}
    out: dict[str, torch.Tensor] = {
        key: tensor for key, tensor in sd.items() if not key.endswith((".weight_v", ".weight_g"))
    }
    for base in bases:
        g = sd[base + ".weight_g"]
        v = sd[base + ".weight_v"]
        norm = v.norm(dim=(1, 2), keepdim=True)
        out[base + ".weight"] = g * v / norm
    return out
