"""Permanent regression fixture for the RAM ratchet on LoRA swap.

Drives the REAL production path a maintainer's LoRA swap goes through:
``ModelLifecycle.acquire()`` -> fingerprint-bust eviction
(``_evict_entry`` + ``cleanup(aggressive=True)``) -> a fresh ``NativeModel`` ->
``lora.apply.apply_loras()`` (the actual delta-computation/patch-in-place math
in ``_compute_delta``/``_apply_inplace``) -> ``cleanup(aggressive=True)`` again
(the post-generation trim). This mirrors the fingerprint-folds-in-the-LoRA-stack
acquire scheme still used by ``model_loader/flux`` (key stable, LoRA stack only
in the fingerprint, a LoRA swap busts and reloads) and both of the fixes already
in ``manager.py``/``generation.py``. NOTE: ``model_loader/krea2/main.py`` moved
OFF this pattern (its DiT fingerprint is now LoRA-independent;
a LoRA swap patches the cached weights in place instead of busting the cache) —
this file still pins the manager-level bust/evict/reload mechanics that pattern
exercises, which remains real and load-bearing for flux/qwen/z_image/anima/ltx.

Only ``map_lora_keys`` (the arch-specific dialect translation, already covered
by ``tests/platform/runtime/native/lora/test_krea2_lora.py`` and
``test_key_mapping*``) is stubbed, so this test can run against a small plain
``nn.Sequential`` of Linears instead of a real ~24GB Krea-2 checkpoint - CPU
only, deterministic, fast.

What this test is FOR: the investigation found that hypotheses "the cache
key changes per LoRA stack" and "a live reference blocks eviction" were both
REFUTED by this exact repro (cache stays at one entry; exactly one
``NativeModel`` is ever alive), while "LoRA-apply transient allocation causes
host-RAM fragmentation that outlives `cleanup(aggressive=True)`" was CONFIRMED
and partially mitigated in ``_compute_delta``. This file pins both findings so
a future change can't silently regress either one.
"""

from __future__ import annotations

import gc
import logging
import os

import psutil
import pytest
import torch
import torch.nn as nn

import src.platform.runtime.model_lifecycle.lifecycle as manager_module
import src.platform.runtime.native.lora.apply as lora_apply
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle
from src.platform.runtime.system_memory import SystemMemory
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.lora.key_mapping import LoraDelta

_DIT_KEY = "native/dit/fake_krea2.safetensors"


@pytest.fixture(autouse=True)
def _reset_default_manager_singleton():
    """``ModelLifecycle.__init__`` sets a module-level
    ``_default_lifecycle`` singleton the first time one is constructed in the
    process and never again - so a lifecycle (and its cached ``NativeModel``)
    built by an earlier test in this session stays reachable, which would
    otherwise defeat this file's process-wide ``gc.get_objects()`` scans for
    live ``NativeModel`` instances. Reset it around every test in this file so
    each test's lifecycle starts and ends as the only one anyone can reach."""
    manager_module._default_lifecycle = None
    yield
    manager_module._default_lifecycle = None


@pytest.fixture(autouse=True)
def _plentiful_host_ram(monkeypatch):
    # Same seam-stub as test_lifecycle.py: `acquire()` reads real host RAM
    # through `get_system_memory()`, and on a small-RAM runner (CI has ~16GB,
    # squeezed further by xdist sibling workers) the RAM-pressure path evicts
    # entries mid-test - which fires allocator trims this file's
    # `trim_calls == []` asserts must not see. Pin it high; the RSS
    # measurements themselves read psutil directly and are unaffected.
    gb = 1024**3
    monkeypatch.setattr(
        manager_module,
        "get_system_memory",
        lambda: SystemMemory(total=int(256 * gb), available=int(200 * gb)),
    )

# Small enough to run in well under a second on CPU, big enough that a
# reintroduced full-model-sized cache duplication or a fragmentation
# regression shows up clearly against psutil's RSS noise floor.
_HIDDEN = 1536
_LAYERS = 6


def _build_fake_dit() -> nn.Module:
    layers = []
    for _ in range(_LAYERS):
        lin = nn.Linear(_HIDDEN, _HIDDEN, bias=False)
        lin.weight.data = lin.weight.data.to(torch.bfloat16)
        lin.lora_deltas = None
        layers.append(lin)
    return nn.Sequential(*layers)


def _build_fake_lora(seed: int, rank: int = 16) -> dict:
    g = torch.Generator().manual_seed(seed)
    sd = {}
    for i in range(_LAYERS):
        sd[f"{i}.lora_down.weight"] = torch.randn(rank, _HIDDEN, generator=g) * 0.01
        sd[f"{i}.lora_up.weight"] = torch.randn(_HIDDEN, rank, generator=g) * 0.01
        sd[f"{i}.alpha"] = torch.tensor(float(rank))
    return sd


def _stub_map_lora_keys(lora_sd, module):
    """Minimal stand-in for the arch-specific dialect map: '<i>.lora_down/up'
    onto '<i>.weight' Linear targets, in the exact ``LoraDelta`` shape
    ``apply_loras()`` expects. The dialect translation itself is exercised for
    real by ``test_krea2_lora.py`` / the key-mapping unit tests; this test's
    job is the memory-lifecycle behavior of ``apply_loras`` -> ``acquire()``,
    not re-deriving key dialects."""
    mapped = {}
    for i in range(_LAYERS):
        down = lora_sd[f"{i}.lora_down.weight"]
        up = lora_sd[f"{i}.lora_up.weight"]
        alpha = float(lora_sd[f"{i}.alpha"])
        mapped[f"{i}.weight"] = [
            LoraDelta(down=down, up=up, alpha=alpha, scale=1.0, target_slice=None, kron=False)
        ]
    return mapped, []


def _acquire_dit(manager: ModelLifecycle, lora_sd, strength: float = 0.8) -> NativeModel:
    """Mirrors the flux-style loader scheme (see module docstring): a stable
    key, LoRA stack folded only into the fingerprint, LoRAs applied inside the
    loader closure. Krea-2 itself no longer works this way as of a later change."""
    lora_fp = f"lora@{strength}:{id(lora_sd)}" if lora_sd is not None else "none"
    dit_fp = f"fake_krea2.safetensors|bfloat16|{lora_fp}"

    def loader() -> NativeModel:
        module = _build_fake_dit()
        native_model = NativeModel(kind="diffusion_model", module=module, device="cpu")
        if lora_sd is not None:
            lora_apply.apply_loras(module, [(lora_sd, strength)])
        return native_model

    est_gb = (_HIDDEN * _HIDDEN * 2 * _LAYERS) / (1024 ** 3)
    return manager.acquire(key=_DIT_KEY, fingerprint=dit_fp, loader=loader, estimated_vram_gb=est_gb)


def _live_native_model_count() -> int:
    """How many ``NativeModel`` instances are reachable process-wide. Safe to
    use as an exact count within this file because ``_reset_default_manager_
    singleton`` guarantees no other test's manager/cache is still reachable."""
    gc.collect()
    return sum(1 for o in gc.get_objects() if type(o).__name__ == "NativeModel")


def _rss_mb() -> float:
    gc.collect()
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def test_lora_swap_key_stays_stable_and_single_entry(monkeypatch):
    """A LoRA change must bust only the DiT's fingerprint, never its key -
    each swap should leave the cache with exactly one entry (the key-proliferation
    hypothesis - refuted; pin it so it can't come back)."""
    monkeypatch.setattr(lora_apply, "map_lora_keys", _stub_map_lora_keys)
    manager = ModelLifecycle(gpu_monitor=None, settings=None)
    lora_a = _build_fake_lora(seed=1)
    lora_b = _build_fake_lora(seed=2)

    for lora_sd in (None, lora_a, lora_b, lora_a):
        _acquire_dit(manager, lora_sd)
        manager.cleanup(aggressive=True)  # mirrors generation.py's end-of-generation trim
        stats = manager.stats()
        assert stats["entries"] == 1
        assert stats["keys"] == [_DIT_KEY]


def test_lora_swap_never_leaves_a_second_live_native_model(monkeypatch):
    """Every fingerprint-bust eviction must actually unload the OLD model
    (refcount<=2 sole-owner fast path) before the new one is built - if
    something in the real flow held a stale reference (the stale-reference
    hypothesis / the then-dead "Clear VRAM & Cache" button), this would show up as more than
    one live ``NativeModel`` at once. Refuted for this flow; pin it."""
    monkeypatch.setattr(lora_apply, "map_lora_keys", _stub_map_lora_keys)
    manager = ModelLifecycle(gpu_monitor=None, settings=None)
    lora_a = _build_fake_lora(seed=1)
    lora_b = _build_fake_lora(seed=2)

    for lora_sd in (None, lora_a, lora_b, lora_a):
        _acquire_dit(manager, lora_sd)
        manager.cleanup(aggressive=True)
        assert _live_native_model_count() == 1


def test_lora_swap_rss_returns_to_baseline_after_invalidate(monkeypatch):
    """The confirmed mechanism: applying a LoRA leaves host RSS elevated
    even after ``cleanup(aggressive=True)`` (glibc arena fragmentation from
    ``_compute_delta``'s transient per-Linear buffers, not a live reference -
    the tensor-byte census is identical before/after). That residue is only
    reclaimed once the model itself is ALSO evicted, e.g. by
    ``ModelLifecycle.invalidate()`` (the real "Clear VRAM & Cache (RAM)"
    action). Assert the qualitative invariant rather than a fixed MB
    threshold (allocator behavior is environment-sensitive) - the case that
    actually matters: invalidate() must recover memory below where it stood
    before the LoRA-bearing load, since it now holds nothing at all."""
    monkeypatch.setattr(lora_apply, "map_lora_keys", _stub_map_lora_keys)
    manager = ModelLifecycle(gpu_monitor=None, settings=None)
    lora_a = _build_fake_lora(seed=1)

    _acquire_dit(manager, None)
    manager.cleanup(aggressive=True)
    rss_before_lora = _rss_mb()

    _acquire_dit(manager, lora_a)
    manager.cleanup(aggressive=True)  # the existing post-swap / post-generation trim
    rss_after_lora = _rss_mb()

    manager.invalidate()  # "Clear VRAM & Cache (RAM)" at idle
    rss_after_invalidate = _rss_mb()

    assert manager.stats()["entries"] == 0

    # The regression this pins: invalidate() must be able to reclaim AT LEAST
    # as much as the LoRA-bearing load added, once it also drops the model
    # itself. A tolerance keeps this robust to run-to-run allocator noise
    # while still catching the reported failure mode (invalidate()
    # doing nothing at idle).
    tolerance_mb = 16.0
    assert rss_after_invalidate <= rss_before_lora + tolerance_mb, (
        f"invalidate() at idle did not recover memory to baseline: "
        f"before_lora={rss_before_lora:.1f}MB after_lora={rss_after_lora:.1f}MB "
        f"after_invalidate={rss_after_invalidate:.1f}MB"
    )


def test_orphaned_dit_is_eventually_freed_and_trimmed_after_stale_holder_releases(monkeypatch):
    """Production sequence from the maintainer's 5090 profiler run: gen1
    leaves the DiT GPU-resident on success (the real
    ``BaseGeneratorPipe.process()`` only calls ``release_gpu()`` on the ERROR
    path - see ``generator_base.py`` - so a successful generation's DiT stays
    resident on purpose, a warm-start optimization). Something outside the
    cache still held a reference at that exact moment (refcount=3 in the
    maintainer's log; the identity of that holder was NOT conclusively
    isolated - a minimal repro of cache+NativeGenerator+residency-tracking
    alone does NOT reproduce refcount=3, so something in the full
    generation.py/pipe execution machinery holds it. The referrer-diagnostic
    logging added alongside this fix is meant to name it from the next real
    incident's logs). Gen2's fingerprint-bust eviction is refcount-blocked
    (A3 safety, correctly NOT weakened here) and drops the cache entry
    without unloading - before this fix, that was the end of the story: the
    model then sits GPU-resident-but-uncached, gets offloaded to host RAM by
    the next VRAM-pressure event (``ensure_free``), and NOTHING ever frees or
    trims it again, however long the stale holder happens to survive.

    This test is deliberately CPU-only-safe (no real CUDA transfer) - it
    talks to ``GpuResidencyRegistry``'s bookkeeping directly (``note_resident``/
    ``mark_orphaned`` are pure dict+weakref logic, no device operation).
    FAILS before this fix (mark_orphaned/finalizer didn't exist - eviction
    dropped the reference with no way to ever detect or trim the eventual
    release); PASSES after (the finalizer registered by ``mark_orphaned``
    guarantees a host-allocator trim fires the moment the stale holder's
    reference is dropped, regardless of timing)."""
    from src.platform.runtime.native.memory import residency as residency_module
    from src.platform.runtime.native.memory.residency import get_residency_registry

    monkeypatch.setattr(lora_apply, "map_lora_keys", _stub_map_lora_keys)
    # Isolate from any other test's tracked entries in the process-global
    # residency registry (same singleton-leak concern as _default_lifecycle).
    monkeypatch.setattr(residency_module, "_registry", residency_module.GpuResidencyRegistry())
    residency = get_residency_registry()

    trim_calls = []
    monkeypatch.setattr(manager_module, "trim_host_allocator", lambda: trim_calls.append(1))

    manager = ModelLifecycle(gpu_monitor=None, settings=None)

    # gen1: acquire the DiT (no LoRA) and leave it GPU-resident on "success" -
    # note_resident is pure bookkeeping (dict + weakref), safe without a real
    # CUDA device.
    dit_model = _acquire_dit(manager, None)
    residency.note_resident(dit_model, "cuda", 0.01)

    # THE STALE HOLDER: models the as-yet-unidentified production reference
    # that survives past "gen1 ends" (refcount=3 in the maintainer's log).
    # `dit_model` itself must not be kept as a separate local past this point -
    # stale_holder becomes the ONLY reference, so clearing it is what actually
    # drops the last one (a lingering `dit_model` local here would keep the
    # object alive until THIS test function's own frame exits, masking the
    # fix entirely - caught empirically: the finalizer only fired during
    # pytest's own teardown until this was fixed).
    stale_holder = [dit_model]
    del dit_model

    # gen2: model_loader acquires under a NEW (LoRA) fingerprint -> fingerprint
    # bust -> refcount-blocked eviction (dit_model + stale_holder's own entry
    # + acquire()'s local var + getrefcount's transient arg > 2) -> the entry
    # is dropped from the cache WITHOUT unloading, exactly like production.
    lora_a = _build_fake_lora(seed=1)
    _acquire_dit(manager, lora_a)
    assert manager.stats()["entries"] == 1  # only the NEW dit; old one is orphaned, not double-cached
    assert len(residency._entries) == 1  # old dit still GPU-resident-tracked, now orphaned

    # No trim has happened yet for the orphaned model - the stale holder is
    # still alive.
    assert trim_calls == []

    # The stale holder finally releases (the common/recoverable case this fix
    # targets - nothing in the pipe/pipeline layer is DESIGNED to hold a
    # generation's models forever, so eventually it lets go).
    stale_holder.clear()
    gc.collect()

    # The finalizer registered by mark_orphaned() must have fired by now,
    # trimming the host allocator - this is the guarantee that closes the gap
    # regardless of whether ensure_free/offload_all ever gets a second chance
    # to see the (by then already-dead) model.
    assert trim_calls, (
        "orphaned DiT's stale holder released but no host-allocator trim ever "
        "fired - the freed multi-GB weights would sit fragmented/unreturned "
        "with no code path noticing"
    )


def test_bundle_retention_across_generations_does_not_keep_dit_alive(monkeypatch):
    """CONFIRMED holder: the maintainer's referrer diagnostic
    (added alongside the orphan fix above) named a live ``Krea2ModelBundle``
    instance as what kept an evicted, orphaned Krea-2 DiT resident forever
    after a LoRA swap (+25GB stuck host RAM). Unlike the generic "some stale
    holder" scenario in the test above, a re-run with the orphan fix already
    live showed the SAME +25GB stuck - meaning this holder never releases, so
    the finalizer-on-eventual-release safety net never fires either. The fix
    has to be at the bundle itself: ``Krea2ModelBundle`` (and every other
    native family's identically-shaped bundle - see ``WeakModelRef``'s module
    docstring: Flux/Qwen/Z-Image/Anima/Wan/LTX/SeedVR2 all share the exact
    same "lightweight view, not an owner" idiom, so all were fixed the same
    way) now holds its ``dit``/``te``/``vae`` fields as weak references, so a
    PERMANENT holder of the bundle object (this test never releases it -
    that's the point) cannot keep the underlying ``NativeModel`` alive once
    the cache itself has moved on to a new fingerprint.

    Confirmed FAILS before the ``WeakModelRef`` fix (a real, un-patched
    ``Krea2ModelBundle`` holds ``dit`` as a plain strong dataclass field, so a
    permanent holder keeps the evicted DiT's refcount above the sole-owner
    threshold forever - orphaned but never freed, matching the maintainer's
    unchanged +25GB) and PASSES after (checked directly against a pristine
    copy of the pre-fix ``bundle.py`` via ``git show HEAD:...`` - never via
    ``git stash``, which must not touch this multi-session working tree)."""
    from src.pipelines.pipes.model_loader.krea2.bundle import Krea2ModelBundle

    monkeypatch.setattr(lora_apply, "map_lora_keys", _stub_map_lora_keys)
    manager = ModelLifecycle(gpu_monitor=None, settings=None)

    dit_model = _acquire_dit(manager, None)
    te_model = NativeModel(kind="text_encoder", module=nn.Linear(8, 8), estimated_vram_gb=0.001, device="cpu")
    vae_model = NativeModel(kind="vae", module=nn.Linear(8, 8), estimated_vram_gb=0.001, device="cpu")
    bundle = Krea2ModelBundle(dit=dit_model, te=te_model, vae=vae_model)
    # Only the bundle and the cache should matter from here on - drop the
    # local strong references the constructor call itself needed.
    del dit_model, te_model, vae_model

    # THE PERMANENT HOLDER: models the CONFIRMED production shape - something
    # retains the bundle itself, across generations, and NEVER releases it
    # (unlike the generic "eventually lets go" scenario in the test above).
    permanent_holder = [bundle]

    lora_a = _build_fake_lora(seed=1)
    _acquire_dit(manager, lora_a)

    assert manager.stats()["entries"] == 1  # only the new dit is cached

    # The critical assertion: even though `permanent_holder` NEVER releases
    # `bundle`, the OLD dit must be gone - Krea2ModelBundle no longer keeps it
    # alive on its own, so the cache's own eviction (sole remaining owner) was
    # free to unload it immediately, with no need to wait for anything.
    assert permanent_holder[0].dit is None, (
        "Krea2ModelBundle still returns the evicted DiT via a permanently-"
        "retained bundle instance - the bundle is holding a STRONG reference "
        "to a component the cache has already moved on from"
    )


def test_tensor_level_diagnostic_fires_when_a_raw_parameter_survives_clean_unload(monkeypatch, caplog):
    """A production capture showed the DiT (~27GB)
    and TE (~23GB) BOTH pass the wrapper-level ``sys.getrefcount(value) <= 2``
    check and run ``unload()`` cleanly on a fingerprint-bust eviction -- yet
    free host RAM did not move AT ALL for either, unlike a 1.35GB VAE
    eviction in the SAME capture, which did free the expected amount. That
    means something holds the underlying PARAMETER TENSORS directly,
    bypassing the ``NativeModel`` wrapper's own refcount entirely -- a
    failure mode the EXISTING ``_log_referrer_diagnostic`` cannot see (it only
    ever fires on the ``refcount > 2`` branch, i.e. only when the WRAPPER
    itself is over-referenced).

    This pins the fix: ``_evict_entry`` now takes a weakref to a sample
    parameter tensor BEFORE calling unload(), and if that tensor survives the
    "clean" unload, logs a TENSOR-level referrer diagnostic naming whatever
    still holds the actual storage. Simulates the production shape with a
    structure that holds a weight tensor directly (not the NativeModel, not
    the bundle) -- exactly what a tensor-identity-keyed structure (rather than
    a NativeModel-keyed one) would look like.
    """
    monkeypatch.setattr(lora_apply, "map_lora_keys", _stub_map_lora_keys)
    manager = ModelLifecycle(gpu_monitor=None, settings=None)

    model_a = _acquire_dit(manager, None)
    # THE STALE HOLDER, at the TENSOR level: something outside the cache (and
    # outside the NativeModel wrapper) keeps a direct reference to one of the
    # DiT's weight tensors.
    stale_tensor_holder = [model_a.module[0].weight]
    # The WRAPPER itself must NOT be held past this point -- its own refcount
    # check still has to pass (sole cache owner), matching the production
    # capture where unload() ran at all.
    del model_a

    lora_a = _build_fake_lora(seed=1)
    with caplog.at_level(logging.WARNING):
        _acquire_dit(manager, lora_a)  # fingerprint bust -> evicts the OLD dit

    assert any("unload() ran" in r.message and "STILL ALIVE" in r.message for r in caplog.records), (
        "expected the new 'unload() ran ... but a sample weight tensor is STILL ALIVE' warning"
    )
    assert any("TENSOR-level referrer diagnostic" in r.message for r in caplog.records), (
        "expected the new tensor-level referrer diagnostic to fire and name the holder"
    )
    assert stale_tensor_holder[0] is not None  # sanity: our own hold is real and is the point


def test_tensor_level_diagnostic_is_silent_on_a_genuinely_clean_unload(monkeypatch, caplog):
    """No false positives: when nothing holds a raw parameter tensor outside
    the module tree, the new tensor-level warning must not fire."""
    monkeypatch.setattr(lora_apply, "map_lora_keys", _stub_map_lora_keys)
    manager = ModelLifecycle(gpu_monitor=None, settings=None)

    _acquire_dit(manager, None)
    lora_a = _build_fake_lora(seed=1)
    with caplog.at_level(logging.WARNING):
        _acquire_dit(manager, lora_a)

    assert not any("TENSOR-level referrer diagnostic" in r.message for r in caplog.records)
    assert not any("STILL ALIVE" in r.message for r in caplog.records)
