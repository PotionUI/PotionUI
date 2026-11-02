from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence

from PIL import Image, ImageOps

from src.platform.media_tools.probe import has_alpha_mode
from src.platform.media_tools.types import (
    FilterFragment,
    ToolContext,
    ToolFailed,
    ToolMedia,
    UnsupportedMedia,
)

if TYPE_CHECKING:
    from src.platform.media_tools.base import MediaTool

logger = logging.getLogger(__name__)

H264_SUFFIXES = (".mp4", ".m4v", ".mov", ".mkv")
FASTSTART_SUFFIXES = (".mp4", ".m4v", ".mov")
WEBM_SUFFIX = ".webm"
VIDEO_OUTPUT_SUFFIXES = (*H264_SUFFIXES, WEBM_SUFFIX)
AUDIO_OUTPUT_SUFFIXES = (".wav", ".mp3", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".opus", ".aiff", ".aif")
AAC_SUFFIXES = (".mp4", ".m4v", ".mov", ".mkv", ".m4a", ".aac")
OPUS_SUFFIXES = (".webm", ".opus")
FALLBACK_VIDEO_SUFFIX = ".mp4"
FALLBACK_AUDIO_SUFFIX = ".wav"

CRF_BY_QUALITY = {"high": 18, "balanced": 23, "small": 28}
DEFAULT_CRF = CRF_BY_QUALITY["balanced"]
WEBM_CRF_OFFSET = 9

OPAQUE_ONLY_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".bmp")
LOSSY_IMAGE_QUALITY = 95
EVEN_CROP = "crop=trunc(iw/2)*2:trunc(ih/2)*2"
SQUARE_PIXELS = "setsar=1"


@dataclass
class MergedPlan:
    seek_start: Optional[float] = None
    seek_end: Optional[float] = None
    vf: list[str] = field(default_factory=list)
    af: list[str] = field(default_factory=list)
    drop_audio: bool = False
    crf: Optional[int] = None
    frames: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    copy_video: bool = False


def advance_media(media: ToolMedia, fragment: FilterFragment) -> ToolMedia:
    changes: dict = {}
    if fragment.width is not None:
        changes["width"] = fragment.width
    if fragment.height is not None:
        changes["height"] = fragment.height
    if fragment.duration is not None:
        changes["duration"] = fragment.duration
    elif fragment.start is not None or fragment.end is not None:
        if media.duration is not None:
            end = media.duration if fragment.end is None else min(fragment.end, media.duration)
            changes["duration"] = max(0.0, end - (fragment.start or 0.0))
    if fragment.drop_audio:
        changes["has_audio"] = False
    return media.with_changes(**changes) if changes else media


def _trim_filters(start: float, end: Optional[float]) -> tuple[list[str], list[str]]:
    parts = [f"start={start:.6f}"]
    if end is not None:
        parts.append(f"end={end:.6f}")
    window = ":".join(parts)
    return [f"trim={window}", "setpts=PTS-STARTPTS"], [f"atrim={window}", "asetpts=PTS-STARTPTS"]


def merge_fragments(media: ToolMedia, fragments: Sequence[FilterFragment]) -> MergedPlan:
    plan = MergedPlan(width=media.width, height=media.height, duration=media.duration)
    current = media
    for fragment in fragments:
        if fragment.start is not None or fragment.end is not None:
            start = fragment.start or 0.0
            if not plan.vf and not plan.af:
                base = plan.seek_start or 0.0
                new_end = base + fragment.end if fragment.end is not None else plan.seek_end
                if new_end is not None and plan.seek_end is not None:
                    new_end = min(new_end, plan.seek_end)
                plan.seek_start = base + start
                plan.seek_end = new_end
            else:
                video_trim, audio_trim = _trim_filters(start, fragment.end)
                plan.vf.extend(video_trim)
                plan.af.extend(audio_trim)
        plan.vf.extend(fragment.vf)
        plan.af.extend(fragment.af)
        plan.drop_audio = plan.drop_audio or fragment.drop_audio
        if fragment.crf is not None:
            plan.crf = fragment.crf
        if fragment.frames is not None:
            plan.frames = fragment.frames if plan.frames is None else min(plan.frames, fragment.frames)
        current = advance_media(current, fragment)
    plan.width, plan.height, plan.duration = current.width, current.height, current.duration
    plan.copy_video = (
        bool(fragments)
        and all(fragment.copy_video for fragment in fragments)
        and plan.seek_start is None
        and plan.seek_end is None
    )
    return plan


def video_output_suffix(media: ToolMedia) -> str:
    suffix = media.path.suffix.lower()
    return suffix if suffix in VIDEO_OUTPUT_SUFFIXES else FALLBACK_VIDEO_SUFFIX


def audio_output_suffix(media: ToolMedia) -> str:
    suffix = media.path.suffix.lower()
    return suffix if suffix in AUDIO_OUTPUT_SUFFIXES else FALLBACK_AUDIO_SUFFIX


def _seek_args(plan: MergedPlan) -> list[str]:
    args: list[str] = []
    if plan.seek_start:
        args += ["-ss", f"{plan.seek_start:.6f}"]
    if plan.seek_end is not None:
        args += ["-t", f"{max(0.0, plan.seek_end - (plan.seek_start or 0.0)):.6f}"]
    return args


PCM_WAV_CODECS = {8: "pcm_u8", 16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}
PCM_AIFF_CODECS = {16: "pcm_s16be", 24: "pcm_s24be", 32: "pcm_s32be"}
FLAC_MAX_BITS = 24
LOSSLESS_AUDIO_CODECS = ("pcm_", "flac", "alac", "wavpack", "tta")


def lossless_audio_args(media: ToolMedia, suffix: str) -> list[str]:
    codec = (media.audio_codec or "").lower()
    if not codec.startswith(LOSSLESS_AUDIO_CODECS):
        return []
    depth = media.bit_depth or 16
    if suffix == ".wav":
        if codec.startswith("pcm_f64"):
            return ["-c:a", "pcm_f64le"]
        if codec.startswith("pcm_f32"):
            return ["-c:a", "pcm_f32le"]
        return ["-c:a", PCM_WAV_CODECS.get(depth, "pcm_s32le" if depth > 24 else "pcm_s16le")]
    if suffix in (".aiff", ".aif"):
        return ["-c:a", PCM_AIFF_CODECS.get(depth, "pcm_s32be" if depth > 24 else "pcm_s16be")]
    if suffix == ".flac":
        if depth <= 16 and not codec.startswith(("pcm_f32", "pcm_f64")):
            return ["-sample_fmt", "s16"]
        return ["-sample_fmt", "s32", "-bits_per_raw_sample", str(FLAC_MAX_BITS)]
    return []


def audio_codec_args(suffix: str, reencode: bool) -> list[str]:
    if not reencode:
        return ["-c:a", "copy"]
    if suffix in OPUS_SUFFIXES:
        return ["-c:a", "libopus", "-b:a", "128k"]
    if suffix in AAC_SUFFIXES:
        return ["-c:a", "aac", "-b:a", "192k"]
    if suffix == ".mp3":
        return ["-b:a", "192k"]
    return []


def video_encoder_args(suffix: str, crf: Optional[int]) -> list[str]:
    quality = DEFAULT_CRF if crf is None else crf
    if suffix == WEBM_SUFFIX:
        return [
            "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(quality + WEBM_CRF_OFFSET),
            "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-pix_fmt", "yuv420p",
        ]
    args = ["-c:v", "libx264", "-preset", "medium", "-crf", str(quality), "-pix_fmt", "yuv420p"]
    if suffix in FASTSTART_SUFFIXES:
        args += ["-movflags", "+faststart"]
    return args


def build_timed_args(media: ToolMedia, plan: MergedPlan, out_path: Path) -> list[str]:
    suffix = out_path.suffix.lower()
    args = _seek_args(plan)
    args += ["-i", str(media.path)]
    reencode_audio = (
        bool(plan.af)
        or plan.seek_start is not None
        or plan.seek_end is not None
        or suffix != media.path.suffix.lower()
    )

    if media.kind == "audio":
        args += ["-vn"]
        if plan.af:
            args += ["-af", ",".join(plan.af)]
        args += lossless_audio_args(media, suffix) or audio_codec_args(suffix, True)
        args += [str(out_path)]
        return args

    if plan.copy_video and suffix == media.path.suffix.lower():
        return args + _copied_video_args(media, plan, suffix, reencode_audio) + [str(out_path)]

    video_filters = list(plan.vf)
    even = plan.width is not None and plan.height is not None and plan.width % 2 == 0 and plan.height % 2 == 0
    if not even:
        video_filters.extend([EVEN_CROP, SQUARE_PIXELS])
    args += ["-map", "0:v:0"]
    if plan.drop_audio:
        args += ["-an"]
    else:
        args += ["-map", "0:a:0?"]
        if plan.af and media.has_audio is not False:
            args += ["-af", ",".join(plan.af)]
        args += audio_codec_args(suffix, reencode_audio)
    if video_filters:
        args += ["-vf", ",".join(video_filters)]
    if plan.frames is not None:
        args += ["-fps_mode", "passthrough", "-frames:v", str(plan.frames)]
    args += video_encoder_args(suffix, plan.crf)
    args += [str(out_path)]
    return args


def _copied_video_args(media: ToolMedia, plan: MergedPlan, suffix: str, reencode_audio: bool) -> list[str]:
    args = ["-map", "0:v:0"]
    if plan.drop_audio:
        args += ["-an"]
    else:
        args += ["-map", "0:a:0?"]
        if plan.af and media.has_audio is not False:
            args += ["-af", ",".join(plan.af)]
        args += audio_codec_args(suffix, reencode_audio)
    args += ["-c:v", "copy"]
    if suffix in FASTSTART_SUFFIXES:
        args += ["-movflags", "+faststart"]
    return args


def run_fragments(media: ToolMedia, fragments: Sequence[FilterFragment], ctx: ToolContext) -> ToolMedia:
    plan = merge_fragments(media, fragments)
    suffix = audio_output_suffix(media) if media.kind == "audio" else video_output_suffix(media)
    out_path = ctx.new_path(suffix, stem=media.path.stem[:40] or "out")
    try:
        ctx.ffmpeg(build_timed_args(media, plan, out_path), duration=plan.duration)
    except BaseException:
        out_path.unlink(missing_ok=True)
        raise
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise ToolFailed("The encoder produced an empty file")
    return ctx.probe(out_path, media.kind)


def prepare_image(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened) or opened
        image.load()
        return image


def save_image(image: Image.Image, dest: Path) -> Path:
    suffix = dest.suffix.lower()
    alpha = has_alpha_mode(image)
    if alpha and suffix in OPAQUE_ONLY_IMAGE_SUFFIXES:
        dest = dest.with_suffix(".png")
        suffix = ".png"
    options: dict = {}
    icc = image.info.get("icc_profile")
    if suffix in (".jpg", ".jpeg"):
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        options.update(quality=LOSSY_IMAGE_QUALITY, subsampling=0)
    elif suffix == ".webp":
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if alpha else "RGB")
        options.update(quality=LOSSY_IMAGE_QUALITY)
    elif suffix == ".bmp" and image.mode not in ("RGB", "L", "P"):
        image = image.convert("RGB")
    if icc and suffix in (".jpg", ".jpeg", ".png", ".webp"):
        options["icc_profile"] = icc
    image.save(dest, **options)
    return dest


def run_image_steps(
    media: ToolMedia,
    steps: Sequence[tuple["MediaTool", dict]],
    ctx: ToolContext,
) -> ToolMedia:
    image = prepare_image(media.path)
    for tool, params in steps:
        image = tool.image(image, params)
    dest = ctx.new_path(media.path.suffix or ".png", stem=media.path.stem[:40] or "out")
    written = save_image(image, dest)
    return ctx.probe(written, "image")


def run_single(tool: "MediaTool", media: ToolMedia, params: dict, ctx: ToolContext) -> ToolMedia:
    if media.kind == "image":
        if not tool.has_image_path():
            raise UnsupportedMedia("This tool cannot edit images")
        return run_image_steps(media, [(tool, params)], ctx)
    fragment = tool.video_filter(media, params)
    if fragment is None:
        raise UnsupportedMedia(f"This tool cannot edit {media.kind}")
    return run_fragments(media, [fragment], ctx)

