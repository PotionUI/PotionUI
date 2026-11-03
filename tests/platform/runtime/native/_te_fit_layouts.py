from __future__ import annotations

from dataclasses import dataclass, field

import torch
import torch.nn as nn

from src.platform.observability import profiling
from src.platform.runtime.native import engine as engine_mod
from src.platform.runtime.native.memory import residency
from src.platform.runtime.native.memory.text_encoder import (
    configure_text_encoder_placement,
    reset_text_encoder_placement,
)
from src.platform.runtime.native.kernels import manual_cast

from ._te_layouts import META, RecordingModule, bf16_linear, fp8_linear, nvfp4_linear
from ._te_layouts import int8_embedding as build_int8_embedding

DEV = "cuda:0"
CTX_GB = 0.5
RESERVE_GB = 1.0
GIB = 1024 ** 3


def configure(mode: str | None = None, budget_gb: float | None = None) -> None:
    configure_text_encoder_placement(mode, vram_budget_gb=budget_gb)


def reset_placement() -> None:
    reset_text_encoder_placement()


def default_gpu_max_vram(total_gb: float) -> int:
    return max(1, int(total_gb - RESERVE_GB))


_LINEAR = {"bf16": bf16_linear, "fp8": fp8_linear, "nvfp4": nvfp4_linear}

def transformer(log, *, hidden, inter, q, kv, layers, vocab, fmt="bf16", nvfp4_tail=0,
                int8_embedding=False, vision=False, name="module"):
    root = RecordingModule(log, name)
    root.embed = (
        build_int8_embedding(vocab, hidden) if int8_embedding
        else manual_cast.Embedding(vocab, hidden, device=META, dtype=torch.bfloat16)
    )
    blocks = nn.ModuleList()
    for index in range(layers):
        make = _LINEAR["nvfp4" if index >= layers - nvfp4_tail else fmt]
        block = nn.Module()
        block.input_norm = manual_cast.RMSNorm(hidden, device=META, dtype=torch.bfloat16)
        block.q, block.k, block.v, block.o = make(hidden, q), make(hidden, kv), make(hidden, kv), make(q, hidden)
        block.gate, block.up, block.down = make(hidden, inter), make(hidden, inter), make(inter, hidden)
        blocks.append(block)
    root.layers = blocks
    root.final_norm = manual_cast.RMSNorm(hidden, device=META, dtype=torch.bfloat16)
    if vision:
        tower = nn.Module()
        tower.patch = manual_cast.Conv3d(3, 1152, (2, 16, 16), stride=(2, 16, 16), device=META, dtype=torch.bfloat16)
        tower.blocks = nn.ModuleList(
            nn.ModuleDict({"qkv": bf16_linear(1152, 3456), "proj": bf16_linear(1152, 1152),
                           "fc1": bf16_linear(1152, 4304), "fc2": bf16_linear(4304, 1152)})
            for _ in range(27)
        )
        tower.merger = bf16_linear(4608, hidden)
        root.visual = tower
    return root


class Encoder:
    def __init__(self, module, log, role="te"):
        self.module = module
        self.role = role
        self._log = log
        self._device = torch.device("cpu")

    def to(self, device):
        self._log.append(("encoder.to", str(device)))
        self._device = torch.device(device)
        self.module.to(device)
        return self

    def encode(self, texts):
        self._log.append(("encode", str(self._device)))
        return {"context": torch.zeros(len(texts), 4, 8)}


class Composite:
    def __init__(self, t5, clip_l, log):
        self.t5, self.clip_l, self._log = t5, clip_l, log
        self.role = "flux1"

    def to(self, device):
        self._log.append(("encoder.to", str(device)))
        self.t5.to(device)
        self.clip_l.to(device)
        return self

    def encode(self, texts):
        self._log.append(("encode", str(self.t5._device)))
        return {"context": torch.zeros(len(texts), 4, 8), "pooled": torch.zeros(len(texts), 8)}


def qwen3_8b(log, fmt="bf16", nvfp4_tail=0, vision=False):
    return Encoder(transformer(log, hidden=4096, inter=12288, q=4096, kv=1024, layers=36, vocab=151936,
                               fmt=fmt, nvfp4_tail=nvfp4_tail, vision=vision), log, "qwen3_8b")


def qwen3_4b(log, fmt="bf16", vision=False):
    return Encoder(transformer(log, hidden=2560, inter=9728, q=4096, kv=1024, layers=36, vocab=151936,
                               fmt=fmt, vision=vision), log, "qwen3_4b")


def qwen25_vl_7b(log, fmt="fp8"):
    return Encoder(transformer(log, hidden=3584, inter=18944, q=3584, kv=512, layers=28, vocab=152064,
                               fmt=fmt), log, "qwen25_vl_7b")


def gemma3_12b(log, fmt="bf16"):
    return Encoder(transformer(log, hidden=3840, inter=15360, q=4096, kv=2048, layers=48, vocab=262144,
                               fmt=fmt), log, "gemma3_12b")


def umt5_xxl(log, fmt="bf16"):
    return Encoder(transformer(log, hidden=4096, inter=10240, q=4096, kv=4096, layers=24, vocab=256384,
                               fmt=fmt), log, "umt5")


def qwen3_06b(log):
    return Encoder(transformer(log, hidden=1024, inter=3072, q=2048, kv=1024, layers=28, vocab=151936),
                   log, "qwen3_06b")


def qwen3vl_32b_nvfp4(log):
    return Encoder(transformer(log, hidden=5120, inter=25600, q=8192, kv=1024, layers=50, vocab=151936,
                               fmt="nvfp4", int8_embedding=True), log, "qwen3vl_32b")


def flux1(log, t5_fmt="fp8"):
    t5 = transformer(log, hidden=4096, inter=10240, q=4096, kv=4096, layers=24, vocab=32128, fmt=t5_fmt, name="t5")
    clip = transformer(log, hidden=768, inter=3072, q=768, kv=768, layers=12, vocab=49408, name="clip_l")
    return Composite(Encoder(t5, log, "t5xxl"), Encoder(clip, log, "clip_l"), log)


@dataclass(frozen=True)
class Case:
    name: str
    card_gb: float
    builder: object = field(repr=False)

    @property
    def total_gb(self) -> float:
        return self.card_gb - 0.01

    def build(self, log):
        return self.builder(log)


def historical_estimate_gb(encoder):
    modules = []
    direct = getattr(encoder, "module", None)
    if isinstance(direct, nn.Module):
        modules.append(direct)
    for attr in ("t5", "clip_l"):
        sub = getattr(getattr(encoder, attr, None), "module", None)
        if isinstance(sub, nn.Module):
            modules.append(sub)
    if not modules:
        return None
    total = 0
    for module in modules:
        for p in module.parameters():
            total += p.numel() * p.element_size()
        for b in module.buffers():
            if b is not None:
                total += b.numel() * b.element_size()
    return (total / GIB) if total > 0 else None


def dequant_peak_gb(encoder) -> float:
    peak = 0.0
    modules = [encoder.module] if hasattr(encoder, "module") else [encoder.t5.module, encoder.clip_l.module]
    for module in modules:
        for leaf in module.modules():
            if getattr(leaf, "_is_nvfp4", False) and getattr(leaf, "nvfp4_packed", None) is not None:
                peak = max(peak, leaf.out_features * leaf.in_features * 10.0)
            elif isinstance(getattr(leaf, "weight", None), torch.Tensor) and getattr(leaf, "weight_scale", None) is not None:
                if leaf.weight.dtype in (torch.int8, torch.float8_e4m3fn, torch.float8_e5m2):
                    peak = max(peak, leaf.weight.numel() * 8.0)
    return peak / GIB


class MarkRecorder:
    def __init__(self, log):
        self._log = log

    def mark(self, name, **fields):
        if name == "te.encode":
            self._log.append((
                "mark", fields.get("path"), fields.get("device"),
                round(float(fields.get("size_gb") or 0.0), 6),
                None if fields.get("free_gb") is None else round(float(fields["free_gb"]), 6),
                None if fields.get("needed_gb") is None else round(float(fields["needed_gb"]), 6),
                fields.get("count"),
            ))


class ResidentModel:
    def __init__(self, log, name="dit"):
        self._log = log
        self._name = name

    def offload(self):
        self._log.append((f"{self._name}.offload",))


def fake_card(monkeypatch, log, total_gb, *, cuda=True):
    registry = residency.get_residency_registry()
    registry.clear()

    def free(device):
        return total_gb - CTX_GB - registry.resident_gb(device)

    def note_resident(model, device, size_gb, _orig=registry.note_resident):
        log.append(("note_resident", type(model).__name__, str(device), round(float(size_gb or 0.0), 6)))
        _orig(model, device, size_gb)

    def note_offloaded(model, _orig=registry.note_offloaded):
        log.append(("note_offloaded", type(model).__name__))
        _orig(model)

    def ensure_free(device, need_gb, current_free_gb, *, exclude=(), _orig=registry.ensure_free):
        log.append(("ensure_free", str(device), round(float(need_gb), 6), round(float(current_free_gb), 6)))
        return _orig(device, need_gb, current_free_gb, exclude=exclude)

    def offload_all(device, *, exclude=(), _orig=registry.offload_all):
        log.append(("offload_all", str(device)))
        return _orig(device, exclude=exclude)

    monkeypatch.setattr(registry, "note_resident", note_resident)
    monkeypatch.setattr(registry, "note_offloaded", note_offloaded)
    monkeypatch.setattr(registry, "ensure_free", ensure_free)
    monkeypatch.setattr(registry, "offload_all", offload_all)
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: cuda)
    monkeypatch.setattr(residency.torch.cuda, "empty_cache", lambda: log.append(("empty_cache",)))
    monkeypatch.setattr(residency, "free_vram_gb", free)
    monkeypatch.setattr(engine_mod, "free_vram_gb", free)
    monkeypatch.setattr(engine_mod, "total_vram_gb", lambda device=None: total_gb, raising=False)
    monkeypatch.setattr(residency, "total_vram_gb", lambda device=None: total_gb)
    monkeypatch.setattr(profiling, "get_profiler", lambda: MarkRecorder(log))
    return registry


def intercept_streaming(monkeypatch, log):
    def streamed_encode(encoder, device, encode_fn, te_plan, footprint, **kwargs):
        log.append(("streamed", str(device), round(float(te_plan.stream_budget_gb or 0.0), 6)))
        return encode_fn()

    class Streaming:
        def __init__(self, encoder):
            self.encoder = encoder
            self.resident_gb = 0.0
            self.streamed_gb = 0.0

        @property
        def active(self):
            return True

        def apply(self, device, budget_gb):
            log.append(("streamed", str(device), round(float(budget_gb), 6)))

        def teardown(self):
            log.append(("stream.teardown",))

    monkeypatch.setattr(residency, "_run_streamed_encode", streamed_encode, raising=False)
    monkeypatch.setattr(engine_mod, "TextEncoderStreaming", Streaming, raising=False)


def whole_move(encoder, size_gb, *, free_gb, path, count=None, reserve_gb=RESERVE_GB):
    if hasattr(encoder, "t5"):
        moves = [("encoder.to", DEV), ("encoder.to", DEV), ("t5.to", DEV), ("encoder.to", DEV), ("clip_l.to", DEV),
                 ("t5.to", DEV), ("clip_l.to", DEV)]
    else:
        moves = [("encoder.to", DEV), ("module.to", DEV), ("module.to", DEV)]
    return moves + [
        ("note_resident", type(encoder).__name__, DEV, round(size_gb, 6)),
        *([("encode", DEV)] * (count or 1)),
        ("mark", path, DEV, round(size_gb, 6), round(free_gb, 6), round(size_gb + reserve_gb, 6), count),
    ]
