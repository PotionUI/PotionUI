"""Tests for partial layer residency (pure logic + CPU forward parity, no GPU)."""

from __future__ import annotations

import torch
import torch.nn as nn

from src.platform.runtime.native.memory.partial import (
    ModuleStreamer,
    is_streamable_leaf,
    iter_streamable_leaves,
    plan_residency_split,
)
from vendor.gpl.comfyui.ops import _scaled_mm_fast_path_reject_reason, disable_weight_init, fp8_ops


class _Tiny(nn.Module):
    """A DiT-shaped toy: an embedding + a stack of Linears + a norm.

    Uses the ops namespace so every leaf is a ``CastWeightBiasOp`` — exactly what
    an arch module is built from.
    """

    def __init__(self, ops=disable_weight_init, n_linear: int = 4, dim: int = 8) -> None:
        super().__init__()
        self.embed = ops.Embedding(16, dim)
        self.blocks = nn.ModuleList(ops.Linear(dim, dim) for _ in range(n_linear))
        self.norm = ops.RMSNorm(dim)
        # Real CPU weights so byte accounting + forward parity are meaningful.
        for p in self.parameters():
            nn.init.normal_(p)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        x = self.embed(tokens)
        for blk in self.blocks:
            x = blk(x)
        return self.norm(x)


# --- leaf classification ------------------------------------------------------

def test_linear_and_norm_are_streamable_embedding_is_not():
    m = _Tiny()
    assert is_streamable_leaf(m.blocks[0]) is True
    assert is_streamable_leaf(m.norm) is True
    # Embedding casts to self.weight.device, not the activation's -> never streamed.
    assert is_streamable_leaf(m.embed) is False
    assert is_streamable_leaf(m) is False  # container, no own weight


def test_iter_streamable_leaves_covers_linears_and_norm_only():
    m = _Tiny(n_linear=3)
    names = [n for n, _ in iter_streamable_leaves(m)]
    assert names == ["blocks.0", "blocks.1", "blocks.2", "norm"]


# --- split math ---------------------------------------------------------------

def _linear_bytes(dim: int) -> int:
    # weight (dim*dim) + bias (dim), float32 = 4 bytes.
    return (dim * dim + dim) * 4


def test_split_is_deterministic_and_prefix_resident():
    m = _Tiny(n_linear=4, dim=8)
    # Budget for exactly the embedding + norm (fixed) + 2 of the 4 Linears.
    lin = _linear_bytes(8)
    gb = 1024 ** 3
    fixed_and_two = plan_residency_split(m, resident_budget_gb=(m_fixed_bytes(m) + 2 * lin) / gb)
    assert fixed_and_two.resident_names == ("blocks.0", "blocks.1")
    assert fixed_and_two.streamed_names == ("blocks.2", "blocks.3", "norm")
    # Same inputs -> same split.
    again = plan_residency_split(m, resident_budget_gb=(m_fixed_bytes(m) + 2 * lin) / gb)
    assert again.resident_names == fixed_and_two.resident_names


def test_zero_budget_streams_all_streamables_keeps_fixed():
    m = _Tiny(n_linear=3)
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    assert plan.resident_names == ()
    assert set(plan.streamed_names) == {"blocks.0", "blocks.1", "blocks.2", "norm"}
    assert plan.fixed_bytes > 0  # the embedding stays resident
    assert plan.fully_resident is False


def test_huge_budget_is_fully_resident():
    m = _Tiny(n_linear=3)
    plan = plan_residency_split(m, resident_budget_gb=1000.0)
    assert plan.streamed_names == ()
    assert plan.fully_resident is True


def test_byte_accounting_partitions_total():
    m = _Tiny(n_linear=4, dim=8)
    plan = plan_residency_split(m, resident_budget_gb=m_fixed_bytes(m) / (1024 ** 3))
    total = sum(p.numel() * p.element_size() for p in m.parameters())
    total += sum(b.numel() * b.element_size() for b in m.buffers())
    assert plan.resident_bytes + plan.streamed_bytes + plan.fixed_bytes == total


def m_fixed_bytes(m: _Tiny) -> int:
    """Bytes of the non-streamable tensors (the embedding here)."""
    return (m.embed.weight.numel() * m.embed.weight.element_size())


# --- streamer apply / teardown ------------------------------------------------

def test_apply_sets_stream_flags_only_on_streamed_leaves():
    m = _Tiny(n_linear=3)
    plan = plan_residency_split(m, resident_budget_gb=0.0)  # stream every leaf
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False, non_blocking=True)

    for name, leaf in iter_streamable_leaves(m):
        assert leaf.comfy_cast_weights is True
        assert leaf.stream_non_blocking is True
    # The embedding (fixed/resident) is untouched.
    assert m.embed.comfy_cast_weights is False
    assert streamer.active is True


def test_teardown_reverts_flags_to_class_default():
    m = _Tiny(n_linear=3)
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)
    streamer.teardown()

    for _, leaf in iter_streamable_leaves(m):
        assert leaf.comfy_cast_weights is type(leaf).comfy_cast_weights  # back to False
        assert "stream_non_blocking" not in leaf.__dict__
    assert streamer.active is False


# --- teardown actually unpins (Fix 4) ------------------------------------------

def test_teardown_swaps_tensor_identity_on_streamed_leaves(monkeypatch):
    """CPU-only structural guard: teardown must replace ``p.data`` on every
    streamed leaf that is (already) pinned -- a plain no-op reassignment (the
    bug) would keep the same page-locked tensor object alive. Doesn't require
    a real CUDA pin_memory() allocation: fakes ``Tensor.is_pinned()`` to
    simulate an already-pinned leaf so the swap path is exercised structurally."""
    m = _Tiny(n_linear=3)
    plan = plan_residency_split(m, resident_budget_gb=0.0)  # stream every leaf
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)

    monkeypatch.setattr(torch.Tensor, "is_pinned", lambda self: True)

    before = {name: leaf.weight.data for name, leaf in iter_streamable_leaves(m)}
    streamer.teardown()
    after = {name: leaf.weight.data for name, leaf in iter_streamable_leaves(m)}

    for name in before:
        assert before[name].data_ptr() != after[name].data_ptr(), (
            f"{name}: teardown left the same (fake-pinned) tensor storage in place"
        )


def test_teardown_unpins_pinned_streamed_leaves():
    """GPU-gated: pin a streamed leaf's weights for real, then assert teardown
    leaves nothing pinned (the actual bug -- pinned memory stuck for the
    module's whole life because device.type=='cpu' looked like a no-op)."""
    if not torch.cuda.is_available():
        import pytest
        pytest.skip("pin_memory() requires CUDA")

    m = _Tiny(n_linear=3)
    plan = plan_residency_split(m, resident_budget_gb=0.0)  # stream every leaf
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=True)

    for _, leaf in iter_streamable_leaves(m):
        assert leaf.weight.data.is_pinned()

    streamer.teardown()

    for _, leaf in iter_streamable_leaves(m):
        assert leaf.weight.data.device.type == "cpu"
        assert not leaf.weight.data.is_pinned()


def test_reapply_pin_true_on_already_pinned_leaf_stays_pinned():
    """Regression guard for the unpin fix itself: calling ``apply(pin=True)``
    AGAIN on a leaf that is already pinned (no teardown in between -- e.g.
    ``NativeModel.stream_to`` retried with a different budget) must leave it
    pinned. The unpin logic must only fire for ``pin=False`` callers."""
    if not torch.cuda.is_available():
        import pytest
        pytest.skip("pin_memory() requires CUDA")

    m = _Tiny(n_linear=3)
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=True)
    for _, leaf in iter_streamable_leaves(m):
        assert leaf.weight.data.is_pinned()

    streamer.apply("cpu", plan, pin=True)  # re-apply without a teardown
    for _, leaf in iter_streamable_leaves(m):
        assert leaf.weight.data.is_pinned()  # must still be pinned, not unpinned


# --- pinned/streamed footprint accounting (drives the engine's pool release) ---

def test_streamed_gb_matches_plan_streamed_bytes():
    m = _Tiny(n_linear=4, dim=8)
    plan = plan_residency_split(m, resident_budget_gb=0.0)  # stream every leaf
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)
    assert streamer.streamed_gb == plan.streamed_bytes / (1024 ** 3)
    assert streamer.streamed_gb > 0.0


def test_pinned_gb_is_zero_when_pin_unavailable():
    """On a CPU-only box ``apply`` can't page-lock, so ``pinned_gb`` must read 0
    even though leaves stream -- the engine gates its pinned-pool release on a
    genuine page-locked footprint, not merely on streaming having happened."""
    m = _Tiny(n_linear=3)
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)  # pin=False -> can_pin False
    assert streamer.streamed_gb > 0.0
    assert streamer.pinned_gb == 0.0


def test_pinned_gb_reflects_streamed_set_when_pinned(monkeypatch):
    """With pinning available, ``pinned_gb`` equals the streamed set's size --
    the figure the engine compares against its release floor. Fakes CUDA
    availability + pin_memory so the accounting path runs without a GPU."""
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.Tensor, "pin_memory", lambda self: self)
    monkeypatch.setattr(torch.Tensor, "is_pinned", lambda self: True)

    m = _Tiny(n_linear=4, dim=8)
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=True)
    assert streamer.pinned_gb == plan.streamed_bytes / (1024 ** 3)


# --- forward parity (the correctness gate) ------------------------------------

def test_partial_residency_forward_matches_full_residency_on_cpu():
    """Streaming must not change numerics: a partially-streamed module produces
    the SAME output as the un-streamed one (validated on CPU; the only thing
    streaming changes on-GPU is WHERE the weight lives per forward)."""
    torch.manual_seed(0)
    m = _Tiny(n_linear=4, dim=8)
    tokens = torch.randint(0, 16, (2, 5))
    reference = m(tokens)

    plan = plan_residency_split(m, resident_budget_gb=m_fixed_bytes(m) / (1024 ** 3) + _linear_bytes(8) / (1024 ** 3))
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)
    streamed_out = m(tokens)
    streamer.teardown()

    assert torch.allclose(reference, streamed_out, atol=1e-6)
    # And teardown restores exact parity too.
    assert torch.allclose(reference, m(tokens), atol=1e-6)


# --- quantized leaf under partial residency ------------------------------------
#
# The MiniMax-H3 fp8 DiT at full canvas is placed mode="partial" with every leaf
# streamed. These pin the real residency state a streamed Fp8ScaledLinear sits in
# (weight stays off-CUDA, comfy_cast_weights forced True) and feed that REAL state
# into the fp8 fast-path reject-reason predicate. There is no CUDA device on this
# box, so `input_is_cuda=True` below is asserted literally -- it stands in for the
# activation, which in a real generation step IS GPU-resident even while a
# streamed leaf's own weight sits on pinned CPU RAM. Everything else fed to the
# predicate (weight_dtype, weight_is_cuda, in_features, out_features,
# weight_scale presence, lora_deltas) comes from the real streamed module.


def _fp8_streamed_module(in_f: int = 32, out_f: int = 16) -> nn.Module:
    class _Fp8Tiny(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.embed = disable_weight_init.Embedding(16, in_f)
            self.fp8 = fp8_ops.Linear(in_f, out_f, bias=False)
            real_w = torch.randn(out_f, in_f) * 0.05
            w_scale = torch.tensor(0.01)
            w_fp8 = (real_w / w_scale).clamp(-448, 448).to(torch.float8_e4m3fn)
            self.fp8.load_state_dict(
                {"weight": w_fp8, "weight_scale": w_scale}, strict=False, assign=True,
            )

        def forward(self, tokens: torch.Tensor) -> torch.Tensor:
            return self.fp8(self.embed(tokens))

    return _Fp8Tiny()


def test_streamed_fp8_leaf_reject_reason_is_weight_device_not_dtype():
    """Real-object: ModuleStreamer streams a genuine Fp8ScaledLinear leaf and its
    weight stays off-CUDA (comfy_cast_weights True, weight.is_cuda False) -- the
    residency state a streamed fp8 leaf is actually in. The reason returned must
    be the weight-device one, never a dtype-shaped string: the two have
    completely different fixes (turn on NATIVE_STREAM_PREFETCH vs. use a real
    fp8 checkpoint vs. fix an fp32 activation island)."""
    m = _fp8_streamed_module()
    plan = plan_residency_split(m, resident_budget_gb=0.0)  # stream every leaf
    assert "fp8" in plan.streamed_names
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)
    leaf = m.fp8

    assert leaf.comfy_cast_weights is True
    assert leaf.weight.is_cuda is False  # real: streamed leaf stays CPU-resident

    reason = _scaled_mm_fast_path_reject_reason(
        weight_dtype=leaf.weight.dtype,
        has_weight_scale=leaf.weight_scale is not None,
        lora_deltas=leaf.lora_deltas,
        input_dtype=torch.bfloat16,
        input_is_cuda=True,
        weight_is_cuda=leaf.weight.is_cuda,
        in_features=leaf.in_features,
        out_features=leaf.out_features,
    )
    assert reason == "weight_not_cuda"


def test_streamed_fp8_leaf_becomes_fast_path_eligible_once_weight_staged_on_device():
    """Mirrors LayerPrefetcher._consume (partial.py) exactly: a forward-pre-hook
    reassigns ``leaf.weight.data`` to a different tensor object -- device
    changed, dtype untouched, never a ``.to(dtype=...)`` cast. There is no CUDA
    device here to stage onto for real, so the hook "stages" onto a same-dtype
    CPU clone -- proving the one thing we CAN prove on this box (the swap
    preserves dtype) -- and eligibility is then asserted via the predicate with
    weight_is_cuda flipped to True, the one value _consume's swap actually
    changes."""
    m = _fp8_streamed_module()
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)
    leaf = m.fp8
    orig_dtype = leaf.weight.dtype

    staged = leaf.weight.data.clone()
    handle = leaf.register_forward_pre_hook(lambda mod, args: setattr(mod.weight, "data", staged))
    try:
        leaf(torch.zeros(1, leaf.in_features, dtype=torch.bfloat16))
    finally:
        handle.remove()

    assert leaf.weight.data.data_ptr() == staged.data_ptr()
    assert leaf.weight.dtype == orig_dtype  # _consume changes device, never dtype

    reason = _scaled_mm_fast_path_reject_reason(
        weight_dtype=leaf.weight.dtype,
        has_weight_scale=leaf.weight_scale is not None,
        lora_deltas=leaf.lora_deltas,
        input_dtype=torch.bfloat16,
        input_is_cuda=True,
        weight_is_cuda=True,  # staged onto device, as _consume leaves it
        in_features=leaf.in_features,
        out_features=leaf.out_features,
    )
    assert reason is None


# --- chunked pinned release during teardown ------------------------------------


def _drain_calls(monkeypatch, streamer, chunk_bytes: int) -> list:
    """Run teardown with the release fn spied and the chunk forced to
    ``chunk_bytes``, returning the recorded calls. ``_pinned`` is forced True:
    on a CPU-only box ``apply`` can never actually pin, but the drain must
    behave exactly as it will on the GPU box where it matters."""
    import src.platform.runtime.model_lifecycle.manager as mlm
    import src.platform.runtime.native.memory.partial as partial_mod

    calls: list = []
    monkeypatch.setattr(mlm, "empty_pinned_host_cache", lambda: calls.append(1))
    monkeypatch.setattr(partial_mod, "_TEARDOWN_RELEASE_CHUNK_GB", chunk_bytes / (1024 ** 3))
    streamer._pinned = True
    streamer.teardown()
    return calls


def test_teardown_drains_the_pinned_pool_in_chunks(monkeypatch):
    """The 2026-08-19 earlyoom kill: one-sweep teardown held fresh unpinned
    copies of the WHOLE streamed model on top of the still-cached pinned pool.
    The drain must fire once per chunk of streamed bytes moved, not once at
    the end."""
    m = _Tiny(n_linear=4)
    plan = plan_residency_split(m, resident_budget_gb=0.0)  # stream every leaf
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)

    leaf_bytes = sum(p.numel() * p.element_size() for p in m.blocks[0].parameters())
    # Chunk sized to two leaves: 4 Linears + the norm drain in >= 2 installments.
    calls = _drain_calls(monkeypatch, streamer, chunk_bytes=2 * leaf_bytes)
    assert len(calls) >= 2

    # Teardown itself must still have done its job.
    assert streamer.active is False
    assert all(not p.data.is_cuda for p in m.parameters())


def test_teardown_without_a_pinned_pool_never_drains(monkeypatch):
    """No page-locked pool (pin unavailable, or below the warm-pool floor) ->
    the old single-sweep behaviour, zero release calls mid-teardown."""
    import src.platform.runtime.model_lifecycle.manager as mlm

    m = _Tiny(n_linear=4)
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=False)  # CPU box: _pinned stays False

    calls: list = []
    monkeypatch.setattr(mlm, "empty_pinned_host_cache", lambda: calls.append(1))
    streamer.teardown()
    assert calls == []
    assert streamer.active is False
