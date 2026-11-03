from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.video.tubes.detection import DetectorBuildResult
from src.pipelines.pipes._shared.video.tubes.tracking import Detection, Track
from src.pipelines.pipes._shared.video.tubes.windowing import TubeWindow
from src.pipelines.pipes.detailer.video_minimax_h3.main import DetailerVideoMinimaxH3Pipe

_MOD = "src.pipelines.pipes.detailer.video_minimax_h3.main"
_SHARED = "src.pipelines.pipes._shared.video.tubes.detailer"


class _Recording:
    def __init__(self, device="cpu"):
        self.offloaded = 0
        self.device = device

    def offload(self):
        self.offloaded += 1
        self.device = "cpu"


def _bundle(family="minimax_h3"):
    return SimpleNamespace(spec=SimpleNamespace(family=family), dit=_Recording(),
                           video_vae=_Recording(), audio_vae=_Recording())


def _pipe(**over):
    cfg = DetailerVideoMinimaxH3Pipe.get_default_config()
    cfg.update({"device": "cpu"})
    cfg.update(over)
    return DetailerVideoMinimaxH3Pipe(config=cfg)


def _pil_clip(n=12, h=64, w=64, value=100):
    return [Image.fromarray(np.full((h, w, 3), value, np.uint8)) for _ in range(n)]


def _one_face_track():
    return [Track([Detection(0, (10, 10, 30, 30), "face"),
                   Detection(6, (10, 10, 30, 30), "face")], "face")]


def test_config_defaults_have_specs():
    spec_names = {s.name for s in DetailerVideoMinimaxH3Pipe.configuration()}
    assert set(DetailerVideoMinimaxH3Pipe.get_default_config()) <= spec_names
    assert {"strength", "detect_faces", "detect_hands", "refine_steps"} <= spec_names


def test_rejects_non_h3_model():
    with pytest.raises(ValueError, match="not a MiniMax-H3"):
        _pipe().process(
            PipeInput(input={"model": _bundle(family="ltx"), "video": ["x.mp4"], "conditioning": [object()]}),
            lambda o: None)


def test_working_resolution_lands_on_the_h3_canvas():
    window = TubeWindow("hand", 0, 0, 300, 40, [(0, 0, 300, 40)], moving=False)
    tw, th = _pipe().working_resolution(window)
    assert tw % 32 == 0 and th % 32 == 0
    assert 0.25 <= tw / th <= 4.0


def test_free_room_keeps_the_h3_models_out_of_the_eviction_sweep():
    bundle = _bundle()
    bundle.dit = _Recording(device="cuda")
    calls = []
    registry = SimpleNamespace(offload_all=lambda device, exclude=(): calls.append((device, tuple(exclude))))
    with patch(f"{_SHARED}.get_residency_registry", return_value=registry), patch(f"{_SHARED}.clear_gpu_memory"):
        _pipe().free_room(bundle, "cuda")
    assert bundle.dit.offloaded == 1
    assert calls == [("cuda", (bundle.video_vae, bundle.audio_vae, bundle.dit))]


def test_the_cancel_probe_reaches_the_h3_refine_of_each_tube(monkeypatch):
    monkeypatch.setattr(f"{_SHARED}.read_video_frames", lambda p: (_pil_clip(value=100), 24.0))
    monkeypatch.setattr(f"{_SHARED}.build_frame_detectors",
                        lambda *a, **k: DetectorBuildResult(detectors=[object()], missing=[]))
    monkeypatch.setattr(f"{_SHARED}.detect_tracks", lambda *a, **k: _one_face_track())
    monkeypatch.setattr(f"{_SHARED}.encode_frames_to_mp4", lambda *a, **k: None)
    seen = {}

    def fake_refine(bundle, cond_model, pixels, clip, **kw):
        seen["is_cancelled"] = kw["is_cancelled"]
        n, th, tw = pixels.shape[2], pixels.shape[3], pixels.shape[4]
        return np.full((n, th, tw, 3), 200, np.uint8)

    monkeypatch.setattr(f"{_MOD}.refine_tube_pixels", fake_refine)
    probe = lambda: False
    _pipe(color_correction="none").process(
        PipeInput(input={"model": _bundle(), "video": ["/src/clip.mp4"], "conditioning": ["c"], "seed": [5]}),
        lambda o: None, is_cancelled=probe)
    assert seen["is_cancelled"] is probe


def test_full_flow_refines_with_the_h3_refiner_and_releases_every_model(monkeypatch):
    monkeypatch.setattr(f"{_SHARED}.read_video_frames", lambda p: (_pil_clip(value=100), 24.0))
    monkeypatch.setattr(f"{_SHARED}.build_frame_detectors",
                        lambda *a, **k: DetectorBuildResult(detectors=[object()], missing=[]))
    monkeypatch.setattr(f"{_SHARED}.detect_tracks", lambda *a, **k: _one_face_track())
    seen = {}

    def fake_refine(bundle, cond_model, pixels, clip, **kw):
        seen["clip"] = clip
        seen["kw"] = kw
        n, th, tw = pixels.shape[2], pixels.shape[3], pixels.shape[4]
        return np.full((n, th, tw, 3), 200, np.uint8)

    monkeypatch.setattr(f"{_MOD}.refine_tube_pixels", fake_refine)
    encoded = {}

    def fake_encode(frames, out_path, fps, audio=None):
        encoded["frames"] = frames.copy()
        encoded["audio"] = audio
        Path(out_path).write_bytes(b"mp4")
        return Path(out_path)

    monkeypatch.setattr(f"{_SHARED}.encode_frames_to_mp4", fake_encode)
    bundle = _bundle()
    out = _pipe(color_correction="none", strength="strong", refine_steps=8).process(
        PipeInput(input={"model": bundle, "video": ["/src/clip.mp4"], "conditioning": ["c"], "seed": [5]}),
        lambda o: None)

    assert out.output["video"][0] != "/src/clip.mp4"
    assert encoded["audio"] == "/src/clip.mp4"
    assert int(encoded["frames"][3, 20, 20, 0]) > 100
    assert int(encoded["frames"][3, 60, 60, 0]) == 100
    assert seen["clip"].source == "/src/clip.mp4" and seen["clip"].fps == 24.0
    assert seen["clip"].start_frame == 0 and seen["clip"].num_frames == 7
    assert seen["kw"]["strength"] == "strong" and seen["kw"]["steps"] == 8 and seen["kw"]["seed"] == 5
    assert bundle.dit.offloaded == 1 and bundle.video_vae.offloaded == 1 and bundle.audio_vae.offloaded == 1
