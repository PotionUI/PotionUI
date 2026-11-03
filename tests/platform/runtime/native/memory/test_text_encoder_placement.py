from __future__ import annotations

import pytest

from src.platform.runtime.native.engine import _estimate_text_encoder_gb
from src.platform.runtime.native.memory.device_plan import DevicePlan
from src.platform.runtime.native.memory.residency import module_size_gb
from src.platform.runtime.native.memory.text_encoder import (
    DEQUANT_BYTES_PER_ELEMENT,
    TEXT_ENCODER_PLACEMENT_MODES,
    configure_text_encoder_placement,
    encoder_gb_on_device,
    leaf_quant_format,
    normalize_text_encoder_placement,
    reset_text_encoder_placement,
    text_encoder_footprint,
    text_encoder_placement_mode,
    text_encoder_placement_settings,
)
from src.platform.runtime.native.memory.tiering import (
    ComponentPlacement,
    plan_placement,
    plan_text_encoder_placement,
)

from .._te_layouts import (
    FamilyEncoder,
    RecordingModule,
    bf16_linear,
    fp8_linear,
    int8_embedding,
    int8_linear,
    minimax_h3_te,
    nvfp4_linear,
)

GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _reset_settings():
    reset_text_encoder_placement()
    yield
    reset_text_encoder_placement()


def _single_leaf(layer) -> FamilyEncoder:
    root = RecordingModule()
    root.layer = layer
    return FamilyEncoder(root, [], "probe")


def test_nvfp4_leaf_counts_packed_codes_and_both_scales():
    out_f, in_f = 25600, 5120
    fp = text_encoder_footprint(_single_leaf(nvfp4_linear(in_f, out_f)))
    packed = out_f * in_f // 2
    natural_scale = out_f * (in_f // 16) * 4
    block_scale = out_f * (in_f // 16)
    expected = packed + natural_scale + block_scale + 4
    assert fp.weights_gb * GB == pytest.approx(expected)
    assert fp.weights_gb * GB / (out_f * in_f) == pytest.approx(0.8125, abs=1e-6)
    assert fp.quant_formats == ("nvfp4",)


def test_nvfp4_dequant_peak_is_ten_bytes_per_weight_element():
    fp = text_encoder_footprint(_single_leaf(nvfp4_linear(5120, 25600)))
    assert fp.dequant_peak_gb * GB == pytest.approx(25600 * 5120 * DEQUANT_BYTES_PER_ELEMENT["nvfp4"])
    assert fp.dequant_peak_gb == pytest.approx(1.22, abs=0.01)
    assert fp.encode_gb == pytest.approx(fp.weights_gb + fp.dequant_peak_gb)


def test_int8_and_fp8_leaves_count_one_byte_per_element_plus_scale():
    int8 = text_encoder_footprint(_single_leaf(int8_linear(4096, 1024)))
    fp8 = text_encoder_footprint(_single_leaf(fp8_linear(4096, 1024)))
    assert int8.weights_gb * GB == 4096 * 1024 + 1024 * 4
    assert fp8.weights_gb * GB == 4096 * 1024 + 4
    assert int8.dequant_peak_gb * GB == 4096 * 1024 * 8
    assert fp8.dequant_peak_gb * GB == 4096 * 1024 * 8
    assert int8.quant_formats == ("int8",) and fp8.quant_formats == ("fp8",)


def test_plain_bf16_encoder_has_no_dequant_transient():
    fp = text_encoder_footprint(_single_leaf(bf16_linear(4096, 4096)))
    assert fp.weights_gb * GB == 4096 * 4096 * 2
    assert fp.dequant_peak_gb == 0.0
    assert fp.quant_formats == ()


def test_int8_embedding_is_counted_from_its_side_buffers():
    fp = text_encoder_footprint(_single_leaf(int8_embedding(151936, 5120)))
    assert fp.weights_gb * GB == 151936 * 5120 + 151936 * 4
    assert leaf_quant_format(int8_embedding(16, 8)) is None


def test_shared_tensor_is_counted_once_across_a_composite():
    class _Part:
        def __init__(self, module):
            self.module = module

    class _Composite:
        def __init__(self, a, b):
            self.t5 = _Part(a)
            self.clip_l = _Part(b)

    shared = RecordingModule()
    shared.layer = bf16_linear(64, 64)
    fp = text_encoder_footprint(_Composite(shared, shared))
    assert fp.weights_gb * GB == 64 * 64 * 2


def test_no_module_means_no_footprint():
    assert text_encoder_footprint(object()) is None
    assert text_encoder_footprint(FamilyEncoder(RecordingModule(), [], "empty")) is None


def test_minimax_h3_te_is_honestly_bigger_than_its_checkpoint():
    te = minimax_h3_te()
    fp = text_encoder_footprint(te)
    assert fp.weights_gb > 18.5
    assert fp.weights_gb / 14.6 > 1.25
    assert fp.dequant_peak_gb == pytest.approx(1.22, abs=0.01)
    assert set(fp.quant_formats) == {"nvfp4"}
    assert _estimate_text_encoder_gb(te) == pytest.approx(fp.weights_gb)
    assert module_size_gb(te) == pytest.approx(fp.weights_gb)


def test_encoder_gb_on_device_counts_only_that_device():
    te = _single_leaf(bf16_linear(64, 64))
    assert encoder_gb_on_device(te, "cuda:0") == 0.0
    assert encoder_gb_on_device(te, "meta") * GB == 64 * 64 * 2


def test_modes_and_default():
    assert TEXT_ENCODER_PLACEMENT_MODES == ("auto", "gpu", "stream")
    assert text_encoder_placement_mode() == "auto"
    assert text_encoder_placement_settings().vram_budget_gb is None


@pytest.mark.parametrize("raw,expected", [(None, "auto"), ("", "auto"), (" GPU ", "gpu"), ("Stream", "stream")])
def test_mode_normalisation(raw, expected):
    assert normalize_text_encoder_placement(raw) == expected


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="text_encoder_placement"):
        normalize_text_encoder_placement("cpu")


def test_configure_sets_mode_and_backend_budget():
    configure_text_encoder_placement("stream", vram_budget_gb=10)
    assert text_encoder_placement_settings().mode == "stream"
    assert text_encoder_placement_settings().vram_budget_gb == 10.0
    configure_text_encoder_placement(None, vram_budget_gb=0)
    assert text_encoder_placement_settings().mode == "auto"
    assert text_encoder_placement_settings().vram_budget_gb is None


def test_auto_keeps_a_fitting_encoder_whole():
    plan = plan_text_encoder_placement(16.0, 8.0, device="cuda:0", dequant_peak_gb=0.5, working_headroom_gb=1.0)
    assert plan == ComponentPlacement("cuda:0", "standard", True)
    assert plan.streamed is False and plan.stream_budget_gb is None


def test_auto_streams_an_encoder_that_does_not_fit_with_the_remaining_budget():
    plan = plan_text_encoder_placement(10.0, 19.4, device="cuda:0", dequant_peak_gb=1.22, working_headroom_gb=1.0)
    assert plan.streamed is True
    assert plan.stream_budget_gb == pytest.approx(10.0 - 1.0 - 1.22)


def test_dequant_peak_never_shrinks_the_fit_region_it_only_sizes_the_resident_share():
    fits = plan_text_encoder_placement(10.0, 9.0, device="cuda:0", dequant_peak_gb=1.2, working_headroom_gb=1.0)
    streamed = plan_text_encoder_placement(10.0, 9.1, device="cuda:0", dequant_peak_gb=1.2, working_headroom_gb=1.0)
    assert fits.streamed is False
    assert streamed.streamed is True
    assert streamed.stream_budget_gb == pytest.approx(10.0 - 1.0 - 1.2)


def test_resident_cap_never_decides_it_only_bounds_the_resident_share():
    fits = plan_text_encoder_placement(15.5, 14.1, device="cuda:0", working_headroom_gb=1.0, resident_cap_gb=14.0)
    streamed = plan_text_encoder_placement(10.0, 19.2, device="cuda:0", working_headroom_gb=1.0, resident_cap_gb=8.0)
    assert fits.streamed is False
    assert streamed.stream_budget_gb == pytest.approx(7.0)


def test_fit_headroom_can_differ_from_the_sizing_headroom():
    whole = plan_text_encoder_placement(14.6, 14.1, device="cuda:0", working_headroom_gb=1.0, fit_headroom_gb=0.0)
    streamed = plan_text_encoder_placement(14.0, 14.1, device="cuda:0", working_headroom_gb=1.0, fit_headroom_gb=0.0)
    assert whole.streamed is False
    assert streamed.streamed is True
    assert streamed.stream_budget_gb == pytest.approx(13.0)


def test_gpu_mode_never_streams():
    plan = plan_text_encoder_placement(4.0, 19.4, device="cuda:0", mode="gpu", dequant_peak_gb=1.2)
    assert plan.streamed is False


def test_stream_mode_always_streams():
    plan = plan_text_encoder_placement(32.0, 4.0, device="cuda:0", mode="stream", working_headroom_gb=1.0)
    assert plan.streamed is True
    assert plan.stream_budget_gb == pytest.approx(31.0)


@pytest.mark.parametrize("available,device", [(None, "cuda:0"), (4.0, "cpu")])
def test_no_budget_or_no_cuda_never_streams(available, device):
    assert plan_text_encoder_placement(available, 19.4, device=device, mode="stream").streamed is False


def test_budget_never_goes_negative():
    plan = plan_text_encoder_placement(1.5, 19.4, device="cuda:0", dequant_peak_gb=1.2, working_headroom_gb=1.0)
    assert plan.stream_budget_gb == 0.0


def test_unknown_planner_mode_is_rejected():
    with pytest.raises(ValueError):
        plan_text_encoder_placement(10.0, 1.0, device="cuda:0", mode="maybe")


_DP = DevicePlan("cuda:0", "cuda:0", "cuda:0")
_SIZES = {"dit": 22.0, "text_encoder": 19.4, "vae": 0.5}


@pytest.mark.parametrize("vram", [8.0, 12.0, 24.0, 32.0, 48.0])
def test_plan_placement_without_a_mode_is_the_sampling_plan_it_always_was(vram):
    baseline = plan_placement(vram, _SIZES, None, _DP, working_headroom_gb=1.4)
    assert baseline.text_encoder.streamed is False
    assert baseline.text_encoder.stream_budget_gb is None
    assert baseline == plan_placement(vram, _SIZES, None, _DP, working_headroom_gb=1.4, text_encoder_mode=None)


def test_plan_placement_streams_only_the_encode_phase_field():
    sampling = plan_placement(12.0, _SIZES, None, _DP, working_headroom_gb=1.4)
    encode = plan_placement(
        12.0, _SIZES, None, _DP, working_headroom_gb=1.4,
        text_encoder_mode="auto", text_encoder_available_gb=10.0,
        text_encoder_dequant_gb=1.22, text_encoder_headroom_gb=1.0,
    )
    assert encode.text_encoder.streamed is True
    assert encode.text_encoder.stream_budget_gb == pytest.approx(7.78)
    assert encode.text_encoder.resident == sampling.text_encoder.resident
    assert (encode.dit, encode.vae, encode.tier, encode.vae_tiling) == (
        sampling.dit, sampling.vae, sampling.tier, sampling.vae_tiling,
    )


def test_plan_placement_auto_with_a_fitting_encoder_matches_the_sampling_plan():
    sizes = {"dit": 9.0, "text_encoder": 7.2, "vae": 0.3}
    sampling = plan_placement(32.0, sizes, None, _DP, working_headroom_gb=1.4)
    encode = plan_placement(32.0, sizes, None, _DP, working_headroom_gb=1.4, text_encoder_mode="auto")
    assert encode == sampling


def test_plan_placement_text_encoder_on_its_own_gpu_needs_its_own_budget():
    spill = DevicePlan("cuda:0", "cuda:1", "cuda:0")
    unknown = plan_placement(12.0, _SIZES, None, spill, text_encoder_mode="stream")
    known = plan_placement(12.0, _SIZES, None, spill, text_encoder_mode="stream", text_encoder_available_gb=20.0)
    assert unknown.text_encoder.streamed is False
    assert known.text_encoder.streamed is True
    assert known.text_encoder.device == "cuda:1"
