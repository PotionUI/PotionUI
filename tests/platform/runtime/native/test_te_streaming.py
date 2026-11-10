from __future__ import annotations

import pytest
import torch
import torch.nn as nn

from src.platform.observability import profiling
from src.platform.runtime import cuda_allocator
from src.platform.runtime.native import engine as engine_mod
from src.platform.runtime.native.engine import NativeGenerator, NativeModel, TextEncoderStreaming
from src.platform.runtime.native.memory import residency
from src.platform.runtime.native.memory.device_plan import DevicePlan
from src.platform.runtime.native.memory.partial import ModuleStreamer, plan_residency_split
from src.platform.runtime.native.memory.residency import get_residency_registry, module_size_gb, run_text_encode
from src.platform.runtime.native.memory.text_encoder import (
    configure_text_encoder_placement,
    reset_text_encoder_placement,
    text_encoder_footprint,
)
from src.platform.runtime.native.memory.tiering import ComponentPlacement, PlacementPlan
from src.platform.runtime.native.kernels import fp8_ops, manual_cast

from ._nvfp4_ref import default_tensor_scale, quantize_nvfp4
from ._te_layouts import FAMILIES, FamilyEncoder, RecordingModule, minimax_h3_module, minimax_h3_te, transformer

_DEV = "cuda:0"
GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _clean_state():
    reset_text_encoder_placement()
    get_residency_registry().clear()
    yield
    reset_text_encoder_placement()
    get_residency_registry().clear()


class _Recorder:
    def __init__(self, log: list) -> None:
        self._log = log

    def mark(self, name, **fields):
        if name == "te.encode":
            self._log.append(("mark", fields.get("path"), fields.get("budget_gb")))


def _fake_card(monkeypatch, log: list, *, capacity_gb: float, others_gb: float):
    registry = get_residency_registry()

    def free(device):
        return capacity_gb - others_gb - registry.resident_gb(device)

    def note_offloaded(model, _orig=registry.note_offloaded):
        log.append(("note_offloaded", type(model).__name__))
        _orig(model)

    monkeypatch.setattr(registry, "note_offloaded", note_offloaded)
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(residency.torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(residency, "free_vram_gb", free)
    monkeypatch.setattr(engine_mod, "free_vram_gb", free)
    monkeypatch.setattr(profiling, "get_profiler", lambda: _Recorder(log))
    return registry, free


def _streamed_oom() -> BaseException:
    return torch.cuda.OutOfMemoryError("fake streamed OOM")


def _wsl_driver_oom() -> BaseException:
    return RuntimeError("CUDA driver error: device not ready")


class _FakeStreaming:
    log: list = []
    oom_budgets: set = set()
    make_oom = _streamed_oom

    def __init__(self, encoder) -> None:
        self.encoder = encoder
        self.resident_gb = 0.0
        self.streamed_gb = 0.0
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def apply(self, device, budget_gb) -> None:
        _FakeStreaming.log.append(("stream.apply", str(device), round(float(budget_gb), 3)))
        if round(float(budget_gb), 3) in _FakeStreaming.oom_budgets:
            raise _FakeStreaming.make_oom()
        self.resident_gb = budget_gb
        self._active = True

    def teardown(self) -> None:
        _FakeStreaming.log.append(("stream.teardown",))
        self._active = False


@pytest.fixture
def fake_streaming(monkeypatch):
    log: list = []
    _FakeStreaming.log = log
    _FakeStreaming.oom_budgets = set()
    monkeypatch.setattr(_FakeStreaming, "make_oom", _streamed_oom)
    monkeypatch.setattr(engine_mod, "TextEncoderStreaming", _FakeStreaming)
    return log


class _Resident:
    def __init__(self, log: list, name: str) -> None:
        self._log = log
        self._name = name

    def offload(self) -> None:
        self._log.append((f"{self._name}.offload",))


def _h3_budget(available: float) -> float:
    fp = text_encoder_footprint(minimax_h3_te())
    return round(available - 1.0 - fp.dequant_peak_gb, 3)


def test_pipe_path_streams_minimax_h3_te_on_a_12gb_card(monkeypatch, fake_streaming):
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    out = run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert out["context"].shape == (1, 4, 8)
    assert ("encoder.to", _DEV) not in log
    assert log == [
        ("note_offloaded", "FamilyEncoder"),
        ("stream.apply", _DEV, _h3_budget(10.0)),
        ("encode", "cpu"),
        ("stream.teardown",),
        ("mark", "streamed", pytest.approx(_h3_budget(10.0), abs=1e-3)),
    ]


def test_pipe_path_streams_an_int8_gemma_te_larger_than_a_12gb_card_instead_of_moving_it_whole(monkeypatch, fake_streaming):
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=1.1)
    module = transformer(
        hidden=3840, inter=15360, q=4096, kv=2048, layers=48, vocab=262144, formats=["int8"] * 48, log=log,
    )
    encoder = FamilyEncoder(module, log, "gemma_12b_int8")
    assert module_size_gb(encoder) > 10.9
    budget = round(10.9 - 1.0 - text_encoder_footprint(encoder).dequant_peak_gb, 3)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]), reserve_gb=1.0)

    assert log == [
        ("note_offloaded", "FamilyEncoder"),
        ("stream.apply", _DEV, budget),
        ("encode", "cpu"),
        ("stream.teardown",),
        ("mark", "streamed", pytest.approx(budget, abs=1e-3)),
    ]


def test_pipe_path_budget_respects_the_backend_vram_cap(monkeypatch, fake_streaming):
    configure_text_encoder_placement("auto", vram_budget_gb=8)
    _fake_card(monkeypatch, fake_streaming, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(fake_streaming)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert ("stream.apply", _DEV, _h3_budget(8.0)) in fake_streaming


def test_pipe_path_evicts_the_resident_dit_before_measuring_the_te_budget(monkeypatch, fake_streaming):
    log = fake_streaming
    registry, free = _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    dit = _Resident(log, "dit")
    registry.note_resident(dit, _DEV, 6.0)
    encoder = minimax_h3_te(log)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert log.index(("dit.offload",)) < log.index(("stream.apply", _DEV, _h3_budget(10.0)))
    assert registry.resident_gb(_DEV) == 0.0


def test_pipe_path_gpu_mode_keeps_the_whole_move_even_when_it_does_not_fit(monkeypatch, fake_streaming):
    configure_text_encoder_placement("gpu")
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert not [entry for entry in log if entry[0].startswith("stream.")]
    assert log[:2] == [("encoder.to", _DEV), ("module.to", _DEV)]
    assert log[-1][:2] == ("mark", "after-evict")


def test_pipe_path_stream_mode_streams_an_encoder_that_fits(monkeypatch, fake_streaming):
    configure_text_encoder_placement("stream")
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=8.0, others_gb=0.5)
    anima = next(f for f in FAMILIES if f.family == "anima")
    encoder = anima.build(log)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert ("encoder.to", _DEV) not in log
    assert ("stream.apply", _DEV, 6.5) in log


@pytest.mark.parametrize("family", FAMILIES, ids=lambda f: f.family)
def test_pipe_path_never_streams_a_fitting_encoder_in_auto(monkeypatch, fake_streaming, family):
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=family.card_gb, others_gb=0.5)
    encoder = family.build(log)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert not [entry for entry in log if entry[0].startswith("stream.")]
    assert log[-1][:2] == ("mark", "co-resident")


def test_pipe_path_streamed_oom_retries_fully_streamed(monkeypatch, fake_streaming):
    _FakeStreaming.oom_budgets = {_h3_budget(10.0)}
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert [e for e in log if e[0] != "note_offloaded"] == [
        ("stream.apply", _DEV, _h3_budget(10.0)),
        ("stream.teardown",),
        ("stream.apply", _DEV, 0.0),
        ("encode", "cpu"),
        ("stream.teardown",),
        ("mark", "streamed", 0.0),
    ]


def test_pipe_path_wsl_driver_oom_retries_fully_streamed_where_the_policy_retries(monkeypatch, fake_streaming):
    monkeypatch.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "retry")
    _FakeStreaming.oom_budgets = {_h3_budget(10.0)}
    _FakeStreaming.make_oom = _wsl_driver_oom
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert [e for e in log if e[0] != "note_offloaded"] == [
        ("stream.apply", _DEV, _h3_budget(10.0)),
        ("stream.teardown",),
        ("stream.apply", _DEV, 0.0),
        ("encode", "cpu"),
        ("stream.teardown",),
        ("mark", "streamed", 0.0),
    ]


def test_pipe_path_tears_down_and_raises_a_fatal_driver_oom(monkeypatch, fake_streaming):
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    monkeypatch.setattr(cuda_allocator, "_stop_growing_expandable_segments", lambda: None)
    monkeypatch.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "fatal")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    _FakeStreaming.oom_budgets = {_h3_budget(10.0)}
    _FakeStreaming.make_oom = _wsl_driver_oom
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    with pytest.raises(RuntimeError, match="device not ready"):
        run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))
    assert [e for e in log if e[0].startswith("stream.")] == [
        ("stream.apply", _DEV, _h3_budget(10.0)),
        ("stream.teardown",),
    ]
    assert cuda_allocator.allocator_damage() == "CUDA driver error: device not ready"


def test_pipe_path_tears_down_and_raises_a_driver_error_that_is_not_exhaustion(monkeypatch, fake_streaming):
    _FakeStreaming.oom_budgets = {_h3_budget(10.0)}
    _FakeStreaming.make_oom = lambda: RuntimeError("CUDA driver error: invalid argument")
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    with pytest.raises(RuntimeError, match="invalid argument"):
        run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))
    assert [e for e in log if e[0].startswith("stream.")] == [
        ("stream.apply", _DEV, _h3_budget(10.0)),
        ("stream.teardown",),
    ]


def test_pipe_path_falls_back_to_the_cpu_encode_when_streaming_keeps_oom(monkeypatch, fake_streaming):
    _FakeStreaming.oom_budgets = {_h3_budget(10.0), 0.0}
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    out = run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert out is not None
    assert log[-2:] == [("mark", "cpu-fallback", None), ("encode", "cpu")]


def test_pipe_path_tears_down_when_the_encode_itself_fails(monkeypatch, fake_streaming):
    _fake_card(monkeypatch, fake_streaming, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(fake_streaming)

    def boom():
        raise RuntimeError("encode failed")

    with pytest.raises(RuntimeError, match="encode failed"):
        run_text_encode(encoder, _DEV, boom)
    assert fake_streaming[-1] == ("stream.teardown",)


class _FakeSpec:
    family = "fake"
    variant = "fake"
    latent_format: dict = {}
    sampling_settings: dict = {}


class _FakeNativeModel:
    def __init__(self, kind: str, gb: float) -> None:
        self.kind = kind
        self.spec = _FakeSpec() if kind == "dit" else None
        self.estimated_vram_gb = gb
        self.quant_format = None
        self.compute_dtype = torch.float32
        self.module = object()
        self._streamer = None

    def move_to(self, device) -> None:
        pass

    def offload(self) -> None:
        pass


def _generator(encoder, *, vram_gb: float, **kwargs) -> NativeGenerator:
    return NativeGenerator(
        _FakeNativeModel("dit", 22.0), encoder, _FakeNativeModel("vae", 0.5),
        DevicePlan(_DEV, _DEV, _DEV), vram_gb=vram_gb, **kwargs,
    )


def test_generator_streams_a_te_that_does_not_fit_and_tears_it_down(monkeypatch, fake_streaming):
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)
    gen = _generator(encoder, vram_gb=12.0)

    gen.encode_prompt("prompt")

    assert log == [("stream.apply", _DEV, _h3_budget(10.0)), ("encode", "cpu"), ("stream.teardown",)]
    assert gen._te_streaming is None


def test_generator_never_evicts_other_families_for_the_encode(monkeypatch, fake_streaming):
    log = fake_streaming
    registry, _ = _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    foreign = _Resident(log, "foreign")
    registry.note_resident(foreign, _DEV, 4.0)
    encoder = minimax_h3_te(log)

    _generator(encoder, vram_gb=12.0).encode_prompt("prompt")

    assert ("foreign.offload",) not in log
    assert log[0] == ("stream.apply", _DEV, _h3_budget(6.0))
    assert registry.resident_gb(_DEV) == 4.0


def test_generator_moves_whole_an_encoder_whose_weights_fit_even_without_the_reserve(monkeypatch, fake_streaming):
    log = fake_streaming
    encoder = minimax_h3_te(log)
    weights = text_encoder_footprint(encoder).weights_gb
    _fake_card(monkeypatch, log, capacity_gb=weights + 0.5, others_gb=0.0)

    _generator(encoder, vram_gb=weights + 0.5).encode_prompt("prompt")

    assert log[0] == ("encoder.to", _DEV)
    assert not [entry for entry in log if entry[0].startswith("stream.")]


def test_pipe_path_backend_cap_never_streams_an_encoder_that_fits_the_freed_vram(monkeypatch, fake_streaming):
    configure_text_encoder_placement("auto", vram_budget_gb=4)
    log = fake_streaming
    registry, _ = _fake_card(monkeypatch, log, capacity_gb=16.0, others_gb=0.5)
    klein = next(f for f in FAMILIES if f.family == "klein")
    encoder = klein.build(log)
    dit = _Resident(log, "dit")
    registry.note_resident(dit, _DEV, 12.0)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert not [entry for entry in log if entry[0].startswith("stream.")]
    assert log[-1][:2] == ("mark", "after-evict")


def test_generator_gpu_mode_keeps_the_whole_move(monkeypatch, fake_streaming):
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    _generator(encoder, vram_gb=12.0, text_encoder_placement="gpu").encode_prompt("prompt")

    assert log == [
        ("encoder.to", _DEV), ("module.to", _DEV), ("encode", _DEV),
        ("encoder.to", "cpu"), ("module.to", "cpu"),
    ]


def test_generator_follows_the_backend_mode_when_not_overridden(monkeypatch, fake_streaming):
    configure_text_encoder_placement("stream")
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=8.0, others_gb=0.5)
    encoder = next(f for f in FAMILIES if f.family == "anima").build(log)

    _generator(encoder, vram_gb=8.0).encode_prompt("prompt")

    assert log[0] == ("stream.apply", _DEV, 6.5)
    assert ("encoder.to", _DEV) not in log


def test_generator_explicit_placement_decides_on_its_own(monkeypatch, fake_streaming):
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)
    whole = ComponentPlacement(_DEV, "standard", True)
    streamed = ComponentPlacement(_DEV, "standard", True, streamed=True, stream_budget_gb=3.0)

    _generator(encoder, vram_gb=12.0, placement=PlacementPlan(whole, whole, whole, False, "resident")).encode_prompt("p")
    assert log[0] == ("encoder.to", _DEV)
    log.clear()
    _generator(encoder, vram_gb=12.0, placement=PlacementPlan(whole, streamed, whole, False, "resident")).encode_prompt("p")
    assert log[0] == ("stream.apply", _DEV, 3.0)


def test_generator_streamed_placement_wsl_driver_oom_retries_fully_streamed_where_the_policy_retries(monkeypatch, fake_streaming):
    monkeypatch.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "retry")
    _FakeStreaming.oom_budgets = {_h3_budget(10.0)}
    _FakeStreaming.make_oom = _wsl_driver_oom
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    _generator(encoder, vram_gb=12.0).encode_prompt("prompt")

    assert log == [
        ("stream.apply", _DEV, _h3_budget(10.0)), ("stream.teardown",),
        ("stream.apply", _DEV, 0.0), ("encode", "cpu"), ("stream.teardown",),
    ]


def test_generator_streamed_placement_tears_down_and_raises_a_fatal_driver_oom(monkeypatch, fake_streaming):
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    monkeypatch.setattr(cuda_allocator, "_stop_growing_expandable_segments", lambda: None)
    monkeypatch.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "fatal")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    _FakeStreaming.oom_budgets = {_h3_budget(10.0)}
    _FakeStreaming.make_oom = _wsl_driver_oom
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    with pytest.raises(RuntimeError, match="device not ready"):
        _generator(encoder, vram_gb=12.0).encode_prompt("prompt")

    assert log == [("stream.apply", _DEV, _h3_budget(10.0)), ("stream.teardown",)]
    assert cuda_allocator.allocator_damage() == "CUDA driver error: device not ready"


def test_generator_streamed_placement_oom_retries_fully_streamed(monkeypatch, fake_streaming):
    _FakeStreaming.oom_budgets = {_h3_budget(10.0)}
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    _generator(encoder, vram_gb=12.0).encode_prompt("prompt")

    assert log == [
        ("stream.apply", _DEV, _h3_budget(10.0)), ("stream.teardown",),
        ("stream.apply", _DEV, 0.0), ("encode", "cpu"), ("stream.teardown",),
    ]


def test_generator_tears_down_when_the_encode_fails(monkeypatch, fake_streaming):
    _fake_card(monkeypatch, fake_streaming, capacity_gb=12.0, others_gb=2.0)

    class _Failing(FamilyEncoder):
        def encode(self, texts):
            raise RuntimeError("encode failed")

    encoder = _Failing(minimax_h3_module(), fake_streaming, "qwen3vl_32b")
    gen = _generator(encoder, vram_gb=12.0)

    with pytest.raises(RuntimeError, match="encode failed"):
        gen.encode_prompt("prompt")
    assert fake_streaming[-1] == ("stream.teardown",)
    assert gen._te_streaming is None


def test_generator_on_cpu_never_streams(monkeypatch, fake_streaming):
    encoder = minimax_h3_te(fake_streaming)
    gen = NativeGenerator(
        _FakeNativeModel("dit", 22.0), encoder, _FakeNativeModel("vae", 0.5), DevicePlan("cpu", "cpu", "cpu"),
    )
    gen.encode_prompt("prompt")
    assert fake_streaming == [("encoder.to", "cpu"), ("module.to", "cpu"), ("encode", "cpu")]


def _replay_stream_to_on_cpu(monkeypatch):
    def stream_to(self, device, resident_budget_gb, *, non_blocking=True):
        plan = plan_residency_split(self.module, resident_budget_gb, stream_nvfp4=self.stream_nvfp4)
        if self._streamer is None:
            self._streamer = ModuleStreamer(self.module, stream_nvfp4=self.stream_nvfp4)
        self._streamer.apply("cpu", plan, pin=False)
        self.last_stream_plan = plan
        self.device = str(device)
        get_residency_registry().note_resident(self, device, plan.resident_gb)

    monkeypatch.setattr(NativeModel, "stream_to", stream_to)


def _nvfp4(in_f: int, out_f: int, seed: int) -> nn.Module:
    torch.manual_seed(seed)
    w = torch.randn(out_f, in_f) * 0.05
    scale = default_tensor_scale(w)
    packed, block, _, _ = quantize_nvfp4(w, scale)
    layer = fp8_ops.Linear(in_f, out_f, bias=False)
    nn.Module.load_state_dict(
        layer, {"weight": packed, "weight_scale": block, "weight_scale_2": scale.clone()}, strict=False, assign=True,
    )
    return layer


class _RealTE(RecordingModule):
    def __init__(self, log: list, name: str = "module", layers: int = 4, dim: int = 64) -> None:
        super().__init__(log, name)
        torch.manual_seed(0)
        self.embed = manual_cast.Embedding(16, dim)
        self.blocks = nn.ModuleList(_nvfp4(dim, dim, seed=i) for i in range(layers))
        self.norm = manual_cast.RMSNorm(dim)
        nn.init.normal_(self.embed.weight)
        nn.init.normal_(self.norm.weight)
        self.requires_grad_(False)

    def forward(self, tokens):
        x = self.embed(tokens).float()
        for block in self.blocks:
            x = block(x)
        return self.norm(x)


class _RealEncoder(FamilyEncoder):
    def encode(self, texts):
        self._log.append(("encode", str(self._device)))
        return {"context": self.module(torch.arange(5).unsqueeze(0))}


def test_real_streaming_matches_the_resident_encode_and_cleans_up(monkeypatch):
    _replay_stream_to_on_cpu(monkeypatch)
    log: list = []
    module = _RealTE(log)
    encoder = _RealEncoder(module, log, "probe")
    with torch.inference_mode():
        reference = encoder.encode(["p"])["context"]
    log.clear()
    registry = get_residency_registry()
    fixed = plan_residency_split(module, 0.0, stream_nvfp4=True).fixed_bytes
    one_block = sum(t.numel() * t.element_size() for t in module.blocks[0].buffers())
    budget = (fixed + one_block) / GB

    streaming = TextEncoderStreaming(encoder)
    streaming.apply(_DEV, budget)
    assert ("module.to", _DEV) not in log
    assert encoder._device == torch.device(_DEV)
    assert streaming.active
    assert registry.resident_gb(_DEV) == pytest.approx(streaming.resident_gb)
    assert streaming.resident_gb <= budget + 1e-12
    assert streaming.streamed_gb > 0.0
    assert [m.comfy_cast_weights for m in module.blocks[1:]] == [True, True, True]
    with torch.inference_mode():
        streamed = encoder.encode(["p"])["context"]
    streaming.teardown()

    assert torch.equal(reference, streamed)
    assert not streaming.active
    assert registry.resident_gb(_DEV) == 0.0
    assert encoder._device == torch.device("cpu")
    assert "to" not in module.__dict__
    assert all("stream_non_blocking" not in m.__dict__ for m in module.blocks)
    assert log[-2:] == [("encoder.to", "cpu"), ("module.to", "cpu")]


def test_real_streaming_keeps_the_small_part_of_a_composite_whole(monkeypatch):
    _replay_stream_to_on_cpu(monkeypatch)

    class _Composite:
        def __init__(self, t5, clip):
            self.t5 = t5
            self.clip_l = clip

        def to(self, device):
            self.t5.to(device)
            self.clip_l.to(device)
            return self

    log: list = []
    t5 = FamilyEncoder(_RealTE(log, "t5", layers=6), log, "t5xxl")
    clip = FamilyEncoder(_RealTE(log, "clip_l", layers=1), log, "clip_l")
    encoder = _Composite(t5, clip)
    clip_size = sum(t.numel() * t.element_size() for t in list(clip.module.parameters()) + list(clip.module.buffers()))
    t5_fixed = plan_residency_split(t5.module, 0.0, stream_nvfp4=True).fixed_bytes
    budget = (clip_size + t5_fixed + 1024) / GB

    streaming = TextEncoderStreaming(encoder)
    assert [part.module for part in streaming.parts] == [clip.module, t5.module]
    streaming.apply(_DEV, budget)

    clip_part, t5_part = streaming.parts
    assert clip_part.last_stream_plan.fully_resident
    assert not t5_part.last_stream_plan.fully_resident
    assert t5._device == torch.device(_DEV) and clip._device == torch.device(_DEV)
    streaming.teardown()
    assert get_residency_registry().resident_gb(_DEV) == 0.0


def test_te_and_dit_budgets_do_not_overlap(monkeypatch):
    _replay_stream_to_on_cpu(monkeypatch)
    log: list = []
    registry, free = _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    dit = _Resident(log, "dit")
    registry.note_resident(dit, _DEV, 6.0)
    module = _RealTE(log)
    encoder = _RealEncoder(module, log, "probe")
    monkeypatch.setattr(residency, "module_size_gb", lambda enc: 19.0)
    monkeypatch.setattr(
        residency, "text_encoder_footprint",
        lambda enc: type(text_encoder_footprint(enc))(weights_gb=19.0, dequant_peak_gb=1.2, quant_formats=("nvfp4",)),
    )
    during: list[tuple[float, float]] = []

    def encode():
        during.append((registry.resident_gb(_DEV), free(_DEV)))
        return encoder.encode(["p"])

    run_text_encode(encoder, _DEV, encode)

    te_resident, free_during = during[0]
    assert ("dit.offload",) in log
    assert te_resident <= 10.0 - 1.0 - 1.2 + 1e-9
    assert free_during >= 1.0 + 1.2 - 1e-9
    assert registry.resident_gb(_DEV) == 0.0

    gen = NativeGenerator(
        _FakeNativeModel("dit", 22.0), encoder, _FakeNativeModel("vae", 0.5), DevicePlan(_DEV, _DEV, _DEV),
    )
    assert gen._dit_weights_budget_gb((1, 16, 64, 64)) == pytest.approx(10.0 - 0.75)


def test_pipe_path_encodes_on_the_cpu_when_host_ram_cannot_pin_the_streamed_share(monkeypatch, fake_streaming):
    from src.platform.runtime.native.errors import HostMemoryExhaustedError

    class _Refused(_FakeStreaming):
        def apply(self, device, budget_gb):
            _FakeStreaming.log.append(("stream.apply", str(device), round(float(budget_gb), 3)))
            raise HostMemoryExhaustedError("not enough host RAM to pin")

    monkeypatch.setattr(engine_mod, "TextEncoderStreaming", _Refused)
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    encoder = minimax_h3_te(log)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert [e for e in log if e[0] != "note_offloaded"] == [
        ("stream.apply", _DEV, _h3_budget(10.0)),
        ("stream.teardown",),
        ("mark", "cpu-fallback", None),
        ("encode", "cpu"),
    ]


def test_pipe_path_stream_mode_without_a_vram_reading_keeps_the_whole_move(monkeypatch, fake_streaming):
    configure_text_encoder_placement("stream")
    log = fake_streaming
    _fake_card(monkeypatch, log, capacity_gb=8.0, others_gb=0.5)
    monkeypatch.setattr(residency, "free_vram_gb", lambda device: None)
    encoder = next(f for f in FAMILIES if f.family == "anima").build(log)

    run_text_encode(encoder, _DEV, lambda: encoder.encode(["prompt"]))

    assert not [entry for entry in log if entry[0].startswith("stream.")]
    assert log[-1][:2] == ("mark", "co-resident")


class _CpuStreamer(ModuleStreamer):
    def apply(self, device, plan, *, pin=True, non_blocking=True):
        super().apply("cpu", plan, pin=False, non_blocking=non_blocking)


@pytest.mark.parametrize("stream_nvfp4", [False, True])
def test_native_model_stream_to_streams_nvfp4_only_when_asked(monkeypatch, stream_nvfp4):
    monkeypatch.setattr(engine_mod, "ModuleStreamer", _CpuStreamer)
    module = _RealTE([])
    model = NativeModel("diffusion_model", module, estimated_vram_gb=0.001, stream_nvfp4=stream_nvfp4)

    model.stream_to(_DEV, 0.0)

    streamed = set(model.last_stream_plan.streamed_names)
    nvfp4 = {f"blocks.{i}" for i in range(len(module.blocks))}
    assert (nvfp4 <= streamed) is stream_nvfp4
    assert nvfp4.isdisjoint(streamed) is (not stream_nvfp4)
    model.offload()
    assert get_residency_registry().resident_gb(_DEV) == 0.0


class _Part:
    def __init__(self, log: list, name: str, error: Exception | None = None) -> None:
        self._log = log
        self._name = name
        self._error = error

    def offload(self) -> None:
        self._log.append(f"{self._name}.offload")
        if self._error is not None:
            raise self._error


class _Mover:
    def __init__(self, log: list) -> None:
        self._log = log

    def to(self, device) -> None:
        self._log.append(f"encoder.to.{device}")


def _streaming_with(parts: list, encoder) -> TextEncoderStreaming:
    streaming = TextEncoderStreaming.__new__(TextEncoderStreaming)
    streaming.encoder = encoder
    streaming.parts = parts
    streaming.device = "cpu"
    return streaming


def test_teardown_offloads_every_part_and_the_encoder_then_raises_the_first_error():
    log: list = []
    first, second = RuntimeError("first"), RuntimeError("second")
    streaming = _streaming_with(
        [_Part(log, "a", first), _Part(log, "b", second), _Part(log, "c")], _Mover(log),
    )

    with pytest.raises(RuntimeError, match="first") as excinfo:
        streaming.teardown()

    assert excinfo.value is first
    assert log == ["a.offload", "b.offload", "c.offload", "encoder.to.cpu"]


def test_teardown_still_moves_the_encoder_to_the_cpu_when_the_move_is_the_only_failure():
    log: list = []

    class _FailingMover:
        def to(self, device) -> None:
            log.append(f"encoder.to.{device}")
            raise RuntimeError("move failed")

    streaming = _streaming_with([_Part(log, "a")], _FailingMover())

    with pytest.raises(RuntimeError, match="move failed"):
        streaming.teardown()
    assert log == ["a.offload", "encoder.to.cpu"]


class _TeardownFails(_FakeStreaming):
    def teardown(self) -> None:
        super().teardown()
        raise RuntimeError("teardown failed")


def test_a_teardown_failure_does_not_mask_the_encode_error(monkeypatch, fake_streaming):
    _fake_card(monkeypatch, fake_streaming, capacity_gb=12.0, others_gb=2.0)
    monkeypatch.setattr(engine_mod, "TextEncoderStreaming", _TeardownFails)
    encoder = minimax_h3_te(fake_streaming)

    def boom():
        raise RuntimeError("encode failed")

    with pytest.raises(RuntimeError, match="encode failed"):
        run_text_encode(encoder, _DEV, boom)


def test_the_generator_logs_a_failed_streaming_teardown_as_a_warning(monkeypatch, fake_streaming, caplog):
    _fake_card(monkeypatch, fake_streaming, capacity_gb=12.0, others_gb=2.0)
    monkeypatch.setattr(engine_mod, "TextEncoderStreaming", _TeardownFails)
    gen = _generator(minimax_h3_te(fake_streaming), vram_gb=12.0)

    with caplog.at_level("WARNING", logger=engine_mod.logger.name):
        gen.encode_prompt("prompt")

    assert any(
        r.levelname == "WARNING" and "streaming teardown failed" in r.getMessage() for r in caplog.records
    )
    assert gen._te_streaming is None


class _Aborted(BaseException):
    pass


def test_the_generator_tears_streaming_down_when_the_encode_is_aborted_by_a_base_exception(
    monkeypatch, fake_streaming,
):
    _fake_card(monkeypatch, fake_streaming, capacity_gb=12.0, others_gb=2.0)

    class _Aborting(FamilyEncoder):
        def encode(self, texts):
            raise _Aborted()

    encoder = _Aborting(minimax_h3_module(), fake_streaming, "qwen3vl_32b")
    gen = _generator(encoder, vram_gb=12.0)

    with pytest.raises(_Aborted):
        gen.encode_prompt("prompt")
    assert fake_streaming[-1] == ("stream.teardown",)
    assert gen._te_streaming is None
