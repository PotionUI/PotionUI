# Derived from: diffusers `modular_pipelines/minimax_h3/modular_pipeline.py`
# (Apache-2.0, "Copyright 2026 The MiniMax and HuggingFace Teams") --
# `resolve_canvas_size`, `align_num_frames`, `video_latent_num_frames`,
# `audio_latent_num_frames` and the magic constants they close over are
# ported verbatim (module-level functions instead of `MiniMaxH3ModularPipeline`
# properties/methods -- this pipe has no modular-pipeline `components` object
# to hang them off).
"""MiniMax-H3 request geometry: canvas resolve, frame-count snap, audio length.

None of this is guessable (dossier "E-geometry / F-constraints"): the video
VAE only accepts `17 * n + 5` pixel frames (`5 * n + 2` latent frames), the
canvas both axes round to is `32` (`vae_spatial_compression_ratio=16 *
patch_size[2]=2`), and the audio latent count is `round(frames / fps * 40)`
-- ALL per channel, doubled again for stereo by the layout builder
(`layout.py`), not here.
"""

from __future__ import annotations

FPS = 24
MIN_ASPECT_RATIO = 1.0 / 4.0
MAX_ASPECT_RATIO = 4.0

AUDIO_LATENTS_PER_SECOND = 40
AUDIO_CHANNELS = 2

# The canvas MiniMax-H3 was released for (dossier "Canvas"): short edge 768,
# area capped at 768*1344, both axes rounded to CANVAS_MULTIPLE = 16 (VAE
# spatial compression) * 2 (patch_size[2]) = 32.
CANVAS_SHORT_EDGE = 768
CANVAS_MAX_PIXELS = 768 * 1344
CANVAS_MULTIPLE = 32

# Video VAE chunk geometry (`vae.config.clip_length` / `vae.tokens_chunk_size`
# -- see `vae/minimax_h3_video.py`): `17 * n + 5` pixel frames <-> `5 * n + 2`
# latent frames.
FRAMES_PER_CHUNK = 17
LATENTS_PER_CHUNK = 5

# Pixel frames each latent frame of a chunk covers, cycling with period
# `LATENTS_PER_CHUNK`. The video VAE encodes 17-frame chunks independently
# through causal convolutions with `frame_pre_padding = 3`, so a chunk's first
# latent sees 1 real pixel frame and its other four see 4 each: `1 + 4*4 = 17`
# (`vae/minimax_h3_video.py`'s chunking docstring). It is the same series
# `layout.ROPE_FRAMES_PER_LATENT` spaces the rotary clock with, which is why a
# latent index and a rotary position agree about where in time a frame sits.
LATENT_FRAME_PIXEL_SPANS: tuple[int, ...] = (1, 4, 4, 4, 4)

MIN_DURATION_S = 5.0
MAX_DURATION_S = 15.0


def resolve_canvas_size(
    aspect_width: float,
    aspect_height: float,
    *,
    canvas_multiple: int = CANVAS_MULTIPLE,
    short_edge: int = CANVAS_SHORT_EDGE,
    max_pixels: int = CANVAS_MAX_PIXELS,
    min_aspect_ratio: float = MIN_ASPECT_RATIO,
    max_aspect_ratio: float = MAX_ASPECT_RATIO,
) -> tuple[int, int]:
    """Resolve a display aspect ratio into a MiniMax-H3 canvas.

    The short edge starts at `short_edge`, the area is capped at `max_pixels`,
    and both axes are then rounded to the nearest `canvas_multiple` -- so the
    final area may end up slightly above the pre-rounding budget. Only the
    ratio of `aspect_width`/`aspect_height` matters; pass either a display
    aspect ratio (`16, 9`) or a keyframe's own source pixel dimensions.

    Returns `(height, width)`.
    """
    if aspect_width <= 0 or aspect_height <= 0:
        raise ValueError(f"the aspect ratio must be positive, got {aspect_width}:{aspect_height}")

    ratio = aspect_width / aspect_height
    if not min_aspect_ratio <= ratio <= max_aspect_ratio:
        raise ValueError(
            f"MiniMax-H3 supports aspect ratios from 1:{1 / min_aspect_ratio:g} to "
            f"{max_aspect_ratio:g}:1, got {aspect_width}:{aspect_height} ({ratio:g})"
        )

    if ratio >= 1.0:
        width, height = short_edge * ratio, float(short_edge)
    else:
        width, height = float(short_edge), short_edge / ratio

    area = width * height
    if area > max_pixels:
        scale = (max_pixels / area) ** 0.5
        width, height = width * scale, height * scale

    multiple = canvas_multiple
    return (
        max(multiple, round(height / multiple) * multiple),
        max(multiple, round(width / multiple) * multiple),
    )


def align_num_frames(
    num_frames: int, *, frames_per_chunk: int = FRAMES_PER_CHUNK, latents_per_chunk: int = LATENTS_PER_CHUNK,
) -> int:
    """Snap `num_frames` UP to the next `frames_per_chunk * n + latents_per_chunk`
    the video VAE can encode (`17 * n + 5` at the released geometry)."""
    if num_frames < 1:
        raise ValueError(f"num_frames must be positive, got {num_frames}")
    while num_frames % frames_per_chunk != latents_per_chunk:
        num_frames += 1
    return num_frames


def video_latent_num_frames(
    num_frames: int, *, frames_per_chunk: int = FRAMES_PER_CHUNK, latents_per_chunk: int = LATENTS_PER_CHUNK,
) -> int:
    """Latent frame count for an already-aligned `num_frames` (`5 * n + 2` at
    the released geometry)."""
    if num_frames % frames_per_chunk != latents_per_chunk:
        raise ValueError(
            f"num_frames must be of the form {frames_per_chunk} * n + {latents_per_chunk}, got {num_frames}"
        )
    return (num_frames - latents_per_chunk) // frames_per_chunk * latents_per_chunk + 2


def audio_latent_num_frames(
    num_frames: int, *, fps: float = FPS, latents_per_second: int = AUDIO_LATENTS_PER_SECOND,
) -> int:
    """Audio latent count PER CHANNEL that covers a `num_frames`-frame video.
    Stereo doubling happens in the layout builder, not here."""
    return int(round(num_frames / fps * latents_per_second))


def pixel_frames_for_latent_frames(
    num_latent_frames: int, *, frames_per_chunk: int = FRAMES_PER_CHUNK, latents_per_chunk: int = LATENTS_PER_CHUNK,
) -> int:
    """Inverse of :func:`video_latent_num_frames`: the aligned (`17*n+5`)
    pixel frame count that encodes to exactly `num_latent_frames` (`5*n+2`)
    latent frames.

    A refine pass derives `frames` from an upstream LATENT's own shape rather
    than request config (no pixel-frame count travels with a raw latent, only
    its own `T_lat`) -- this is that derivation. Raises when
    `num_latent_frames` is not itself of the `5*n+2` form the video VAE ever
    produces, which means the latent did not come from this VAE's own encode.
    """
    if num_latent_frames < 2:
        raise ValueError(f"num_latent_frames must be >= 2, got {num_latent_frames}")
    chunks, remainder = divmod(num_latent_frames - 2, latents_per_chunk)
    if remainder:
        raise ValueError(
            f"{num_latent_frames} latent frames is not of the form {latents_per_chunk}*n+2 this video VAE "
            f"produces -- not a latent this pipe's own encoder could have made"
        )
    return chunks * frames_per_chunk + latents_per_chunk


def head_frames_for_latents(
    num_latents: int, *, spans: tuple[int, ...] = LATENT_FRAME_PIXEL_SPANS,
) -> int:
    """Pixel frames the FIRST `num_latents` latent frames of a clip cover.

    The series starts at the head of the clip, so it runs `1, 5, 9, 13, 17,
    18, 22, ...` -- a sliding-window continuation trims exactly this many
    decoded frames off a window whose leading `num_latents` latents were
    pinned to the previous window's tail.
    """
    if num_latents < 0:
        raise ValueError(f"num_latents must not be negative, got {num_latents}")
    return sum(spans[index % len(spans)] for index in range(num_latents))


def tail_latents_for_frames(
    num_frames: int, *, spans: tuple[int, ...] = LATENT_FRAME_PIXEL_SPANS,
    latents_per_chunk: int = LATENTS_PER_CHUNK,
) -> int:
    """Trailing latent frames of an aligned (`17n+5`) clip needed to cover at
    least `num_frames` pixel frames.

    Walked from the TAIL, which is a different phase of the cycle than the
    head: an aligned clip ends on latent index `5n+1`, so the spans run
    `4, 1, 4, 4, 4, ...` backwards and the series is `4, 5, 9, 13, 17, 21,
    22, ...`. The two walks agree exactly at whole chunks (5 latents <-> 17
    pixel frames, 10 <-> 34) and nowhere else, which is why the preset's
    overlap options are multiples of 17.
    """
    if num_frames < 0:
        raise ValueError(f"num_frames must not be negative, got {num_frames}")
    covered = 0
    count = 0
    while covered < num_frames:
        # Index of the `count`-th latent from the end of a `5n+2`-long clip:
        # `(5n + 1 - count) % 5` reduces to `(1 - count) % 5`.
        covered += spans[(1 - count) % len(spans)]
        count += 1
    return count


def latent_index_for_frame(
    frame: int, *, spans: tuple[int, ...] = LATENT_FRAME_PIXEL_SPANS,
) -> int:
    """The latent frame containing pixel frame `frame`, counted from the head
    of a clip -- the inverse of :func:`head_frames_for_latents`."""
    if frame < 0:
        raise ValueError(f"frame must not be negative, got {frame}")
    covered = 0
    index = 0
    while covered + spans[index % len(spans)] <= frame:
        covered += spans[index % len(spans)]
        index += 1
    return index


def resolve_request_geometry(
    height: int | None, width: int | None, num_frames: int,
) -> tuple[int, int, int, int, int, int, int]:
    """End-to-end geometry resolve for a `t2va`/`fl2va` request.

    `height`/`width` both `None` -> the 16:9 default canvas (no keyframe to
    take the aspect ratio from). Returns `(height, width, num_frames,
    num_latent_frames, latent_height, latent_width, num_audio_latents)`.
    """
    if (height is None) != (width is None):
        raise ValueError("height and width must be given together, or neither")
    if height is None:
        height, width = resolve_canvas_size(16, 9)
    if height % CANVAS_MULTIPLE or width % CANVAS_MULTIPLE:
        raise ValueError(
            f"height and width must be multiples of {CANVAS_MULTIPLE}, got {height}x{width}"
        )

    aligned_frames = align_num_frames(num_frames)
    duration = aligned_frames / FPS
    if not MIN_DURATION_S <= duration <= MAX_DURATION_S:
        raise ValueError(
            f"MiniMax-H3 generates between {MIN_DURATION_S} and {MAX_DURATION_S} seconds at {FPS} fps, "
            f"so num_frames (rounded up to the next 17*n+5) must be between "
            f"{int(MIN_DURATION_S * FPS)} and {int(MAX_DURATION_S * FPS)}, got {num_frames} "
            f"(rounded up to {aligned_frames})"
        )

    num_latent_frames = video_latent_num_frames(aligned_frames)
    latent_height = height // 16
    latent_width = width // 16
    num_audio_latents = audio_latent_num_frames(aligned_frames)
    return height, width, aligned_frames, num_latent_frames, latent_height, latent_width, num_audio_latents
