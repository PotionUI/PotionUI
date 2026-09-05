"""RIFE frame interpolator (native).

Reads a video frame-by-frame (streaming, never the whole decoded clip in
memory), synthesises ``factor - 1`` intermediate frames between each source
pair with the vendored RIFE 4.x IFNet, and pipes the result to ffmpeg
incrementally at ``source_fps * factor`` (so duration is preserved). The source
audio is muxed back unchanged when ``keep_audio`` is set.

Frame-count: ``N`` source frames -> ``N * factor`` output frames (originals
preserved, ``factor - 1`` interpolated between each pair, and the final frame
held for the ``factor - 1`` slots it owns -- it has no successor to interpolate
toward). Holding that tail is what keeps ``N * factor / (fps * factor)`` equal
to the source's ``N / fps``; emitting ``(N - 1) * factor + 1`` frames instead
lands ``(factor - 1) / (fps * factor)`` short and drags the audio with it.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch

from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import (
    GalleryGenerationOutput,
    Icon,
    Progress,
    ProgressGenerationOutput,
    VideoGenerationOutput,
)
from src.platform.observability.logger import logger
from src.platform.runtime.native.errors import SamplingCancelled
from src.pipelines.pipes.interpolator.rife.encode import (
    StreamingMp4Writer,
    mux_audio_from_source,
)
from vendor.rife import load_ifnet
from vendor.rife.inference import PreparedFrame, interpolate_prepared, prepare_frame

_PROGRESS_EVERY = 8

#: Fallback cache for isolated pipe use (no ``MODELS`` service injected, e.g.
#: tests): at most ONE entry, keyed the same way as the ``MODELS`` path below.
#: Never a second unbounded global -- a real pipeline always wires ``MODELS``.
_FALLBACK_MODEL: Dict[str, Any] = {}


def _model_fingerprint(model_path: str) -> str:
    """Fingerprint on the checkpoint's on-disk revision (mtime + size), not
    just its path, so a checkpoint overwritten in place (re-downloaded,
    swapped) replaces the cached module instead of being served stale."""
    stat = Path(model_path).stat()
    return f"{stat.st_mtime}|{stat.st_size}"


def _acquire_base_model(models: Optional[Any], model_path: str) -> "torch.nn.Module":
    """Load-or-reuse the CPU-resident RIFE checkpoint.

    Goes through the injected ``MODELS`` lifecycle service when the pipeline
    wires one in -- keyed on the resolved path, fingerprinted on the
    checkpoint's revision (see :func:`_model_fingerprint`) so a same-path
    revision replaces the obsolete cached module via the lifecycle's own
    fingerprint-bust path, and bounded for every other distinct selection by
    the lifecycle's own eviction policy, same as any other native component.

    Falls back to :data:`_FALLBACK_MODEL` -- a single-entry cache, not a
    per-frame reload -- when no service is injected."""
    def load() -> "torch.nn.Module":
        return load_ifnet(model_path, device="cpu")

    key = f"native/rife/{model_path}"
    fingerprint = _model_fingerprint(model_path)
    if models is not None:
        return models.acquire(key=key, fingerprint=fingerprint, loader=load)

    if _FALLBACK_MODEL.get("key") == key and _FALLBACK_MODEL.get("fingerprint") == fingerprint:
        return _FALLBACK_MODEL["module"]
    module = load()
    _FALLBACK_MODEL.clear()
    _FALLBACK_MODEL.update(key=key, fingerprint=fingerprint, module=module)
    return module


def _place_model(module: "torch.nn.Module", device: str) -> "torch.nn.Module":
    """Move ``module`` onto ``device`` and to its compute dtype -- fp16 on CUDA
    (upstream inference_video.py runs the flownet + inputs half), fp32 on CPU
    (half convs are unsupported / slow there). Mutates and returns the SAME
    object (``nn.Module.to()``/``.half()``/``.float()`` do this by contract).

    A raise from here (mid-``.to()``, mid-``.half()``/``.float()``) can leave
    ``module`` with its parameters split across devices or dtypes -- and
    ``module`` is the object CACHED under this checkpoint's key, so a caller
    must treat that as an unsafe cache entry (see :func:`_invalidate_model`),
    never hand it back to the next acquire as if placement had succeeded."""
    module = module.to(device)
    return module.half() if device == "cuda" else module.float()


def _invalidate_model(models: Optional[Any], model_path: str, module: "torch.nn.Module") -> None:
    """Drop the cache entry for ``module`` after a failed device/dtype
    transition (see :func:`_place_model`'s docstring for why the entry can no
    longer be trusted). Best-effort and silent -- this runs from a cleanup
    path and must never replace the caller's original exception with one of
    its own, nor mask it by raising here instead."""
    key = f"native/rife/{model_path}"
    try:
        if models is not None:
            evict = getattr(models, "evict_dead_weight", None)
            if callable(evict):
                evict(key)
        elif _FALLBACK_MODEL.get("module") is module:
            _FALLBACK_MODEL.clear()
    except Exception:
        pass


def _load_model(model_path: str, device: str, models: Optional[Any] = None) -> "torch.nn.Module":
    """Acquire the checkpoint (see :func:`_acquire_base_model`) -- the handle
    is retained across the placement step below so a failed transition can
    still be invalidated rather than silently reused by the next acquire.

    :func:`_idle_model` undoes a SUCCESSFUL placement once the clip is done,
    so the entry goes back to idle CPU/fp32 rather than pinning VRAM between
    clips; a FAILED placement is invalidated instead (see
    :func:`_invalidate_model`) and the original exception propagates
    unmasked."""
    module = _acquire_base_model(models, model_path)
    try:
        return _place_model(module, device)
    except Exception:
        _invalidate_model(models, model_path, module)
        raise


def _idle_model(model: "torch.nn.Module") -> None:
    """Return ``model`` to its idle placement -- CPU, fp32, matching load
    time -- on success, failure or cancellation alike, so a cached RIFE
    checkpoint never sits GPU-resident between generations."""
    model.to("cpu")
    model.float()


class RifeInterpolatorPipe(BasePipe):
    name = "interpolator/rife"
    description = "RIFE 4.x frame interpolation (2x/4x) with audio passthrough"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "model": None,
            "factor": 2,
            "flow_scale": 1.0,
            "keep_audio": True,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("model", dict, None,
                           "RIFE checkpoint ({file_path, name}) from the vfi model field",
                           required=True),
            PipeConfigSpec("factor", int, 2,
                           "Interpolation factor: 2x inserts t=0.5; 4x inserts t=0.25/0.5/0.75",
                           required=False, choices=[2, 4]),
            PipeConfigSpec("flow_scale", float, 1.0,
                           "Flow-computation scale (0.5 for high-res >2K inputs)",
                           required=False, choices=[1.0, 0.5]),
            PipeConfigSpec("keep_audio", bool, True,
                           "Mux the source video's audio track into the output", required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("video", IOType.VIDEO, True, "Source video to interpolate", is_array=True),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service for RIFE checkpoint reuse across clips",
                          is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO, "Frame-interpolated video", is_array=True),
        ]

    @staticmethod
    def output_frame_count(n_source: int, factor: int) -> int:
        return n_source * factor if n_source > 0 else 0

    def _resolve_model_path(self) -> str:
        model_cfg = self.config.get("model")
        if isinstance(model_cfg, dict):
            path = model_cfg.get("file_path") or model_cfg.get("name")
        else:
            path = model_cfg
        if not path:
            raise ValueError("interpolator/rife requires a 'model' checkpoint in config")
        return str(path)

    def process(
        self,
        pipe_input: PipeInput,
        generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        import cv2

        is_cancelled = is_cancelled or (lambda: False)

        videos = pipe_input.input.get("video")
        if not videos:
            raise ValueError("interpolator/rife requires a 'video' input")
        if isinstance(videos, list):
            video_path = videos[0]
        else:
            video_path = videos
        video_path = str(video_path)

        factor = int(self.config.get("factor", 2))
        if factor not in (2, 4):
            raise ValueError(f"interpolator/rife: factor must be 2 or 4, got {factor}")
        flow_scale = float(self.config.get("flow_scale", 1.0))
        keep_audio = bool(self.config.get("keep_audio", True))

        model_path = self._resolve_model_path()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        models = pipe_input.input.get("MODELS", None)
        model = _load_model(model_path, device, models)
        model_dtype = next(model.parameters()).dtype

        # The model stays resident on `device` for the whole clip once
        # acquired; `_idle_model` returns it to CPU/fp32 here regardless of
        # how this method exits -- success, a cap-open failure before the
        # frame loop even starts, a mid-clip failure, or cancellation --  so
        # a cached checkpoint never sits GPU-resident between generations.
        try:
            cap = cv2.VideoCapture(video_path)
            # Owned from the moment it is acquired, not from the moment it
            # first proves useful: a `finally` starting here releases it even
            # when `isOpened()` comes back False or the temp-file create just
            # below raises, both of which used to bypass release entirely by
            # sitting outside the try that used to own it.
            try:
                if not cap.isOpened():
                    raise ValueError(f"interpolator/rife: could not open video: {video_path}")

                # Every path this pipe creates from here on is owned by it until
                # ownership explicitly transfers to the gallery output at the very
                # end (`owned.clear()`). Any exception -- cancellation included --
                # falls through to the `except` below, which releases a still-running
                # writer and deletes whatever `owned` still lists, so a cancelled or
                # failed clip never leaves an attempt file (or a live ffmpeg child)
                # behind.
                out_tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
                owned: List[str] = [out_tmp]
                writer: Optional[StreamingMp4Writer] = None

                try:
                    try:
                        src_fps = cap.get(cv2.CAP_PROP_FPS)
                        src_fps = float(src_fps) if src_fps and src_fps > 0 else 24.0
                        src_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                        out_fps = src_fps * factor
                        out_total = self.output_frame_count(src_count, factor) if src_count > 0 else 0

                        generation_outputs(ProgressGenerationOutput(
                            state=(f"Interpolating <<NUMBER:{src_count} frames>> at <<NUMBER:{factor}x>> "
                                   f"-> <<NUMBER:{out_fps:.2f} fps>>"),
                            icon=Icon(name="film", effect="pulse"),
                        ))

                        timesteps = [f / factor for f in range(1, factor)]
                        written = 0

                        prev_rgb: Optional[np.ndarray] = None
                        prev_tensor: Optional[torch.Tensor] = None
                        prev_prepared: Optional[PreparedFrame] = None
                        f0 = f1 = None
                        read_idx = 0
                        while True:
                            if is_cancelled():
                                raise SamplingCancelled()
                            ret, frame_bgr = cap.read()
                            if not ret or frame_bgr is None:
                                break
                            rgb = self._to_even_rgb(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
                            read_idx += 1

                            if writer is None:
                                h, w = rgb.shape[0], rgb.shape[1]
                                writer = StreamingMp4Writer(out_tmp, w, h, out_fps)

                            cur_tensor = self._to_tensor(rgb, device, model_dtype)
                            if prev_rgb is None:
                                writer.write(prev_rgb := rgb)
                                prev_tensor = cur_tensor
                                written += 1
                                continue

                            # Drop the previous pair's frames BEFORE the next right frame is
                            # allocated: `prev_prepared` already holds the only one still
                            # needed (it is this pair's left frame), and releasing here is
                            # what bounds the clip at two live prepared frames instead of
                            # three. Assigning through the same locals would free the old
                            # left frame only after the new right one exists.
                            f0 = f1 = None
                            f0, f1 = self._prepare_pair(model, prev_tensor, cur_tensor,
                                                        flow_scale, prev_prepared)
                            for t in timesteps:
                                mid = self._run(model, f0, f1, t, flow_scale)
                                writer.write(mid)
                                written += 1
                            writer.write(rgb)
                            written += 1

                            prev_rgb = rgb
                            prev_tensor = cur_tensor
                            prev_prepared = f1

                            if out_total and read_idx % _PROGRESS_EVERY == 0:
                                generation_outputs(ProgressGenerationOutput(
                                    state=f"Interpolated <<NUMBER:{written}>> / <<NUMBER:{out_total}>> frames",
                                    icon=Icon(name="film", effect="pulse"),
                                    progress=Progress(current=written, max=out_total),
                                ))
                    finally:
                        # Padded frames and encoder features are the largest tensors the loop
                        # holds; drop them on the normal exit, on cancellation and on an
                        # exception alike. `prev_rgb` outlives this block -- the tail hold and
                        # the output resolution read it.
                        prev_tensor = prev_prepared = f0 = f1 = None

                    if writer is None:
                        raise ValueError(f"interpolator/rife: no frames decoded from {video_path}")

                    # Cooperative boundary before paying for the tail hold, the encode
                    # close and the audio mux -- none of which are themselves
                    # interruptible.
                    if is_cancelled():
                        raise SamplingCancelled()

                    # The last decoded frame owns `factor` slots at the output rate but has no
                    # successor to interpolate toward, so it is held for the remaining ones.
                    for _ in range(factor - 1):
                        writer.write(prev_rgb)
                        written += 1
                    writer.close()

                    final_path = out_tmp
                    if keep_audio:
                        muxed = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
                        owned.append(muxed)
                        if mux_audio_from_source(out_tmp, video_path, muxed):
                            final_path = muxed
                            self._discard(out_tmp)
                            owned.remove(out_tmp)
                        else:
                            self._discard(muxed)
                            owned.remove(muxed)

                    # Cooperative boundary again, right before publication -- a
                    # cancellation observed during the encode/mux above must still
                    # keep the finished clip out of the gallery.
                    if is_cancelled():
                        raise SamplingCancelled()

                    h, w = prev_rgb.shape[0], prev_rgb.shape[1]
                    generation_outputs(ProgressGenerationOutput(
                        state=f"Wrote <<NUMBER:{written} frames>> at <<RESOLUTION:{w}x{h}>>",
                        icon=Icon(name="check-circle"),
                    ))
                    generation_outputs(GalleryGenerationOutput(images=[], videos=[
                        VideoGenerationOutput(video_path=final_path, temporary=True,
                                              resolution=(w, h), fps=out_fps),
                    ]))
                    owned.clear()  # ownership of `final_path` transfers to the gallery output
                    return PipeOutput(output={"video": [final_path]})
                except Exception:
                    self._cleanup_on_failure(writer, owned)
                    raise
            finally:
                cap.release()
        finally:
            _idle_model(model)

    def _cleanup_on_failure(self, writer: Optional[StreamingMp4Writer], owned: List[str]) -> None:
        """Best-effort release of everything this attempt owns, run once from
        the single ``except`` around the whole clip.

        Each release step runs in its OWN try/except so one failing (e.g.
        ``writer.abort()`` itself raising) can never skip the other, and
        neither can replace the exception this is handling -- the caller's
        bare ``raise`` re-raises exactly what was already being handled,
        never one of these cleanup failures."""
        if writer is not None:
            try:
                writer.abort()
            except Exception:
                logger.warning(
                    "[INTERPOLATOR RIFE] writer.abort() failed during cleanup", exc_info=True,
                )
        try:
            self._discard(*owned)
        except Exception:
            logger.warning(
                "[INTERPOLATOR RIFE] cleanup of owned temp files failed", exc_info=True,
            )

    @staticmethod
    def _discard(*paths: str) -> None:
        """Best-effort delete of attempt files this pipe still owns -- called
        once, from the single ``except`` around the whole clip, so it runs the
        same way whether the failure was cancellation, a model error, or a
        write/encode/mux failure."""
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _to_even_rgb(rgb: np.ndarray) -> np.ndarray:
        h, w = rgb.shape[0], rgb.shape[1]
        pad_h, pad_w = h % 2, w % 2
        if pad_h or pad_w:
            rgb = np.pad(rgb, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
        return np.ascontiguousarray(rgb, dtype=np.uint8)

    @staticmethod
    def _to_tensor(rgb: np.ndarray, device: str, dtype: "torch.dtype") -> torch.Tensor:
        t = torch.from_numpy(rgb).float().div_(255.0)
        return t.permute(2, 0, 1).unsqueeze(0).to(device=device, dtype=dtype)

    @staticmethod
    def _prepare_pair(model, img0: torch.Tensor, img1: torch.Tensor, flow_scale: float,
                      carried: Optional[PreparedFrame] = None,
                      ) -> Tuple[PreparedFrame, PreparedFrame]:
        """Pad and feature-encode the pair's two frames once so every timestep in
        the pair shares that work, reusing ``carried`` as the left frame.

        ``PreparedFrame.matches`` only establishes compatibility (same model,
        geometry, device, dtype, flow scale) -- it cannot tell whether ``carried``
        holds ``img0``'s pixels, and two frames of one clip always match each
        other. The same-frame invariant comes from the caller: ``process`` streams
        pairs, so the tensor it passes as ``carried`` is the one it prepared as the
        previous pair's ``img1``, which is this pair's ``img0``. Pass ``None``
        rather than a prepared frame from anywhere else.

        Model identity is checked by object, not by weights. That is sound here
        because `_load_model` hands out an `eval()` model whose parameters nothing
        mutates for the life of the clip; a caller that reloads or requantises
        mid-clip would have to drop its prepared frames itself."""
        if carried is not None and carried.matches(model, img0, flow_scale):
            f0 = carried
        else:
            f0 = prepare_frame(model, img0, flow_scale)
        return f0, prepare_frame(model, img1, flow_scale)

    @staticmethod
    def _run(model, f0: PreparedFrame, f1: PreparedFrame, timestep: float,
             flow_scale: float) -> np.ndarray:
        mid = interpolate_prepared(model, f0, f1, timestep, flow_scale)
        mid = mid.float().clamp_(0.0, 1.0).mul_(255.0).round_().to(torch.uint8)[0]
        return mid.permute(1, 2, 0).contiguous().cpu().numpy()
