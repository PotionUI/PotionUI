"""Trajectory warm-start ("iterate mode") — resume a generation from a cached
mid-trajectory latent instead of pure noise (roadmap §4.1).

In a real session, successive generations are *edits*: same seed with the prompt
tweaked, same prompt with CFG nudged. Early denoise steps fix the global layout
(they depend mostly on the seed + coarse conditioning); late steps refine detail.
Nobody exploits this — every tool recomputes the full trajectory. This module
caches on-trajectory latent checkpoints at 25/50/75% of the schedule and, when
the next request's conditioning is close enough, resumes from the deepest still-
valid checkpoint.

Conservative v1 (opt-in "Iterate mode ⚡"):

* **Deterministic single-step samplers only** (``euler``). Multistep samplers
  (dpmpp_2m / unipc) carry history the checkpoint doesn't capture; stochastic
  ones (euler_sde / euler_restart) can't reproduce a trajectory. The engine
  gates on ``sampler == "euler"``.
* **txt2img only** — an img2img run already starts from an image latent.
* **Static key must match exactly** (model, seed, resolution, sampler, steps,
  schedule, guidance mode); any mismatch is a cold start. The key makes a resumed
  trajectory bit-for-bit identical to the equivalent cold run when the
  conditioning is unchanged (same euler math on the same on-trajectory state).
* **Checkpoint indexing (write it down, an off-by-one silently degrades
  quality):** ``checkpoints[k]`` is the state *entering* step ``k`` — i.e. the
  latent AFTER step ``k-1`` completed. Euler's per-step loop, resumed with
  ``x = checkpoints[k]`` and ``start_step = k`` over the SAME full sigma array,
  reproduces the cold run's steps ``k..N-1`` exactly. The capture hook stores
  ``x`` at the boundary ``on_step(i=k-1)`` fires (its ``x`` is already the
  post-step-``k-1`` state).

Determinism changes when a run is warm-started, so it is an explicit toggle and
the engine stamps ``warm_started_from`` info (steps skipped + similarity).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

import torch

from .hooks import BaseStepHook

logger = logging.getLogger(__name__)

Tensor = torch.Tensor

# Fractions of the schedule at which to checkpoint, and the similarity->depth
# ladder for resuming. Conservative thresholds: a CFG/detail tweak barely moves
# the pooled prompt embedding, a small word swap moves it a little.
CHECKPOINT_FRACTIONS = (0.25, 0.5, 0.75)
RESUME_LADDER = ((0.995, 0.75), (0.98, 0.5), (0.95, 0.25))  # (min cosine, depth fraction)

_MAX_CHECKPOINTS_PER_ENTRY = len(CHECKPOINT_FRACTIONS)


def checkpoint_steps(total_steps: int) -> list[int]:
    """Step indices to checkpoint: floor(frac * N), de-duped, strictly interior.

    Excludes 0 (that's pure noise — a cold start) and ``total_steps`` (the final
    latent — nothing left to resume). Clamped to at most one per fraction.
    """
    marks = {int(f * total_steps) for f in CHECKPOINT_FRACTIONS}
    return sorted(k for k in marks if 0 < k < total_steps)


def pooled_fingerprint(context: Tensor) -> Tensor:
    """Cheap conditioning fingerprint: mean AND std over every non-feature axis.

    ``context`` is a ``[B, S, D]`` (or ``[B, S, L, D]``) embedding. Pooling only
    the mean collapses token structure — ``[u+d, u-d]`` and ``[u+100d, u-100d]``
    both mean to ``u`` and would look identical (S12). Concatenating the per-
    feature standard deviation distinguishes them (``|d|`` vs ``100|d|``) while
    staying a small ``[2D]`` CPU float32 vector.
    """
    dims = tuple(range(context.ndim - 1))
    c = context.detach().float()
    mean = c.mean(dim=dims).flatten()
    # std over a single pooled element is undefined (NaN); use zeros there.
    pooled_count = c.numel() // c.shape[-1]
    std = c.std(dim=dims).flatten() if pooled_count > 1 else torch.zeros_like(mean)
    return torch.cat([mean, std]).cpu()


def conditioning_fingerprint(cond: dict, uncond: dict | None) -> Tensor:
    """Fingerprint the FULL conditioning: positive context + negative context.

    A trajectory depends on the negative prompt too, so a fingerprint that omits
    it (S12) lets a changed negative prompt resume from a stale checkpoint. The
    uncond half is concatenated (zeros-padded to the cond width when absent) so
    the vector shape is stable for a given guidance mode.
    """
    pos = pooled_fingerprint(cond["context"])
    if uncond is not None and uncond.get("context") is not None:
        neg = pooled_fingerprint(uncond["context"])
    else:
        neg = torch.zeros_like(pos)
    return torch.cat([pos, neg])


def cosine(a: Tensor, b: Tensor) -> float:
    """Cosine similarity of two 1-D CPU vectors; 0.0 on any shape mismatch."""
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    return float(torch.nn.functional.cosine_similarity(a, b, dim=0, eps=1e-8))


# Keys of ``sampling_settings`` that shape the sigma schedule; two runs agreeing
# on these (plus steps + image_seq_len) build an identical sigma array.
_SCHEDULE_KEYS = (
    "shift", "base_shift", "max_shift", "dynamic_shift", "fixed_mu",
    "schedule", "schedule_options", "detail_strength", "detail_start", "detail_end",
)


# Trajectory-affecting guidance knobs NOT already captured by the schedule hash
# or the static key's guidance mode (E2).
_WARM_START_SETTING_KEYS = (
    "cfg_zero_star", "zero_init_steps",
    "apg_eta", "apg_norm_threshold", "apg_momentum",
    "slg_scale", "slg_layers", "slg_sigma_start", "slg_sigma_end",
)


def _stable_repr(v) -> str:
    """repr that is order-stable for sets (``slg_layers``)."""
    if isinstance(v, (set, frozenset)):
        return repr(sorted(v))
    return repr(v)


def warm_start_settings_signature(settings: dict, guidance_options, sampler_options,
                                  step_cache_options) -> str:
    """Hash every trajectory-affecting knob outside the schedule + guidance mode.

    Two warm-start runs may only cross-resume when all of these agree (E2):
    APG/CFG-Zero*/SLG params, sampler options, and FBCache step-cache options all
    change the trajectory. ``cfg_scale`` is intentionally NOT included.
    """
    def canon(d) -> tuple:
        if not d:
            return ()
        return tuple(sorted((str(k), _stable_repr(v)) for k, v in d.items()))

    payload = (
        tuple((k, _stable_repr((settings or {}).get(k))) for k in _WARM_START_SETTING_KEYS),
        canon(guidance_options), canon(sampler_options), canon(step_cache_options),
    )
    return hashlib.sha1(repr(payload).encode()).hexdigest()


def schedule_signature(steps: int, image_seq_len, sampling_settings: dict,
                       explicit_sigmas: Tensor | None = None) -> str:
    """Stable hash of everything that determines the sigma schedule.

    Guards against a schedule change the coarse static key might otherwise miss;
    computed from the build inputs so the engine need not build the sigma array a
    second time to key on it.

    ``explicit_sigmas``, when given (an explicit ``sigmas=`` list passed to
    ``NativeGenerator.sample``, bypassing schedule derivation entirely), replaces
    the derived-schedule payload with a hash of its own values under a distinct
    tag -- an explicit-list run's signature can never collide with a derived-
    schedule run's of the same nominal ``steps``, in either direction.
    """
    if explicit_sigmas is not None:
        payload = ("explicit", tuple(round(float(v), 6) for v in explicit_sigmas.tolist()))
    else:
        payload = (int(steps), image_seq_len) + tuple((k, sampling_settings.get(k)) for k in _SCHEDULE_KEYS)
    return hashlib.sha1(repr(payload).encode()).hexdigest()


@dataclass
class TrajectoryEntry:
    static_key: tuple
    total_steps: int
    schedule_sig: str
    cond_fingerprint: Tensor | None = None
    checkpoints: dict[int, Tensor] = field(default_factory=dict)

    def store(self, step_idx: int, latent: Tensor) -> None:
        """Store an on-trajectory checkpoint (CPU clone, detached)."""
        self.checkpoints[step_idx] = latent.detach().to("cpu", copy=True)


@dataclass
class ResumePlan:
    resume_step: int          # 0 == cold start
    latent: Tensor | None     # CPU checkpoint to resume from (None when cold)
    similarity: float

    @property
    def is_warm(self) -> bool:
        return self.resume_step > 0 and self.latent is not None


def decide_resume(entry: TrajectoryEntry | None, new_fp: Tensor, total_steps: int,
                  sched_sig: str) -> ResumePlan:
    """Pick the deepest still-valid checkpoint for a conditioning change.

    Cold (``resume_step == 0``) when: no entry, a schedule/step mismatch the
    static key didn't already exclude, the fingerprint similarity falls below the
    shallowest ladder rung, or no captured checkpoint is at-or-below the depth the
    similarity permits.
    """
    if entry is None or entry.cond_fingerprint is None:
        return ResumePlan(0, None, 0.0)
    if entry.total_steps != total_steps or entry.schedule_sig != sched_sig:
        return ResumePlan(0, None, 0.0)
    sim = cosine(entry.cond_fingerprint, new_fp)
    depth_frac = 0.0
    for min_sim, frac in RESUME_LADDER:
        if sim >= min_sim:
            depth_frac = frac
            break
    if depth_frac == 0.0:
        return ResumePlan(0, None, sim)
    target = int(depth_frac * total_steps)
    available = [k for k in entry.checkpoints if k <= target]
    if not available:
        return ResumePlan(0, None, sim)
    k = max(available)
    return ResumePlan(k, entry.checkpoints[k], sim)


class CheckpointCaptureHook(BaseStepHook):
    """StepHook that clones the latent at the checkpoint marks into an entry.

    Rides the existing per-step hook seam (``on_step`` already receives ``x``),
    so capture costs one CPU clone at 2-3 boundaries per run and nothing on every
    other step. ``on_step(i, ...)``'s ``x`` is the state entering step ``i+1``,
    which is exactly ``checkpoints[i+1]`` (see the module docstring's indexing).
    """

    priority = -100  # after progress/preview; capture is bookkeeping

    def __init__(self, entry: TrajectoryEntry, total_steps: int) -> None:
        self.entry = entry
        self._marks = set(checkpoint_steps(total_steps))

    def on_step(self, step_index, total_steps, x, sigma, denoised_x0) -> None:
        mark = step_index + 1
        if mark in self._marks:
            self.entry.store(mark, x)


class TrajectoryCache:
    """Process-global LRU of the last ``max_entries`` trajectories (CPU-resident).

    Image-scale latents are a few MB, so 2 entries × 3 checkpoints is tens of MB.
    Keyed by an opaque static key the engine builds; thread-safe for the
    single-GPU serialized generation path plus any incidental concurrency.
    """

    def __init__(self, max_entries: int = 2) -> None:
        self.max_entries = max_entries
        self._entries: "OrderedDict[tuple, TrajectoryEntry]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, static_key: tuple) -> TrajectoryEntry | None:
        with self._lock:
            entry = self._entries.get(static_key)
            if entry is not None:
                self._entries.move_to_end(static_key)
            return entry

    def get_or_create(self, static_key: tuple, total_steps: int, sched_sig: str) -> TrajectoryEntry:
        with self._lock:
            entry = self._entries.get(static_key)
            if entry is None or entry.total_steps != total_steps or entry.schedule_sig != sched_sig:
                entry = TrajectoryEntry(static_key, total_steps, sched_sig)
                self._entries[static_key] = entry
            self._entries.move_to_end(static_key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)  # evict LRU
            return entry

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


_CACHE: TrajectoryCache | None = None
_CACHE_LOCK = threading.Lock()


def get_trajectory_cache() -> TrajectoryCache:
    """Return the process-global :class:`TrajectoryCache` (lazy singleton)."""
    global _CACHE
    if _CACHE is None:
        with _CACHE_LOCK:
            if _CACHE is None:
                _CACHE = TrajectoryCache()
    return _CACHE
