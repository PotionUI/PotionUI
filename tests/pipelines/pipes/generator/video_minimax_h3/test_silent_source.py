from __future__ import annotations

import numpy as np
import pytest
import torch

from src.pipelines.pipes._shared.media.video_encode import AudioTrack
from src.pipelines.pipes.generator.video_minimax_h3 import main as h3_main
from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe, _MiniMaxH3Ctx
from src.platform.util.video_stream import AUDIO_PROBE_FAILED, AUDIO_PROBE_PRESENT, AUDIO_PROBE_SILENT, AudioProbeResult
from tests.pipelines.pipes.generator.video_minimax_h3.test_main import (
    _fake_bundle,
    _fake_conditioning,
    _pipe_input,
    _run_generate_one,
)
from tests.platform.media_tools.media_factory import ffmpeg_available, make_video

_MAIN = "src.pipelines.pipes.generator.video_minimax_h3.main"

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg")


def _probe_as(monkeypatch, status):
    monkeypatch.setattr(f"{_MAIN}.probe_audio_stream", lambda path: AudioProbeResult(status))


def _pipe(**config):
    return GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), **config})


def _context(pipe, **inputs):
    return pipe.build_context(_pipe_input(model=_fake_bundle(), conditioning=[_fake_conditioning(3)], **inputs)).extra


def _refine_overrides(extra):
    overrides = {
        "decode": False, "denoise": 0.5, "initial_latents": [torch.rand(1, 24, 2, 2, 2)],
        "audio_source": extra.audio_source, "audio_file": extra.audio_file,
    }
    if hasattr(extra, "audio_silent"):
        overrides["audio_silent"] = extra.audio_silent
    return overrides


def _forbid_decode(monkeypatch):
    def refuse(path):
        raise AssertionError(f"tried to decode audio out of {path!r}")
    monkeypatch.setattr(f"{_MAIN}._decode_waveform_via_ffmpeg", refuse)


def test_silent_passthrough_source_is_marked_silent_and_drops_the_track(monkeypatch):
    _probe_as(monkeypatch, AUDIO_PROBE_SILENT)
    extra = _context(_pipe(audio_source="passthrough"), audio=["clip.mp4"])
    assert extra.audio_silent is True
    assert extra.audio_file is None


def test_passthrough_source_with_audio_keeps_its_track(monkeypatch):
    _probe_as(monkeypatch, AUDIO_PROBE_PRESENT)
    extra = _context(_pipe(audio_source="passthrough"), audio=["clip.mp4"])
    assert extra.audio_silent is False
    assert extra.audio_file == "clip.mp4"


def test_unprobeable_source_is_not_assumed_silent(monkeypatch):
    _probe_as(monkeypatch, AUDIO_PROBE_FAILED)
    extra = _context(_pipe(audio_source="passthrough"), audio=["clip.mp4"])
    assert extra.audio_silent is False
    assert extra.audio_file == "clip.mp4"


def test_silent_media_check_covers_any_path_like_source(monkeypatch, tmp_path):
    _probe_as(monkeypatch, AUDIO_PROBE_SILENT)
    assert h3_main._is_silent_media("clip.mp4") is True
    assert h3_main._is_silent_media(tmp_path / "clip.mp4") is True
    assert h3_main._is_silent_media(None) is False


def test_an_in_memory_track_is_never_probed(monkeypatch):
    monkeypatch.setattr(f"{_MAIN}.probe_audio_stream", lambda path: pytest.fail("probed an in-memory track"))
    track = AudioTrack(waveform=np.zeros((2, 1600), dtype=np.float32), sample_rate=32000)
    extra = _context(_pipe(audio_source="passthrough"), audio=[track])
    assert extra.audio_silent is False
    assert extra.audio_file is track


def test_generated_audio_source_never_probes(monkeypatch):
    monkeypatch.setattr(f"{_MAIN}.probe_audio_stream", lambda path: pytest.fail("probed with nothing to probe"))
    assert _context(_pipe(audio_source="generate")).audio_silent is False


def test_generate_ignores_a_provided_audio_input(monkeypatch):
    monkeypatch.setattr(f"{_MAIN}.probe_audio_stream", lambda path: pytest.fail("probed with nothing to probe"))
    extra = _context(_pipe(audio_source="generate"), audio=["driving.mp4"])
    assert extra.audio_file is None
    assert extra.audio_silent is False


def test_generate_muxes_the_generated_track_not_the_provided_input(monkeypatch):
    generated = AudioTrack(waveform=np.ones((2, 8), dtype=np.float32), sample_rate=16000)
    monkeypatch.setattr(f"{_MAIN}.probe_audio_stream", lambda path: pytest.fail("probed with nothing to probe"))
    monkeypatch.setattr(f"{_MAIN}._place_vae", lambda ctx, vae: None)
    monkeypatch.setattr(f"{_MAIN}.decode_generated_audio", lambda *args, **kwargs: generated)
    pipe = _pipe(audio_source="generate")
    ctx = pipe.build_context(
        _pipe_input(model=_fake_bundle(), conditioning=[_fake_conditioning(3)], audio=["driving.mp4"])
    ).extra
    assert pipe._resolve_audio(ctx, torch.zeros(1)) is generated


def test_resolve_audio_of_a_silent_source_is_no_track():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    ctx = _MiniMaxH3Ctx(
        bundle=None, conditioning=[], steps=2, height=32, width=32, frames=5,
        num_latent_frames=2, latent_height=2, latent_width=2, num_audio_latents=3,
        device="cpu", dtype=torch.float32, spec=None, audio_source="passthrough", audio_silent=True,
    )
    assert pipe._resolve_audio(ctx, torch.zeros(1)) is None


def test_silent_refine_never_decodes_audio_and_outputs_no_track(monkeypatch):
    _forbid_decode(monkeypatch)
    pipe, _ = _run_generate_one({}, ctx_overrides={
        "decode": False, "denoise": 0.5, "initial_latents": [torch.rand(1, 24, 2, 2, 2)],
        "audio_source": "passthrough", "audio_file": None, "audio_silent": True,
    })
    assert pipe._audio_results == [None]
    assert pipe.build_output([pipe._last_result])["audio"] == [None]


def test_silent_refine_locks_on_silence_rather_than_noise():
    fixed_video = torch.rand(1, 24, 2, 2, 2)
    base = {"decode": False, "denoise": 0.5, "initial_latents": [fixed_video], "audio_source": "passthrough"}
    silent_a, _ = _run_generate_one({}, ctx_overrides={**base, "audio_file": None, "audio_silent": True})
    silent_b, _ = _run_generate_one({}, ctx_overrides={**base, "audio_file": None, "audio_silent": True})
    loud, _ = _run_generate_one({}, ctx_overrides={
        **base, "audio_file": AudioTrack(waveform=np.full((2, 1600), 0.5, dtype=np.float32), sample_rate=32000),
    })
    assert torch.allclose(silent_a._audio_latent_results[0], silent_b._audio_latent_results[0])
    assert not torch.allclose(silent_a._audio_latent_results[0], loud._audio_latent_results[0])


def test_refine_with_an_audio_track_still_passes_it_through():
    track = AudioTrack(waveform=np.full((2, 1600), 0.2, dtype=np.float32), sample_rate=32000)
    pipe, _ = _run_generate_one({}, ctx_overrides={
        "decode": False, "denoise": 0.5, "initial_latents": [torch.rand(1, 24, 2, 2, 2)],
        "audio_source": "passthrough", "audio_file": track,
    })
    assert pipe._audio_results == [track]


def test_silent_continue_window_drops_nothing_on_none():
    assert h3_main._trim_audio_head(None, 12) is None
    assert h3_main._trim_audio_to_frames(None, 12) is None
    assert h3_main._concat_audio_tracks([None, None]) is None


@needs_ffmpeg
def test_real_silent_clip_refines_without_decoding(tmp_path):
    clip = make_video(tmp_path / "silent.mp4", audio=False)
    extra = _context(_pipe(audio_source="passthrough"), audio=[str(clip)])
    pipe, _ = _run_generate_one({}, ctx_overrides=_refine_overrides(extra))
    assert extra.audio_silent is True
    assert pipe._audio_results == [None]


@needs_ffmpeg
def test_real_clip_with_audio_still_decodes_and_passes_through(tmp_path):
    clip = make_video(tmp_path / "sound.mp4", audio=True)
    extra = _context(_pipe(audio_source="passthrough"), audio=[str(clip)])
    pipe, _ = _run_generate_one({}, ctx_overrides=_refine_overrides(extra))
    assert extra.audio_silent is False
    assert pipe._audio_results == [str(clip)]
