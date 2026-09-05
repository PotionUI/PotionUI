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

import hashlib
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

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


def _pixels_from_array(image_hw3: np.ndarray, device: Any) -> Tensor:
    """`(H, W, 3)` uint8 array -> `(1, 3, 1, H, W)` uint8-valued tensor -- the
    ndarray-input counterpart of :func:`_pixels_from_image`, used by a caller
    that already materialized the fitted pixels as an array to hash for the
    visual latent cache key and does not want to convert twice."""
    arr = torch.from_numpy(image_hw3.copy()).to(device)
    return arr.permute(2, 0, 1)[None, :, None].contiguous()


def _pixels_from_image(image: Image.Image, device: Any) -> Tensor:
    """PIL RGB image -> `(1, 3, 1, H, W)` uint8-valued float32 tensor."""
    return _pixels_from_array(np.array(image.convert("RGB")), device)


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

    `vae_module.parameters` missing entirely (rather than merely empty)
    falls back to `float32` too, same as no floating-point parameter being
    found -- a real VAE module always has it; only a test double stood in for
    one to keep an unrelated call path from touching the module at all can
    lack it, and `visual_references_need_encode`/`keyframes_need_encode` (the
    cache's pre-check, run over the SAME key-building path as the real
    encode) must be able to call this on exactly those doubles without ever
    reaching an actual `encode()`.
    """
    parameters = getattr(vae_module, "parameters", None)
    if not callable(parameters):
        return torch.float32
    for parameter in parameters():
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


# Bytes budget for the request-local `VisualLatentCache` below. Sized to hold a
# full `MAX_REFERENCES` (12) reference set at `REFERENCE_IMAGE_SHORT_EDGE`'s
# worst-case 1:4 aspect ratio in float32 with headroom left over for a handful
# of Director windows layering distinct keyframes on top of that. A
# pathological request that would exceed it does not grow the cache past this
# -- entries are evicted LRU, and any single entry over the WHOLE budget skips
# caching for that one (see `VisualLatentCache.put`), falling back to the
# ordinary uncached encode every time it recurs.
VISUAL_LATENT_CACHE_BUDGET_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class VisualLatentCacheKey:
    """Everything that has to match for a cached clean visual condition
    latent to be reusable for a DIFFERENT keyframe/reference occurrence.

    `content_digest`/`shape` identify the fitted (post fit/snap) pixels
    themselves; `fit_role`/`target_size`/`frame_selection` are the ADDITIONAL
    context a byte-identical digest could not distinguish on its own (mostly
    theoretical -- two different fits producing identical output bytes -- but
    cheap to include and exactly what the fit step could vary); the rest pins
    the VAE this would be re-encoded through: its identity and live weight
    revision, the normalization buffers' VALUES (`latents_mean_digest`/
    `latents_std_digest` -- see `_normalization_digest`'s docstring for why
    this is a content digest and not `id()`), the dtype the encode ran in and
    the device the result lives on.
    """

    content_digest: str
    shape: tuple[int, ...]
    fit_role: str
    target_size: tuple[int, int]
    frame_selection: tuple[int, ...]
    vae_id: int
    weight_revision: Any
    latents_mean_digest: str
    latents_std_digest: str
    encode_dtype: str
    device: str


def _fitted_content_digest(pixels: np.ndarray) -> str:
    """sha256 of `pixels`' raw bytes plus its shape/dtype header.

    `pixels` must already be through the fit/snap step (`fit_keyframe_to_
    canvas`/`normalize_reference_image`/`snap_reference_video_frames`) -- this
    hashes whatever it is given, so handing it raw, un-fit media would key the
    cache on content two different requests could share by coincidence while
    still meaning two different fits.
    """
    contiguous = np.ascontiguousarray(pixels)
    header = f"{contiguous.dtype}:{contiguous.shape}".encode("utf-8")
    return hashlib.sha256(header + contiguous.tobytes()).hexdigest()


def _normalization_digest(values: Any) -> str:
    """sha256 of a normalization buffer's VALUES (`latents_mean`/`latents_
    std`), not its object identity.

    `id()` would look attractive here -- a real VAE's `latents_mean`/
    `latents_std` are registered buffers -- but `nn.Module.to()` (what a video
    VAE's `move_to`/`offload` calls) reassigns buffer OBJECTS on every device
    conversion (`Module._apply`), even though the values they hold do not
    change. A key built from `id(latents_mean)` would therefore miss on the
    very next occurrence after an ordinary placement round-trip -- exactly
    the case (an actual encode about to run) a hit is worth the most. Hashing
    the VALUES is stable across that churn and still misses when the
    normalization actually changes (a different VAE, or a real revision
    change) -- `weight_revision` covers the latter independently, this field
    covers the former.

    Tiny tensors (`latent_channels` elements, tens of bytes) -- recomputed on
    every key build rather than cached on the VAE instance, since hashing
    them costs nothing next to hashing a keyframe's own pixels.
    """
    tensor = torch.as_tensor(values, dtype=torch.float64).detach().to("cpu").contiguous()
    header = f"{tensor.shape}".encode("utf-8")
    return hashlib.sha256(header + tensor.numpy().tobytes()).hexdigest()


def visual_latent_cache_key(
    fitted_pixels: np.ndarray, *, fit_role: str, target_size: tuple[int, int], frame_selection: tuple[int, ...],
    vae_module: Any, weight_revision: Any, latents_mean: Any, latents_std: Any, device: Any,
) -> VisualLatentCacheKey:
    """Build the :class:`VisualLatentCacheKey` for one already-fitted
    keyframe/reference occurrence. See that class for what each field guards
    against a false hit."""
    return VisualLatentCacheKey(
        content_digest=_fitted_content_digest(fitted_pixels),
        shape=tuple(fitted_pixels.shape),
        fit_role=fit_role,
        target_size=tuple(target_size),
        frame_selection=tuple(frame_selection),
        vae_id=id(vae_module),
        weight_revision=weight_revision,
        latents_mean_digest=_normalization_digest(latents_mean),
        latents_std_digest=_normalization_digest(latents_std),
        encode_dtype=str(_encode_dtype(vae_module)),
        device=str(device),
    )


class VisualLatentCache:
    """Request-local LRU cache of clean, posterior-sampled, rounded and
    normalized visual condition latents -- `encode_keyframe_condition`'s
    output, memoized so repeating the SAME keyframe/reference within one
    request (across `quantity` outputs, or across Director windows that share
    a reference pool) does not re-run the VAE encoder for it.

    Owned by one request (`_MiniMaxH3Ctx.reference_cache` in main.py), never
    process-wide, and released with it (`release()`) on success, cancellation
    or failure -- an instance that outlived its request would let one run's
    fitted media alias a later, unrelated request's cache key collision odds
    down to nothing, but the byte cost of holding it has no reason to survive
    past the request that built it.

    Entries are CPU clones, mirroring `PromptEmbedCache`/`embed_cache.py`: a
    hit never pins GPU memory (the byte budget below is host RAM, not VRAM),
    and a caller mutating its own copy cannot corrupt the entry. Only the
    CLEAN latent is ever stored here -- the per-use noise augmentation
    (`schedule.scale_noise`) and patchify run on EVERY use, hit or miss, off
    the request's own generator, so a hit changes nothing about the
    generator's draw count, order or resulting state.

    Sized by RETAINED BYTES (`budget_bytes`), not entry count -- a keyframe at
    the target canvas and a `ref2va` video reference's frame-stack latent
    differ by two orders of magnitude, so a fixed entry cap would either waste
    the budget on tiny keyframes or let a few large references exhaust it.
    Eviction is deterministic LRU; a single entry whose own cost exceeds the
    WHOLE budget is never stored at all (`oversize_skips`) -- it falls back to
    the ordinary uncached path every time it recurs rather than evicting
    everything else to make room for one entry that would immediately be the
    next eviction target anyway.
    """

    def __init__(self, budget_bytes: int = VISUAL_LATENT_CACHE_BUDGET_BYTES) -> None:
        self._budget = max(0, int(budget_bytes))
        self._store: "OrderedDict[VisualLatentCacheKey, Tensor]" = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.oversize_skips = 0

    @staticmethod
    def _cost(tensor: Tensor) -> int:
        return tensor.numel() * tensor.element_size()

    def contains(self, key: VisualLatentCacheKey) -> bool:
        """Membership check with no side effect -- no LRU touch, no hit/miss
        counter movement. Used to decide whether a VAE still needs to be
        placed on device BEFORE actually reading the cache for real (main.py's
        `visual_references_need_encode`/`keyframes_need_encode`); counting
        that peek as a hit would double-count against the real read that
        follows it on the same key.
        """
        return key in self._store

    def get_on_device(self, key: VisualLatentCacheKey, *, device: Any, dtype: torch.dtype) -> Optional[Tensor]:
        """The cached latent for `key`, materialized fresh on `device`/`dtype`,
        or `None` on a miss. Always a tensor the caller owns outright (mirrors
        `embed_cache._to_device_tree`): `Tensor.to()` returns the SAME object
        when no conversion is needed, so this clones in that case rather than
        handing out a reference into the cache's own storage.
        """
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        self._store.move_to_end(key)
        self.hits += 1
        moved = entry.to(device=device, dtype=dtype)
        return moved.clone() if moved is entry else moved

    def put(self, key: VisualLatentCacheKey, latent: Tensor) -> None:
        """Store a CPU clone of `latent` under `key`, evicting LRU entries
        until the budget is met. A `latent` whose own cost exceeds the whole
        budget is skipped rather than stored (`oversize_skips`) -- admission
        is decided from `latent`'s OWN shape/dtype, before any transfer or
        copy: `numel()`/`element_size()` are pure metadata reads, so an
        oversized latent never pays for the `.detach()/.to("cpu")/.clone()`
        the skip exists to avoid.
        """
        cost = self._cost(latent)
        if cost > self._budget:
            self.oversize_skips += 1
            return
        cpu_latent = latent.detach().to("cpu").clone()
        if key in self._store:
            self._bytes -= self._cost(self._store[key])
        self._store[key] = cpu_latent
        self._store.move_to_end(key)
        self._bytes += cost
        while self._bytes > self._budget and self._store:
            _, evicted = self._store.popitem(last=False)
            self._bytes -= self._cost(evicted)
            self.evictions += 1

    def release(self) -> None:
        """Drop every entry. Called by the request that owns this cache on
        success, cancellation and failure alike -- see this class's own
        docstring for why an instance must never outlive its request."""
        self._store.clear()
        self._bytes = 0

    @property
    def retained_bytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._store)


def _encode_visual_latent(
    cache: Optional[VisualLatentCache], cache_key: Optional[VisualLatentCacheKey],
    vae_module: Any, pixels_uint8: Tensor, *, latents_mean: Any, latents_std: Any,
) -> Tensor:
    """`encode_keyframe_condition`, memoized in `cache` under `cache_key` when
    both are given (`cache=None` -- no caller-supplied cache -- and
    `cache_key=None` -- an audio reference, which never reaches here -- both
    fall straight through to a plain encode).

    A HIT never touches `vae_module` at all, which is what lets a caller
    decide whether the video VAE needs to be resident BEFORE calling this
    (`visual_references_need_encode`/`keyframes_need_encode` run this same
    membership check up front, off the identical key-building path, so the
    two can never disagree about which references are misses).
    """
    if cache is not None and cache_key is not None:
        hit = cache.get_on_device(cache_key, device=pixels_uint8.device, dtype=torch.float32)
        if hit is not None:
            return hit
    latent = encode_keyframe_condition(vae_module, pixels_uint8, latents_mean=latents_mean, latents_std=latents_std)
    if cache is not None and cache_key is not None:
        cache.put(cache_key, latent)
    return latent


def _keyframe_fit_role(index: int) -> str:
    return "keyframe:anchor" if index == 0 else "keyframe:follower"


def keyframe_visual_cache_key(
    image: Image.Image, index: int, *, height: int, width: int, vae_module: Any, weight_revision: Any,
    latents_mean: Any, latents_std: Any, device: Any,
) -> VisualLatentCacheKey:
    """The :class:`VisualLatentCacheKey` `prepare_keyframe_condition_rows`
    would build for `keyframes[index]` -- shared with `keyframes_need_encode`
    so the two can never disagree about which keyframes are misses."""
    fitted = fit_keyframe_to_canvas(image, height, width, is_geometry_anchor=index == 0)
    fitted_array = np.array(fitted.convert("RGB"))
    return visual_latent_cache_key(
        fitted_array, fit_role=_keyframe_fit_role(index), target_size=(height, width), frame_selection=(),
        vae_module=vae_module, weight_revision=weight_revision,
        latents_mean=latents_mean, latents_std=latents_std, device=device,
    )


def keyframes_need_encode(
    keyframes: list, *, cache: Optional[VisualLatentCache], height: int, width: int, vae_module: Any,
    weight_revision: Any, latents_mean: Any, latents_std: Any, device: Any,
) -> bool:
    """Whether at least one of `keyframes` would still need an actual VAE
    encode -- a cache miss, or no cache at all. `keyframes=[]` needs none.
    Mirrors `visual_references_need_encode`'s role for `ref2va` references:
    a caller decides whether the video VAE has to be placed on device from
    this, BEFORE `prepare_keyframe_condition_rows` runs."""
    if not keyframes:
        return False
    if cache is None:
        return True
    for index, image in enumerate(keyframes):
        key = keyframe_visual_cache_key(
            image, index, height=height, width=width, vae_module=vae_module, weight_revision=weight_revision,
            latents_mean=latents_mean, latents_std=latents_std, device=device,
        )
        if not cache.contains(key):
            return True
    return False


def prepare_keyframe_condition_rows(
    keyframes: list, anchors: tuple, *, vae_module: Any, height: int, width: int,
    patch_size: tuple[int, int, int], device: Any, dtype: torch.dtype,
    latents_mean: Any, latents_std: Any, generator: torch.Generator,
    cache: Optional[VisualLatentCache] = None, weight_revision: Any = None,
) -> Tensor:
    """`fl2va` keyframes -> canvas-fit -> VAE-encode (memoized in `cache`,
    when given) -> noise to `t = KEYFRAME_NOISE_AUG` -> patchify ->
    concatenate, in packed (`keyframe_anchors`) order.

    One noise draw per condition from `generator`, in order -- callers MUST
    draw this BEFORE the request's own video/audio noise (dossier "One
    generator, three draws, in order: conditioning noise -> video noise ->
    audio noise") -- drawn on EVERY keyframe regardless of a cache hit or
    miss, so caching never changes the generator's draw count, order or
    resulting state. Returns an empty `(0, video_patch_dim)` tensor for
    `keyframes=[]` (`t2va`, or an `fl2va` mode with no anchors resolved).
    """
    video_patch_dim = None
    rows: list[Tensor] = []
    for index, (image, _anchor) in enumerate(zip(keyframes, anchors)):
        fitted = fit_keyframe_to_canvas(image, height, width, is_geometry_anchor=index == 0)
        fitted_array = np.array(fitted.convert("RGB"))
        pixels = _pixels_from_array(fitted_array, device)
        cache_key = None
        if cache is not None:
            cache_key = visual_latent_cache_key(
                fitted_array, fit_role=_keyframe_fit_role(index), target_size=(height, width), frame_selection=(),
                vae_module=vae_module, weight_revision=weight_revision,
                latents_mean=latents_mean, latents_std=latents_std, device=device,
            )
        latent = _encode_visual_latent(
            cache, cache_key, vae_module, pixels, latents_mean=latents_mean, latents_std=latents_std,
        )
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
    cache: Optional[VisualLatentCache] = None, cache_key: Optional[VisualLatentCacheKey] = None,
) -> tuple[Tensor, Tensor]:
    """One visual reference (image OR video frame stack) -> its CLEAN
    condition latent (memoized in `cache` under `cache_key`, when given) and
    its noised, patchified rows.

    ONE draw off `generator`, whatever the reference's frame count -- the
    noise is drawn `latent.shape`-wide in a single call, so a video reference
    consumes exactly as much generator state as an image one does. Drawn on
    EVERY call regardless of a cache hit or miss, so caching never changes the
    generator's draw count, order or resulting state.
    """
    latent = _encode_visual_latent(
        cache, cache_key, vae_module, pixels, latents_mean=latents_mean, latents_std=latents_std,
    )
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


def _reference_fit_signature(reference: ReferenceMedia) -> Optional[tuple[str, np.ndarray, tuple[int, int], tuple]]:
    """`(fit_role, fitted_pixel_array, target_size, frame_selection)` for one
    ALREADY-NORMALIZED visual reference -- the exact content `prepare_
    reference_conditioning` would VAE-encode for it, computed WITHOUT
    touching the VAE. `None` for an audio reference (nothing visual to
    encode or cache).

    Shared by the actual encode path and by `visual_references_need_encode`'s
    pre-check so the two can never disagree about which references are
    misses -- `prepare_reference_conditioning`'s own loop calls this too
    rather than re-deriving the same fields inline.
    """
    if reference.kind == "image":
        pixels = np.array(reference.image.convert("RGB"))
        return "reference:image", pixels, (reference.image.height, reference.image.width), ()
    if reference.kind == "video":
        frames = np.asarray(reference.frames)
        frames = frames[: snap_reference_video_frames(frames.shape[0])]
        return "reference:video", frames, (frames.shape[1], frames.shape[2]), (frames.shape[0],)
    return None


def reference_visual_cache_key(
    reference: ReferenceMedia, *, vae_module: Any, weight_revision: Any, latents_mean: Any, latents_std: Any,
    device: Any,
) -> Optional[VisualLatentCacheKey]:
    """The :class:`VisualLatentCacheKey` `prepare_reference_conditioning`
    would build for `reference`, or `None` for an audio reference."""
    signature = _reference_fit_signature(reference)
    if signature is None:
        return None
    fit_role, fitted_pixels, target_size, frame_selection = signature
    return visual_latent_cache_key(
        fitted_pixels, fit_role=fit_role, target_size=target_size, frame_selection=frame_selection,
        vae_module=vae_module, weight_revision=weight_revision,
        latents_mean=latents_mean, latents_std=latents_std, device=device,
    )


def visual_references_need_encode(
    references: list[ReferenceMedia] | tuple[ReferenceMedia, ...], *, cache: Optional[VisualLatentCache],
    vae_module: Any, weight_revision: Any, latents_mean: Any, latents_std: Any, device: Any,
) -> bool:
    """Whether at least one VISUAL (image/video) reference in `references`
    would still need an actual VAE encode -- a cache miss, or no cache at
    all. An audio-only (or empty) `references` needs none, same as today.

    Used by main.py's `_build_ref2va_layout` to decide whether the video VAE
    has to be placed on device before `prepare_reference_conditioning` runs
    -- extends that placement gate: a reference set that hits in full needs
    the video VAE no more than an audio-only one does.
    """
    for reference in references:
        if reference.kind == "audio":
            continue
        if cache is None:
            return True
        key = reference_visual_cache_key(
            reference, vae_module=vae_module, weight_revision=weight_revision,
            latents_mean=latents_mean, latents_std=latents_std, device=device,
        )
        if key is None or not cache.contains(key):
            return True
    return False


def prepare_reference_conditioning(
    references: list[ReferenceMedia] | tuple[ReferenceMedia, ...], *, vae_module: Any, audio_vae_module: Any = None,
    patch_size: tuple[int, int, int], device: Any, dtype: torch.dtype, latents_mean: Any, latents_std: Any,
    generator: torch.Generator, audio_channels: int = AUDIO_CHANNELS,
    cache: Optional[VisualLatentCache] = None, weight_revision: Any = None,
) -> ReferenceConditioning:
    """Encode ALREADY-NORMALIZED `ref2va` references into everything the
    layout and the sampler need (`MiniMaxH3Ref2VAReferenceEncoderStep`).

    `references` must have come through :func:`normalize_references` -- this
    function does no fitting or resampling, it only encodes, so handing it
    raw media silently conditions on the wrong resolution and rate.

    Visual references (image and video) are VAE-encoded (memoized in `cache`,
    when given), noise-augmented to `t = KEYFRAME_NOISE_AUG` and patchified;
    a video's frame count is first snapped down to `17 * n + 5`
    (:func:`snap_reference_video_frames`). Soundtracks go through the AUDIO
    VAE clean, at `t = 1.0`, and are packed channel-major -- `audio_vae_
    module` is required as soon as any reference carries one; a soundtrack is
    never cached (out of this cache's scope -- see `VisualLatentCache`'s own
    docstring).

    One noise draw per VISUAL reference off `generator`, in packed order --
    the same "one generator, three draws, in order: conditioning noise ->
    video noise -> audio noise" contract `prepare_keyframe_condition_rows`
    documents, and callers MUST draw this BEFORE the request's own video and
    audio noise -- on EVERY visual reference regardless of a cache hit or
    miss, so caching never changes the generator's draw count, order or
    resulting state. A soundtrack draws nothing at all (the audio VAE returns
    the posterior mean), so adding one to a request does not shift the video
    noise that follows.
    """
    blocks: list[ReferenceBlock] = []
    condition_latents: list[Tensor] = []
    audio_condition_latents: list[Tensor] = []
    packed_rows: list[Tensor] = []

    for reference in references:
        signature = _reference_fit_signature(reference)
        if signature is not None:
            fit_role, fitted_pixels, target_size, frame_selection = signature
            pixels_tensor = (
                _pixels_from_array(fitted_pixels, device) if reference.kind == "image"
                else _pixels_from_frames(fitted_pixels, device)
            )
            cache_key = None
            if cache is not None:
                cache_key = visual_latent_cache_key(
                    fitted_pixels, fit_role=fit_role, target_size=target_size, frame_selection=frame_selection,
                    vae_module=vae_module, weight_revision=weight_revision,
                    latents_mean=latents_mean, latents_std=latents_std, device=device,
                )
            latent, packed = _encode_and_pack_visual_reference(
                vae_module, pixels_tensor, patch_size=patch_size, device=device,
                dtype=dtype, latents_mean=latents_mean, latents_std=latents_std, generator=generator,
                cache=cache, cache_key=cache_key,
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
