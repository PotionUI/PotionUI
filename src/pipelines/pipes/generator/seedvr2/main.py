"""Generator for the native SeedVR2 family (image restoration / upscale).

SeedVR2 is not a txt2img sampler: it is a **one-step** restoration DiT. Per image
the pipe area-resizes the low-res input toward a target area (the actual spatial
upscale is this bicubic step; the DiT restores detail at that resolution), VAE-
encodes it to a 16-channel conditioning latent, then runs a SINGLE DiT forward at
timestep 1000 over a 33-channel input ``[z | cond | task-flag]`` where ``z`` is
pure seeded noise. The clean latent is ``x0 = z - v`` (v-prediction, one-step
APT) — no CFG, no scheduler/denoise loop. The latent is decoded through the self-
normalizing causal-video VAE and color-corrected back to the resized input.

The heavy lifting (VAE encode/decode with the self-normalizing branch, DiT GPU
placement / streaming / partial residency, tiled decode + OOM nets) is inherited
from :class:`NativeGenerator`; :class:`SeedVR2NativeGenerator` only adds the
one-step ``upscale`` method. SeedVR2 has no text encoder, so the generator is
built with ``te=None`` (its conditioning is the fixed prompt embedding carried by
the bundle and passed into ``upscale`` by the pipe).
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

import numpy as np
import torch
from PIL import Image

from src.pipelines.outputs import (
    CompareImagesGenerationOutput,
    GalleryGenerationOutput,
    ImageGenerationOutput,
    VideoGenerationOutput,
)
from src.platform.observability.logger import logger
from src.platform.observability.profiling import get_profiler, profiling_enabled
from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.memory.residency import (
    effective_free_vram_gb,
    free_vram_gb,
    get_residency_registry,
)
from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import (
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import Icon
from src.pipelines.pipes._shared.generation.native_generator import (
    build_native_generator,
    register_native_generator,
)
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes.generator.seedvr2.color_fix import color_correct
from src.pipelines.pipes.generator.seedvr2.resize import CROP_MULTIPLE, prepare_input

SEEDVR2_TIMESTEP = 1000.0

# -- VRAM-aware tile sizing for the video clip's VAE encode/decode ---
#
# ``upscale_video`` processes whole clips through the causal-3D VAE without
# going through ``NativeGenerator.encode_image``/``decode``, which assume a
# T=1 still image for this self-normalizing VAE. At high target resolutions the
# encoder's first down-block and the decoder's last up-block (full pixel
# resolution, T frames, before/after any spatial downsample) are the OOM
# sources — not the windowed-attention DiT forward, whose per-token cost is
# bounded by the fixed (4,3,3) window count regardless of resolution.
#
# The sizing below mirrors ``engine.py``'s ``_decode_causal3d_tiled`` but is
# re-implemented rather than imported: that method is private to the still-image
# ``decode()`` path this video loop doesn't call.
_MIN_DECODE_TILE_LATENT = 16          # floor, latent px (mirrors engine.py)
_MIN_ENCODE_TILE_PIXEL = 256          # floor, image px (64x the latent floor)
#
# Decode spike per latent px PER OUTPUT FRAME. SeedVR2's VAE is a 16-channel
# ``[128,256,512,512]`` inflated SD3 AE, not the 128-channel LTX/Wan latent
# engine.py's 1.2 constant assumes.
#
# The decode's real spike is its top up-block at full pixel resolution, whose
# buffer is ``(1, 128, T_out, 8t, 8t)`` fp32 for a ``t``-latent-px tile — it
# scales with the OUTPUT frame count ``T_out = 1 + 4*(T_lat - 1)``, NOT the
# latent-frame count, so the tile sizer is fed ``T_out`` (see ``_decode_clip``);
# feeding it ``T_lat`` under-counts the 4x temporal expansion. 0.05
# MB/latent-px/output-frame puts the model peak at ~1.3GB for a tile=40 window
# and ~20GB for the whole clip untiled (~2 full-res 128-ch fp32 buffers).
_CAUSAL3D_DECODE_MB_PER_LATENT_PX = 0.05
# The encoder's spike is its architecturally-symmetric bottom DOWN-block, at the
# same full pixel resolution, channel width AND frame count as the decoder's top
# block, so its cost per INPUT pixel per input frame is exactly the decoder's
# per-latent-px-per-output-frame figure over the 8x8 spatial ratio (64 image px
# per latent px) — an EXACT ratio for the two mirrored blocks, not a guess.
# Being off only shrinks/grows the tile via the feathered-seam blend, never the
# encoded/decoded values, so a miscalibration costs VRAM headroom or one extra
# shrink-on-OOM retry, not correctness.
_CAUSAL3D_ENCODE_MB_PER_PIXEL = _CAUSAL3D_DECODE_MB_PER_LATENT_PX / 64.0
_TILE_VRAM_FRACTION = 0.75             # mirrors engine.py's _DECODE_TILE_VRAM_FRACTION


def _adaptive_tile_size(
    long_axis: int,
    floor: int,
    *,
    free_vram_gb_value: Optional[float],
    mb_per_px: float,
    frames: int,
    vram_fraction: float = _TILE_VRAM_FRACTION,
) -> int:
    """Solve a square tile edge (px) whose ``(tile*1.125)^2 * frames`` spike
    fits ``free_vram_gb_value * vram_fraction`` at ``mb_per_px`` MB/px/frame,
    snapped to a multiple of 8 and floored at ``floor``. ``1.125`` accounts for
    the tile's overlap margin (``overlap ~= tile/8``), mirroring the same
    fudge factor in ``engine.py``'s ``_decode_causal3d_tiled``.

    Starts from the FULL long axis (rounded UP to a multiple of 8), unlike
    engine.py's still-image tiler which starts at half the long axis: SeedVR2's
    video clips are short and self-normalizing, so when free VRAM allows the
    whole clip we want ONE tile (``tile >= max(h, w)`` makes ``tiled_encode`` /
    ``tiled_decode`` no-op to a byte-identical whole-clip pass, no seams) rather
    than forcing a 2x2 split the way half-axis always would. The VRAM cap below
    still shrinks this when the card can't afford the whole clip. Returns the
    full-axis start unchanged when free VRAM can't be queried (CPU / no CUDA)."""
    tile = max(floor, ((long_axis + 7) // 8) * 8)
    if free_vram_gb_value is None:
        return tile
    budget_mb = free_vram_gb_value * vram_fraction * 1024.0
    budget_area = budget_mb / max(1e-9, mb_per_px * max(1, frames))
    cap = int((budget_area ** 0.5) / 1.125) // 8 * 8
    return max(floor, min(tile, cap))


# -- VRAM-aware temporal batch sizing -----------------------
#
# The video path processes the clip in 4n+1-frame temporal batches, one on the
# GPU at a time; ``batch_size`` (frames per batch) is the temporal VRAM knob.
# A batch's peak is the DiT weights plus its sampling activations, and the
# activations scale with the DiT's TOKEN count -- ``latent_frames`` times the
# spatial tokens per latent frame -- which is a function of the OUTPUT
# RESOLUTION, not of free VRAM alone. Sizing the batch from free VRAM while
# blind to resolution (the previous model) picks the same batch for a 512x512
# clip and a 4K clip; at 4K that hugely over-estimates and pays a full doomed
# encode + DiT forward before the OOM ladder rescues it (the ~2min wasted
# attempt in the 4K profile). The model here is
#
#     peak_gb(lf) ~= weights_gb + ACT_GB_PER_TOKEN * lf * spatial_tokens
#
# solved for the largest ``lf`` whose peak fits ``FRACTION`` of live free VRAM.
#
#   * ACT_GB_PER_TOKEN: activation GB per latent token, calibrated on the 7B 4K
#     profile -- a 4-latent-frame clip at 480x270 latent (32400 spatial
#     tokens/frame) fit with headroom while an 8-latent-frame clip at the same
#     geometry exceeded a 32GB card. Fixed to the 7B (the heavy, OOM-prone
#     variant): a smaller DiT has both smaller weights AND smaller per-token
#     activation, so a 7B-calibrated figure only ever UNDER-shoots the batch
#     there -- safe (never an OOM), just a touch conservative.
#   * weights_gb is read from the live DiT (``estimated_vram_gb``) so the reserve
#     tracks the 3B/7B variant actually loaded; falls back to the 7B size.
#
# The VAE tile ladder and the batch OOM ladder remain the safety nets underneath.
_SEEDVR2_ACT_GB_PER_TOKEN = 8.4e-5
_SEEDVR2_BATCH_VRAM_FRACTION = 0.92
_SEEDVR2_MAX_BATCH = 49               # matches the preset form's max (4*12+1)
_SEEDVR2_DEFAULT_WEIGHTS_GB = 15.0    # 7B fp16 resident size; fallback when unknown
# VAE /8 spatial downsample, then the NaDiT patchify folds (2,2) spatial patches
# into one token (mirrors SeedVR2Config.patch_size == (1, 2, 2)).
_SEEDVR2_VAE_DOWNSAMPLE = 8
_SEEDVR2_PATCH_HW = (2, 2)


def _spatial_tokens_per_latent_frame(height: int, width: int) -> int:
    """DiT spatial token count for ONE latent frame of a ``height x width`` px
    clip: the VAE downsamples /8, then the NaDiT patchify folds (2,2) spatial
    patches, so tokens/frame = ``(H/8/2) * (W/8/2)``. Pure and testable; the
    temporal (latent-frame) factor is applied by the caller."""
    ph, pw = _SEEDVR2_PATCH_HW
    d = _SEEDVR2_VAE_DOWNSAMPLE
    return max(1, (int(height) // d // ph) * (int(width) // d // pw))


def _auto_batch_size(
    free_vram_gb_value: Optional[float],
    *,
    spatial_tokens_per_latent_frame: int,
    weights_gb: float,
) -> int:
    """Pick a 4n+1 temporal batch size whose modeled DiT peak fits ~92% of live
    free VRAM at the clip's OUTPUT resolution.

    Returns the old default (5) when free VRAM can't be queried (CPU / no CUDA),
    so behaviour is unchanged off-GPU. Solves the largest latent-frame count
    whose ``weights_gb + ACT_GB_PER_TOKEN * lf * spatial_tokens`` fits the
    budget, converts to frames (``1 + 4k``), and clamps to ``[5, 49]`` (never
    smaller than the historical default, never past the form's max)."""
    if free_vram_gb_value is None or free_vram_gb_value <= 0:
        return 5
    weights = weights_gb if weights_gb and weights_gb > 0 else _SEEDVR2_DEFAULT_WEIGHTS_GB
    act_budget = _SEEDVR2_BATCH_VRAM_FRACTION * float(free_vram_gb_value) - weights
    per_latent_frame = _SEEDVR2_ACT_GB_PER_TOKEN * max(1, int(spatial_tokens_per_latent_frame))
    latent_frames = int(act_budget // per_latent_frame) if act_budget > 0 else 0
    if latent_frames < 1:
        latent_frames = 1
    frames = 1 + 4 * (latent_frames - 1)   # T' latent frames <- 1+4k input frames
    return max(5, min(_SEEDVR2_MAX_BATCH, frames))


def _count_windows(total_frames: int, batch_size: int, temporal_overlap: int) -> int:
    """Closed-form equivalent of ``len(batching.plan_batches(total_frames, ...)[0])``
    that never materialises the window list -- for the progress-bar estimate a
    real container's frame-count hint can be in the millions, and building every
    ``(start, end)`` tuple just to take ``len()`` is O(frame count) memory for a
    number nothing else needs.

    Mirrors ``plan_batches``' loop exactly: every window before the tail is a
    full ``batch_size``-wide step (never dropped, since ``overlap < batch_size``
    always holds once ``plan_batches``' own overlap>=batch_size reset applies),
    so only the final, ``total``-reaching window can ever be the one dropped for
    re-covering nothing but the overlap region.
    """
    batch_size = max(1, int(batch_size))
    overlap = max(0, int(temporal_overlap))
    if overlap >= batch_size:
        overlap = 0
    total = int(total_frames)
    if total <= 0:
        return 0
    if total <= batch_size:
        return 1

    step = batch_size - overlap if overlap > 0 else batch_size
    # Smallest k such that window k's end (`k*step + batch_size`, capped at
    # `total`) reaches `total` -- every window before it is `end < total` and
    # therefore always counted (see the docstring above).
    k_last = -(-(total - batch_size) // step)  # ceil((total - batch_size) / step)
    tail = total - k_last * step
    return k_last if tail <= overlap else k_last + 1


def _discard_output(path: str) -> None:
    """Remove a video output and the audio-muxed sibling
    ``encode_video_with_audio`` may have written next to it, so a partial or
    superseded file is never left where something could publish it."""
    import os

    for candidate in (path, f"{path}.audio.mp4"):
        try:
            os.unlink(candidate)
        except FileNotFoundError:
            pass
        except OSError as exc:  # pragma: no cover - cleanup must not mask the real failure
            logger.warning("[GENERATOR SEEDVR2] could not remove %s: %s", candidate, exc)


def _sync_if_profiling(device: str) -> None:
    """Block on the CUDA stream so a ``time.perf_counter()`` delta measures actual
    kernel execution, not just async launch — but only when profiling is on (the
    sync itself has a cost we don't want on the normal path)."""
    if profiling_enabled() and torch.cuda.is_available():
        try:
            torch.cuda.synchronize(device)
        except Exception:  # noqa: BLE001 — timing accuracy is never worth a crash
            pass


def _window_count(latent_shape: "torch.Size | tuple") -> int:
    """Number of aligned (even-layer) 3D-Swin windows the NaDiT partitions the
    clip into, derived purely from the conditioning-latent geometry — the same
    ``(4,3,3)`` window op the arch runs, evaluated here for a profiler mark so a
    profile shows the per-forward window count without instrumenting the hot
    attention loop. Best-effort: any shape/import hiccup returns 0 (the mark is
    observability, never load-bearing)."""
    try:
        from src.platform.runtime.native.arch.seedvr2.config import SeedVR2Config
        from vendor.seedvr2.window import get_window_op

        cfg = SeedVR2Config()
        # DiT input latent (B, C, T', Hl, Wl); NaPatchIn patch (1,2,2) -> token
        # grid (T', Hl//pt_h, Wl//pt_w). Even layers use the aligned window op.
        _, _, t, hl, wl = latent_shape
        pt, ph, pw = cfg.patch_size
        grid = (int(t) // pt, int(hl) // ph, int(wl) // pw)
        return len(get_window_op(cfg.window_methods[0])(grid, cfg.window))
    except Exception:  # noqa: BLE001 — a profiler mark must never break generation
        return 0


def _effective_attention_backend(device: str) -> str:
    """Report which attention kernel the SeedVR2 DiT actually runs, for a
    profiler mark. The windowed varlen path uses the real ``flash_attn_varlen``
    kernel when it is installed and healthy; otherwise it falls back to the
    shared native dispatcher (sage/flash/sdpa) per window block, so the effective
    backend is whatever that dispatcher selects for this device."""
    try:
        from vendor.seedvr2 import attention as _sv2_attn

        if not _sv2_attn._flash_varlen_broken and _sv2_attn._probe_flash_varlen() is not None:
            return "flash_varlen"
        from src.platform.runtime.native.attention import get_attention_backend

        idx = torch.device(device).index if isinstance(device, str) and "cuda" in device else None
        return get_attention_backend(device_index=idx)
    except Exception:  # noqa: BLE001
        return "unknown"


@register_native_generator("seedvr2")
class SeedVR2NativeGenerator(NativeGenerator):
    """``NativeGenerator`` with a one-step restoration ``upscale`` (no denoise loop).

    Reuses the base class's VAE encode/decode (self-normalizing branch) and DiT
    placement helpers; the only new piece is the 33-channel one-step forward.
    """

    def upscale(
        self,
        pixels: "np.ndarray",
        prompt_embedding: torch.Tensor,
        *,
        seed: int,
        latent_noise_scale: float = 0.0,
        tile_size: Optional[int] = None,
        tile_overlap: Optional[int] = None,
    ) -> "np.ndarray":
        """Restore/upscale one already-resized image (uint8 ``HWC``) in a single
        DiT forward. Returns a uint8 ``(B, H, W, 3)`` array (B == 1)."""
        # 1. VAE-encode the input as a 1-frame clip through the SAME VRAM-adaptive
        #    tiled path the video branch uses (_encode_clip). A whole-image
        #    encode_image() call OOMs 4K targets: the encoder materializes
        #    128-channel buffers at FULL pixel resolution (~2GB each at 3856x2160,
        #    several alive at once), which only the tiler bounds. tiled_encode
        #    no-ops to a byte-identical whole pass when one tile covers the image,
        #    so small upscales are unchanged.
        device = self.device_plan.dit_device
        clip = pixels if pixels.ndim == 4 else pixels[None]  # (1, H, W, 3)
        cond_latent = self._encode_clip(
            clip, device, tile_size=tile_size, tile_overlap=tile_overlap,
        )
        dtype = self.dit.compute_dtype
        cond_latent = cond_latent.to(device=device, dtype=dtype)

        # 2. Placement + move the DiT onto the GPU (mirrors NativeGenerator.sample:
        #    fits whole when it can, else partial residency; foreign residents are
        #    evicted first, with the OOM-retry net behind it).
        self.placement = self._build_placement(cond_latent.shape)
        if self._resident("dit"):
            self._move_dit_to_gpu(device)
            self._maybe_compile()
        else:
            self._stream_dit_to_gpu(device, cond_latent.shape)

        generator = torch.Generator(device=device).manual_seed(int(seed))
        # 3. Pure noise z (the t=T start point); drawn first so it is seed-stable
        #    regardless of the optional latent-noise knob.
        z = torch.randn(cond_latent.shape, generator=generator, device=device, dtype=dtype)
        if latent_noise_scale and latent_noise_scale > 0:
            cond_latent = self._forward_noise(cond_latent, z, latent_noise_scale, generator)

        # 4. 33-channel input [noise | conditioning | all-ones task flag].
        flag = torch.ones(
            (cond_latent.shape[0], 1, *cond_latent.shape[2:]), device=device, dtype=dtype
        )
        vid = torch.cat([z, cond_latent, flag], dim=1)          # (B, 33, T, H, W)
        txt = prompt_embedding.to(device=device, dtype=dtype)
        timestep = torch.tensor([SEEDVR2_TIMESTEP], device=device, dtype=dtype)

        # 5. One forward -> v-prediction; clean latent x0 = z - v (one-step APT).
        with torch.no_grad():
            v = self.dit.module(vid, timestep, txt)
        x0 = z - v
        if not self._resident("dit"):
            self.dit.offload()

        # 6. Decode through the same tiled path as the video branch (latent-space
        #    tiles sized from live free VRAM, shrink-on-OOM ladder behind it),
        #    then release the VAE.
        out = self._decode_clip(x0, device, tile_size=tile_size, tile_overlap=tile_overlap)
        self.vae.offload()
        return out

    @staticmethod
    def _forward_noise(
        latent: torch.Tensor, z: torch.Tensor, scale: float, generator: torch.Generator
    ) -> torch.Tensor:
        """Forward-noise the conditioning latent along the flow-matching path at
        normalized time ``scale`` (t = 1000*scale): ``(1-s)*latent + s*aug``.

        ``aug`` mirrors the reference's ``z*0.1 + N(0,1)*0.05`` augmentation noise.
        A no-op at ``scale == 0`` (the SeedVR2-faithful default is 0)."""
        s = float(min(max(scale, 0.0), 1.0))
        aug = z * 0.1 + torch.randn(
            latent.shape, generator=generator, device=latent.device, dtype=latent.dtype
        ) * 0.05
        return (1.0 - s) * latent + s * aug

    # -- video (temporal-batch) restoration --------------------------------

    def upscale_video(
        self,
        clips: "Iterable[np.ndarray]",
        prompt_embedding: torch.Tensor,
        *,
        seed: int,
        latent_noise_scale: float = 0.0,
        tile_size: Optional[int] = None,
        tile_overlap: Optional[int] = None,
    ) -> "Iterator[np.ndarray]":
        """Restore/upscale already-resized, VAE-legal (4n+1) frame clips, one
        temporal batch at a time, yielding each decoded clip as it finishes.

        ``clips`` is an ITERABLE of uint8 ``(T, H, W, 3)`` arrays whose ``T``
        satisfies ``T % 4 == 1``; each yielded array is the matching decoded uint8
        ``(T, H*scale, W*scale, 3)`` clip (frame-count preserved). Pulling one clip
        at a time is what lets a caller stream a clip of any duration: the producer
        only assembles the next input clip once the previous one has been decoded
        and consumed. The DiT and VAE
        are moved onto the GPU once and kept resident across batches (weight thrash
        would dwarf the per-batch activation cost of a 3B model); only ONE batch's
        activations are live at a time, so VRAM is bounded by ``batch_size`` -- the
        temporal knob. The encode and decode of each batch are SPATIALLY TILED,
        sized from live free VRAM (shrinking further on OOM) rather than run
        whole-clip -- at high target resolutions the untiled causal-3D VAE
        encode/decode of a full frame is the actual OOM source, not the
        windowed-attention DiT forward (see the module-level tile-sizing
        docstring above). ``tile_size``/``tile_overlap`` (pixels) optionally cap
        the tile from the pipe config; the live-VRAM estimate still applies
        underneath them, and a whole-clip encode/decode is preserved
        byte-identically whenever the clip already fits inside one tile. The
        seed is reset per batch (reference ``set_seed(seed)``) so a batch's
        output is independent of its position in the clip.
        """
        device = self.device_plan.dit_device
        vae_device = self.device_plan.vae_device
        dtype = self.dit.compute_dtype
        txt = prompt_embedding.to(device=device, dtype=dtype)

        profiler = get_profiler()
        profiler.mark(
            "seedvr2.video.start",
            attention=_effective_attention_backend(device),
        )

        clips_done = 0
        dit_placed = False
        try:
            for i, clip in enumerate(clips):
                # 1. VAE-encode the whole (short) clip to a 5D conditioning latent.
                t_enc = time.perf_counter()
                cond_latent = self._encode_clip(
                    clip, vae_device, tile_size=tile_size, tile_overlap=tile_overlap,
                ).to(device=device, dtype=dtype)
                _sync_if_profiling(device)
                profiler.mark(
                    "seedvr2.encode", clip=i, seconds=time.perf_counter() - t_enc,
                    frames=int(clip.shape[0]), latent_frames=int(cond_latent.shape[2]),
                    height=int(clip.shape[1]), width=int(clip.shape[2]),
                )

                # 2. Place the DiT once, from the first batch's latent geometry.
                if not dit_placed:
                    self.placement = self._build_placement(cond_latent.shape)
                    if self._resident("dit"):
                        self._move_dit_to_gpu(device)
                        self._maybe_compile()
                    else:
                        self._stream_dit_to_gpu(device, cond_latent.shape)
                    dit_placed = True
                    profiler.mark(
                        "seedvr2.geometry", windows=_window_count(cond_latent.shape),
                        latent=[int(s) for s in cond_latent.shape],
                    )

                # 3. One forward -> v-prediction; x0 = z - v (one-step APT). The DiT
                #    builds a fresh Cache per call, so nothing leaks across batches.
                t_dit = time.perf_counter()
                generator = torch.Generator(device=device).manual_seed(int(seed))
                z = torch.randn(cond_latent.shape, generator=generator, device=device, dtype=dtype)
                if latent_noise_scale and latent_noise_scale > 0:
                    cond_latent = self._forward_noise(cond_latent, z, latent_noise_scale, generator)

                # 33-channel input [noise | conditioning | all-ones task flag]; T preserved.
                flag = torch.ones(
                    (cond_latent.shape[0], 1, *cond_latent.shape[2:]), device=device, dtype=dtype
                )
                vid = torch.cat([z, cond_latent, flag], dim=1)          # (B, 33, T, H, W)
                timestep = torch.tensor([SEEDVR2_TIMESTEP], device=device, dtype=dtype)

                with torch.no_grad():
                    v = self.dit.module(vid, timestep, txt)
                x0 = z - v
                _sync_if_profiling(device)
                profiler.mark("seedvr2.dit", clip=i, seconds=time.perf_counter() - t_dit)

                # 4. Decode the whole clip (self-normalizing; spatially tiled).
                t_dec = time.perf_counter()
                decoded = self._decode_clip(x0, vae_device, tile_size=tile_size, tile_overlap=tile_overlap)
                profiler.mark(
                    "seedvr2.decode", clip=i, seconds=time.perf_counter() - t_dec,
                    out_height=int(decoded.shape[1]), out_width=int(decoded.shape[2]),
                )
                clips_done += 1
                yield decoded
        finally:
            profiler.mark("seedvr2.video.end", clips=clips_done)
            # Free the per-clip GPU residency; weights survive on CPU (cached by
            # ModelLifecycle) and reload on the next generation.
            if not self._resident("dit"):
                self.dit.offload()
            self.vae.offload()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def _encode_clip(
        self, clip: "np.ndarray", device: str, *,
        tile_size: Optional[int] = None, tile_overlap: Optional[int] = None,
    ) -> torch.Tensor:
        """VAE-encode a uint8 ``(T,H,W,3)`` clip to a self-normalized 5D latent
        ``(1,16,T',H/8,W/8)`` (``T' = 1 + (T-1)/4``). The VAE stays resident (the
        caller offloads it once the whole clip is done).

        Spatially tiled (pixel-space, VRAM-adaptive; see module docstring) with a
        shrink-on-OOM retry loop — ``tiled_encode`` no-ops to a plain whole-clip
        ``encode`` when the clip already fits inside one tile, so this is
        byte-identical to the previous unconditional whole-clip call at low
        resolutions (e.g. the GPU-validated 256->512 case)."""
        self.vae.move_to(device)
        pixels = self._clip_to_pixels(clip).to(device=device, dtype=self.vae.compute_dtype)
        h, w = pixels.shape[-2], pixels.shape[-1]
        frames = pixels.shape[2]
        tile = _adaptive_tile_size(
            max(h, w), _MIN_ENCODE_TILE_PIXEL,
            free_vram_gb_value=effective_free_vram_gb(device),
            mb_per_px=_CAUSAL3D_ENCODE_MB_PER_PIXEL, frames=frames,
        )
        if tile_size and tile_size > 0:
            tile = min(tile, max(_MIN_ENCODE_TILE_PIXEL, (int(tile_size) // 8) * 8))
        overlap_cap = int(tile_overlap) if tile_overlap and tile_overlap > 0 else None

        shrinks = 0
        with torch.no_grad():
            while True:
                overlap = min(tile // 2, max(64, tile // 8))
                if overlap_cap is not None:
                    overlap = min(overlap, overlap_cap)
                try:
                    out = self.vae.module.tiled_encode(pixels, tile_size=tile, overlap=overlap)
                    get_profiler().mark(
                        "seedvr2.encode.tile", tile=tile, overlap=overlap,
                        shrinks=shrinks, whole_clip=tile >= max(h, w),
                    )
                    return out
                except torch.cuda.OutOfMemoryError:
                    if tile <= _MIN_ENCODE_TILE_PIXEL:
                        raise
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    tile = max(_MIN_ENCODE_TILE_PIXEL, (tile // 2 // 8) * 8)
                    shrinks += 1
                    logger.warning(
                        "[GENERATOR SEEDVR2] tiled encode OOM — shrinking tile to %d px", tile,
                    )

    def _decode_clip(
        self, latent: torch.Tensor, device: str, *,
        tile_size: Optional[int] = None, tile_overlap: Optional[int] = None,
    ) -> "np.ndarray":
        """Decode a 5D SeedVR2 latent to a uint8 ``(T,H,W,3)`` clip (self-
        normalizing VAE inverts its own scaling).

        Spatially tiled (latent-space, VRAM-adaptive; see module docstring) with a
        shrink-on-OOM retry loop; ``tiled_decode`` no-ops to a plain whole-clip
        ``decode`` when the clip already fits inside one tile. As a last resort,
        once the tile has shrunk to the floor and still OOMs, foreign GPU-resident
        models (never our own DiT/VAE) are evicted once and the floor tile is
        retried before giving up — mirrors ``NativeGenerator``'s
        ``_free_for_decode_retry`` without touching our own DiT's residency
        (offloading/restoring it mid per-batch-loop is a bigger change than this
        fix needs)."""
        self.vae.move_to(device)
        latent = latent.to(device=device, dtype=self.vae.compute_dtype)
        h, w = latent.shape[-2], latent.shape[-1]
        # Decode VRAM scales with the OUTPUT frame count the top up-block
        # materializes (``T_out = 1 + 4*(T_lat - 1)`` for the t4 VAE), not the
        # latent-frame count — feeding the sizer T_out is what keeps the tile
        # honest across batch sizes.
        latent_frames = latent.shape[2] if latent.ndim == 5 else 1
        frames = 1 + 4 * (latent_frames - 1) if latent_frames > 1 else 1
        tile = _adaptive_tile_size(
            max(h, w), _MIN_DECODE_TILE_LATENT,
            free_vram_gb_value=effective_free_vram_gb(device),
            mb_per_px=_CAUSAL3D_DECODE_MB_PER_LATENT_PX, frames=frames,
        )
        if tile_size and tile_size > 0:
            tile = min(tile, max(_MIN_DECODE_TILE_LATENT, int(tile_size) // 8))
        overlap_cap = (int(tile_overlap) // 8) if tile_overlap and tile_overlap > 0 else None

        freed_foreign_residents = False
        shrinks = 0
        with torch.no_grad():
            while True:
                overlap = min(tile // 2, max(8, tile // 8))
                if overlap_cap is not None:
                    overlap = min(overlap, max(0, overlap_cap))
                try:
                    pixels = self.vae.module.tiled_decode(latent, tile_size=tile, overlap=overlap)
                    get_profiler().mark(
                        "seedvr2.decode.tile", tile=tile, overlap=overlap,
                        shrinks=shrinks, evicted_foreign=freed_foreign_residents,
                        whole_clip=tile >= max(h, w),
                    )
                    break
                except torch.cuda.OutOfMemoryError:
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    if tile > _MIN_DECODE_TILE_LATENT:
                        tile = max(_MIN_DECODE_TILE_LATENT, (tile // 2 // 8) * 8)
                        shrinks += 1
                        logger.warning(
                            "[GENERATOR SEEDVR2] tiled decode OOM — shrinking tile to %d latent px", tile,
                        )
                        continue
                    if not freed_foreign_residents:
                        freed_foreign_residents = True
                        logger.warning(
                            "[GENERATOR SEEDVR2] tiled decode OOM at floor tile — evicting foreign "
                            "GPU residents and retrying",
                        )
                        get_residency_registry().offload_all(device, exclude=[self.dit, self.vae])
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                        continue
                    raise
        return self._clip_frames_to_uint8(pixels)

    @staticmethod
    def _clip_to_pixels(clip: "np.ndarray") -> torch.Tensor:
        """uint8 ``(T,H,W,3)`` -> float ``(1,3,T,H,W)`` in ``[-1, 1]``."""
        t = torch.from_numpy(np.ascontiguousarray(clip)).float()    # T,H,W,3
        t = t.permute(3, 0, 1, 2).unsqueeze(0).contiguous()         # 1,3,T,H,W
        return t / 127.5 - 1.0

    @staticmethod
    def _clip_frames_to_uint8(pixels: torch.Tensor) -> "np.ndarray":
        """float ``(1,3,T,H,W)`` in ``[-1, 1]`` -> uint8 ``(T,H,W,3)``."""
        p = pixels.detach().float().clamp(-1.0, 1.0)
        p = ((p + 1.0) * 127.5).round().to(torch.uint8)[0]          # 3,T,H,W
        return p.permute(1, 2, 3, 0).contiguous().cpu().numpy()     # T,H,W,3


class GeneratorSeedVR2Pipe(BasePipe):
    name = "generator"
    description = "Native SeedVR2 one-step restoration upscaler (no text encoder, no CFG, no denoise loop)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "scale": 2.0,
            "target_short_side": 0,
            "color_correction": "wavelet",
            "latent_noise_scale": 0.0,
            "input_noise_scale": 0.0,
            "seed": -1,
            "device": "cuda",
            # Ceilings, not fixed sizes: the VAE encode/decode auto-tiles UNDER
            # these by live free VRAM. Defaulted high (2048px) so a roomy card
            # decodes/encodes a 1080p clip in 1-2 tiles instead of being pinned
            # to a needless 2x2 split. Small cards are unaffected: the live-VRAM
            # sizer + OOM ladder keep the actual tile well below this ceiling.
            "tile_size": 2048,
            "tile_overlap": 256,
            # Video (temporal) knobs — ignored on the image path.
            "batch_size": 0,
            "temporal_overlap": 0,
            "prepend_frames": 0,
            "uniform_batch_size": True,
            "keep_audio": True,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("scale", float, 2.0,
                           "Upscale factor (output area = scale^2 x input); ignored when target_short_side > 0",
                           required=False, min_value=1.0, max_value=8.0),
            PipeConfigSpec("target_short_side", int, 0,
                           "Target short-side in px (0 -> use scale). When >0 this wins over scale.",
                           required=False, min_value=0, max_value=4096),
            PipeConfigSpec("color_correction", str, "wavelet",
                           "Match output colors back to the resized input", required=False,
                           choices=["wavelet", "adain", "none"]),
            PipeConfigSpec("latent_noise_scale", float, 0.0,
                           "Forward-noise the conditioning latent (0 = SeedVR2-faithful; softens artifacts)",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("input_noise_scale", float, 0.0,
                           "Small gaussian on the input pixels before encode (0 = off)",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("seed", int, -1, "Seed (-1 -> per-image random)", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            # The engine's causal-3D decode auto-tiles by live free VRAM (with a
            # shrink-on-OOM net); image encode/decode size their own tile from VRAM
            # too. These act as a CEILING on top of that live-VRAM estimate for the
            # video path's per-batch VAE encode/decode — lower them to force
            # extra headroom at very high target resolutions; the auto-sizing still
            # applies underneath (shrinks further, never grows past this ceiling).
            PipeConfigSpec("tile_size", int, 2048,
                           "VAE encode/decode tile size ceiling (px); auto-tiles by live VRAM under this",
                           required=False, min_value=256, max_value=2048),
            PipeConfigSpec("tile_overlap", int, 256, "VAE encode/decode tile overlap ceiling (px)",
                           required=False, min_value=0, max_value=512),
            # -- Video (temporal) knobs. Ignored when the input is an image. ----
            PipeConfigSpec("batch_size", int, 0,
                           "Frames per temporal batch (snapped to the VAE's 4n+1 lattice: 1,5,9,…). "
                           "The temporal VRAM knob — one batch is on the GPU at a time. "
                           "0 = auto-size to ~92% of live free VRAM at the output resolution "
                           "(recommended); the OOM ladder "
                           "halves it and re-runs if a batch ever exceeds real VRAM.",
                           required=False, min_value=0, max_value=49),
            PipeConfigSpec("temporal_overlap", int, 0,
                           "Overlapping frames cross-faded between consecutive batches (0 = hard cut)",
                           required=False, min_value=0, max_value=16),
            PipeConfigSpec("prepend_frames", int, 0,
                           "Reversed head frames prepended before upscaling to reduce clip-start "
                           "artifacts (auto-removed from the output)",
                           required=False, min_value=0, max_value=16),
            PipeConfigSpec("uniform_batch_size", bool, True,
                           "Pad a short final batch up to the full batch size (avoids seam artifacts)",
                           required=False),
            PipeConfigSpec("keep_audio", bool, True,
                           "Mux the source video's audio track into the upscaled output", required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, False, "Low-res images to restore/upscale", is_array=True),
            PipeInputSpec("video", IOType.VIDEO, False, "Low-res video(s) to restore/upscale", is_array=True),
            PipeInputSpec("model", IOType.MODEL, True, "SeedVR2 model bundle", is_array=False),
            PipeInputSpec("seed", IOType.SEED, False, "Per-image seeds", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Restored / upscaled images", is_array=True),
            PipeOutputSpec("video", IOType.VIDEO, "Restored / upscaled video(s)", is_array=True),
        ]

    # -- process -----------------------------------------------------------

    def process(
        self,
        pipe_input: PipeInput,
        generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        # Video mode: when a video input is wired, restore it frame-for-frame in
        # temporal batches. The image path below stays byte-identical.
        videos = pipe_input.input.get("video")
        if videos:
            return self._process_video(pipe_input, generation_outputs, is_cancelled)

        images = pipe_input.input.get("image")
        if not images:
            raise ValueError("generator/seedvr2 requires an 'image' or 'video' input")
        if not isinstance(images, list):
            images = [images]
        bundle = pipe_input.input["model"]
        seeds = pipe_input.input.get("seed") or []

        scale = float(self.config.get("scale", 2.0))
        target_short_side = int(self.config.get("target_short_side", 0))
        color_mode = self.config.get("color_correction", "wavelet")
        latent_noise_scale = float(self.config.get("latent_noise_scale", 0.0))
        input_noise_scale = float(self.config.get("input_noise_scale", 0.0))
        cfg_seed = int(self.config.get("seed", -1))
        device = self.config.get("device", "cuda")

        progress = ProgressEmitter(generation_outputs, title=self.name)
        generator = build_native_generator(bundle, device=device)

        logger.debug(
            "[GENERATOR SEEDVR2] %d image(s), scale %.2fx%s, color=%s, latent_noise=%.2f, input_noise=%.2f",
            len(images), scale,
            f" (short side {target_short_side}px)" if target_short_side > 0 else "",
            color_mode, latent_noise_scale, input_noise_scale,
        )

        results: List[ImageGenerationOutput] = []
        try:
            for index, source in enumerate(images):
                if is_cancelled and is_cancelled():
                    break

                src_pil = source.convert("RGB") if isinstance(source, Image.Image) \
                    else Image.fromarray(np.asarray(source)).convert("RGB")
                seed = self._seed_for(seeds, index, cfg_seed)

                resized = prepare_input(src_pil, scale, target_short_side, CROP_MULTIPLE)
                resized_np = np.asarray(resized, dtype=np.uint8)
                encode_np = self._apply_input_noise(resized_np, input_noise_scale, seed)

                progress.state(
                    f"Restoring <<RESOLUTION:{resized.width}x{resized.height}>> image {index + 1}/{len(images)}",
                    icon=Icon(name="bolt", effect="pulse"),
                )

                out = generator.upscale(
                    encode_np, bundle.prompt_embedding,
                    seed=seed, latent_noise_scale=latent_noise_scale,
                    tile_size=int(self.config.get("tile_size", 1024)),
                    tile_overlap=int(self.config.get("tile_overlap", 128)),
                )
                out_np = out[0] if out.ndim == 4 else out
                if color_mode != "none":
                    out_np = color_correct(out_np, resized_np, color_mode)
                out_pil = Image.fromarray(out_np)

                generation_outputs(CompareImagesGenerationOutput(
                    index=index,
                    compare=("Original", src_pil),
                    to=(f"Upscaled {out_pil.width}x{out_pil.height}", out_pil),
                ))
                results.append(ImageGenerationOutput(
                    image=out_pil, temporary=True, seed=seed,
                    resolution=(out_pil.width, out_pil.height),
                ))
                progress.step(index + 1, len(images), state="RESTORE",
                              icon=Icon(name="bolt", effect="pulse"))
        except Exception:
            # A failed upscale must not leave the DiT/VAE resident (~7GB) — free
            # GPU VRAM before the exception propagates.
            try:
                generator.release_gpu()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
            raise

        generation_outputs(GalleryGenerationOutput(images=results))
        return PipeOutput(output={"image": [r.image for r in results]})

    # -- video path --------------------------------------------------------

    def _process_video(
        self,
        pipe_input: PipeInput,
        generation_outputs: callable,
        is_cancelled: Optional[callable],
    ) -> PipeOutput:
        """Frame-for-frame video restore, streamed end to end: frames are read and
        resized one at a time, assembled into 4n+1 temporal windows (with optional
        prepend + overlap), one-step upscaled, blended, color-fixed and piped
        straight into ffmpeg. Nothing holds the whole clip, so peak memory tracks
        the temporal batch rather than the clip's duration. The source audio is
        muxed afterwards as a separate step."""
        import os
        import tempfile

        from src.pipelines.pipes.generator.seedvr2 import batching as B
        from src.pipelines.pipes.generator.seedvr2 import frame_source as FS
        from src.pipelines.pipes.generator.seedvr2.encode import AUDIO_MUX_FAILED, AUDIO_PROBE_FAILED

        videos = pipe_input.input["video"]
        if not isinstance(videos, list):
            videos = [videos]
        video_path = videos[0]
        if isinstance(video_path, Path):
            video_path = str(video_path)
        bundle = pipe_input.input["model"]
        seeds = pipe_input.input.get("seed") or []

        scale = float(self.config.get("scale", 2.0))
        target_short_side = int(self.config.get("target_short_side", 0))
        color_mode = self.config.get("color_correction", "wavelet")
        latent_noise_scale = float(self.config.get("latent_noise_scale", 0.0))
        input_noise_scale = float(self.config.get("input_noise_scale", 0.0))
        cfg_seed = int(self.config.get("seed", -1))
        device = self.config.get("device", "cuda")
        requested_batch = int(self.config.get("batch_size", 0))
        temporal_overlap = int(self.config.get("temporal_overlap", 0))
        prepend_frames = int(self.config.get("prepend_frames", 0))
        uniform = bool(self.config.get("uniform_batch_size", True))
        keep_audio = bool(self.config.get("keep_audio", True))
        tile_size = int(self.config.get("tile_size", 1024))
        tile_overlap = int(self.config.get("tile_overlap", 128))
        seed = self._seed_for(seeds, 0, cfg_seed)

        progress = ProgressEmitter(generation_outputs, title=self.name)

        # 1. Open the source as a replayable stream and read its fps + the resized
        #    geometry from frame one. All frames share the first frame's input
        #    size, so that geometry (hence the output size) holds for the clip.
        source = FS.ResizedFrameSource(
            video_path, scale=scale, target_short_side=target_short_side,
            crop_multiple=CROP_MULTIPLE,
        )
        source.probe()
        fps = source.fps
        out_h, out_w = source.frame_shape
        progress.state(
            f"Streaming <<NUMBER:{source.frame_count_hint} frames>> @ <<NUMBER:{fps:.2f} fps>>"
            if source.frame_count_hint > 0 else f"Streaming @ <<NUMBER:{fps:.2f} fps>>",
            icon=Icon(name="film", effect="pulse"),
        )

        generator = build_native_generator(bundle, device=device)

        # ``batch_size == 0`` (the form default) auto-sizes the temporal batch to
        # ~92% of live free VRAM AT THE OUTPUT RESOLUTION -- sized only now that
        # the resized geometry and the loaded DiT's weight size are both known, so
        # a 4K target picks a small batch up front instead of paying a doomed
        # oversized attempt first. Any explicit value is honoured as-is. Either
        # way the OOM ladder below is the safety net: a batch that exceeds real
        # VRAM is halved and re-run, so an over-estimate never fails the job.
        free_gb = free_vram_gb(device)
        spatial_tokens = _spatial_tokens_per_latent_frame(out_h, out_w)
        weights_gb = float(getattr(getattr(generator, "dit", None), "estimated_vram_gb", 0.0) or 0.0)
        if requested_batch <= 0:
            batch_size = B.snap_batch_size(_auto_batch_size(
                free_gb, spatial_tokens_per_latent_frame=spatial_tokens, weights_gb=weights_gb,
            ))
        else:
            batch_size = B.snap_batch_size(requested_batch)
            # Surface what auto WOULD have picked so a stale explicit value (a
            # small session-carried batch_size on a big card) is visible in the
            # logs rather than silently leaving the card idle.
            auto_would = B.snap_batch_size(_auto_batch_size(
                free_gb, spatial_tokens_per_latent_frame=spatial_tokens, weights_gb=weights_gb,
            ))
            logger.debug(
                "[GENERATOR SEEDVR2] explicit batch_size=%d; auto would pick %d%s",
                batch_size, auto_would,
                f" (free VRAM {free_gb:.1f}GB)" if free_gb is not None else " (free VRAM unknown)",
            )

        # 2. Each attempt streams the whole clip into its OWN output file. On a
        #    CUDA OOM the file is discarded, the batch is halved and the clip is
        #    re-read from frame 0 -- a retry never continues, appends to or
        #    truncates a partial output. Any non-OOM failure frees the GPU,
        #    discards the partial file and propagates unchanged.
        final_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        attempt_path = final_path
        attempt = 0
        try:
            while True:
                attempt_path = f"{final_path}.attempt{attempt}.mp4"
                attempt += 1
                try:
                    encode_result, stats = self._run_video_attempt(
                        generator, bundle, source, attempt_path, batch_size,
                        temporal_overlap=temporal_overlap, prepend_frames=prepend_frames,
                        uniform=uniform, input_noise_scale=input_noise_scale, seed=seed,
                        latent_noise_scale=latent_noise_scale, color_mode=color_mode,
                        tile_size=tile_size, tile_overlap=tile_overlap, keep_audio=keep_audio,
                        device=device, progress=progress, is_cancelled=is_cancelled,
                    )
                    break
                except torch.cuda.OutOfMemoryError:
                    if batch_size <= 1:
                        raise
                    _discard_output(attempt_path)
                    self._release_gpu_quietly(generator)
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    smaller = B.snap_batch_size(max(1, batch_size // 2))
                    if smaller >= batch_size:
                        smaller = 1
                    logger.warning(
                        "[GENERATOR SEEDVR2] video OOM at batch %d — retrying at batch %d",
                        batch_size, smaller,
                    )
                    get_profiler().mark(
                        "seedvr2.video.oom_retry", from_batch=batch_size, to_batch=smaller,
                    )
                    batch_size = smaller
        except BaseException:
            _discard_output(attempt_path)
            _discard_output(final_path)
            self._release_gpu_quietly(generator)
            raise

        # 3. Publish only once the attempt is complete: move the finished file
        #    onto the path handed downstream and drop whatever the mux step left
        #    behind (the video-only file when a muxed sibling won). Nothing from
        #    here to the gallery output below is interruptible, so cancellation
        #    is checked one last time before anything is moved into place --
        #    without it, cancellation raised during the encode/mux inside
        #    `_run_video_attempt` (after its own last per-batch check passed)
        #    would still complete and publish. This step owns its own cleanup:
        #    a publish failure (or this last-moment cancellation) must leave
        #    neither the attempt/muxed sibling nor the empty final placeholder
        #    behind, the same as the attempt loop above.
        try:
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled()
            os.replace(encode_result.video_path, final_path)
            _discard_output(attempt_path)
        except BaseException:
            _discard_output(attempt_path)
            _discard_output(final_path)
            self._release_gpu_quietly(generator)
            raise

        if encode_result.audio_outcome == AUDIO_MUX_FAILED:
            logger.warning(
                "[GENERATOR SEEDVR2] audio mux failed — keeping video without audio (%s)",
                encode_result.omitted_reason,
            )
            progress.state(
                "Audio mux failed — output has no audio track",
                icon=Icon(name="alert-triangle"),
            )
        elif encode_result.audio_outcome == AUDIO_PROBE_FAILED:
            logger.warning(
                "[GENERATOR SEEDVR2] could not determine whether the source has audio — "
                "keeping video without audio (%s)",
                encode_result.omitted_reason,
            )
            progress.state(
                "Could not check source audio — output has no audio track",
                icon=Icon(name="alert-triangle"),
            )

        profiler = get_profiler()
        profiler.mark(
            "seedvr2.assemble", streamed=True, frames=stats["frames"],
            batch_size=batch_size, overlap=stats["overlap"],
        )
        if color_mode != "none":
            profiler.mark(
                "seedvr2.color_fix", seconds=round(stats["color_seconds"], 4),
                frames=stats["color_frames"], mode=color_mode, device=str(device),
            )
        profiler.mark(
            "seedvr2.encode_mp4", streamed=True, seconds=stats["encode_seconds"],
            frames=stats["frames"], height=stats["height"], width=stats["width"],
            audio=encode_result.audio_outcome,
        )

        progress.state(
            f"Upscaled <<NUMBER:{stats['frames']} frames>> to "
            f"<<RESOLUTION:{stats['width']}x{stats['height']}>>",
            icon=Icon(name="check-circle"),
        )
        generation_outputs(GalleryGenerationOutput(images=[], videos=[
            VideoGenerationOutput(video_path=final_path, temporary=True, seed=seed,
                                  resolution=(stats["width"], stats["height"]), fps=fps),
        ]))
        return PipeOutput(output={"video": [final_path]})

    def _run_video_attempt(
        self, generator, bundle, source, out_path: str, batch_size: int, *,
        temporal_overlap: int, prepend_frames: int, uniform: bool,
        input_noise_scale: float, seed: int, latent_noise_scale: float,
        color_mode: str, tile_size: int, tile_overlap: int, keep_audio: bool,
        device: str, progress: ProgressEmitter, is_cancelled: Optional[callable],
    ):
        """One streamed pass over the whole clip at ``batch_size``, writing into
        ``out_path``; returns ``(encode_result, stats)``.

        The batch-size-dependent core of the video path, isolated so the OOM
        ladder can re-run it at a smaller batch against a freshly opened reader
        and a fresh output file. Every stage is pulled by ffmpeg's appetite for
        the next frame, so at most one temporal batch, the held-back overlap tail
        and the source frames still owed to color correction are ever resident."""
        from src.pipelines.pipes.generator.seedvr2 import stream as S
        from src.pipelines.pipes.generator.seedvr2.color_fix import color_correct_batch
        from src.pipelines.pipes.generator.seedvr2.encode import (
            encode_frames_stream_to_mp4,
            encode_video_with_audio,
        )

        overlap = S.effective_overlap(batch_size, temporal_overlap)
        total_batches = self._estimate_batches(
            source.frame_count_hint, batch_size, temporal_overlap, prepend_frames,
        )
        logger.debug(
            "[GENERATOR SEEDVR2] video: streaming %s frame(s) in batches of %d "
            "(overlap %d, prepend %d), color=%s, fps=%.2f",
            source.frame_count_hint or "?", batch_size, overlap, prepend_frames,
            color_mode, source.fps,
        )

        stats = {
            "frames": 0, "height": 0, "width": 0, "overlap": overlap,
            "color_seconds": 0.0, "color_frames": 0, "encode_seconds": 0.0,
        }

        def _prepare(padded):
            return [self._apply_input_noise(f, input_noise_scale, seed) for f in padded]

        def _correct(frames, sources):
            t0 = time.perf_counter()
            corrected = color_correct_batch(frames, sources, color_mode, device=device)
            stats["color_seconds"] += time.perf_counter() - t0
            stats["color_frames"] += len(frames)
            return corrected

        def _on_batch(done: int) -> None:
            progress.step(done, max(total_batches, done), state="RESTORE",
                          icon=Icon(name="film", effect="pulse"))

        def _upscale(clips):
            return generator.upscale_video(
                clips, bundle.prompt_embedding, seed=seed,
                latent_noise_scale=latent_noise_scale,
                tile_size=tile_size, tile_overlap=tile_overlap,
            )

        def _counted(frames):
            for frame in frames:
                if stats["frames"] == 0:
                    stats["height"], stats["width"] = int(frame.shape[0]), int(frame.shape[1])
                stats["frames"] += 1
                yield frame

        frames_iter = source.iter_frames()
        out_frames = S.stream_output_frames(
            frames_iter,
            batch_size=batch_size, temporal_overlap=temporal_overlap,
            prepend_frames=prepend_frames, uniform=uniform,
            upscale_clips=_upscale,
            prepare_clip=_prepare if input_noise_scale > 0 else None,
            correct=_correct if color_mode != "none" else None,
            on_batch=_on_batch, is_cancelled=is_cancelled,
        )
        t_enc = time.perf_counter()
        try:
            encode_result = encode_video_with_audio(
                _counted(out_frames), out_path, source.fps,
                source_audio_path=source.video_path, keep_audio=keep_audio,
                encode_video=encode_frames_stream_to_mp4,
            )
        finally:
            stats["encode_seconds"] = round(time.perf_counter() - t_enc, 4)
            out_frames.close()
            frames_iter.close()
        return encode_result, stats

    @staticmethod
    def _estimate_batches(
        frame_hint: int, batch_size: int, temporal_overlap: int, prepend_frames: int
    ) -> int:
        """Batch count for the progress bar, derived from the container's
        frame-count hint. The hint is metadata and can be absent or wrong, so it
        only ever bounds the progress denominator (``0`` = unknown) and never
        feeds geometry or batching. Computed via :func:`_count_windows` in
        constant space -- ``plan_batches`` would materialise every window just
        to report ``len()``, which a multi-million-frame hint makes wasteful for
        a number only ever used as a progress denominator."""
        if frame_hint <= 0:
            return 0
        return _count_windows(
            int(frame_hint) + max(0, int(prepend_frames)), batch_size, temporal_overlap,
        )

    @staticmethod
    def _release_gpu_quietly(generator) -> None:
        try:
            generator.release_gpu()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _seed_for(seeds: List[Any], index: int, cfg_seed: int) -> int:
        if index < len(seeds):
            return int(seeds[index])
        if cfg_seed >= 0:
            return cfg_seed
        return random.randint(0, 2 ** 31 - 1)

    @staticmethod
    def _apply_input_noise(arr: np.ndarray, scale: float, seed: int) -> np.ndarray:
        """Blend a small seeded gaussian into the input pixels (reference's
        ``input_noise_scale``): ``px*(1-b) + (px+n)*b`` with ``n ~ N(0,1)*0.05``
        and ``b = scale*0.5``. No-op at ``scale == 0``."""
        if not scale or scale <= 0:
            return arr
        rng = np.random.default_rng(int(seed))
        px = arr.astype(np.float32) / 255.0
        noise = rng.standard_normal(px.shape).astype(np.float32) * 0.05
        blend = float(min(max(scale, 0.0), 1.0)) * 0.5
        px = px * (1.0 - blend) + (px + noise) * blend
        return (np.clip(px, 0.0, 1.0) * 255.0).round().astype(np.uint8)
