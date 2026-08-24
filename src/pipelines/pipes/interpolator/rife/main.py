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
from src.pipelines.pipes.interpolator.rife.encode import (
    StreamingMp4Writer,
    mux_audio_from_source,
)
from vendor.rife import interpolate as rife_interpolate
from vendor.rife import load_ifnet

_MODEL_CACHE: Dict[Tuple[str, float], "torch.nn.Module"] = {}
_PROGRESS_EVERY = 8


def _load_model(model_path: str, device: str) -> "torch.nn.Module":
    key = (str(model_path), Path(model_path).stat().st_mtime)
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = load_ifnet(model_path, device="cpu")
        _MODEL_CACHE[key] = model
    model = model.to(device)
    # fp16 on CUDA (upstream inference_video.py runs the flownet + inputs half),
    # fp32 on CPU (half convs are unsupported / slow there).
    return model.half() if device == "cuda" else model.float()


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
        model = _load_model(model_path, device)
        model_dtype = next(model.parameters()).dtype

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"interpolator/rife: could not open video: {video_path}")
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

            out_tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
            writer: Optional[StreamingMp4Writer] = None
            timesteps = [f / factor for f in range(1, factor)]
            written = 0

            prev_rgb: Optional[np.ndarray] = None
            prev_tensor: Optional[torch.Tensor] = None
            read_idx = 0
            while True:
                if is_cancelled and is_cancelled():
                    break
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

                for t in timesteps:
                    mid = self._run(model, prev_tensor, cur_tensor, t, flow_scale)
                    writer.write(mid)
                    written += 1
                writer.write(rgb)
                written += 1

                prev_rgb = rgb
                prev_tensor = cur_tensor

                if out_total and read_idx % _PROGRESS_EVERY == 0:
                    generation_outputs(ProgressGenerationOutput(
                        state=f"Interpolated <<NUMBER:{written}>> / <<NUMBER:{out_total}>> frames",
                        icon=Icon(name="film", effect="pulse"),
                        progress=Progress(current=written, max=out_total),
                    ))
        finally:
            cap.release()

        if writer is None:
            raise ValueError(f"interpolator/rife: no frames decoded from {video_path}")

        # The last decoded frame owns `factor` slots at the output rate but has no
        # successor to interpolate toward, so it is held for the remaining ones.
        for _ in range(factor - 1):
            writer.write(prev_rgb)
            written += 1
        writer.close()

        final_path = out_tmp
        if keep_audio:
            muxed = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
            if mux_audio_from_source(out_tmp, video_path, muxed):
                final_path = muxed

        h, w = prev_rgb.shape[0], prev_rgb.shape[1]
        generation_outputs(ProgressGenerationOutput(
            state=f"Wrote <<NUMBER:{written} frames>> at <<RESOLUTION:{w}x{h}>>",
            icon=Icon(name="check-circle"),
        ))
        generation_outputs(GalleryGenerationOutput(images=[], videos=[
            VideoGenerationOutput(video_path=final_path, temporary=True,
                                  resolution=(w, h), fps=out_fps),
        ]))
        return PipeOutput(output={"video": [final_path]})

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
    def _run(model, img0: torch.Tensor, img1: torch.Tensor, timestep: float,
             flow_scale: float) -> np.ndarray:
        with torch.no_grad():
            mid = rife_interpolate(model, img0, img1, timestep, flow_scale)
        mid = mid.float().clamp_(0.0, 1.0).mul_(255.0).round_().to(torch.uint8)[0]
        return mid.permute(1, 2, 0).contiguous().cpu().numpy()
