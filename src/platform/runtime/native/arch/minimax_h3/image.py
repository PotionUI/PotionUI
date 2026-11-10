# Derived from: comfy/ldm/minimax/model.py (ComfyUI, GPL-3.0) — MiniMaxH3ImageModel's packed sequence and timestep rule, the reference-row noise augmentation, and the FinalLayer PDD head bank (_pdd_head); the block stack is the diffusers-derived MiniMaxH3Model in model.py.
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

import torch
import torch.nn.functional as F
from torch import Tensor

from ...cache_identity import tensor_identity
from ...errors import NativeEngineUnsupportedError
from ...lora.row_mask import lora_row_mask
from .config import MINIMAX_H3_MODALITY_NUM, MiniMaxH3Config
from .model import MiniMaxH3Model, PreparedTextContext, _cast_to, default_ffn_chunk_rows
from .packing import TEXT_TAG, VIDEO_TAG, frame_position_grid, patchify_video_latents, unpatchify_video_rows

logger = logging.getLogger(__name__)

REFERENCE_TIMESTEP = 0.999
_DROPPED_SUFFIXES = (".comfy_attention.config",)
_UNSUPPORTED_SUFFIX = ".weight_codebook"
W4A8_MESSAGE = (
    "The w4a8 MiniMax-H3-Image file is not supported yet: use the int8 ConvRot or bf16 checkpoint"
)


def convert_minimax_h3_image_state_dict(sd: dict[str, Tensor]) -> dict[str, Tensor]:
    if any(key.endswith(_UNSUPPORTED_SUFFIX) for key in sd):
        raise NativeEngineUnsupportedError(W4A8_MESSAGE)
    return {key: value for key, value in sd.items() if not key.endswith(_DROPPED_SUFFIXES)}


def augment_reference_latent(latent: Tensor, seed: int, patch_size: tuple[int, int, int]) -> Tensor:
    rows = patchify_video_latents(latent.to(torch.float32), patch_size)
    noise = torch.randn(rows.shape, generator=torch.Generator("cpu").manual_seed(int(seed)), dtype=torch.float32)
    rows = REFERENCE_TIMESTEP * rows + (1.0 - REFERENCE_TIMESTEP) * noise.to(rows.device)
    _, channels, frames, height, width = latent.shape
    return unpatchify_video_rows(
        rows, num_latent_frames=frames, latent_height=height, latent_width=width,
        channels=channels, patch_size=patch_size,
    )


@dataclass(frozen=True)
class ReferenceLatents:
    latents: tuple[Tensor, ...]


@dataclass(frozen=True)
class ImagePackedLayout:
    position_ids: Tensor
    token_tags: Tensor
    video_indices: Tensor
    text_indices: Tensor
    target_slice: slice
    num_reference_rows: int


def build_image_layout(
    text_token_tags: Tensor, reference_hw: Sequence[tuple[int, int]], latent_height: int, latent_width: int,
    patch_size: tuple[int, int, int], device: torch.device | str | None = None,
) -> ImagePackedLayout:
    device = torch.device(device) if device is not None else text_token_tags.device
    tags = text_token_tags.to(device=device, dtype=torch.long)
    _, patch_h, patch_w = patch_size
    num_text = int(tags.shape[0])
    reference_rows = [(height // patch_h) * (width // patch_w) for height, width in reference_hw]
    target_rows = (latent_height // patch_h) * (latent_width // patch_w)
    sequence_length = num_text + sum(reference_rows) + target_rows

    position_ids = torch.zeros(sequence_length, 3, dtype=torch.float64, device=device)
    position_ids[:num_text, 0] = torch.arange(num_text, dtype=torch.float64, device=device)
    cursor = num_text
    rotary_time = float(num_text)
    for (height, width), rows in zip(reference_hw, reference_rows):
        grid, _ = frame_position_grid(height, width, patch_h, patch_w, device)
        position_ids[cursor:cursor + rows, 0] = rotary_time
        position_ids[cursor:cursor + rows, 1:] = grid
        cursor += rows
        rotary_time += 1.0
    grid, _ = frame_position_grid(latent_height, latent_width, patch_h, patch_w, device)
    position_ids[cursor:, 0] = rotary_time
    position_ids[cursor:, 1:] = grid

    token_tags = torch.full((sequence_length,), VIDEO_TAG, dtype=torch.long, device=device)
    token_tags[:num_text] = tags
    return ImagePackedLayout(
        position_ids=position_ids,
        token_tags=token_tags,
        video_indices=torch.arange(num_text, sequence_length, device=device),
        text_indices=torch.arange(num_text, device=device),
        target_slice=slice(cursor, sequence_length),
        num_reference_rows=cursor - num_text,
    )


def row_timesteps(layout: ImagePackedLayout, sigma: float) -> tuple[Tensor, Tensor]:
    t = 1.0 - sigma
    device = layout.token_tags.device
    rows = torch.full((layout.token_tags.shape[0],), t, dtype=torch.float32, device=device)
    start = int(layout.text_indices.shape[0])
    rows[start:start + layout.num_reference_rows] = max(t, REFERENCE_TIMESTEP)
    return torch.unique(rows, sorted=True, return_inverse=True)


def pdd_interval(grid: Sequence[float], sigma: float, sigma_next: float) -> tuple[int, int]:
    steps = len(grid) - 1
    start = min(range(steps + 1), key=lambda k: abs(grid[k] - sigma))
    stop = min(range(steps + 1), key=lambda k: abs(grid[k] - sigma_next))
    start = min(start, steps - 1)
    return start, max(stop, start + 1)


def pdd_head(weight: Tensor, bias: Tensor, grid: Sequence[float], start: int, stop: int) -> tuple[Tensor, Tensor]:
    steps = len(grid) - 1
    rows = weight.reshape(steps, -1, weight.shape[1])
    bias_rows = bias.reshape(steps, -1)
    spans = [grid[k] - grid[k + 1] for k in range(start, stop)]
    weights = torch.tensor(spans, dtype=weight.dtype, device=weight.device) / sum(spans)
    first = max(start, 1)
    tail = weights[first - start:]
    return (
        rows[0] + torch.einsum("n,noi->oi", tail, rows[first:stop]),
        bias_rows[0] + torch.einsum("n,no->o", tail, bias_rows[first:stop]),
    )


class MiniMaxH3ImageModel(MiniMaxH3Model):
    _layout_entry: tuple | None = None
    _text_entry: tuple | None = None
    _head_entries: dict | None = None

    def __init__(self, config: MiniMaxH3Config, operations, dtype=None, device=None) -> None:
        super().__init__(config, operations, dtype=dtype, device=device)
        self.patch_size = config.patch_size[-1]

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "MiniMaxH3ImageModel":
        return cls(MiniMaxH3Config.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        loaded = self.rope.inv_freq
        usable = loaded.device.type != "meta" and bool(torch.isfinite(loaded).all())
        loaded = loaded.detach().clone() if usable else None
        super().post_load()
        self.release_derived_caches()
        if loaded is None:
            return
        computed = self.rope.inv_freq
        if not torch.allclose(loaded.to(computed), computed, rtol=1e-6, atol=0.0):
            logger.warning("minimax_h3_image: the checkpoint's rope.inv_freq differs from theta %.0f; using the checkpoint's",
                           self.config.rope_theta)
            self.rope.inv_freq = loaded.to(computed)

    def release_derived_caches(self) -> int:
        released = super().release_derived_caches()
        self._layout_entry = None
        self._text_entry = None
        self._head_entries = None
        return released

    def _layout(self, tags: Tensor, reference_hw: list[tuple[int, int]], height: int, width: int,
                device: torch.device) -> tuple[tuple, ImagePackedLayout]:
        key = (tuple(reference_hw), height, width, str(device), int(tags.shape[0]))
        entry = self._layout_entry
        if entry is not None and entry[0] == key and torch.equal(entry[1], tags):
            return entry, entry[2]
        layout = build_image_layout(tags, reference_hw, height, width, self.config.patch_size, device)
        return (key, tags.clone(), layout), layout

    def _text(self, context: Tensor, dtype: torch.dtype, device: torch.device) -> Tensor:
        entry = self._text_entry
        if entry is not None and not entry[1].is_stale(context, None, dtype, device, id(self)):
            return entry[1].text_embeds
        prepared = PreparedTextContext(
            text_embeds=self._prepare_context(context, dtype),
            source_identity=tensor_identity(context),
            producer_id=id(self),
        )
        self._text_entry = (context, prepared)
        return prepared.text_embeds

    def _head(self, sigma: float, sigma_next: Tensor | None, device: torch.device) -> tuple[Tensor, Tensor]:
        video_out = self.final_layer.video_out
        grid = self.config.pdd_sigmas
        if self.config.pdd_steps == 1:
            key = (0, 1)
        else:
            if sigma_next is None:
                raise ValueError("MiniMax-H3-Image needs each step's target sigma to pick its output head")
            key = pdd_interval(grid, sigma, float(sigma_next.reshape(-1)[0]))
        entries = self._head_entries
        if entries is None:
            entries = self._head_entries = {}
        cached = entries.get((key, str(device)))
        if cached is not None:
            return cached
        weight = video_out.weight.detach().to(device=device, dtype=torch.float32)
        bias = video_out.bias.detach().to(device=device, dtype=torch.float32)
        head = (weight, bias) if self.config.pdd_steps == 1 else pdd_head(weight, bias, grid, *key)
        entries[(key, str(device))] = head
        return head

    def forward(
        self,
        x: Tensor,
        timestep: Tensor,
        context: Tensor,
        y: Tensor | None = None,
        guidance: Tensor | None = None,
        attention_mask: Tensor | None = None,
        ref_latents: ReferenceLatents | Sequence[Tensor] | None = None,
        flow_map_target: Tensor | None = None,
        token_tags: Tensor | None = None,
        **kwargs: Any,
    ) -> Tensor:
        if x.ndim != 5 or x.shape[0] != 1 or x.shape[2] != 1:
            raise ValueError(f"MiniMax-H3-Image takes one [1, C, 1, H, W] latent, got {list(x.shape)}")
        if context.ndim != 3 or context.shape[0] != 1:
            raise ValueError(f"context must be [1, tokens, {self.config.text_dim}], got {list(context.shape)}")
        config = self.config
        device = x.device
        sigma = float(timestep.reshape(-1)[0])
        if isinstance(ref_latents, ReferenceLatents):
            ref_latents = ref_latents.latents
        references = [reference.to(device=device, dtype=torch.float32) for reference in (ref_latents or ())]
        height, width = int(x.shape[-2]), int(x.shape[-1])
        tags = token_tags if token_tags is not None else torch.full((context.shape[1],), TEXT_TAG, dtype=torch.long)
        tags = tags.reshape(-1).to(device=device, dtype=torch.long)
        if tags.shape[0] != context.shape[1]:
            raise ValueError(f"token_tags has {tags.shape[0]} entries for {context.shape[1]} text tokens")
        layout_entry, layout = self._layout(
            tags, [(int(r.shape[-2]), int(r.shape[-1])) for r in references], height, width, device,
        )

        rows = [patchify_video_latents(r.to(torch.float32), config.patch_size) for r in references]
        rows.append(patchify_video_latents(x.to(torch.float32), config.patch_size))
        video_embeds = self.video_patch_proj(_cast_to(torch.cat(rows).unsqueeze(0), self.video_patch_proj))
        compute_dtype = kwargs.pop("compute_dtype", None) or self._stream_dtype()
        rotary_emb = self._prepare_positional_embeddings(layout.position_ids, compute_dtype)
        self._layout_entry = layout_entry
        text_embeds = self._text(context.to(device), compute_dtype, device)

        hidden_dtype = text_embeds.dtype
        sequence_length = int(layout.token_tags.shape[0])
        packed = text_embeds.new_zeros((1, sequence_length, config.hidden_size))
        packed = packed.index_copy(1, layout.text_indices, text_embeds)
        packed = packed.index_copy(1, layout.video_indices, video_embeds.to(hidden_dtype))

        timesteps, timestep_indices = row_timesteps(layout, sigma)
        temb = self._prepare_timestep(timesteps)
        adaln_indices = timestep_indices * MINIMAX_H3_MODALITY_NUM + layout.token_tags
        seq_chunk_rows = int(kwargs.pop("seq_chunk_rows", 0))
        ffn_chunk_rows = default_ffn_chunk_rows(sequence_length, config.hidden_size, config.ffn_dim)
        generated = layout.video_indices[layout.num_reference_rows:]
        with lora_row_mask(torch.ones(sequence_length, dtype=torch.bool, device=device)):
            packed, _, _ = self._process_transformer_blocks(
                packed, temb, adaln_indices, rotary_emb, generated, generated[:0],
                seq_chunk_rows=seq_chunk_rows, ffn_chunk_rows=ffn_chunk_rows,
            )

        ops = self.operations
        final = self.final_layer
        shift, scale = final.adaln_proj(temb)
        target = ops.norm_modulate(
            packed[:, layout.target_slice], **ops.norm_args(final.norm), scale=scale, shift=shift,
            index=timestep_indices[layout.target_slice],
        )
        weight, bias = self._head(sigma, flow_map_target, device)
        out = F.linear(target.to(torch.float32), weight, bias)
        latent = unpatchify_video_rows(
            out[0], num_latent_frames=1, latent_height=height, latent_width=width,
            channels=config.in_channels, patch_size=config.patch_size,
        )
        return (-latent).to(x.dtype)
