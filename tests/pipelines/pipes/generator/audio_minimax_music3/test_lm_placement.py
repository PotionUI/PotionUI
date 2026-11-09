from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from src.pipelines.pipes.generator.audio_minimax_music3 import lm_placement
from src.pipelines.pipes.generator.audio_minimax_music3.lm_placement import (
    OutsideLayers,
    kv_cache_gb,
    place_lm,
    plan_lm_placement,
)
from src.platform.runtime.native.arch.minimax_music3.ar_loop import generate
from src.platform.runtime.native.arch.minimax_music3.config import MiniMaxMusic3TextEncoderConfig
from src.platform.runtime.native.arch.minimax_music3.lm import MiniMaxMusic3AudioLM
from src.platform.runtime.native.detect.te_detect import detect_te_config
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.kernels import disable_weight_init
from src.platform.runtime.native.memory.partial import ModuleStreamer, plan_residency_split
from src.platform.runtime.native.memory.residency import get_residency_registry
from src.platform.runtime.native.memory.text_encoder import module_weights_bytes
from tests.platform.runtime.native._header_listings import load_header

GB = 1024 ** 3
_DEV = "cuda:0"
_DTYPES = {"BF16": torch.bfloat16, "F32": torch.float32, "F16": torch.float16, "U8": torch.uint8}


@pytest.fixture(autouse=True)
def _clean_registry():
    get_residency_registry().clear()
    yield
    get_residency_registry().clear()


def _tiny_config() -> MiniMaxMusic3TextEncoderConfig:
    return MiniMaxMusic3TextEncoderConfig(
        hidden_size=16, intermediate_size=24, num_layers=4, head_dim=8,
        num_attention_heads=2, num_key_value_heads=1, rope_theta=10000.0,
        rms_norm_eps=1e-6, max_position_embeddings=64,
        decoder_intermediate_size=20, decoder_num_layers=2, decoder_num_heads=2, decoder_head_dim=8,
        audio_vocab_size=6, num_codebooks=8,
        merged_qkv=True, merged_mlp=True, decoder_merged_qkv=True, decoder_merged_mlp=True,
        pruned_embeddings=True, pruned_lm_head=True,
    )


def _tiny_lm(seed: int = 0) -> MiniMaxMusic3AudioLM:
    lm = MiniMaxMusic3AudioLM(_tiny_config(), disable_weight_init, dtype=torch.float32)
    g = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for p in lm.parameters():
            p.copy_(torch.randn(p.shape, generator=g) * 0.5)
    lm.post_load()
    lm.eval()
    return lm


def _real_pruned_lm() -> MiniMaxMusic3AudioLM:
    header = load_header("minimax_music3/minimax_music3_text_encoder_pruned_bf16")
    sd = {
        key: torch.empty(entry["shape"], dtype=_DTYPES[entry["dtype"]], device="meta")
        for key, entry in header.items()
    }
    cfg = MiniMaxMusic3TextEncoderConfig.from_detect_config(detect_te_config(sd))
    with torch.device("meta"):
        return MiniMaxMusic3AudioLM(cfg, disable_weight_init, dtype=torch.bfloat16)


def _real_sizes() -> tuple[float, float, float]:
    lm = _real_pruned_lm()
    total = module_weights_bytes(lm) / GB
    layers = module_weights_bytes(lm.model.layers) / GB
    kv = kv_cache_gb(lm.cfg, 400 + 250 + 1)
    return total, layers, kv


def test_kv_cache_estimate_matches_the_real_allocation():
    lm = _tiny_lm()
    cache = lm.new_kv_cache(max_len=37, device="cpu")
    allocated = sum(t.numel() * t.element_size() for t in list(cache.keys) + list(cache.values))
    assert kv_cache_gb(lm.cfg, 37) * GB == pytest.approx(allocated)


def test_real_pruned_text_encoder_cannot_be_placed_whole_on_a_12gb_card():
    total, layers, kv = _real_sizes()
    assert total > 12.0
    plan = plan_lm_placement(
        total_gb=total, layers_gb=layers, kv_gb=kv, reserve_gb=1.0, free_gb=11.2, budget_gb=10.0,
    )
    assert plan.stream
    assert plan.available_gb == pytest.approx(10.0)
    fixed = total - layers
    assert plan.layers_budget_gb == pytest.approx(10.0 - fixed - kv - 1.0)
    assert fixed + plan.layers_budget_gb + kv + 1.0 <= 10.0 + 1e-9


def test_real_pruned_text_encoder_stays_resident_on_a_24gb_card():
    total, layers, kv = _real_sizes()
    plan = plan_lm_placement(
        total_gb=total, layers_gb=layers, kv_gb=kv, reserve_gb=1.0, free_gb=22.8, budget_gb=19.4,
    )
    assert not plan.stream


def test_the_smaller_of_free_vram_and_budget_bounds_the_placement():
    plan = plan_lm_placement(total_gb=8.0, layers_gb=6.0, kv_gb=0.5, reserve_gb=1.0, free_gb=9.0, budget_gb=20.0)
    assert plan.stream
    assert plan.available_gb == pytest.approx(9.0)
    assert plan.layers_budget_gb == pytest.approx(9.0 - 2.0 - 0.5 - 1.0)


def test_no_vram_reading_keeps_the_resident_placement():
    plan = plan_lm_placement(total_gb=16.0, layers_gb=13.0, kv_gb=0.1, reserve_gb=1.0, free_gb=None, budget_gb=None)
    assert not plan.stream


def test_layers_budget_never_goes_negative():
    plan = plan_lm_placement(total_gb=16.0, layers_gb=13.0, kv_gb=0.1, reserve_gb=1.0, free_gb=2.0, budget_gb=None)
    assert plan.stream
    assert plan.layers_budget_gb == 0.0


def test_outside_layers_moves_everything_but_the_llm_layers_and_keeps_module_order():
    lm = _tiny_lm()
    order = [name for name, _ in lm.named_modules()]
    OutsideLayers(lm).to(torch.float64)
    assert all(p.dtype == torch.float32 for p in lm.model.layers.parameters())
    assert lm.model.audio_decoder.audio_heads[0].weight.dtype == torch.float64
    assert lm.model.embed_tokens_prefill.weight.dtype == torch.float64
    assert [name for name, _ in lm.named_modules()] == order


class _RecordingModel:
    def __init__(self, module):
        self.module = module
        self.calls = []

    def move_to(self, device):
        self.calls.append(("move_to", str(device)))

    def offload(self):
        self.calls.append(("offload",))


def test_cpu_device_keeps_the_plain_move():
    model = _RecordingModel(_tiny_lm())
    placed = place_lm(model, "cpu", budget_gb=10.0, max_len=20)
    assert placed.resident
    placed.release()
    assert model.calls == [("move_to", "cpu"), ("offload",)]


def test_a_fitting_ar_core_is_moved_whole(monkeypatch):
    model = _RecordingModel(_tiny_lm())
    monkeypatch.setattr(lm_placement, "free_vram_gb", lambda device: 100.0)
    placed = place_lm(model, _DEV, budget_gb=50.0, max_len=20)
    assert placed.resident
    assert model.calls == [("move_to", _DEV)]


def _replay_stream_on_cpu(monkeypatch):
    def stream_to(self, device, resident_budget_gb, *, non_blocking=True):
        plan = plan_residency_split(self.module, resident_budget_gb, stream_nvfp4=self.stream_nvfp4)
        if self._streamer is None:
            self._streamer = ModuleStreamer(self.module, stream_nvfp4=self.stream_nvfp4)
        self._streamer.apply("cpu", plan, pin=False)
        self.last_stream_plan = plan
        self.device = str(device)
        get_residency_registry().note_resident(self, device, plan.resident_gb)

    original_to = OutsideLayers.to

    def to_cpu(self, *args, **kwargs):
        return original_to(self, "cpu")

    monkeypatch.setattr(NativeModel, "stream_to", stream_to)
    monkeypatch.setattr(OutsideLayers, "to", to_cpu)


def test_an_oversized_ar_core_streams_its_layers_and_generates_the_same_frames(monkeypatch):
    _replay_stream_on_cpu(monkeypatch)
    lm = _tiny_lm(seed=5)
    ids = torch.randint(0, 500, (2, 4), generator=torch.Generator().manual_seed(3))
    with torch.inference_mode():
        reference = generate(lm, ids.clone(), torch.Generator().manual_seed(11), max_frames=4)

    total = module_weights_bytes(lm) / GB
    layers = module_weights_bytes(lm.model.layers) / GB
    max_len = ids.shape[1] + 4 + 1
    kv = kv_cache_gb(lm.cfg, max_len)
    available = (total - layers) + kv + layers / 2
    monkeypatch.setattr(lm_placement, "free_vram_gb", lambda device: available)
    monkeypatch.setattr(lm_placement, "minimum_inference_memory_gb", lambda: 0.0)

    model = NativeModel("text_encoder", lm, compute_dtype=torch.float32)
    placed = place_lm(model, _DEV, budget_gb=None, max_len=max_len)
    assert not placed.resident
    assert placed.plan.layers_budget_gb == pytest.approx(layers / 2)
    assert placed.streaming.streamed_gb > 0.0
    assert placed.streaming.resident_gb <= layers / 2 + 1e-12
    assert get_residency_registry().resident_gb(_DEV) == pytest.approx(placed.streaming.resident_gb)

    with torch.inference_mode():
        streamed = generate(lm, ids.clone(), torch.Generator().manual_seed(11), max_frames=4)
    placed.release()

    assert torch.equal(reference, streamed)
    assert get_residency_registry().resident_gb(_DEV) == 0.0
    assert model.device == "cpu"
    assert "to" not in lm.model.layers.__dict__
    assert set(lm.model._modules) >= {"layers", "audio_decoder"}


def test_generator_streams_the_ar_core_with_the_loader_budget(monkeypatch):
    from src.pipelines.pipes.generator.audio_minimax_music3 import main

    seen = {}

    class _Placed:
        resident = False

        def release(self):
            seen["released"] = True

    def fake_place_lm(lm_model, device, *, budget_gb, max_len, own_models=()):
        seen.update(budget_gb=budget_gb, max_len=max_len, own=tuple(own_models))
        return _Placed()

    monkeypatch.setattr(main, "place_lm", fake_place_lm)
    compiled = {}
    monkeypatch.setattr(
        main, "maybe_compile_music3_ar",
        lambda lm, *, resident, is_cuda: compiled.update(resident=resident),
    )
    monkeypatch.setattr(main.ar_loop, "generate", lambda *a, **k: torch.zeros(1, 0, 8))

    tokenizer = SimpleNamespace(build_conditional_pair=lambda caption, lyrics: torch.zeros(2, 7, dtype=torch.long))
    lm = SimpleNamespace(module=SimpleNamespace(cfg=SimpleNamespace(max_position_embeddings=10240)))
    dit, dav = object(), object()
    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_music3", variant="music3"),
        lm=lm, dit=dit, dav=dav, tokenizer=tokenizer, lm_cache_key=None, vram_budget_gb=10.0,
    )
    pipe = main.GeneratorAudioMinimaxMusic3Pipe({
        **main.GeneratorAudioMinimaxMusic3Pipe.get_default_config(),
        "caption": "x", "duration": 10, "device": "cpu",
    })
    from src.pipelines.contracts import PipeInput
    from src.pipelines.pipes._shared.generation.progress import ProgressEmitter

    ctx = pipe.build_context(PipeInput(input={"model": bundle, "MODELS": None}))
    ctx.is_cancelled = lambda: False
    with pytest.raises(ValueError, match="frame 0"):
        pipe.generate_one(ctx, 0, 1, ProgressEmitter(lambda o: None, title="generator"))

    assert seen["budget_gb"] == 10.0
    assert seen["max_len"] == 7 + 250 + 1
    assert seen["own"] == (dit, dav)
    assert seen["released"] is True
    assert compiled["resident"] is False
