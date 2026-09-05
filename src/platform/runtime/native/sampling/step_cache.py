"""First-Block Cache (FBCache) — skip whole DiT forwards when they barely change.

Across most of a diffusion trajectory the transformer's output changes slowly
from one step to the next, so re-running every block each step is wasted work.
FBCache (chengzeyi/ParaAttention; a calibration-free cousin of TeaCache,
arXiv:2411.19108) exploits this with a *cheap proxy*: the output of the model's
**first transformer block**. If block-0's output barely moved versus the last
step we actually computed, the full output will barely move too, so we return
the cached output and skip blocks 1..N and the final projection.

Output caching vs. residual caching
-----------------------------------
The classic FBCache formulation caches the OUTPUT-side residual
``(output - input)`` and reconstructs ``x + cached_residual`` on a skip. That
assumes the input ``x`` is the right anchor to add the residual back onto. Our
models are flow-matching v-predictors: the network output ``v`` is the velocity
that points from noise toward data, and across *adjacent* steps ``v`` itself is
the smooth quantity (it drifts slowly), whereas ``x`` changes every step. So we
cache and return the model **output ``v`` directly** (output caching). The
block-0 probe is exactly the gate that justifies it: a near-zero relative change
in block-0 predicts a near-zero change in ``v``. This also avoids taking any
position on what "input" the residual should attach to — a per-family question
(packed tokens? unpacked latent?) that output caching sidesteps entirely.

This class holds no torch/model imports beyond the probe/output tensors it is
handed; it is pure state + a float comparison so it unit-tests without a model.
"""

from __future__ import annotations

import logging
from collections import OrderedDict

import torch

logger = logging.getLogger(__name__)

Tensor = torch.Tensor


class GroupedProbe:
    """A block-0 probe split into per-modality row groups, each gated on its own.

    One flattened probe lets a big modality hide a small one. A multimodal
    forward whose short audio stream moved sharply while its much larger video
    stream held still averages out under the threshold — the step skips and
    replays a stale audio velocity — and the converse hides a changed video
    behind a stable audio track. Condition and text rows make it worse: they
    are pinned across the trajectory, so every one of them drags the pooled
    mean toward zero.

    Each group is compared against the previous step's group of the same name,
    and a step may only be skipped when EVERY group passes the threshold.
    Groups passed as ``None`` or with no elements are dropped, so an absent
    audio track leaves the video group gating alone; a probe with no group at
    all never skips.
    """

    __slots__ = ("groups",)

    def __init__(self, **groups: Tensor | None) -> None:
        self.groups: dict[str, Tensor] = {
            name: t for name, t in groups.items() if t is not None and t.numel() > 0
        }

    def detach(self) -> "GroupedProbe":
        return GroupedProbe(**{name: t.detach() for name, t in self.groups.items()})


_SINGLE_GROUP = ""


def _probe_groups(probe) -> dict[str, Tensor]:
    """Normalize a probe to name -> tensor. A bare tensor is one anonymous
    group, which is what every single-stream arch hands in."""
    if isinstance(probe, GroupedProbe):
        return probe.groups
    return {_SINGLE_GROUP: probe}


def _relative_change(cur: Tensor, ref: Tensor) -> Tensor:
    """Per-sample ``mean|cur - ref| / (mean|ref| + eps)`` in fp32, reduced over
    every dim except the batch dim — shape ``(B,)``."""
    cur = cur.detach().float()
    ref = ref.float()
    reduce_dims = tuple(range(1, cur.ndim))
    num = (cur - ref).abs().mean(dim=reduce_dims)
    den = ref.abs().mean(dim=reduce_dims) + 1e-8
    return num / den


def _detach_tree(output):
    """Detach every tensor in a tensor / tuple / list, preserving structure.

    Single-tensor outputs (the image-DiT common case) are byte-identical to a
    plain ``.detach()``. Nested containers (LTX's ``[video, audio]`` /
    ``(video, audio, extra)``, ``None`` slots included) are rebuilt with each
    tensor detached and everything else passed through unchanged.
    """
    if isinstance(output, torch.Tensor):
        return output.detach()
    if isinstance(output, tuple):
        return tuple(_detach_tree(o) for o in output)
    if isinstance(output, list):
        return [_detach_tree(o) for o in output]
    return output


class FirstBlockCache:
    """Per-branch step cache: gate on block-0 drift, reuse the last output.

    One instance tracks ONE guidance branch (cond and uncond trajectories differ
    — sharing a cache across them corrupts both). The arch forward drives it:

        if step_cache is not None and step_cache.should_skip(probe):
            return step_cache.record_skip()          # reuse cached output
        ...run blocks 1..N + final projection -> out...
        if step_cache is not None:
            step_cache.record_compute(probe, out)
        return out

    ``rel_threshold`` gates skipping: ``0.0`` disables the cache (never skips);
    typical live values are 0.08 (conservative, ~1.3x) to 0.15 (aggressive,
    ~1.8-2.2x). ``warmup_steps`` forces real compute for the first N steps (early
    layout formation changes fast and must not be cached). ``max_consecutive_skips``
    caps how many steps in a row may be skipped so drift can't accumulate
    unbounded before a fresh probe re-anchors the cache.
    """

    def __init__(
        self,
        rel_threshold: float = 0.0,
        warmup_steps: int = 4,
        max_consecutive_skips: int = 3,
    ) -> None:
        self.rel_threshold = float(rel_threshold)
        self.warmup_steps = int(warmup_steps)
        self.max_consecutive_skips = int(max_consecutive_skips)

        # block-0 output of the last computed step: a bare tensor from a
        # single-stream arch, a GroupedProbe from a multimodal one.
        self.prev_probe: Tensor | GroupedProbe | None = None
        self.cached_output: Tensor | None = None     # full model output of last computed step
        self.skips_in_a_row = 0
        self.steps_seen = 0                           # computed + skipped, this branch

        # Reporting counters (see :meth:`stats`).
        self.steps_computed = 0
        self.steps_skipped = 0

    @property
    def enabled(self) -> bool:
        return self.rel_threshold > 0.0

    def should_skip(self, probe: Tensor | GroupedProbe) -> bool:
        """Return whether this step may reuse the cached output.

        Pure read — never mutates state (the arch calls :meth:`record_skip` or
        :meth:`record_compute` to commit the decision). ``False`` during warmup,
        when there is no cached probe yet, when the probe's groups or their
        shapes changed (a resolution change, or a modality appearing or
        vanishing, invalidates the cache), or when the consecutive-skip ceiling
        is hit. Otherwise skip iff the block-0 relative change is below
        ``rel_threshold`` for EVERY sample in the batch, in EVERY group of a
        :class:`GroupedProbe` (see :meth:`relative_changes` — a pooled mean
        lets one stable high-magnitude sample, or one stable large modality,
        mask an arbitrarily changed small one). The relative change is computed
        in fp32 regardless of the model's compute dtype so the gate is stable
        under bf16/fp16.
        """
        if not self.enabled:
            return False
        if self.prev_probe is None or self.cached_output is None:
            return False
        cur = _probe_groups(probe)
        prev = _probe_groups(self.prev_probe)
        if not cur or cur.keys() != prev.keys():
            return False
        if any(cur[name].shape != prev[name].shape for name in cur):
            return False
        if self.steps_seen < self.warmup_steps:
            return False
        if self.skips_in_a_row >= self.max_consecutive_skips:
            return False
        # A non-finite ratio (a probe that went NaN/inf) fails this comparison,
        # so a poisoned step computes rather than skipping.
        return all(bool((rel < self.rel_threshold).all()) for rel in self.relative_changes(probe).values())

    def relative_changes(self, probe: Tensor | GroupedProbe) -> dict[str, Tensor]:
        """Per-group, per-sample ``mean|probe - prev_probe| / (mean|prev_probe|
        + eps)`` in fp32, each reduced over every dim except the batch dim (dim
        0) — a ``(B,)`` tensor per group.

        Per-sample, not a single batch-pooled scalar: a quantity>1 generation
        runs several independent trajectories through one forward, and pooling
        their block-0 drift into one mean lets a stable high-magnitude sample
        hide an arbitrarily large relative change in a low-magnitude sample,
        corrupting BOTH when the pooled mean stays under threshold (see the
        failure scenario in
        ``test_should_skip_requires_every_sample_below_threshold``). The
        per-group split is the same argument one axis up, across modalities
        rather than across batch rows — see :class:`GroupedProbe`.
        """
        prev = _probe_groups(self.prev_probe)
        return {name: _relative_change(t, prev[name]) for name, t in _probe_groups(probe).items()}

    def relative_change(self, probe: Tensor) -> Tensor:
        """The single-group form of :meth:`relative_changes` — ``(B,)`` for a
        bare-tensor probe."""
        prev = self.prev_probe
        assert isinstance(prev, torch.Tensor)
        return _relative_change(probe, prev)

    def record_skip(self) -> Tensor:
        """Commit a skip and return the cached output to reuse."""
        assert self.cached_output is not None
        self.skips_in_a_row += 1
        self.steps_skipped += 1
        self.steps_seen += 1
        return self.cached_output

    def record_compute(self, probe: Tensor | GroupedProbe, output) -> None:
        """Commit a real compute: refresh the probe/output anchors.

        ``output`` is whatever the arch forward returns — a single tensor for
        image DiTs, or a nested tuple/list for multi-stream models (LTX's
        ``[video, audio]`` / ``(video, audio, extra)``). :func:`_detach_tree`
        detaches every tensor within it while leaving the structure (and any
        ``None`` slots) intact, so ``record_skip`` can hand the exact same
        structure back.
        """
        self.prev_probe = probe.detach()
        self.cached_output = _detach_tree(output)
        self.skips_in_a_row = 0
        self.steps_computed += 1
        self.steps_seen += 1

    def stats(self) -> dict[str, int]:
        return {"computed": self.steps_computed, "skipped": self.steps_skipped}


class StepCacheSet:
    """Lazily mints one :class:`FirstBlockCache` per guidance branch.

    ``denoise()`` builds one set per generation and asks it for a branch cache
    keyed by an opaque branch label — ``("cond"|"uncond", network identity)``,
    since a multi-expert router must never let one network read another's cached
    output (see ``denoise_loop._CachingGuidance``). Caches are made on first use
    and capped so a misbehaving caller cannot mint an unbounded number of them;
    the cap allows every guidance branch times a couple of routed networks. All
    branches share the same options.

    At capacity the least-recently-used cache is EVICTED and the new identity
    gets an empty one. Handing back some other identity's warmed cache instead
    would defeat the keying outright — the new network would replay a velocity
    it never produced — so the cap costs a cold start, never a wrong output.
    """

    def __init__(self, options: dict | None = None, max_branches: int = 8) -> None:
        self.options = normalize_options(options)
        self.max_branches = max_branches
        self._caches: OrderedDict[object, FirstBlockCache] = OrderedDict()
        # Counters of evicted caches, so a run report still accounts for every
        # step this set ever gated.
        self._evicted = {"computed": 0, "skipped": 0}

    @property
    def enabled(self) -> bool:
        return self.options["rel_threshold"] > 0.0

    def for_branch(self, key: object) -> FirstBlockCache:
        cache = self._caches.get(key)
        if cache is not None:
            self._caches.move_to_end(key)
            return cache
        if len(self._caches) >= self.max_branches:
            _, dropped = self._caches.popitem(last=False)
            self._evicted["computed"] += dropped.steps_computed
            self._evicted["skipped"] += dropped.steps_skipped
        cache = FirstBlockCache(**self.options)
        self._caches[key] = cache
        return cache

    def totals(self) -> dict[str, int]:
        computed = self._evicted["computed"] + sum(c.steps_computed for c in self._caches.values())
        skipped = self._evicted["skipped"] + sum(c.steps_skipped for c in self._caches.values())
        return {"computed": computed, "skipped": skipped}


def normalize_options(options: dict | None) -> dict:
    """Coerce a preset/user options dict to FirstBlockCache kwargs with defaults.

    Unknown keys are dropped so a preset can pass a wider dict without breaking.
    """
    opts = options or {}
    return {
        "rel_threshold": float(opts.get("rel_threshold", 0.0)),
        "warmup_steps": int(opts.get("warmup_steps", 4)),
        "max_consecutive_skips": int(opts.get("max_consecutive_skips", 3)),
    }
