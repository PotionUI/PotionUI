"""FBCache isolation across a multi-expert router (Wan 2.2's high/low DiTs).

The cached value is one network's output. A router that switches transformer
mid-trajectory must therefore never let the incoming expert read the outgoing
one's cache — block-0 probes either side of the boundary can easily sit within
``rel_threshold``, which would replay the wrong network's velocity. These stubs
drive the real ``FirstBlockCache`` protocol the arch forwards implement, with
two "experts" whose probes are deliberately IDENTICAL and whose velocities are
not, so a leak is visible in the returned tensor.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.denoise_loop import _CachingGuidance, denoise
from src.platform.runtime.native.sampling.step_cache import StepCacheSet

PROBE = torch.ones(1, 4, 4, 4)


class _Expert:
    """One fake transformer: constant velocity, constant block-0 probe."""

    def __init__(self, velocity: float) -> None:
        self.velocity = velocity
        self.computed = 0
        self.skipped = 0

    def __call__(self, x, conditioning):
        cache = conditioning.get("step_cache")
        if cache is not None and cache.should_skip(PROBE):
            self.skipped += 1
            return cache.record_skip()
        self.computed += 1
        out = torch.full_like(x, self.velocity)
        if cache is not None:
            cache.record_compute(PROBE, out)
        return out


class _FakeRouter:
    """The shape ``_ExpertRouter`` presents to denoise(): sigma picks the expert,
    and ``cache_identity`` names the one a given sigma will run."""

    def __init__(self, high: _Expert, low: _Expert, boundary: float) -> None:
        self.high = high
        self.low = low
        self.boundary = boundary
        self.velocities: list[float] = []

    def _select(self, sigma_val: float) -> _Expert:
        return self.high if sigma_val > self.boundary else self.low

    def cache_identity(self, sigma_val: float) -> tuple:
        return (id(self._select(sigma_val)), 1)

    def __call__(self, x, sigma, conditioning):
        out = self._select(float(sigma.reshape(-1)[0]))(x, conditioning)
        self.velocities.append(float(out.reshape(-1)[0]))
        return out


class _NoCFG:
    """The single-branch guidance shape ``_CachingGuidance`` wraps."""

    def __call__(self, model_fn, x, sigma, cond, uncond, step_index):
        return model_fn(x, sigma, cond)


class _SingleNetwork:
    """A model_forward with no ``cache_identity`` — every non-routed family."""

    def __init__(self) -> None:
        self.expert = _Expert(3.0)

    def __call__(self, x, sigma, conditioning):
        return self.expert(x, conditioning)


def _run(model_forward, sigmas, **overrides):
    kwargs = dict(
        latents=torch.zeros(1, 4, 4, 4),
        cond={"context": "c"},
        uncond=None,
        steps=len(sigmas) - 1,
        sampling_settings={"guidance": None},
        guidance_scale=0.0,
        seed_noise=torch.zeros(1, 4, 4, 4),
        sigmas=sigmas,
        step_cache_options={"rel_threshold": 0.5, "warmup_steps": 2,
                            "max_consecutive_skips": 999},
    )
    kwargs.update(overrides)
    return denoise(model_forward, **kwargs)


def test_low_expert_never_replays_the_high_expert_velocity():
    # Steps 0-3 run high (sigma > 0.5), steps 4-7 run low. Probes are identical
    # everywhere, so a shared cache would hand the low expert high's velocity
    # from its very first step.
    sigmas = torch.tensor([1.0, 0.9, 0.8, 0.7, 0.5, 0.4, 0.2, 0.1, 0.0])
    high, low = _Expert(1.0), _Expert(-1.0)
    router = _FakeRouter(high, low, boundary=0.5)

    _run(router, sigmas)

    assert router.velocities[:4] == [1.0, 1.0, 1.0, 1.0]
    assert router.velocities[4:] == [-1.0, -1.0, -1.0, -1.0]
    # Each expert warms up on its own cache: two real computes before it may skip.
    assert high.computed == 2 and high.skipped == 2
    # Low's last step is the forced final compute, so 2 warmup + 1 final.
    assert low.computed == 3 and low.skipped == 1


def test_each_expert_keeps_its_own_cache_across_back_and_forth_sigmas():
    # Samplers may evaluate at intermediate sigmas, which can re-enter the
    # outgoing expert. Each expert must get its own cache back, not a cleared
    # one and never the peer's.
    caches = StepCacheSet({"rel_threshold": 0.5})
    router = _FakeRouter(_Expert(1.0), _Expert(-1.0), boundary=0.5)
    guidance = _CachingGuidance(_NoCFG(), caches, total_steps=10, model_forward=router)

    seen = []

    def model_fn(xx, ss, conditioning):
        seen.append((float(ss), conditioning["step_cache"]))
        return torch.zeros(1, 1)

    cond = {"context": "c"}
    for sigma in (0.9, 0.4, 0.8, 0.3, 0.9):
        guidance(model_fn, torch.zeros(1, 1), torch.tensor(sigma), cond, None, step_index=0)

    high_caches = {id(c) for s, c in seen if s > 0.5}
    low_caches = {id(c) for s, c in seen if s <= 0.5}
    assert len(high_caches) == 1
    assert len(low_caches) == 1
    assert high_caches.isdisjoint(low_caches)


def test_single_network_forward_still_keys_by_branch_alone():
    sigmas = torch.tensor([1.0, 0.8, 0.6, 0.4, 0.2, 0.0])
    model = _SingleNetwork()

    _run(model, sigmas)

    # 2 warmup computes + the forced final compute; the rest skip. Unchanged by
    # expert keying — a forward without cache_identity contributes ``None``.
    assert model.expert.computed == 3
    assert model.expert.skipped == 2
