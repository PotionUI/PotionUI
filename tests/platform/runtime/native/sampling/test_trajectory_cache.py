"""Unit tests for the trajectory warm-start cache (model-free)."""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.trajectory_cache import (
    CheckpointCaptureHook,
    TrajectoryCache,
    TrajectoryEntry,
    checkpoint_steps,
    cosine,
    decide_resume,
    get_trajectory_cache,
    pooled_fingerprint,
    schedule_signature,
)


def _unit(cos: float) -> torch.Tensor:
    """A 2-D unit vector whose cosine with [1,0] is exactly ``cos``."""
    return torch.tensor([cos, (1.0 - cos * cos) ** 0.5], dtype=torch.float32)


_REF = torch.tensor([1.0, 0.0])


# --- checkpoint marks -----------------------------------------------------

def test_checkpoint_steps_interior_and_deduped():
    assert checkpoint_steps(8) == [2, 4, 6]
    assert checkpoint_steps(20) == [5, 10, 15]
    # tiny schedules: marks that collapse to 0 or N are dropped.
    assert 0 not in checkpoint_steps(3)
    assert all(0 < k < 4 for k in checkpoint_steps(4))


# --- fingerprint + cosine -------------------------------------------------

def test_pooled_fingerprint_shape_and_cpu():
    # mean AND std over non-feature axes -> 2*D (S12).
    fp = pooled_fingerprint(torch.randn(2, 7, 16))
    assert fp.shape == (32,)
    assert fp.device.type == "cpu" and fp.dtype == torch.float32


def test_pooled_fingerprint_distinguishes_token_structure():
    # Mean-only pooling would collapse [u+d, u-d] and [u+100d, u-100d] to the same
    # u; the std half must tell them apart (S12).
    u = torch.ones(16)
    d = torch.arange(16, dtype=torch.float32)
    a = torch.stack([u + d, u - d]).unsqueeze(0)          # (1, 2, 16)
    b = torch.stack([u + 100 * d, u - 100 * d]).unsqueeze(0)
    assert not torch.allclose(pooled_fingerprint(a), pooled_fingerprint(b))


def test_conditioning_fingerprint_includes_negative():
    from src.platform.runtime.native.sampling.trajectory_cache import conditioning_fingerprint
    cond = {"context": torch.randn(1, 4, 8)}
    neg_a = {"context": torch.randn(1, 4, 8)}
    neg_b = {"context": torch.randn(1, 4, 8)}
    # same positive, different negative -> different fingerprint (S12).
    fa = conditioning_fingerprint(cond, neg_a)
    fb = conditioning_fingerprint(cond, neg_b)
    assert not torch.allclose(fa, fb)
    # no uncond -> zeros-padded to the same width as the with-uncond vector.
    assert conditioning_fingerprint(cond, None).shape == fa.shape


def test_cosine_identical_and_mismatch():
    assert cosine(_REF, _REF) == 1.0
    assert cosine(_REF, torch.tensor([1.0, 0.0, 0.0])) == 0.0  # shape mismatch -> 0
    assert abs(cosine(_REF, _unit(0.99)) - 0.99) < 1e-5


# --- resume ladder --------------------------------------------------------

def _entry(fp, checkpoints, total_steps=8, sched="sig"):
    e = TrajectoryEntry(static_key=("k",), total_steps=total_steps, schedule_sig=sched)
    e.cond_fingerprint = fp
    e.checkpoints = dict(checkpoints)
    return e


def test_resume_depth_follows_similarity():
    marks = {2: torch.zeros(4), 4: torch.ones(4), 6: torch.full((4,), 2.0)}
    e = _entry(_REF, marks)
    # >0.995 -> 0.75 -> step 6
    assert decide_resume(e, _unit(0.999), 8, "sig").resume_step == 6
    # [0.98,0.995) -> 0.5 -> step 4
    assert decide_resume(e, _unit(0.99), 8, "sig").resume_step == 4
    # [0.95,0.98) -> 0.25 -> step 2
    assert decide_resume(e, _unit(0.96), 8, "sig").resume_step == 2
    # below the shallowest rung -> cold
    assert decide_resume(e, _unit(0.90), 8, "sig").resume_step == 0


def test_resume_picks_deepest_available_at_or_below_target():
    # similarity allows depth 0.75 (step 6) but only step 2 was captured.
    e = _entry(_REF, {2: torch.zeros(4)})
    plan = decide_resume(e, _unit(0.999), 8, "sig")
    assert plan.resume_step == 2
    assert torch.equal(plan.latent, torch.zeros(4))


def test_resume_cold_on_key_and_schedule_mismatch():
    e = _entry(_REF, {6: torch.zeros(4)})
    assert decide_resume(None, _REF, 8, "sig").resume_step == 0            # no entry
    assert decide_resume(e, _REF, 12, "sig").resume_step == 0             # step mismatch
    assert decide_resume(e, _REF, 8, "other").resume_step == 0            # schedule mismatch
    e2 = _entry(None, {6: torch.zeros(4)})
    assert decide_resume(e2, _REF, 8, "sig").resume_step == 0             # no stored fingerprint


# --- capture hook ---------------------------------------------------------

def test_capture_hook_stores_cpu_clones_at_marks_only():
    entry = TrajectoryEntry(("k",), total_steps=8, schedule_sig="sig")
    hook = CheckpointCaptureHook(entry, total_steps=8)
    x = torch.zeros(1, 4)
    for i in range(8):
        x = x + 1.0  # mutate each step
        hook.on_step(i, 8, x, float(i), None)
    # marks are steps 2/4/6 == on_step(i=1/3/5) -> x values 2/4/6.
    assert sorted(entry.checkpoints) == [2, 4, 6]
    assert entry.checkpoints[2].device.type == "cpu"
    assert float(entry.checkpoints[2][0, 0]) == 2.0
    # stored is a clone: further mutation of x does not change it.
    x += 100
    assert float(entry.checkpoints[6][0, 0]) == 6.0


# --- cache LRU ------------------------------------------------------------

def test_cache_lru_evicts_and_bounds():
    cache = TrajectoryCache(max_entries=2)
    a = cache.get_or_create(("a",), 8, "s")
    cache.get_or_create(("b",), 8, "s")
    cache.get(("a",))                       # touch a -> b is now LRU
    cache.get_or_create(("c",), 8, "s")     # evicts b
    assert len(cache) == 2
    assert cache.get(("a",)) is a
    assert cache.get(("b",)) is None
    assert cache.get(("c",)) is not None


def test_get_or_create_replaces_on_schedule_change():
    cache = TrajectoryCache()
    e1 = cache.get_or_create(("k",), 8, "s1")
    e1.checkpoints[4] = torch.zeros(2)
    e2 = cache.get_or_create(("k",), 8, "s2")  # schedule changed -> fresh entry
    assert e2 is not e1
    assert e2.checkpoints == {}


def test_singleton_is_stable():
    assert get_trajectory_cache() is get_trajectory_cache()


def test_schedule_signature_sensitive_to_schedule_keys():
    base = {"shift": 3.0, "guidance": "cfg"}
    a = schedule_signature(8, None, base)
    assert a == schedule_signature(8, None, {"shift": 3.0, "guidance": "cfg"})
    assert a != schedule_signature(8, None, {"shift": 2.0})      # shift changed
    assert a != schedule_signature(12, None, base)               # steps changed


# --- explicit-sigmas signature isolation --------------------------

def test_explicit_sigmas_signature_differs_from_equivalent_length_derived_schedule():
    base = {"shift": 3.0, "guidance": "cfg"}
    derived = schedule_signature(8, None, base)
    explicit = torch.tensor([1.0, 0.875, 0.75, 0.625, 0.5, 0.375, 0.25, 0.125, 0.0])
    assert len(explicit) - 1 == 8   # same nominal step count as `derived`
    explicit_sig = schedule_signature(8, None, base, explicit_sigmas=explicit)
    assert explicit_sig != derived


def test_explicit_sigmas_signature_stable_and_sensitive_to_values():
    a = torch.tensor([1.0, 0.5, 0.0])
    b = torch.tensor([1.0, 0.5, 0.0])
    c = torch.tensor([1.0, 0.6, 0.0])
    assert schedule_signature(2, None, {}, explicit_sigmas=a) == schedule_signature(2, None, {}, explicit_sigmas=b)
    assert schedule_signature(2, None, {}, explicit_sigmas=a) != schedule_signature(2, None, {}, explicit_sigmas=c)
