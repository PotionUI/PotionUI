import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes.controlnet_preprocessor import main as main_module
from src.pipelines.pipes.controlnet_preprocessor.main import ControlNetPreprocessorPipe
from src.platform.runtime.pose.layout import BODY_COLORS, FACE, LEFT_HAND, NECK, NUM_POINTS, R_WRIST
from tests.fixtures.pose_skeletons import skeleton


class FakeEstimator:
    def __init__(self, people=1):
        self.people = people
        self.detect_calls = 0

    def detect(self, rgb):
        self.detect_calls += 1
        height, width = rgb.shape[:2]
        return np.array([[0, 0, width, height, 0.9]] * self.people, dtype=np.float32).reshape(-1, 5)

    def estimate(self, rgb, box):
        height, width = rgb.shape[:2]
        return skeleton(neck=(width * 0.5, height * 0.3), size=height / 260.0)


class EmptyEstimator(FakeEstimator):
    def estimate(self, rgb, box):
        return np.zeros((NUM_POINTS, 3), dtype=np.float32)


def pipe(**config):
    return ControlNetPreprocessorPipe(config=config)


def draw(estimator, params=None, size=(256, 384)):
    return np.asarray(pipe()._preprocess_openpose(Image.new("RGB", size, (90, 90, 90)), params or {}, estimator))


def test_output_is_a_black_pose_map_at_the_input_size():
    out = draw(FakeEstimator())
    assert out.shape == (384, 256, 3)
    assert out.max() > 200
    assert (out.max(axis=2) == 0).mean() > 0.9


def test_default_is_body_only_like_the_old_openpose_option():
    out = draw(FakeEstimator())
    pose = skeleton(neck=(128, 115), size=384 / 260.0)
    x, y = (int(round(v)) for v in pose[R_WRIST, :2])
    assert tuple(out[y, x]) == BODY_COLORS[R_WRIST]
    hx, hy = (int(round(v)) for v in pose[LEFT_HAND.start + 19, :2])
    fx, fy = (int(round(v)) for v in pose[FACE.start, :2])
    assert tuple(out[hy, hx]) != (0, 0, 255)
    assert tuple(out[fy, fx]) != (255, 255, 255)


def test_hand_and_face_options_add_those_parts():
    base = draw(FakeEstimator())
    full = draw(FakeEstimator(), {"include_hand": True, "include_face": True})
    assert (full > 0).any(axis=2).sum() > (base > 0).any(axis=2).sum()
    assert ((full[..., 0] == 255) & (full[..., 1] == 255) & (full[..., 2] == 255)).any()
    assert ((full[..., 0] == 0) & (full[..., 1] == 0) & (full[..., 2] == 255)).any()


def test_body_off_draws_no_limbs():
    out = draw(FakeEstimator(), {"include_body": False, "include_face": True})
    assert not (out == np.array(BODY_COLORS[NECK], dtype=np.uint8)).all(axis=2).any()


def test_every_person_in_the_image_is_drawn():
    class Spread(FakeEstimator):
        def __init__(self):
            super().__init__(people=2)
            self.index = 0

        def estimate(self, rgb, box):
            self.index += 1
            height, width = rgb.shape[:2]
            return skeleton(neck=(width * (0.25 if self.index == 1 else 0.75), height * 0.3), size=height / 400.0)

    out = draw(Spread())
    left, right = out[:, :128], out[:, 128:]
    assert left.max() > 200 and right.max() > 200


def test_no_person_gives_a_blank_map_so_strict_mode_can_report_it():
    out = draw(EmptyEstimator())
    assert out.max() == 0


def test_an_estimator_failure_falls_back_to_the_image():
    class Broken(FakeEstimator):
        def detect(self, rgb):
            raise RuntimeError("onnx exploded")

    image = Image.new("RGB", (64, 64), (5, 6, 7))
    assert pipe()._preprocess_openpose(image, {}, Broken()) is image


def test_the_pipe_no_longer_references_the_cmu_openpose_detector():
    assert not hasattr(main_module, "OpenposeDetector")
    assert "OpenposeDetector" not in open(main_module.__file__, encoding="utf-8").read()


def test_pose_needs_no_controlnet_aux(monkeypatch):
    monkeypatch.setattr(main_module, "CONTROLNET_AUX_AVAILABLE", False)
    monkeypatch.setattr(ControlNetPreprocessorPipe, "_resolve_pose_estimator", staticmethod(lambda pipe_input, config: FakeEstimator()))
    result = ControlNetPreprocessorPipe(config={
        "strict": True, "preprocessors": [{"type": "openpose", "enabled": True, "parameters": {}}],
    }).process(PipeInput(input={"image": [Image.new("RGB", (128, 128))]}), lambda _o: None)
    assert np.asarray(result.output["image"][0]).max() > 200


class TestResolvePoseEstimator:
    resolve = staticmethod(ControlNetPreprocessorPipe._resolve_pose_estimator)

    def test_not_loaded_when_no_pose_preprocessor_is_enabled(self, monkeypatch):
        monkeypatch.setattr(main_module, "acquire_pose_estimator", lambda *a: pytest.fail("loaded"))
        assert self.resolve(PipeInput(input={}), [{"type": "canny", "enabled": True}]) is None
        assert self.resolve(PipeInput(input={}), [{"type": "openpose", "enabled": False}]) is None

    def test_loaded_through_the_assets_and_models_services(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(main_module, "acquire_pose_estimator", lambda assets, models: seen.update(a=assets, m=models) or "estimator")
        out = self.resolve(PipeInput(input={"ASSETS": "A", "MODELS": "M"}), [{"type": "OpenPose", "enabled": True}])
        assert out == "estimator" and seen == {"a": "A", "m": "M"}

    def test_a_missing_asset_service_fails_the_generation_instead_of_passing_the_photo(self):
        with pytest.raises(GenerationExecutionError, match="asset service"):
            self.resolve(PipeInput(input={}), [{"type": "openpose", "enabled": True}])
