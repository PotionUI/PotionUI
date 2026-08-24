# Derived from: diffusers `src/diffusers/pipelines/ltx2/duration_head.py`
# (Apache-2.0) for the module and `predict_num_frames`, and
# `scripts/convert_ltx2_to_diffusers.py:632-673` (Apache-2.0) for the
# fused-qkv split and the shape-derived config.

"""LTX-2.5 duration head — predicts a shot's natural length from the prompt.

The head consumes the **prompt encoder's connector token outputs** (the same
per-token hidden states the DiT cross-attends to), not any latent: an
``LTXAVModel``'s ``video_embeddings_connector`` output (width 4096 on the 22B
checkpoint) and/or its ``audio_embeddings_connector`` output (width 2048).
Modality-specific input projections map each stream into a shared 256-wide
pooler space, a learnable per-modality embedding tags each stream, a
one-query cross-attention pooler collapses the concatenation to a fixed
vector, and a 2-layer MLP regresses a **log**-duration in seconds
(:meth:`LTXDurationHead.forward` exponentiates, so callers always get
seconds).

:meth:`LTXDurationHead.predict_num_frames` is the whole consumer-facing API:
seconds -> clamp -> the causal VAE's ``k * temporal_compression_ratio + 1``
frame grid. Nothing in this engine calls it yet — the model loads, the
prediction is available, but no generator pipe consults it (that wiring is a
design decision, not a port decision).

**Checkpoint layout.** The head ships as its own small file
(``ltx-2.5-duration-head-bf16.safetensors``, published under the LTX-2.5
repository's ``model_patches/`` directory) with its weights under a
``duration_head.`` prefix, and no hyperparameters in metadata. Two
consequences, both handled by :func:`convert_duration_head_state_dict` and
``detect_ltx_duration_head_config``:

* the config is **derived from weight shapes**; ``num_pooler_heads`` is the
  one value shapes cannot recover (``to_q`` is square regardless) and is
  fixed at the trained value, 4;
* the pooler's projections are stored **fused**, in
  ``torch.nn.MultiheadAttention`` layout (``attention_pooler.cross_attn.
  in_proj_weight`` / ``in_proj_bias``, ``...out_proj.*``). They are split into
  separate q/k/v here, because every parameterised layer in this engine is
  built through the ``operations`` seam and ``nn.MultiheadAttention`` is not
  part of it. That is the same split diffusers' converter performs.

No local checkpoint ships this model yet, so the real-header parity test
skips until the file appears; everything else is exercised against
synthetic tiny configs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ...base import NativeArchModule, load_into_module
from ...detect.vae_detect import detect_ltx_duration_head_config
from ...errors import NativeEngineUnsupportedError
from ...io.safetensors_loader import load_torch_file
from ...io.state_dict_utils import strip_prefix

logger = logging.getLogger(__name__)

DURATION_HEAD_PREFIX = "duration_head."

# `attention_pooler.cross_attn` is `nn.MultiheadAttention`; these are its own
# parameter names, not ours.
_FUSED_QKV_WEIGHT = "attention_pooler.cross_attn.in_proj_weight"
_FUSED_QKV_BIAS = "attention_pooler.cross_attn.in_proj_bias"
_FUSED_OUT_WEIGHT = "attention_pooler.cross_attn.out_proj.weight"
_FUSED_OUT_BIAS = "attention_pooler.cross_attn.out_proj.bias"

# The key that says "this state dict is a duration head", in our (split) layout.
SIGNATURE_KEY = "attention_pooler.query_tokens"


class _DurationAttentionPooler(nn.Module):
    """Cross-attends ``num_queries`` learnable tokens against the caption
    tokens, producing a fixed ``(batch, num_queries, hidden_dim)`` output
    whatever the input sequence length.

    No attention mask: the text connectors substitute learnable registers for
    padded positions and mark the result fully attendable, so every token
    reaching this module is already valid.
    """

    def __init__(self, *, hidden_dim: int, num_queries: int, num_heads: int, operations: Any) -> None:
        super().__init__()
        if hidden_dim % num_heads != 0:
            raise NativeEngineUnsupportedError(
                f"LTX duration head: pooler_hidden_dim {hidden_dim} is not divisible by "
                f"num_pooler_heads {num_heads}"
            )
        self.heads = num_heads
        self.query_tokens = nn.Parameter(torch.empty(num_queries, hidden_dim))
        self.to_q = operations.Linear(hidden_dim, hidden_dim)
        self.to_k = operations.Linear(hidden_dim, hidden_dim)
        self.to_v = operations.Linear(hidden_dim, hidden_dim)
        self.to_out = operations.Linear(hidden_dim, hidden_dim)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        queries = self.query_tokens.unsqueeze(0).expand(tokens.shape[0], -1, -1)
        query = self.to_q(queries).unflatten(2, (self.heads, -1)).transpose(1, 2)
        key = self.to_k(tokens).unflatten(2, (self.heads, -1)).transpose(1, 2)
        value = self.to_v(tokens).unflatten(2, (self.heads, -1)).transpose(1, 2)
        hidden = F.scaled_dot_product_attention(query, key, value)
        return self.to_out(hidden.transpose(1, 2).flatten(2, 3))


class LTXDurationHead(NativeArchModule):
    """Duration regressor over LTX-2.5 connector tokens (see module docstring)."""

    def __init__(
        self,
        *,
        video_cross_attention_dim: int = 4096,
        audio_cross_attention_dim: int = 2048,
        pooler_hidden_dim: int = 256,
        num_queries: int = 1,
        num_pooler_heads: int = 4,
        mlp_hidden_dim: int = 256,
        operations: Any,
    ) -> None:
        super().__init__()
        self.video_cross_attention_dim = video_cross_attention_dim
        self.audio_cross_attention_dim = audio_cross_attention_dim
        self.pooler_hidden_dim = pooler_hidden_dim
        self.num_queries = num_queries
        self.num_pooler_heads = num_pooler_heads
        self.mlp_hidden_dim = mlp_hidden_dim

        self.video_input_proj = operations.Linear(video_cross_attention_dim, pooler_hidden_dim)
        self.video_modality_emb = nn.Parameter(torch.empty(pooler_hidden_dim))
        self.audio_input_proj = operations.Linear(audio_cross_attention_dim, pooler_hidden_dim)
        self.audio_modality_emb = nn.Parameter(torch.empty(pooler_hidden_dim))

        self.attention_pooler = _DurationAttentionPooler(
            hidden_dim=pooler_hidden_dim,
            num_queries=num_queries,
            num_heads=num_pooler_heads,
            operations=operations,
        )
        self.mlp_hidden = operations.Linear(pooler_hidden_dim * num_queries, mlp_hidden_dim)
        self.mlp_out = operations.Linear(mlp_hidden_dim, 1)

    @classmethod
    def from_config(cls, config: dict, operations: Any) -> "LTXDurationHead":
        return cls(
            video_cross_attention_dim=config.get("video_cross_attention_dim", 4096),
            audio_cross_attention_dim=config.get("audio_cross_attention_dim", 2048),
            pooler_hidden_dim=config.get("pooler_hidden_dim", 256),
            num_queries=config.get("num_queries", 1),
            num_pooler_heads=config.get("num_pooler_heads", 4),
            mlp_hidden_dim=config.get("mlp_hidden_dim", 256),
            operations=operations,
        )

    def post_load(self) -> None:
        return None

    @property
    def dtype(self) -> torch.dtype:
        """Compute dtype for the connector tokens handed to :meth:`forward`.

        A weight's dtype is not generally the compute dtype in this engine
        (fp8/nvfp4 storage), but this head is a few-MB bf16 file that no
        quantiser targets, so its stored dtype IS what the maths runs in.
        """
        return self.mlp_out.weight.dtype

    def forward(
        self,
        video_tokens: torch.Tensor | None = None,
        audio_tokens: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """``(batch,)`` predicted durations in **seconds**.

        ``video_tokens`` / ``audio_tokens`` are ``(batch, seq_len, dim)``
        connector outputs; at least one is required.
        """
        if video_tokens is None and audio_tokens is None:
            raise ValueError("LTXDurationHead requires at least one of video_tokens / audio_tokens")

        head_dtype = self.dtype
        groups = []
        if video_tokens is not None:
            groups.append(self.video_input_proj(video_tokens.to(head_dtype)) + self.video_modality_emb)
        if audio_tokens is not None:
            groups.append(self.audio_input_proj(audio_tokens.to(head_dtype)) + self.audio_modality_emb)

        pooled = self.attention_pooler(torch.cat(groups, dim=1)).flatten(1)
        # tanh-approximate GELU: the head was trained in JAX, and the exact
        # GELU gives different numbers.
        hidden = F.gelu(self.mlp_hidden(pooled), approximate="tanh")
        return self.mlp_out(hidden).squeeze(-1).exp()

    def predict_num_frames(
        self,
        video_tokens: torch.Tensor | None = None,
        audio_tokens: torch.Tensor | None = None,
        *,
        frame_rate: float,
        temporal_compression_ratio: int,
        min_seconds: float = 1.0,
        max_seconds: float = 20.0,
    ) -> int:
        """A frame count on the VAE's causal temporal grid
        (``k * temporal_compression_ratio + 1``) for one prompt.

        Clamp first, snap second: a clamped frame count is not necessarily
        grid-aligned, so snapping first gives a different answer. Snapping
        floors, so it can land below the minimum — that case is snapped *up*
        to the next grid point instead, keeping the clamp contract.

        Narrow bounds can convert to a frame window holding no grid point at
        all (at 24 fps ``[1.0s, 1.02s]`` rounds to ``[24, 24]``, and 24 is not
        ``8k + 1``). The nearest grid point wins and a warning is logged:
        overshooting by under one grid step beats refusing to generate, so the
        result is always on the grid but may fall just outside the bounds.
        """
        predicted = self(video_tokens, audio_tokens)
        if predicted.numel() != 1:
            raise ValueError(
                "LTXDurationHead.predict_num_frames supports a single prediction only, but got a "
                f"prediction of shape {tuple(predicted.shape)} -- one frame count cannot serve prompts "
                "with different natural durations; predict for one prompt at a time"
            )
        seconds = float(predicted.item())

        # Floored at 1 so the grid arithmetic below cannot go negative.
        min_frames = max(1, round(min_seconds * frame_rate))
        max_frames = round(max_seconds * frame_rate)
        clamped = max(min_frames, min(round(seconds * frame_rate), max_frames))

        num_frames = ((clamped - 1) // temporal_compression_ratio) * temporal_compression_ratio + 1
        if num_frames < min_frames:
            snapped_up = num_frames + temporal_compression_ratio
            if snapped_up <= max_frames:
                num_frames = snapped_up
            else:
                if abs(snapped_up - clamped) < abs(num_frames - clamped):
                    num_frames = snapped_up
                logger.warning(
                    "duration bounds [%.2fs, %.2fs] at %.2f fps admit no frame count on the VAE's "
                    "temporal grid (k * %d + 1); using the nearest: %d frames (%.2fs)",
                    min_seconds, max_seconds, frame_rate, temporal_compression_ratio,
                    num_frames, num_frames / frame_rate,
                )

        if seconds < min_seconds or seconds > max_seconds:
            logger.warning(
                "duration prediction clamped: raw %.2fs outside [%.2fs, %.2fs], using %.2fs (%d frames) @ %.2f fps",
                seconds, min_seconds, max_seconds, num_frames / frame_rate, num_frames, frame_rate,
            )
        else:
            logger.info(
                "predicted duration %.2fs (%d frames @ %.2f fps)", seconds, num_frames, frame_rate
            )
        return num_frames


def convert_duration_head_state_dict(sd: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    """Checkpoint keys -> :class:`LTXDurationHead` keys.

    Strips the ``duration_head.`` prefix when the file carries one (a
    standalone head file may or may not; a monolithic checkpoint always does),
    then splits the fused ``nn.MultiheadAttention`` projections into the
    separate q/k/v this module builds. A state dict that is already in split
    layout passes through unchanged, so the conversion is idempotent.
    """
    if any(k.startswith(DURATION_HEAD_PREFIX) for k in sd):
        sd = strip_prefix(sd, DURATION_HEAD_PREFIX)
    else:
        sd = dict(sd)

    if _FUSED_QKV_WEIGHT not in sd:
        return sd

    fused_weight = sd.pop(_FUSED_QKV_WEIGHT)
    fused_bias = sd.pop(_FUSED_QKV_BIAS)
    q_w, k_w, v_w = fused_weight.chunk(3, dim=0)
    q_b, k_b, v_b = fused_bias.chunk(3, dim=0)
    sd["attention_pooler.to_q.weight"] = q_w
    sd["attention_pooler.to_k.weight"] = k_w
    sd["attention_pooler.to_v.weight"] = v_w
    sd["attention_pooler.to_q.bias"] = q_b
    sd["attention_pooler.to_k.bias"] = k_b
    sd["attention_pooler.to_v.bias"] = v_b
    sd["attention_pooler.to_out.weight"] = sd.pop(_FUSED_OUT_WEIGHT)
    sd["attention_pooler.to_out.bias"] = sd.pop(_FUSED_OUT_BIAS)
    return sd


class _DurationHeadSpec:
    family = "ltx"
    variant = "duration_head"

    def key_is_expected_missing(self, key: str) -> bool:
        return False

    def key_is_expected_unexpected(self, key: str) -> bool:
        return False


def load_ltx_duration_head(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
    sd: dict[str, torch.Tensor] | None = None,
    metadata: dict[str, str] | None = None,
) -> LTXDurationHead:
    """Load the standalone ``ltx-2.5-duration-head-bf16.safetensors`` (or the
    ``duration_head.*`` slice of a monolithic checkpoint) into an
    :class:`LTXDurationHead`.

    ``sd``/``metadata`` let a caller that already read the file pass them
    through instead of paying for a second read.
    """
    path = Path(path)
    if sd is None:
        sd, metadata = load_torch_file(path, device=device)

    config = detect_ltx_duration_head_config(sd, metadata or {})
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' carries no LTX duration head "
            f"(no '{SIGNATURE_KEY}' key, with or without the '{DURATION_HEAD_PREFIX}' prefix)"
        )

    module = LTXDurationHead.from_config(config, operations)
    load_into_module(module, convert_duration_head_state_dict(sd), _DurationHeadSpec())
    logger.debug(
        "loaded LTX duration head from %s (video_dim=%d, audio_dim=%d, pooler=%d x %d queries)",
        path.name, module.video_cross_attention_dim, module.audio_cross_attention_dim,
        module.pooler_hidden_dim, module.num_queries,
    )
    return module
