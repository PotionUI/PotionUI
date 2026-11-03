from __future__ import annotations

import torch
import torch.nn as nn

from src.platform.runtime.native.memory import partial
from src.platform.runtime.native.memory.partial import (
    ModuleStreamer,
    is_nvfp4_leaf,
    is_streamable_leaf,
    iter_streamable_leaves,
    plan_residency_split,
)
from src.platform.runtime.native.kernels import fp8_ops, manual_cast, sync_nvfp4_dequant_tables

from .._nvfp4_ref import default_tensor_scale, quantize_nvfp4
from .._quant_layouts import int8_state_dict

_DIM = 64


def _nvfp4_sd(prefix: str, out_f: int, in_f: int, *, bias: bool, seed: int) -> dict:
    torch.manual_seed(seed)
    w = torch.randn(out_f, in_f) * 0.05
    scale = default_tensor_scale(w)
    packed, block, _, _ = quantize_nvfp4(w, scale)
    sd = {prefix + "weight": packed, prefix + "weight_scale": block, prefix + "weight_scale_2": scale.clone()}
    if bias:
        sd[prefix + "bias"] = torch.randn(out_f) * 0.1
    return sd


class _QuantTE(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embed = manual_cast.Embedding(16, _DIM)
        self.nv_in = fp8_ops.Linear(_DIM, _DIM, bias=True)
        self.int8 = fp8_ops.Linear(_DIM, _DIM, bias=False)
        self.fp8 = fp8_ops.Linear(_DIM, _DIM, bias=False)
        self.plain = manual_cast.Linear(_DIM, _DIM, bias=True)
        self.norm = manual_cast.RMSNorm(_DIM)
        self.nv_out = fp8_ops.Linear(_DIM, 32, bias=False)
        torch.manual_seed(0)
        w_fp8 = (torch.randn(_DIM, _DIM) * 0.02).to(torch.float8_e4m3fn)
        sd = {
            "embed.weight": torch.randn(16, _DIM),
            "fp8.weight": w_fp8, "fp8.weight_scale": torch.tensor(0.5),
            "plain.weight": torch.randn(_DIM, _DIM) * 0.05, "plain.bias": torch.randn(_DIM) * 0.1,
            "norm.weight": torch.ones(_DIM),
        }
        sd.update(_nvfp4_sd("nv_in.", _DIM, _DIM, bias=True, seed=1))
        sd.update(_nvfp4_sd("nv_out.", 32, _DIM, bias=False, seed=2))
        sd.update(int8_state_dict(_DIM, _DIM, prefix="int8.")[0])
        self.requires_grad_(False)
        nn.Module.load_state_dict(self, sd, strict=False, assign=True)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        x = self.embed(tokens).float()
        x = self.int8(self.nv_in(x))
        x = self.plain(self.fp8(x))
        return self.nv_out(self.norm(x))


def _tokens() -> torch.Tensor:
    return torch.randint(0, 16, (2, 5), generator=torch.Generator().manual_seed(3))


def test_the_fixture_holds_every_quantised_layout():
    m = _QuantTE()
    assert is_nvfp4_leaf(m.nv_in) and is_nvfp4_leaf(m.nv_out)
    assert m.nv_in.weight is None and m.nv_in.nvfp4_packed.dtype == torch.uint8
    assert m.int8.weight.dtype == torch.int8 and m.int8.weight_scale is not None
    assert m.fp8.weight.dtype == torch.float8_e4m3fn
    assert not is_nvfp4_leaf(m.int8) and not is_nvfp4_leaf(m.embed)


def test_nvfp4_leaves_stay_fixed_unless_the_caller_opts_in():
    m = _QuantTE()
    assert is_streamable_leaf(m.nv_in) is False
    assert is_streamable_leaf(m.nv_in, include_nvfp4=True) is True
    assert is_streamable_leaf(m.embed, include_nvfp4=True) is False
    default = [n for n, _ in iter_streamable_leaves(m)]
    opted = [n for n, _ in iter_streamable_leaves(m, include_nvfp4=True)]
    assert default == ["int8", "fp8", "plain", "norm"]
    assert opted == ["nv_in", "int8", "fp8", "plain", "norm", "nv_out"]


def test_split_counts_nvfp4_bytes_as_fixed_by_default_and_streamed_when_opted_in():
    m = _QuantTE()
    nv_bytes = sum(partial._own_tensor_bytes(x) for x in (m.nv_in, m.nv_out))
    default = plan_residency_split(m, 0.0)
    opted = plan_residency_split(m, 0.0, stream_nvfp4=True)
    assert "nv_in" not in default.streamed_names
    assert opted.streamed_names == ("nv_in", "int8", "fp8", "plain", "norm", "nv_out")
    assert default.fixed_bytes - opted.fixed_bytes == nv_bytes
    assert opted.streamed_bytes - default.streamed_bytes == nv_bytes


def test_fully_streamed_quantised_encoder_matches_the_resident_forward():
    torch.manual_seed(0)
    m = _QuantTE()
    tokens = _tokens()
    with torch.inference_mode():
        reference = m(tokens)

    plan = plan_residency_split(m, 0.0, stream_nvfp4=True)
    streamer = ModuleStreamer(m, stream_nvfp4=True)
    streamer.apply("cpu", plan, pin=False)
    with torch.inference_mode():
        streamed = m(tokens)
    streamer.teardown()
    with torch.inference_mode():
        after = m(tokens)

    assert torch.equal(reference, streamed)
    assert torch.equal(reference, after)


def test_partially_resident_quantised_encoder_matches_the_resident_forward():
    m = _QuantTE()
    tokens = _tokens()
    with torch.inference_mode():
        reference = m(tokens)
    fixed = plan_residency_split(m, 0.0, stream_nvfp4=True).fixed_bytes
    budget = (fixed + partial._own_tensor_bytes(m.nv_in) + partial._own_tensor_bytes(m.int8)) / (1024 ** 3)
    plan = plan_residency_split(m, budget, stream_nvfp4=True)
    assert plan.resident_names == ("nv_in", "int8")
    streamer = ModuleStreamer(m, stream_nvfp4=True)
    streamer.apply("cpu", plan, pin=False)
    with torch.inference_mode():
        assert torch.equal(reference, m(tokens))
    streamer.teardown()


def test_streamed_leaves_get_the_cast_flags_and_only_nvfp4_gets_staging_hooks():
    m = _QuantTE()
    hooks_before = {name: len(mod._forward_pre_hooks) for name, mod in m.named_modules()}
    plan = plan_residency_split(m, 0.0, stream_nvfp4=True)
    streamer = ModuleStreamer(m, stream_nvfp4=True, prefetch=False)
    streamer.apply("cpu", plan, pin=False, non_blocking=True)

    for leaf in (m.nv_in, m.int8, m.fp8, m.plain, m.norm, m.nv_out):
        assert leaf.comfy_cast_weights is True
        assert leaf.stream_non_blocking is True
    assert len(m.nv_in._forward_pre_hooks) == hooks_before["nv_in"] + 1
    assert len(m.nv_in._forward_hooks) == 1
    assert len(m.int8._forward_pre_hooks) == hooks_before["int8"]
    assert streamer.streamed_gb * (1024 ** 3) == plan.streamed_bytes
    assert streamer.pinned_gb == 0.0

    streamer.teardown()
    for name, mod in m.named_modules():
        assert len(mod._forward_pre_hooks) == hooks_before[name]
        assert len(mod._forward_hooks) == 0
    for leaf in (m.nv_in, m.int8, m.plain):
        assert leaf.comfy_cast_weights is type(leaf).comfy_cast_weights
        assert "stream_non_blocking" not in leaf.__dict__


def test_default_streamer_never_touches_nvfp4_leaves():
    m = _QuantTE()
    plan = plan_residency_split(m, 0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)
    assert m.nv_in not in streamer._streamed
    assert "stream_non_blocking" not in m.nv_in.__dict__
    assert len(m.nv_in._forward_pre_hooks) == 0
    streamer.teardown()


def test_nvfp4_buffers_are_staged_onto_the_activation_device_for_the_call_only():
    m = _QuantTE()
    leaf = m.nv_out
    sync_nvfp4_dequant_tables(m)
    plan = plan_residency_split(m, 0.0, stream_nvfp4=True)
    streamer = ModuleStreamer(m, stream_nvfp4=True, prefetch=False)
    streamer.apply("cpu", plan, pin=False)
    originals = {name: buf for name, buf in leaf._buffers.items() if buf is not None}
    seen: dict[str, str] = {}

    def spy(module, args):
        seen.update({name: buf.device.type for name, buf in module._buffers.items() if buf is not None})

    handle = leaf.register_forward_pre_hook(spy)
    with torch.inference_mode():
        out = leaf(torch.empty(3, _DIM, device="meta"))
    handle.remove()

    assert out.device.type == "meta" and out.shape == (3, 32)
    assert set(seen.values()) == {"meta"}
    assert all(leaf._buffers[name] is buf for name, buf in originals.items())
    streamer.teardown()


def test_teardown_unpins_the_buffers_of_streamed_nvfp4_leaves_only(monkeypatch):
    m = _QuantTE()
    unpinned: list[nn.Module] = []
    monkeypatch.setattr(partial, "_unpin_own_buffers", lambda module: unpinned.append(module))
    streamer = ModuleStreamer(m, stream_nvfp4=True)
    streamer.apply("cpu", plan_residency_split(m, 0.0, stream_nvfp4=True), pin=False)
    streamer.teardown()
    assert unpinned == [m.nv_in, m.nv_out]


def test_unpin_replaces_a_pinned_buffer_with_an_equal_plain_copy(monkeypatch):
    m = _QuantTE()
    before = m.nv_in.nvfp4_packed
    monkeypatch.setattr(torch.Tensor, "is_pinned", lambda self: True)
    partial._unpin_own_buffers(m.nv_in)
    monkeypatch.undo()
    assert m.nv_in.nvfp4_packed is not before
    assert torch.equal(m.nv_in.nvfp4_packed, before)


def test_place_streamed_nvfp4_keeps_the_bias_resident_and_pins_buffers_when_asked(monkeypatch):
    m = _QuantTE()
    pinned: list[int] = []
    monkeypatch.setattr(torch.Tensor, "pin_memory", lambda self: (pinned.append(self.numel()), self.clone())[1])
    monkeypatch.setattr(torch.Tensor, "is_pinned", lambda self: False)
    monkeypatch.setattr(partial, "add_pinned_bytes", lambda n: None)
    partial._place_streamed_nvfp4(m.nv_in, "cpu", pin=True)
    monkeypatch.undo()
    buffers = [b for b in m.nv_in._buffers.values() if b is not None]
    assert sorted(pinned) == sorted(b.numel() for b in buffers)
    assert m.nv_in.bias.device.type == "cpu"
