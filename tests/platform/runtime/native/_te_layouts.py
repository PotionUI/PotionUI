from __future__ import annotations

from dataclasses import dataclass, field

import torch
import torch.nn as nn

from src.platform.runtime.native.io.quant_formats import NVFP4_BLOCK
from src.platform.runtime.native.kernels import fp8_ops, manual_cast

META = torch.device("meta")


class RecordingModule(nn.Module):
    def __init__(self, log: list | None = None, name: str = "module") -> None:
        super().__init__()
        object.__setattr__(self, "_log", log if log is not None else [])
        object.__setattr__(self, "_name", name)

    def to(self, *args, **kwargs):
        device = args[0] if args else kwargs.get("device")
        self._log.append((f"{self._name}.to", str(device)))
        return self


def bf16_linear(in_f: int, out_f: int) -> nn.Module:
    return manual_cast.Linear(in_f, out_f, bias=False, device=META, dtype=torch.bfloat16)


def fp8_linear(in_f: int, out_f: int) -> nn.Module:
    layer = fp8_ops.Linear(in_f, out_f, bias=False, device=META, dtype=torch.float8_e4m3fn)
    layer.weight_scale = torch.empty((), dtype=torch.float32, device=META)
    return layer


def int8_linear(in_f: int, out_f: int) -> nn.Module:
    layer = fp8_ops.Linear(in_f, out_f, bias=False, device=META, dtype=torch.bfloat16)
    layer.weight = nn.Parameter(torch.empty(out_f, in_f, dtype=torch.int8, device=META), requires_grad=False)
    layer.weight_scale = torch.empty((out_f, 1), dtype=torch.float32, device=META)
    return layer


def nvfp4_linear(in_f: int, out_f: int) -> nn.Module:
    layer = fp8_ops.Linear(in_f, out_f, bias=False, device=META, dtype=torch.bfloat16)
    blocks = in_f // NVFP4_BLOCK
    padded_rows = (out_f + 127) // 128 * 128
    padded_cols = (blocks + 3) // 4 * 4
    layer._is_nvfp4 = True
    layer.nvfp4_packed = torch.empty(out_f, in_f // 2, dtype=torch.uint8, device=META)
    layer.nvfp4_scale = torch.empty(out_f, blocks, dtype=torch.float32, device=META)
    layer.nvfp4_weight_block_scale = torch.empty(padded_rows, padded_cols, dtype=torch.float8_e4m3fn, device=META)
    layer.nvfp4_weight_global_scale = torch.empty((), dtype=torch.float32, device=META)
    layer.weight = None
    return layer


def bf16_embedding(vocab: int, dim: int) -> nn.Module:
    return manual_cast.Embedding(vocab, dim, device=META, dtype=torch.bfloat16)


def int8_embedding(vocab: int, dim: int) -> nn.Module:
    emb = manual_cast.Embedding(1, dim, device=META, dtype=torch.bfloat16)
    emb._int8_weight = torch.empty(vocab, dim, dtype=torch.int8, device=META)
    emb._int8_weight_scale = torch.empty((vocab, 1), dtype=torch.float32, device=META)
    emb.weight = None
    return emb


def norm(dim: int) -> nn.Module:
    return manual_cast.RMSNorm(dim, device=META, dtype=torch.bfloat16)


_LINEAR = {"bf16": bf16_linear, "fp8": fp8_linear, "int8": int8_linear, "nvfp4": nvfp4_linear}


def transformer(
    *, hidden: int, inter: int, q: int, kv: int, layers: int, vocab: int,
    formats: list[str], embedding: str = "bf16", log: list | None = None, name: str = "module",
) -> RecordingModule:
    root = RecordingModule(log, name)
    root.embed = int8_embedding(vocab, hidden) if embedding == "int8" else bf16_embedding(vocab, hidden)
    blocks = nn.ModuleList()
    for index in range(layers):
        make = _LINEAR[formats[index]]
        block = nn.Module()
        block.input_norm = norm(hidden)
        block.q = make(hidden, q)
        block.k = make(hidden, kv)
        block.v = make(hidden, kv)
        block.o = make(q, hidden)
        block.post_norm = norm(hidden)
        block.gate = make(hidden, inter)
        block.up = make(hidden, inter)
        block.down = make(inter, hidden)
        blocks.append(block)
    root.layers = blocks
    root.final_norm = norm(hidden)
    return root


class FamilyEncoder:
    def __init__(self, module: nn.Module, log: list, role: str) -> None:
        self.module = module
        self.role = role
        self._log = log
        self._device = torch.device("cpu")

    def to(self, device):
        self._log.append(("encoder.to", str(device)))
        self._device = torch.device(device)
        self.module.to(device)
        return self

    def offload(self) -> None:
        self.to("cpu")

    def encode(self, texts):
        self._log.append(("encode", str(self._device)))
        return {"context": torch.zeros(len(texts), 4, 8)}


class CompositeEncoder:
    def __init__(self, t5: FamilyEncoder, clip_l: FamilyEncoder, log: list) -> None:
        self.t5 = t5
        self.clip_l = clip_l
        self.role = "flux1"
        self._log = log

    def to(self, device):
        self._log.append(("encoder.to", str(device)))
        self.t5.to(device)
        self.clip_l.to(device)
        return self

    def offload(self) -> None:
        self.to("cpu")

    def encode(self, texts):
        self._log.append(("encode", str(self.t5._device)))
        return {"context": torch.zeros(len(texts), 4, 8), "pooled": torch.zeros(len(texts), 8)}


@dataclass(frozen=True)
class FamilyTE:
    family: str
    card_gb: float
    builder: object = field(repr=False)

    def build(self, log: list):
        return self.builder(log)


def _qwen3(log, *, hidden, inter, q, kv, layers, formats, embedding="bf16"):
    return transformer(
        hidden=hidden, inter=inter, q=q, kv=kv, layers=layers, vocab=151936,
        formats=formats, embedding=embedding, log=log,
    )


def _flux1(log):
    t5 = transformer(
        hidden=4096, inter=10240, q=4096, kv=4096, layers=24, vocab=32128,
        formats=["fp8"] * 24, log=log, name="t5",
    )
    clip = transformer(
        hidden=768, inter=3072, q=768, kv=768, layers=12, vocab=49408,
        formats=["bf16"] * 12, log=log, name="clip_l",
    )
    return CompositeEncoder(FamilyEncoder(t5, log, "t5xxl"), FamilyEncoder(clip, log, "clip_l"), log)


def _single(module_builder, role):
    return lambda log: FamilyEncoder(module_builder(log), log, role)


FAMILIES: tuple[FamilyTE, ...] = (
    FamilyTE("flux1", 24.0, _flux1),
    FamilyTE("klein", 16.0, _single(
        lambda log: _qwen3(log, hidden=4096, inter=12288, q=4096, kv=1024, layers=36,
                           formats=["fp8"] * 24 + ["nvfp4"] * 12), "qwen3_8b")),
    FamilyTE("krea2_4b", 16.0, _single(
        lambda log: _qwen3(log, hidden=2560, inter=9728, q=4096, kv=1024, layers=36,
                           formats=["bf16"] * 36), "qwen3vl_4b")),
    FamilyTE("qwen_image_8b", 16.0, _single(
        lambda log: _qwen3(log, hidden=4096, inter=12288, q=4096, kv=1024, layers=36,
                           formats=["fp8"] * 36), "qwen3vl_8b")),
    FamilyTE("z_image", 12.0, _single(
        lambda log: _qwen3(log, hidden=2560, inter=9728, q=4096, kv=1024, layers=36,
                           formats=["bf16"] * 36), "z_image_qwen3")),
    FamilyTE("anima", 8.0, _single(
        lambda log: _qwen3(log, hidden=1024, inter=3072, q=2048, kv=1024, layers=28,
                           formats=["bf16"] * 28), "qwen3_06b")),
    FamilyTE("ltx_gemma3_12b", 32.0, _single(
        lambda log: transformer(hidden=3840, inter=15360, q=4096, kv=2048, layers=48, vocab=262144,
                                formats=["bf16"] * 48, log=log), "gemma3_12b")),
    FamilyTE("wan_umt5_xxl", 24.0, _single(
        lambda log: transformer(hidden=4096, inter=10240, q=4096, kv=4096, layers=24, vocab=256384,
                                formats=["bf16"] * 24, log=log), "umt5")),
    FamilyTE("minimax_h3_32gb", 32.0, _single(lambda log: minimax_h3_module(log), "qwen3vl_32b")),
)


def minimax_h3_module(log: list | None = None) -> RecordingModule:
    return _qwen3(
        [] if log is None else log, hidden=5120, inter=25600, q=8192, kv=1024, layers=50,
        formats=["nvfp4"] * 50, embedding="int8",
    )


def minimax_h3_te(log: list | None = None) -> FamilyEncoder:
    log = [] if log is None else log
    return FamilyEncoder(minimax_h3_module(log), log, "qwen3vl_32b")
