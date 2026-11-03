"""Tests for streaming prefetch overlap (LayerPrefetcher), CPU-only via fake CUDA.

There is no usable GPU in the dev env, so the copy-stream / event choreography is
exercised by monkeypatching partial.py's CUDA seams (``_new_cuda_stream``,
``_new_cuda_event``, ``_current_cuda_stream``, ``_cuda_stream_ctx``,
``_stage_copy``, ``_record_stream_on``) with fakes. The staged weight is a plain
CPU clone (same values), so forward parity doubles as a correctness gate: a
prefetched forward must produce the same numbers as an un-prefetched one.
"""

from __future__ import annotations

import gc
import weakref

import pytest
import torch
import torch.nn as nn

import src.platform.runtime.native.memory.partial as partial
from src.platform.runtime.native.memory.partial import (
    LayerPrefetcher,
    ModuleStreamer,
    iter_streamable_leaves,
    plan_residency_split,
)
from src.platform.settings import runtime_flags
from src.platform.runtime.native.kernels import disable_weight_init


# --- toy module ---------------------------------------------------------------


class _Tiny(nn.Module):
    def __init__(self, n_linear: int = 4, dim: int = 8) -> None:
        super().__init__()
        self.embed = disable_weight_init.Embedding(16, dim)
        self.blocks = nn.ModuleList(disable_weight_init.Linear(dim, dim) for _ in range(n_linear))
        self.norm = disable_weight_init.RMSNorm(dim)
        for p in self.parameters():
            nn.init.normal_(p)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        x = self.embed(tokens)
        for blk in self.blocks:
            x = blk(x)
        return self.norm(x)


# --- fake CUDA primitives -----------------------------------------------------


class _FakeEvent:
    def __init__(self) -> None:
        self.recorded_on = None
        self.waited = False

    def record(self, stream=None) -> None:
        self.recorded_on = stream


class _FakeStream:
    def __init__(self, name: str = "stream") -> None:
        self.name = name
        self.waited: list = []

    def wait_event(self, ev) -> None:
        ev.waited = True
        self.waited.append(ev)


class _FakeStreamCtx:
    def __init__(self, stream) -> None:
        self.stream = stream

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


def _install_fake_cuda(monkeypatch, *, stage=None, record=None):
    compute = _FakeStream("compute")
    copy = _FakeStream("copy")
    events: list[_FakeEvent] = []
    op_log: list = []

    def _new_event():
        ev = _FakeEvent()
        events.append(ev)
        return ev

    def _wait(ev):  # not used directly; wait goes through the stream
        pass

    def _default_stage(tensor, device, *, non_blocking):
        clone = tensor.detach().clone()
        op_log.append(("stage", id(clone)))
        return clone

    def _default_record(t, s):
        op_log.append(("record_stream", id(t)))

    # Route wait through the compute stream so op ordering is observable.
    orig_wait = compute.wait_event

    def _logged_wait(ev):
        op_log.append(("wait", id(ev)))
        orig_wait(ev)

    compute.wait_event = _logged_wait

    stream_devices: list = []

    monkeypatch.setattr(partial.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(partial, "_new_cuda_stream", lambda device=None: stream_devices.append(("new", device)) or copy)
    monkeypatch.setattr(partial, "_new_cuda_event", _new_event)
    monkeypatch.setattr(partial, "_current_cuda_stream", lambda device=None: stream_devices.append(("current", device)) or compute)
    monkeypatch.setattr(partial, "_cuda_stream_ctx", lambda s: _FakeStreamCtx(s))
    monkeypatch.setattr(partial, "_stage_copy", stage or _default_stage)
    monkeypatch.setattr(partial, "_record_stream_on", record or _default_record)
    return {"compute": compute, "copy": copy, "events": events, "op_log": op_log,
            "stream_devices": stream_devices}


def _streamed_leaves(m: _Tiny):
    return [leaf for _, leaf in iter_streamable_leaves(m)]


# --- order discovery + prefetch (a) -------------------------------------------


def test_records_order_on_first_forward_then_prefetches(monkeypatch):
    fake = _install_fake_cuda(monkeypatch)
    m = _Tiny(n_linear=4, dim=8)
    tokens = torch.randint(0, 16, (2, 5))
    reference = m(tokens)

    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=1)

    out1 = m(tokens)                       # forward #1: record only, no prefetch
    assert pf._order == _streamed_leaves(m)          # execution order captured
    assert pf.max_staged == 0                        # nothing staged on the recording pass
    assert not fake["compute"].waited                # no event waited yet
    assert torch.allclose(out1, reference, atol=1e-6)

    out2 = m(tokens)                       # forward #2: prefetch along recorded order
    assert pf.max_staged >= 1                         # staging happened
    assert len(fake["compute"].waited) >= 1          # consumers event-waited
    assert torch.allclose(out2, reference, atol=1e-6)  # numerics unchanged

    pf.teardown()


# --- staging budget cap (b) ---------------------------------------------------


def test_staging_budget_never_exceeds_depth(monkeypatch):
    for depth in (1, 2):
        _install_fake_cuda(monkeypatch)
        m = _Tiny(n_linear=6, dim=8)
        tokens = torch.randint(0, 16, (2, 5))
        pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=depth)
        m(tokens)            # record
        for _ in range(3):   # several prefetching passes
            m(tokens)
        assert pf.max_staged <= depth, f"depth={depth} exceeded: {pf.max_staged}"
        assert pf.max_staged >= 1
        pf.teardown()


# --- event-wait precedes consumption (c) --------------------------------------


def test_event_waited_and_recorded_on_copy_stream_before_use(monkeypatch):
    fake = _install_fake_cuda(monkeypatch)
    m = _Tiny(n_linear=4, dim=8)
    tokens = torch.randint(0, 16, (2, 5))
    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=1)

    # A test pre-hook registered AFTER the prefetcher fires after its consume-swap,
    # so it observes the post-swap weight and logs a "consume" marker into op_log.
    seen: dict[int, bool] = {}
    for leaf in _streamed_leaves(m):
        def _mk(l):
            def _hook(mod, args):
                fake["op_log"].append(("consume", id(l)))
                seen[id(l)] = True
            return _hook
        leaf.register_forward_pre_hook(_mk(leaf))

    m(tokens)   # record
    m(tokens)   # prefetch

    # Every waited event was first recorded on the copy stream (never consumed unrecorded).
    assert fake["compute"].waited
    for ev in fake["compute"].waited:
        assert ev.recorded_on is fake["copy"]
        assert ev.waited is True

    # Ordering: the first wait in the op-log precedes the first consume that follows a stage.
    log = fake["op_log"]
    first_wait = next(i for i, e in enumerate(log) if e[0] == "wait")
    first_consume_after_wait = next(
        i for i, e in enumerate(log) if e[0] == "consume" and i > first_wait
    )
    assert first_wait < first_consume_after_wait
    pf.teardown()


# --- failure containment (d) --------------------------------------------------


def test_prefetch_exception_disables_and_stays_correct(monkeypatch):
    # Stage succeeds once (leaf1) then raises (leaf2) — so a swap is in flight when
    # the error hits, exercising the restore-on-error path.
    calls = {"n": 0}

    def _flaky_stage(tensor, device, *, non_blocking):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("simulated copy-stream failure")
        return tensor.detach().clone()

    _install_fake_cuda(monkeypatch, stage=_flaky_stage)
    m = _Tiny(n_linear=4, dim=8)
    tokens = torch.randint(0, 16, (2, 5))
    reference = m(tokens)

    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=1)
    m(tokens)                       # record
    out = m(tokens)                 # prefetch: second stage raises -> disable

    assert pf.disabled is True
    assert torch.allclose(out, reference, atol=1e-6)      # degraded but correct
    # No leaf left holding a staged (swapped) weight after the forward.
    assert pf._orig_weight == {}
    # Subsequent forwards are pure on-demand and never raise.
    assert torch.allclose(m(tokens), reference, atol=1e-6)
    pf.teardown()


# --- teardown drops hooks + refs (e) ------------------------------------------


def test_teardown_removes_hooks_and_drops_refs(monkeypatch):
    _install_fake_cuda(monkeypatch)
    m = _Tiny(n_linear=3, dim=8)
    tokens = torch.randint(0, 16, (2, 5))
    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=1)
    m(tokens)
    m(tokens)

    leaf0 = m.blocks[0]
    assert len(leaf0._forward_pre_hooks) >= 1     # prefetcher hooks installed

    # Force one staged entry and weakref it: teardown must drop the GPU-weight ref.
    pf._recording = False
    pf._prefetch_after(pf._order[0])
    staged_tensor = next(iter(pf._staged.values()))[0]
    staged_ref = weakref.ref(staged_tensor)
    del staged_tensor

    pf.teardown()

    assert pf._handles == []
    assert pf._staged == {}
    assert pf._copy_stream is None
    assert len(leaf0._forward_pre_hooks) == 0     # hooks removed
    assert len(m._forward_pre_hooks) == 0
    gc.collect()
    assert staged_ref() is None                   # staged weight reference dropped
    # Weights are all back to their originals (no swap survives teardown).
    out = m(tokens)
    assert out is not None


# --- ModuleStreamer wiring / env toggle (f) -----------------------------------


def test_module_streamer_flag_toggle_gates_prefetcher(monkeypatch):
    _install_fake_cuda(monkeypatch)
    # Skip the real device moves (no CUDA); we only test prefetcher construction.
    monkeypatch.setattr(partial, "_move_own_tensors", lambda *a, **k: None)
    m = _Tiny(n_linear=3, dim=8)
    plan = plan_residency_split(m, resident_budget_gb=0.0)  # stream everything

    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_stream_prefetch", False)
    s_off = ModuleStreamer(m)
    s_off.apply("cuda:0", plan)
    assert s_off.prefetcher is None                # default OFF: zero machinery
    s_off.teardown()

    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_stream_prefetch", True)
    s_on = ModuleStreamer(m)
    s_on.apply("cuda:0", plan)
    assert s_on.prefetcher is not None             # env on -> constructed
    s_on.teardown()
    assert s_on.prefetcher is None                 # teardown drops it


def test_module_streamer_explicit_override_beats_flag(monkeypatch):
    _install_fake_cuda(monkeypatch)
    monkeypatch.setattr(partial, "_move_own_tensors", lambda *a, **k: None)
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_stream_prefetch", True)
    m = _Tiny(n_linear=3, dim=8)
    plan = plan_residency_split(m, resident_budget_gb=0.0)

    s = ModuleStreamer(m, prefetch=False)          # explicit override wins over env
    s.apply("cuda:0", plan)
    assert s.prefetcher is None
    s.teardown()


def test_no_prefetcher_without_cuda(monkeypatch):
    # is_available False -> prefetcher never constructed even with the env on.
    monkeypatch.setattr(partial.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(partial, "_move_own_tensors", lambda *a, **k: None)
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_stream_prefetch", True)
    m = _Tiny(n_linear=3, dim=8)
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    s = ModuleStreamer(m, prefetch=True)
    s.apply("cpu", plan, pin=False)
    assert s.prefetcher is None
    s.teardown()


def test_stream_prefetch_follows_the_runtime_flag(monkeypatch):
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_stream_prefetch", False)
    assert partial.stream_prefetch_enabled() is False
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_stream_prefetch", True)
    assert partial.stream_prefetch_enabled() is True


# --- Codex E12-E14: prefetcher robustness -----------------------------------


def test_streams_bound_to_target_device(monkeypatch):
    # E14: copy + consumer streams must be created for the STREAMING device, not
    # the process's globally-current CUDA device (they can differ on multi-GPU).
    fake = _install_fake_cuda(monkeypatch)
    m = _Tiny(n_linear=4, dim=8)
    tokens = torch.randint(0, 16, (2, 5))
    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:3", prefetch_depth=1)
    m(tokens)   # record
    m(tokens)   # prefetch (consume uses current_stream(device))
    devs = {d for _kind, d in fake["stream_devices"]}
    assert devs == {"cuda:3"}   # every stream request named the target device
    pf.teardown()


def test_unconsumed_staged_released_at_root_completion(monkeypatch):
    # E13: a staged-but-skipped weight (FBCache ran A->C, skipping B) must be freed
    # at root completion, not linger until the next forward's pre-hook.
    _install_fake_cuda(monkeypatch)
    m = _Tiny(n_linear=3, dim=8)
    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=1)
    m(torch.randint(0, 16, (2, 5)))     # record order
    pf._recording = False
    pf._prefetch_after(pf._order[0])    # stage order[1] as if it will run...
    assert len(pf._staged) == 1
    pf._on_root_post()                  # ...but the forward ends without consuming it
    assert pf._staged == {}             # released now, not deferred to next pre-hook
    pf.teardown()


def test_swapped_weight_restored_when_leaf_forward_raises(monkeypatch):
    # E12: a leaf whose forward raises after its weight was swapped to the staged
    # GPU copy must still be restored (post-hook registered always_call), so no
    # weight is left pointing at GPU residency after a failed request.
    _install_fake_cuda(monkeypatch)
    m = _Tiny(n_linear=4, dim=8)
    tokens = torch.randint(0, 16, (2, 5))
    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=1)
    m(tokens)   # record order

    leaves = _streamed_leaves(m)
    victim = leaves[1]                  # prefetched by leaves[0], consumed at its own pre-hook
    original_ptr = victim.weight.data_ptr()   # the staged clone has a DIFFERENT storage

    def _boom(*a, **k):
        raise RuntimeError("simulated OOM inside the leaf forward")

    monkeypatch.setattr(victim, "forward", _boom)
    with pytest.raises(RuntimeError):
        m(tokens)                       # forward #2: victim swapped then raises

    # always_call post-hook restored the pinned-CPU original (not the staged copy).
    assert victim.weight.data_ptr() == original_ptr
    assert pf._orig_weight == {}
    pf.teardown()


class _ChunkedTiny(nn.Module):
    def __init__(self, chunks: int = 3, dim: int = 8) -> None:
        super().__init__()
        self.qkv = disable_weight_init.Linear(dim, dim)
        self.out = disable_weight_init.Linear(dim, dim)
        self.chunks = chunks
        for p in self.parameters():
            nn.init.normal_(p)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = torch.cat([self.qkv(c) for c in x.chunk(self.chunks, dim=1)], dim=1)
        return torch.cat([self.out(c) for c in h.chunk(self.chunks, dim=1)], dim=1)


def _install_device_faithful_cuda(monkeypatch):
    device_storage: set[int] = set()
    recorded: list[int] = []

    def _on_device(tensor: torch.Tensor) -> bool:
        return tensor.untyped_storage().data_ptr() in device_storage

    def _stage(tensor, device, *, non_blocking):
        if _on_device(tensor):
            return tensor
        clone = tensor.detach().clone()
        device_storage.add(clone.untyped_storage().data_ptr())
        return clone

    def _record(tensor, stream):
        if not _on_device(tensor):
            raise NotImplementedError("Could not run 'aten::record_stream' with arguments from the 'CPU' backend")
        recorded.append(id(tensor))

    fake = _install_fake_cuda(monkeypatch, stage=_stage, record=_record)
    fake["device_storage"] = device_storage
    fake["recorded"] = recorded
    return fake


def test_a_leaf_called_repeatedly_in_one_forward_does_not_disable_prefetch(monkeypatch):
    _install_device_faithful_cuda(monkeypatch)
    m = _ChunkedTiny(chunks=3)
    x = torch.randn(2, 6, 8)
    reference = m(x)

    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=1)
    for _ in range(4):
        out = m(x)
        assert torch.allclose(out, reference, atol=1e-6)

    assert pf.disabled is False
    assert pf._orig_weight == {}
    pf.teardown()


def test_a_staged_weight_never_aliases_a_weight_swapped_into_a_leaf(monkeypatch):
    _install_device_faithful_cuda(monkeypatch)
    m = _ChunkedTiny(chunks=3)
    x = torch.randn(2, 6, 8)
    reference = m(x)
    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=1)
    m(x)
    pf._next_of[id(m.qkv)] = m.qkv

    for _ in range(3):
        assert torch.allclose(m(x), reference, atol=1e-6)

    assert pf.disabled is False
    pf.teardown()


def test_consecutive_calls_of_one_leaf_are_recorded_once(monkeypatch):
    _install_device_faithful_cuda(monkeypatch)
    m = _ChunkedTiny(chunks=3)
    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=1)
    m(torch.randn(2, 6, 8))

    assert pf._order == [m.qkv, m.out]
    pf.teardown()


def test_disabling_mid_forward_releases_every_staged_copy(monkeypatch):
    fake = _install_device_faithful_cuda(monkeypatch)
    m = _Tiny(n_linear=4, dim=8)
    tokens = torch.randint(0, 16, (2, 5))
    pf = LayerPrefetcher(m, _streamed_leaves(m), "cuda:0", prefetch_depth=2)
    m(tokens)
    refs: list = []
    orig_stage = partial._stage_copy

    def _tracking_stage(tensor, device, *, non_blocking):
        staged = orig_stage(tensor, device, non_blocking=non_blocking)
        refs.append(weakref.ref(staged))
        return staged

    monkeypatch.setattr(partial, "_stage_copy", _tracking_stage)
    pf._recording = False
    pf._prefetch_after(pf._order[0])
    pf._on_error()
    gc.collect()

    assert pf._staged == {} and pf._orig_weight == {}
    assert all(r() is None for r in refs)
    pf.teardown()
