"""Tests for the audio_trim pipe: the `(start, duration)` window contract
(pinned via `compute_trim_window`, so the arithmetic is checkable without a
real audio file), clamping to the source's real length, the empty-window
degenerate case, mono/stereo channel preservation, and that the emitted
`AudioGenerationOutput` metadata matches what was ACTUALLY written to disk -
not just what the pipe claims.
"""

import soundfile as sf

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.outputs import AudioGenerationOutput
from src.pipelines.pipes.audio_trim.main import AudioTrimPipe, compute_trim_window
from tests.fixtures.audio_fixtures import build_minimal_wav


def _write_wav(tmp_path, name="source.wav", **kwargs):
    path = tmp_path / name
    path.write_bytes(build_minimal_wav(**kwargs))
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
