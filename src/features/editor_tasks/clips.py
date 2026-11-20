import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from src.platform.http.errors import DomainError
from src.platform.media_tools.fastpath import audio_codec_args, video_encoder_args
from src.platform.media_tools.ffmpeg import FfmpegRunner
from src.platform.media_tools.probe import probe
from src.platform.media_tools.types import MediaToolError

SUFFIX = ".mp4"
SAMPLE_RATE = 48000
EDGE_TOLERANCE_SECONDS = 0.05
CRF: Optional[int] = None

UNREADABLE = "The clip could not be read"


@dataclass(frozen=True)
class ClipFacts:
    width: int
    height: int
    fps: float
    frames: int
    duration: float
    has_audio: bool


@dataclass(frozen=True)
class FrameRange:
    start: int
    end: int
    fps: float

    @property
    def count(self) -> int:
        return self.end - self.start

    @property
    def start_s(self) -> float:
        return self.start / self.fps

    @property
    def end_s(self) -> float:
        return self.end / self.fps


def facts_of(path: Path) -> ClipFacts:
    try:
        media = probe(path, "video")
    except MediaToolError as exc:
        raise DomainError("clip_unreadable", UNREADABLE, 422) from exc
    if not media.fps or not media.duration or not media.width or not media.height:
        raise DomainError("clip_unreadable", UNREADABLE, 422)
    return ClipFacts(
        width=int(media.width),
        height=int(media.height),
        fps=float(media.fps),
        frames=max(1, round(float(media.duration) * float(media.fps))),
        duration=float(media.duration),
        has_audio=bool(media.has_audio),
    )


def plan_range(facts: ClipFacts, start_s: Optional[float], end_s: Optional[float]) -> FrameRange:
    if start_s is None and end_s is None:
        return FrameRange(0, facts.frames, facts.fps)
    start = 0.0 if start_s is None else float(start_s)
    end = facts.duration if end_s is None else float(end_s)
    if start < 0:
        raise DomainError("range_invalid", "The range cannot start before the beginning of the clip", 422)
    if end <= start:
        raise DomainError("range_invalid", "The range must end after it starts", 422)
    if start >= facts.duration:
        raise DomainError(
            "range_outside_clip", f"The range starts ({start:g}s) past the end of the clip ({facts.duration:.3f}s)", 422
        )
    if end > facts.duration + max(EDGE_TOLERANCE_SECONDS, 1.0 / facts.fps):
        raise DomainError(
            "range_outside_clip", f"The range ends ({end:g}s) past the end of the clip ({facts.duration:.3f}s)", 422
        )
    first = round(start * facts.fps)
    last = min(facts.frames, round(end * facts.fps))
    if last <= first:
        raise DomainError("range_invalid", "The range is shorter than one frame of the clip", 422)
    return FrameRange(first, last, facts.fps)


def _rate(fps: float) -> str:
    return f"{fps:.5f}"


def _encode(has_audio: bool, fps: float) -> List[str]:
    args = ["-r", _rate(fps), *video_encoder_args(SUFFIX, CRF)]
    args += audio_codec_args(SUFFIX, True) if has_audio else ["-an"]
    return args


def cut(source: Path, facts: ClipFacts, window: FrameRange, dest: Path) -> Path:
    args = ["-i", str(source), "-map", "0:v:0"]
    args += ["-vf", f"trim=start_frame={window.start}:end_frame={window.end},setpts=PTS-STARTPTS"]
    if facts.has_audio:
        args += ["-map", "0:a:0", "-af", f"atrim=start={window.start_s:.6f}:end={window.end_s:.6f},asetpts=PTS-STARTPTS"]
    args += [*_encode(facts.has_audio, facts.fps), str(dest)]
    FfmpegRunner().run(args)
    return dest


def conform(
    result: Path,
    facts: ClipFacts,
    dest: Path,
    count: Optional[int] = None,
    skip: int = 0,
    adopt_size: bool = False,
    with_audio: bool = False,
) -> Path:
    width, height = facts.width, facts.height
    if adopt_size:
        found = facts_of(result)
        width, height = found.width, found.height
    filters = [
        f"fps={_rate(facts.fps)}",
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos",
        f"crop={width}:{height}",
        "setsar=1",
        "format=yuv420p",
    ]
    if skip:
        filters.append(f"trim=start_frame={skip}")
        filters.append("setpts=PTS-STARTPTS")
    if count is not None:
        filters += [f"tpad=stop_mode=clone:stop={count}", f"trim=end_frame={count}"]
    filters.append("setpts=PTS-STARTPTS")
    args = ["-i", str(result), "-map", "0:v:0", "-vf", ",".join(filters)]
    if with_audio:
        start = skip / facts.fps
        args += ["-map", "0:a:0", "-af", f"atrim=start={start:.6f},asetpts=PTS-STARTPTS"]
    args += [*_encode(with_audio, facts.fps), str(dest)]
    FfmpegRunner().run(args)
    return dest


def _segment_audio(
    label: str, index: int, start_s: Optional[float], end_s: Optional[float], pad_to: Optional[float] = None
) -> str:
    trim = []
    if start_s is not None:
        trim.append(f"start={start_s:.6f}")
    if end_s is not None:
        trim.append(f"end={end_s:.6f}")
    chain = [f"atrim={':'.join(trim)}"] if trim else []
    chain += [
        "asetpts=PTS-STARTPTS",
        f"aresample={SAMPLE_RATE}",
        "aformat=sample_fmts=fltp:channel_layouts=stereo",
    ]
    if pad_to is not None:
        chain.append(f"apad=whole_dur={pad_to:.6f}")
    return f"[{index}:a]{','.join(chain)}[{label}]"


def _segment_video(label: str, index: int, first: Optional[int], last: Optional[int]) -> str:
    trim = []
    if first is not None:
        trim.append(f"start_frame={first}")
    if last is not None:
        trim.append(f"end_frame={last}")
    chain = [f"trim={':'.join(trim)}"] if trim else []
    chain += ["setpts=PTS-STARTPTS", "setsar=1", "format=yuv420p"]
    return f"[{index}:v]{','.join(chain)}[{label}]"


def splice_replace(base: Path, facts: ClipFacts, window: FrameRange, patch: Path, dest: Path) -> Path:
    nodes: List[str] = []
    parts: List[Tuple[str, str]] = []
    if window.start > 0:
        nodes.append(_segment_video("hv", 0, 0, window.start))
        parts.append(("hv", "v"))
    nodes.append(_segment_video("mv", 1, None, None))
    parts.append(("mv", "v"))
    if window.end < facts.frames:
        nodes.append(_segment_video("tv", 0, window.end, None))
        parts.append(("tv", "v"))
    video = "".join(f"[{name}]" for name, _ in parts)
    graph = ";".join([*nodes, f"{video}concat=n={len(parts)}:v=1:a=0[v]"])
    args = ["-i", str(base), "-i", str(patch), "-filter_complex", graph]
    args += ["-map", "[v]"]
    if facts.has_audio:
        args += ["-map", "0:a:0"]
    args += [*_encode(facts.has_audio, facts.fps), str(dest)]
    FfmpegRunner().run(args)
    return dest


def splice_append(
    base: Path, facts: ClipFacts, at: int, added: Path, added_frames: int, added_has_audio: bool, dest: Path
) -> Path:
    at_s = at / facts.fps
    added_s = added_frames / facts.fps
    nodes: List[str] = []
    parts: List[Tuple[str, str]] = []
    sound: List[Tuple[str, str]] = []
    if at > 0:
        nodes.append(_segment_video("hv", 0, 0, at))
        parts.append(("hv", "v"))
    nodes.append(_segment_video("mv", 1, None, None))
    parts.append(("mv", "v"))
    if at < facts.frames:
        nodes.append(_segment_video("tv", 0, at, None))
        parts.append(("tv", "v"))
    if facts.has_audio:
        if at > 0:
            nodes.append(_segment_audio("ha", 0, None, at_s))
            sound.append(("ha", "a"))
        if added_has_audio:
            nodes.append(_segment_audio("ma", 1, None, added_s, added_s))
        else:
            nodes.append(
                f"anullsrc=r={SAMPLE_RATE}:cl=stereo,atrim=end={added_s:.6f},asetpts=PTS-STARTPTS,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[ma]"
            )
        sound.append(("ma", "a"))
        if at < facts.frames:
            nodes.append(_segment_audio("ta", 0, at_s, None))
            sound.append(("ta", "a"))
    video = "".join(f"[{name}]" for name, _ in parts)
    graph = f"{video}concat=n={len(parts)}:v=1:a=0[v]"
    if facts.has_audio:
        names = "".join(f"[{name}]" for name, _ in sound)
        graph += f";{names}concat=n={len(sound)}:v=0:a=1[a]"
    args = ["-i", str(base), "-i", str(added), "-filter_complex", ";".join([*nodes, graph]), "-map", "[v]"]
    if facts.has_audio:
        args += ["-map", "[a]"]
    args += [*_encode(facts.has_audio, facts.fps), str(dest)]
    FfmpegRunner().run(args)
    return dest


def copy_file(source: Path, dest: Path) -> Path:
    shutil.copyfile(source, dest)
    return dest
