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

import torch

logger = logging.getLogger(__name__)

Tensor = torch.Tensor


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

        self.prev_probe: Tensor | None = None       # block-0 output of last computed step
        self.cached_output: Tensor | None = None     # full model output of last computed step
        self.skips_in_a_row = 0
        self.steps_seen = 0                           # computed + skipped, this branch

        # Reporting counters (see :meth:`stats`).
        self.steps_computed = 0
        self.steps_skipped = 0

    @property
    def enabled(self) -> bool:
        return self.rel_threshold > 0.0

    def should_skip(self, probe: Tensor) -> bool:
        """Return whether this step may reuse the cached output.

        Pure read — never mutates state (the arch calls :meth:`record_skip` or
        :meth:`record_compute` to commit the decision). ``False`` during warmup,
        when there is no cached probe yet, when the probe shape changed
        (resolution change invalidates the cache), or when the consecutive-skip
        ceiling is hit. Otherwise skip iff the block-0 relative change is below
        ``rel_threshold`` for EVERY sample in the batch (see
        :meth:`relative_change` — a batch-pooled mean would let one stable
        high-magnitude sample mask an arbitrarily changed low-magnitude one).
        The relative change is computed in fp32 regardless of the model's
        compute dtype so the gate is stable under bf16/fp16.
        """
        if not self.enabled:
            return False
        if self.prev_probe is None or self.cached_output is None:
            return False
        if probe.shape != self.prev_probe.shape:
            return False
        if self.steps_seen < self.warmup_steps:
            return False
        if self.skips_in_a_row >= self.max_consecutive_skips:
            return False
        rel = self.relative_change(probe)
        return bool((rel < self.rel_threshold).all())

    def relative_change(self, probe: Tensor) -> Tensor:
        """Per-sample ``mean|probe - prev_probe| / (mean|prev_probe| + eps)``,
        in fp32, reduced over every dim except the batch dim (dim 0) — shape
        ``(B,)``. Per-sample, not a single batch-pooled scalar: a quantity>1
        generation runs several independent trajectories through one forward,
        and pooling their block-0 drift into one mean lets a stable
        high-magnitude sample hide an arbitrarily large relative change in a
        low-magnitude sample, corrupting BOTH when the pooled mean stays under
        threshold (see the failure scenario in
        ``test_should_skip_requires_every_sample_below_threshold``).
        """
        prev = self.prev_probe
        assert prev is not None
        cur = probe.detach().float()
        ref = prev.float()
        reduce_dims = tuple(range(1, cur.ndim))
        num = (cur - ref).abs().mean(dim=reduce_dims)
        den = ref.abs().mean(dim=reduce_dims) + 1e-8
        return num / den

    def record_skip(self) -> Tensor:
        """Commit a skip and return the cached output to reuse."""
        assert self.cached_output is not None
        self.skips_in_a_row += 1
        self.steps_skipped += 1
        self.steps_seen += 1
        return self.cached_output

    def record_compute(self, probe: Tensor, output) -> None:
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
    keyed by an opaque branch label (``"cond"`` / ``"uncond"``). Caches are made
    on first use and capped so a misbehaving caller cannot mint an unbounded
    number of them. All branches share the same options.
    """

    def __init__(self, options: dict | None = None, max_branches: int = 3) -> None:
        self.options = normalize_options(options)
        self.max_branches = max_branches
        self._caches: dict[object, FirstBlockCache] = {}

    @property
    def enabled(self) -> bool:
        return self.options["rel_threshold"] > 0.0

    def for_branch(self, key: object) -> FirstBlockCache:
        cache = self._caches.get(key)
        if cache is None:
            if len(self._caches) >= self.max_branches:
                # Reuse an existing cache rather than grow unbounded; branch keys
                # are a tiny fixed set (cond/uncond) so this is a safety net only.
                return next(iter(self._caches.values()))
            cache = FirstBlockCache(**self.options)
            self._caches[key] = cache
        return cache

    def totals(self) -> dict[str, int]:
        computed = sum(c.steps_computed for c in self._caches.values())
        skipped = sum(c.steps_skipped for c in self._caches.values())
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
