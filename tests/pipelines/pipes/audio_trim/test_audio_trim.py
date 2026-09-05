"""Tests for the audio_trim pipe: the `(start, duration)` window contract
(pinned via `compute_trim_window`, so the arithmetic is checkable without a
real audio file), clamping to the source's real length, the empty-window
degenerate case, mono/stereo channel preservation, that the emitted
`AudioGenerationOutput` metadata matches what was ACTUALLY written to disk -
not just what the pipe claims - and that the sample *precision* of the
source survives the trim (`resolve_trim_io_policy`'s read-dtype/write-subtype
table), not just the frame count.
"""

import os

import numpy as np
import soundfile as sf

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.outputs import AudioGenerationOutput
from src.pipelines.pipes.audio_trim import main as audio_trim_main
from src.pipelines.pipes.audio_trim.main import (
    AudioTrimPipe,
    _TRIM_BLOCK_FRAMES,
    compute_trim_window,
    resolve_trim_io_policy,
)
from tests.fixtures.audio_fixtures import build_minimal_wav


def _write_wav(tmp_path, name="source.wav", **kwargs):
    path = tmp_path / name
    path.write_bytes(build_minimal_wav(**kwargs))
    return str(path)


def _write_precise_wav(tmp_path, data, sample_rate, subtype, name="precise_source.wav"):
    """Write `data` (float64/float32 in [-1, 1], mono 1-D or stereo 2-D) as a
    real `.wav` with an explicit `subtype`, so the source SoundFile really
    reports that subtype - not a stand-in for it."""
    path = tmp_path / name
    sf.write(str(path), data, sample_rate, subtype=subtype)
    return str(path)


def _pipe(**config_over):
    cfg = AudioTrimPipe.get_default_config()
    cfg.update(config_over)
    return AudioTrimPipe(cfg)


# -- compute_trim_window: pinned (start, duration) -> (start_frame, end_frame) ----

def test_normal_window_worked_example():
    # 10s @ 1000Hz = 10000 frames. start=2s, duration=3s -> [2000, 5000).
    start_frame, end_frame = compute_trim_window(2.0, 3.0, sample_rate=1000, total_frames=10000)
    assert (start_frame, end_frame) == (2000, 5000)


def test_window_longer_than_file_is_clamped():
    # start=1s, duration=100s on a 10s file -> clamped to [1000, 10000).
    start_frame, end_frame = compute_trim_window(1.0, 100.0, sample_rate=1000, total_frames=10000)
    assert (start_frame, end_frame) == (1000, 10000)


def test_start_beyond_end_yields_empty_window():
    start_frame, end_frame = compute_trim_window(50.0, 5.0, sample_rate=1000, total_frames=10000)
    assert start_frame == 10000
    assert end_frame == 10000  # never negative span


def test_start_exactly_at_end_yields_empty_window():
    start_frame, end_frame = compute_trim_window(10.0, 5.0, sample_rate=1000, total_frames=10000)
    assert (start_frame, end_frame) == (10000, 10000)


def test_zero_duration_yields_empty_window():
    start_frame, end_frame = compute_trim_window(2.0, 0.0, sample_rate=1000, total_frames=10000)
    assert (start_frame, end_frame) == (2000, 2000)


def test_negative_duration_is_clamped_like_zero():
    start_frame, end_frame = compute_trim_window(2.0, -5.0, sample_rate=1000, total_frames=10000)
    assert (start_frame, end_frame) == (2000, 2000)


def test_negative_start_is_clamped_to_zero():
    start_frame, end_frame = compute_trim_window(-3.0, 2.0, sample_rate=1000, total_frames=10000)
    assert (start_frame, end_frame) == (0, 2000)


def test_bite_check_off_by_one_on_frame_offset():
    """Bite-check: an off-by-one on the frame offset (e.g. `start_seconds *
    sample_rate + 1`) must make this fail. Also documents that the window is
    a plain `round()`, not `int()`/floor - 2.5s at 1000Hz is frame 2500
    exactly here, so use a value where floor and round diverge."""
    # 1.2345s @ 1000Hz -> 1234.5 frames -> round() = 1234 (banker's rounding
    # rounds .5 to even; 1234 is even) or 1236 for a naive int()+1 bug either
    # way this pins the exact value round() gives.
    start_frame, _ = compute_trim_window(1.2345, 1.0, sample_rate=1000, total_frames=100000)
    assert start_frame == round(1.2345 * 1000)


def test_bite_check_clamp_uses_total_frames_not_start_plus_requested():
    """Bite-check: a broken clamp that returns `start_frame + requested_frames`
    unconditionally (ignoring `total_frames`) would return an end_frame past
    the file - this pins that it never exceeds total_frames."""
    start_frame, end_frame = compute_trim_window(9.0, 5.0, sample_rate=1000, total_frames=10000)
    assert end_frame == 10000
    assert end_frame <= 10000


# -- end-to-end through the pipe: assert the ACTUAL decoded output --------------

def test_normal_trim_end_to_end(tmp_path):
    source = _write_wav(tmp_path, duration_seconds=10.0, sample_rate=8000, channels=1)
    pipe = _pipe(start_seconds=2.0, duration_seconds=3.0)
    emitted = []
    result = pipe.process(PipeInput(input={"audio": [source]}), emitted.append)

    out_path = result.output["audio"][0]
    info = sf.info(out_path)
    assert info.frames == 3.0 * 8000
    assert info.samplerate == 8000
    assert info.channels == 1

    audio_outputs = [o for o in emitted if isinstance(o, AudioGenerationOutput)]
    assert len(audio_outputs) == 1
    assert audio_outputs[0].duration == 3.0
    assert audio_outputs[0].sample_rate == 8000
    assert audio_outputs[0].channels == 1
    assert audio_outputs[0].temporary is False


def test_window_longer_than_file_clamps_end_to_end(tmp_path):
    source = _write_wav(tmp_path, duration_seconds=5.0, sample_rate=8000, channels=1)
    pipe = _pipe(start_seconds=1.0, duration_seconds=100.0)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)

    out_path = result.output["audio"][0]
    info = sf.info(out_path)
    # Kept exactly the remaining 4s, not the full 100s requested nor the
    # original 5s (which would mean the start offset was dropped).
    assert info.frames == 4.0 * 8000


def test_start_beyond_end_produces_empty_but_valid_file(tmp_path):
    source = _write_wav(tmp_path, duration_seconds=2.0, sample_rate=8000, channels=1)
    pipe = _pipe(start_seconds=50.0, duration_seconds=5.0)
    emitted = []
    result = pipe.process(PipeInput(input={"audio": [source]}), emitted.append)

    out_path = result.output["audio"][0]
    info = sf.info(out_path)
    assert info.frames == 0
    assert info.samplerate == 8000  # sample rate still preserved, even empty

    audio_outputs = [o for o in emitted if isinstance(o, AudioGenerationOutput)]
    assert audio_outputs[0].duration == 0.0


def test_zero_duration_produces_empty_but_valid_file(tmp_path):
    source = _write_wav(tmp_path, duration_seconds=5.0, sample_rate=8000, channels=1)
    pipe = _pipe(start_seconds=1.0, duration_seconds=0.0)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)

    out_path = result.output["audio"][0]
    info = sf.info(out_path)
    assert info.frames == 0


def test_negative_duration_produces_empty_but_valid_file(tmp_path):
    source = _write_wav(tmp_path, duration_seconds=5.0, sample_rate=8000, channels=1)
    pipe = _pipe(start_seconds=1.0, duration_seconds=-2.0)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)

    out_path = result.output["audio"][0]
    assert sf.info(out_path).frames == 0


def test_stereo_channel_count_is_preserved(tmp_path):
    source = _write_wav(tmp_path, duration_seconds=6.0, sample_rate=8000, channels=2)
    pipe = _pipe(start_seconds=1.0, duration_seconds=2.0)
    emitted = []
    result = pipe.process(PipeInput(input={"audio": [source]}), emitted.append)

    out_path = result.output["audio"][0]
    info = sf.info(out_path)
    assert info.channels == 2
    assert info.frames == 2.0 * 8000

    audio_outputs = [o for o in emitted if isinstance(o, AudioGenerationOutput)]
    assert audio_outputs[0].channels == 2


def test_mono_channel_count_is_preserved(tmp_path):
    source = _write_wav(tmp_path, duration_seconds=4.0, sample_rate=8000, channels=1)
    pipe = _pipe(start_seconds=0.5, duration_seconds=1.5)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)

    out_path = result.output["audio"][0]
    assert sf.info(out_path).channels == 1


def test_sample_rate_is_preserved_when_not_the_default(tmp_path):
    source = _write_wav(tmp_path, duration_seconds=3.0, sample_rate=44100, channels=2)
    pipe = _pipe(start_seconds=0.0, duration_seconds=1.0)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)

    out_path = result.output["audio"][0]
    info = sf.info(out_path)
    assert info.samplerate == 44100
    assert info.frames == 44100  # exactly 1s at 44.1kHz


# -- array contract: process EVERY file, never just the first -----------------

def test_audio_io_is_declared_as_an_array():
    ins = {s.name: s for s in AudioTrimPipe.inputs()}
    outs = {s.name: s for s in AudioTrimPipe.outputs()}
    assert ins["audio"].is_array is True
    assert outs["audio"].is_array is True


def test_two_file_list_returns_two_trimmed_files_bite_check(tmp_path):
    """Bite-check: reverting to unwrapping `audio[0]` and returning a bare
    `PipeOutput(output={"audio": result})` makes this go red (a one-element
    result / a crash iterating a bare path downstream)."""
    source_a = _write_wav(tmp_path, name="a.wav", duration_seconds=10.0, sample_rate=8000, channels=1)
    source_b = _write_wav(tmp_path, name="b.wav", duration_seconds=6.0, sample_rate=8000, channels=2)
    pipe = _pipe(start_seconds=1.0, duration_seconds=2.0)

    result = pipe.process(PipeInput(input={"audio": [source_a, source_b]}), lambda o: None)

    paths = result.output["audio"]
    assert isinstance(paths, list) and len(paths) == 2
    info_a, info_b = sf.info(paths[0]), sf.info(paths[1])
    assert info_a.channels == 1
    assert info_b.channels == 2
    assert info_a.frames == info_b.frames == 2.0 * 8000


def test_bare_string_input_is_still_accepted_and_wrapped(tmp_path):
    source = _write_wav(tmp_path, duration_seconds=5.0, sample_rate=8000, channels=1)
    pipe = _pipe(start_seconds=0.0, duration_seconds=1.0)
    result = pipe.process(PipeInput(input={"audio": source}), lambda o: None)
    assert isinstance(result.output["audio"], list)
    assert len(result.output["audio"]) == 1


# -- guards --------------------------------------------------------------------

def test_missing_audio_raises():
    pipe = _pipe()
    try:
        pipe.process(PipeInput(input={}), lambda o: None)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_empty_audio_list_raises():
    pipe = _pipe()
    try:
        pipe.process(PipeInput(input={"audio": []}), lambda o: None)
        assert False, "expected ValueError"
    except ValueError:
        pass


# -- config / IO contract -------------------------------------------------------

def test_name_and_contract():
    assert AudioTrimPipe.name == "audio_trim"
    ins = {s.name: s.io_type for s in AudioTrimPipe.inputs()}
    outs = {s.name: s.io_type for s in AudioTrimPipe.outputs()}
    assert ins == {"audio": IOType.AUDIO}
    assert outs == {"audio": IOType.AUDIO}


def test_config_spec_matches_contract():
    specs = {s.name: s for s in AudioTrimPipe.configuration()}
    assert set(specs) == {"start_seconds", "duration_seconds"}
    assert specs["start_seconds"].default == 0.0
    assert specs["duration_seconds"].default == 10.0
    assert specs["start_seconds"].min_value == 0.0
    assert specs["duration_seconds"].min_value == 0.0


# -- resolve_trim_io_policy: pinned subtype -> (read_dtype, write_subtype) ------

def test_policy_pcm16_passthrough():
    assert resolve_trim_io_policy("PCM_16") == ("int16", "PCM_16")


def test_policy_pcm24_widens_read_keeps_write_subtype():
    assert resolve_trim_io_policy("PCM_24") == ("int32", "PCM_24")


def test_policy_pcm32():
    assert resolve_trim_io_policy("PCM_32") == ("int32", "PCM_32")


def test_policy_float():
    assert resolve_trim_io_policy("FLOAT") == ("float32", "FLOAT")


def test_policy_double():
    assert resolve_trim_io_policy("DOUBLE") == ("float64", "DOUBLE")


def test_policy_pcm_u8_widens_to_pcm16():
    assert resolve_trim_io_policy("PCM_U8") == ("int16", "PCM_16")


def test_policy_pcm_s8_widens_to_pcm16():
    assert resolve_trim_io_policy("PCM_S8") == ("int16", "PCM_16")


def test_policy_unknown_subtype_falls_back_to_float_not_silent_pcm16():
    """Bite-check: an unrecognised (compressed/companded) source subtype must
    not silently fall back to the old PCM_16 default - it gets the documented
    FLOAT fallback instead."""
    read_dtype, write_subtype = resolve_trim_io_policy("VORBIS")
    assert (read_dtype, write_subtype) == ("float32", "FLOAT")
    assert write_subtype != "PCM_16"


def test_policy_falls_back_to_float_when_chosen_subtype_unsupported_for_wav(monkeypatch):
    """Even a subtype the table knows about (PCM_24) must fall back to FLOAT
    if this libsndfile build can't write that subtype into a `.wav`
    container - simulated here via a monkeypatched `check_format`."""
    real_check_format = audio_trim_main.sf.check_format

    def fake_check_format(fmt, subtype=None, endian=None):
        if subtype == "PCM_24":
            return False
        return real_check_format(fmt, subtype, endian)

    monkeypatch.setattr(audio_trim_main.sf, "check_format", fake_check_format)
    assert resolve_trim_io_policy("PCM_24") == ("float32", "FLOAT")


# -- end-to-end: sample precision survives the trim, not just the frame count --

def test_pcm24_low_amplitude_samples_are_bit_identical_to_source_window(tmp_path):
    """Bite-check: at this amplitude (2e-5), 16-bit PCM's resolution
    (~3.05e-5) can't represent the signal at all - a `subtype`-less
    `sf.write` (the old behaviour) quantizes every sample to 0. This must
    fail red against that code and pass once the trim preserves PCM_24."""
    sample_rate = 8000
    t = np.arange(int(2.0 * sample_rate)) / sample_rate
    full = (2e-5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    source = _write_precise_wav(tmp_path, full, sample_rate, subtype="PCM_24")

    pipe = _pipe(start_seconds=0.5, duration_seconds=1.0)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    out_path = result.output["audio"][0]

    assert sf.info(out_path).subtype == "PCM_24"

    start_frame, end_frame = compute_trim_window(0.5, 1.0, sample_rate, len(full))
    with sf.SoundFile(source) as src:
        src.seek(start_frame)
        expected = src.read(frames=end_frame - start_frame, dtype="int32", always_2d=False)
    with sf.SoundFile(out_path) as out:
        actual = out.read(dtype="int32", always_2d=False)

    assert np.array_equal(actual, expected)
    # And it is not the all-zero quantization a PCM_16 write would produce.
    assert np.any(actual != 0)


def test_pcm32_samples_are_bit_identical_to_source_window(tmp_path):
    sample_rate = 8000
    t = np.arange(int(1.0 * sample_rate)) / sample_rate
    full = (2e-5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    source = _write_precise_wav(tmp_path, full, sample_rate, subtype="PCM_32")

    pipe = _pipe(start_seconds=0.2, duration_seconds=0.5)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    out_path = result.output["audio"][0]

    assert sf.info(out_path).subtype == "PCM_32"

    start_frame, end_frame = compute_trim_window(0.2, 0.5, sample_rate, len(full))
    with sf.SoundFile(source) as src:
        src.seek(start_frame)
        expected = src.read(frames=end_frame - start_frame, dtype="int32", always_2d=False)
    with sf.SoundFile(out_path) as out:
        actual = out.read(dtype="int32", always_2d=False)

    assert np.array_equal(actual, expected)


def test_float_samples_are_bit_identical_to_source_window(tmp_path):
    sample_rate = 8000
    t = np.arange(int(1.0 * sample_rate)) / sample_rate
    full = (2e-5 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    source = _write_precise_wav(tmp_path, full, sample_rate, subtype="FLOAT")

    pipe = _pipe(start_seconds=0.1, duration_seconds=0.4)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    out_path = result.output["audio"][0]

    assert sf.info(out_path).subtype == "FLOAT"

    start_frame, end_frame = compute_trim_window(0.1, 0.4, sample_rate, len(full))
    expected = full[start_frame:end_frame]
    with sf.SoundFile(out_path) as out:
        actual = out.read(dtype="float32", always_2d=False)

    assert np.array_equal(actual, expected)


def test_double_samples_are_bit_identical_to_source_window(tmp_path):
    sample_rate = 8000
    t = np.arange(int(1.0 * sample_rate)) / sample_rate
    full = (2e-5 * np.sin(2 * np.pi * 550 * t)).astype(np.float64)
    source = _write_precise_wav(tmp_path, full, sample_rate, subtype="DOUBLE")

    pipe = _pipe(start_seconds=0.3, duration_seconds=0.2)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    out_path = result.output["audio"][0]

    assert sf.info(out_path).subtype == "DOUBLE"

    start_frame, end_frame = compute_trim_window(0.3, 0.2, sample_rate, len(full))
    expected = full[start_frame:end_frame]
    with sf.SoundFile(out_path) as out:
        actual = out.read(dtype="float64", always_2d=False)

    assert np.array_equal(actual, expected)


def test_pcm16_source_remains_pcm16_after_trim(tmp_path):
    sample_rate = 8000
    t = np.arange(int(1.0 * sample_rate)) / sample_rate
    full = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    source = _write_precise_wav(tmp_path, full, sample_rate, subtype="PCM_16")

    pipe = _pipe(start_seconds=0.1, duration_seconds=0.3)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    out_path = result.output["audio"][0]

    assert sf.info(out_path).subtype == "PCM_16"


def test_stereo_high_precision_subtype_preserves_both_channels_exactly(tmp_path):
    sample_rate = 8000
    t = np.arange(int(1.0 * sample_rate)) / sample_rate
    left = (2e-5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    right = (2e-5 * np.sin(2 * np.pi * 660 * t)).astype(np.float32)
    full = np.stack([left, right], axis=1)
    source = _write_precise_wav(tmp_path, full, sample_rate, subtype="PCM_24")

    pipe = _pipe(start_seconds=0.25, duration_seconds=0.5)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    out_path = result.output["audio"][0]

    info = sf.info(out_path)
    assert info.subtype == "PCM_24"
    assert info.channels == 2

    start_frame, end_frame = compute_trim_window(0.25, 0.5, sample_rate, len(full))
    with sf.SoundFile(source) as src:
        src.seek(start_frame)
        expected = src.read(frames=end_frame - start_frame, dtype="int32", always_2d=True)
    with sf.SoundFile(out_path) as out:
        actual = out.read(dtype="int32", always_2d=True)

    assert np.array_equal(actual, expected)


def test_window_boundary_is_exact_against_a_direct_source_slice(tmp_path):
    """Every sample of the trimmed output must equal the direct numpy slice
    of the source array at [start_frame:end_frame] - not an off-by-one
    window, not a resampled/interpolated approximation."""
    sample_rate = 1000
    full = np.linspace(-2e-5, 2e-5, num=3000, dtype=np.float32)
    source = _write_precise_wav(tmp_path, full, sample_rate, subtype="FLOAT")

    pipe = _pipe(start_seconds=0.777, duration_seconds=1.111)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    out_path = result.output["audio"][0]

    start_frame, end_frame = compute_trim_window(0.777, 1.111, sample_rate, len(full))
    expected = full[start_frame:end_frame]
    with sf.SoundFile(out_path) as out:
        actual = out.read(dtype="float32", always_2d=False)

    assert len(actual) == end_frame - start_frame
    assert np.array_equal(actual, expected)


def test_unsupported_wav_subtype_source_falls_back_to_float_end_to_end(tmp_path, monkeypatch):
    """A source subtype the policy table doesn't know (a companded codec,
    here simulated with a synthetic subtype string) must produce a FLOAT
    `.wav`, never crash and never silently downgrade to PCM_16."""
    sample_rate = 8000
    t = np.arange(int(0.5 * sample_rate)) / sample_rate
    full = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    source = _write_precise_wav(tmp_path, full, sample_rate, subtype="PCM_16")

    real_sound_file = audio_trim_main.sf.SoundFile

    class FakeSubtypeSoundFile:
        """Wraps a real, *read-mode* SoundFile but reports a synthetic,
        unknown subtype - the source file itself stays perfectly real and
        readable. `sf.write`'s own internal `SoundFile(..., 'w', ...)` calls
        (positional/keyword args beyond a bare path) pass straight through
        to the real class so the write path under test is untouched."""

        def __new__(cls, path, *args, **kwargs):
            if args or kwargs:
                return real_sound_file(path, *args, **kwargs)
            return super().__new__(cls)

        def __init__(self, path):
            self._inner = real_sound_file(path)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, item):
            return getattr(self._inner, item)

        def __len__(self):
            return len(self._inner)

        @property
        def subtype(self):
            return "SYNTHETIC_CODEC"

    monkeypatch.setattr(audio_trim_main.sf, "SoundFile", FakeSubtypeSoundFile)

    pipe = _pipe(start_seconds=0.1, duration_seconds=0.2)
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    out_path = result.output["audio"][0]
    monkeypatch.undo()  # inspect the real output file, not through the fake

    assert sf.info(out_path).subtype == "FLOAT"


# -- streaming copy: bounded block size, chunk-boundary parity, failure cleanup -

class _RecordingFakeSource:
    """Declares a huge (default 50M-frame) window without ever holding that
    much data: each `read()` synthesizes only the requested block's worth of
    zero samples on demand and records the size actually requested, so a
    test can pin the max read size against `_TRIM_BLOCK_FRAMES` without a
    huge file or a long benchmark."""

    def __init__(self, samplerate=8000, channels=2, subtype="PCM_16", total_frames=50_000_000):
        self.samplerate = samplerate
        self.channels = channels
        self.subtype = subtype
        self._total_frames = total_frames
        self._pos = 0
        self.read_sizes: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __len__(self):
        return self._total_frames

    def seek(self, frame):
        self._pos = frame

    def read(self, frames, dtype, always_2d=False):
        n = min(frames, self._total_frames - self._pos)
        self.read_sizes.append(n)
        self._pos += n
        shape = (n, self.channels) if (always_2d or self.channels > 1) else (n,)
        return np.zeros(shape, dtype=dtype)


class _RecordingFakeDest:
    """Records each `write()` call's frame count instead of touching disk,
    so the block-bound test stays fast and small even for a 50M-frame
    window."""

    def __init__(self):
        self.write_sizes: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def write(self, data):
        self.write_sizes.append(len(data))


def test_max_read_size_is_bounded_by_the_block_constant_on_a_huge_window(monkeypatch):
    """Bite-check: a single unbounded `read(frames=frame_count, ...)` (the
    old implementation) would request all 50,000,000 frames in one call.
    This asserts every read the pipe issues is capped at `_TRIM_BLOCK_FRAMES`,
    and that the destination never receives more than one block's worth of
    data at a time - the live payload never exceeds one block regardless of
    how long the requested window is."""
    total_frames = 50_000_000
    source = _RecordingFakeSource(samplerate=8000, channels=2, subtype="PCM_16", total_frames=total_frames)
    dest = _RecordingFakeDest()

    def fake_sound_file(path, *args, **kwargs):
        if kwargs.get("mode") == "w":
            return dest
        return source

    monkeypatch.setattr(audio_trim_main.sf, "SoundFile", fake_sound_file)

    pipe = _pipe(start_seconds=0.0, duration_seconds=total_frames / source.samplerate)
    pipe.process(PipeInput(input={"audio": ["fake-source.wav"]}), lambda o: None)

    assert source.read_sizes, "expected at least one chunked read"
    assert max(source.read_sizes) == _TRIM_BLOCK_FRAMES
    assert all(n <= _TRIM_BLOCK_FRAMES for n in source.read_sizes)
    assert sum(source.read_sizes) == total_frames
    # The destination is fed exactly what was read, one block at a time -
    # never the whole window buffered and written in a single call.
    assert dest.write_sizes == source.read_sizes
    assert max(dest.write_sizes) == _TRIM_BLOCK_FRAMES


def test_chunked_copy_matches_single_read_across_block_boundaries_mono(tmp_path, monkeypatch):
    """With an artificially tiny block size, the window is copied across many
    block boundaries - the chunked read/write must still reassemble
    byte-for-byte the same window a single `read()` would produce."""
    monkeypatch.setattr(audio_trim_main, "_TRIM_BLOCK_FRAMES", 3)
    sample_rate = 1000
    full = np.linspace(-0.5, 0.5, num=500, dtype=np.float32)
    source = _write_precise_wav(tmp_path, full, sample_rate, subtype="FLOAT", name="mono.wav")

    pipe = _pipe(start_seconds=0.037, duration_seconds=0.281)  # window not a multiple of the block size
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    out_path = result.output["audio"][0]

    start_frame, end_frame = compute_trim_window(0.037, 0.281, sample_rate, len(full))
    expected = full[start_frame:end_frame]
    with sf.SoundFile(out_path) as out:
        actual = out.read(dtype="float32", always_2d=False)

    assert len(actual) == len(expected) == (end_frame - start_frame)
    assert np.array_equal(actual, expected)


def test_chunked_copy_matches_single_read_across_block_boundaries_stereo(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_trim_main, "_TRIM_BLOCK_FRAMES", 4)
    sample_rate = 1000
    left = np.linspace(-0.5, 0.5, num=500, dtype=np.float32)
    right = np.linspace(0.5, -0.5, num=500, dtype=np.float32)
    full = np.stack([left, right], axis=1)
    source = _write_precise_wav(tmp_path, full, sample_rate, subtype="FLOAT", name="stereo.wav")

    pipe = _pipe(start_seconds=0.043, duration_seconds=0.297)  # window not a multiple of the block size
    result = pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    out_path = result.output["audio"][0]

    start_frame, end_frame = compute_trim_window(0.043, 0.297, sample_rate, len(full))
    expected = full[start_frame:end_frame]
    with sf.SoundFile(out_path) as out:
        actual = out.read(dtype="float32", always_2d=True)

    assert actual.shape == expected.shape
    assert np.array_equal(actual, expected)


def test_incomplete_output_removed_on_write_failure(tmp_path, monkeypatch):
    """A failure partway through the streamed copy must not leave a partial
    `.wav` on disk - only a fully-copied output is ever published, matching
    the pipeline's existing ownership contract for a trim's temp file."""
    source = _write_wav(tmp_path, duration_seconds=5.0, sample_rate=8000, channels=1)
    pipe = _pipe(start_seconds=0.0, duration_seconds=2.0)

    real_sound_file = audio_trim_main.sf.SoundFile
    captured: dict = {}

    class FailingWriteSoundFile:
        def __new__(cls, path, *args, **kwargs):
            inner = real_sound_file(path, *args, **kwargs)
            if kwargs.get("mode") == "w":
                captured["out_path"] = path

                def failing_write(data, *a, **kw):
                    raise IOError("simulated write failure")

                inner.write = failing_write
            return inner

    monkeypatch.setattr(audio_trim_main.sf, "SoundFile", FailingWriteSoundFile)

    raised = False
    try:
        pipe.process(PipeInput(input={"audio": [source]}), lambda o: None)
    except IOError:
        raised = True
    assert raised, "expected the simulated write failure to propagate"

    assert "out_path" in captured
    assert not os.path.exists(captured["out_path"])
