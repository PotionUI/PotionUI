"""Trim an audio file to a window - the audio counterpart of `canvas_fit`'s
"deterministic transform, no model, no seed" shape.

Contract: **(start, duration)**, not (start, end). Two independently-edited
absolute positions (start/end) need a cross-field invariant (`end > start`)
that a UI has to keep enforcing; `duration` sidesteps it entirely - it only
needs its own `>= 0` bound, the same shape every other slider in this preset
family already has (see `canvas_fit`'s `scale_percent`). It's also the more
natural dial: "keep 15s starting at 0:30" is what a user means, not an
absolute end timestamp they'd have to compute from the source's length.

``duration_seconds`` (kept + clamped)::

    start_frame = min(round(start_seconds * sample_rate), total_frames)
    end_frame   = min(start_frame + round(duration_seconds * sample_rate), total_frames)

Both `start_seconds` and `duration_seconds` are clamped to non-negative
before this; `end_frame` is clamped again to be no smaller than `start_frame`.
So the window can never run past the source, and a request that's entirely
out of range (start at/after the source's end, or a zero/negative duration)
degrades to a *well-formed, empty* (0-frame) output rather than raising - a
trim pipe's job is to produce the window that was actually askable given the
source, not to fail a whole generation over a slider dragged too far. Either
degenerate case emits a `ProgressGenerationOutput` naming what happened, so
it is visible rather than a silent zero-length surprise.

Reads/writes go through `soundfile` (seek + partial `read`), never a full
file decode: a 380s/44.1kHz stereo clip is ~67MB, and copying a 3s window out
of it should cost a few hundred KB of I/O, not a full-file load. The window
itself is also never materialized as one in-memory array - a long window
(minutes of stereo `DOUBLE` audio) would otherwise hold hundreds of MB live
for the whole trim. Instead the copy streams in `_TRIM_BLOCK_FRAMES`-frame
blocks: seek once, then alternate bounded `read()`/`write()` calls until the
window is exhausted, so the live buffer is capped at one block regardless of
how long the requested window is.
`torchaudio.save` is unusable on this box (torchaudio 2.11 routes `save`
through TorchCodec, which isn't installed - see media_loader/audio_handler
notes), which is the other reason this pipe is soundfile-only, read and
write.

The output is always a `.wav` - deterministic and lossless, regardless of the
source's own container - preserving the source's sample rate and channel
count exactly (channel count falls out of the array shape `soundfile` reads;
it is never resampled or downmixed here).

"Lossless" also has to hold at the sample level, not just the frame boundary:
`soundfile`'s own WAV write default is `PCM_16`, so reading a higher-precision
source as `float32` and writing it back with no explicit `subtype` silently
quantizes `PCM_24`/`PCM_32`/`FLOAT`/`DOUBLE` sources down to 16 bits. To avoid
that, the source's own `SoundFile.subtype` picks both the read dtype (wide
enough to hold the subtype exactly) and the write subtype (round-tripping the
source's own precision), via one explicit policy:

============  ===========  =============
source        read dtype   write subtype
============  ===========  =============
PCM_16        int16        PCM_16
PCM_24        int32        PCM_24
PCM_32        int32        PCM_32
FLOAT         float32      FLOAT
DOUBLE        float64      DOUBLE
PCM_U8        int16        PCM_16   (lossless widening, not a passthrough)
PCM_S8        int16        PCM_16   (lossless widening, not a passthrough)
anything else float32      FLOAT    (compressed/companded codecs - no
                                      lossless integer form, so this is a
                                      documented fallback, never a silent
                                      PCM_16 downgrade)
============  ===========  =============

The chosen write subtype is also checked against `sf.check_format('WAV', ...)`
before use (a source subtype the installed libsndfile can decode but not
re-encode into a `.wav` container) and falls back to `FLOAT` if unsupported.
"""

import os
import tempfile
from typing import Any, Dict, List, Tuple

import soundfile as sf

from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    logger,
)
from src.pipelines.outputs import AudioGenerationOutput, Icon, Progress, ProgressGenerationOutput


# source subtype -> (read dtype, write subtype). See the module docstring
# table this pins. Keys are `soundfile.SoundFile.subtype` strings; a subtype
# not listed here (a compressed/companded codec) uses `_FALLBACK_IO_POLICY`.
_SUBTYPE_IO_POLICY: Dict[str, Tuple[str, str]] = {
    "PCM_16": ("int16", "PCM_16"),
    "PCM_24": ("int32", "PCM_24"),
    "PCM_32": ("int32", "PCM_32"),
    "FLOAT": ("float32", "FLOAT"),
    "DOUBLE": ("float64", "DOUBLE"),
    "PCM_U8": ("int16", "PCM_16"),
    "PCM_S8": ("int16", "PCM_16"),
}
_FALLBACK_IO_POLICY: Tuple[str, str] = ("float32", "FLOAT")

# Frames copied per read/write block while streaming the trim window. Bounds
# the live payload to `_TRIM_BLOCK_FRAMES x channels x bytes-per-sample`
# regardless of the requested window's length; at this policy's widest
# combination (stereo `DOUBLE`, 8 bytes/sample) that's
# 65536 x 2 x 8 = 2**20 bytes (1 MiB) held at once, versus an unbounded
# single-read buffer that grows with the requested duration (a 50M-frame
# stereo `DOUBLE` window is ~800 MB read in one call).
_TRIM_BLOCK_FRAMES = 65536


def resolve_trim_io_policy(subtype: str) -> Tuple[str, str]:
    """Source `subtype` -> `(read_dtype, write_subtype)`, per the module
    docstring's policy table. Falls back to `_FALLBACK_IO_POLICY` for a
    source subtype not in `_SUBTYPE_IO_POLICY`, and again if the chosen write
    subtype turns out to be unsupported for a `.wav` container."""
    read_dtype, write_subtype = _SUBTYPE_IO_POLICY.get(subtype, _FALLBACK_IO_POLICY)
    if not sf.check_format("WAV", write_subtype):
        read_dtype, write_subtype = _FALLBACK_IO_POLICY
    return read_dtype, write_subtype


def compute_trim_window(start_seconds: float, duration_seconds: float,
                        sample_rate: int, total_frames: int) -> Tuple[int, int]:
    """`(start_seconds, duration_seconds)` -> `(start_frame, end_frame)`,
    clamped to `[0, total_frames]`. See the module docstring for the exact
    contract this pins - it's what
    `tests/pipelines/pipes/audio_trim/test_audio_trim.py` bite-checks
    directly, without needing a real audio file."""
    if sample_rate <= 0:
        raise ValueError(f"audio_trim: source has a non-positive sample rate ({sample_rate})")
    if total_frames < 0:
        raise ValueError(f"audio_trim: source has a negative frame count ({total_frames})")

    start_seconds = max(0.0, float(start_seconds))
    duration_seconds = max(0.0, float(duration_seconds))

    start_frame = min(round(start_seconds * sample_rate), total_frames)
    requested_frames = round(duration_seconds * sample_rate)
    end_frame = min(start_frame + requested_frames, total_frames)
    end_frame = max(end_frame, start_frame)

    return start_frame, end_frame


class AudioTrimPipe(BasePipe):
    name = "audio_trim"
    description = "Trim audio to a start/duration window, sample-accurate, without decoding the whole file"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "start_seconds": 0.0,
            "duration_seconds": 10.0,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("start_seconds", float, 0.0,
                           "Where the kept window starts, in seconds from the source's beginning",
                           required=False, min_value=0.0),
            PipeConfigSpec("duration_seconds", float, 10.0,
                           "How many seconds to keep from start_seconds - clamped to the source's remaining length",
                           required=False, min_value=0.0),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("audio", IOType.AUDIO, True, "Source audio file path(s) to trim", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("audio", IOType.AUDIO, "Trimmed audio file path(s)", is_array=True),
        ]

    def _trim_one(self, audio_path: str, start_seconds: float, duration_seconds: float,
                 generation_outputs: callable, index: int, total: int) -> str:
        out_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        try:
            with sf.SoundFile(str(audio_path)) as source:
                sample_rate = source.samplerate
                channels = source.channels
                total_frames = len(source)
                source_duration = total_frames / sample_rate if sample_rate else 0.0
                read_dtype, write_subtype = resolve_trim_io_policy(source.subtype)

                start_frame, end_frame = compute_trim_window(
                    start_seconds, duration_seconds, sample_rate, total_frames
                )
                frame_count = end_frame - start_frame

                requested_end_frame = start_frame + max(0, round(max(0.0, duration_seconds) * sample_rate))
                if frame_count <= 0:
                    generation_outputs(ProgressGenerationOutput(
                        state=f"Trim window is empty: source is <<NUMBER:{source_duration:.2f}s:clock>> long, "
                              f"requested start <<NUMBER:{start_seconds:.2f}s>> leaves nothing to keep",
                        icon=Icon("alert-triangle"),
                    ))
                elif requested_end_frame > total_frames:
                    generation_outputs(ProgressGenerationOutput(
                        state=f"Trim window clamped to source length: kept <<NUMBER:{frame_count / sample_rate:.2f}s:scissors>> "
                              f"of the requested <<NUMBER:{duration_seconds:.2f}s>>",
                        icon=Icon("scissors"),
                    ))

                source.seek(start_frame)
                with sf.SoundFile(out_path, mode="w", samplerate=sample_rate, channels=channels,
                                  subtype=write_subtype) as dest:
                    remaining = frame_count
                    while remaining > 0:
                        block_frames = min(_TRIM_BLOCK_FRAMES, remaining)
                        data = source.read(frames=block_frames, dtype=read_dtype, always_2d=False)
                        dest.write(data)
                        remaining -= block_frames
        except Exception:
            if os.path.exists(out_path):
                os.remove(out_path)
            raise

        kept_duration = frame_count / sample_rate if sample_rate else 0.0
        generation_outputs(ProgressGenerationOutput(
            state=f"Trimmed audio <<NUMBER:{index + 1}/{total}:scissors>>: kept "
                  f"<<NUMBER:{kept_duration:.2f}s:clock>> ({channels}ch @ {sample_rate}Hz)",
            icon=Icon("scissors"),
            progress=Progress(100, 100),
        ))

        generation_outputs(AudioGenerationOutput(
            audio_path=out_path,
            temporary=False,
            duration=kept_duration,
            sample_rate=sample_rate,
            channels=channels,
        ))

        logger.info(
            f"[AUDIO_TRIM] {audio_path} ({source_duration:.2f}s) -> {out_path} "
            f"[{start_frame}:{end_frame}] = {kept_duration:.2f}s @ {sample_rate}Hz, {channels}ch"
        )

        return out_path

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        audio_paths = pipe_input.input.get("audio")
        if not audio_paths:
            raise ValueError("audio_trim requires at least one input audio file")
        if isinstance(audio_paths, str):
            audio_paths = [audio_paths]

        start_seconds = float(self.config.get("start_seconds", 0.0))
        duration_seconds = float(self.config.get("duration_seconds", 10.0))

        generation_outputs(ProgressGenerationOutput(
            state=f"Trimming <<NUMBER:{len(audio_paths)} audio file:scissors>>",
            icon=Icon("scissors"),
            progress=Progress(0, 100),
        ))

        results = [
            self._trim_one(path, start_seconds, duration_seconds, generation_outputs, i, len(audio_paths))
            for i, path in enumerate(audio_paths)
        ]

        return PipeOutput(output={"audio": results})
