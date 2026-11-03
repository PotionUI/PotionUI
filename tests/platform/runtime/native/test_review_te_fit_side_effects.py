from __future__ import annotations

import pytest
import torch
import torch.nn as nn

from src.platform.runtime.native import engine as engine_mod
from src.platform.runtime.native.engine import NativeEngineLoader, NativeGenerator, _estimate_text_encoder_gb
from src.platform.runtime.native.memory import residency
from src.platform.runtime.native.memory.device_plan import DevicePlan
from src.platform.runtime.native.memory.partial import ModuleStreamer, plan_residency_split
from src.platform.runtime.native.memory.residency import module_size_gb, run_text_encode, run_text_encode_batch
from src.platform.runtime.native.memory.tiering import ComponentPlacement, PlacementPlan, plan_placement
from src.platform.runtime.native.kernels import fp8_ops, manual_cast

from . import _review_fit_layouts as L
from ._review_fit_layouts import CTX_GB, DEV, RESERVE_GB, Case


COMFORTABLE = (
    Case("flux1_t5_fp8_clip_l@12", 12.0, lambda log: L.flux1(log)),
    Case("klein_qwen3_8b_fp8mixed@16", 16.0, lambda log: L.qwen3_8b(log, "fp8", nvfp4_tail=12)),
    Case("krea2_qwen3vl_4b_fp8_edit_vision@12", 12.0, lambda log: L.qwen3_4b(log, "fp8", vision=True)),
    Case("qwen_image21_qwen3vl_8b_fp8@16", 16.0, lambda log: L.qwen3_8b(log, "fp8")),
    Case("qwen_image21_qwen3vl_8b_bf16_edit_vision@24", 24.0, lambda log: L.qwen3_8b(log, vision=True)),
    Case("qwen_image_qwen25vl_7b_fp8@12", 12.0, lambda log: L.qwen25_vl_7b(log)),
    Case("z_image_qwen3_4b_bf16@12", 12.0, lambda log: L.qwen3_4b(log)),
    Case("anima_qwen3_06b@8", 8.0, L.qwen3_06b),
    Case("ltx_gemma3_12b_fp8@16", 16.0, lambda log: L.gemma3_12b(log, "fp8")),
    Case("wan_umt5_xxl_bf16@16", 16.0, L.umt5_xxl),
    Case("minimax_h3_qwen3vl_32b_nvfp4@32", 32.0, L.qwen3vl_32b_nvfp4),
)

BUDGET_BAND = (
    Case("flux1_t5_fp16_clip_l@12", 12.0, lambda log: L.flux1(log, "bf16")),
    Case("klein_qwen3_8b_bf16@16", 16.0, L.qwen3_8b),
    Case("qwen_image_qwen25vl_7b_bf16@16", 16.0, lambda log: L.qwen25_vl_7b(log, "bf16")),
    Case("z_image_qwen3_4b_bf16@10", 10.0, L.qwen3_4b),
    Case("ltx_gemma3_12b_bf16@24", 24.0, L.gemma3_12b),
    Case("wan_umt5_xxl_fp8@8", 8.0, lambda log: L.umt5_xxl(log, "fp8")),
)


def _ids(case):
    return case.name


def _encode_fn(encoder):
    return lambda: encoder.encode(["a prompt"])


@pytest.fixture(autouse=True)
def _clean_state():
    residency.get_residency_registry().clear()
    L.reset_placement()
    yield
    L.reset_placement()
    residency.get_residency_registry().clear()


def _after_evict_expectation(encoder, size):
    return [
        ("ensure_free", DEV, round(size * 1.5 + RESERVE_GB, 6), round(size, 6)),
        ("dit.offload",),
        ("empty_cache",),
    ] + L.whole_move(encoder, size, free_gb=size, path="after-evict")


def _run_after_evict(monkeypatch, case, *, budget):
    log: list = []
    encoder = case.build(log)
    size = module_size_gb(encoder)
    registry = L.fake_card(monkeypatch, log, case.total_gb)
    L.intercept_streaming(monkeypatch, log)
    L.configure(None, budget)
    resident = L.ResidentModel(log)
    registry.note_resident(resident, DEV, case.total_gb - CTX_GB - size)
    log.clear()
    run_text_encode(encoder, DEV, _encode_fn(encoder))
    return log, encoder, size


@pytest.mark.parametrize("case", COMFORTABLE, ids=_ids)
def test_estimated_vram_gb_is_the_historical_figure(case):
    encoder = case.build([])
    assert _estimate_text_encoder_gb(encoder) == pytest.approx(L.historical_estimate_gb(encoder), rel=0, abs=1e-12)


def test_loader_te_estimate_is_the_historical_figure(monkeypatch):
    encoder = L.qwen3vl_32b_nvfp4([])
    monkeypatch.setattr(engine_mod, "load_text_encoder", lambda *args, **kwargs: encoder)
    model = NativeEngineLoader(device="cpu")._load_te("unused.safetensors")
    assert model.estimated_vram_gb == pytest.approx(L.historical_estimate_gb(encoder), rel=0, abs=1e-12)


@pytest.mark.parametrize("case", COMFORTABLE, ids=_ids)
def test_coresident_encode_with_the_production_budget_is_unchanged(monkeypatch, case):
    log: list = []
    encoder = case.build(log)
    size = module_size_gb(encoder)
    registry = L.fake_card(monkeypatch, log, case.total_gb)
    L.intercept_streaming(monkeypatch, log)
    L.configure(None, L.default_gpu_max_vram(case.total_gb))
    resident = L.ResidentModel(log)
    registry.note_resident(resident, DEV, 0.25)
    log.clear()
    free = case.total_gb - CTX_GB - 0.25
    assert free >= size + RESERVE_GB

    run_text_encode(encoder, DEV, _encode_fn(encoder))

    assert log == L.whole_move(encoder, size, free_gb=free, path="co-resident")


@pytest.mark.parametrize("case", COMFORTABLE, ids=_ids)
def test_after_evict_encode_with_the_production_budget_is_unchanged(monkeypatch, case):
    budget = L.default_gpu_max_vram(case.total_gb)
    probe = case.build([])
    assert module_size_gb(probe) + L.dequant_peak_gb(probe) + RESERVE_GB <= budget

    log, encoder, size = _run_after_evict(monkeypatch, case, budget=budget)

    assert log == _after_evict_expectation(encoder, size)


@pytest.mark.parametrize("case", COMFORTABLE[:3], ids=_ids)
def test_batch_window_with_the_production_budget_is_unchanged(monkeypatch, case):
    log: list = []
    encoder = case.build(log)
    size = module_size_gb(encoder)
    L.fake_card(monkeypatch, log, case.total_gb)
    L.intercept_streaming(monkeypatch, log)
    L.configure(None, L.default_gpu_max_vram(case.total_gb))

    run_text_encode_batch(encoder, DEV, [_encode_fn(encoder), _encode_fn(encoder)])

    free = case.total_gb - CTX_GB
    assert log == L.whole_move(encoder, size, free_gb=free, path="co-resident", count=2)


@pytest.mark.parametrize("case", BUDGET_BAND, ids=_ids)
def test_after_evict_encode_that_fits_freed_vram_keeps_the_whole_move(monkeypatch, case):
    budget = L.default_gpu_max_vram(case.total_gb)
    probe = case.build([])
    weights = module_size_gb(probe)
    assert weights + RESERVE_GB <= case.total_gb - CTX_GB
    assert weights + L.dequant_peak_gb(probe) + RESERVE_GB > budget

    log, encoder, size = _run_after_evict(monkeypatch, case, budget=budget)

    assert log == _after_evict_expectation(encoder, size)


@pytest.mark.parametrize("case", BUDGET_BAND, ids=_ids)
def test_coresident_step_ignores_the_budget_for_the_same_encoders(monkeypatch, case):
    log: list = []
    encoder = case.build(log)
    size = module_size_gb(encoder)
    L.fake_card(monkeypatch, log, case.total_gb)
    L.intercept_streaming(monkeypatch, log)
    L.configure(None, L.default_gpu_max_vram(case.total_gb))

    run_text_encode(encoder, DEV, _encode_fn(encoder))

    assert log == L.whole_move(encoder, size, free_gb=case.total_gb - CTX_GB, path="co-resident")


@pytest.mark.parametrize("case", BUDGET_BAND[:2], ids=_ids)
def test_mode_gpu_keeps_the_whole_move_in_the_budget_band(monkeypatch, case):
    log: list = []
    encoder = case.build(log)
    size = module_size_gb(encoder)
    registry = L.fake_card(monkeypatch, log, case.total_gb)
    L.intercept_streaming(monkeypatch, log)
    L.configure("gpu", L.default_gpu_max_vram(case.total_gb))
    resident = L.ResidentModel(log)
    registry.note_resident(resident, DEV, case.total_gb - CTX_GB - size)
    log.clear()

    run_text_encode(encoder, DEV, _encode_fn(encoder))

    assert log == _after_evict_expectation(encoder, size)


DEQUANT_BAND = (
    Case("qwen_image21_qwen3vl_8b_fp8", 0.0, lambda log: L.qwen3_8b(log, "fp8")),
    Case("klein_qwen3_8b_fp8mixed", 0.0, lambda log: L.qwen3_8b(log, "fp8", nvfp4_tail=12)),
    Case("minimax_h3_qwen3vl_32b_nvfp4", 0.0, L.qwen3vl_32b_nvfp4),
)


@pytest.mark.parametrize("case", DEQUANT_BAND, ids=_ids)
def test_after_evict_quantised_encoder_within_the_reserve_keeps_the_whole_move(monkeypatch, case):
    probe = case.build([])
    weights, peak = module_size_gb(probe), L.dequant_peak_gb(probe)
    assert peak > 0.0
    total = weights + RESERVE_GB + peak / 2 + CTX_GB
    sized = Case(case.name, total + 0.01, case.builder)

    log, encoder, size = _run_after_evict(monkeypatch, sized, budget=None)

    assert log == _after_evict_expectation(encoder, size)


@pytest.mark.parametrize("case", COMFORTABLE[:4] + BUDGET_BAND[:2], ids=_ids)
def test_after_evict_encoder_that_fits_only_without_the_reserve_keeps_the_whole_move(monkeypatch, case):
    weights = module_size_gb(case.build([]))
    sized = Case(case.name, weights + RESERVE_GB / 2 + CTX_GB, case.builder)

    log, encoder, size = _run_after_evict(monkeypatch, sized, budget=None)

    assert log == _after_evict_expectation(encoder, size)


def test_cpu_device_never_moves_the_encoder_whatever_the_mode(monkeypatch):
    log: list = []
    encoder = L.qwen3_8b(log)
    size = module_size_gb(encoder)
    L.fake_card(monkeypatch, log, 16.0)
    L.intercept_streaming(monkeypatch, log)
    L.configure("stream", 14.0)

    run_text_encode(encoder, "cpu", _encode_fn(encoder))

    assert log == [("mark", "cpu-fallback", "cpu", round(size, 6), None, None, None), ("encode", "cpu")]


def test_cuda_device_without_cuda_encodes_on_cpu_whatever_the_mode(monkeypatch):
    log: list = []
    encoder = L.flux1(log)
    size = module_size_gb(encoder)
    L.fake_card(monkeypatch, log, 16.0, cuda=False)
    L.intercept_streaming(monkeypatch, log)
    L.configure("stream", 14.0)

    run_text_encode_batch(encoder, DEV, [_encode_fn(encoder)])

    assert log == [("mark", "cpu-fallback", "cpu", round(size, 6), None, None, 1), ("encode", "cpu")]


class _Spec:
    family = "fake"
    variant = "fake"
    latent_format: dict = {}

    def __init__(self, guidance=None):
        self.sampling_settings = {"guidance": guidance} if guidance else {}


class _Model:
    def __init__(self, kind, gb, guidance=None):
        self.kind = kind
        self.spec = _Spec(guidance) if kind == "dit" else None
        self.estimated_vram_gb = gb
        self.quant_format = None
        self.compute_dtype = torch.float32
        self.module = object()

    def move_to(self, device):
        pass

    def offload(self):
        pass


def _generator(encoder, device_plan, *, vram_gb=None, placement=None, guidance=None):
    return NativeGenerator(
        _Model("dit", 4.0, guidance), encoder, _Model("vae", 0.3), device_plan,
        vram_gb=vram_gb, placement=placement,
    )


_UP = [("encoder.to", DEV), ("module.to", DEV)]
_DOWN = [("encoder.to", "cpu"), ("module.to", "cpu")]


def test_encode_prompt_on_a_cpu_plan_is_unchanged_whatever_the_mode(monkeypatch):
    log: list = []
    encoder = L.qwen3_8b(log)
    L.fake_card(monkeypatch, log, 16.0)
    L.intercept_streaming(monkeypatch, log)
    L.configure("stream", 14.0)
    gen = _generator(encoder, DevicePlan("cpu", "cpu", "cpu"), guidance="cfg")

    gen.encode_prompt("a prompt", "a negative")

    assert log == [("encoder.to", "cpu"), ("module.to", "cpu"), ("encode", "cpu"), ("encode", "cpu")]


def test_encode_prompt_without_a_budget_is_unchanged(monkeypatch):
    log: list = []
    encoder = L.qwen3_8b(log)
    L.fake_card(monkeypatch, log, 16.0)
    L.intercept_streaming(monkeypatch, log)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: False)
    gen = _generator(encoder, DevicePlan(DEV, DEV, DEV))

    gen.encode_prompt("a prompt")

    assert log == _UP + [("encode", DEV)] + _DOWN


def test_encode_prompt_with_an_explicit_placement_moves_whole_without_eviction(monkeypatch):
    log: list = []
    encoder = L.qwen3_8b(log)
    registry = L.fake_card(monkeypatch, log, 16.0)
    L.intercept_streaming(monkeypatch, log)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: True)
    resident = L.ResidentModel(log, "foreign")
    registry.note_resident(resident, DEV, 8.0)
    log.clear()
    explicit = plan_placement(16.0, {"dit": 4.0, "text_encoder": 14.1, "vae": 0.3}, None, DevicePlan(DEV, DEV, DEV))
    gen = _generator(encoder, DevicePlan(DEV, DEV, DEV), vram_gb=16.0, placement=explicit)

    gen.encode_prompt("a prompt")

    assert log == _UP + [("encode", DEV)] + _DOWN


def test_encode_prompt_fitting_encoder_with_negative_is_unchanged(monkeypatch):
    log: list = []
    encoder = L.qwen3_8b(log, "fp8")
    L.fake_card(monkeypatch, log, 24.0)
    L.intercept_streaming(monkeypatch, log)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: True)
    gen = _generator(encoder, DevicePlan(DEV, DEV, DEV), vram_gb=24.0, guidance="cfg")

    gen.encode_prompt("a prompt", "a negative")

    assert log == _UP + [("encode", DEV), ("encode", DEV)] + _DOWN


def test_release_gpu_offloads_a_whole_encoder_as_before(monkeypatch):
    log: list = []
    encoder = L.qwen3_8b(log)
    L.fake_card(monkeypatch, log, 16.0)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: False)
    gen = _generator(encoder, DevicePlan(DEV, DEV, DEV))

    gen.release_gpu()

    assert log == _DOWN


def test_encode_prompt_encoder_that_fits_live_free_vram_moves_whole(monkeypatch):
    log: list = []
    encoder = L.qwen3_8b(log)
    weights = module_size_gb(encoder)
    total = weights + 0.5 + CTX_GB
    L.fake_card(monkeypatch, log, total)
    L.intercept_streaming(monkeypatch, log)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: True)
    gen = _generator(encoder, DevicePlan(DEV, DEV, DEV), vram_gb=total)

    gen.encode_prompt("a prompt")

    assert log == _UP + [("encode", DEV)] + _DOWN


def _small_nvfp4(in_f, out_f):
    layer = fp8_ops.Linear(in_f, out_f, bias=False, dtype=torch.bfloat16)
    layer._is_nvfp4 = True
    layer.nvfp4_packed = torch.zeros(out_f, in_f // 2, dtype=torch.uint8)
    layer.nvfp4_scale = torch.ones(out_f, in_f // 16, dtype=torch.float32)
    layer.nvfp4_weight_block_scale = torch.ones(128, 4, dtype=torch.float8_e4m3fn)
    layer.nvfp4_weight_global_scale = torch.ones((), dtype=torch.float32)
    layer.weight = None
    return layer


def _dit_like():
    root = nn.Module()
    root.embed = manual_cast.Embedding(64, 32, dtype=torch.bfloat16)
    root.blocks = nn.ModuleList()
    for _ in range(4):
        block = nn.Module()
        block.attn = manual_cast.Linear(32, 32, bias=True, dtype=torch.bfloat16)
        block.mlp = _small_nvfp4(32, 64)
        block.out = manual_cast.Linear(64, 32, bias=False, dtype=torch.bfloat16)
        root.blocks.append(block)
    return root


def test_dit_partial_residency_keeps_nvfp4_and_embedding_fixed():
    root = _dit_like()

    plan = plan_residency_split(root, 0.0)

    names = set(plan.streamed_names)
    assert names == {f"blocks.{i}.{leaf}" for i in range(4) for leaf in ("attn", "out")}


def test_dit_streamer_apply_and_teardown_leave_nvfp4_leaves_untouched():
    root = _dit_like()
    plan = plan_residency_split(root, 0.0)
    flags = {name: getattr(m, "comfy_cast_weights", None) for name, m in root.named_modules()}
    buffers = {name: dict(m._buffers) for name, m in root.named_modules()}
    streamer = ModuleStreamer(root, prefetch=True)

    streamer.apply("cpu", plan, pin=False, non_blocking=True)

    for name, module in root.named_modules():
        assert not module._forward_pre_hooks, name
        assert not module._forward_hooks, name
        if name.endswith("mlp"):
            assert module.comfy_cast_weights == flags[name]
            assert all(module._buffers[k] is v for k, v in buffers[name].items())
    assert streamer.prefetcher is None

    streamer.teardown()

    assert {name: getattr(m, "comfy_cast_weights", None) for name, m in root.named_modules()} == flags
    for name, module in root.named_modules():
        assert not module._forward_pre_hooks and not module._forward_hooks, name
        if name.endswith("mlp"):
            assert all(not b.is_pinned() for b in module._buffers.values() if b is not None)


@pytest.mark.parametrize("vram", [6.0, 8.0, 12.0, 16.0, 24.0, 32.0])
@pytest.mark.parametrize("sizes", [
    {"dit": 11.9, "text_encoder": 9.2, "vae": 0.2},
    {"dit": 20.0, "text_encoder": 21.9, "vae": 0.5},
    {"dit": 0.0, "text_encoder": 0.0, "vae": 0.0},
])
def test_sampling_plan_without_a_text_encoder_mode_is_the_historical_plan(vram, sizes):
    plan = plan_placement(vram, sizes, None, DevicePlan(DEV, DEV, DEV))

    assert plan.text_encoder == ComponentPlacement(DEV, "standard", plan.text_encoder.resident)
    assert getattr(plan.text_encoder, "streamed", False) is False
    assert getattr(plan.text_encoder, "stream_budget_gb", None) is None
    assert isinstance(plan, PlacementPlan)
