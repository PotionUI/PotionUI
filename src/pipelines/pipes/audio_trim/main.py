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
of it should cost a few hundred KB of I/O, not a full-file load.
`torchaudio.save` is unusable on this box (torchaudio 2.11 routes `save`
through TorchCodec, which isn't installed - see media_loader/audio_handler
notes), which is the other reason this pipe is soundfile-only, read and
write.

The output is always a `.wav` - deterministic and lossless, regardless of the
source's own container - preserving the source's sample rate and channel
count exactly (channel count falls out of the array shape `soundfile` reads;
it is never resampled or downmixed here).
"""

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
        with sf.SoundFile(str(audio_path)) as source:
            sample_rate = source.samplerate
            channels = source.channels
            total_frames = len(source)
            source_duration = total_frames / sample_rate if sample_rate else 0.0

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
            data = source.read(frames=frame_count, dtype="float32", always_2d=False)

        out_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        sf.write(out_path, data, samplerate=sample_rate)

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
