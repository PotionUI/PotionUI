"""Unit tests for FirstBlockCache / StepCacheSet (model-free)."""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.step_cache import (
    FirstBlockCache,
    GroupedProbe,
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
    b = s.for_branch("b")
    # A third distinct key evicts the least recently used one and gets its own
    # empty cache; the set stays bounded.
    c = s.for_branch("c")
    assert c is not a and c is not b
    assert len(s._caches) == 2
    assert list(s._caches) == ["b", "c"]


def test_cache_set_eviction_is_least_recently_used():
    s = StepCacheSet({"rel_threshold": 0.1}, max_branches=2)
    a = s.for_branch("a")
    s.for_branch("b")
    s.for_branch("a")            # touching "a" makes "b" the oldest
    s.for_branch("c")
    assert set(s._caches) == {"a", "c"}          # "b" was the oldest, not "a"
    assert s.for_branch("a") is a


def test_overflow_identity_cannot_replay_another_networks_velocity():
    """The reason the cap must evict rather than share: branch keys carry the
    routed network's identity, so handing a ninth identity the first one's
    warmed cache would replay a velocity that network never produced."""
    s = StepCacheSet({"rel_threshold": 1.0, "warmup_steps": 0}, max_branches=8)
    first = s.for_branch(("cond", "expert-0"))
    first_output = _out(42.0)
    first.record_compute(_probe(1.0), first_output)
    assert first.should_skip(_probe(1.0)) is True      # primed to skip

    for n in range(1, 8):                              # fills the cap
        s.for_branch(("cond", f"expert-{n}"))
    ninth = s.for_branch(("cond", "expert-8"))

    assert ninth is not first
    assert ninth.cached_output is None
    assert ninth.should_skip(_probe(1.0)) is False
    assert len(s._caches) == 8


def test_totals_keep_counting_evicted_branches():
    """A run report must still account for the steps an evicted cache gated."""
    s = StepCacheSet({"rel_threshold": 1.0, "warmup_steps": 0}, max_branches=1)
    first = s.for_branch("a")
    first.record_compute(_probe(1.0), _out(1.0))
    first.record_skip()
    second = s.for_branch("b")                         # evicts "a"
    second.record_compute(_probe(1.0), _out(1.0))
    assert s.totals() == {"computed": 2, "skipped": 1}


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


# --- per-modality gating (GroupedProbe) ----------------------------------

def _grouped(video: float, audio: float, video_rows: int = 1000, audio_rows: int = 2) -> GroupedProbe:
    """A probe shaped like a real multimodal step: a long video stream and a
    short audio one, each filled with a constant so the drift is exact."""
    return GroupedProbe(
        video=torch.full((1, video_rows, 8), video, dtype=torch.float32),
        audio=torch.full((1, audio_rows, 8), audio, dtype=torch.float32),
    )


def _flat_ratio(prev: GroupedProbe, cur: GroupedProbe) -> float:
    """What one flattened probe over both streams would have measured — the
    metric these tests exist to replace."""
    def flat(g):
        return torch.cat([t.flatten(1) for t in g.groups.values()], dim=1)
    ref, now = flat(prev), flat(cur)
    return ((now - ref).abs().mean() / (ref.abs().mean() + 1e-8)).item()


def test_stable_video_does_not_mask_a_changed_audio_stream():
    """The failure one flattened probe allows: audio is 0.2% of the elements,
    so a 10x jump in it barely moves the pooled ratio — the step skips and
    replays a completely stale audio velocity."""
    c = FirstBlockCache(rel_threshold=0.08, warmup_steps=0)
    prev, cur = _grouped(1.0, 1.0), _grouped(1.0, 10.0)
    c.record_compute(prev, _out(1.0))
    assert _flat_ratio(prev, cur) < 0.08          # a flattened probe would skip
    assert c.relative_changes(cur)["audio"].item() == 9.0
    assert c.should_skip(cur) is False


def test_stable_audio_does_not_mask_a_changed_video_stream():
    """The converse — a short video grid under a long audio track, which is
    what a still-frame clip with a long score looks like."""
    c = FirstBlockCache(rel_threshold=0.08, warmup_steps=0)
    prev = _grouped(1.0, 1.0, video_rows=2, audio_rows=1000)
    cur = _grouped(10.0, 1.0, video_rows=2, audio_rows=1000)
    c.record_compute(prev, _out(1.0))
    assert _flat_ratio(prev, cur) < 0.08
    assert c.relative_changes(cur)["video"].item() == 9.0
    assert c.should_skip(cur) is False


def test_unchanged_multimodal_probe_still_skips():
    c = FirstBlockCache(rel_threshold=0.08, warmup_steps=0)
    c.record_compute(_grouped(1.0, 1.0), _out(1.0))
    assert c.should_skip(_grouped(1.0, 1.0)) is True


def test_every_group_below_threshold_skips():
    """Both modalities drifting a little — under threshold each — is exactly
    what the cache exists to skip."""
    c = FirstBlockCache(rel_threshold=0.08, warmup_steps=0)
    c.record_compute(_grouped(1.0, 1.0), _out(1.0))
    assert c.should_skip(_grouped(1.02, 1.05)) is True


def test_absent_group_is_dropped_and_the_rest_still_gates():
    """A zero-length audio stream (no audio track) leaves the video group
    gating alone rather than dividing by an empty mean."""
    c = FirstBlockCache(rel_threshold=0.08, warmup_steps=0)
    empty = torch.zeros((1, 0, 8))
    c.record_compute(GroupedProbe(video=_probe(1.0), audio=empty), _out(1.0))
    assert c.prev_probe.groups.keys() == {"video"}
    assert c.should_skip(GroupedProbe(video=_probe(1.0), audio=empty)) is True
    assert c.should_skip(GroupedProbe(video=_probe(2.0), audio=empty)) is False


def test_probe_with_no_groups_never_skips():
    c = FirstBlockCache(rel_threshold=1.0, warmup_steps=0)
    c.record_compute(GroupedProbe(video=torch.zeros((1, 0, 8))), _out(1.0))
    assert c.should_skip(GroupedProbe(video=torch.zeros((1, 0, 8)))) is False


def test_modality_appearing_invalidates_the_cache():
    """Groups are matched by name: an audio stream arriving mid-run means the
    cached output has no audio to replay, so the step must compute."""
    c = FirstBlockCache(rel_threshold=1.0, warmup_steps=0)
    c.record_compute(GroupedProbe(video=_probe(1.0)), _out(1.0))
    assert c.should_skip(GroupedProbe(video=_probe(1.0), audio=_probe(1.0))) is False


def test_group_shape_mismatch_forces_compute():
    c = FirstBlockCache(rel_threshold=1.0, warmup_steps=0)
    c.record_compute(_grouped(1.0, 1.0), _out(1.0))
    assert c.should_skip(_grouped(1.0, 1.0, audio_rows=3)) is False


def test_grouped_probe_respects_warmup_and_skip_ceiling():
    """The gates that sit in front of the drift check are untouched by
    grouping."""
    c = FirstBlockCache(rel_threshold=1.0, warmup_steps=2, max_consecutive_skips=1)
    c.record_compute(_grouped(1.0, 1.0), _out(1.0))
    assert c.should_skip(_grouped(1.0, 1.0)) is False       # warmup
    c.record_compute(_grouped(1.0, 1.0), _out(1.0))
    assert c.should_skip(_grouped(1.0, 1.0)) is True
    c.record_skip()
    assert c.should_skip(_grouped(1.0, 1.0)) is False       # ceiling


def test_grouped_probe_gates_per_sample_within_a_group():
    """Per-sample gating survives grouping: a stable high-magnitude sample
    must not mask a changed low-magnitude one inside the same modality."""
    c = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    prev = torch.tensor([[[1000.0]], [[0.0]]])
    c.record_compute(GroupedProbe(video=prev), _out(1.0, shape=(2, 1, 1)))
    cur = torch.tensor([[[1000.0]], [[1.0]]])
    assert c.should_skip(GroupedProbe(video=cur)) is False


def test_a_non_finite_group_computes():
    """A probe that went NaN decides nothing — the comparison fails and the
    step runs the real model."""
    c = FirstBlockCache(rel_threshold=1.0, warmup_steps=0)
    c.record_compute(_grouped(1.0, 1.0), _out(1.0))
    poisoned = _grouped(1.0, 1.0)
    poisoned.groups["audio"] = torch.full((1, 2, 8), float("nan"))
    assert c.should_skip(poisoned) is False


def test_zero_magnitude_group_does_not_divide_by_zero():
    c = FirstBlockCache(rel_threshold=0.08, warmup_steps=0)
    c.record_compute(_grouped(1.0, 0.0), _out(1.0))
    rel = c.relative_changes(_grouped(1.0, 0.0))["audio"]
    assert torch.isfinite(rel).all() and rel.item() == 0.0
    assert c.should_skip(_grouped(1.0, 0.0)) is True
    assert c.should_skip(_grouped(1.0, 1.0)) is False


# --- single-group callers are unchanged ----------------------------------

def test_bare_tensor_and_one_group_probe_decide_identically():
    """Every single-stream arch still hands in a bare tensor; wrapping the
    same tensor in one group must not change a single decision."""
    bare = FirstBlockCache(rel_threshold=0.1, warmup_steps=1)
    grouped = FirstBlockCache(rel_threshold=0.1, warmup_steps=1)
    for val in (1.0, 1.05, 1.06, 1.5, 1.5, 1.5):
        probe = _probe(val)
        b = bare.should_skip(probe)
        g = grouped.should_skip(GroupedProbe(video=probe))
        assert b == g
        if b:
            bare.record_skip()
            grouped.record_skip()
        else:
            bare.record_compute(probe, _out(val))
            grouped.record_compute(GroupedProbe(video=probe), _out(val))
    assert bare.stats() == grouped.stats()
