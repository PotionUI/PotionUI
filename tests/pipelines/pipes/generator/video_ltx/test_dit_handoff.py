from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import torch

from src.pipelines.pipes.latent_upscaler.ltx import main as upscaler_main

from .test_video_ltx_generator import _MAIN, _bundle, _pipe, _pipe_input

_PLACEMENT = "src.pipelines.pipes._shared.generation.dit_placement"


class _TrackedDit:
    def __init__(self, log):
        self.log = log
        self.device = "cpu"
        self.compute_dtype = torch.float32
        self.estimated_vram_gb = 11.4
        self._streamer = None
        self.module = SimpleNamespace(config=SimpleNamespace(causal_temporal_positioning=True))

    def move_to(self, device):
        self.log.append(("move_to", str(device)))
        self.device = str(device)

    def offload(self):
        self.log.append(("offload",))
        self.device = "cpu"


def _on_gpu(dit, device):
    return str(getattr(dit, "device", "")).startswith("cuda")


def _two_stage_run(monkeypatch, *, beside=None):
    log = []
    bundle = _bundle()
    bundle.dit = _TrackedDit(log)
    beside_calls = []

    def fits(vae, latent, free_gb, beside_gb=0.0):
        beside_calls.append(beside_gb)
        return bool(beside)

    def restore(dit, device):
        log.append(("restore",))
        if not _on_gpu(dit, device):
            dit.move_to("cuda:0")

    monkeypatch.setattr(f"{_MAIN}.place_dit_for_sequence", lambda dit, device, **k: dit.move_to("cuda:0"))
    monkeypatch.setattr(f"{_MAIN}.guard_sampling_oom", lambda forward, **k: forward)
    monkeypatch.setattr(f"{_MAIN}.denoise_prenoised", lambda fwd, x, c, u, **k: x)
    monkeypatch.setattr(f"{_MAIN}.dit_is_fully_resident", _on_gpu)
    monkeypatch.setattr(f"{_PLACEMENT}.dit_is_fully_resident", _on_gpu)
    monkeypatch.setattr(f"{_MAIN}.effective_free_vram_gb", lambda device: 20.0)
    monkeypatch.setattr(f"{_MAIN}.decode_fits_beside", fits)
    monkeypatch.setattr(f"{_MAIN}.decode_ltx_video",
                        lambda vae, device, latent, seed, keep_resident=(): np.zeros((9, 64, 64, 3), np.uint8))
    monkeypatch.setattr(f"{_MAIN}.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
    monkeypatch.setattr(f"{_MAIN}.restore_dit_best_effort", restore)
    monkeypatch.setattr(upscaler_main, "get_residency_registry",
                        lambda: SimpleNamespace(offload_all=lambda *a, **k: None))
    monkeypatch.setattr(upscaler_main, "clear_gpu_memory", lambda: None)

    _pipe(resolution="64x64", frames=9, decode=False, preview=False).process(
        _pipe_input(bundle=bundle), lambda o: None)
    log.append(("stage1_done",))
    upscaler_main._free_room_for_upscale(bundle, "cuda")
    log.append(("upscaler_done",))
    stage2 = _pipe_input(bundle=bundle)
    stage2.input["initial_latent"] = [torch.zeros(1, 128, 2, 2, 2)]
    _pipe(resolution="64x64", frames=9, decode=True, preview=False, refine_sigmas="0.4, 0.0").process(
        stage2, lambda o: None)
    return log, beside_calls


def test_stage_one_hands_the_resident_dit_to_the_upscaler_without_a_restore_offload_round_trip(monkeypatch):
    log, _ = _two_stage_run(monkeypatch)
    assert log == [
        ("move_to", "cuda:0"),
        ("stage1_done",),
        ("offload",),
        ("upscaler_done",),
        ("move_to", "cuda:0"),
        ("offload",),
        ("restore",),
        ("move_to", "cuda:0"),
    ]


def test_the_decode_beside_check_is_told_how_much_the_kept_dit_would_free(monkeypatch):
    log, beside_calls = _two_stage_run(monkeypatch, beside=True)
    assert beside_calls == [11.4]
    assert log[-3:] == [("upscaler_done",), ("move_to", "cuda:0"), ("restore",)]


def _stage_one_audio_run(monkeypatch, audio_source):
    log = []
    bundle = _bundle(audio=True)
    bundle.dit = _TrackedDit(log)
    monkeypatch.setattr(f"{_MAIN}.place_dit_for_sequence", lambda dit, device, **k: dit.move_to("cuda:0"))
    monkeypatch.setattr(f"{_MAIN}.guard_sampling_oom", lambda forward, **k: forward)
    monkeypatch.setattr(f"{_MAIN}.denoise_prenoised", lambda fwd, x, c, u, **k: x)
    monkeypatch.setattr(f"{_PLACEMENT}.dit_is_fully_resident", _on_gpu)
    monkeypatch.setattr(f"{_MAIN}.decode_generated_audio", lambda *a: log.append(("audio_decode",)))
    pipe_input = _pipe_input(bundle=bundle)
    if audio_source == "file":
        pipe_input.input["audio"] = ["voice.wav"]
    _pipe(resolution="64x64", frames=9, decode=False, preview=False, audio=True, audio_source=audio_source).process(
        pipe_input, lambda o: None)
    return log


def test_a_handoff_that_generates_audio_offloads_the_dit_before_the_audio_decode(monkeypatch):
    assert _stage_one_audio_run(monkeypatch, "generate") == [
        ("move_to", "cuda:0"), ("offload",), ("audio_decode",),
    ]


def test_a_handoff_that_carries_a_file_track_keeps_the_dit_resident(monkeypatch):
    assert _stage_one_audio_run(monkeypatch, "file") == [("move_to", "cuda:0")]
