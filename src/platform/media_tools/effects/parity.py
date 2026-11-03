from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

import numpy as np
from PIL import Image

from src.platform.media_tools.effects.runtime import Choice, EffectRuntime
from src.platform.media_tools.effects.spec import Candidate, EffectSpec
from src.platform.media_tools.effects.tool import ANALYSIS_KEY, EffectTool, effect_tool_spec
from src.platform.media_tools.fastpath import RGB_IN
from src.platform.media_tools.params import validate_params
from src.platform.media_tools.types import ToolMedia
from src.platform.util.video_transcode import find_ffmpeg

FFMPEG_TIMEOUT_SECONDS = 120.0
BARS = np.array(
    [[255, 255, 255], [255, 255, 0], [0, 255, 255], [0, 255, 0], [255, 0, 255], [255, 0, 0], [0, 0, 255], [0, 0, 0]],
    dtype=np.float64,
)
SKIN = np.array([224.0, 172.0, 138.0])


class ParityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParityCase:
    index: int
    params: dict
    frames: tuple[int, ...]
    analysis: Optional[dict] = None


def parity_media(effect: EffectSpec) -> ToolMedia:
    media = effect.parity["media"]
    return ToolMedia(
        path=Path("parity.mp4"), kind="video", width=int(media["width"]), height=int(media["height"]),
        fps=float(media["fps"]), duration=float(media["duration"]),
    )


def parity_metric(effect: EffectSpec) -> str:
    return effect.parity.get("metric", "pixels")


def parity_cases(effect: EffectSpec) -> list[ParityCase]:
    analysis = effect.parity.get("analysis")
    return [
        ParityCase(
            index=index,
            params=dict(case.get("params") or {}),
            frames=tuple(case["frames"]),
            analysis=dict(analysis) if analysis is not None else None,
        )
        for index, case in enumerate(effect.parity["cases"])
    ]


def golden_name(case: ParityCase, frame: int) -> str:
    return f"c{case.index}_f{frame}.png"


def fixture_frame(width: int, height: int) -> np.ndarray:
    y, x = np.mgrid[0:height, 0:width].astype(np.float64)
    u = (x + 0.5) / width
    v = (y + 0.5) / height
    band = np.minimum((v * 4).astype(int), 3)
    out = np.zeros((height, width, 3))
    ramp = np.stack([u, u * (0.75 + 0.25 * v * 4), u], axis=-1) * 255
    bars = BARS[np.minimum((u * 8).astype(int), 7)]
    skin = SKIN[None, None, :] * (0.35 + 0.65 * u)[:, :, None]
    checker = np.where((x.astype(int) + y.astype(int)) % 2 == 0, 40.0, 215.0)
    stripes = np.where((x.astype(int) // 3) % 2 == 0, 30.0, 220.0)
    detail = np.stack([checker, stripes, (checker + stripes) / 2], axis=-1)
    left = (u < 0.5)[:, :, None]
    radius = np.hypot(u - 0.5, (v - 0.875) * height / width)
    rings = 127.5 + 127.5 * np.stack(
        [np.sin(radius * 90), np.sin(radius * 60 + 1), np.cos(radius * 40)], axis=-1
    )
    for index, layer in enumerate((ramp, bars, np.where(left, skin, detail), rings)):
        out = np.where((band == index)[:, :, None], layer, out)
    return np.clip(np.floor(out + 0.5), 0, 255).astype(np.uint8)


def case_params(effect: EffectSpec, case: ParityCase) -> dict:
    params = validate_params(effect_tool_spec(effect), dict(case.params))
    if effect.analysis is not None:
        if case.analysis is None:
            raise ParityError(f"{effect.tool_id}: parity.analysis is required for an effect with an analysis")
        params[ANALYSIS_KEY] = dict(case.analysis)
    return params


def _ffmpeg_frames(
    vf: list[str], frame: np.ndarray, count: int, fps: float, binary: str
) -> np.ndarray:
    height, width = frame.shape[:2]
    filters = ",".join([*RGB_IN, *vf, "format=rgb24"])
    args = [
        binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1", "-filter_threads", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", f"{fps:g}", "-i", "pipe:0",
        "-vf", filters, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    data = np.ascontiguousarray(np.broadcast_to(frame, (count, height, width, 3))).tobytes()
    completed = subprocess.run(args, input=data, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise ParityError(completed.stderr.decode("utf-8", "replace").strip() or "ffmpeg failed")
    out = np.frombuffer(completed.stdout, dtype=np.uint8)
    size = width * height * 3
    if out.size != count * size:
        raise ParityError(f"ffmpeg returned {out.size // size} frames, expected {count}")
    return out.reshape(count, height, width, 3)


def render_case(
    effect: EffectSpec, candidate: Candidate, case: ParityCase, frame: Optional[np.ndarray] = None,
    ffmpeg: Optional[str] = None,
) -> dict[int, np.ndarray]:
    media = parity_media(effect)
    source = fixture_frame(media.width, media.height) if frame is None else frame
    tool = EffectTool(effect, EffectRuntime())
    binding = tool.bind(media, case_params(effect, case))
    if candidate.kind == "ffmpeg":
        binary = ffmpeg or find_ffmpeg()
        if not binary:
            raise ParityError("ffmpeg is not installed")
        compiled = tool.compile(binding, Choice(candidate))
        frames = _ffmpeg_frames(compiled.vf, source, max(case.frames) + 1, media.fps, binary)
        return {index: frames[index] for index in case.frames}
    stage = tool.stage(binding, Choice(candidate, "cpu" if candidate.kind == "torch" else None))
    batch = np.ascontiguousarray(np.broadcast_to(source, (len(case.frames), *source.shape)))
    out = stage.apply(batch, case.frames, media.fps, media.duration)
    return {index: out[slot] for slot, index in enumerate(case.frames)}


def _stack(frames: Mapping[int, np.ndarray], order: tuple[int, ...]) -> np.ndarray:
    return np.stack([np.asarray(frames[index], dtype=np.float64) for index in order])


def score_case(
    metric: str, expected: Mapping[int, np.ndarray], actual: Mapping[int, np.ndarray], source: np.ndarray,
) -> dict:
    order = tuple(sorted(expected))
    if set(actual) != set(expected):
        raise ParityError("the render and the golden cover different frames")
    want, got = _stack(expected, order), _stack(actual, order)
    if want.shape != got.shape:
        raise ParityError(f"frame shape {got.shape[1:]} does not match the golden {want.shape[1:]}")
    if metric == "stats":
        base = np.asarray(source, dtype=np.float64)[None]
        want_delta, got_delta = (want - base).reshape(-1, 3), (got - base).reshape(-1, 3)
        return {
            "mean_abs": float(np.max(np.abs(want_delta.mean(axis=0) - got_delta.mean(axis=0)))),
            "std_abs": float(np.max(np.abs(want_delta.std(axis=0) - got_delta.std(axis=0)))),
        }
    diff = np.abs(want - got)
    return {
        "max_abs": float(diff.max()),
        "mean_abs": float(diff.mean()),
        "p99_abs": float(np.percentile(diff, 99)),
    }


def over_tolerance(score: Mapping[str, float], tolerance: Mapping[str, float]) -> list[str]:
    return [
        f"{key} {score[key]:.4g} > {float(limit):g}"
        for key, limit in tolerance.items()
        if key in score and score[key] > float(limit)
    ]


def write_golden(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8), "RGB").save(path, optimize=True)


def read_golden(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)
