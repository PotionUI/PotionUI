"""Pipe-contract tests for detailer/video_ltx.

The pipe's orchestration -- read -> detect -> per-track refine+composite ->
re-encode -- with the detector, the model refine, the frame reader and the mp4
encoder all stubbed at the module boundary. No mediapipe, no model, no GPU, no
cv2, no ffmpeg.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.outputs import CompareImagesGenerationOutput
from src.pipelines.pipes.detailer.video_ltx.detection import DetectorBuildResult
from src.pipelines.pipes.detailer.video_ltx.main import DetailerVideoLtxPipe, _free_room_for_tube_refine
from src.pipelines.pipes.detailer.video_ltx.tracking import Detection, Track

_MOD = "src.pipelines.pipes.detailer.video_ltx.main"


def _available_build_result(*a, **k):
    """Stand-in for `build_frame_detectors` -- both models present, one stub
    detector each. `detect_tracks` is monkeypatched separately in these tests,
    so the detector objects themselves are never actually run."""
    return DetectorBuildResult(detectors=[object(), object()], missing=[])


class _Recording:
    """Stand-in for a bundle's `dit`/`vae`: records `offload()` calls and
    tracks `.device` like the real `NativeModel` does
    (`_free_room_for_tube_refine`'s residency check reads this attribute)."""

    def __init__(self, device="cpu"):
        self.offloaded = 0
        self.device = device

    def offload(self):
        self.offloaded += 1
        self.device = "cpu"


def _bundle(family="ltx"):
    return SimpleNamespace(
        spec=SimpleNamespace(family=family),
        dit=_Recording(),
        vae=_Recording(),
    )


class _FakeResidencyRegistry:
    """Records every `offload_all` call; never actually offloads anything --
    mirrors the test double in `test_dit_placement.py` /
    `test_latent_upscaler_ltx.py`."""

    def __init__(self):
        self.offload_all_calls = []

    def offload_all(self, device, *, exclude=()):
        self.offload_all_calls.append((device, tuple(exclude)))
        return []


def _pipe(**over):
    cfg = DetailerVideoLtxPipe.get_default_config()
    cfg.update({"device": "cpu"})
    cfg.update(over)
    return DetailerVideoLtxPipe(config=cfg)


def _pil_clip(n=12, h=64, w=64, value=100):
    return [Image.fromarray(np.full((h, w, 3), value, np.uint8)) for _ in range(n)]


# -- static contract ------------------------------------------------------


def test_name_and_io():
    assert DetailerVideoLtxPipe.name == "detailer"
    inputs = {i.name: i for i in DetailerVideoLtxPipe.inputs()}
    assert inputs["model"].io_type == IOType.MODEL and inputs["model"].required
    assert inputs["video"].io_type == IOType.VIDEO and inputs["video"].is_array
    assert inputs["conditioning"].io_type == IOType.CONDITIONING
    assert inputs["seed"].required is False
    outputs = {o.name: o for o in DetailerVideoLtxPipe.outputs()}
    assert outputs["video"].io_type == IOType.VIDEO and outputs["video"].is_array


def test_config_defaults_have_specs():
    spec_names = {s.name for s in DetailerVideoLtxPipe.configuration()}
    assert set(DetailerVideoLtxPipe.get_default_config()) <= spec_names


def test_strength_choices():
    strength = next(s for s in DetailerVideoLtxPipe.configuration() if s.name == "strength")
    assert strength.choices == ["light", "balanced", "strong"]


# -- guards ---------------------------------------------------------------


def test_rejects_non_ltx_model():
    with pytest.raises(ValueError, match="not an\\s+LTX"):
        _pipe().process(
            PipeInput(input={"model": _bundle(family="flux"), "video": ["x.mp4"],
                             "conditioning": [object()]}),
            lambda o: None)


def test_empty_video_input_returns_empty():
    out = _pipe().process(
        PipeInput(input={"model": _bundle(), "video": [], "conditioning": [object()]}), lambda o: None)
    assert out.output["video"] == []


def test_missing_conditioning_raises():
    with pytest.raises(ValueError, match="requires 'conditioning'"):
        _pipe().process(PipeInput(input={"model": _bundle(), "video": ["x.mp4"]}), lambda o: None)


# -- orchestration --------------------------------------------------------


def _one_face_track():
    return [Track([Detection(0, (10, 10, 30, 30), "face"),
                   Detection(6, (10, 10, 30, 30), "face")], "face")]


def test_no_tracks_returns_source_unchanged_without_reencode(monkeypatch):
    monkeypatch.setattr(f"{_MOD}.read_video_frames", lambda p: (_pil_clip(), 24.0))
    monkeypatch.setattr(f"{_MOD}.build_frame_detectors", _available_build_result)
    monkeypatch.setattr(f"{_MOD}.detect_tracks", lambda *a, **k: [])
    encode_called = {"n": 0}
    monkeypatch.setattr(f"{_MOD}.encode_frames_to_mp4",
                        lambda *a, **k: encode_called.__setitem__("n", encode_called["n"] + 1))

    out = _pipe().process(
        PipeInput(input={"model": _bundle(), "video": ["/src/clip.mp4"], "conditioning": [object()]}),
        lambda o: None)

    assert out.output["video"] == ["/src/clip.mp4"]  # untouched source path
    assert encode_called["n"] == 0                    # no wasteful re-encode


def test_full_flow_refines_and_reencodes_with_audio_passthrough(monkeypatch, tmp_path):
    monkeypatch.setattr(f"{_MOD}.read_video_frames", lambda p: (_pil_clip(value=100), 24.0))
    monkeypatch.setattr(f"{_MOD}.build_frame_detectors", _available_build_result)
    monkeypatch.setattr(f"{_MOD}.detect_tracks", lambda *a, **k: _one_face_track())

    def fake_refine(bundle, cond_model, pixels, **kw):
        # pixels is (1,3,n,th,tw) -> return an all-200 uint8 tube (n,th,tw,3)
        n, th, tw = pixels.shape[2], pixels.shape[3], pixels.shape[4]
        return np.full((n, th, tw, 3), 200, np.uint8)

    monkeypatch.setattr(f"{_MOD}.refine_tube_pixels", fake_refine)

    encoded = {}

    def fake_encode(frames, out_path, fps, audio=None):
        encoded["frames"] = frames.copy()
        encoded["audio"] = audio
        Path(out_path).write_bytes(b"mp4")
        return Path(out_path)

    monkeypatch.setattr(f"{_MOD}.encode_frames_to_mp4", fake_encode)

    bundle = _bundle()
    out = _pipe(color_correction="none").process(
        PipeInput(input={"model": bundle, "video": ["/src/clip.mp4"],
                         "conditioning": [object()], "seed": [42]}),
        lambda o: None)

    # returned a freshly-encoded file, not the source
    assert out.output["video"][0] != "/src/clip.mp4"
    assert Path(out.output["video"][0]).exists()
    # audio was carried over from the source clip
    assert encoded["audio"] == "/src/clip.mp4"
    # the refined region actually changed (center of the tube window moved
    # toward the 200 patch from the original 100)
    assert int(encoded["frames"][3, 20, 20, 0]) > 100
    # a pixel outside the window is untouched
    assert int(encoded["frames"][3, 60, 60, 0]) == 100
    # model weights this pipe pinned were released on the way out
    assert bundle.dit.offloaded == 1 and bundle.vae.offloaded == 1


# -- per-tube before/after compare artifact (2026-07-16) -------
# A/B-ing two whole generations is confounded, so every run emits its OWN
# evidence per refined tube: the middle frame, original crop vs. refined patch.


def _face_and_hand_tracks():
    return [
        Track([Detection(0, (10, 10, 30, 30), "face"), Detection(6, (10, 10, 30, 30), "face")], "face"),
        Track([Detection(0, (34, 34, 54, 54), "hand"), Detection(6, (34, 34, 54, 54), "hand")], "hand"),
    ]


def _refine_to_200(bundle, cond_model, pixels, **kw):
    n, th, tw = pixels.shape[2], pixels.shape[3], pixels.shape[4]
    return np.full((n, th, tw, 3), 200, np.uint8)


def test_emits_one_before_after_compare_per_tube(monkeypatch):
    monkeypatch.setattr(f"{_MOD}.read_video_frames", lambda p: (_pil_clip(value=100), 24.0))
    monkeypatch.setattr(f"{_MOD}.build_frame_detectors", _available_build_result)
    monkeypatch.setattr(f"{_MOD}.detect_tracks", lambda *a, **k: _face_and_hand_tracks())
    monkeypatch.setattr(f"{_MOD}.refine_tube_pixels", _refine_to_200)
    monkeypatch.setattr(f"{_MOD}.encode_frames_to_mp4",
                        lambda frames, out_path, fps, audio=None: Path(out_path).write_bytes(b"mp4"))

    messages = []
    _pipe(color_correction="none").process(
        PipeInput(input={"model": _bundle(), "video": ["/src/clip.mp4"],
                         "conditioning": [object()], "seed": [42]}),
        messages.append)

    compares = [m for m in messages if isinstance(m, CompareImagesGenerationOutput)]
    # one pair per refined track, in track order, labelled per-kind and numbered
    assert len(compares) == 2
    assert [c.index for c in compares] == [0, 1]
    assert compares[0].compare[0] == "Face 1 - before enhancement"
    assert compares[0].to[0] == "Face 1 - after enhancement"
    assert compares[1].compare[0] == "Hand 2 - before enhancement"
    assert compares[1].to[0] == "Hand 2 - after enhancement"


def test_compare_before_is_original_after_is_refined_same_size(monkeypatch):
    monkeypatch.setattr(f"{_MOD}.read_video_frames", lambda p: (_pil_clip(value=100), 24.0))
    monkeypatch.setattr(f"{_MOD}.build_frame_detectors", _available_build_result)
    monkeypatch.setattr(f"{_MOD}.detect_tracks", lambda *a, **k: _one_face_track())
    monkeypatch.setattr(f"{_MOD}.refine_tube_pixels", _refine_to_200)
    monkeypatch.setattr(f"{_MOD}.encode_frames_to_mp4",
                        lambda frames, out_path, fps, audio=None: Path(out_path).write_bytes(b"mp4"))

    messages = []
    _pipe(color_correction="none").process(
        PipeInput(input={"model": _bundle(), "video": ["/src/clip.mp4"],
                         "conditioning": [object()], "seed": [42]}),
        messages.append)

    compare = next(m for m in messages if isinstance(m, CompareImagesGenerationOutput))
    before = np.asarray(compare.compare[1])
    after = np.asarray(compare.to[1])
    # before = the untouched source crop (~100); after = the refined patch (200)
    assert before.mean() < after.mean()
    # both are the tube window's exact size -> they line up as a clean side-by-side
    assert before.shape == after.shape


def test_offload_runs_even_if_processing_raises(monkeypatch):
    monkeypatch.setattr(f"{_MOD}.read_video_frames", lambda p: (_pil_clip(), 24.0))
    monkeypatch.setattr(f"{_MOD}.build_frame_detectors", _available_build_result)

    def boom(*a, **k):
        raise RuntimeError("detect boom")

    monkeypatch.setattr(f"{_MOD}.detect_tracks", boom)

    bundle = _bundle()
    with pytest.raises(RuntimeError, match="detect boom"):
        _pipe().process(
            PipeInput(input={"model": bundle, "video": ["/src/clip.mp4"], "conditioning": [object()]}),
            lambda o: None)
    assert bundle.dit.offloaded == 1 and bundle.vae.offloaded == 1


# -- missing detection models: graceful degradation
# The MediaPipe .task files are fetched on demand and are not bundled -- a
# fresh install (or a partial download) must never crash the whole generation,
# and a missing hand model must never block face-only enhancement.


def test_both_models_missing_skips_enhancement_without_crashing(monkeypatch):
    monkeypatch.setattr(f"{_MOD}.read_video_frames", lambda p: (_pil_clip(), 24.0))
    monkeypatch.setattr(
        f"{_MOD}.build_frame_detectors",
        lambda *a, **k: DetectorBuildResult(detectors=[], missing=["face", "hand"]))

    def boom(*a, **k):
        raise AssertionError("detect_tracks must not run when every kind is missing its model")

    monkeypatch.setattr(f"{_MOD}.detect_tracks", boom)

    messages = []
    out = _pipe().process(
        PipeInput(input={"model": _bundle(), "video": ["/src/clip.mp4"], "conditioning": [object()]}),
        messages.append)

    # base video delivered unchanged -- the optional enhancement never fails generation
    assert out.output["video"] == ["/src/clip.mp4"]
    states = [getattr(m, "state", "") for m in messages]
    assert any("Face detection model isn't downloaded yet" in s for s in states)
    assert any("Hand detection model isn't downloaded yet" in s for s in states)
    assert any("Face and hand enhancement was skipped" in s for s in states)


def test_hand_model_missing_still_refines_faces(monkeypatch):
    monkeypatch.setattr(f"{_MOD}.read_video_frames", lambda p: (_pil_clip(value=100), 24.0))
    monkeypatch.setattr(
        f"{_MOD}.build_frame_detectors",
        lambda *a, **k: DetectorBuildResult(detectors=[object()], missing=["hand"]))
    monkeypatch.setattr(f"{_MOD}.detect_tracks", lambda *a, **k: _one_face_track())

    def fake_refine(bundle, cond_model, pixels, **kw):
        n, th, tw = pixels.shape[2], pixels.shape[3], pixels.shape[4]
        return np.full((n, th, tw, 3), 200, np.uint8)

    monkeypatch.setattr(f"{_MOD}.refine_tube_pixels", fake_refine)
    monkeypatch.setattr(f"{_MOD}.encode_frames_to_mp4",
                        lambda frames, out_path, fps, audio=None: Path(out_path).write_bytes(b"mp4"))

    messages = []
    out = _pipe(color_correction="none").process(
        PipeInput(input={"model": _bundle(), "video": ["/src/clip.mp4"],
                         "conditioning": [object()], "seed": [42]}),
        messages.append)

    # face-only enhancement still ran and produced a fresh file
    assert out.output["video"][0] != "/src/clip.mp4"
    states = [getattr(m, "state", "") for m in messages]
    assert any("Hand detection model isn't downloaded yet" in s for s in states)
    # face was never reported missing, and the "skip entirely" message never fired
    assert not any("Face detection model isn't downloaded yet" in s for s in states)
    assert not any("Face and hand enhancement was skipped" in s for s in states)


# -- OOM fix: free room BEFORE the per-track loop's own GPU work
# (the first tube's VAE encode) -- mirrors latent_upscaler/ltx's
# `_free_room_for_upscale`. A live maintainer OOM traced to a fully-resident
# DiT (warm-parked by the generator pipe that runs right before this one)
# still occupying VRAM when the first tube's encode ran.


def test_free_room_for_tube_refine_noop_on_non_cuda_device():
    dit = _Recording(device="cuda")  # parked resident
    bundle = _bundle()
    bundle.dit = dit
    manager = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_tube_refine(bundle, "cpu")
    assert dit.offloaded == 0
    assert manager.offload_all_calls == []
    mock_clear.assert_not_called()


def test_free_room_for_tube_refine_offloads_resident_dit_and_evicts_foreign_residents():
    dit = _Recording(device="cuda")  # dit_restore.py's warm-start
    bundle = _bundle()
    bundle.dit = dit
    manager = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_tube_refine(bundle, "cuda")
    assert dit.offloaded == 1
    assert manager.offload_all_calls == [("cuda", (bundle.vae, dit))]
    mock_clear.assert_called_once()


def test_free_room_for_tube_refine_skips_dit_offload_when_not_resident():
    dit = _Recording()  # device defaults to "cpu" -- nothing to evict
    bundle = _bundle()
    bundle.dit = dit
    manager = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_tube_refine(bundle, "cuda")
    assert dit.offloaded == 0
    # offload_all/clear_gpu_memory still run unconditionally -- other foreign
    # residents besides the DiT may exist.
    assert manager.offload_all_calls == [("cuda", (bundle.vae, dit))]
    mock_clear.assert_called_once()


# -- crop -> working-resolution upscale filter (softness root-cause)
#
# `_tube_to_pixels` almost always UPSCALES the crop before the refine (small
# faces/hands are the common case). At light/balanced strength the refine's
# low-noise denoise keeps a majority weight on this interpolated latent (see
# refine.py's STRENGTH_SIGMA_START), so a blurrier interpolant here bakes
# softness into the refine's own input, not just its paste-back output.
# Bicubic keeps more high-frequency energy than bilinear at the same upscale
# factor -- pinned below with a cv2-free Laplacian-variance sharpness metric.


def _laplacian_variance(img):
    gray = img.astype(np.float64).mean(axis=-1)
    h, w = gray.shape
    padded = np.pad(gray, 1, mode="edge")
    lap = (
        padded[0:h, 1:w + 1] + padded[2:h + 2, 1:w + 1]
        + padded[1:h + 1, 0:w] + padded[1:h + 1, 2:w + 2]
        - 4 * padded[1:h + 1, 1:w + 1]
    )
    return float(lap.var())


def _checkerboard(size, cell=4):
    yy, xx = np.mgrid[0:size, 0:size]
    board = (((xx // cell) + (yy // cell)) % 2) * 255
    return np.stack([board] * 3, axis=-1).astype(np.uint8)


def test_tube_to_pixels_uses_bicubic_interpolation(monkeypatch):
    import torch as _torch
    from src.pipelines.pipes.detailer.video_ltx import main as main_mod

    seen = {}
    orig_interp = _torch.nn.functional.interpolate

    def spy_interpolate(*a, **kw):
        seen["mode"] = kw.get("mode")
        return orig_interp(*a, **kw)

    monkeypatch.setattr(main_mod.torch.nn.functional, "interpolate", spy_interpolate)
    frames = np.zeros((1, 32, 32, 3), np.uint8)[None, ...][0]  # (1,32,32,3)
    from src.pipelines.pipes.detailer.video_ltx.windowing import TubeWindow
    window = TubeWindow("face", 0, 0, 32, 32, [(0, 0, 32, 32)], moving=False)

    DetailerVideoLtxPipe._tube_to_pixels(frames, window, 64, 64, "cpu")
    assert seen["mode"] == "bicubic"
    assert seen["mode"] != "bilinear"


def test_tube_to_pixels_output_is_clamped_to_valid_range():
    from src.pipelines.pipes.detailer.video_ltx.windowing import TubeWindow

    frames = _checkerboard(32, cell=2)[None, ...]  # (1,32,32,3), sharp high-freq edges
    window = TubeWindow("face", 0, 0, 32, 32, [(0, 0, 32, 32)], moving=False)

    pixels, _crops = DetailerVideoLtxPipe._tube_to_pixels(frames, window, 64, 64, "cpu")
    # bicubic can ring beyond the source range -- must be clamped before the
    # [-1, 1] remap, or the VAE encode sees out-of-distribution pixel values.
    assert pixels.min() >= -1.0 - 1e-5
    assert pixels.max() <= 1.0 + 1e-5


def test_tube_to_pixels_bicubic_sharper_than_bilinear_upscale():
    import torch as _torch
    from src.pipelines.pipes.detailer.video_ltx.windowing import TubeWindow

    crop = _checkerboard(128, cell=4)
    frames = crop[None, ...]
    window = TubeWindow("face", 0, 0, 128, 128, [(0, 0, 128, 128)], moving=False)

    pixels, _ = DetailerVideoLtxPipe._tube_to_pixels(frames, window, 512, 512, "cpu")
    bicubic_up = ((pixels[0, :, 0].permute(1, 2, 0).numpy() + 1.0) * 127.5).clip(0, 255).astype(np.uint8)

    t = _torch.from_numpy(crop).float().div(255.0).permute(2, 0, 1).unsqueeze(0)
    bilinear_up = _torch.nn.functional.interpolate(t, size=(512, 512), mode="bilinear", align_corners=False)
    bilinear_up = (bilinear_up[0].permute(1, 2, 0).numpy() * 255).clip(0, 255).astype(np.uint8)

    assert _laplacian_variance(bicubic_up) > _laplacian_variance(bilinear_up)


def test_process_calls_free_room_for_tube_refine_before_the_track_loop(monkeypatch):
    """Integration repro of the live crash: a parked-resident DiT must
    be offloaded by `process()` BEFORE the first track's `refine_tube_pixels`
    call actually runs."""
    monkeypatch.setattr(f"{_MOD}.read_video_frames", lambda p: (_pil_clip(value=100), 24.0))
    monkeypatch.setattr(f"{_MOD}.build_frame_detectors", _available_build_result)
    monkeypatch.setattr(f"{_MOD}.detect_tracks", lambda *a, **k: _one_face_track())
    monkeypatch.setattr(f"{_MOD}.encode_frames_to_mp4",
                        lambda frames, out_path, fps, audio=None: Path(out_path).write_bytes(b"mp4"))

    seen = {}

    def fake_refine(bundle, cond_model, pixels, **kw):
        seen.setdefault("dit_device_at_first_refine", bundle.dit.device)
        n, th, tw = pixels.shape[2], pixels.shape[3], pixels.shape[4]
        return np.full((n, th, tw, 3), 200, np.uint8)

    monkeypatch.setattr(f"{_MOD}.refine_tube_pixels", fake_refine)

    # `_tube_to_pixels` (unlike everything else this test mocks) does a real
    # tensor `.to(device)` -- irrelevant to what's under test here (eviction
    # ORDER, gated on the pipe's own "cuda" device string), so pin it to cpu
    # rather than requiring a real CUDA device just to exercise that plumbing.
    _orig_tube_to_pixels = DetailerVideoLtxPipe._tube_to_pixels
    monkeypatch.setattr(
        DetailerVideoLtxPipe, "_tube_to_pixels",
        staticmethod(lambda frames, window, tw, th, device: _orig_tube_to_pixels(frames, window, tw, th, "cpu")),
    )

    dit = _Recording(device="cuda")  # parked resident by the prior generator pipe
    bundle = _bundle()
    bundle.dit = dit

    manager = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory"):
        _pipe(device="cuda").process(
            PipeInput(input={"model": bundle, "video": ["/src/clip.mp4"],
                             "conditioning": [object()], "seed": [42]}),
            lambda o: None)

    # the free-room pass ran (and offloaded the DiT) before refine_tube_pixels
    # ever saw it -- proves the eviction happens before the per-track loop,
    # not after.
    assert seen["dit_device_at_first_refine"] == "cpu"
    # the very first eviction sweep excludes this pipe's own vae/dit, not a
    # foreign resident.
    assert manager.offload_all_calls[0] == ("cuda", (bundle.vae, dit))
    # dit.offload() ran at least once from the free-room pass (plus once more
    # from process()'s own teardown finally-block).
    assert dit.offloaded >= 1
