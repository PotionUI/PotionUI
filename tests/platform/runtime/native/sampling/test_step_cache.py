"""Unit tests for FirstBlockCache / StepCacheSet (model-free)."""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.step_cache import (
    FirstBlockCache,
    StepCacheSet,
    normalize_options,
)


def _probe(val: float, shape=(1, 4, 8)) -> torch.Tensor:
    return torch.full(shape, val, dtype=torch.float32)


def _out(val: float, shape=(1, 4, 8)) -> torch.Tensor:
    return torch.full(shape, val, dtype=torch.float32)


# --- disabled / default --------------------------------------------------

def test_zero_threshold_never_skips():
    c = FirstBlockCache(rel_threshold=0.0)
    assert c.enabled is False
    c.record_compute(_probe(1.0), _out(1.0))
    # identical probe would be a skip candidate, but a disabled cache never skips.
    assert c.should_skip(_probe(1.0)) is False


def test_first_step_never_skips_no_prev():
    c = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    assert c.should_skip(_probe(1.0)) is False  # prev_probe is None


# --- warmup --------------------------------------------------------------

def test_warmup_blocks_skipping():
    c = FirstBlockCache(rel_threshold=1.0, warmup_steps=3)
    # feed identical probes; even though rel==0 < threshold, warmup forbids skips.
    for _ in range(3):
        assert c.should_skip(_probe(1.0)) is False
        c.record_compute(_probe(1.0), _out(1.0))
    # steps_seen == 3 == warmup_steps -> skipping now allowed.
    assert c.should_skip(_probe(1.0)) is True


# --- threshold gating ----------------------------------------------------

def test_threshold_gates_on_relative_change():
    c = FirstBlockCache(rel_threshold=0.1, warmup_steps=0)
    c.record_compute(_probe(1.0), _out(10.0))
    # rel = |1.05-1.0| / 1.0 = 0.05 < 0.1 -> skip.
    assert c.should_skip(_probe(1.05)) is True
    # rel = |1.5-1.0| / 1.0 = 0.5 >= 0.1 -> compute.
    assert c.should_skip(_probe(1.5)) is False


def test_relative_change_is_fp32_and_correct():
    c = FirstBlockCache(rel_threshold=0.1)
    c.prev_probe = _probe(2.0)
    # mean|3-2| / (mean|2| + eps) = 1/2 = 0.5, per-sample (batch=1 here).
    rel = c.relative_change(_probe(3.0))
    assert rel.shape == (1,)
    assert abs(rel.item() - 0.5) < 1e-6


# --- per-sample gating (batch>1 correctness, roadmap S4/#4) --------------

def test_relative_change_is_computed_per_sample():
    """Each batch element gets its own relative-change scalar; a stable
    sample must not be averaged together with a changed one."""
    c = FirstBlockCache(rel_threshold=0.1)
    prev = torch.stack([_probe(1000.0).squeeze(0), _probe(0.0).squeeze(0)])  # (2, 4, 8)
    c.prev_probe = prev
    cur = torch.stack([_probe(1000.0).squeeze(0), _probe(1.0).squeeze(0)])   # sample 1: 0 -> 1
    rel = c.relative_change(cur)
    assert rel.shape == (2,)
    assert rel[0].item() < 1e-6          # sample 0 unchanged
    assert rel[1].item() > 1e6           # sample 1: |1-0|/(0+eps), huge


def test_should_skip_requires_every_sample_below_threshold():
    """The exact failure scenario from the finding: a stable high-magnitude
    sample must not mask an arbitrarily changed low-magnitude sample. Pooling
    the whole batch into one mean gives a tiny (wrongly skip-eligible) global
    ratio; per-sample gating must refuse to skip."""
    c = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    prev = torch.tensor([[[1000.0]], [[0.0]]])  # (2, 1, 1)
    c.record_compute(prev, _out(1.0, shape=(2, 1, 1)))
    cur = torch.tensor([[[1000.0]], [[1.0]]])   # sample 0 stable, sample 1: 0 -> 1
    # A batch-pooled mean would give |1|/|1000| = 0.001 < 0.01 and wrongly skip.
    assert c.should_skip(cur) is False


def test_should_skip_true_when_every_sample_is_stable():
    c = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    prev = torch.tensor([[[1000.0]], [[500.0]]])
    c.record_compute(prev, _out(1.0, shape=(2, 1, 1)))
    cur = torch.tensor([[[1000.0]], [[500.0]]])  # both samples unchanged
    assert c.should_skip(cur) is True


def test_record_skip_returns_cached_output():
    c = FirstBlockCache(rel_threshold=0.1, warmup_steps=0)
    out = _out(7.0)
    c.record_compute(_probe(1.0), out)
    got = c.record_skip()
    assert torch.equal(got, out)


# --- consecutive-skip ceiling -------------------------------------------

def test_max_consecutive_skips_ceiling():
    c = FirstBlockCache(rel_threshold=1.0, warmup_steps=0, max_consecutive_skips=2)
    c.record_compute(_probe(1.0), _out(1.0))
    # identical probe: rel==0 < 1.0, so gated only by the consecutive ceiling.
    assert c.should_skip(_probe(1.0)) is True
    c.record_skip()
    assert c.should_skip(_probe(1.0)) is True
    c.record_skip()
    # two skips in a row -> ceiling hit, force a compute.
    assert c.should_skip(_probe(1.0)) is False
    c.record_compute(_probe(1.0), _out(1.0))  # resets skips_in_a_row
    assert c.should_skip(_probe(1.0)) is True


# --- shape mismatch (resolution change) ---------------------------------

def test_shape_mismatch_forces_compute():
    c = FirstBlockCache(rel_threshold=1.0, warmup_steps=0)
    c.record_compute(_probe(1.0, shape=(1, 4, 8)), _out(1.0, shape=(1, 4, 8)))
    # different token count -> cannot reuse; must compute.
    assert c.should_skip(_probe(1.0, shape=(1, 6, 8))) is False


# --- counters ------------------------------------------------------------

def test_counters_track_computed_and_skipped():
    c = FirstBlockCache(rel_threshold=1.0, warmup_steps=1)
    c.record_compute(_probe(1.0), _out(1.0))   # step 0 (warmup)
    assert c.should_skip(_probe(1.0)) is True
    c.record_skip()                            # step 1
    c.record_skip()                            # step 2 (manually)
    assert c.stats() == {"computed": 1, "skipped": 2}
    assert c.steps_seen == 3


# --- StepCacheSet --------------------------------------------------------

def test_cache_set_independent_per_branch():
    s = StepCacheSet({"rel_threshold": 0.1})
    cond = s.for_branch("cond")
    uncond = s.for_branch("uncond")
    assert cond is not uncond
    # same key returns the same instance.
    assert s.for_branch("cond") is cond


def test_cache_set_caps_branches():
    s = StepCacheSet({"rel_threshold": 0.1}, max_branches=2)
    a = s.for_branch("a")
    s.for_branch("b")
    # third distinct key does not mint a new cache; reuses an existing one.
    c = s.for_branch("c")
    assert c in (a, s.for_branch("b"))
    assert len(s._caches) == 2


def test_cache_set_totals_sum_branches():
    s = StepCacheSet({"rel_threshold": 1.0, "warmup_steps": 0})
    cond = s.for_branch("cond")
    cond.record_compute(_probe(1.0), _out(1.0))
    cond.record_skip()
    uncond = s.for_branch("uncond")
    uncond.record_compute(_probe(1.0), _out(1.0))
    assert s.totals() == {"computed": 2, "skipped": 1}


def test_cache_set_enabled_reflects_threshold():
    assert StepCacheSet({"rel_threshold": 0.1}).enabled is True
    assert StepCacheSet({"rel_threshold": 0.0}).enabled is False
    assert StepCacheSet(None).enabled is False


def test_normalize_options_defaults_and_drops_unknown():
    opts = normalize_options({"rel_threshold": 0.2, "bogus": 5})
    assert opts == {"rel_threshold": 0.2, "warmup_steps": 4, "max_consecutive_skips": 3}
    assert normalize_options(None)["rel_threshold"] == 0.0
