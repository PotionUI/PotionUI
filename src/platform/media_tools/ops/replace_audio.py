from __future__ import annotations

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.fastpath import (
    FASTSTART_SUFFIXES,
    audio_codec_args,
    video_encoder_args,
    video_output_suffix,
)
from src.platform.media_tools.types import (
    EDITOR_SCOPE,
    InvalidToolParams,
    ToolContext,
    ToolFailed,
    ToolMedia,
    ToolSpec,
    UnsupportedMedia,
)

SPEC = dict(
    id="replace_audio",
    label="Replace audio",
    icon="audio",
    accepts=("video",),
    produces="same",
    cardinality="one_to_one",
    group="audio",
    name_suffix="-audio",
    description="Use another sound track",
    scopes=(EDITOR_SCOPE,),
    params=[
        {
            "name": "audio",
            "type": "media",
            "label": "Audio",
            "description": "Library audio or video",
            "accepts": ["audio", "video"],
        },
        {
            "name": "start",
            "type": "float",
            "label": "Start at",
            "description": "Skip into the track",
            "default": 0,
            "min": 0,
            "unit": "s",
        },
        {
            "name": "loop",
            "type": "checkbox",
            "label": "Loop to fit",
            "default": True,
        },
    ],
)


class ReplaceAudio(MediaTool):
    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        count = len(inputs)
        return [
            self._replace(media, params, ctx.scoped(index / count, (index + 1) / count))
            for index, media in enumerate(inputs)
        ]

    def still(self, image, params: dict, frame):
        return image

    def advance(self, media: ToolMedia, params: dict) -> ToolMedia:
        return media.with_changes(has_audio=True)

    def _replace(self, media: ToolMedia, params: dict, ctx: ToolContext) -> ToolMedia:
        track = params.get("audio")
        if not isinstance(track, ToolMedia):
            raise UnsupportedMedia("Replace audio needs the new track attached")
        if track.kind == "image" or track.has_audio is False:
            raise UnsupportedMedia("The new track has no sound")
        if media.duration is None:
            raise ToolFailed("Could not read the video's duration")
        start = float(params.get("start") or 0.0)
        if track.duration is not None and start >= track.duration:
            raise InvalidToolParams(
                "start", f"The start ({start:g}s) is past the end of the track ({track.duration:.3f}s)"
            )
        suffix = video_output_suffix(media)
        out_path = ctx.new_path(suffix, stem=media.path.stem[:40] or "out")
        args = ["-i", str(media.path)]
        if params.get("loop", True):
            args += ["-stream_loop", "-1"]
        args += [
            "-i", str(track.path),
            "-filter_complex", f"[1:a:0]atrim=start={start:.6f},asetpts=PTS-STARTPTS[track]",
            "-map", "0:v:0", "-map", "[track]",
        ]
        if suffix == media.path.suffix.lower():
            args += ["-c:v", "copy"]
            if suffix in FASTSTART_SUFFIXES:
                args += ["-movflags", "+faststart"]
        else:
            args += video_encoder_args(suffix, None)
        args += audio_codec_args(suffix, True)
        args += ["-t", f"{media.duration:.6f}", str(out_path)]
        try:
            ctx.ffmpeg(args, duration=media.duration)
        except BaseException:
            out_path.unlink(missing_ok=True)
            raise
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise ToolFailed("The encoder produced an empty file")
        return ctx.probe(out_path, "video")


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=ReplaceAudio())
