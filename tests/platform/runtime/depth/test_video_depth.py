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


def reference_align(depth_list):
    align_len = vd.OVERLAP - vd.INTERP_LEN
    kf_align = list(vd.KEYFRAMES[:align_len])
    aligned, reference = [], []
    for frame_id in range(0, len(depth_list), vd.INFER_LEN):
        if not aligned:
            aligned += depth_list[: vd.INFER_LEN]
            reference += [depth_list[frame_id + kf] for kf in kf_align]
            continue
        current = [depth_list[frame_id + i] for i in range(len(kf_align))]
        scale, shift = vd.solve_scale_and_shift(np.concatenate(current), np.concatenate(reference))
        before = aligned[-vd.INTERP_LEN:]
        after = [np.maximum(depth_list[frame_id + i] * scale + shift, 0) for i in range(align_len, vd.OVERLAP)]
        aligned[-vd.INTERP_LEN:] = vd.blend_frames(before, after)
        for i in range(vd.OVERLAP, vd.INFER_LEN):
            aligned.append(np.maximum(depth_list[frame_id + i] * scale + shift, 0))
        reference = reference[:1] + [np.maximum(depth_list[frame_id + kf] * scale + shift, 0) for kf in kf_align[1:]]
    return aligned


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

    def test_the_aligner_matches_the_whole_clip_reference_implementation(self):
        rng = np.random.default_rng(3)
        windows = [rng.uniform(0.1, 2.0, (6, 8)).astype(np.float32) * (1 + index // 32) for index in range(32 * 4)]
        expected = reference_align(windows)
        actual = vd.align_windows(windows, len(expected))
        assert len(actual) == len(expected) == 32 + 22 * 3
        for got, want in zip(actual, expected):
            np.testing.assert_allclose(got, want, rtol=1e-5, atol=1e-6)

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

    def to(self, device):
        self.moved.append(device)
        return self


class CountingFrames:
    def __init__(self, total, size=(28, 42)):
        self.total = total
        self.size = size
        self.pulled = 0

    def __iter__(self):
        for index in range(self.total):
            self.pulled += 1
            yield np.full((self.size[0], self.size[1], 3), (index * 5) % 255, dtype=np.uint8)


class TestEstimator:
    def frames(self, total, size=(28, 42)):
        return list(CountingFrames(total, size))

    @pytest.mark.parametrize("total", [1, 5, 22, 23, 31, 32, 33, 44, 45, 66, 67])
    def test_one_depth_map_per_frame_for_any_clip_length(self, total):
        depth = vd.VideoDepthEstimator(FakeModel(), "cpu").estimate(self.frames(total))
        assert depth.shape == (total, 28, 42) and depth.dtype == np.float16

    def test_the_requested_size_is_used(self):
        depth = vd.VideoDepthEstimator(FakeModel(), "cpu").estimate(self.frames(30), (20, 12))
        assert depth.shape == (30, 12, 20)

    @pytest.mark.parametrize("total,windows", [(1, 1), (22, 1), (23, 2), (32, 2), (45, 3), (46, 3), (67, 4)])
    def test_a_window_is_run_only_while_frames_remain(self, total, windows):
        model = FakeModel()
        vd.VideoDepthEstimator(model, "cpu").estimate(self.frames(total))
        assert len(model.calls) == windows
        assert all(call.shape[:2] == (1, vd.INFER_LEN) for call in model.calls)

    def test_each_later_window_inherits_ten_keyframes_from_the_one_before(self):
        model = FakeModel()
        vd.VideoDepthEstimator(model, "cpu").estimate(self.frames(45))
        first, second = model.calls[0], model.calls[1]
        for slot, keyframe in enumerate(vd.KEYFRAMES):
            assert torch.equal(second[0, slot], first[0, keyframe])

    def test_frames_are_pulled_one_window_at_a_time_and_results_stream_out(self):
        source = CountingFrames(120)
        stream = vd.VideoDepthEstimator(FakeModel(), "cpu").iter_depth(source)
        first = next(stream)
        assert first.dtype == np.float16
        assert source.pulled == vd.INFER_LEN
        for _ in range(vd.INFER_LEN - vd.INTERP_LEN - 1):
            next(stream)
        assert source.pulled == vd.INFER_LEN
        next(stream)
        assert source.pulled == vd.INFER_LEN + (vd.INFER_LEN - vd.OVERLAP)
        assert sum(1 for _ in stream) + vd.INFER_LEN - vd.INTERP_LEN + 1 == 120

    def test_depth_is_never_held_as_float32_beyond_a_window(self):
        results = list(vd.VideoDepthEstimator(FakeModel(), "cpu").iter_depth(CountingFrames(90)))
        assert {item.dtype for item in results} == {np.dtype(np.float16)}

    def test_progress_reports_the_frames_read_so_far(self):
        seen = []
        vd.VideoDepthEstimator(FakeModel(), "cpu").estimate(self.frames(45), None, seen.append)
        assert seen == [32, 45, 45]

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

    def test_unload_lets_the_lifecycle_free_the_weights(self):
        model = FakeModel()
        estimator = vd.VideoDepthEstimator(model, "cpu")
        estimator.unload()
        assert estimator.model is None and model.moved == ["cpu"]

    def test_activate_picks_a_device_and_moves_the_model(self):
        model = FakeModel()
        vd.VideoDepthEstimator(model, "cpu").activate()
        assert model.moved == ["cuda" if torch.cuda.is_available() else "cpu"]

    def test_it_declares_a_vram_estimate_for_the_lifecycle(self):
        assert vd.VideoDepthEstimator(FakeModel()).estimated_vram_gb > 0


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
