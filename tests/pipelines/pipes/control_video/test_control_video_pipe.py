import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from src.features.generation.engine import validate_pipe_configuration
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.outputs import (
    GalleryGenerationOutput,
    GenerationExecutionError,
    ImageGenerationOutput,
    ProgressGenerationOutput,
)
from src.pipelines.pipes._shared.pose import estimator as shared_estimator
from src.pipelines.pipes.control_video import main as pipe_module
from src.pipelines.pipes.control_video.main import ControlVideoPipe
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.pose.dwpose import DETECTOR_FILENAME, DWPOSE_REPO, POSE_FILENAME
from src.platform.runtime.pose.layout import NUM_POINTS
from tests.fixtures.pose_skeletons import skeleton
from tests.platform.media_tools.media_factory import make_video

FRAMES = 12
FPS = 10


class FakeEstimator:
    def __init__(self, detect_empty=False):
        self.detect_empty = detect_empty
        self.calls = 0

    def detect(self, rgb):
        if self.detect_empty:
            return np.zeros((0, 5), dtype=np.float32)
        height, width = rgb.shape[:2]
        return np.array([[0, 0, width, height, 0.9]], dtype=np.float32)

    def estimate(self, rgb, box):
        if self.detect_empty:
            return np.zeros((NUM_POINTS, 3), dtype=np.float32)
        height, width = rgb.shape[:2]
        self.calls += 1
        size = height / 260.0
        sway = 6.0 * np.sin(self.calls / 2.0)
        return skeleton(neck=(width / 2 + sway, height * 0.25), size=size)


def make(**config):
    values = ControlVideoPipe.get_default_config()
    values.update(config)
    return ControlVideoPipe(values)


def run(pipe, estimator=None, cancelled=None, **inputs):
    estimator = estimator or FakeEstimator()
    pipe._load_estimator = lambda _input: estimator
    emitted = []
    result = pipe.process(PipeInput(input=inputs), emitted.append, cancelled)
    return result.output["video"], emitted


def probe(path):
    capture = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    fps = capture.get(cv2.CAP_PROP_FPS)
    capture.release()
    return frames, fps


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("clips") / "dance.mp4"
    return str(make_video(path, seconds=FRAMES / FPS, size=(160, 96), fps=FPS, audio=False))


@pytest.fixture
def picture():
    return Image.new("RGB", (200, 300), (120, 120, 120))


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


class TestContract:
    def test_identity(self):
        assert ControlVideoPipe.name == "control_video"

    def test_inputs_and_outputs(self):
        inputs = {spec.name: spec for spec in ControlVideoPipe.inputs()}
        assert (inputs["video"].io_type, inputs["video"].required, inputs["video"].is_array) == (IOType.VIDEO, True, True)
        assert (inputs["image"].io_type, inputs["image"].required, inputs["image"].is_array) == (IOType.IMAGE, False, True)
        assert inputs["MODELS"].io_type == IOType.SERVICE and inputs["ASSETS"].io_type == IOType.SERVICE
        (out,) = ControlVideoPipe.outputs()
        assert (out.name, out.io_type, out.is_array) == ("video", IOType.VIDEO, True)

    def test_configuration_names_and_defaults(self):
        specs = {spec.name: spec for spec in ControlVideoPipe.configuration()}
        assert {name: spec.default for name, spec in specs.items()} == {
            "type": "pose", "width": 0, "height": 0, "max_pixels": 0, "size_multiple": 32, "fit": "contain",
            "match_picture": True, "ease_in": 0.0, "body": True, "hands": True, "face": True,
            "detect_resolution": 512, "line_scale": 1.0, "min_confidence": 0.3, "smoothing": 0.5,
            "keyframe_interval": 8, "max_gap": 12, "max_frames": 0, "max_seconds": 0.0, "canny_low": 100.0, "canny_high": 200.0,
            "save_guide": False, "weights_dir": "",
        }
        assert specs["type"].choices == ["pose", "depth", "edges"]

    def test_an_unknown_guide_type_is_rejected_by_the_config_check(self):
        with pytest.raises(ValueError, match="type"):
            validate_pipe_configuration(ControlVideoPipe, {"type": "normals"})

    def test_an_unknown_guide_type_is_rejected_at_run_time(self, clip):
        with pytest.raises(GenerationExecutionError, match="Unknown guide type"):
            run(make(type="normals"), video=[clip])

    def test_no_video_is_an_error(self):
        with pytest.raises(GenerationExecutionError, match="at least one video"):
            run(make(), video=None)


class TestGuideVideo:
    def test_frame_count_fps_and_size_follow_the_contract(self, clip):
        (guide,), _ = run(make(), video=[clip])
        frames, fps = probe(guide)
        assert len(frames) == FRAMES
        assert fps == pytest.approx(FPS, abs=0.01)
        height, width = frames[0].shape[:2]
        assert width % 32 == 0 and height % 32 == 0
        assert (width, height) == (160, 96)
        Path(guide).unlink()

    def test_guide_is_a_skeleton_on_black(self, clip):
        (guide,), _ = run(make(smoothing=0.0), video=[clip])
        frames, _ = probe(guide)
        middle = frames[FRAMES // 2]
        coloured = (middle.max(axis=2) > 80).sum()
        assert 30 < coloured < middle.shape[0] * middle.shape[1] * 0.3
        assert (frames[0] < 8).mean() > 0.8
        Path(guide).unlink()

    def test_one_guide_per_video_in_order(self, clip, tmp_path):
        second = str(make_video(tmp_path / "other.mp4", seconds=0.6, size=(96, 160), fps=FPS, audio=False))
        guides, _ = run(make(), video=[clip, second])
        counts = [len(probe(g)[0]) for g in guides]
        assert counts == [FRAMES, 6]
        for guide in guides:
            Path(guide).unlink()

    def test_explicit_size_and_max_pixels(self, clip):
        (sized,), _ = run(make(width=320, height=192), video=[clip])
        assert probe(sized)[0][0].shape[:2] == (192, 320)
        (capped,), _ = run(make(max_pixels=4096), video=[clip])
        height, width = probe(capped)[0][0].shape[:2]
        assert width * height <= 4096
        Path(sized).unlink()
        Path(capped).unlink()

    def test_max_frames_limits_the_work(self, clip):
        (guide,), _ = run(make(max_frames=5), video=[clip])
        assert len(probe(guide)[0]) == 5
        Path(guide).unlink()

    def test_max_seconds_trims_by_time_at_any_frame_rate(self, tmp_path):
        fast = str(make_video(tmp_path / "fast.mp4", seconds=2.0, size=(160, 96), fps=60, audio=False))
        slow = str(make_video(tmp_path / "slow.mp4", seconds=2.0, size=(160, 96), fps=10, audio=False))
        (fast_guide,), _ = run(make(max_seconds=0.5), video=[fast])
        (slow_guide,), _ = run(make(max_seconds=0.5), video=[slow])
        assert len(probe(fast_guide)[0]) == 30
        assert len(probe(slow_guide)[0]) == 5
        (both,), _ = run(make(max_seconds=0.5, max_frames=12), video=[fast])
        assert len(probe(both)[0]) == 12
        (full,), _ = run(make(), video=[slow])
        assert len(probe(full)[0]) == 20
        for guide in (fast_guide, slow_guide, both, full):
            Path(guide).unlink()

    def test_fit_cover_and_contain_place_the_person_differently(self, clip):
        (contain,), _ = run(make(width=96, height=96, fit="contain", smoothing=0.0), video=[clip])
        (cover,), _ = run(make(width=96, height=96, fit="cover", smoothing=0.0), video=[clip])
        a, b = probe(contain)[0][4], probe(cover)[0][4]
        assert not np.array_equal(a, b)
        Path(contain).unlink()
        Path(cover).unlink()

    @pytest.mark.parametrize("flag", ["hands", "face", "body"])
    def test_section_flags_reach_the_renderer(self, clip, flag):
        (full,), _ = run(make(smoothing=0.0), video=[clip])
        (partial,), _ = run(make(smoothing=0.0, **{flag: False}), video=[clip])
        assert (probe(full)[0][3] > 0).sum() > (probe(partial)[0][3] > 0).sum()
        Path(full).unlink()
        Path(partial).unlink()


class TestPictureRetarget:
    def test_size_comes_from_the_picture_and_motion_is_retargeted(self, clip, picture):
        (guide,), _ = run(make(smoothing=0.0), video=[clip], image=[picture])
        frames, fps = probe(guide)
        assert len(frames) == FRAMES and fps == pytest.approx(FPS, abs=0.01)
        assert frames[0].shape[:2] == (288, 192)
        Path(guide).unlink()

    def test_match_picture_off_ignores_the_picture(self, clip, picture):
        (guide,), _ = run(make(match_picture=False), video=[clip], image=[picture])
        assert probe(guide)[0][0].shape[:2] == (96, 160)
        Path(guide).unlink()

    def test_one_picture_pairs_with_every_video(self, clip, picture, tmp_path):
        second = str(make_video(tmp_path / "b.mp4", seconds=0.5, size=(160, 96), fps=FPS, audio=False))
        guides, _ = run(make(), video=[clip, second], image=[picture])
        assert [probe(g)[0][0].shape[:2] for g in guides] == [(288, 192)] * 2
        for guide in guides:
            Path(guide).unlink()

    def test_the_retarget_is_kept_inside_the_pictures_frame(self, clip, picture, monkeypatch):
        seen = {}
        real = pipe_module.retarget_sequence

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr(pipe_module, "retarget_sequence", spy)
        (guide,), _ = run(make(), video=[clip], image=[picture])
        assert seen["bounds"] == (200, 300)
        Path(guide).unlink()

    def test_each_video_pairs_with_its_own_picture(self, clip, picture, tmp_path):
        second = str(make_video(tmp_path / "c.mp4", seconds=0.5, size=(160, 96), fps=FPS, audio=False))
        wide = Image.new("RGB", (300, 200), (120, 120, 120))
        guides, _ = run(make(), video=[clip, second], image=[picture, wide])
        assert [probe(g)[0][0].shape[:2] for g in guides] == [(288, 192), (192, 288)]
        for guide in guides:
            Path(guide).unlink()

    def test_ease_in_starts_from_the_pictures_pose(self, clip, picture):
        (plain,), _ = run(make(smoothing=0.0), video=[clip], image=[picture])
        (eased,), _ = run(make(smoothing=0.0, ease_in=0.5), video=[clip], image=[picture])
        assert not np.array_equal(probe(plain)[0][0], probe(eased)[0][0])
        first = np.abs(probe(plain)[0][0].astype(int) - probe(eased)[0][0].astype(int)).mean()
        last = np.abs(probe(plain)[0][-1].astype(int) - probe(eased)[0][-1].astype(int)).mean()
        assert first > 5 * last
        Path(plain).unlink()
        Path(eased).unlink()


class TestOutputs:
    def test_a_preview_artifact_and_progress_are_emitted(self, clip):
        (guide,), emitted = run(make(), video=[clip])
        previews = [o for o in emitted if isinstance(o, ImageGenerationOutput)]
        assert len(previews) == 1 and previews[0].isArtifact and previews[0].label == "Pose guide"
        assert any(isinstance(o, ProgressGenerationOutput) for o in emitted)
        assert not any(isinstance(o, GalleryGenerationOutput) for o in emitted)
        Path(guide).unlink()

    def test_save_guide_puts_the_video_in_the_gallery(self, clip):
        (guide,), emitted = run(make(save_guide=True), video=[clip])
        (gallery,) = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
        (saved,) = gallery.videos
        assert saved.temporary is False and str(saved.video_path) == guide
        assert saved.fps == pytest.approx(FPS, abs=0.01)
        Path(guide).unlink()


class TestFailures:
    def test_no_person_in_the_video(self, clip, scratch):
        with pytest.raises(GenerationExecutionError, match="No person found in the video"):
            run(make(), estimator=FakeEstimator(detect_empty=True), video=[clip])
        assert list(scratch.iterdir()) == []

    def test_no_person_in_the_picture(self, clip, picture):
        class VideoOnly(FakeEstimator):
            def estimate(self, rgb, box):
                if rgb.shape[0] > rgb.shape[1]:
                    return np.zeros((NUM_POINTS, 3), dtype=np.float32)
                return super().estimate(rgb, box)

        with pytest.raises(GenerationExecutionError, match="No person found in the picture"):
            run(make(), estimator=VideoOnly(), video=[clip], image=[picture])

    def test_unreadable_video(self, tmp_path):
        broken = tmp_path / "broken.mp4"
        broken.write_bytes(b"not a video")
        with pytest.raises(GenerationExecutionError, match="Could not open"):
            run(make(), video=[str(broken)])

    def test_cancellation_stops_and_leaves_no_files(self, clip, scratch):
        state = {"n": 0}

        def cancelled():
            state["n"] += 1
            return state["n"] > 14

        with pytest.raises(SamplingCancelled):
            run(make(), cancelled=cancelled, video=[clip])
        assert list(scratch.iterdir()) == []

    def test_no_person_in_a_later_video_discards_the_earlier_guide(self, clip, tmp_path, scratch):
        class OnlyLandscape(FakeEstimator):
            def estimate(self, rgb, box):
                if rgb.shape[0] > rgb.shape[1]:
                    return np.zeros((NUM_POINTS, 3), dtype=np.float32)
                return super().estimate(rgb, box)

        portrait = str(make_video(tmp_path / "portrait.mp4", seconds=0.5, size=(96, 160), fps=FPS, audio=False))
        with pytest.raises(GenerationExecutionError, match="No person found in the video"):
            run(make(), estimator=OnlyLandscape(), video=[clip, portrait])
        assert list(scratch.iterdir()) == []

    def test_a_cancelled_encode_aborts_the_writer_without_closing_it(self, clip, scratch, monkeypatch):
        events = []

        class FakeWriter:
            def __init__(self, path, *args, **kwargs):
                Path(path).write_bytes(b"partial")

            def write(self, frame):
                events.append("write")

            def close(self):
                events.append("close")

            def abort(self):
                events.append("abort")

        monkeypatch.setattr(pipe_module, "StreamingMp4Writer", FakeWriter)
        state = {"n": 0}

        def cancelled():
            state["n"] += 1
            return state["n"] > 14

        with pytest.raises(SamplingCancelled):
            run(make(), cancelled=cancelled, video=[clip])
        assert "abort" in events and "close" not in events
        assert list(scratch.iterdir()) == []

    def test_a_finished_encode_closes_the_writer(self, clip, monkeypatch):
        events = []

        class FakeWriter:
            def __init__(self, *args, **kwargs):
                pass

            def write(self, frame):
                pass

            def close(self):
                events.append("close")

            def abort(self):
                events.append("abort")

        monkeypatch.setattr(pipe_module, "StreamingMp4Writer", FakeWriter)
        (guide,), _ = run(make(), video=[clip])
        assert events == ["close"]
        Path(guide).unlink()

    def test_a_failure_on_a_later_video_discards_the_earlier_guide(self, clip, tmp_path, scratch):
        broken = tmp_path / "broken.mp4"
        broken.write_bytes(b"not a video")
        with pytest.raises(GenerationExecutionError):
            run(make(), video=[clip, str(broken)])
        assert list(scratch.iterdir()) == []


class TestModels:
    def install(self, directory):
        for name in (DETECTOR_FILENAME, POSE_FILENAME):
            (directory / name).write_bytes(b"x")

    def test_assets_service_fetches_only_the_two_files(self, tmp_path, monkeypatch):
        self.install(tmp_path)
        monkeypatch.setattr(shared_estimator, "verify_weights", lambda directory: None)
        monkeypatch.setattr(shared_estimator.DWPoseEstimator, "from_files", classmethod(lambda cls, directory: ("loaded", directory)))
        seen = {}

        class Assets:
            def ensure_asset_repo(self, repo, *, subdir, files=None):
                seen.update(repo=repo, subdir=subdir, files=files)
                return tmp_path

        loaded = make()._load_estimator(PipeInput(input={"ASSETS": Assets()}))
        assert loaded == ("loaded", tmp_path)
        assert seen["repo"] == DWPOSE_REPO == "yzd-v/DWPose"
        assert seen["subdir"] == "annotators/yzd-v-dwpose"
        assert seen["files"] == [DETECTOR_FILENAME, POSE_FILENAME]

    def test_models_service_caches_under_one_key_with_a_file_fingerprint(self, tmp_path, monkeypatch):
        self.install(tmp_path)
        monkeypatch.setattr(shared_estimator, "verify_weights", lambda directory: None)
        seen = {}

        class Models:
            def acquire(self, key, fingerprint, loader):
                seen.update(key=key, fingerprint=fingerprint)
                return "cached"

        pipe = make(weights_dir=str(tmp_path))
        assert pipe._load_estimator(PipeInput(input={"MODELS": Models()})) == "cached"
        assert seen["key"] == "native/pose/dwpose"
        assert seen["fingerprint"].count("|") == 2

    def test_no_weights_source_is_an_error(self):
        with pytest.raises(GenerationExecutionError, match="asset service"):
            make()._load_estimator(PipeInput(input={}))

    def test_a_corrupted_download_is_reported_not_loaded(self, tmp_path):
        self.install(tmp_path)
        with pytest.raises(GenerationExecutionError, match="incomplete or corrupted"):
            make(weights_dir=str(tmp_path))._load_estimator(PipeInput(input={}))
