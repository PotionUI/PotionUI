"""Qwen2.5-VL vision tower — image conditioning for Qwen-Image-Edit.

Vendored/derived from ComfyUI:
    ``comfy/text_encoders/qwen_vl.py``      — patch embed, vision rotary embedding,
                                               patch merger, vision attention/MLP/
                                               block, ``Qwen2VLVisionTransformer``,
                                               ``process_qwen2vl_images``.
    ``comfy/text_encoders/llama.py``        — the m-RoPE position-id construction
                                               embedded in ``Qwen25_7BVLI.forward``
                                               (ported here as a standalone,
                                               testable function).
License: GPL-3.0 (see ``vendor/gpl/comfyui/LICENSE``).

Cross-checked against HuggingFace ``transformers``
``models/qwen2_5_vl/modeling_qwen2_5_vl.py`` (window-index partitioning, vision
rotary position ids, ``cu_seqlens`` construction, ``apply_multimodal_rotary_pos_emb``'s
mrope-section split, ``get_rope_index``'s per-image position advance): no numeric
discrepancy found for Qwen2.5-VL's non-interleaved m-RoPE. Qwen3-VL (Krea-2) uses a
*different*, interleaved m-RoPE variant (``qwen3.py``'s ``KREA2_LAYERS`` docstring) —
out of scope here.

Built through the ``operations`` seam (``operations.Linear`` / ``Conv3d`` /
``RMSNorm``), matching ComfyUI's own vision-tower module (unlike this package's
Llama-family text modules, which own a private ``_RMSNorm`` — the vision tower
has no per-layer "add 1.0" RMSNorm variant to special-case, so
``operations.RMSNorm`` is used directly, exactly as ComfyUI does). A quantised
(fp8-scaled / nvfp4) vision-tower checkpoint therefore dequantises for free
through the same ``fp8_ops``/``manual_cast`` machinery the language model uses.

Checkpoint reality (verified against the local repacks, not restated here):
``qwen_2.5_vl_7b_fp8*.safetensors`` ship the vision tower as
top-level ``visual.*`` keys, 32 blocks, hidden 1280, intermediate 3420, 16 heads,
patch 14, temporal-patch 2, spatial-merge 2, window 112 (``fullatt_block_indexes``
= every 4th block, 1-indexed from the end of each quarter: ``[7, 15, 23, 31]``).

Scope: one image per :func:`preprocess_qwen_vl_image` call, one tower forward per
image (the caller splices per-image outputs into the token sequence — see
``qwen25_vl.py``'s ``Qwen25VLTextEncoder._encode_with_images``). ComfyUI's tower
can batch several images' patches through one forward sharing window/cu_seqlens
bookkeeping; not needed for this stage and not ported. Qwen3-VL's "DeepStack"
per-layer visual-feature injection is a different checkpoint layout and is also
not ported here.
"""

from __future__ import annotations

import math
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ._functional import optimized_attention

# Fixed Qwen2.5-VL-7B vision-tower architecture constants. None of these are
# recoverable from checkpoint tensor shapes alone (unlike hidden_size/num_layers,
# which the loader's ``_build_config`` derives from weight shapes) — verified
# against the local qwen_2.5_vl_7b_fp8*.safetensors headers and matching both
# ComfyUI's ``Qwen2VLVisionTransformer`` defaults and HF's
# ``Qwen2_5_VLVisionConfig`` defaults.
VISION_HIDDEN_SIZE = 1280
VISION_INTERMEDIATE_SIZE = 3420
VISION_NUM_LAYERS = 32
VISION_NUM_HEADS = 16
VISION_PATCH_SIZE = 14
VISION_TEMPORAL_PATCH_SIZE = 2
VISION_SPATIAL_MERGE_SIZE = 2
VISION_WINDOW_SIZE = 112
# T/H/W split of the LM's head_dim//2=64 rope frequencies for m-RoPE (sums to 64).
ROPE_DIMS = (16, 24, 24)
# Qwen2 vocab special tokens (shared with the Qwen3/Qwen3-VL tokenizer assets).
IMAGE_PAD_TOKEN = 151655
VISION_START_TOKEN = 151652
VISION_END_TOKEN = 151653

# OpenAI-CLIP normalisation constants (ComfyUI's ``process_qwen2vl_images``
# default — matches HF's Qwen2VLImageProcessor default too).
_CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
_CLIP_STD = (0.26862954, 0.26130258, 0.27577711)


def preprocess_qwen_vl_image(
    image: torch.Tensor,
    *,
    min_pixels: int = 3136,
    max_pixels: int = 12845056,
    patch_size: int = VISION_PATCH_SIZE,
    temporal_patch_size: int = VISION_TEMPORAL_PATCH_SIZE,
    merge_size: int = VISION_SPATIAL_MERGE_SIZE,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Resize/normalize/patchify one image -> ``(flatten_patches, grid_thw)``.

    ``image`` is ``[H, W, 3]`` float, values in ``[0, 1]`` (the ComfyUI ``IMAGE``
    tensor convention, minus the batch axis — ComfyUI's ``process_qwen2vl_images``
    takes a ``[B,H,W,C]`` batch but only ever reads ``images[0]``, so this port
    takes the single image directly).

    Returns ``flatten_patches`` ``[grid_h*grid_w, 3*temporal_patch_size*
    patch_size*patch_size]`` (the :class:`VisionPatchEmbed` input) and
    ``grid_thw`` ``[1, 3]`` = ``(1, grid_h, grid_w)`` in PATCH units (before the
    2x2 spatial merge — halve ``grid_h``/``grid_w`` for post-merge LLM-token
    grid dims).
    """
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError(f"expected an [H, W, 3] image tensor, got shape {tuple(image.shape)}")

    height, width, _channels = image.shape
    device = image.device
    img = image.permute(2, 0, 1).float()  # [C, H, W]

    factor = patch_size * merge_size
    h_bar = round(height / factor) * factor
    w_bar = round(width / factor) * factor
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = max(factor, math.floor(height / beta / factor) * factor)
        w_bar = max(factor, math.floor(width / beta / factor) * factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = math.ceil(height * beta / factor) * factor
        w_bar = math.ceil(width * beta / factor) * factor

    img_resized = F.interpolate(
        img.unsqueeze(0), size=(h_bar, w_bar), mode="bilinear", align_corners=False
    ).squeeze(0)
    normalized = img_resized.clone()
    for c in range(3):
        normalized[c] = (img_resized[c] - _CLIP_MEAN[c]) / _CLIP_STD[c]

    grid_h = h_bar // patch_size
    grid_w = w_bar // patch_size
    grid_thw = torch.tensor([[1, grid_h, grid_w]], device=device, dtype=torch.long)

    # ComfyUI stacks the single frame `temporal_patch_size` times so a still
    # image gets a valid temporal-patch stack for the Conv3d patch embed.
    channels = normalized.shape[0]
    pixel_values = normalized.unsqueeze(0).repeat(temporal_patch_size, 1, 1, 1)
    patches = pixel_values.reshape(
        1,
        temporal_patch_size,
        channels,
        grid_h // merge_size,
        merge_size,
        patch_size,
        grid_w // merge_size,
        merge_size,
        patch_size,
    )
    patches = patches.permute(0, 3, 6, 4, 7, 2, 1, 5, 8)
    flatten_patches = patches.reshape(
        grid_h * grid_w, channels * temporal_patch_size * patch_size * patch_size
    )
    return flatten_patches, grid_thw


class VisionPatchEmbed(nn.Module):
    """Conv3d patch embedding — checkpoint key ``patch_embed.proj.*`` (no bias)."""

    def __init__(
        self,
        patch_size: int = VISION_PATCH_SIZE,
        temporal_patch_size: int = VISION_TEMPORAL_PATCH_SIZE,
        in_channels: int = 3,
        embed_dim: int = VISION_HIDDEN_SIZE,
        operations: Any = None,
        device=None,
        dtype=None,
    ) -> None:
        super().__init__()
        self.patch_size = patch_size
        self.temporal_patch_size = temporal_patch_size
        self.in_channels = in_channels
        self.embed_dim = embed_dim
        kernel_size = (temporal_patch_size, patch_size, patch_size)
        self.proj = operations.Conv3d(
            in_channels, embed_dim, kernel_size=kernel_size, stride=kernel_size,
            bias=False, device=device, dtype=dtype,
        )

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        hidden_states = hidden_states.view(
            -1, self.in_channels, self.temporal_patch_size, self.patch_size, self.patch_size
        )
        # No dtype cast here: the ops seam casts the WEIGHT to the activation's
        # dtype on forward (``cast_bias_weight``), not the other way around —
        # casting the input to the weight's (possibly fp8) storage dtype would
        # quantise the input pixels instead of dequantising the weight.
        hidden_states = self.proj(hidden_states)
        return hidden_states.view(-1, self.embed_dim)


class VisionRotaryEmbedding(nn.Module):
    """Un-parameterised 2-D (H/W) rotary frequency table (no checkpoint weight)."""

    def __init__(self, dim: int, theta: float = 10000.0) -> None:
        super().__init__()
        self.dim = dim
        self.theta = theta

    def forward(self, seqlen: int, device) -> torch.Tensor:
        inv_freq = 1.0 / (self.theta ** (torch.arange(0, self.dim, 2, dtype=torch.float32, device=device) / self.dim))
        seq = torch.arange(seqlen, device=device, dtype=torch.float32)
        return torch.outer(seq, inv_freq)


def _rotate_half_vision(x: torch.Tensor) -> torch.Tensor:
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2 :]
    return torch.cat((-x2, x1), dim=-1)


def apply_rotary_pos_emb_vision(q, k, cos, sin):
    cos, sin = cos.unsqueeze(-2).float(), sin.unsqueeze(-2).float()
    q_embed = (q * cos) + (_rotate_half_vision(q) * sin)
    k_embed = (k * cos) + (_rotate_half_vision(k) * sin)
    return q_embed, k_embed


class PatchMerger(nn.Module):
    """2x2 spatial merge + 2-layer MLP down to the LM hidden size.

    Checkpoint keys: ``merger.ln_q.*``, ``merger.mlp.0.*``, ``merger.mlp.2.*``
    (the ``nn.Sequential`` index skips 1, the GELU, which owns no weight).
    """

    def __init__(
        self, dim: int, context_dim: int, spatial_merge_size: int = VISION_SPATIAL_MERGE_SIZE,
        operations: Any = None, device=None, dtype=None,
    ) -> None:
        super().__init__()
        self.hidden_size = context_dim * (spatial_merge_size ** 2)
        self.ln_q = operations.RMSNorm(context_dim, eps=1e-6, device=device, dtype=dtype)
        self.mlp = nn.Sequential(
            operations.Linear(self.hidden_size, self.hidden_size, device=device, dtype=dtype),
            nn.GELU(),
            operations.Linear(self.hidden_size, dim, device=device, dtype=dtype),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.ln_q(x).reshape(-1, self.hidden_size)
        return self.mlp(x)


class VisionAttention(nn.Module):
    """Fused-qkv self-attention, split per ``cu_seqlens`` span (window or full)."""

    def __init__(self, hidden_size: int, num_heads: int, operations: Any = None, device=None, dtype=None) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.num_heads = num_heads
        self.head_dim = hidden_size // num_heads
        self.qkv = operations.Linear(hidden_size, hidden_size * 3, bias=True, device=device, dtype=dtype)
        self.proj = operations.Linear(hidden_size, hidden_size, bias=True, device=device, dtype=dtype)

    def forward(self, hidden_states: torch.Tensor, position_embeddings, cu_seqlens) -> torch.Tensor:
        seq_length, _ = hidden_states.shape
        qkv = self.qkv(hidden_states).reshape(seq_length, 3, self.num_heads, self.head_dim)
        q, k, v = qkv.permute(1, 0, 2, 3).unbind(0)  # each [S, heads, dim]

        cos, sin = position_embeddings
        q, k = apply_rotary_pos_emb_vision(q, k, cos, sin)

        q = q.transpose(0, 1).unsqueeze(0)  # [1, heads, S, dim]
        k = k.transpose(0, 1).unsqueeze(0)
        v = v.transpose(0, 1).unsqueeze(0)

        lengths = (cu_seqlens[1:] - cu_seqlens[:-1]).tolist()
        splits = [torch.split(t, lengths, dim=2) for t in (q, k, v)]
        attn_outputs = [
            optimized_attention(qs, ks, vs, self.num_heads, skip_reshape=True)
            for qs, ks, vs in zip(*splits)
        ]
        attn_output = torch.cat(attn_outputs, dim=1).reshape(seq_length, -1)
        return self.proj(attn_output)


class VisionMLP(nn.Module):
    """SwiGLU MLP — ``gate_proj``/``up_proj``/``down_proj``, all bias=True."""

    def __init__(self, hidden_size: int, intermediate_size: int, operations: Any = None, device=None, dtype=None) -> None:
        super().__init__()
        self.gate_proj = operations.Linear(hidden_size, intermediate_size, bias=True, device=device, dtype=dtype)
        self.up_proj = operations.Linear(hidden_size, intermediate_size, bias=True, device=device, dtype=dtype)
        self.down_proj = operations.Linear(intermediate_size, hidden_size, bias=True, device=device, dtype=dtype)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x))


class VisionBlock(nn.Module):
    def __init__(
        self, hidden_size: int, intermediate_size: int, num_heads: int,
        operations: Any = None, device=None, dtype=None,
    ) -> None:
        super().__init__()
        self.norm1 = operations.RMSNorm(hidden_size, eps=1e-6, device=device, dtype=dtype)
        self.norm2 = operations.RMSNorm(hidden_size, eps=1e-6, device=device, dtype=dtype)
        self.attn = VisionAttention(hidden_size, num_heads, operations=operations, device=device, dtype=dtype)
        self.mlp = VisionMLP(hidden_size, intermediate_size, operations=operations, device=device, dtype=dtype)

    def forward(self, hidden_states: torch.Tensor, position_embeddings, cu_seqlens) -> torch.Tensor:
        hidden_states = hidden_states + self.attn(self.norm1(hidden_states), position_embeddings, cu_seqlens)
        hidden_states = hidden_states + self.mlp(self.norm2(hidden_states))
        return hidden_states


class Qwen2VLVisionTower(nn.Module):
    """Top-level vision tower: patch embed -> windowed/full blocks -> merger.

    Checkpoint prefix ``visual.*`` (Qwen2.5-VL; top-level, unlike Qwen3-VL's
    ``model.visual.*``). ``forward(pixel_values, grid_thw)`` ->
    ``[num_merged_tokens, output_hidden_size]``, ``num_merged_tokens ==
    grid_t*grid_h*grid_w // spatial_merge_size**2``.
    """

    def __init__(
        self,
        hidden_size: int = VISION_HIDDEN_SIZE,
        output_hidden_size: int = 3584,
        intermediate_size: int = VISION_INTERMEDIATE_SIZE,
        num_heads: int = VISION_NUM_HEADS,
        num_layers: int = VISION_NUM_LAYERS,
        patch_size: int = VISION_PATCH_SIZE,
        temporal_patch_size: int = VISION_TEMPORAL_PATCH_SIZE,
        spatial_merge_size: int = VISION_SPATIAL_MERGE_SIZE,
        window_size: int = VISION_WINDOW_SIZE,
        operations: Any = None,
        device=None,
        dtype=None,
    ) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.patch_size = patch_size
        self.spatial_merge_size = spatial_merge_size
        self.window_size = window_size
        # Every 4th block (1-indexed from each quarter's end) runs full attention;
        # the rest run windowed. [7,15,23,31] for the real 32-layer tower.
        self.fullatt_block_indexes = [(num_layers // 4) * (i + 1) - 1 for i in range(4)]

        self.patch_embed = VisionPatchEmbed(
            patch_size=patch_size, temporal_patch_size=temporal_patch_size, in_channels=3,
            embed_dim=hidden_size, operations=operations, device=device, dtype=dtype,
        )
        head_dim = hidden_size // num_heads
        self.rotary_pos_emb = VisionRotaryEmbedding(head_dim // 2)
        self.blocks = nn.ModuleList([
            VisionBlock(hidden_size, intermediate_size, num_heads, operations=operations, device=device, dtype=dtype)
            for _ in range(num_layers)
        ])
        self.merger = PatchMerger(
            dim=output_hidden_size, context_dim=hidden_size, spatial_merge_size=spatial_merge_size,
            operations=operations, device=device, dtype=dtype,
        )

    def get_window_index(self, grid_thw: torch.Tensor):
        window_index = []
        cu_window_seqlens = [0]
        window_index_id = 0
        vit_merger_window_size = self.window_size // self.spatial_merge_size // self.patch_size

        for grid_t, grid_h, grid_w in grid_thw:
            llm_grid_h = grid_h // self.spatial_merge_size
            llm_grid_w = grid_w // self.spatial_merge_size
            index = torch.arange(grid_t * llm_grid_h * llm_grid_w).reshape(grid_t, llm_grid_h, llm_grid_w)

            pad_h = vit_merger_window_size - llm_grid_h % vit_merger_window_size
            pad_w = vit_merger_window_size - llm_grid_w % vit_merger_window_size
            num_windows_h = (llm_grid_h + pad_h) // vit_merger_window_size
            num_windows_w = (llm_grid_w + pad_w) // vit_merger_window_size

            index_padded = F.pad(index, (0, pad_w, 0, pad_h), "constant", -100)
            index_padded = index_padded.reshape(
                grid_t, num_windows_h, vit_merger_window_size, num_windows_w, vit_merger_window_size,
            )
            index_padded = index_padded.permute(0, 1, 3, 2, 4).reshape(
                grid_t, num_windows_h * num_windows_w, vit_merger_window_size, vit_merger_window_size,
            )

            seqlens = (index_padded != -100).sum([2, 3]).reshape(-1)
            index_padded = index_padded.reshape(-1)
            index_new = index_padded[index_padded != -100]
            window_index.append(index_new + window_index_id)

            cu_seqlens_tmp = seqlens.cumsum(0) * self.spatial_merge_size * self.spatial_merge_size + cu_window_seqlens[-1]
            cu_window_seqlens.extend(cu_seqlens_tmp.tolist())
            window_index_id += int((grid_t * llm_grid_h * llm_grid_w).item())

        return torch.cat(window_index, dim=0), cu_window_seqlens

    def get_position_embeddings(self, grid_thw: torch.Tensor, device):
        pos_ids = []
        for t, h, w in grid_thw:
            hpos_ids = torch.arange(h, device=device).unsqueeze(1).expand(-1, w)
            hpos_ids = hpos_ids.reshape(
                h // self.spatial_merge_size, self.spatial_merge_size,
                w // self.spatial_merge_size, self.spatial_merge_size,
            ).permute(0, 2, 1, 3).flatten()

            wpos_ids = torch.arange(w, device=device).unsqueeze(0).expand(h, -1)
            wpos_ids = wpos_ids.reshape(
                h // self.spatial_merge_size, self.spatial_merge_size,
                w // self.spatial_merge_size, self.spatial_merge_size,
            ).permute(0, 2, 1, 3).flatten()

            pos_ids.append(torch.stack([hpos_ids, wpos_ids], dim=-1).repeat(t, 1))

        pos_ids = torch.cat(pos_ids, dim=0)
        max_grid_size = int(grid_thw[:, 1:].max().item())
        rotary_pos_emb_full = self.rotary_pos_emb(max_grid_size, device)
        return rotary_pos_emb_full[pos_ids].flatten(1)

    def forward(self, pixel_values: torch.Tensor, image_grid_thw: torch.Tensor) -> torch.Tensor:
        hidden_states = self.patch_embed(pixel_values)

        window_index, cu_window_seqlens = self.get_window_index(image_grid_thw)
        cu_window_seqlens_t = torch.unique_consecutive(
            torch.tensor(cu_window_seqlens, device=hidden_states.device)
        )

        position_embeddings = self.get_position_embeddings(image_grid_thw, hidden_states.device)

        seq_len, _ = hidden_states.size()
        merge_unit = self.spatial_merge_size * self.spatial_merge_size

        hidden_states = hidden_states.reshape(seq_len // merge_unit, merge_unit, -1)[window_index].reshape(seq_len, -1)

        position_embeddings = position_embeddings.reshape(seq_len // merge_unit, merge_unit, -1)[window_index]
        position_embeddings = position_embeddings.reshape(seq_len, -1)
        position_embeddings = torch.cat((position_embeddings, position_embeddings), dim=-1)
        position_embeddings = (position_embeddings.cos(), position_embeddings.sin())

        cu_seqlens = torch.repeat_interleave(
            image_grid_thw[:, 1] * image_grid_thw[:, 2], image_grid_thw[:, 0]
        ).cumsum(dim=0, dtype=torch.int32)
        cu_seqlens = F.pad(cu_seqlens, (1, 0), value=0)

        for i, block in enumerate(self.blocks):
            cu_now = cu_seqlens if i in self.fullatt_block_indexes else cu_window_seqlens_t
            hidden_states = block(hidden_states, position_embeddings, cu_now)

        hidden_states = self.merger(hidden_states)
        reverse_indices = torch.argsort(window_index)
        return hidden_states[reverse_indices, :]


def qwen25vl_mrope_position_ids(
    image_spans: list[tuple[int, int, torch.Tensor]],
    seq_len: int,
    device,
    attention_mask: torch.Tensor | None = None,
) -> torch.Tensor:
    """3-axis (T,H,W) m-RoPE position ids for a token+spliced-vision sequence.

    ``image_spans``: ``(start, size, grid_thw)`` triples, in sequence order, in
    the SPLICED sequence (``start`` = the first vision-embedding row's index,
    ``size`` = merged vision-token count, ``grid_thw`` = ``[1,3]`` PATCH-unit
    grid from :func:`preprocess_qwen_vl_image`). ``attention_mask`` is
    ``[seq_len]`` (batch already indexed out by the caller — this encoder
    restricts image-conditioned encode to batch size 1).

    Port of the position-id construction embedded in ComfyUI's
    ``Qwen25_7BVLI.forward`` (there is no separate upstream function for the
    single-model case — ``qwen_image.py``'s ``qwen2vl_mrope_position_ids`` is
    the same algorithm generalised over ``embeds_info`` dicts for the
    multi-encoder dispatcher, not used here). Cross-checked against HF
    ``Qwen2VLModel.get_rope_index``: identical algorithm (text runs plain 1-D
    sequential positions on all 3 axes; an image span gets ``T`` held constant
    at the span's start, ``H``/``W`` tiled over the post-merge grid; text AFTER
    an image resumes at ``start + max(grid_h, grid_w)//spatial_merge_size``).
    ``offset`` threads the cumulative "compression" (image span occupies
    ``size`` sequence slots but only advances the position counter by
    ``len_max``) across multiple images so a second image's positions are
    correctly based off the first image's.
    """
    if seq_len <= 0:
        raise ValueError("seq_len must be positive")
    position_ids = torch.arange(seq_len, device=device).unsqueeze(0).repeat(3, 1).clone()
    if not image_spans:
        return position_ids

    offset = 0
    for start, size, grid in image_spans:
        if size <= 0:
            raise ValueError(f"image span size must be positive, got {size}")
        end = start + size
        grid = grid.to(device)
        len_max = int(grid.max().item()) // VISION_SPATIAL_MERGE_SIZE
        start_next = len_max + start

        if attention_mask is not None:
            after_mask = attention_mask[end:]
            text_positions = after_mask.cumsum(0) - 1 + start_next + offset
            position_ids[:, end:] = torch.where(after_mask.bool(), text_positions, position_ids[0, end:])
        else:
            position_ids[:, end:] = torch.arange(
                start_next + offset, start_next + (seq_len - end) + offset, device=device
            )

        position_ids[0, start:end] = start + offset
        max_h = int(grid[0][1].item()) // VISION_SPATIAL_MERGE_SIZE
        position_ids[1, start:end] = (
            torch.arange(start + offset, start + max_h + offset, device=device)
            .unsqueeze(1).repeat(1, math.ceil((end - start) / max_h)).flatten(0)[: end - start]
        )
        max_w = int(grid[0][2].item()) // VISION_SPATIAL_MERGE_SIZE
        position_ids[2, start:end] = (
            torch.arange(start + offset, start + max_w + offset, device=device)
            .unsqueeze(0).repeat(math.ceil((end - start) / max_w), 1).flatten(0)[: end - start]
        )
        offset += len_max - (end - start)

    return position_ids.to(torch.long)
