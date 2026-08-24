# Derived from: diffusers `modular_pipelines/minimax_h3/before_denoise.py`
# (Apache-2.0, "Copyright 2026 The MiniMax and HuggingFace Teams") --
# `patchify_video_latents`, `_spatial_position_grid`, `_temporal_position_grid`,
# `_frame_position_grid`, `MiniMaxH3PrepareLayoutStep.build_packed_sequence`,
# `MiniMaxH3SetTimestepsStep.build_row_timesteps` and
# `MiniMaxH3Ref2VAPrepareLayoutStep.build_ref2va_packed_sequence` are ported
# near-verbatim (module-level functions here instead of `ModularPipelineBlocks`
# methods/staticmethods). `_fill_audio_positions` is ported verbatim.
"""MiniMax-H3 packed-sequence layout: the `(t, h, w)` rotary grid and the
row-index/modality-tag bookkeeping every other part of the generator reads.

The transformer runs full self-attention over ONE packed 1-D sequence holding
text rows, keyframe-conditioning rows, audio rows and target video rows, in
that fixed order (dossier §A.8):

    [ text | condition audio | keyframe conditions | target audio | target video ]

`condition audio` is empty (`num_condition_audio_latents=0`) for a plain
`t2va`/`fl2va` request, which reduces the layout to the reference's own
`[text | keyframe conditions | target audio | target video]`.

**The condition-audio block moves the target's rotary origin.** Condition
audio is a PREFIX ON THE SHARED CLOCK, not an overlay: it occupies
`num_text_tokens … num_text_tokens + num_condition_audio_latents - 1` and the
target audio, the target video and every keyframe anchor start after it, at
`media_rotary_origin`. That is the reference's own `ref2va` rule -- an audio
reference fills at the running `rotary_time` and then does
`rotary_time += float(reference_audio_latents)`, and the generated rows take
whatever origin the reference blocks left behind. Keyframe anchors, by
contrast, are OVERLAYS: `"first"` is exactly the target's frame-0 time, so it
tracks `media_rotary_origin` rather than the text length.

Traps preserved verbatim from the reference (each is dossier-documented and
silently wrong if skipped):

1. `position_ids` is float64 throughout -- only cast to fp32 inside the arch
   module's own RoPE projection. Every helper below returns/accumulates
   float64.
2. `_spatial_position_grid` is built with `np.linspace(..., endpoint=False)`,
   which is `start + arange(num) * (stop - start) / num` -- NOT what
   `torch.linspace` computes. Reproduced via `numpy` deliberately, not
   translated to `torch.linspace`.
3. Temporal spacing is non-uniform: `5/3 * (1, 4, 4, 4, 4)` per latent frame,
   cycling.
4. The `"last"` keyframe anchor sums that per-latent-frame span series with
   NUMPY'S PAIRWISE summation (`np.ones(...).sum()`), which differs from a
   plain sequential Python/`torch` sum in the last ulp from 16 latent frames
   onward. `_pairwise_span_sum` exists so this exact algorithm survives the
   port -- do not "simplify" it to `sum()`/`.cumsum()[-1]`.
5. A keyframe's vision-block TEXT rows are tagged `video_tag` (0), not
   `text_tag` (1) -- the caller's text-encoder wrapper is responsible for
   presenting this in `text_token_tags` (see `model_loader/minimax_h3/clip.py`);
   this module only consumes that tensor, it does not construct it.
6. `build_ref2va_packed_sequence`'s video-reference `video_span` (how far a
   VIDEO reference advances the shared clock) is Python's builtin `sum()`
   over the span series -- the reference's own algorithm, reproduced
   verbatim -- which is a DIFFERENT algorithm from `_pairwise_span_sum`'s
   numpy pairwise summation: the two disagree in the last ulp from 16 latent
   frames onward (trap 4), and the reference keeps both, one per call site.
   Do not consolidate them. NOTE for anyone testing this: `sum()` over
   floats is not a naive term-by-term accumulation on Python 3.12+ (it uses
   compensated/Neumaier summation internally), so an "independent reference"
   for this trap has to call `sum()` itself, not reimplement a manual loop --
   a manual loop silently diverges from `sum()`'s own result.

**`ref2va` layout.** `build_ref2va_packed_sequence` builds the sibling layout
`[text | reference blocks | target audio | target video]` (dossier §A.8 /
`MiniMaxH3Ref2VAPrepareLayoutStep`): one block per entry of an ordered
`references` list, each a PREFIX that advances the same shared `rotary_time`
clock `build_packed_sequence`'s condition-audio block does, rather than an
OVERLAY the way a keyframe anchor is. Per reference kind:

- `"image"`: one block at the running `rotary_time`, which then advances by
  EXACTLY `1.0` regardless of that image's own latent-frame count (an image
  is a single rotary instant, not `5/3` units of video time).
- `"audio"`: one block at `rotary_time`, which then advances by
  `float(reference_audio_latents)` -- identical to `build_packed_sequence`'s
  own condition-audio block, reused via the same `_fill_audio_positions`.
- `"video"`: its soundtrack rows are packed immediately BEFORE its video rows
  and share their origin (rotary-aligned exactly like the target audio/video
  are), pinned to the reference's OWN width grid (not the target's -- unlike
  a standalone `"audio"` reference or the target audio block, which both pin
  to the target width grid); `rotary_time` then advances by
  `max(reference_audio_latents, video_span)` (trap 6 above).

The generated audio/video rows share whatever origin the reference loop left
behind, in `PackedLayout.media_rotary_origin` -- structurally the same field
`build_packed_sequence` returns, just computed by a different loop.
`condition_latents`/`audio_condition_latents` are consumed as ITERATORS
alongside `references`, one entry per image/video and per audio-bearing
reference respectively (never indexed), because references are encoded at
their own resolutions and a video reference's soundtrack and vision blocks
each draw from a different iterator.

**Device.** `build_packed_sequence`/`build_row_timesteps` build EVERY internal
tensor directly on an explicit `device` -- never bare `torch.zeros`/`torch.
arange`/`torch.full` defaulting to CPU -- and coerce every caller-supplied
tensor (`text_token_tags`, `video_indices`, `audio_indices`) onto that same
device before use, rather than assuming they already match. `prompt_encoder`
runs on CPU while the generator's own sampler state lives on the generation
device; a caller-supplied tensor that had already been moved to that device
(as `generator/video_minimax_h3/main.py` does for `text_token_tags` before
calling `build_packed_sequence`) previously hit a bare-CPU internal tensor
here and raised `RuntimeError: ... cuda:0 vs cpu` on a real GPU run -- fixed
once, at the source, rather than patched at every call site.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch

# One shared audio/video rotary clock: 40 Hz audio latents, 24 fps video --
# 5/3 rotary units per pixel-frame-second. `(1, 4, 4, 4, 4)` mirrors the video
# VAE's 17-pixel-frame -> 5-latent-frame chunking (dossier §A.3/§A.8).
ROPE_FRAME_RESCALE = 5.0 / 3.0
ROPE_FRAMES_PER_LATENT: tuple[int, ...] = (1, 4, 4, 4, 4)
ROPE_SPATIAL_SCALE = 32

# Per-row modality tags -- index the transformer's AdaLN table
# (`timestep_indices * MINIMAX_H3_MODALITY_NUM + token_tags`), so these values
# are a checkpoint contract, not a free choice.
VIDEO_TAG = 0
TEXT_TAG = 1
AUDIO_TAG = 2

Tensor = torch.Tensor


def patchify_video_latents(latents: Tensor, patch_size: tuple[int, int, int]) -> Tensor:
    """`(B, C, F, H, W)` -> `(B * num_patches, C * prod(patch_size))`, rows
    ordered frame-major then row-major, channel the SLOWEST axis inside a
    patch (dossier §A.8's ``patchify_video_latents``)."""
    patch_t, patch_h, patch_w = patch_size
    b, c, f, h, w = latents.shape
    if f % patch_t or h % patch_h or w % patch_w:
        raise ValueError(f"latents of shape {tuple(latents.shape)} are not divisible by patch {patch_size}")
    latents = latents.reshape(b, c, f // patch_t, patch_t, h // patch_h, patch_h, w // patch_w, patch_w)
    latents = latents.permute(0, 2, 4, 6, 1, 3, 5, 7)
    return latents.reshape(-1, c * patch_t * patch_h * patch_w).contiguous()


def unpatchify_video_rows(
    rows: Tensor, *, num_latent_frames: int, latent_height: int, latent_width: int,
    channels: int, patch_size: tuple[int, int, int],
) -> Tensor:
    """Inverse of :func:`patchify_video_latents` -> `(1, C, F, H, W)`."""
    patch_t, patch_h, patch_w = patch_size
    rows = rows.reshape(
        -1, num_latent_frames // patch_t, latent_height // patch_h, latent_width // patch_w,
        channels, patch_t, patch_h, patch_w,
    )
    rows = rows.permute(0, 4, 1, 5, 2, 6, 3, 7)
    return rows.reshape(-1, channels, num_latent_frames, latent_height, latent_width).contiguous()


def _spatial_position_grid(dim: int, patch: int, sqrt_area: float, device: torch.device) -> Tensor:
    """One aspect-normalized spatial rotary axis, float64, right endpoint
    excluded (a square canvas spans `[0, 32)`). Built with `numpy` on
    purpose -- see module docstring, trap 2. The `numpy` math itself always
    runs on the host CPU (numpy has no device concept); only the resulting
    tensor is placed on `device`."""
    ratio = dim / sqrt_area
    left = (1.0 - ratio) / 2.0
    grid = np.linspace(left, left + ratio, dim // patch, endpoint=False) * ROPE_SPATIAL_SCALE
    return torch.from_numpy(grid).to(device=device, dtype=torch.float64)


def _temporal_position_grid(num_latent_frames: int, origin: float, device: torch.device) -> Tensor:
    """Rotary time of every latent frame starting at `origin`, spacing
    `5/3 * (1, 4, 4, 4, 4)` cycling (non-uniform, NOT `origin + arange(n)`)."""
    spans = torch.tensor(
        [
            ROPE_FRAME_RESCALE * ROPE_FRAMES_PER_LATENT[index % len(ROPE_FRAMES_PER_LATENT)]
            for index in range(num_latent_frames)
        ],
        dtype=torch.float64, device=device,
    )
    return origin + torch.cat([torch.zeros(1, dtype=torch.float64, device=device), spans[:-1].cumsum(0)])


def _frame_position_grid(
    latent_height: int, latent_width: int, patch_h: int, patch_w: int, device: torch.device,
) -> tuple[Tensor, Tensor]:
    """`(h, w)` rotary coordinates of one latent frame (`(rows_per_frame, 2)`),
    and the width axis they were built from (stereo audio pins to its
    extremes -- see `_fill_target_audio_positions`)."""
    sqrt_area = np.sqrt(latent_height * latent_width)
    height_grid = _spatial_position_grid(latent_height, patch_h, sqrt_area, device)
    width_grid = _spatial_position_grid(latent_width, patch_w, sqrt_area, device)
    grids = torch.meshgrid(height_grid, width_grid, indexing="ij")
    return torch.stack([grid.reshape(-1) for grid in grids], dim=-1), width_grid


def _pairwise_span_sum(num_latent_frames: int) -> float:
    """The rotary time the generated video frames span, summed with NUMPY'S
    PAIRWISE summation -- reproduces `np.ones(n, float64) * rescale` scaled
    per-offset then `.sum()`, which the reference uses for the `"last"`
    keyframe anchor specifically (dossier §A.8 trap 4: this differs from a
    sequential sum in the last ulp from 16 latent frames onward -- do not
    replace with `sum()`/`torch.cumsum(...)[-1]`)."""
    spans = np.ones(num_latent_frames, dtype=np.float64) * ROPE_FRAME_RESCALE
    for offset in range(len(ROPE_FRAMES_PER_LATENT)):
        spans[offset :: len(ROPE_FRAMES_PER_LATENT)] *= ROPE_FRAMES_PER_LATENT[offset]
    return float(spans.sum())


def _fill_audio_positions(
    position_ids: Tensor, rows: slice, num_audio_latents: int, rotary_time: float,
    width_grid: Tensor, audio_channels: int, device: torch.device,
) -> None:
    """Place one channel-major audio block: no height coordinate, L pinned to
    `width_grid[0]`, R pinned to `width_grid[-1]` -- the width extreme is the
    ONLY thing that distinguishes stereo channels (dossier §A.8 trap 5).

    Serves the target block and the condition block alike; the reference pins
    a standalone audio block to the TARGET width grid (only a video
    reference's own soundtrack uses that reference's grid), so both callers
    here pass the target grid.
    """
    time = rotary_time + torch.arange(num_audio_latents, dtype=torch.float64, device=device)
    position_ids[rows, 0] = time.repeat(audio_channels)
    position_ids[rows, 2] = torch.cat([
        torch.full((num_audio_latents,), float(width_grid[0]), dtype=torch.float64, device=device),
        torch.full((num_audio_latents,), float(width_grid[-1]), dtype=torch.float64, device=device),
    ])


KeyframeAnchor = str | int


def _keyframe_anchor_time(
    anchor: KeyframeAnchor, *, num_latent_frames: int, media_origin: float, temporal_grid: Tensor,
) -> float:
    """Rotary time of one keyframe conditioning block.

    `"first"`/`"last"` are the reference's own two anchors. `"last"` is NOT
    `temporal_grid[-1]`: it sums the per-latent-frame span series with numpy's
    PAIRWISE summation, which diverges from the sequential `cumsum`
    `_temporal_position_grid` builds in the last ulp from 16 latent frames
    onward (module docstring, trap 4). An integer `k` addresses latent frame
    `k` through that grid, so `0` and `"first"` agree bit-for-bit while
    `num_latent_frames - 1` and `"last"` deliberately need not.
    """
    if isinstance(anchor, bool):
        raise ValueError(f"a keyframe anchor must be 'first', 'last' or a latent-frame index, got {anchor!r}")
    if isinstance(anchor, int):
        if not 0 <= anchor < num_latent_frames:
            raise ValueError(
                f"a keyframe anchor index must be in [0, {num_latent_frames}), got {anchor}"
            )
        return float(temporal_grid[anchor])
    if anchor == "first":
        return media_origin
    if anchor == "last":
        return media_origin + _pairwise_span_sum(num_latent_frames) - ROPE_FRAME_RESCALE
    raise ValueError(f"a keyframe anchor must be 'first', 'last' or a latent-frame index, got {anchor!r}")


@dataclass(frozen=True)
class PackedLayout:
    """The `t2va`/`fl2va` packed-sequence layout: rotary grid + row indices.

    `token_tags`/`position_ids`/the three `*_indices` tensors are exactly the
    transformer's own `forward()` parameter names (dossier §A.2) -- a caller
    forwards this dataclass's fields by name.
    """

    position_ids: Tensor           # (seq_len, 3) float64
    token_tags: Tensor             # (seq_len,) long
    video_indices: Tensor          # (num_condition_video_rows + num_video_rows,) long
    audio_indices: Tensor          # (num_audio_rows,) long
    text_indices: Tensor           # (num_text_tokens,) long
    num_condition_video_rows: int
    num_condition_audio_rows: int
    media_rotary_origin: float     # rotary time the TARGET audio/video start at


def build_packed_sequence(
    text_token_tags: Tensor,
    *,
    num_latent_frames: int,
    latent_height: int,
    latent_width: int,
    num_audio_latents: int,
    patch_size: tuple[int, int, int],
    audio_channels: int = 2,
    keyframe_anchors: tuple[KeyframeAnchor, ...] = (),
    num_condition_audio_latents: int = 0,
    device: torch.device | str | None = None,
) -> PackedLayout:
    """Build the `[text | condition audio | keyframe conditions | target audio
    | target video]` layout used by `t2va` (`keyframe_anchors=()`) and `fl2va`.

    `text_token_tags`: `(num_text_tokens,)` modality tag of every text row --
    `TEXT_TAG` except a keyframe's own vision-block rows, which the TE tags
    `VIDEO_TAG` (dossier §A.8 trap 6 -- this module only consumes the tensor).

    `keyframe_anchors`: one entry per keyframe conditioning block, laid out in
    the order given -- `"first"`, `"last"`, or an integer latent-frame index
    `0 <= k < num_latent_frames` addressing that frame's own rotary time.

    `num_condition_audio_latents`: clean audio latents PER CHANNEL prepended
    ahead of the target, `audio_channels` rows each (so
    `num_condition_audio_rows = num_condition_audio_latents * audio_channels`,
    the count `audio.encode_audio_condition` produces rows for). They push the
    target's rotary origin to `media_rotary_origin`; see the module
    docstring's clock section.

    Condition rows are a contiguous PREFIX of their own stream --
    `video_indices[:num_condition_video_rows]` and
    `audio_indices[:num_condition_audio_rows]` -- which is what
    `build_row_timesteps` pins and what the sampler leaves untouched when it
    writes back `rows[num_condition_*:]`.

    `device` (default `None` -> `text_token_tags.device`): every tensor this
    function builds -- and `text_token_tags` itself, coerced explicitly
    rather than assumed to already match -- lands on this ONE device. Do not
    assume a caller-supplied tensor already agrees with an internal
    `torch.zeros`/`torch.arange`/`torch.full` default (bare CPU); see the
    module docstring's "Device" section for the crash this caused.
    """
    if num_condition_audio_latents < 0:
        raise ValueError(f"num_condition_audio_latents must not be negative, got {num_condition_audio_latents}")

    device = torch.device(device) if device is not None else text_token_tags.device
    text_token_tags = text_token_tags.to(device=device, dtype=torch.long)

    _, patch_h, patch_w = patch_size
    rows_per_frame = (latent_height // patch_h) * (latent_width // patch_w)
    num_text_tokens = int(text_token_tags.shape[0])
    num_condition_audio_rows = num_condition_audio_latents * audio_channels
    num_condition_rows = len(keyframe_anchors) * rows_per_frame
    num_audio_rows = num_audio_latents * audio_channels
    num_video_rows = num_latent_frames * rows_per_frame
    sequence_length = (
        num_text_tokens + num_condition_audio_rows + num_condition_rows + num_audio_rows + num_video_rows
    )

    condition_audio_start = num_text_tokens
    condition_start = condition_audio_start + num_condition_audio_rows
    audio_start = condition_start + num_condition_rows
    video_start = audio_start + num_audio_rows
    media_origin = float(num_text_tokens) + float(num_condition_audio_latents)

    position_ids = torch.zeros(sequence_length, 3, dtype=torch.float64, device=device)
    position_ids[:num_text_tokens, 0] = torch.arange(num_text_tokens, dtype=torch.float64, device=device)

    frame_grid, width_grid = _frame_position_grid(latent_height, latent_width, patch_h, patch_w, device)

    if num_condition_audio_rows:
        _fill_audio_positions(
            position_ids, slice(condition_audio_start, condition_start), num_condition_audio_latents,
            float(num_text_tokens), width_grid, audio_channels, device,
        )

    temporal_grid = _temporal_position_grid(num_latent_frames, media_origin, device)

    for index, anchor in enumerate(keyframe_anchors):
        anchor_time = _keyframe_anchor_time(
            anchor, num_latent_frames=num_latent_frames, media_origin=media_origin, temporal_grid=temporal_grid,
        )
        rows = slice(condition_start + index * rows_per_frame, condition_start + (index + 1) * rows_per_frame)
        position_ids[rows, 0] = anchor_time
        position_ids[rows, 1:] = frame_grid

    _fill_audio_positions(
        position_ids, slice(audio_start, video_start), num_audio_latents,
        media_origin, width_grid, audio_channels, device,
    )

    video_position_ids = torch.empty(num_latent_frames, rows_per_frame, 3, dtype=torch.float64, device=device)
    video_position_ids[:, :, 0] = temporal_grid[:, None]
    video_position_ids[:, :, 1:] = frame_grid[None]
    position_ids[video_start:] = video_position_ids.reshape(-1, 3)

    video_indices = torch.cat([
        torch.arange(condition_start, audio_start, device=device),
        torch.arange(video_start, sequence_length, device=device),
    ])
    audio_indices = torch.cat([
        torch.arange(condition_audio_start, condition_start, device=device),
        torch.arange(audio_start, video_start, device=device),
    ])
    text_indices = torch.arange(num_text_tokens, device=device)

    token_tags = torch.empty(sequence_length, dtype=torch.long, device=device)
    token_tags[text_indices] = text_token_tags
    token_tags[audio_indices] = AUDIO_TAG
    token_tags[video_indices] = VIDEO_TAG

    return PackedLayout(
        position_ids=position_ids, token_tags=token_tags,
        video_indices=video_indices, audio_indices=audio_indices, text_indices=text_indices,
        num_condition_video_rows=num_condition_rows, num_condition_audio_rows=num_condition_audio_rows,
        media_rotary_origin=media_origin,
    )


@dataclass(frozen=True)
class ReferenceBlock:
    """One `ref2va` reference's kind, for `build_ref2va_packed_sequence`'s
    per-block loop -- mirrors diffusers' `MiniMaxH3Reference.kind`/`.has_audio`
    (`references.py`) without importing its media-holding subclasses (the
    media itself lives in `condition_latents`/`audio_condition_latents`,
    consumed as iterators; this dataclass only carries what the loop branches
    on). `"video"`/`"audio"` are ported so a future caller can use them
    without reworking the layout -- this pipe only ever constructs
    `kind="image"` today.
    """

    kind: str  # "image" | "video" | "audio"
    has_audio: bool = False


def build_ref2va_packed_sequence(
    text_token_tags: Tensor,
    references: tuple[ReferenceBlock, ...] | list[ReferenceBlock],
    condition_latents: tuple[Tensor, ...] | list[Tensor],
    audio_condition_latents: tuple[Tensor, ...] | list[Tensor],
    *,
    num_latent_frames: int,
    latent_height: int,
    latent_width: int,
    num_audio_latents: int,
    patch_size: tuple[int, int, int],
    audio_channels: int = 2,
    device: torch.device | str | None = None,
) -> PackedLayout:
    """Build the `[text | reference blocks | target audio | target video]`
    layout used by `ref2va` (dossier §A.8, `MiniMaxH3Ref2VAPrepareLayoutStep.
    build_ref2va_packed_sequence`) -- see the module docstring's "`ref2va`
    layout" section for the per-kind clock-advance rules.

    `text_token_tags`: as `build_packed_sequence` -- `TEXT_TAG` except a
    reference's own vision-block rows, which the TE tags `VIDEO_TAG`.

    `references`: one entry per reference, in packed order -- the same order
    that fixed each reference's label in the presentation and its position on
    the shared rotary clock (a different order is a different request).

    `condition_latents`: one `(1, channels, num_latent_frames_, height,
    width)` tensor per IMAGE and VIDEO reference, in packed order -- the
    geometry every such block's rows are built from, so it can never disagree
    with what was actually encoded. `audio_condition_latents`: one
    `(num_audio_latents * audio_channels, channels)` tensor per AUDIO-BEARING
    reference (a standalone `"audio"` reference, or a `"video"` reference with
    `has_audio=True`), in packed order. Both are consumed as ITERATORS
    alongside `references` rather than indexed by it, because they skip the
    references they do not apply to.

    `device` (default `None` -> `text_token_tags.device`): see
    `build_packed_sequence`'s docstring "Device" section -- the same
    discipline applies here.
    """
    device = torch.device(device) if device is not None else text_token_tags.device
    text_token_tags = text_token_tags.to(device=device, dtype=torch.long)

    _, patch_h, patch_w = patch_size
    num_text_tokens = int(text_token_tags.shape[0])
    rows_per_frame = (latent_height // patch_h) * (latent_width // patch_w)
    num_target_video_rows = num_latent_frames * rows_per_frame
    num_target_audio_rows = num_audio_latents * audio_channels

    visual_geometry = iter(tuple(latents.shape[2:5]) for latents in condition_latents)
    audio_row_counts = iter(int(rows.shape[0]) for rows in audio_condition_latents)
    num_reference_video_rows = sum(
        frames * (height // patch_h) * (width // patch_w)
        for frames, height, width in (tuple(latents.shape[2:5]) for latents in condition_latents)
    )
    num_reference_audio_rows = sum(int(rows.shape[0]) for rows in audio_condition_latents)
    sequence_length = (
        num_text_tokens + num_reference_video_rows + num_reference_audio_rows
        + num_target_audio_rows + num_target_video_rows
    )

    position_ids = torch.zeros(sequence_length, 3, dtype=torch.float64, device=device)
    position_ids[:num_text_tokens, 0] = torch.arange(num_text_tokens, dtype=torch.float64, device=device)
    target_frame_grid, target_width_grid = _frame_position_grid(latent_height, latent_width, patch_h, patch_w, device)

    video_index_blocks: list[Tensor] = []
    audio_index_blocks: list[Tensor] = []
    cursor = num_text_tokens
    rotary_time = float(num_text_tokens)
    for reference in references:
        if reference.kind == "image":
            num_latent_frames_, reference_height, reference_width = next(visual_geometry)
            num_video_rows = num_latent_frames_ * (reference_height // patch_h) * (reference_width // patch_w)
            rows = slice(cursor, cursor + num_video_rows)
            cursor = rows.stop
            video_index_blocks.append(torch.arange(rows.start, rows.stop, device=device))
            frame_grid, _ = _frame_position_grid(reference_height, reference_width, patch_h, patch_w, device)
            position_ids[rows, 0] = rotary_time
            position_ids[rows, 1:] = frame_grid
            # An image is a single frame and takes a single integer rotary
            # slot, not a latent frame's 5/3 units.
            rotary_time += 1.0
        elif reference.kind == "audio":
            num_audio_rows = next(audio_row_counts)
            reference_audio_latents = num_audio_rows // audio_channels
            rows = slice(cursor, cursor + num_audio_rows)
            cursor = rows.stop
            audio_index_blocks.append(torch.arange(rows.start, rows.stop, device=device))
            _fill_audio_positions(
                position_ids, rows, reference_audio_latents, rotary_time, target_width_grid, audio_channels, device,
            )
            rotary_time += float(reference_audio_latents)
        elif reference.kind == "video":
            # A video reference's soundtrack rows are packed immediately
            # before its video rows and share their origin, so the two are
            # rotary-aligned exactly as the generated audio and video are.
            num_audio_rows = next(audio_row_counts) if reference.has_audio else 0
            reference_audio_latents = num_audio_rows // audio_channels
            num_latent_frames_, reference_height, reference_width = next(visual_geometry)
            num_video_rows = num_latent_frames_ * (reference_height // patch_h) * (reference_width // patch_w)
            audio_rows = slice(cursor, cursor + num_audio_rows)
            video_rows = slice(audio_rows.stop, audio_rows.stop + num_video_rows)
            cursor = video_rows.stop
            audio_index_blocks.append(torch.arange(audio_rows.start, audio_rows.stop, device=device))
            video_index_blocks.append(torch.arange(video_rows.start, video_rows.stop, device=device))

            frame_grid, width_grid = _frame_position_grid(reference_height, reference_width, patch_h, patch_w, device)
            _fill_audio_positions(
                position_ids, audio_rows, reference_audio_latents, rotary_time, width_grid, audio_channels, device,
            )
            frame_time = _temporal_position_grid(num_latent_frames_, rotary_time, device)
            position_ids[video_rows, 0] = frame_time.repeat_interleave(frame_grid.shape[0])
            position_ids[video_rows, 1:] = frame_grid.repeat(num_latent_frames_, 1)
            # The rotary time this reference advances the clock by, via
            # Python's builtin `sum()` -- deliberately not `_pairwise_span_
            # sum` (module docstring trap 6): the two disagree in the last
            # ulp from 16 latent frames on, and the reference keeps both.
            video_span = sum(
                ROPE_FRAME_RESCALE * ROPE_FRAMES_PER_LATENT[index % len(ROPE_FRAMES_PER_LATENT)]
                for index in range(num_latent_frames_)
            )
            rotary_time += max(float(reference_audio_latents), video_span)
        else:
            raise ValueError(f"a reference must be 'image', 'video' or 'audio', got {reference.kind!r}")

    # The generated rows. Target audio and target video share the origin the
    # reference blocks left behind.
    audio_start = cursor
    video_start = audio_start + num_target_audio_rows
    _fill_audio_positions(
        position_ids, slice(audio_start, video_start), num_audio_latents, rotary_time, target_width_grid,
        audio_channels, device,
    )
    frame_time = _temporal_position_grid(num_latent_frames, rotary_time, device)
    position_ids[video_start:, 0] = frame_time.repeat_interleave(target_frame_grid.shape[0])
    position_ids[video_start:, 1:] = target_frame_grid.repeat(num_latent_frames, 1)

    video_indices = torch.cat(video_index_blocks + [torch.arange(video_start, sequence_length, device=device)])
    audio_indices = torch.cat(audio_index_blocks + [torch.arange(audio_start, video_start, device=device)])
    text_indices = torch.arange(num_text_tokens, device=device)

    token_tags = torch.empty(sequence_length, dtype=torch.long, device=device)
    token_tags[text_indices] = text_token_tags
    token_tags[audio_indices] = AUDIO_TAG
    token_tags[video_indices] = VIDEO_TAG

    return PackedLayout(
        position_ids=position_ids, token_tags=token_tags,
        video_indices=video_indices, audio_indices=audio_indices, text_indices=text_indices,
        num_condition_video_rows=num_reference_video_rows, num_condition_audio_rows=num_reference_audio_rows,
        media_rotary_origin=rotary_time,
    )


def build_row_timesteps(
    video_indices: Tensor, audio_indices: Tensor, *,
    num_condition_video_rows: int, num_condition_audio_rows: int, num_text_tokens: int,
    video_timestep: float, audio_timestep: float,
    condition_video_timestep: float, condition_audio_timestep: float,
) -> tuple[Tensor, Tensor]:
    """Assign a timestep to every row, reduced to the transformer's
    `(timestep, timestep_indices)` pair via `torch.unique(..., return_inverse=True)`.

    Text rows never reach an output head and inherit the video timestep (an
    arbitrary but harmless choice -- their AdaLN row is addressed by
    `text_tag`, distinct from video's, so the shared timestep value only
    affects which *bucket* of the unique-timestep table they read, not video's
    own bucket).

    `video_indices`' own device is the "layout device" this function builds
    on -- `audio_indices` is coerced onto it explicitly (both are always
    expected to come from the SAME `PackedLayout`, but this is cheap
    insurance against a caller passing indices from two different sources).
    Same class of bug as `build_packed_sequence`'s bare-CPU internal tensors
    (see this module's "Device" docstring section) -- `row_timesteps` used to
    default to CPU regardless of what device `video_indices`/`audio_indices`
    actually carried, raising on a real GPU run the moment either was CUDA.
    """
    device = video_indices.device
    audio_indices = audio_indices.to(device)
    sequence_length = int(video_indices.numel() + audio_indices.numel() + num_text_tokens)
    row_timesteps = torch.full((sequence_length,), float(video_timestep), dtype=torch.float32, device=device)
    row_timesteps[video_indices[:num_condition_video_rows]] = float(condition_video_timestep)
    row_timesteps[audio_indices[num_condition_audio_rows:]] = float(audio_timestep)
    row_timesteps[audio_indices[:num_condition_audio_rows]] = float(condition_audio_timestep)
    return torch.unique(row_timesteps, sorted=True, return_inverse=True)
