import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from src.platform.runtime.depth import video_depth as vd
from src.platform.runtime.native.errors import SamplingCancelled

KEYS = Path("tests/fixtures/video_depth_small_keys.json")


def ramp_depth(total, height=6, width=8, scale=1.0, shift=0.0):
    base = np.linspace(0.2, 1.0, height * width, dtype=np.float32).reshape(height, width)
    return [base * (1.0 + 0.01 * index) * scale + shift for index in range(total)]


class TestWindows:
    @pytest.mark.parametrize("total,expected", [(1, 32), (22, 32), (23, 54), (44, 54), (45, 76)])
    def test_the_clip_is_padded_to_whole_windows(self, total, expected):
        assert vd.padded_length(total) == expected

    def test_windows_start_every_22_frames_and_cover_the_clip(self):
        assert vd.window_starts(1) == [0]
        assert vd.window_starts(45) == [0, 22, 44]
        for total in (1, 21, 22, 23, 100):
            starts = vd.window_starts(total)
            assert starts[-1] + vd.INFER_LEN <= vd.padded_length(total)


class TestInputSize:
    def test_a_square_frame_uses_the_model_size(self):
        assert vd.model_input_size(518, 518) == (518, 518)

    def test_sizes_are_multiples_of_the_patch(self):
        for height, width in ((540, 960), (720, 1280), (1080, 1920), (480, 640), (544, 960)):
            new_h, new_w = vd.model_input_size(height, width)
            assert new_h % 14 == 0 and new_w % 14 == 0

    def test_the_short_side_reaches_518(self):
        new_h, new_w = vd.model_input_size(480, 640)
        assert new_h >= 518 and new_w >= 518 * 640 / 480 - 14

    def test_very_wide_frames_get_a_smaller_input_to_save_memory(self):
        normal = vd.model_input_size(544, 960)
        wide = vd.model_input_size(400, 1280)
        assert wide[0] * wide[1] < normal[0] * normal[1] * 1.2
        assert wide[0] < 518

    def test_prepare_frame_normalises_and_orders_channels(self):
        frame = np.full((20, 30, 3), 255, dtype=np.uint8)
        out = vd.prepare_frame(frame, (28, 42))
        assert out.shape == (3, 28, 42) and out.dtype == np.float32
        np.testing.assert_allclose(out[0], (1.0 - 0.485) / 0.229, rtol=1e-4)
        np.testing.assert_allclose(out[2], (1.0 - 0.406) / 0.225, rtol=1e-4)


class TestAlignment:
    def test_scale_and_shift_are_recovered(self):
        target = np.random.default_rng(0).uniform(0.1, 1.0, 500)
        scale, shift = vd.solve_scale_and_shift(target * 0.5 - 0.1, target)
        assert scale == pytest.approx(2.0, rel=1e-6)
        assert shift == pytest.approx(0.2, rel=1e-6)

    def test_a_flat_prediction_has_no_solution_and_falls_back(self):
        assert vd.solve_scale_and_shift(np.zeros(10), np.ones(10)) == (1.0, 0.0)

    def test_blending_ramps_from_the_first_set_to_the_second(self):
        before = [np.zeros((2, 2)) for _ in range(4)]
        after = [np.ones((2, 2)) for _ in range(4)]
        blended = vd.blend_frames(before, after)
        assert [round(float(frame[0, 0]), 3) for frame in blended] == [0.0, 0.333, 0.667, 1.0]

    def test_windows_with_a_different_scale_are_pulled_onto_the_first(self):
        truth = ramp_depth(54)
        second = [truth[kf] * 3.0 + 0.5 for kf in vd.KEYFRAMES] + [truth[32 + j] * 3.0 + 0.5 for j in range(22)]
        aligned = vd.align_windows(list(truth[:32]) + second, 54)
        assert len(aligned) == 54
        for index in range(54):
            np.testing.assert_allclose(aligned[index], truth[index], rtol=1e-3, atol=1e-3)

    def test_only_the_first_keyframes_fit_the_scale_and_the_rest_are_blended_in(self):
        truth = ramp_depth(54)
        second = [truth[kf] * 3.0 + 0.5 for kf in vd.KEYFRAMES[:2]]
        second += [truth[kf] * 3.0 + 0.5 + 10.0 for kf in vd.KEYFRAMES[2:]]
        second += [truth[32 + j] * 3.0 + 0.5 + 10.0 for j in range(22)]
        aligned = vd.align_windows(list(truth[:32]) + second, 54)
        np.testing.assert_allclose(aligned[23], truth[23], rtol=1e-6)
        np.testing.assert_allclose(aligned[24], truth[24], rtol=1e-6)
        assert np.abs(aligned[25] - truth[25]).max() > 0.1
        np.testing.assert_allclose(aligned[31], truth[31] + 10.0 / 3.0, rtol=1e-3)

    def test_the_result_never_goes_negative(self):
        windows = ramp_depth(32) + [frame * 0.2 - 5.0 for frame in ramp_depth(32)]
        assert min(float(frame.min()) for frame in vd.align_windows(windows, 54)) >= 0.0

    def test_a_single_window_is_returned_cut_to_length(self):
        windows = ramp_depth(32)
        aligned = vd.align_windows(windows, 20)
        assert len(aligned) == 20 and aligned[5] is windows[5]


class TestGuide:
    def test_depth_is_stretched_to_the_full_range(self):
        depth = np.stack(ramp_depth(4)).astype(np.float16)
        guide = vd.depth_to_guide(depth)
        assert guide.dtype == np.uint8 and guide.shape == depth.shape
        assert guide.max() == 255 and guide.min() == 0

    def test_closer_is_brighter(self):
        depth = np.zeros((1, 4, 4), dtype=np.float16)
        depth[0, :, 2:] = 1.0
        guide = vd.depth_to_guide(depth)
        assert guide[0, 0, 3] > guide[0, 0, 0]

    def test_a_flat_clip_does_not_divide_by_zero(self):
        guide = vd.depth_to_guide(np.full((2, 3, 3), 0.5, dtype=np.float16))
        assert guide.min() == guide.max()


class FakeModel:
    def __init__(self):
        self.calls = []
        self.moved = []

    def __call__(self, window):
        self.calls.append(window.clone())
        return window.mean(dim=2) + 1.0

    def cpu(self):
        self.moved.append("cpu")
        return self


class TestEstimator:
    def frames(self, total, size=(28, 42)):
        return [np.full((size[0], size[1], 3), (index * 5) % 255, dtype=np.uint8) for index in range(total)]

    def test_one_depth_map_per_frame_at_the_requested_size(self):
        estimator = vd.VideoDepthEstimator(FakeModel(), "cpu")
        depth = estimator.estimate(self.frames(30), (20, 12))
        assert depth.shape == (30, 12, 20) and depth.dtype == np.float16

    def test_the_default_size_is_the_frame_size(self):
        depth = vd.VideoDepthEstimator(FakeModel(), "cpu").estimate(self.frames(5))
        assert depth.shape == (5, 28, 42)

    def test_each_window_is_32_frames_and_inherits_ten_keyframes(self):
        model = FakeModel()
        vd.VideoDepthEstimator(model, "cpu").estimate(self.frames(45))
        assert len(model.calls) == 3
        assert all(call.shape[:2] == (1, vd.INFER_LEN) for call in model.calls)
        first, second = model.calls[0], model.calls[1]
        for slot, keyframe in enumerate(vd.KEYFRAMES):
            assert torch.equal(second[0, slot], first[0, keyframe])

    def test_progress_counts_windows(self):
        seen = []
        vd.VideoDepthEstimator(FakeModel(), "cpu").estimate(self.frames(45), None, lambda done, total: seen.append((done, total)))
        assert seen == [(1, 3), (2, 3), (3, 3)]

    def test_cancelling_stops_before_the_next_window(self):
        model = FakeModel()
        with pytest.raises(SamplingCancelled):
            vd.VideoDepthEstimator(model, "cpu").estimate(self.frames(45), None, None, lambda: len(model.calls) >= 1)
        assert len(model.calls) == 1

    def test_no_frames_is_an_error(self):
        with pytest.raises(ValueError, match="No frames"):
            vd.VideoDepthEstimator(FakeModel(), "cpu").estimate([])

    def test_release_moves_the_model_off_the_device(self):
        model = FakeModel()
        estimator = vd.VideoDepthEstimator(model, "cuda")
        estimator.release()
        assert model.moved == ["cpu"] and estimator.device == "cpu"


class TestWeights:
    def test_a_missing_file_is_named(self, tmp_path):
        with pytest.raises(vd.DepthWeightsError, match="not found"):
            vd.verify_weights(tmp_path)

    def test_a_truncated_file_is_reported(self, tmp_path):
        (tmp_path / vd.WEIGHT_FILENAME).write_bytes(b"x" * 10)
        with pytest.raises(vd.DepthWeightsError, match="incomplete or corrupted"):
            vd.verify_weights(tmp_path)

    def test_a_right_sized_but_wrong_file_is_reported(self, tmp_path, monkeypatch):
        data = b"y" * 64
        (tmp_path / vd.WEIGHT_FILENAME).write_bytes(data)
        monkeypatch.setattr(vd, "WEIGHT_SIZE", len(data))
        with pytest.raises(vd.DepthWeightsError, match="incomplete or corrupted"):
            vd.verify_weights(tmp_path)

    def test_the_right_file_passes_once(self, tmp_path, monkeypatch):
        data = b"z" * 64
        path = tmp_path / vd.WEIGHT_FILENAME
        path.write_bytes(data)
        monkeypatch.setattr(vd, "WEIGHT_SIZE", len(data))
        monkeypatch.setattr(vd, "WEIGHT_SHA256", hashlib.sha256(data).hexdigest())
        assert vd.verify_weights(tmp_path) == path

    def test_the_pinned_download_matches_the_published_checkpoint(self):
        assert vd.DEPTH_REPO == "depth-anything/Video-Depth-Anything-Small"
        assert vd.WEIGHT_FILENAME == "video_depth_anything_vits.pth"
        assert vd.WEIGHT_SIZE == 116440756
        assert len(vd.WEIGHT_SHA256) == 64


class TestVendoredNetwork:
    def test_its_parameters_match_the_published_small_checkpoint(self):
        from vendor.VideoDepthAnything import VideoDepthAnything

        expected = json.loads(KEYS.read_text(encoding="utf-8"))
        actual = {name: list(tensor.shape) for name, tensor in VideoDepthAnything().state_dict().items()}
        assert actual == expected

    def test_a_clip_goes_in_and_one_depth_map_per_frame_comes_out(self):
        from vendor.VideoDepthAnything import VideoDepthAnything

        model = VideoDepthAnything().eval()
        with torch.no_grad():
            out = model(torch.randn(1, vd.INFER_LEN, 3, 28, 42))
        assert out.shape == (1, vd.INFER_LEN, 28, 42)
        assert float(out.min()) >= 0.0

    def test_a_saved_state_dict_loads_strictly(self, tmp_path):
        from vendor.VideoDepthAnything import VideoDepthAnything

        path = tmp_path / "weights.pth"
        torch.save(VideoDepthAnything().state_dict(), path)
        assert isinstance(vd.load_model(path), VideoDepthAnything)

    def test_the_vendored_sources_carry_no_xformers_dependency(self):
        for source in Path("vendor/VideoDepthAnything").rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            assert "import xformers" not in text and "from xformers" not in text, source
