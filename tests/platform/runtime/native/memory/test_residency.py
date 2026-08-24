"""Tests for the GPU residency coordinator + text-encoder placement (no GPU).

The coordinator is estimate-driven so its eviction logic is fully exercisable on
CPU; the ``run_text_encode`` CUDA path is driven with monkeypatched CUDA probes
and a fake encoder so we can assert the co-reside / evict / retry / CPU-fallback
sequence without a device.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.memory import residency
from src.platform.runtime.native.memory.residency import (
    _BYTES_PER_GB,
    GpuResidencyManager,
    device_index,
    effective_free_vram_gb,
    minimum_inference_memory_gb,
    module_size_gb,
    run_text_encode,
    run_text_encode_batch,
)


# --- helpers ------------------------------------------------------------------


class _FakeModel:
    """An offloadable stand-in for a NativeModel: records offload() calls."""

    def __init__(self) -> None:
        self.offloaded = False

    def offload(self) -> None:
        self.offloaded = True


class _RecordingEncoder:
    """Fake text encoder: records the device history of .to() calls."""

    def __init__(self) -> None:
        self.devices: list[str] = []

    def to(self, device):
        self.devices.append(str(device))
        return self


# --- device_index -------------------------------------------------------------


def test_device_index_parses_ordinal():
    assert device_index("cuda") == 0
    assert device_index("cuda:0") == 0
    assert device_index("cuda:3") == 3
    assert device_index("cpu") == 0


# --- module_size_gb -----------------------------------------------------------


def test_module_size_counts_params_and_buffers():
    m = torch.nn.Linear(1000, 1000)  # 1000*1000 + 1000 float32 params
    expected = (1000 * 1000 + 1000) * 4 / (1024 ** 3)
    assert abs(module_size_gb(m) - expected) < 1e-9


def test_module_size_unwraps_native_model_module_attr():
    class Wrap:
        def __init__(self, mod):
            self.module = mod

    m = torch.nn.Linear(64, 64)
    assert abs(module_size_gb(Wrap(m)) - module_size_gb(m)) < 1e-12


def test_module_size_sums_composite_t5_clip():
    class Sub:
        def __init__(self, mod):
            self.module = mod

    class Composite:
        def __init__(self, a, b):
            self.t5 = Sub(a)
            self.clip_l = Sub(b)

    a, b = torch.nn.Linear(32, 32), torch.nn.Linear(16, 16)
    total = module_size_gb(Composite(a, b))
    assert abs(total - (module_size_gb(a) + module_size_gb(b))) < 1e-12


# --- registration -------------------------------------------------------------


def test_note_resident_and_resident_gb():
    mgr = GpuResidencyManager()
    a, b = _FakeModel(), _FakeModel()
    mgr.note_resident(a, "cuda:0", 10.0)
    mgr.note_resident(b, "cuda:0", 4.0)
    assert mgr.resident_gb("cuda:0") == 14.0
    assert mgr.resident_gb() == 14.0


def test_note_resident_cpu_deregisters():
    mgr = GpuResidencyManager()
    a = _FakeModel()
    mgr.note_resident(a, "cuda:0", 10.0)
    # A subsequent move to CPU must clear the entry (note_resident with cpu).
    mgr.note_resident(a, "cpu", 10.0)
    assert mgr.resident_gb() == 0.0


def test_resident_gb_is_per_device():
    mgr = GpuResidencyManager()
    # The registry only weak-refs models (Fix 2) -- keep them alive here with a
    # local strong ref, exactly as a real caller's NativeModel/bundle would.
    a, b = _FakeModel(), _FakeModel()
    mgr.note_resident(a, "cuda:0", 10.0)
    mgr.note_resident(b, "cuda:1", 5.0)
    assert mgr.resident_gb("cuda:0") == 10.0
    assert mgr.resident_gb("cuda:1") == 5.0
    assert mgr.resident_gb() == 15.0


# --- ensure_free --------------------------------------------------------------


def test_ensure_free_noop_when_already_enough():
    mgr = GpuResidencyManager()
    a = _FakeModel()
    mgr.note_resident(a, "cuda:0", 10.0)
    offloaded = mgr.ensure_free("cuda:0", need_gb=5.0, current_free_gb=6.0)
    assert offloaded == []
    assert a.offloaded is False


def test_ensure_free_offloads_lru_first():
    mgr = GpuResidencyManager()
    old, new = _FakeModel(), _FakeModel()
    mgr.note_resident(old, "cuda:0", 10.0)   # registered first -> older
    mgr.note_resident(new, "cuda:0", 10.0)
    mgr.touch(new)                            # make `new` most-recently-used
    # Need 8GB, have 2 free -> must free >= 6GB: evict the LRU (old) only.
    offloaded = mgr.ensure_free("cuda:0", need_gb=8.0, current_free_gb=2.0)
    assert offloaded == [old]
    assert old.offloaded is True
    assert new.offloaded is False
    assert mgr.resident_gb() == 10.0          # only `new` remains tracked


def test_ensure_free_respects_exclude():
    mgr = GpuResidencyManager()
    keep, evictable = _FakeModel(), _FakeModel()
    mgr.note_resident(keep, "cuda:0", 20.0)
    mgr.note_resident(evictable, "cuda:0", 5.0)
    offloaded = mgr.ensure_free("cuda:0", need_gb=4.0, current_free_gb=1.0, exclude=[keep])
    assert offloaded == [evictable]
    assert keep.offloaded is False


def test_ensure_free_only_targets_requested_device():
    mgr = GpuResidencyManager()
    other = _FakeModel()
    mgr.note_resident(other, "cuda:1", 20.0)
    # Nothing evictable on cuda:0 -> returns empty even though the request can't
    # be satisfied (best effort, never touches another device's models).
    offloaded = mgr.ensure_free("cuda:0", need_gb=10.0, current_free_gb=0.0)
    assert offloaded == []
    assert other.offloaded is False


def test_ensure_free_stops_once_satisfied():
    mgr = GpuResidencyManager()
    a, b, c = _FakeModel(), _FakeModel(), _FakeModel()
    for m in (a, b, c):
        mgr.note_resident(m, "cuda:0", 5.0)
    # Need 6GB, 0 free -> free >= 6 needs two 5GB evictions (a, b), not c.
    offloaded = mgr.ensure_free("cuda:0", need_gb=6.0, current_free_gb=0.0)
    assert offloaded == [a, b]
    assert c.offloaded is False


# --- offload_all (missing-estimate fallback) ----------------------------------


def test_offload_all_evicts_every_foreign_on_device():
    mgr = GpuResidencyManager()
    a, b = _FakeModel(), _FakeModel()
    mgr.note_resident(a, "cuda:0", 10.0)
    mgr.note_resident(b, "cuda:0", 4.0)
    offloaded = mgr.offload_all("cuda:0")
    assert set(map(id, offloaded)) == {id(a), id(b)}
    assert a.offloaded and b.offloaded
    assert mgr.resident_gb() == 0.0


def test_offload_all_respects_exclude():
    mgr = GpuResidencyManager()
    own, foreign = _FakeModel(), _FakeModel()
    mgr.note_resident(own, "cuda:0", 24.0)
    mgr.note_resident(foreign, "cuda:0", 10.0)
    offloaded = mgr.offload_all("cuda:0", exclude=[own])
    assert offloaded == [foreign]
    assert own.offloaded is False           # the owner is never evicted
    assert foreign.offloaded is True


def test_offload_all_only_targets_requested_device():
    mgr = GpuResidencyManager()
    here, elsewhere = _FakeModel(), _FakeModel()
    mgr.note_resident(here, "cuda:0", 10.0)
    mgr.note_resident(elsewhere, "cuda:1", 10.0)
    mgr.offload_all("cuda:0")
    assert here.offloaded is True
    assert elsewhere.offloaded is False


# --- offload_all stats + always-log (visibility gap fix) ---------------------


def test_offload_all_reports_count_and_freed_gb():
    mgr = GpuResidencyManager()
    a, b = _FakeModel(), _FakeModel()
    mgr.note_resident(a, "cuda:0", 10.0)
    mgr.note_resident(b, "cuda:0", 4.0)
    result = mgr.offload_all("cuda:0")
    assert len(result) == 2                 # still list-like for existing callers
    assert result.freed_gb == pytest.approx(14.0)
    assert result.failed == []


def test_offload_all_reports_zero_when_nothing_resident(caplog):
    mgr = GpuResidencyManager()
    with caplog.at_level("INFO"):
        result = mgr.offload_all("cuda:0")
    assert result == []
    assert result.freed_gb == 0.0
    assert any("nothing resident to offload" in r.message for r in caplog.records)


def test_offload_all_logs_when_something_was_freed(caplog):
    mgr = GpuResidencyManager()
    a = _FakeModel()
    mgr.note_resident(a, "cuda:0", 10.0)
    with caplog.at_level("INFO"):
        mgr.offload_all("cuda:0")
    assert any("offloaded 1 component" in r.message for r in caplog.records)


class _FailingModel:
    """An offload() that always raises - a component that can't be reclaimed."""

    def offload(self) -> None:
        raise RuntimeError("stuck on device")


def test_offload_all_surfaces_reclaim_failures_at_warning_not_debug(caplog):
    mgr = GpuResidencyManager()
    bad = _FailingModel()
    mgr.note_resident(bad, "cuda:0", 5.0)
    with caplog.at_level("WARNING"):
        result = mgr.offload_all("cuda:0")
    assert result == []                      # not counted as offloaded
    assert result.failed == [bad]
    assert result.freed_gb == 0.0
    assert any(r.levelname == "WARNING" and "reclaim" in r.message for r in caplog.records)


# --- weakref (no strong-ref leak across drops without an explicit offload) ----


def test_registry_does_not_keep_model_alive():
    import gc
    import weakref as _weakref

    mgr = GpuResidencyManager()
    model = _FakeModel()
    ref = _weakref.ref(model)
    mgr.note_resident(model, "cuda:0", 10.0)
    assert mgr.resident_gb("cuda:0") == 10.0

    del model
    gc.collect()

    assert ref() is None                 # the registry held no strong ref
    assert mgr.resident_gb("cuda:0") == 0.0   # dead entry pruned on read


def test_offload_all_skips_and_prunes_dead_refs():
    import gc

    mgr = GpuResidencyManager()
    alive = _FakeModel()
    dying = _FakeModel()
    mgr.note_resident(alive, "cuda:0", 10.0)
    mgr.note_resident(dying, "cuda:0", 5.0)

    del dying
    gc.collect()

    offloaded = mgr.offload_all("cuda:0")  # must not crash on the dead entry
    assert offloaded == [alive]
    assert mgr.resident_gb("cuda:0") == 0.0


def test_ensure_free_skips_and_prunes_dead_refs():
    import gc

    mgr = GpuResidencyManager()
    dying = _FakeModel()
    mgr.note_resident(dying, "cuda:0", 10.0)
    del dying
    gc.collect()

    # Nothing left alive to evict -> best-effort empty result, no crash.
    offloaded = mgr.ensure_free("cuda:0", need_gb=8.0, current_free_gb=0.0)
    assert offloaded == []
    assert mgr.resident_gb("cuda:0") == 0.0


# --- minimum_inference_memory reserve -----------------------------------------


def test_minimum_inference_memory_default(monkeypatch):
    monkeypatch.delenv("NATIVE_MIN_INFERENCE_MEMORY_GB", raising=False)
    assert minimum_inference_memory_gb() == 1.0


def test_minimum_inference_memory_env_override(monkeypatch):
    monkeypatch.setenv("NATIVE_MIN_INFERENCE_MEMORY_GB", "4.5")
    assert minimum_inference_memory_gb() == 4.5


def test_minimum_inference_memory_ignores_garbage(monkeypatch):
    monkeypatch.setenv("NATIVE_MIN_INFERENCE_MEMORY_GB", "not-a-number")
    assert minimum_inference_memory_gb() == 1.0


# --- effective_free_vram_gb (idle reserved pool) ---------------


def test_effective_free_adds_idle_reserved_pool(monkeypatch):
    # mem_get_info reports 10GB free; our own allocator holds a 6GB pool of
    # which only 2GB is actually allocated -- the other 4GB is idle and
    # giveable-back, so effective free is 10 + 4 = 14GB, not just 10GB.
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        residency.torch.cuda, "mem_get_info", lambda idx: (10 * _BYTES_PER_GB, 32 * _BYTES_PER_GB)
    )
    monkeypatch.setattr(residency.torch.cuda, "memory_reserved", lambda idx: 6 * _BYTES_PER_GB)
    monkeypatch.setattr(residency.torch.cuda, "memory_allocated", lambda idx: 2 * _BYTES_PER_GB)
    assert effective_free_vram_gb("cuda:0") == pytest.approx(14.0)


def test_effective_free_matches_raw_free_when_nothing_idle(monkeypatch):
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        residency.torch.cuda, "mem_get_info", lambda idx: (10 * _BYTES_PER_GB, 32 * _BYTES_PER_GB)
    )
    monkeypatch.setattr(residency.torch.cuda, "memory_reserved", lambda idx: 2 * _BYTES_PER_GB)
    monkeypatch.setattr(residency.torch.cuda, "memory_allocated", lambda idx: 2 * _BYTES_PER_GB)
    assert effective_free_vram_gb("cuda:0") == pytest.approx(10.0)


def test_effective_free_none_when_no_cuda(monkeypatch):
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: False)
    assert effective_free_vram_gb("cuda:0") is None


def test_effective_free_none_on_cpu_device():
    assert effective_free_vram_gb("cpu") is None


def test_effective_free_none_on_query_failure(monkeypatch):
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: True)

    def _boom(idx):
        raise RuntimeError("driver gone")

    monkeypatch.setattr(residency.torch.cuda, "mem_get_info", _boom)
    assert effective_free_vram_gb("cuda:0") is None


# --- run_text_encode ----------------------------------------------------------


def _cuda_world(monkeypatch, *, free_gb: float, te_gb: float = 5.0):
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(residency.torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(residency, "free_vram_gb", lambda dev: free_gb)
    monkeypatch.setattr(residency, "module_size_gb", lambda enc: te_gb)


def test_encode_is_noop_on_cpu_and_returns_result():
    enc = _RecordingEncoder()
    result = run_text_encode(enc, "cpu", lambda: "OUT")
    assert result == "OUT"
    assert enc.devices == []  # never moved on CPU


def test_encode_coresides_without_eviction_when_it_fits(monkeypatch):
    # TE (5GB) + reserve (1GB) fits in 30GB free -> run beside the resident DiT,
    # NO eviction (avoids the offload/reload ping-pong).
    _cuda_world(monkeypatch, free_gb=30.0)
    mgr = residency.get_residency_manager()
    mgr.clear()
    dit = _FakeModel()
    mgr.note_resident(dit, "cuda:0", 24.0)

    enc = _RecordingEncoder()
    calls = []
    out = run_text_encode(enc, "cuda:0", lambda: calls.append(enc.devices[-1]) or "OK")
    assert out == "OK"
    assert calls == ["cuda:0"]                 # encode ran with the TE on the GPU
    assert enc.devices == ["cuda:0", "cpu"]    # moved up then back down
    assert dit.offloaded is False              # DiT NOT evicted — it co-resided
    mgr.clear()


def test_encode_evicts_resident_when_it_does_not_fit(monkeypatch):
    # TE (5GB) + reserve (1GB) does NOT fit in 2GB free -> evict the DiT, then encode.
    _cuda_world(monkeypatch, free_gb=2.0)
    mgr = residency.get_residency_manager()
    mgr.clear()
    dit = _FakeModel()
    mgr.note_resident(dit, "cuda:0", 24.0)

    enc = _RecordingEncoder()
    run_text_encode(enc, "cuda:0", lambda: "OK")
    assert dit.offloaded is True               # evicted to make room
    assert enc.devices == ["cuda:0", "cpu"]
    mgr.clear()


def test_encode_coresident_oom_then_evicts_and_retries(monkeypatch):
    # free=7 is in [size+reserve=6, size*1.5+reserve=8.5): co-residency is tried
    # first (7>=6), it OOMs, so we evict the DiT (7<8.5) and retry on the GPU.
    _cuda_world(monkeypatch, free_gb=7.0)
    mgr = residency.get_residency_manager()
    mgr.clear()
    dit = _FakeModel()
    mgr.note_resident(dit, "cuda:0", 24.0)

    class _OnceOOMEncoder:
        def __init__(self):
            self.devices = []
            self.gpu_moves = 0

        def to(self, device):
            self.devices.append(str(device))
            if str(device).startswith("cuda"):
                self.gpu_moves += 1
                if self.gpu_moves == 1:
                    raise torch.cuda.OutOfMemoryError("first attempt")
            return self

    enc = _OnceOOMEncoder()
    out = run_text_encode(enc, "cuda:0", lambda: "OK")
    assert out == "OK"
    assert dit.offloaded is True               # evicted on the retry path
    assert enc.gpu_moves == 2                   # co-resident attempt + retry
    mgr.clear()


def test_encode_falls_back_to_cpu_when_gpu_keeps_oom(monkeypatch):
    _cuda_world(monkeypatch, free_gb=30.0)
    mgr = residency.get_residency_manager()
    mgr.clear()

    class _AlwaysOOMEncoder:
        def __init__(self):
            self.devices = []

        def to(self, device):
            self.devices.append(str(device))
            if str(device).startswith("cuda"):
                raise torch.cuda.OutOfMemoryError("no room")
            return self

    enc = _AlwaysOOMEncoder()
    out = run_text_encode(enc, "cuda:0", lambda: "CPU_RESULT")
    assert out == "CPU_RESULT"                  # encode still ran (on CPU)
    assert enc.devices[-1] == "cpu"             # ended offloaded
    assert enc.devices.count("cuda:0") == 2     # tried GPU twice before CPU
    mgr.clear()


# --- run_text_encode: safety-net move of the real underlying module(s) --------
#
# Regression pin: ``run_text_encode``'s contract is that ``encoder.to(device)``
# moves EVERY underlying weight. A wrapper's own ``.to()`` is caller-authored and
# can silently fail to cascade to a part it holds (composite encoders, or a part
# with no override) -- the encode would then still "succeed" but run on the CPU
# with the GPU allocator untouched, exactly the shape of a real profiled bug (35s
# CPU encode, flat 0.031GB CUDA allocated, no OOM/warning in the logs because
# nothing on the fast-path actually errored). The essential assertion here is
# that the REAL inner nn.Module's ``.to()`` gets called with the target device
# even when the wrapper's own ``.to()`` is a no-op on it.
#
# ``_TrackedModule`` is a genuine ``nn.Module`` (so ``isinstance`` checks in the
# discovery walk see it) whose ``.to()`` just records the device instead of
# doing a real CUDA move -- keeps these tests CPU-only regardless of whether
# CUDA hardware happens to be present in the environment they run in.


class _TrackedModule(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.moves: list[str] = []
        self.current_device = "cpu"

    def to(self, device, *args, **kwargs):  # noqa: D401 - matches nn.Module.to's signature loosely
        self.moves.append(str(device))
        self.current_device = str(device)
        return self


def test_wrapper_to_not_cascading_to_inner_module_is_still_moved(monkeypatch):
    """Pins the exact failure shape: a wrapper whose .to() never touches its
    real inner nn.Module. run_text_encode's discovery-walk safety net must call
    .to() on the inner module directly, so the encode genuinely runs off the CPU."""
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=0.001)
    mgr = residency.get_residency_manager()
    mgr.clear()

    class _BrokenWrapper:
        def __init__(self, mod):
            self.module = mod
            self.to_calls = []

        def to(self, device):
            self.to_calls.append(str(device))
            return self  # BUG: never touches self.module -- the silent no-move shape

    inner = _TrackedModule()
    enc = _BrokenWrapper(inner)

    seen_devices = []

    def _encode():
        seen_devices.append(inner.current_device)
        return "OK"

    out = run_text_encode(enc, "cuda:0", _encode)

    assert out == "OK"
    assert enc.to_calls == ["cuda:0", "cpu"]          # wrapper's own (broken) .to() still called
    assert seen_devices == ["cuda:0"]                  # but the real module WAS moved during encode
    assert inner.moves == ["cuda:0", "cpu"]             # discovery walk moved it there and back
    mgr.clear()


def test_composite_encoder_moves_every_inner_module(monkeypatch):
    """A Flux1-style composite (.t5 / .clip_l, each wrapping a .module) must have
    every real inner module moved, not just whichever the wrapper's own .to()
    happens to touch."""
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=0.001)
    mgr = residency.get_residency_manager()
    mgr.clear()

    class _Part:
        def __init__(self, mod):
            self.module = mod

    class _Composite:
        def __init__(self, t5_mod, clip_mod):
            self.t5 = _Part(t5_mod)
            self.clip_l = _Part(clip_mod)
            self.to_calls = []

        def to(self, device):
            self.to_calls.append(str(device))
            return self  # BUG: forgets to cascade into .t5 / .clip_l

    t5_inner = _TrackedModule()
    clip_inner = _TrackedModule()
    enc = _Composite(t5_inner, clip_inner)

    seen = []

    def _encode():
        seen.append((t5_inner.current_device, clip_inner.current_device))
        return "OK"

    run_text_encode(enc, "cuda:0", _encode)

    assert seen == [("cuda:0", "cuda:0")]
    assert t5_inner.moves == ["cuda:0", "cpu"]
    assert clip_inner.moves == ["cuda:0", "cpu"]
    mgr.clear()


def test_finally_restores_to_cpu_even_when_encode_fn_raises(monkeypatch):
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=0.001)
    mgr = residency.get_residency_manager()
    mgr.clear()

    inner = _TrackedModule()

    class _Wrapper:
        def __init__(self, mod):
            self.module = mod

        def to(self, device):
            self.module.to(device)
            return self

    enc = _Wrapper(inner)

    def _boom():
        raise RuntimeError("encode blew up")

    try:
        run_text_encode(enc, "cuda:0", _boom)
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass

    assert inner.moves[-1] == "cpu"  # finally still ran despite the raise
    mgr.clear()


# --- run_text_encode: prompt-embedding cache ----------------------------------


def _fresh_embed_cache():
    from src.platform.runtime.native.text_encoders.embed_cache import get_prompt_embed_cache

    cache = get_prompt_embed_cache()
    cache.clear()
    return cache


def test_cache_key_none_bypasses_cache():
    # No key -> the cache is never consulted or populated (the image-conditioned /
    # no-fingerprint contract). CPU device keeps the encode path a plain call.
    cache = _fresh_embed_cache()
    enc = _RecordingEncoder()
    calls = []
    out = run_text_encode(
        enc, "cpu", lambda: calls.append(1) or {"context": torch.ones(1, 2)},
        cache_key=None,
    )
    assert torch.equal(out["context"], torch.ones(1, 2))
    assert len(calls) == 1
    assert len(cache) == 0


def test_cache_miss_encodes_then_hit_skips_encode():
    cache = _fresh_embed_cache()
    enc = _RecordingEncoder()
    calls = []

    def _encode():
        calls.append(1)
        return {"context": torch.ones(1, 2)}

    first = run_text_encode(enc, "cpu", _encode, cache_key="k1")
    assert len(calls) == 1 and len(cache) == 1
    assert torch.equal(first["context"], torch.ones(1, 2))

    # Second call with the same key: encode_fn is NOT invoked; value comes back
    # from the cache (a distinct, mutation-safe copy).
    second = run_text_encode(enc, "cpu", _encode, cache_key="k1")
    assert len(calls) == 1                       # no re-encode
    assert torch.equal(second["context"], torch.ones(1, 2))
    assert second["context"] is not first["context"]


@pytest.mark.requires_gpu  # cache.get_on_device() moves the cached tensor with a
# real .to("cuda:0") -- there is no CPU-only substitute that still exercises it.
def test_cache_hit_never_moves_encoder_to_gpu(monkeypatch):
    # The whole point of the cache: on a hit the multi-GB encoder never pages onto
    # the device. Prime the entry via a miss (co-resident GPU encode), then assert
    # the second call touches no device at all.
    cache = _fresh_embed_cache()
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=1.0)
    mgr = residency.get_residency_manager()
    mgr.clear()

    enc = _RecordingEncoder()
    run_text_encode(enc, "cuda:0", lambda: {"context": torch.ones(1, 2)}, cache_key="k2")
    assert enc.devices == ["cuda:0", "cpu"]      # miss moved it up and back

    enc.devices.clear()
    out = run_text_encode(enc, "cuda:0", lambda: {"context": torch.zeros(1, 2)}, cache_key="k2")
    assert enc.devices == []                     # hit: encoder untouched
    # Cached value (ones) is returned on the requested device, not the new zeros.
    assert torch.equal(out["context"].cpu(), torch.ones(1, 2))
    mgr.clear()
    cache.clear()


# --- run_text_encode: coordinator registration + weights_gb census -----------


def test_run_text_encode_registers_with_coordinator_during_the_gpu_window(monkeypatch):
    """The coordinator must see the encoder's own VRAM footprint
    while it's actually resident, not just the DiT/VAE that go through
    NativeModel.move_to. Registering only inside the GPU window (and
    deregistering before falling back to CPU) keeps resident_gb() honest for a
    concurrent eviction decision, and leaves no residual entry once the encode
    is done."""
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=7.0)
    mgr = residency.get_residency_manager()
    mgr.clear()

    seen_resident_gb = []

    def _encode():
        seen_resident_gb.append(mgr.resident_gb("cuda:0"))
        return "OK"

    enc = _RecordingEncoder()
    run_text_encode(enc, "cuda:0", _encode)

    assert seen_resident_gb == [7.0]           # resident DURING the encode
    assert mgr.resident_gb("cuda:0") == 0.0    # deregistered once back on CPU
    mgr.clear()


def test_run_text_encode_mark_reports_real_tensor_census_not_the_intent(monkeypatch):
    """The ``te.encode`` mark's ``weights_gb`` must come from a live per-device
    byte census — not merely echo the device the caller asked for — or it
    can't catch the shape (a broken cascade that "succeeds" with
    ``path=co-resident`` and nothing on the GPU to show for it).
    ``_TrackedModule.to()`` (like a caller-authored wrapper that forgets to
    cascade) only *records* the intended device without moving its parameter;
    ``weights_gb`` must therefore still report the bytes under "cpu", proving
    the mark reports ground truth instead of trusting the call that was made.
    """
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=0.001)
    mgr = residency.get_residency_manager()
    mgr.clear()

    marks = []

    class _FakeProfiler:
        def mark(self, event, **fields):
            marks.append((event, fields))

    monkeypatch.setattr(
        "src.platform.observability.profiling.get_profiler", lambda: _FakeProfiler()
    )

    class _Wrapped:
        def __init__(self, mod):
            self.module = mod

        def to(self, device):
            self.module.to(device)  # cascades to the (non-moving) fake below
            return self

    inner = _TrackedModule()
    inner.register_parameter("w", torch.nn.Parameter(torch.zeros(4)))
    enc = _Wrapped(inner)

    run_text_encode(enc, "cuda:0", lambda: "OK")

    assert len(marks) == 1
    event, fields = marks[0]
    assert event == "te.encode"
    assert fields["weights_gb"] == {"cpu": pytest.approx(4 * 4 / _BYTES_PER_GB)}
    assert "cuda:0" in inner.moves              # yet .to("cuda:0") was called on it
    mgr.clear()


def test_run_text_encode_mark_free_and_needed_gb_are_none_on_the_no_cuda_path(monkeypatch):
    """GAP 3, no-CUDA branch: `device="cpu"` never reaches the placement
    gate at all, so `free_gb`/`needed_gb` must be explicit `None` on that
    mark -- not a fake `0.0`, and not silently omitted (which would be
    indistinguishable from a bug in the OTHER paths' wiring). CPU-only: no
    CUDA mocking needed since this branch never touches `torch.cuda`."""
    marks = []

    class _FakeProfiler:
        def mark(self, event, **fields):
            marks.append((event, fields))

    monkeypatch.setattr(
        "src.platform.observability.profiling.get_profiler", lambda: _FakeProfiler()
    )

    enc = _RecordingEncoder()
    result = run_text_encode(enc, "cpu", lambda: "OUT")

    assert result == "OUT"
    assert len(marks) == 1
    event, fields = marks[0]
    assert event == "te.encode"
    assert fields["path"] == "cpu-fallback"
    assert fields["free_gb"] is None
    assert fields["needed_gb"] is None


def test_run_text_encode_mark_carries_the_free_and_needed_gb_that_drove_the_path(monkeypatch):
    """GAP 3: the `te.encode` mark must carry `free_gb` (the live
    `free_vram_gb` reading the co-resident/after-evict gate read) and
    `needed_gb` (`size_gb + reserve`) -- the numbers that decided `path`, not
    just the outcome. Without them an unexpected `after-evict` (the DiT
    ping-pong the co-resident path exists to avoid) can't be explained from
    the profile alone."""
    monkeypatch.delenv("NATIVE_MIN_INFERENCE_MEMORY_GB", raising=False)
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=5.0)  # co-resident: 30 >= 5+1
    mgr = residency.get_residency_manager()
    mgr.clear()

    marks = []

    class _FakeProfiler:
        def mark(self, event, **fields):
            marks.append((event, fields))

    monkeypatch.setattr(
        "src.platform.observability.profiling.get_profiler", lambda: _FakeProfiler()
    )

    enc = _RecordingEncoder()
    run_text_encode(enc, "cuda:0", lambda: "OK")

    assert len(marks) == 1
    event, fields = marks[0]
    assert event == "te.encode"
    assert fields["path"] == "co-resident"
    assert fields["free_gb"] == 30.0
    assert fields["needed_gb"] == pytest.approx(6.0)  # size_gb(5) + reserve(1)
    mgr.clear()


def test_run_text_encode_mark_free_and_needed_gb_on_the_after_evict_path(monkeypatch):
    """Same GAP 3 fields on the OTHER named path -- eviction fires because
    `free_gb` (read at the gate) was below `needed_gb`."""
    monkeypatch.delenv("NATIVE_MIN_INFERENCE_MEMORY_GB", raising=False)
    _cuda_world(monkeypatch, free_gb=2.0, te_gb=5.0)  # 2 < 5+1 -> evict, then retry
    mgr = residency.get_residency_manager()
    mgr.clear()
    dit = _FakeModel()
    mgr.note_resident(dit, "cuda:0", 24.0)

    marks = []

    class _FakeProfiler:
        def mark(self, event, **fields):
            marks.append((event, fields))

    monkeypatch.setattr(
        "src.platform.observability.profiling.get_profiler", lambda: _FakeProfiler()
    )

    enc = _RecordingEncoder()
    run_text_encode(enc, "cuda:0", lambda: "OK")

    assert len(marks) == 1
    event, fields = marks[0]
    assert event == "te.encode"
    assert fields["path"] == "after-evict"
    assert fields["free_gb"] == 2.0
    assert fields["needed_gb"] == pytest.approx(6.0)
    mgr.clear()


@pytest.mark.requires_gpu  # exercises a real nn.Module.to("cuda:0") split across
# two params to prove the census reads actual tensor devices, not one sentinel --
# needs a genuine second device, not mockable.
def test_run_text_encode_mark_census_catches_a_partial_move(monkeypatch):
    """A single-sentinel device check (the previous
    cut of this instrumentation) reads "cuda:0" as long as SOME real parameter
    landed there — even if it's a small one and the bulk of a large composite
    stayed on CPU (a partial move: technically "moved", still running mostly
    off the CPU). The byte census must surface that split instead of hiding it
    behind one lucky tensor."""
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=0.001)
    mgr = residency.get_residency_manager()
    mgr.clear()

    marks = []

    class _FakeProfiler:
        def mark(self, event, **fields):
            marks.append((event, fields))

    monkeypatch.setattr(
        "src.platform.observability.profiling.get_profiler", lambda: _FakeProfiler()
    )

    class _PartialMover(torch.nn.Module):
        """A real nn.Module whose .to() only moves ONE of its two parameters —
        the shape a broken/partial cascade takes in production (e.g. a small
        embedding moves while the bulk of the transformer blocks don't)."""

        def __init__(self):
            super().__init__()
            self.small = torch.nn.Parameter(torch.zeros(4))       # "moves"
            self.bulk = torch.nn.Parameter(torch.zeros(1000))     # "stays on cpu"

        def to(self, device, *a, **kw):
            self.small.data = self.small.data.to(device)
            return self  # BUG: `bulk` never cascades

    class _Wrapped:
        def __init__(self, mod):
            self.module = mod

        def to(self, device):
            self.module.to(device)
            return self

    inner = _PartialMover()
    enc = _Wrapped(inner)

    run_text_encode(enc, "cuda:0", lambda: "OK")

    assert len(marks) == 1
    _event, fields = marks[0]
    weights_gb = fields["weights_gb"]
    assert set(weights_gb) == {"cuda:0", "cpu"}
    assert weights_gb["cpu"] > weights_gb["cuda:0"]   # the bulk stayed behind
    mgr.clear()


def test_distinct_keys_do_not_collide():
    cache = _fresh_embed_cache()
    enc = _RecordingEncoder()
    run_text_encode(enc, "cpu", lambda: {"context": torch.ones(1, 2)}, cache_key="ka")
    out_b = run_text_encode(enc, "cpu", lambda: {"context": torch.full((1, 2), 7.0)}, cache_key="kb")
    assert torch.equal(out_b["context"], torch.full((1, 2), 7.0))
    assert len(cache) == 2


# --- run_text_encode_batch (N sequential encodes, ONE GPU window) -----


def test_batch_all_miss_shares_one_gpu_window(monkeypatch):
    """N misses must move the encoder to the GPU ONCE and back ONCE — not once
    per request — while still calling every encode_fn, in order."""
    cache = _fresh_embed_cache()
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=5.0)
    mgr = residency.get_residency_manager()
    mgr.clear()

    enc = _RecordingEncoder()
    calls: list[int] = []

    def _fn(i):
        def _run():
            calls.append(i)
            return enc.devices[-1]
        return _run

    results = run_text_encode_batch(
        enc, "cuda:0", [_fn(0), _fn(1), _fn(2)], cache_keys=[None, None, None],
    )

    assert calls == [0, 1, 2]                    # every request encoded, in order
    assert results == ["cuda:0", "cuda:0", "cuda:0"]  # all ran while resident
    assert enc.devices == ["cuda:0", "cpu"]       # ONE move up, ONE move back
    mgr.clear()
    cache.clear()


class _FakeEmbedCache:
    """A ``PromptEmbedCache`` stand-in whose ``get_on_device`` returns the
    stored tree AS-IS (no real device move).

    The real cache's ``get_on_device`` runs an actual ``tensor.to(device=...)``
    (see ``embed_cache.py``'s ``_to_device_tree``), which needs a real CUDA
    device even when the surrounding placement machinery (``free_vram_gb``,
    ``torch.cuda.is_available``, ...) is monkeypatched via ``_cuda_world`` --
    exactly the sandbox limitation ``test_cache_hit_never_moves_encoder_to_gpu``
    already hits. Tests that only care about hit/miss *selection* (not the
    cache's own device-materialisation behaviour, which the pre-existing
    ``run_text_encode`` cache tests above already cover) swap in this fake so
    they run CPU-only regardless of a real GPU being present.
    """

    def __init__(self, store: dict) -> None:
        self._store = dict(store)

    def get_on_device(self, key, device):
        return self._store.get(key)

    def put(self, key, value) -> None:
        self._store[key] = value


def test_batch_all_hit_triggers_zero_placements(monkeypatch):
    """Every request served from the embed cache -> the encoder is never
    touched and no GPU placement decision runs at all."""
    fake_cache = _FakeEmbedCache({
        "k0": {"context": torch.ones(1, 2)},
        "k1": {"context": torch.full((1, 2), 2.0)},
    })
    monkeypatch.setattr(
        "src.platform.runtime.native.text_encoders.embed_cache.get_prompt_embed_cache",
        lambda: fake_cache,
    )

    enc = _RecordingEncoder()

    def _must_not_run():
        raise AssertionError("cache-hit request must not invoke its encode_fn")

    results = run_text_encode_batch(
        enc, "cuda:0", [_must_not_run, _must_not_run], cache_keys=["k0", "k1"],
    )

    assert enc.devices == []                     # not touched at all
    assert torch.equal(results[0]["context"], torch.ones(1, 2))
    assert torch.equal(results[1]["context"], torch.full((1, 2), 2.0))


def test_batch_mixed_hit_miss_only_encodes_the_misses(monkeypatch):
    """A batch with both hits and misses must skip the hit's encode_fn
    entirely and run only the misses, sharing ONE window between them."""
    fake_cache = _FakeEmbedCache({"hit": {"context": torch.ones(1, 2)}})
    monkeypatch.setattr(
        "src.platform.runtime.native.text_encoders.embed_cache.get_prompt_embed_cache",
        lambda: fake_cache,
    )
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=5.0)
    mgr = residency.get_residency_manager()
    mgr.clear()

    enc = _RecordingEncoder()
    calls: list[str] = []

    def _miss(tag):
        def _run():
            calls.append(tag)
            return {"context": torch.full((1, 2), 9.0)}
        return _run

    def _must_not_run():
        raise AssertionError("cache-hit request must not invoke its encode_fn")

    results = run_text_encode_batch(
        enc, "cuda:0", [_miss("a"), _must_not_run, _miss("b")],
        cache_keys=[None, "hit", None],
    )

    assert calls == ["a", "b"]                    # only the two misses ran
    assert enc.devices == ["cuda:0", "cpu"]        # ONE shared window for both
    assert torch.equal(results[1]["context"], torch.ones(1, 2))
    mgr.clear()


def test_batch_results_match_sequential_run_text_encode():
    """Byte-identical output to calling run_text_encode once per item — the
    batch changes placement cadence only, never the encoded values."""
    cache = _fresh_embed_cache()
    enc = _RecordingEncoder()

    def fn0():
        return {"context": torch.tensor([[1.0, 2.0]])}

    def fn1():
        return {"context": torch.tensor([[3.0, 4.0]])}

    batch_results = run_text_encode_batch(enc, "cpu", [fn0, fn1], cache_keys=[None, None])

    cache.clear()
    sequential_results = [
        run_text_encode(enc, "cpu", fn0, cache_key=None),
        run_text_encode(enc, "cpu", fn1, cache_key=None),
    ]

    for batch_out, sequential_out in zip(batch_results, sequential_results):
        assert torch.equal(batch_out["context"], sequential_out["context"])


def test_batch_cache_keys_length_mismatch_raises():
    enc = _RecordingEncoder()
    with pytest.raises(ValueError):
        run_text_encode_batch(enc, "cpu", [lambda: "a", lambda: "b"], cache_keys=[None])


def test_batch_none_cache_keys_defaults_every_item_uncacheable():
    """``cache_keys=None`` (the default) mirrors ``run_text_encode(cache_key=None)``
    -- every item is a miss and none are stored."""
    cache = _fresh_embed_cache()
    enc = _RecordingEncoder()
    calls: list[int] = []

    def _fn(i):
        def _run():
            calls.append(i)
            return {"context": torch.tensor([[float(i)]])}
        return _run

    run_text_encode_batch(enc, "cpu", [_fn(0), _fn(1)])
    assert calls == [0, 1]
    assert len(cache) == 0


def test_batch_miss_window_mark_carries_count(monkeypatch):
    """The shared miss window's ``te.encode`` mark keeps every field the
    single-item path already carries (path/weights_gb/free_gb/needed_gb) and
    ADDS ``count`` — the number of requests encoded inside that one window."""
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=5.0)
    mgr = residency.get_residency_manager()
    mgr.clear()

    marks = []

    class _FakeProfiler:
        def mark(self, event, **fields):
            marks.append((event, fields))

    monkeypatch.setattr(
        "src.platform.observability.profiling.get_profiler", lambda: _FakeProfiler()
    )

    enc = _RecordingEncoder()
    run_text_encode_batch(
        enc, "cuda:0", [lambda: "a", lambda: "b", lambda: "c"], cache_keys=[None, None, None],
    )

    assert len(marks) == 1
    event, fields = marks[0]
    assert event == "te.encode"
    assert fields["path"] == "co-resident"
    assert fields["count"] == 3
    mgr.clear()


def test_batch_all_hit_mark_reports_count_and_no_placement(monkeypatch):
    """The all-hit fast path still emits ONE observability mark (so an
    all-cache-hit batch isn't silently invisible), tagged
    ``path=embed-cache-hit`` with the number of items served."""
    cache = _fresh_embed_cache()
    cache.put("k0", {"context": torch.ones(1, 2)})
    cache.put("k1", {"context": torch.full((1, 2), 2.0)})

    marks = []

    class _FakeProfiler:
        def mark(self, event, **fields):
            marks.append((event, fields))

    monkeypatch.setattr(
        "src.platform.observability.profiling.get_profiler", lambda: _FakeProfiler()
    )

    enc = _RecordingEncoder()
    run_text_encode_batch(enc, "cpu", [lambda: "unused", lambda: "unused"], cache_keys=["k0", "k1"])

    assert len(marks) == 1
    event, fields = marks[0]
    assert event == "te.encode"
    assert fields["path"] == "embed-cache-hit"
    assert fields["count"] == 2
    cache.clear()


def test_single_item_te_encode_mark_has_no_count_field(monkeypatch):
    """Regression pin: the pre-existing single-item ``run_text_encode`` marks
    must NOT gain a ``count`` field just because the batch path exists — every
    field set the pre-existing tests above assert on stays exactly as it was."""
    _cuda_world(monkeypatch, free_gb=30.0, te_gb=5.0)
    mgr = residency.get_residency_manager()
    mgr.clear()

    marks = []

    class _FakeProfiler:
        def mark(self, event, **fields):
            marks.append((event, fields))

    monkeypatch.setattr(
        "src.platform.observability.profiling.get_profiler", lambda: _FakeProfiler()
    )

    enc = _RecordingEncoder()
    run_text_encode(enc, "cuda:0", lambda: "OK")

    assert len(marks) == 1
    _event, fields = marks[0]
    assert "count" not in fields
    mgr.clear()
