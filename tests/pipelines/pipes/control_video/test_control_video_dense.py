import tempfile

import cv2
import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import (
    GalleryGenerationOutput,
    GenerationExecutionError,
    ImageGenerationOutput,
    ProgressGenerationOutput,
)
from src.pipelines.pipes._shared.depth import estimator as shared_depth
from src.pipelines.pipes.control_video import main as pipe_module
from src.pipelines.pipes.control_video.main import ControlVideoPipe
from src.platform.runtime.depth.video_depth import WEIGHT_FILENAME, depth_to_guide
from src.platform.runtime.native.errors import SamplingCancelled
from tests.pipelines.pipes.control_video.test_control_video_pipe import probe
from tests.platform.media_tools.media_factory import make_video

FRAMES = 12
FPS = 10


class FakeDepth:
    def __init__(self):
        self.seen = None
        self.released = False

    def iter_depth(self, frames, output_size, progress, cancelled):
        count = 0
        shape = None
        for frame in frames:
            count += 1
            shape = frame.shape
            height, width = frame.shape[:2]
            yield np.tile(np.linspace(0.0, 1.0, width, dtype=np.float16), (height, 1))
        self.seen = (count, shape, output_size)
        if progress:
            progress(count)

    def release(self):
        self.released = True


def make(**config):
    values = ControlVideoPipe.get_default_config()
    values.update(config)
    return ControlVideoPipe(values)


def run(pipe, depth=None, cancelled=None, **inputs):
    depth = depth or FakeDepth()
    pipe._load_depth_estimator = lambda _input: depth
    emitted = []
    result = pipe.process(PipeInput(input=inputs), emitted.append, cancelled)
    return result.output["video"], emitted


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("dense") / "scene.mp4"
    return str(make_video(path, seconds=FRAMES / FPS, size=(160, 96), fps=FPS, audio=False))


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


class TestEdges:
    def test_the_guide_has_the_clips_frames_rate_and_size(self, clip):
        (guide,), _ = run(make(type="edges"), video=[clip])
        frames, fps = probe(guide)
        assert len(frames) == FRAMES
        assert fps == pytest.approx(FPS, abs=0.01)
        assert frames[0].shape[:2] == (96, 160)

    def test_it_is_white_lines_on_black(self, clip):
        (guide,), _ = run(make(type="edges"), video=[clip])
        frame = probe(guide)[0][0]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        assert (gray < 40).mean() > 0.6
        assert (gray > 200).mean() > 0.01

    def test_it_needs_no_model(self, clip):
        pipe = make(type="edges")
        pipe._load_depth_estimator = lambda _input: pytest.fail("edges must not load the depth model")
        pipe._load_estimator = lambda _input: pytest.fail("edges must not load the pose model")
        result = pipe.process(PipeInput(input={"video": [clip]}), lambda _o: None)
        assert len(result.output["video"]) == 1

    def test_the_thresholds_change_the_lines(self, clip):
        (soft,), _ = run(make(type="edges", canny_low=20, canny_high=60), video=[clip])
        (hard,), _ = run(make(type="edges", canny_low=400, canny_high=500), video=[clip])
        assert probe(soft)[0][0].mean() > probe(hard)[0][0].mean()

    def test_a_picture_is_ignored_and_the_clips_frame_is_kept(self, clip):
        picture = Image.new("RGB", (200, 300), (120, 120, 120))
        (guide,), _ = run(make(type="edges"), video=[clip], image=[picture])
        assert probe(guide)[0][0].shape[:2] == (96, 160)

    def test_a_pixel_budget_shrinks_the_guide_on_the_grid(self, clip):
        (guide,), _ = run(make(type="edges", max_pixels=64 * 64, size_multiple=32), video=[clip])
        height, width = probe(guide)[0][0].shape[:2]
        assert width % 32 == 0 and height % 32 == 0 and width * height <= 64 * 64

    def test_an_exact_size_with_cover_fills_it(self, clip):
        (guide,), _ = run(make(type="edges", width=96, height=96, fit="cover"), video=[clip])
        assert probe(guide)[0][0].shape[:2] == (96, 96)

    def test_max_frames_limits_the_guide(self, clip):
        (guide,), _ = run(make(type="edges", max_frames=5), video=[clip])
        assert len(probe(guide)[0]) == 5

    def test_the_preview_is_labelled_and_progress_is_reported(self, clip):
        _, emitted = run(make(type="edges"), video=[clip])
        previews = [o for o in emitted if isinstance(o, ImageGenerationOutput)]
        assert [p.label for p in previews] == ["Edge guide"]
        assert any(isinstance(o, ProgressGenerationOutput) for o in emitted)

    def test_save_guide_sends_it_to_the_gallery(self, clip):
        _, emitted = run(make(type="edges", save_guide=True), video=[clip])
        assert len([o for o in emitted if isinstance(o, GalleryGenerationOutput)]) == 1

    def test_one_guide_per_video(self, clip):
        guides, _ = run(make(type="edges"), video=[clip, clip])
        assert len(guides) == 2 and guides[0] != guides[1]

    def test_cancelling_leaves_no_file_behind(self, clip, scratch):
        with pytest.raises(SamplingCancelled):
            run(make(type="edges"), cancelled=lambda: True, video=[clip])
        assert list(scratch.iterdir()) == []


class TestDepth:
    def test_the_guide_has_the_clips_frames_rate_and_size(self, clip):
        (guide,), _ = run(make(type="depth"), video=[clip])
        frames, fps = probe(guide)
        assert len(frames) == FRAMES
        assert fps == pytest.approx(FPS, abs=0.01)
        assert frames[0].shape[:2] == (96, 160)

    def test_the_model_sees_every_frame_at_the_guide_size(self, clip):
        depth = FakeDepth()
        run(make(type="depth"), depth=depth, video=[clip])
        assert depth.seen == (FRAMES, (96, 160, 3), None)

    def test_the_guide_is_a_grey_map_with_the_models_gradient(self, clip):
        (guide,), _ = run(make(type="depth"), video=[clip])
        gray = cv2.cvtColor(probe(guide)[0][0], cv2.COLOR_BGR2GRAY)
        assert gray[:, -4:].mean() > gray[:, :4].mean() + 150

    def test_it_matches_the_runtime_normalisation(self, clip):
        (guide,), _ = run(make(type="depth"), video=[clip])
        frame = probe(guide)[0][0]
        ramp = np.tile(np.linspace(0.0, 1.0, 160, dtype=np.float16), (96, 1))[None]
        expected = depth_to_guide(ramp)[0]
        assert np.abs(frame[:, :, 1].astype(int) - expected.astype(int)).mean() < 8

    def test_the_model_is_released_after_the_run(self, clip):
        depth = FakeDepth()
        run(make(type="depth"), depth=depth, video=[clip])
        assert depth.released is True

    def test_the_model_is_released_even_when_it_fails(self, clip):
        depth = FakeDepth()
        depth.iter_depth = lambda *args: (_ for _ in ()).throw(RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            run(make(type="depth"), depth=depth, video=[clip])
        assert depth.released is True

    def test_the_preview_is_labelled(self, clip):
        _, emitted = run(make(type="depth"), video=[clip])
        assert [o.label for o in emitted if isinstance(o, ImageGenerationOutput)] == ["Depth guide"]

    def test_cancelling_leaves_no_file_behind(self, clip, scratch):
        with pytest.raises(SamplingCancelled):
            run(make(type="depth"), cancelled=lambda: True, video=[clip])
        assert list(scratch.iterdir()) == []

    def test_the_pose_model_is_never_loaded(self, clip):
        pipe = make(type="depth")
        pipe._load_estimator = lambda _input: pytest.fail("depth must not load the pose model")
        pipe._load_depth_estimator = lambda _input: FakeDepth()
        pipe.process(PipeInput(input={"video": [clip]}), lambda _o: None)


class TestDepthWeights:
    def test_without_an_asset_service_or_folder_it_says_so(self):
        with pytest.raises(GenerationExecutionError, match="no asset service"):
            shared_depth.acquire_depth_estimator(None, None, "")

    def test_the_depot_fetch_asks_for_the_one_checkpoint(self, tmp_path):
        calls = {}

        class Assets:
            def ensure_asset_repo(self, repo, subdir, files):
                calls.update(repo=repo, subdir=subdir, files=files)
                return str(tmp_path)

        with pytest.raises(GenerationExecutionError, match="not found"):
            shared_depth.acquire_depth_estimator(Assets(), None, "")
        assert calls == {
            "repo": "depth-anything/Video-Depth-Anything-Small",
            "subdir": "annotators/depth-anything-video-depth-anything-small",
            "files": [WEIGHT_FILENAME],
        }

    def test_a_corrupt_download_is_reported_by_name(self, tmp_path):
        (tmp_path / WEIGHT_FILENAME).write_bytes(b"short")
        with pytest.raises(GenerationExecutionError, match="incomplete or corrupted"):
            shared_depth.acquire_depth_estimator(None, None, str(tmp_path))


class FakeModels:
    def __init__(self):
        self.calls = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint, estimated_vram_gb))
        return loader()


class TestLifecycle:
    def test_the_depth_model_is_cached_and_budgeted_through_the_lifecycle(self, tmp_path, monkeypatch):
        weights = tmp_path / WEIGHT_FILENAME
        weights.write_bytes(b"w")
        loaded = []

        class Model:
            def to(self, device):
                loaded.append(device)
                return self

        monkeypatch.setattr(shared_depth, "verify_weights", lambda directory: weights)
        monkeypatch.setattr(shared_depth, "load_model", lambda path: Model())
        models = FakeModels()
        estimator = shared_depth.acquire_depth_estimator(None, models, str(tmp_path))
        (key, fingerprint, vram) = models.calls[0]
        assert key == "native/depth/video_depth_small" and fingerprint and vram == 1.5
        assert estimator.estimated_vram_gb == 1.5 and len(loaded) == 1

    def test_without_the_lifecycle_the_model_is_loaded_directly(self, tmp_path, monkeypatch):
        weights = tmp_path / WEIGHT_FILENAME
        weights.write_bytes(b"w")

        class Model:
            def to(self, device):
                return self

        monkeypatch.setattr(shared_depth, "verify_weights", lambda directory: weights)
        monkeypatch.setattr(shared_depth, "load_model", lambda path: Model())
        assert shared_depth.acquire_depth_estimator(None, None, str(tmp_path)).model is not None

    def test_the_pipe_hands_the_models_and_assets_services_to_the_loader(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            pipe_module, "acquire_depth_estimator",
            lambda assets, models, weights_dir: seen.update(assets=assets, models=models, weights=weights_dir) or FakeDepth(),
        )
        pipe = make(type="depth", weights_dir="/w")
        pipe._load_depth_estimator(PipeInput(input={"ASSETS": "A", "MODELS": "M"}))
        assert seen == {"assets": "A", "models": "M", "weights": "/w"}


class TestBoundedMemory:
    def test_depth_never_runs_unbounded_and_edges_keep_the_clip_length(self, clip, monkeypatch):
        limits = []
        real = ControlVideoPipe._frames

        def spy(path, limit, max_seconds=0.0):
            limits.append(limit)
            return real(path, limit, max_seconds)

        monkeypatch.setattr(ControlVideoPipe, "_frames", staticmethod(spy))
        run(make(type="depth"), video=[clip])
        run(make(type="depth", max_frames=7), video=[clip])
        run(make(type="depth", max_frames=5000), video=[clip])
        run(make(type="edges"), video=[clip])
        assert limits == [1001, 7, 1001, 0]

    def test_frames_flow_to_the_estimator_one_at_a_time(self, clip):
        class Spy(FakeDepth):
            def iter_depth(self, frames, output_size, progress, cancelled):
                assert not isinstance(frames, (list, tuple))
                return super().iter_depth(frames, output_size, progress, cancelled)

        run(make(type="depth"), depth=Spy(), video=[clip])

    def test_only_the_float16_depth_is_buffered(self, clip, monkeypatch):
        kept = []
        real = pipe_module.sample_depth

        def spy(depth):
            kept.append(depth.dtype)
            return real(depth)

        monkeypatch.setattr(pipe_module, "sample_depth", spy)
        run(make(type="depth"), video=[clip])
        assert len(kept) == FRAMES and set(kept) == {np.dtype(np.float16)}

    def test_a_clip_with_no_readable_frames_fails_cleanly(self, tmp_path):
        empty = tmp_path / "empty.mp4"
        empty.write_bytes(b"")
        with pytest.raises(GenerationExecutionError):
            run(make(type="edges"), video=[str(empty)])
