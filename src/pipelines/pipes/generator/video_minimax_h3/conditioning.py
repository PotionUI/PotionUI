# Derived from: diffusers `modular_pipelines/minimax_h3/before_encoder.py`
# (`MiniMaxH3ResizeStep`, `MiniMaxH3Ref2VASetupStep` -- its image, video and
# audio reference branches, `_normalize_video_condition` and
# `_normalize_audio_condition`) and `encoders.py`
# (`MiniMaxH3KeyframeVaeEncoderStep`, `encode_vae_condition`,
# `MiniMaxH3Ref2VAReferenceEncoderStep`), Apache-2.0, "Copyright 2026 The
# MiniMax and HuggingFace Teams" -- the canvas-fit (stretch-first/cover-crop-
# follower), reference-own-resolution-fit, constant-frame-rate resample,
# reference-video snap-down and pixel-normalize/encode/round recipes are
# ported near-verbatim.
"""fl2va keyframe / ref2va reference conditioning: fit -> VAE-encode ->
noise-to-0.999 -> patchify -> prepend as condition rows.

Both share the same encode/noise/patchify recipe (`encode_keyframe_condition`
below); they differ only in what canvas the image is fit onto before that.
An `fl2va` keyframe is fit onto the TARGET canvas (`fit_keyframe_to_canvas`),
overlaying the generated frames it anchors. A `ref2va` reference keeps its
OWN resolution (`normalize_reference_image`) -- a short-edge-2048 fit, no
upper pixel cap, no crop -- because it is a PREFIX ahead of the generated
rows rather than an overlay onto them (`layout.build_ref2va_packed_sequence`
reads each reference's geometry from what was actually encoded here).

`encode_vae_condition` (the reference) samples the video VAE's Gaussian
posterior under a FIXED generator (`keyframe_encode_seed = 42`, independent
of the request's own generator -- a fresh `Generator().manual_seed(42)` per
keyframe/reference, not one shared/advancing generator across a multi-image
request) then rounds the sample to float16 precision before normalizing.
`MiniMaxH3VideoVAE.encode(sample_posterior=True, generator=...)`
(`src/platform/runtime/native/vae/minimax_h3_video.py`) implements that exact
sampling math -- see `KEYFRAME_ENCODE_SEED`'s docstring for how this module
drives it.

**All three reference modalities.** `normalize_references` ->
`prepare_reference_conditioning` is the whole `ref2va` conditioning path, for
an ordered list of :class:`ReferenceMedia` mixing images, videos and audio.
The three differ in what they contribute:

- an IMAGE contributes one visual condition latent, encoded at its own
  2048-short-edge resolution;
- a VIDEO contributes one visual condition latent -- a frame stack, so the
  VAE's temporal chunking applies and the latent carries `5 * n + 2` frames
  rather than 1 -- at the canvas ITS OWN aspect ratio resolves to under the
  target's canvas rule (not the 2048 image rule), plus a soundtrack if it
  carries one;
- an AUDIO reference contributes only clean soundtrack rows, no visual
  latent at all.

Both visual kinds are noise-augmented to `t = KEYFRAME_NOISE_AUG` and
patchified into the same `condition_rows` stream, in packed order.
Soundtracks ride along CLEAN at `t = 1.0` in a separate
`condition_audio_rows` stream (`audio.encode_audio_condition` takes the
posterior mean and draws no noise), which is why a soundtrack consumes
nothing from the request generator and the "one generator, three draws, in
order" contract counts only the VISUAL references.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
from PIL import Image

from src.pipelines.pipes.generator.video_minimax_h3.audio import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    encode_audio_condition,
    normalize_condition_waveform,
)
from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
    CANVAS_MAX_PIXELS,
    CANVAS_MULTIPLE,
    CANVAS_SHORT_EDGE,
    FPS,
    FRAMES_PER_CHUNK,
    LATENTS_PER_CHUNK,
    resolve_canvas_size,
)
from src.pipelines.pipes.generator.video_minimax_h3.layout import ReferenceBlock, patchify_video_latents
from src.pipelines.pipes.generator.video_minimax_h3.schedule import KEYFRAME_NOISE_AUG, scale_noise

Tensor = torch.Tensor

# `reference_image_short_edge` (`MiniMaxH3Ref2VASetupStep`'s `ConfigSpec`) --
# a `ref2va` image reference is encoded at high detail, uncapped by area,
# unlike an `fl2va` keyframe (which is fit onto the target canvas) or a
# `ref2va` VIDEO reference (which shares the target's own canvas rule).
REFERENCE_IMAGE_SHORT_EDGE = 2048

# Per-modality reference limits the released checkpoint documents
# (`MiniMaxH3Ref2VASetupStep.__init__`'s `max_images`/`max_videos`/
# `max_audios`/`max_references`). They bound validation only -- a fine-tune
# that packs more can raise them.
MAX_IMAGE_REFERENCES = 9
MAX_VIDEO_REFERENCES = 3
MAX_AUDIO_REFERENCES = 3
MAX_REFERENCES = 12

PIXEL_MEAN = (0.485, 0.456, 0.406)
PIXEL_STD = (0.229, 0.224, 0.225)

# The seed a keyframe's VAE-encode posterior is ALWAYS sampled under,
# independent of the request's own seed -- dossier `keyframe_encode_seed`:
# "the same keyframe always encodes to the same anchor". A fresh CPU
# generator is constructed per keyframe (matching the reference's own
# per-call `torch.Generator().manual_seed(42)`, not a generator shared/
# advancing across a multi-keyframe request) -- see `_randn_like_reference`'s
# docstring (minimax_h3_video.py) for why a CPU generator specifically
# reproduces the same noise regardless of which device the VAE runs on.
KEYFRAME_ENCODE_SEED = 42


def fit_keyframe_to_canvas(image: Image.Image, height: int, width: int, *, is_geometry_anchor: bool) -> Image.Image:
    """Put one keyframe onto the `(height, width)` canvas.

    The geometry anchor (`is_geometry_anchor=True` -- the request's `image`,
    or `last_image` alone with no `image`) is STRETCHED onto the canvas
    (`PIL` `resize((width, height), LANCZOS)`); a follower keyframe is
    COVER-CROPPED with the released model's own (not `VaeImageProcessor`'s
    `resize_mode="crop"`) rounding/centring arithmetic -- the two disagree by
    a pixel on some aspect ratios, so this keeps the released arithmetic
    verbatim rather than reusing the shared image processor.
    """
    if image.size == (width, height):
        return image
    if is_geometry_anchor:
        return image.resize((width, height), Image.Resampling.LANCZOS)
    scale = max(width / image.size[0], height / image.size[1])
    resized_size = (max(width, round(image.size[0] * scale)), max(height, round(image.size[1] * scale)))
    left = max(0, (resized_size[0] - width) // 2)
    top = max(0, (resized_size[1] - height) // 2)
    resized = image.resize(resized_size, Image.Resampling.LANCZOS)
    return resized.crop((left, top, left + width, top + height))


def normalize_reference_image(
    image: Image.Image, *, canvas_multiple: int, short_edge: int = REFERENCE_IMAGE_SHORT_EDGE,
) -> Image.Image:
    """Put one `ref2va` image reference on its OWN resolution: short edge
    `short_edge` (upscaling included), each axis then rounded to the nearest
    `canvas_multiple` with a floor of `canvas_multiple`, no upper pixel cap
    and no crop -- unlike `fit_keyframe_to_canvas`'s target-canvas fit
    (`MiniMaxH3Ref2VASetupStep`'s image branch).

    Raises on an aspect ratio outside `[1:4, 4:1]`, the same bound the target
    canvas and a video reference are held to.
    """
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError(f"a reference image must have a positive size, got {image.size}")
    if width > 4 * height or height > 4 * width:
        raise ValueError(f"a reference image must be within 1:4 and 4:1, got {width}x{height}")
    scale = short_edge / min(width, height)
    target_height = max(canvas_multiple, round(height * scale / canvas_multiple) * canvas_multiple)
    target_width = max(canvas_multiple, round(width * scale / canvas_multiple) * canvas_multiple)
    if (width, height) == (target_width, target_height):
        return image
    return image.resize((target_width, target_height), Image.Resampling.LANCZOS)


def normalize_reference_video(
    frames: Any, *, fps: float, num_frames: int, target_fps: float = FPS,
    canvas_multiple: int = CANVAS_MULTIPLE, short_edge: int = CANVAS_SHORT_EDGE,
    max_pixels: int = CANVAS_MAX_PIXELS,
) -> np.ndarray:
    """Put one `ref2va` VIDEO reference on MiniMax-H3's own rate and canvas:
    `(num_frames, height, width, 3)` uint8 at `target_fps`, truncated to the
    generated frame count, on the canvas ITS OWN aspect ratio resolves to
    (`MiniMaxH3Ref2VASetupStep._normalize_video_condition`).

    A video reference follows the TARGET's canvas rule -- short edge 768,
    area capped, both axes to `canvas_multiple` -- NOT the 2048-short-edge
    uncapped rule an image reference gets (`normalize_reference_image`); only
    the aspect ratio it is resolved from is the reference's own. It is
    therefore capped in area where an image reference is not.

    The two passes run in the reference's own order: the constant-frame-rate
    resample first (dropping and duplicating WHOLE frames the way `ffmpeg`'s
    `fps` filter does -- not interpolating), the LANCZOS rescale second.
    Frames handed over already at `target_fps` and already on that canvas
    flow through untouched, which is the parity-exact route: the released
    model rescaled with `ffmpeg`'s own LANCZOS while decoding, so only frames
    decoded at the canvas reproduce its pixels bit for bit.

    `frames` is a list of PIL images, a `(F, H, W, 3)` array or a
    `(F, 3, H, W)` tensor, uint8 or floating point over `[0, 1]`.
    """
    if isinstance(frames, list):
        frames = np.stack([np.asarray(frame.convert("RGB")) for frame in frames])
    if isinstance(frames, torch.Tensor):
        frames = frames.movedim(-3, -1).cpu().numpy()
    frames = np.asarray(frames)
    if frames.dtype != np.uint8:
        frames = (frames * 255.0).round().clip(0, 255).astype(np.uint8)
    if frames.ndim != 4 or frames.shape[3] != 3:
        raise ValueError(
            f"a reference video must be (num_frames, height, width, 3) RGB frames, got {tuple(frames.shape)}"
        )

    if fps <= 0:
        raise ValueError(f"a reference video must have a positive frame rate, got {fps}")
    if fps != target_fps:
        scale = target_fps / fps
        slots = np.floor(np.arange(frames.shape[0]) * scale + 0.5).astype(np.int64)
        frames = np.repeat(frames, np.diff(slots, append=math.floor(frames.shape[0] * scale + 0.5)), axis=0)

    frames = frames[:num_frames]
    height, width = resolve_canvas_size(
        frames.shape[2], frames.shape[1],
        canvas_multiple=canvas_multiple, short_edge=short_edge, max_pixels=max_pixels,
    )
    if frames.shape[1:3] == (height, width):
        return frames
    return np.stack([
        np.asarray(Image.fromarray(frame).resize((width, height), Image.Resampling.LANCZOS)) for frame in frames
    ])


def snap_reference_video_frames(
    num_frames: int, *, frames_per_chunk: int = FRAMES_PER_CHUNK, latents_per_chunk: int = LATENTS_PER_CHUNK,
) -> int:
    """Snap a reference video's frame count DOWN to the `17 * n + 5` the video
    VAE encodes without padding (`MiniMaxH3Ref2VAReferenceEncoderStep`'s video
    branch) -- the opposite direction from `geometry.align_num_frames`, which
    snaps a REQUEST up.

    Down, because a reference is trimmed to fit the encoder rather than
    stretched to fill a request; it only bites when the reference is shorter
    than the target, whose own frame count already has that form.

    The reference's arithmetic floors at ONE chunk (`max(1, ...)`), so below
    `frames_per_chunk + latents_per_chunk` frames it returns MORE frames than
    were supplied. That is not encodable, so this raises there rather than
    handing the VAE a count that is neither `17 * n + 5` nor available --
    `prepare_reference_conditioning` would otherwise fail deep inside the VAE
    on a shape mismatch.
    """
    if num_frames < frames_per_chunk + latents_per_chunk:
        raise ValueError(
            f"a reference video must be at least {frames_per_chunk + latents_per_chunk} frames at "
            f"{FPS:g} fps for the video VAE to encode a whole chunk, got {num_frames}"
        )
    return max(1, (num_frames - latents_per_chunk) // frames_per_chunk) * frames_per_chunk + latents_per_chunk


def _pixels_from_image(image: Image.Image, device: Any) -> Tensor:
    """PIL RGB image -> `(1, 3, 1, H, W)` uint8-valued float32 tensor."""
    arr = torch.from_numpy(np.array(image.convert("RGB"))).to(device)
    return arr.permute(2, 0, 1)[None, :, None].contiguous()


def _pixels_from_frames(frames: np.ndarray, device: Any) -> Tensor:
    """`(F, H, W, 3)` uint8 frames -> `(1, 3, F, H, W)` uint8-valued tensor --
    the frame-stack counterpart of :func:`_pixels_from_image`, and the shape
    that puts the video VAE on its temporal-chunking path rather than its
    single-frame spatial-only one."""
    return torch.from_numpy(frames.copy()).to(device).permute(3, 0, 1, 2)[None].contiguous()


def _encode_dtype(vae_module: Any) -> torch.dtype:
    """The float dtype to hand `encode` its pixels in.

    Quantised repacks (the int8_tensorwise/ConvRot video VAE) store some
    parameters as integer codes, which are not a valid activation dtype;
    the ops layer casts each weight to whatever dtype the activation
    arrives in, so the choice must come from a parameter actually stored
    in floating point.
    """
    for parameter in vae_module.parameters():
        if parameter.is_floating_point():
            return parameter.dtype
    return torch.float32


def encode_keyframe_condition(
    vae_module: Any, pixels_uint8: Tensor, *, latents_mean: Any, latents_std: Any,
) -> Tensor:
    """Encode one `(1, 3, 1, H, W)` uint8-valued keyframe into a normalized
    `(1, latent_channels, 1, H/16, W/16)` conditioning latent.

    ImageNet-normalizes the pixels, encodes (single frame -> spatial encoder
    only, no temporal chunking -- `MiniMaxH3VideoVAE.encode`'s `num_frames ==
    1` branch) with the posterior SAMPLED (not the mode) under a fresh
    `KEYFRAME_ENCODE_SEED`-seeded CPU generator, rounds the sample to
    float16 precision (reference's own quantization step, independent of the
    sampling itself), then per-channel normalizes with the VAE's own
    `latents_mean`/`latents_std`.
    """
    device = pixels_uint8.device
    pixel_mean = torch.tensor(PIXEL_MEAN, device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
    pixel_std = torch.tensor(PIXEL_STD, device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
    pixels = (pixels_uint8.to(torch.float32) / 255.0 - pixel_mean) / pixel_std

    generator = torch.Generator(device="cpu").manual_seed(KEYFRAME_ENCODE_SEED)
    with torch.no_grad():
        latent = vae_module.encode(
            pixels.to(dtype=_encode_dtype(vae_module)),
            sample_posterior=True, generator=generator,
        )
    latent = latent.to(torch.float16).float()

    lmean = torch.as_tensor(latents_mean, device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
    lstd = torch.as_tensor(latents_std, device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
    return (latent - lmean) / lstd


def prepare_keyframe_condition_rows(
    keyframes: list, anchors: tuple, *, vae_module: Any, height: int, width: int,
    patch_size: tuple[int, int, int], device: Any, dtype: torch.dtype,
    latents_mean: Any, latents_std: Any, generator: torch.Generator,
) -> Tensor:
    """`fl2va` keyframes -> canvas-fit -> VAE-encode -> noise to `t =
    KEYFRAME_NOISE_AUG` -> patchify -> concatenate, in packed (`keyframe_
    anchors`) order.

    One noise draw per condition from `generator`, in order -- callers MUST
    draw this BEFORE the request's own video/audio noise (dossier "One
    generator, three draws, in order: conditioning noise -> video noise ->
    audio noise").  Returns an empty `(0, video_patch_dim)` tensor for
    `keyframes=[]` (`t2va`, or an `fl2va` mode with no anchors resolved).
    """
    video_patch_dim = None
    rows: list[Tensor] = []
    for index, (image, _anchor) in enumerate(zip(keyframes, anchors)):
        fitted = fit_keyframe_to_canvas(image, height, width, is_geometry_anchor=index == 0)
        pixels = _pixels_from_image(fitted, device)
        latent = encode_keyframe_condition(vae_module, pixels, latents_mean=latents_mean, latents_std=latents_std)
        noise = torch.randn(latent.shape, generator=generator, device=device, dtype=torch.float32)
        noised = scale_noise(latent, KEYFRAME_NOISE_AUG, noise)
        packed = patchify_video_latents(noised.to(dtype), patch_size)
        video_patch_dim = packed.shape[-1]
        rows.append(packed)
    if not rows:
        default_patch_dim = 24 * patch_size[0] * patch_size[1] * patch_size[2]
        return torch.zeros((0, default_patch_dim), device=device, dtype=dtype)
    return torch.cat(rows, dim=0)


def prepare_reference_condition_rows(
    images: list, *, vae_module: Any, canvas_multiple: int, patch_size: tuple[int, int, int],
    device: Any, dtype: torch.dtype, latents_mean: Any, latents_std: Any, generator: torch.Generator,
    short_edge: int = REFERENCE_IMAGE_SHORT_EDGE,
) -> tuple[list[Tensor], Tensor]:
    """`ref2va` image references -> own-resolution fit -> VAE-encode -> noise
    to `t = KEYFRAME_NOISE_AUG` -> patchify, in reference (packed) order.

    Shares `encode_keyframe_condition`'s recipe with `fl2va` keyframes
    (`prepare_keyframe_condition_rows`); the only difference is the fit
    (`normalize_reference_image` instead of `fit_keyframe_to_canvas`) --
    every reference keeps its OWN resolution rather than the target canvas,
    so unlike that function this one cannot concatenate the patched rows
    without first individually noising/patchifying each one, and it also
    returns the clean, un-patchified latent of each reference: that shape
    (not the target's) is what `layout.build_ref2va_packed_sequence` reads
    every reference block's geometry from.

    `short_edge` defaults to the released checkpoint's own
    `REFERENCE_IMAGE_SHORT_EDGE`; exposed as a parameter (like
    `normalize_reference_image`'s own) purely so a test can shrink it --
    every real caller leaves it at the default.

    One noise draw per reference from `generator`, in order -- same "one
    generator, three draws, in order" contract `prepare_keyframe_condition_
    rows` documents; callers MUST draw this BEFORE the request's own
    video/audio noise. Returns `([], (0, video_patch_dim))` for `images=[]`.
    """
    condition_latents: list[Tensor] = []
    packed_rows: list[Tensor] = []
    for image in images:
        fitted = normalize_reference_image(image, canvas_multiple=canvas_multiple, short_edge=short_edge)
        latent, packed = _encode_and_pack_visual_reference(
            vae_module, _pixels_from_image(fitted, device), patch_size=patch_size, device=device, dtype=dtype,
            latents_mean=latents_mean, latents_std=latents_std, generator=generator,
        )
        condition_latents.append(latent)
        packed_rows.append(packed)
    if not packed_rows:
        return condition_latents, _empty_condition_rows(patch_size, device, dtype)
    return condition_latents, torch.cat(packed_rows, dim=0)


def _empty_condition_rows(patch_size: tuple[int, int, int], device: Any, dtype: torch.dtype) -> Tensor:
    return torch.zeros(
        (0, 24 * patch_size[0] * patch_size[1] * patch_size[2]), device=device, dtype=dtype,
    )


def _encode_and_pack_visual_reference(
    vae_module: Any, pixels: Tensor, *, patch_size: tuple[int, int, int], device: Any, dtype: torch.dtype,
    latents_mean: Any, latents_std: Any, generator: torch.Generator,
) -> tuple[Tensor, Tensor]:
    """One visual reference (image OR video frame stack) -> its CLEAN
    condition latent and its noised, patchified rows.

    ONE draw off `generator`, whatever the reference's frame count -- the
    noise is drawn `latent.shape`-wide in a single call, so a video reference
    consumes exactly as much generator state as an image one does.
    """
    latent = encode_keyframe_condition(vae_module, pixels, latents_mean=latents_mean, latents_std=latents_std)
    noise = torch.randn(latent.shape, generator=generator, device=device, dtype=torch.float32)
    noised = scale_noise(latent, KEYFRAME_NOISE_AUG, noise)
    return latent, patchify_video_latents(noised.to(dtype), patch_size)


@dataclass(frozen=True)
class ReferenceMedia:
    """One `ref2va` reference's media, in packed order.

    Mirrors diffusers' three `MiniMaxH3ImageReference`/`VideoReference`/
    `AudioReference` dataclasses (`references.py`) as ONE tagged record --
    they carry disjoint fields and this pipe never dispatches on their type,
    only on `kind`, which is also all `layout.ReferenceBlock` branches on.

    `kind="image"`: `image` is a PIL image. `kind="video"`: `frames` is a
    frame sequence at `fps` (a list of PIL images, a `(F, H, W, 3)` array or a
    `(F, 3, H, W)` tensor) which MAY carry `audio`. `kind="audio"`: `audio` is
    a `(channels, samples)` waveform at `sample_rate`, and there is no visual
    media at all.

    `has_audio` is `audio is not None`, which reproduces all three reference
    classes at once: an audio reference always has one, an image reference
    never does, and a video reference's is optional.
    """

    kind: str
    image: Image.Image | None = None
    frames: Any | None = None
    fps: float | None = None
    audio: Tensor | None = None
    sample_rate: int | None = None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None


@dataclass(frozen=True)
class ReferenceConditioning:
    """Everything a `ref2va` request's references contribute, in packed order.

    `blocks`, `condition_latents` and `audio_condition_latents` are
    `layout.build_ref2va_packed_sequence`'s first three arguments, positionally
    -- that function consumes the latter two as ITERATORS alongside `blocks`,
    so their per-kind lengths differ on purpose: one visual latent per image
    and video reference, one audio row block per AUDIO-BEARING reference (a
    standalone audio reference, or a video reference that carries a
    soundtrack).

    `condition_rows` is the noised, patchified visual prefix of the packed
    video stream, concatenated in the same order `build_ref2va_packed_
    sequence` emits its `video_index_blocks` in. `condition_audio_rows` is the
    CLEAN audio prefix, likewise ordered -- `None` when no reference carries
    sound, the same "no condition audio" signal the sampler already takes.
    """

    blocks: tuple[ReferenceBlock, ...]
    condition_latents: tuple[Tensor, ...]
    audio_condition_latents: tuple[Tensor, ...]
    condition_rows: Tensor
    condition_audio_rows: Tensor | None


def validate_references(references: list[ReferenceMedia] | tuple[ReferenceMedia, ...]) -> None:
    """The released checkpoint's own reference-count rules
    (`MiniMaxH3Ref2VASetupStep.__call__`): per-modality and total limits, and
    audio references cannot be the only ones -- a soundtrack conditions a
    video that some visual reference has to anchor."""
    if not references:
        raise ValueError("ref2va needs at least one reference; use the t2va/fl2va path for text-only requests")
    kinds = [reference.kind for reference in references]
    for kind, limit in (
        ("image", MAX_IMAGE_REFERENCES), ("video", MAX_VIDEO_REFERENCES), ("audio", MAX_AUDIO_REFERENCES),
    ):
        if kinds.count(kind) > limit:
            raise ValueError(f"MiniMax-H3 accepts at most {limit} {kind} references, got {kinds.count(kind)}")
    if len(kinds) > MAX_REFERENCES:
        raise ValueError(f"MiniMax-H3 accepts at most {MAX_REFERENCES} references in total, got {len(kinds)}")
    if set(kinds) == {"audio"}:
        raise ValueError(
            "an audio reference has to be paired with at least one image or video reference and cannot be used "
            "on its own"
        )
    for index, reference in enumerate(references):
        if reference.kind not in ("image", "video", "audio"):
            raise ValueError(f"references[{index}] must be 'image', 'video' or 'audio', got {reference.kind!r}")
        if reference.kind == "image" and reference.has_audio:
            raise ValueError(f"references[{index}] is an image reference and cannot carry a soundtrack")
        if reference.kind == "audio" and not reference.has_audio:
            raise ValueError(f"references[{index}] is an audio reference and must carry a waveform")


def normalize_references(
    references: list[ReferenceMedia] | tuple[ReferenceMedia, ...], *, num_frames: int, fps: float = FPS,
    canvas_multiple: int = CANVAS_MULTIPLE, canvas_short_edge: int = CANVAS_SHORT_EDGE,
    canvas_max_pixels: int = CANVAS_MAX_PIXELS, reference_short_edge: int = REFERENCE_IMAGE_SHORT_EDGE,
    audio_sample_rate: int = AUDIO_SAMPLE_RATE, audio_channels: int = AUDIO_CHANNELS,
) -> list[ReferenceMedia]:
    """Put every reference on MiniMax-H3's own rates and resolutions, in
    packed order (`MiniMaxH3Ref2VASetupStep.__call__`'s normalize loop).

    Per modality: an image to its own `reference_short_edge` resolution, a
    video onto `fps` and onto the canvas its own aspect ratio resolves to,
    and ANY reference's soundtrack -- a standalone audio reference's, and a
    video reference's alike -- onto the audio VAE's `audio_sample_rate`,
    truncated to the generated duration `num_frames / fps`.

    The soundtrack is truncated at its SOURCE rate and resampled once
    afterwards, in that order (`audio.normalize_condition_waveform`); a
    reference longer than the generated video contributes only its head.

    `num_frames` is the request's ALREADY-ALIGNED (`17 * n + 5`) frame count
    -- `geometry.resolve_request_geometry` resolves it; references never bind
    the generated geometry, so nothing here can change it.
    """
    validate_references(references)

    normalized: list[ReferenceMedia] = []
    for reference in references:
        waveform = None
        if reference.has_audio:
            waveform = normalize_condition_waveform(
                reference.audio,
                sample_rate=reference.sample_rate if reference.sample_rate is not None else audio_sample_rate,
                target_sample_rate=audio_sample_rate, max_duration=num_frames / fps, audio_channels=audio_channels,
            )

        if reference.kind == "image":
            normalized.append(ReferenceMedia(
                kind="image",
                image=normalize_reference_image(
                    reference.image, canvas_multiple=canvas_multiple, short_edge=reference_short_edge,
                ),
            ))
        elif reference.kind == "video":
            normalized.append(ReferenceMedia(
                kind="video",
                frames=normalize_reference_video(
                    reference.frames, fps=float(reference.fps if reference.fps is not None else fps),
                    num_frames=num_frames, target_fps=fps, canvas_multiple=canvas_multiple,
                    short_edge=canvas_short_edge, max_pixels=canvas_max_pixels,
                ),
                fps=fps, audio=waveform, sample_rate=None if waveform is None else audio_sample_rate,
            ))
        else:
            normalized.append(ReferenceMedia(kind="audio", audio=waveform, sample_rate=audio_sample_rate))
    return normalized


def prepare_reference_conditioning(
    references: list[ReferenceMedia] | tuple[ReferenceMedia, ...], *, vae_module: Any, audio_vae_module: Any = None,
    patch_size: tuple[int, int, int], device: Any, dtype: torch.dtype, latents_mean: Any, latents_std: Any,
    generator: torch.Generator, audio_channels: int = AUDIO_CHANNELS,
) -> ReferenceConditioning:
    """Encode ALREADY-NORMALIZED `ref2va` references into everything the
    layout and the sampler need (`MiniMaxH3Ref2VAReferenceEncoderStep`).

    `references` must have come through :func:`normalize_references` -- this
    function does no fitting or resampling, it only encodes, so handing it
    raw media silently conditions on the wrong resolution and rate.

    Visual references (image and video) are VAE-encoded, noise-augmented to
    `t = KEYFRAME_NOISE_AUG` and patchified; a video's frame count is first
    snapped down to `17 * n + 5` (:func:`snap_reference_video_frames`).
    Soundtracks go through the AUDIO VAE clean, at `t = 1.0`, and are packed
    channel-major -- `audio_vae_module` is required as soon as any reference
    carries one.

    One noise draw per VISUAL reference off `generator`, in packed order --
    the same "one generator, three draws, in order: conditioning noise ->
    video noise -> audio noise" contract `prepare_keyframe_condition_rows`
    documents, and callers MUST draw this BEFORE the request's own video and
    audio noise. A soundtrack draws nothing at all (the audio VAE returns the
    posterior mean), so adding one to a request does not shift the video
    noise that follows.
    """
    blocks: list[ReferenceBlock] = []
    condition_latents: list[Tensor] = []
    audio_condition_latents: list[Tensor] = []
    packed_rows: list[Tensor] = []

    for reference in references:
        if reference.kind == "image":
            latent, packed = _encode_and_pack_visual_reference(
                vae_module, _pixels_from_image(reference.image, device), patch_size=patch_size, device=device,
                dtype=dtype, latents_mean=latents_mean, latents_std=latents_std, generator=generator,
            )
            condition_latents.append(latent)
            packed_rows.append(packed)
        elif reference.kind == "video":
            frames = np.asarray(reference.frames)
            frames = frames[: snap_reference_video_frames(frames.shape[0])]
            latent, packed = _encode_and_pack_visual_reference(
                vae_module, _pixels_from_frames(frames, device), patch_size=patch_size, device=device,
                dtype=dtype, latents_mean=latents_mean, latents_std=latents_std, generator=generator,
            )
            condition_latents.append(latent)
            packed_rows.append(packed)
        elif reference.kind != "audio":
            raise ValueError(f"a reference must be 'image', 'video' or 'audio', got {reference.kind!r}")

        if reference.has_audio:
            if audio_vae_module is None:
                raise ValueError(
                    "a ref2va reference carries a soundtrack, so prepare_reference_conditioning needs the audio VAE"
                )
            audio_condition_latents.append(encode_audio_condition(
                audio_vae_module, reference.audio, sample_rate=AUDIO_SAMPLE_RATE,
                audio_channels=audio_channels, device=device, dtype=dtype,
            ))
        blocks.append(ReferenceBlock(kind=reference.kind, has_audio=reference.has_audio))

    return ReferenceConditioning(
        blocks=tuple(blocks),
        condition_latents=tuple(condition_latents),
        audio_condition_latents=tuple(audio_condition_latents),
        condition_rows=(
            torch.cat(packed_rows, dim=0) if packed_rows else _empty_condition_rows(patch_size, device, dtype)
        ),
        condition_audio_rows=torch.cat(audio_condition_latents, dim=0) if audio_condition_latents else None,
    )
