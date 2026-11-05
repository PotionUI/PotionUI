from __future__ import annotations

import logging
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import torch
import torch.nn as nn
from torch import Tensor

from ...base import NativeArchModule
from ...cancellation import raise_if_cancelled
from ...errors import NativeEngineUnsupportedError
from .config import TEXT_BLOCKS, MossImageConfig
from .layers import Block, RMSNorm, TextProjection, rms_args, rope_bands, rope_table

logger = logging.getLogger(__name__)

TIMESTEP_SCALE = 1000.0
_DROPPED_SUFFIXES = (".comfy_attention.config",)
_DROPPED_KEYS = frozenset({"rope.bands"})
_QKV_PARTS = ("q", "k", "v")
_FLOAT_DTYPES = (torch.bfloat16, torch.float16, torch.float32)


def convert_moss_image_state_dict(sd: dict[str, Tensor]) -> dict[str, Tensor]:
    out = {
        key: value
        for key, value in sd.items()
        if key not in _DROPPED_KEYS and not key.endswith(_DROPPED_SUFFIXES)
    }
    if len(out) != len(sd):
        logger.info("moss_image: ignoring rope.bands and comfy_attention hints, attention uses the configured backend")
    suffix = ".proj.q.weight"
    for prefix in sorted(key[: -len(suffix)] for key in out if key.endswith(suffix)):
        parts = [out.pop(f"{prefix}.proj.{name}.weight") for name in _QKV_PARTS]
        if any(part.dtype not in _FLOAT_DTYPES for part in parts):
            raise NativeEngineUnsupportedError(
                "MOSS-Image checkpoints with separate quantised q/k/v projections are not supported: "
                "use the fused-qkv bf16 or int8 ConvRot DiT"
            )
        out[f"{prefix}.proj.qkv.weight"] = torch.cat(parts, dim=0)
    return out


def _sinusoid(timestep: Tensor, batch: int, freq_dim: int) -> Tensor:
    times = timestep.reshape(-1).to(torch.float32) * TIMESTEP_SCALE
    times = times.expand(batch)
    half = freq_dim // 2
    freq = 10000 ** (-torch.arange(half, device=times.device, dtype=torch.float64) / half)
    phase = times.double()[:, None] * freq
    return torch.cat((phase.cos(), phase.sin()), -1).float()


class MossImage(NativeArchModule):
    def __init__(self, cfg: MossImageConfig, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.operations = operations
        self.patch_size = 1
        self.params = SimpleNamespace(in_channels=cfg.in_channels)
        kw = {"dtype": dtype, "device": device}
        dim = cfg.dim
        self.video_embedder = operations.Linear(cfg.in_channels, dim, **kw)
        self.text_embedder = TextProjection(cfg.text_dim, dim, cfg.ffn_dim, cfg.heads, TEXT_BLOCKS, operations, **kw)
        self.time_embedding = nn.Sequential(
            operations.Linear(cfg.freq_dim, dim, **kw),
            nn.SiLU(),
            operations.Linear(dim, dim, **kw),
        )
        self.shared_block_modulation = operations.Linear(dim, 6 * dim, **kw)
        self.layers = nn.ModuleList(
            [Block(dim, cfg.ffn_dim, cfg.heads, operations, **kw) for _ in range(cfg.depth)]
        )
        self.final_norm_video = RMSNorm(dim, **kw)
        self.final_linear_video = operations.Linear(dim, cfg.in_channels, bias=False, **kw)
        self.video_head_modulation = operations.Linear(dim, 2 * dim, **kw)
        self._bands = rope_bands(cfg.head_dim, cfg.rope_base)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "MossImage":
        return cls(MossImageConfig.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        return None

    def _skip_set(self, skip_layers: Iterable[int] | None) -> frozenset[int]:
        skip = frozenset(skip_layers or ())
        if any(type(i) is not int or not 0 <= i < self.cfg.depth for i in skip):
            raise ValueError(f"skip_layers must hold block indices in [0, {self.cfg.depth})")
        return skip

    def forward(
        self,
        x: Tensor,
        timestep: Tensor,
        context: Tensor,
        attention_mask: Tensor | None = None,
        skip_layers: Iterable[int] | None = None,
        **kwargs: Any,
    ) -> Tensor:
        cfg = self.cfg
        skip = self._skip_set(skip_layers)
        if x.ndim != 4 or x.shape[1] != cfg.in_channels:
            raise ValueError(f"x must be [B, {cfg.in_channels}, H, W]")
        batch, _, height, width = x.shape
        if context.ndim != 3 or context.shape[0] != batch:
            raise ValueError("context must be [B, S, text_dim]")
        mask = attention_mask
        if mask is None:
            mask = torch.ones(context.shape[:2], dtype=torch.bool, device=x.device)
        if mask.shape != context.shape[:2]:
            raise ValueError("attention_mask must be a [B, S] mask")
        dtype = x.dtype
        context = context.to(device=x.device, dtype=dtype)

        embedding = _sinusoid(timestep.to(x.device), batch, cfg.freq_dim)
        time = self.time_embedding(embedding.to(dtype)).float()
        mod = self.shared_block_modulation(time.to(dtype)).view(batch, 6, -1)
        head = self.video_head_modulation(time.to(dtype)).view(batch, 2, -1)

        step_cache = kwargs.get("step_cache")
        video_len = height * width
        streams = []
        ropes = []
        for i in range(batch):
            text_in = context[i : i + 1, mask[i].bool().to(x.device)]
            if text_in.shape[1] == 0:
                raise ValueError("every sample needs at least one valid text token")
            video = self.video_embedder(x[i : i + 1].flatten(2).transpose(1, 2).contiguous())
            rope = rope_table(height, width, text_in.shape[1], self._bands, x.device).to(video.dtype)
            text = self.text_embedder(text_in, rope[:, video_len:])
            stream = torch.cat((video, text), 1)
            raise_if_cancelled(0)
            if 0 not in skip:
                stream = self.layers[0](stream, rope, mod[i : i + 1])
            streams.append(stream)
            ropes.append(rope)

        probe = None
        if step_cache is not None:
            probe = torch.cat([stream[:, :video_len] for stream in streams])
            if step_cache.should_skip(probe):
                return step_cache.record_skip()

        outputs = []
        for i, stream in enumerate(streams):
            for index in range(1, len(self.layers)):
                raise_if_cancelled(index)
                if index not in skip:
                    stream = self.layers[index](stream, ropes[i], mod[i : i + 1])
            out = self.operations.norm_modulate(
                stream[:, :video_len], **rms_args(self.final_norm_video),
                scale=head[i : i + 1, 1, None], shift=head[i : i + 1, 0, None],
            )
            out = self.final_linear_video(out)
            outputs.append(out.transpose(1, 2).reshape(1, -1, height, width))
        result = torch.cat(outputs)
        if probe is not None:
            step_cache.record_compute(probe, result)
        return result
