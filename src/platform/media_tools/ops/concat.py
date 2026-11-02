from __future__ import annotations

from pathlib import Path
from typing import Sequence

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.fastpath import encode_to
from src.platform.media_tools.types import (
    InvalidToolParams,
    ToolContext,
    ToolMedia,
    ToolSpec,
    UnsupportedMedia,
)

SAMPLE_RATE = 48000
DEFAULT_FPS = 24.0
EDGE_NUDGE_SECONDS = 0.001

ClipTrims = Sequence[tuple[float, float]]

SPEC = dict(
    id="concat",
    label="Concat",
    icon="concat",
    accepts=("video",),
    produces="video",
    cardinality="many_to_one",
    selection={"min": 2, "max": 8},
    params=[
        {
            "name": "overlap",
            "type": "select",
            "label": "Overlap",
            "default": "cut",
            "options": [
                {"label": "Cut", "value": "cut"},
                {"label": "Crossfade", "value": "crossfade"},
            ],
        },
        {
            "name": "overlap_frames",
            "type": "integer",
            "label": "Overlap frames",
            "default": 0,
            "min": 0,
            "max": 48,
            "when": {"field": "overlap", "equals": "crossfade"},
        },
        {
            "name": "fit",
            "type": "select",
            "label": "Fit",
            "default": "pad",
            "options": [
                {"label": "Pad", "value": "pad"},
                {"label": "Cover", "value": "cover"},
            ],
        },
        {"name": "keep_audio", "type": "checkbox", "label": "Keep audio", "default": True},
    ],
)


def _even(value: int) -> int:
    return value - value % 2


def fit_filter(width: int, height: int, fit: str) -> str:
    if fit == "cover":
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )


def trim_filter(name: str, duration: float, start: float, end: float) -> str:
    if start <= 0 and end <= 0:
        return ""
    parts = []
    if start > 0:
        parts.append(f"start={max(0.0, start - EDGE_NUDGE_SECONDS):.6f}")
    if end > 0:
        parts.append(f"end={duration - end - EDGE_NUDGE_SECONDS:.6f}")
    reset = "asetpts" if name == "atrim" else "setpts"
    return f"{name}={':'.join(parts)},{reset}=PTS-STARTPTS,"


def kept_lengths(clips: Sequence[ToolMedia], trims: ClipTrims) -> list[float]:
    lengths = [float(clip.duration) - start - end for clip, (start, end) in zip(clips, trims)]
    if any(start < 0 or end < 0 for start, end in trims) or any(length <= 0 for length in lengths):
        raise InvalidToolParams("trims", "A clip has nothing left after trimming")
    return lengths


def check_layout(lengths: Sequence[float], crossfades: Sequence[float]) -> None:
    joined = lengths[0]
    for fade, length in zip(crossfades, lengths[1:]):
        if fade < 0 or fade >= min(joined, length):
            raise InvalidToolParams(
                "overlap_frames",
                f"A {fade:.3f}s crossfade is longer than the clips it joins",
            )
        joined += length - fade


def blend_chain(lengths: Sequence[float], crossfades: Sequence[float], with_audio: bool) -> list[str]:
    filters: list[str] = []
    video, sound = "v0", "a0"
    elapsed = lengths[0]
    last = len(lengths) - 1
    for index in range(1, len(lengths)):
        fade = crossfades[index - 1]
        video_out = "v" if index == last else f"vj{index}"
        sound_out = "a" if index == last else f"aj{index}"
        if fade > 0:
            filters.append(
                f"[{video}][v{index}]xfade=transition=fade:duration={fade:.6f}:offset={elapsed - fade:.6f}[{video_out}]"
            )
            if with_audio:
                filters.append(f"[{sound}][a{index}]acrossfade=d={fade:.6f}[{sound_out}]")
        else:
            filters.append(f"[{video}][v{index}]concat=n=2:v=1:a=0[{video_out}]")
            if with_audio:
                filters.append(f"[{sound}][a{index}]concat=n=2:v=0:a=1[{sound_out}]")
        elapsed += lengths[index] - fade
        video, sound = video_out, sound_out
    return filters


def concat_args(
    clips: Sequence[ToolMedia],
    out_path: Path,
    *,
    crossfades: Sequence[float],
    audio: bool,
    fit: str,
    trims: ClipTrims | None = None,
    canvas: int = 0,
) -> list[str]:
    reference = clips[canvas]
    fps = reference.fps if reference.fps and reference.fps > 0 else DEFAULT_FPS
    width, height = _even(int(reference.width)), _even(int(reference.height))
    with_audio = audio and any(clip.has_audio for clip in clips)
    blended = any(fade > 0 for fade in crossfades)
    windows = list(trims) if trims is not None else [(0.0, 0.0)] * len(clips)
    lengths = kept_lengths(clips, windows)

    args: list[str] = []
    for clip in clips:
        args += ["-i", str(clip.path)]
    silence_inputs: dict[int, int] = {}
    for index, clip in enumerate(clips):
        if with_audio and not clip.has_audio:
            silence_inputs[index] = len(clips) + len(silence_inputs)
            args += [
                "-f", "lavfi", "-t", f"{float(clip.duration):.3f}",
                "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo",
            ]

    filters: list[str] = []
    joined = ""
    timebase = ",settb=AVTB" if blended else ""
    for index, clip in enumerate(clips):
        start, end = windows[index]
        filters.append(
            f"[{index}:v]{trim_filter('trim', float(clip.duration), start, end)}"
            f"fps={fps:g},{fit_filter(width, height, fit)},setsar=1,format=yuv420p{timebase}[v{index}]"
        )
        joined += f"[v{index}]"
        if with_audio:
            source = f"{silence_inputs[index]}:a" if index in silence_inputs else f"{index}:a"
            filters.append(
                f"[{source}]{trim_filter('atrim', float(clip.duration), start, end)}"
                f"aresample={SAMPLE_RATE},aformat=channel_layouts=stereo[a{index}]"
            )
            joined += f"[a{index}]"
    if blended:
        filters += blend_chain(lengths, crossfades, with_audio)
    else:
        filters.append(
            f"{joined}concat=n={len(clips)}:v=1:a={1 if with_audio else 0}[v]" + ("[a]" if with_audio else "")
        )
    args += ["-filter_complex", ";".join(filters), "-map", "[v]"]
    if with_audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(out_path),
    ]
    return args


def join_clips(
    clips: Sequence[ToolMedia],
    ctx: ToolContext,
    *,
    trims: ClipTrims | None = None,
    crossfades: Sequence[float] | None = None,
    canvas: int = 0,
    fit: str = "pad",
    audio: bool = True,
) -> ToolMedia:
    clips = list(clips)
    if not clips:
        raise InvalidToolParams("inputs", "There are no clips to join")
    for clip in clips:
        if not clip.width or not clip.height or not clip.duration or clip.duration <= 0:
            raise UnsupportedMedia(f"The clip {clip.path.name} cannot be joined: its size or length is unknown")
    if not 0 <= canvas < len(clips):
        raise InvalidToolParams("canvas", f"The canvas clip {canvas} is not one of the {len(clips)} clips")
    windows = list(trims) if trims is not None else [(0.0, 0.0)] * len(clips)
    fades = list(crossfades) if crossfades is not None else [0.0] * (len(clips) - 1)
    if len(windows) != len(clips):
        raise InvalidToolParams("trims", f"Expected {len(clips)} trim(s), got {len(windows)}")
    if len(fades) != len(clips) - 1:
        raise InvalidToolParams("crossfades", f"Expected {len(clips) - 1} crossfade(s), got {len(fades)}")
    lengths = kept_lengths(clips, windows)
    check_layout(lengths, fades)

    out_path = ctx.new_path(".mp4", stem="joined")
    return encode_to(
        ctx,
        concat_args(clips, out_path, crossfades=fades, audio=audio, fit=fit, trims=windows, canvas=canvas),
        out_path,
        "video",
        sum(lengths) - sum(fades),
    )


class Concat(MediaTool):
    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        clips = list(inputs)
        if not clips:
            raise InvalidToolParams("inputs", "There are no clips to join")
        first = clips[0]
        rate = first.fps if first.fps and first.fps > 0 else DEFAULT_FPS
        overlap_frames = int(params.get("overlap_frames") or 0)
        fade = overlap_frames / rate if params.get("overlap") == "crossfade" and overlap_frames > 0 else 0.0
        joined = join_clips(
            clips,
            ctx,
            crossfades=[fade] * (len(clips) - 1),
            fit=str(params.get("fit") or "pad"),
            audio=bool(params.get("keep_audio", True)),
        )
        return [joined]


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=Concat())
