"""Frames -> MP4 encoding via ffmpeg, for native-engine video generators
(Wan/LTX) and any other pipe producing an in-memory frame sequence.

Design note: `video_frame_merger` (`src/pipelines/pipes/video_frame_merger/main.py`)
already encodes video, but through OpenCV's `cv2.VideoWriter` with a
fourcc-fallback ladder, tightly coupled to its own PIL-frame preprocessing
(resize modes, fade in/out, loop/reverse). That logic doesn't factor cleanly
into a generic encode helper, and OpenCV's fourcc path can't do CRF-based
quality control the way ffmpeg can. So this helper is written fresh against
ffmpeg (subprocess, rawvideo piped to stdin) rather than refactored out of
`video_frame_merger` -- `video_frame_merger` is left untouched.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

import numpy as np
import torch
from PIL import Image

logger = logging.getLogger(__name__)

FramesInput = Union[np.ndarray, List[Image.Image], torch.Tensor]


class FFmpegNotFoundError(RuntimeError):
    """Raised when the `ffmpeg` binary is not available on PATH."""


@dataclass
class AudioTrack:
    """An in-memory audio track to mux alongside encoded video.

    `waveform` is (channels, samples) float32 in [-1, 1] -- the same shape
    convention as torchaudio/most native-engine audio pipes, not the
    (samples, channels) layout WAV/ffmpeg expect on the wire.
    """
    waveform: "np.ndarray"
    sample_rate: int


AudioInput = Union[AudioTrack, str, Path, None]


def has_audio_stream(path: Union[str, Path]) -> bool:
    """Probe ``path`` for an audio stream via ``ffprobe``.

    Used to decide whether an existing media file is safe to hand to
    :func:`encode_frames_to_mp4` as ``audio=``: a source video with NO audio
    track must fall back to the plain ``-an`` path (byte-identical to no
    audio at all), not be passed through as a second ffmpeg input that then
    has nothing for ``-map 1:a:0`` to resolve (ffmpeg would exit non-zero).
    Returns ``False`` -- never raises -- on any probe failure (``ffprobe``
    missing, a corrupt/unreadable file, a timeout), so a probing hiccup
    degrades to "no audio" rather than crashing an otherwise-working encode.
    """
    if shutil.which("ffprobe") is None:
        return False
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", "-select_streams", "a",
                str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        return bool(data.get("streams"))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return False


def probe_source_fps(path: Union[str, Path]) -> Optional[float]:
    """Best-effort native frame rate of ``path`` via ``ffprobe`` (same idiom as
    :func:`has_audio_stream`).

    Used by refine/upscale flows that re-encode an existing source video at a
    STATIC config default (e.g. 25fps) with no way to know the real source
    rate at preset-render time -- calling this at RUN time lets the encode
    step sync to the source instead of silently drifting audio/video out of
    time when the source isn't at that default. Returns ``None`` (never
    raises) on any probe failure -- missing binary, corrupt file, timeout, an
    unparseable/zero rate -- so callers fall back to their own default.
    """
    if shutil.which("ffprobe") is None:
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", "-select_streams", "v:0",
                str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        streams = data.get("streams") or []
        if not streams:
            return None
        raw = streams[0].get("r_frame_rate")
        if not raw:
            return None
        if "/" in raw:
            num, den = raw.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else None
        return float(raw)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, ValueError, ZeroDivisionError):
        return None


def probe_source_duration(path: Union[str, Path]) -> Optional[float]:
    """Best-effort container-level duration (seconds) of ``path`` via
    ``ffprobe``'s ``format`` block -- decode-independent (reads metadata only,
    no frame decoding), same best-effort idiom as :func:`probe_source_fps`.

    Paired with :func:`probe_decoded_frame_count` by :func:`probe_effective_fps`
    to derive a TRUE frame rate (``frame_count / duration``) that is robust to
    variable-frame-rate sources and mislabeled ``r_frame_rate`` metadata, which
    :func:`probe_source_fps` alone cannot detect. Returns ``None`` (never
    raises) on any probe failure -- missing binary, corrupt file, timeout, an
    unparseable/non-positive duration.
    """
    if shutil.which("ffprobe") is None:
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_entries", "format=duration",
                str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        raw = (data.get("format") or {}).get("duration")
        if raw is None:
            return None
        duration = float(raw)
        return duration if duration > 0 else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, ValueError):
        return None


def probe_decoded_frame_count(path: Union[str, Path]) -> Optional[int]:
    """Exact video-frame count of ``path``'s first video stream, obtained by
    actually decoding via ffprobe's ``-count_frames``/``nb_read_frames`` --
    unlike the ``nb_frames`` metadata field (frequently absent, or simply
    wrong for variable-frame-rate sources and edited/remuxed containers),
    this reflects what the stream truly decodes to.

    Slower than :func:`probe_source_fps`/:func:`probe_source_duration` (it
    decodes the whole stream), so it is only ever combined with
    :func:`probe_source_duration` inside :func:`probe_effective_fps` for the
    refine/upscale audio-sync path, not called on every video probe. Returns
    ``None`` (never raises) on any probe failure.
    """
    if shutil.which("ffprobe") is None:
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-count_frames", "-select_streams", "v:0",
                "-show_entries", "stream=nb_read_frames",
                str(path),
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        streams = data.get("streams") or []
        if not streams:
            return None
        raw = streams[0].get("nb_read_frames")
        if raw is None:
            return None
        count = int(raw)
        return count if count > 0 else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, ValueError):
        return None


# A duration-derived rate must differ from the nominal ``r_frame_rate`` by
# more than this fraction to be considered a MATERIAL disagreement worth
# overriding the (cleaner, often-exact-rational) nominal value with -- guards
# against float noise in the duration-derived estimate flip-flopping the
# choice for sources that are, for all practical purposes, at their nominal
# rate already.
_FPS_DISAGREEMENT_THRESHOLD = 0.02


def probe_effective_fps(path: Union[str, Path]) -> Optional[float]:
    """Best-effort TRUE frame rate of ``path``, preferring a duration-derived
    rate (``decoded_frame_count / duration``) over the nominal ``r_frame_rate``
    (:func:`probe_source_fps`) whenever the two disagree by more than
    :data:`_FPS_DISAGREEMENT_THRESHOLD` (relative).

    ``r_frame_rate`` is a per-stream metadata LABEL that can be flatly wrong
    for variable-frame-rate sources or mislabeled/remuxed containers; a
    duration-derived rate reflects what the file actually decodes to, so it
    is preferred whenever it materially disagrees. When both roughly agree,
    the nominal rate is kept (it's often an exact rational like
    ``24000/1001`` that a float division would only add noise to). Falls back
    to whichever single probe succeeded, and returns ``None`` only when
    NEITHER probe could produce a rate (missing ffprobe, corrupt file,
    timeout, ...).
    """
    nominal = probe_source_fps(path)
    duration = probe_source_duration(path)
    frame_count = probe_decoded_frame_count(path)
    derived = (frame_count / duration) if (duration and frame_count) else None

    if derived is not None and derived > 0:
        if not nominal or nominal <= 0:
            return derived
        if abs(derived - nominal) / nominal > _FPS_DISAGREEMENT_THRESHOLD:
            return derived
        return nominal
    return nominal


def _write_wav_pcm16(waveform: np.ndarray, sample_rate: int, out_path: Union[str, Path]) -> None:
    """Write a (channels, samples) float32 [-1, 1] waveform to a PCM16 WAV file.

    Values are clipped to [-1, 1] before quantizing, so an over-driven track
    is silently clamped rather than wrapping/aliasing.
    """
    arr = np.asarray(waveform, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[np.newaxis, :]
    if arr.ndim != 2:
        raise ValueError(f"expected a (channels, samples) array, got shape {arr.shape}")

    channels = arr.shape[0]
    # wave writes frames as interleaved samples: (samples, channels) -> flat int16 bytes.
    interleaved = np.clip(arr, -1.0, 1.0).T
    pcm16 = (interleaved * 32767.0).round().astype(np.int16)

    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16.tobytes())


def _build_ffmpeg_args(
    *,
    width: int,
    height: int,
    fps: float,
    codec: str,
    crf: int,
    out_path: Union[str, Path],
    audio_path: Optional[Union[str, Path]] = None,
) -> List[str]:
    """Build the ffmpeg argv, given an already-resolved audio input file (or None).

    Kept as a pure function of primitives (no frame data, no filesystem access)
    so the exact argument list -- including ordering -- can be asserted in
    tests without invoking ffmpeg.
    """
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "-",
    ]
    if audio_path is not None:
        # Second input, declared before any output options -- ffmpeg requires
        # every `-i` to precede the output-option block it doesn't belong to.
        cmd += ["-i", str(audio_path)]
        # Explicit stream mapping: without it, ffmpeg's automatic stream
        # selection picks the VIDEO stream with the highest resolution across
        # every input. That's harmless when `audio_path` is a synthesized WAV
        # (no video stream to compete), but WRONG when it's an existing video
        # file passed through as an audio source -- its own video stream could
        # win over the piped rawvideo frames this function is actually
        # encoding. Pin video to input 0 (the pipe) and audio to input 1's
        # audio stream explicitly, regardless of either input's resolution or
        # stream layout. (Callers are expected to have already confirmed input
        # 1 actually has an audio stream -- see `has_audio_stream` -- so a bare
        # `1:a:0`, not the optional `1:a:0?`, is intentional: a missing stream
        # here is a caller bug, not something to map around silently.)
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-an"]

    cmd += [
        "-c:v", codec,
        "-pix_fmt", "yuv420p",
        "-crf", str(crf),
        "-movflags", "+faststart",
    ]

    if audio_path is not None:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]

    cmd += [str(out_path)]
    return cmd


def _normalize_frames(frames: FramesInput) -> np.ndarray:
    """Normalize any of the three accepted frame formats into a contiguous
    uint8 numpy array shaped (T, H, W, 3), RGB channel order.
    """
    if isinstance(frames, torch.Tensor):
        if frames.ndim != 4:
            raise ValueError(f"expected a (T,C,H,W) tensor, got shape {tuple(frames.shape)}")
        arr = frames.detach().to(dtype=torch.float32, device="cpu")
        arr = arr.clamp(0.0, 1.0).mul(255.0).round().to(torch.uint8)
        arr = arr.permute(0, 2, 3, 1).contiguous().numpy()  # T,C,H,W -> T,H,W,C
        if arr.shape[-1] == 1:
            arr = np.repeat(arr, 3, axis=-1)
        elif arr.shape[-1] != 3:
            raise ValueError(f"expected 1 or 3 channels, got {arr.shape[-1]}")
        return arr

    if isinstance(frames, list):
        if not frames:
            raise ValueError("frames list is empty")
        return np.stack([np.array(f.convert("RGB"), dtype=np.uint8) for f in frames], axis=0)

    if isinstance(frames, np.ndarray):
        if frames.ndim != 4 or frames.shape[-1] != 3:
            raise ValueError(f"expected a (T,H,W,3) uint8 array, got shape {frames.shape}")
        return np.ascontiguousarray(frames.astype(np.uint8, copy=False))

    raise TypeError(
        f"unsupported frames type {type(frames).__name__}; expected a numpy (T,H,W,3) "
        "uint8 array, a list of PIL Images, or a float torch.Tensor (T,C,H,W) in [0,1]"
    )


def _pad_to_even(frames: np.ndarray) -> np.ndarray:
    """yuv420p (the output pixel format below) requires even width/height."""
    _, h, w, _ = frames.shape
    pad_h = h % 2
    pad_w = w % 2
    if not pad_h and not pad_w:
        return frames
    return np.pad(frames, ((0, 0), (0, pad_h), (0, pad_w), (0, 0)), mode="edge")


def encode_frames_to_mp4(
    frames: FramesInput,
    out_path: Union[str, Path],
    fps: float,
    *,
    codec: str = "libx264",
    crf: int = 18,
    audio: AudioInput = None,
) -> Path:
    """Encode a frame sequence to an MP4 file via ffmpeg (rawvideo piped to stdin).

    `frames` accepts any of: a uint8 numpy array (T,H,W,3) RGB, a list of PIL
    Images, or a float `torch.Tensor` (T,C,H,W) in [0,1] -- all normalized to
    the same rawvideo buffer before piping. Odd width/height is edge-padded
    to even (yuv420p requires it). `crf` is passed through as-is; it's only
    meaningful for crf-capable encoders (the libx264/libx265 default) --
    passing an incompatible `codec` is the caller's responsibility.

    `audio`, when given, is muxed into the output as an AAC track:
    - `AudioTrack`: an in-memory waveform, written to a temporary PCM16 WAV
      and cleaned up after encoding.
    - `str`/`Path`: an existing media file, passed straight to ffmpeg as a
      second input (e.g. a user-supplied replacement track, or an existing
      video whose audio track should carry over into this new encode --
      standalone upscale flows use this to preserve a source video's audio).
      Probed with `has_audio_stream` first: a file with no audio stream
      degrades to the same no-audio path as `audio=None` (logged, not
      raised) rather than being handed to ffmpeg with nothing for `-map` to
      resolve.
    - `None` (default): behavior is byte-identical to before `audio` existed
      -- no audio stream, `-an` passed through.
    The muxed output is trimmed to the shorter of the two streams (`-shortest`).

    Raises `FFmpegNotFoundError` if `ffmpeg` isn't on PATH, `ValueError` for
    malformed/empty input, and `RuntimeError` if ffmpeg exits non-zero or
    times out.
    """
    if shutil.which("ffmpeg") is None:
        raise FFmpegNotFoundError(
            "ffmpeg binary not found on PATH -- required to encode video output. "
            "Install ffmpeg (e.g. `apt install ffmpeg`) and retry."
        )

    arr = _normalize_frames(frames)
    arr = _pad_to_even(arr)
    t, h, w, _ = arr.shape
    if t == 0:
        raise ValueError("no frames to encode")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_wav_path: Optional[Path] = None
    audio_path: Optional[Union[str, Path]] = None
    if isinstance(audio, AudioTrack):
        tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_wav.close()
        tmp_wav_path = Path(tmp_wav.name)
        _write_wav_pcm16(audio.waveform, audio.sample_rate, tmp_wav_path)
        audio_path = tmp_wav_path
    elif isinstance(audio, (str, Path)):
        if has_audio_stream(audio):
            audio_path = audio
        else:
            logger.info(
                f"[VIDEO_ENCODE] audio source {audio} has no audio stream -- encoding without audio"
            )
    elif audio is not None:
        raise TypeError(
            f"unsupported audio type {type(audio).__name__}; expected AudioTrack, str, Path, or None"
        )

    try:
        cmd = _build_ffmpeg_args(
            width=w, height=h, fps=fps, codec=codec, crf=crf, out_path=out_path, audio_path=audio_path,
        )

        logger.info(f"[VIDEO_ENCODE] Encoding {t} frames ({w}x{h} @ {fps}fps) -> {out_path} via {codec}"
                    + (f" with audio from {audio_path}" if audio_path is not None else ""))

        try:
            # Zero-copy stdin without `.tobytes()` (which would materialize a
            # second full-size copy of a multi-GB frame buffer) -- but a raw
            # ndarray is NOT a valid `input=`: subprocess truth-tests it
            # (`if not input:` raised "truth value of an array is ambiguous"
            # in production) and its byte-offset chunking would mis-slice a
            # multi-dimensional view. A 1-D uint8 memoryview satisfies both:
            # len() is well-defined and slicing is per-byte. reshape(-1) is a
            # view (no copy) on the already-contiguous array.
            flat = np.ascontiguousarray(arr).reshape(-1)
            result = subprocess.run(cmd, input=memoryview(flat), capture_output=True, timeout=600)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"ffmpeg timed out after 600s encoding {out_path}") from e

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg failed (exit {result.returncode}) encoding {out_path}: {stderr[-2000:]}")

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg reported success but produced no output at {out_path}")

        return out_path
    finally:
        if tmp_wav_path is not None:
            tmp_wav_path.unlink(missing_ok=True)
