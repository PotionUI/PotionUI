"""Qwen3-VL vision tower — image conditioning for Krea-2's Qwen3-VL-4B text encoder.

Architecture constants below are sourced from HuggingFace's ``Qwen/Qwen3-VL-4B-
Instruct`` ``config.json`` / ``preprocessor_config.json`` (fetched 2026-07-22;
no local checkpoint is available in this environment to cross-check tensor
shapes directly, unlike ``qwen_vl_vision.py``'s Qwen2.5-VL constants, which
were verified against local repacks — GPU validation against the real Krea-2
TE checkpoint is still outstanding). Code ported
from ``transformers/models/qwen3_vl/modeling_qwen3_vl.py`` +
``transformers/vision_utils.py`` (main branch, 2026-07-22), Apache-2.0.

Differences from Qwen2.5-VL's tower (``qwen_vl_vision.py``), all confirmed
against that source:
  * ``LayerNorm``, not ``RMSNorm``, in every vision block + merger.
  * No windowing: every block runs full (per-image) attention — no
    ``fullatt_block_indexes`` / ``window_index`` / ``cu_window_seqlens``. A
    single tower forward already covers one whole image, so there is nothing
    to split.
  * A LEARNED, bilinearly-interpolated absolute position embedding
    (``pos_embed``, ``num_position_embeddings=2304`` -> a 48x48 grid,
    ``get_vision_bilinear_indices_and_weights``) is ADDED to the rotary
    embedding (Qwen2.5-VL's tower has no learned position embedding at all).
  * DeepStack: 3 extra per-layer feature taps (``deepstack_visual_indexes``,
    ``(5, 11, 17)`` for the 24-layer tower) are merged through their own
    ``PatchMerger`` (``use_postshuffle_norm=True``) and returned alongside the
    regular merged output. Injecting them into the LANGUAGE MODEL's first 3
    decoder layers is ``qwen3.py``'s job (``_Qwen3Transformer.forward``'s
    ``deepstack_embeds`` parameter) — NOT this module's.
  * Patch embed ``Conv3d`` carries a bias (Qwen2.5-VL's does not); patch 16
    (not 14); image normalization is plain ``(0.5,0.5,0.5)``/``(0.5,0.5,0.5)``,
    not OpenAI-CLIP stats.
  * Vision MLP is a plain 2-layer GELU-tanh MLP (``linear_fc1``/``linear_fc2``),
    not the SwiGLU 3-layer MLP Qwen2.5-VL's tower uses.
  * The vision-tower rotary embedding takes position ids directly
    (``position_ids.unsqueeze(-1) * inv_freq``) rather than building a
    ``seqlen``-indexed lookup table first — numerically the same operation,
    simpler call shape.

The LLM-side 3-axis (T,H,W) position-id CONSTRUCTION for an image-conditioned
sequence is, per HF's ``Qwen3VLModel.get_rope_index``, the same algorithm as
Qwen2.5-VL's (text spans advance sequentially on all 3 axes; an image span
holds T constant and tiles H/W over the post-merge grid; the position counter
advances by ``max(grid_h, grid_w) // spatial_merge_size`` after an image) — so
``qwen3.py`` reuses ``qwen_vl_vision.qwen25vl_mrope_position_ids`` directly for
Krea-2 instead of duplicating it. What genuinely differs at the LLM level is
the ROTARY EMBEDDING itself: Qwen3-VL's ``mrope_interleaved: true`` splits
T/H/W across the frequency table with a stride-3 interleave rather than three
contiguous chunks — ``qwen3.py``'s ``_interleave_mrope_freqs`` port.
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ._functional import optimized_attention

# Fixed Qwen3-VL-4B vision-tower architecture constants (HF
# Qwen/Qwen3-VL-4B-Instruct config.json's `vision_config`, 2026-07-22). Not
# recoverable from checkpoint tensor shapes alone (mirrors qwen_vl_vision.py's
# own such constants).
VISION_HIDDEN_SIZE = 1024
VISION_INTERMEDIATE_SIZE = 4096
VISION_NUM_LAYERS = 24
VISION_NUM_HEADS = 16
VISION_PATCH_SIZE = 16
VISION_TEMPORAL_PATCH_SIZE = 2
VISION_SPATIAL_MERGE_SIZE = 2
VISION_NUM_POSITION_EMBEDDINGS = 2304
VISION_DEEPSTACK_INDEXES = (5, 11, 17)
VISION_ROTARY_THETA = 10000.0
VISION_OUT_HIDDEN_SIZE = 2560  # == Krea-2's Qwen3-VL-4B LM hidden_size (txtdim)

# Qwen2 vocab special tokens (shared with the Qwen3/Qwen3-VL tokenizer assets;
# identical values to qwen_vl_vision.py's, duplicated here so this module has
# no import-time dependency on the Qwen2.5-VL tower module).
IMAGE_PAD_TOKEN = 151655
VIDEO_PAD_TOKEN = 151656
VISION_START_TOKEN = 151652
VISION_END_TOKEN = 151653

# Fixed Qwen3-VL-32B (MiniMax-H3's text encoder) vision-tower architecture
# constants, from `MiniMaxAI/MiniMax-H3` `text_encoder/config.json`'s
# `vision_config` (fetched 2026-08-10; cross-checked against the Comfy-Org
# trimmed bf16 repack's safetensors header — ai/minimax_h3/te_bf16_header.json
# — hidden_size=1152 and 27 `visual.blocks.*` both confirmed byte-exact).
# `num_heads`/`patch_size`/`temporal_patch_size`/`spatial_merge_size`/
# `num_position_embeddings` are IDENTICAL to the 4B tower's constants above
# (reused directly, no override needed); only hidden/intermediate/depth/
# deepstack differ, so only those get their own constants.
H3_VISION_HIDDEN_SIZE = 1152
H3_VISION_INTERMEDIATE_SIZE = 4304
H3_VISION_NUM_LAYERS = 27
H3_VISION_DEEPSTACK_INDEXES = (8, 16, 24)

# H3's smart-resize pixel-area bounds (`preprocess_qwen3_vl_image`'s
# `min_pixels`/`max_pixels`), from the SAME `text_encoder/preprocessor_config.json`
# fetched 2026-08-10: `size: {"shortest_edge": 65536, "longest_edge": 16777216}`.
# Confirmed by reading the installed `transformers` source
# (`transformers/models/qwen2_vl/image_processing_qwen2_vl.py`:
# `Qwen2VLImageProcessor.size["shortest_edge"/"longest_edge"]` are fed
# straight into `smart_resize(..., min_pixels=, max_pixels=)` as pixel-AREA
# bounds despite the edge-sounding key names — same class the checkpoint's
# `image_processor_type: Qwen2VLImageProcessorFast` names) — NOT assumed from
# the key names alone. Differs from `preprocess_qwen3_vl_image`'s own
# defaults (3136/12845056), which are that SAME class's stock defaults
# (`56*56`/`28*28*1280`) as used by Krea-2's 4B — H3's checkpoint overrides
# them, so a caller preprocessing for H3 must pass these explicitly rather
# than rely on the function's Krea-2-shaped defaults.
H3_VISION_MIN_PIXELS = 65536
H3_VISION_MAX_PIXELS = 16777216

# `preprocessor_config.json`: plain [0,1]-centered normalization, NOT the
# OpenAI-CLIP stats Qwen2.5-VL's processor uses.
_MEAN = (0.5, 0.5, 0.5)
_STD = (0.5, 0.5, 0.5)


def _smart_resize_grid(
    height: int, width: int, *, factor: int, min_pixels: int, max_pixels: int,
) -> tuple[int, int]:
    """The Qwen-VL smart-resize target size for ``(height, width)``: both axes
    rounded to a ``factor`` multiple, then rescaled if the resulting AREA
    falls outside ``[min_pixels, max_pixels]``.

    Shared verbatim by the image and video preprocessors — a video resolves
    ONE grid for its whole stack, so both need exactly this arithmetic and a
    second copy of it is how the two silently drift apart.

    The bounds are applied as a single ``if``/``elif``, not two independent
    clamps: a ``max_pixels`` below ``min_pixels`` therefore wins outright
    rather than producing an empty range (``model_loader/minimax_h3/clip.py``
    clamps its caller-supplied budget UP to the minimum for this reason).
    """
    h_bar = round(height / factor) * factor
    w_bar = round(width / factor) * factor
    if h_bar * w_bar > max_pixels:
        beta = ((height * width) / max_pixels) ** 0.5
        h_bar = max(factor, int(height / beta / factor) * factor)
        w_bar = max(factor, int(width / beta / factor) * factor)
    elif h_bar * w_bar < min_pixels:
        beta = (min_pixels / (height * width)) ** 0.5
        h_bar = -(-int(height * beta) // factor) * factor  # ceil to a factor multiple
        w_bar = -(-int(width * beta) // factor) * factor
    return h_bar, w_bar


def preprocess_qwen3_vl_image(
    image: torch.Tensor,
    *,
    grounding_px: int = 768,
    min_pixels: int = 3136,
    max_pixels: int = 12845056,
    patch_size: int = VISION_PATCH_SIZE,
    temporal_patch_size: int = VISION_TEMPORAL_PATCH_SIZE,
    merge_size: int = VISION_SPATIAL_MERGE_SIZE,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Resize/normalize/patchify one image -> ``(flatten_patches, grid_thw)``.

    ``image`` is ``[H, W, 3]`` float in ``[0, 1]``, same convention as
    :func:`~.qwen_vl_vision.preprocess_qwen_vl_image`.

    ``grounding_px`` (comfyui-krea2edit's ``Krea2EditGroundedEncode`` node):
    caps the LONGEST side sent to the vision encoder — training used 384-768px
    jitter, so upstream documents 640-768 as in-distribution; ``0`` disables
    the cap (native resolution, subject only to the ``min_pixels``/
    ``max_pixels`` smart-resize below). Implemented as a pre-scale of the
    logical (height, width) used to compute the smart-resize target grid —
    the actual pixel resample still happens once, straight from the source
    resolution to that target grid, so this does not introduce a second
    resample pass.
    """
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError(f"expected an [H, W, 3] image tensor, got shape {tuple(image.shape)}")

    height, width, _channels = image.shape
    device = image.device
    img = image.permute(2, 0, 1).float()  # [C, H, W]

    if grounding_px and max(height, width) > grounding_px:
        scale = grounding_px / max(height, width)
        height = max(1, round(height * scale))
        width = max(1, round(width * scale))

    factor = patch_size * merge_size
    h_bar, w_bar = _smart_resize_grid(
        height, width, factor=factor, min_pixels=min_pixels, max_pixels=max_pixels,
    )

    img_resized = F.interpolate(
        img.unsqueeze(0), size=(h_bar, w_bar), mode="bilinear", align_corners=False
    ).squeeze(0)
    normalized = img_resized.clone()
    for c in range(3):
        normalized[c] = (img_resized[c] - _MEAN[c]) / _STD[c]

    grid_h = h_bar // patch_size
    grid_w = w_bar // patch_size
    grid_thw = torch.tensor([[1, grid_h, grid_w]], device=device, dtype=torch.long)

    channels = normalized.shape[0]
    pixel_values = normalized.unsqueeze(0).repeat(temporal_patch_size, 1, 1, 1)
    patches = pixel_values.reshape(
        1, temporal_patch_size, channels,
        grid_h // merge_size, merge_size, patch_size,
        grid_w // merge_size, merge_size, patch_size,
    )
    patches = patches.permute(0, 3, 6, 4, 7, 2, 1, 5, 8)
    flatten_patches = patches.reshape(
        grid_h * grid_w, channels * temporal_patch_size * patch_size * patch_size
    )
    return flatten_patches, grid_thw


# Derived from: `transformers/models/qwen3_vl/video_processing_qwen3_vl.py`
# (`Qwen3VLVideoProcessor.patchify`, Apache-2.0) — the temporal padding
# (`if pad := -num_frames % temporal_patch_size`, repeating the LAST frame)
# and the `view`/`permute`/`reshape` patchify are ported verbatim, with the
# reference's leading batch axis dropped (this port encodes one video per
# call) and its permutation `(0, 1, 4, 7, 5, 8, 3, 2, 6, 9)` re-indexed
# accordingly to `(0, 3, 6, 4, 7, 2, 1, 5, 8)`. Reached from diffusers'
# `modular_pipelines/minimax_h3/encoders.py`
# (`MiniMaxH3Ref2VATextEncoderStep._gather_vision_features`), which calls
# `processor.video_processor(videos=..., do_sample_frames=False)`.
def preprocess_qwen3_vl_video(
    frames: torch.Tensor,
    *,
    min_pixels: int = 3136,
    max_pixels: int = 12845056,
    patch_size: int = VISION_PATCH_SIZE,
    temporal_patch_size: int = VISION_TEMPORAL_PATCH_SIZE,
    merge_size: int = VISION_SPATIAL_MERGE_SIZE,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Resize/normalize/patchify a frame stack -> ``(flatten_patches,
    grid_thw)``, the video counterpart of :func:`preprocess_qwen3_vl_image`.

    ``frames`` is ``[F, H, W, 3]`` float in ``[0, 1]``. Two things differ from
    the image path and both are load-bearing:

    1. The smart-resize target grid is resolved ONCE, from the stack's own
       ``(H, W)`` (:func:`_smart_resize_grid`, shared with the image path),
       and every frame is resampled onto it — a video is one grid, not ``F``
       independent ones.
    2. ``grid_thw`` is ``[[F // temporal_patch_size, grid_h, grid_w]]``, so
       the tower merges CONSECUTIVE DISTINCT frames in groups. The image path
       fakes ``grid_t = 1`` by repeating its single frame
       ``temporal_patch_size`` times; doing that per frame here would pair
       every frame with itself and throw away all motion.

    When ``F`` does not divide ``temporal_patch_size`` the LAST frame is
    repeated to fill the final group, so ``grid_t`` is
    ``ceil(F / temporal_patch_size)`` and NOT ``F // temporal_patch_size``.
    A caller labelling the groups (MiniMax-H3 timestamps each one) has to pad
    its own label list the same way, or the two silently disagree about how
    many groups exist — an off-by-one here produces a correctly-SHAPED tensor
    whose rotary positions are wrong, which no shape assertion catches.

    ``grounding_px`` has no analogue here: neither the reference video
    processor nor MiniMax-H3 caps a video's long side; that cap is Krea-2's.
    """
    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise ValueError(f"expected an [F, H, W, 3] frame stack, got shape {tuple(frames.shape)}")
    if frames.shape[0] < 1:
        raise ValueError("a frame stack must carry at least one frame")

    _num_frames, height, width, _channels = frames.shape
    device = frames.device
    video = frames.permute(0, 3, 1, 2).float()  # [F, C, H, W]

    factor = patch_size * merge_size
    h_bar, w_bar = _smart_resize_grid(
        height, width, factor=factor, min_pixels=min_pixels, max_pixels=max_pixels,
    )

    resized = F.interpolate(video, size=(h_bar, w_bar), mode="bilinear", align_corners=False)
    normalized = resized.clone()
    for c in range(3):
        normalized[:, c] = (resized[:, c] - _MEAN[c]) / _STD[c]

    if pad := -normalized.shape[0] % temporal_patch_size:
        normalized = torch.cat([normalized, normalized[-1:].expand(pad, -1, -1, -1)], dim=0)

    channels = normalized.shape[1]
    grid_t = normalized.shape[0] // temporal_patch_size
    grid_h = h_bar // patch_size
    grid_w = w_bar // patch_size
    patches = normalized.reshape(
        grid_t, temporal_patch_size, channels,
        grid_h // merge_size, merge_size, patch_size,
        grid_w // merge_size, merge_size, patch_size,
    )
    patches = patches.permute(0, 3, 6, 4, 7, 2, 1, 5, 8)
    flatten_patches = patches.reshape(
        grid_t * grid_h * grid_w, channels * temporal_patch_size * patch_size * patch_size
    )
    grid_thw = torch.tensor([[grid_t, grid_h, grid_w]], device=device, dtype=torch.long)
    return flatten_patches, grid_thw


class Qwen3VLVisionPatchEmbed(nn.Module):
    """Conv3d patch embedding, WITH bias (unlike Qwen2.5-VL's) — ``patch_embed.proj.*``."""

    def __init__(
        self, patch_size: int = VISION_PATCH_SIZE, temporal_patch_size: int = VISION_TEMPORAL_PATCH_SIZE,
        in_channels: int = 3, embed_dim: int = VISION_HIDDEN_SIZE,
        operations: Any = None, device=None, dtype=None,
    ) -> None:
        super().__init__()
        self.patch_size = patch_size
        self.temporal_patch_size = temporal_patch_size
        self.in_channels = in_channels
        self.embed_dim = embed_dim
        kernel_size = (temporal_patch_size, patch_size, patch_size)
        self.proj = operations.Conv3d(
            in_channels, embed_dim, kernel_size=kernel_size, stride=kernel_size,
            bias=True, device=device, dtype=dtype,
        )

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        hidden_states = hidden_states.view(
            -1, self.in_channels, self.temporal_patch_size, self.patch_size, self.patch_size
        )
        return self.proj(hidden_states).view(-1, self.embed_dim)


class Qwen3VLVisionRotaryEmbedding(nn.Module):
    """Direct position-id -> frequency map (no lookup table; see module docstring)."""

    def __init__(self, dim: int, theta: float = VISION_ROTARY_THETA) -> None:
        super().__init__()
        self.dim = dim
        self.theta = theta

    def forward(self, position_ids: torch.Tensor, device) -> torch.Tensor:
        inv_freq = 1.0 / (self.theta ** (torch.arange(0, self.dim, 2, dtype=torch.float32, device=device) / self.dim))
        freqs = position_ids.to(torch.float32).unsqueeze(-1) * inv_freq
        return freqs.flatten(1)


def _rotate_half_vision(x: torch.Tensor) -> torch.Tensor:
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2 :]
    return torch.cat((-x2, x1), dim=-1)


def apply_rotary_pos_emb_vision(q, k, cos, sin):
    cos, sin = cos.unsqueeze(-2).float(), sin.unsqueeze(-2).float()
    q_embed = (q * cos) + (_rotate_half_vision(q) * sin)
    k_embed = (k * cos) + (_rotate_half_vision(k) * sin)
    return q_embed, k_embed


def vision_rope_position_ids(grid_thw: torch.Tensor, merge_size: int, device) -> torch.Tensor:
    """Per-patch (h, w) rotary position ids for one image, in merge-adjacent
    sequence order (matching :func:`preprocess_qwen3_vl_image`'s patch
    ordering) — ``transformers/vision_utils.py``'s ``get_vision_position_ids``
    with ``include_temporal=False``."""
    t, h, w = (int(v) for v in grid_thw[0].tolist())
    hpos, wpos = torch.meshgrid(
        torch.arange(h, device=device), torch.arange(w, device=device), indexing="ij"
    )
    block = (h // merge_size, merge_size, w // merge_size, merge_size)
    hpos = hpos.reshape(block).transpose(1, 2).flatten()
    wpos = wpos.reshape(block).transpose(1, 2).flatten()
    return torch.stack([hpos, wpos], dim=-1).repeat(t, 1)  # (h*w, 2)


def vision_bilinear_pos_embed_indices_weights(
    grid_thw: torch.Tensor, num_grid_per_side: int, merge_size: int, device,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Bilinear-interpolation corner indices/weights into the learned
    ``num_grid_per_side x num_grid_per_side`` position-embedding table, in
    merge-adjacent sequence order. Port of
    ``transformers/vision_utils.py``'s ``get_vision_bilinear_indices_and_weights``
    for a single image (``grid_thw`` shape ``[1, 3]``)."""
    side = num_grid_per_side
    t, h, w = (int(v) for v in grid_thw[0].tolist())

    h_grid = torch.linspace(0, side - 1, h, device=device)
    w_grid = torch.linspace(0, side - 1, w, device=device)
    h_floor, w_floor = h_grid.int(), w_grid.int()
    h_ceil = (h_floor + 1).clamp(max=side - 1)
    w_ceil = (w_floor + 1).clamp(max=side - 1)
    h_frac, w_frac = h_grid - h_floor, w_grid - w_floor
    h_floor_off, h_ceil_off = h_floor * side, h_ceil * side

    corner_indices = [
        (h_floor_off[:, None] + w_floor[None, :]).flatten(),
        (h_floor_off[:, None] + w_ceil[None, :]).flatten(),
        (h_ceil_off[:, None] + w_floor[None, :]).flatten(),
        (h_ceil_off[:, None] + w_ceil[None, :]).flatten(),
    ]
    corner_weights = [
        ((1 - h_frac)[:, None] * (1 - w_frac)[None, :]).flatten(),
        ((1 - h_frac)[:, None] * w_frac[None, :]).flatten(),
        (h_frac[:, None] * (1 - w_frac)[None, :]).flatten(),
        (h_frac[:, None] * w_frac[None, :]).flatten(),
    ]

    h_idx = torch.arange(h, device=device).view(h // merge_size, merge_size)
    w_idx = torch.arange(w, device=device).view(w // merge_size, merge_size)
    reorder = (h_idx[:, :, None, None] * w + w_idx[None, None, :, :]).transpose(1, 2).flatten().repeat(t)

    idx = torch.stack([ci[reorder] for ci in corner_indices])
    weight = torch.stack([cw[reorder] for cw in corner_weights])
    return idx, weight


class Qwen3VLVisionPatchMerger(nn.Module):
    """2x2 spatial merge + 2-layer MLP down to ``out_hidden_size``.

    ``use_postshuffle_norm=False`` (the regular merger): LayerNorm at the
    PRE-merge width, then reshape/merge. ``use_postshuffle_norm=True`` (each
    DeepStack tap): reshape/merge FIRST, then LayerNorm at the merged width.
    Checkpoint keys: ``merger.norm.*`` / ``merger.linear_fc1.*`` /
    ``merger.linear_fc2.*`` (regular); ``deepstack_merger_list.N.*`` (taps).
    """

    def __init__(
        self, hidden_size: int, out_hidden_size: int, spatial_merge_size: int, use_postshuffle_norm: bool,
        operations: Any = None, device=None, dtype=None,
    ) -> None:
        super().__init__()
        self.merged_size = hidden_size * (spatial_merge_size ** 2)
        self.use_postshuffle_norm = use_postshuffle_norm
        norm_dim = self.merged_size if use_postshuffle_norm else hidden_size
        self.norm = operations.LayerNorm(norm_dim, eps=1e-6, device=device, dtype=dtype)
        self.linear_fc1 = operations.Linear(self.merged_size, self.merged_size, device=device, dtype=dtype)
        self.linear_fc2 = operations.Linear(self.merged_size, out_hidden_size, device=device, dtype=dtype)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.use_postshuffle_norm:
            x = self.norm(x.view(-1, self.merged_size))
        else:
            x = self.norm(x).view(-1, self.merged_size)
        return self.linear_fc2(F.gelu(self.linear_fc1(x)))


class Qwen3VLVisionAttention(nn.Module):
    """Fused-qkv full (non-windowed) self-attention over one image's patches."""

    def __init__(self, hidden_size: int, num_heads: int, operations: Any = None, device=None, dtype=None) -> None:
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = hidden_size // num_heads
        self.qkv = operations.Linear(hidden_size, hidden_size * 3, bias=True, device=device, dtype=dtype)
        self.proj = operations.Linear(hidden_size, hidden_size, bias=True, device=device, dtype=dtype)

    def forward(self, hidden_states: torch.Tensor, position_embeddings) -> torch.Tensor:
        seq_length, _ = hidden_states.shape
        qkv = self.qkv(hidden_states).reshape(seq_length, 3, self.num_heads, self.head_dim)
        q, k, v = qkv.permute(1, 0, 2, 3).unbind(0)  # each [S, heads, dim]

        cos, sin = position_embeddings
        q, k = apply_rotary_pos_emb_vision(q, k, cos, sin)

        q = q.transpose(0, 1).unsqueeze(0)  # [1, heads, S, dim]
        k = k.transpose(0, 1).unsqueeze(0)
        v = v.transpose(0, 1).unsqueeze(0)

        out = optimized_attention(q, k, v, self.num_heads, skip_reshape=True)  # [1, S, hidden]
        return self.proj(out.reshape(seq_length, -1))


class Qwen3VLVisionMLP(nn.Module):
    """Plain 2-layer GELU-tanh MLP (not SwiGLU) — ``linear_fc1``/``linear_fc2``."""

    def __init__(self, hidden_size: int, intermediate_size: int, operations: Any = None, device=None, dtype=None) -> None:
        super().__init__()
        self.linear_fc1 = operations.Linear(hidden_size, intermediate_size, bias=True, device=device, dtype=dtype)
        self.linear_fc2 = operations.Linear(intermediate_size, hidden_size, bias=True, device=device, dtype=dtype)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.linear_fc2(F.gelu(self.linear_fc1(x), approximate="tanh"))


class Qwen3VLVisionBlock(nn.Module):
    def __init__(self, hidden_size: int, intermediate_size: int, num_heads: int,
                 operations: Any = None, device=None, dtype=None) -> None:
        super().__init__()
        self.norm1 = operations.LayerNorm(hidden_size, eps=1e-6, device=device, dtype=dtype)
        self.norm2 = operations.LayerNorm(hidden_size, eps=1e-6, device=device, dtype=dtype)
        self.attn = Qwen3VLVisionAttention(hidden_size, num_heads, operations=operations, device=device, dtype=dtype)
        self.mlp = Qwen3VLVisionMLP(hidden_size, intermediate_size, operations=operations, device=device, dtype=dtype)

    def forward(self, hidden_states: torch.Tensor, position_embeddings) -> torch.Tensor:
        hidden_states = hidden_states + self.attn(self.norm1(hidden_states), position_embeddings)
        hidden_states = hidden_states + self.mlp(self.norm2(hidden_states))
        return hidden_states


class Qwen3VLVisionTower(nn.Module):
    """Top-level Qwen3-VL vision tower: patch embed + learned/rotary position
    embeddings -> full-attention blocks -> merger (+ DeepStack taps).

    Checkpoint prefix is ``model.visual.*`` for Krea-2's 4B (nested under the
    LLM's ``model.*``, UNLIKE Qwen2.5-VL's top-level ``visual.*``) — this
    module is then built as an attribute of :class:`~.qwen3._Qwen3Transformer`
    (assigned ``self.model`` by :class:`~.qwen3.Qwen3Model`), not of
    ``Qwen3Model`` itself, so its own state-dict keys resolve to that prefix
    automatically. MiniMax-H3's 32B checkpoint carries the tower TOP-LEVEL
    instead, ``visual.*`` (like Qwen2.5-VL's) — for that width the module is
    built as an attribute of ``Qwen3Model`` itself. Which attachment point is
    used is ``Qwen3Config.vision_top_level``, set from detection's
    ``vision_top_level`` (te_detect.py); see ``qwen3.py``'s
    ``_build_vision_tower``/``Qwen3Model.__init__``.

    ``forward(pixel_values, grid_thw)`` -> ``(merged, deepstack_features)``:
    ``merged`` is ``[num_merged_tokens, out_hidden_size]`` (spliced into the
    LLM's input embeddings in place of the image's ``<|image_pad|>`` slot);
    ``deepstack_features`` is a list of ``len(deepstack_indexes)`` tensors,
    each also ``[num_merged_tokens, out_hidden_size]``, additively injected
    into the LLM's first ``len(deepstack_indexes)`` decoder layers at the
    image-token positions (``qwen3.py``'s job, not this module's).
    """

    def __init__(
        self,
        hidden_size: int = VISION_HIDDEN_SIZE,
        out_hidden_size: int = VISION_OUT_HIDDEN_SIZE,
        intermediate_size: int = VISION_INTERMEDIATE_SIZE,
        num_heads: int = VISION_NUM_HEADS,
        num_layers: int = VISION_NUM_LAYERS,
        patch_size: int = VISION_PATCH_SIZE,
        temporal_patch_size: int = VISION_TEMPORAL_PATCH_SIZE,
        spatial_merge_size: int = VISION_SPATIAL_MERGE_SIZE,
        num_position_embeddings: int = VISION_NUM_POSITION_EMBEDDINGS,
        deepstack_indexes: tuple[int, ...] = VISION_DEEPSTACK_INDEXES,
        operations: Any = None,
        device=None,
        dtype=None,
    ) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.patch_size = patch_size
        self.spatial_merge_size = spatial_merge_size
        self.num_position_embeddings = num_position_embeddings
        self.num_grid_per_side = round(num_position_embeddings ** 0.5)
        self.deepstack_indexes = list(deepstack_indexes)

        self.patch_embed = Qwen3VLVisionPatchEmbed(
            patch_size=patch_size, temporal_patch_size=temporal_patch_size, in_channels=3,
            embed_dim=hidden_size, operations=operations, device=device, dtype=dtype,
        )
        self.pos_embed = operations.Embedding(num_position_embeddings, hidden_size, device=device, dtype=dtype)
        head_dim = hidden_size // num_heads
        self.rotary_pos_emb = Qwen3VLVisionRotaryEmbedding(head_dim // 2)
        self.blocks = nn.ModuleList([
            Qwen3VLVisionBlock(hidden_size, intermediate_size, num_heads, operations=operations, device=device, dtype=dtype)
            for _ in range(num_layers)
        ])
        self.merger = Qwen3VLVisionPatchMerger(
            hidden_size, out_hidden_size, spatial_merge_size, use_postshuffle_norm=False,
            operations=operations, device=device, dtype=dtype,
        )
        self.deepstack_merger_list = nn.ModuleList([
            Qwen3VLVisionPatchMerger(
                hidden_size, out_hidden_size, spatial_merge_size, use_postshuffle_norm=True,
                operations=operations, device=device, dtype=dtype,
            )
            for _ in self.deepstack_indexes
        ])

    def forward(self, pixel_values: torch.Tensor, grid_thw: torch.Tensor) -> tuple[torch.Tensor, list[torch.Tensor]]:
        device = pixel_values.device
        hidden_states = self.patch_embed(pixel_values)

        bilinear_idx, bilinear_w = vision_bilinear_pos_embed_indices_weights(
            grid_thw, self.num_grid_per_side, self.spatial_merge_size, device
        )
        pos_embeds = (self.pos_embed(bilinear_idx) * bilinear_w[:, :, None]).sum(0)
        hidden_states = hidden_states + pos_embeds.to(hidden_states.dtype)

        position_ids = vision_rope_position_ids(grid_thw, self.spatial_merge_size, device)
        rotary = self.rotary_pos_emb(position_ids, device)  # (seq, head_dim//2)
        emb = torch.cat((rotary, rotary), dim=-1)
        position_embeddings = (emb.cos(), emb.sin())

        deepstack_features: list[torch.Tensor] = []
        for i, block in enumerate(self.blocks):
            hidden_states = block(hidden_states, position_embeddings)
            if i in self.deepstack_indexes:
                j = self.deepstack_indexes.index(i)
                deepstack_features.append(self.deepstack_merger_list[j](hidden_states))

        merged = self.merger(hidden_states)
        return merged, deepstack_features
