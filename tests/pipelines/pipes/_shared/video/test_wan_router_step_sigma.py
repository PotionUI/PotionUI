from __future__ import annotations

import pytest
import torch

from src.pipelines.pipes._shared.generation.wan_ti2v import TI2VForward
from src.pipelines.pipes._shared.video import wan_experts
from src.pipelines.pipes._shared.video.wan_experts import ExpertRouter

_BOUNDARY = 0.875
_CONTEXT = {"context": torch.zeros(1, 2, 4)}


class _Expert:
    def __init__(self, name, calls):
        self.name = name
        self.device = "cpu"
        self._calls = calls
        self.module = self

    def move_to(self, device):
        self.device = str(device)

    def offload(self):
        self.device = "cpu"

    def __call__(self, x, t, context, **extra):
        self._calls.append((self.name, t.clone()))
        return torch.ones_like(x)


@pytest.fixture
def router(monkeypatch):
    monkeypatch.setattr(wan_experts, "maybe_compile_dit", lambda *a, **k: None)
    calls = []
    return ExpertRouter(_Expert("high", calls), _Expert("low", calls), _BOUNDARY, "cpu"), calls


@pytest.mark.parametrize("sigma, expert", [(0.99, "high"), (0.9, "high"), (0.875, "low"), (0.5, "low"), (0.01, "low")])
def test_an_unpinned_step_picks_the_same_expert_from_a_scalar_or_a_per_sample_sigma(router, sigma, expert):
    route, calls = router
    x = torch.zeros(2, 4, 3, 2, 2)
    route(x, torch.tensor(sigma), _CONTEXT)
    route(x, torch.full((2,), sigma), _CONTEXT)
    assert [name for name, _ in calls] == [expert, expert]


@pytest.mark.parametrize("sigma, expert", [(0.99, "high"), (0.9, "high"), (0.5, "low")])
def test_a_pinned_first_frame_at_zero_does_not_decide_the_expert(router, sigma, expert):
    route, calls = router
    forward = TI2VForward(route, torch.zeros(1, 4, 1, 2, 2))
    forward(torch.zeros(1, 4, 3, 2, 2), torch.tensor([sigma]), _CONTEXT)
    name, timestep = calls[0]
    assert name == expert
    assert timestep[0, 0].item() == 0.0
    assert timestep[0, 1].item() == pytest.approx(sigma * wan_experts.TIMESTEP_SCALE)


def test_a_full_sampling_run_switches_experts_at_the_boundary_with_the_first_frame_pinned(router):
    route, calls = router
    forward = TI2VForward(route, torch.zeros(1, 4, 1, 2, 2))
    for sigma in (1.0, 0.95, 0.9, 0.85, 0.5, 0.1):
        forward(torch.zeros(1, 4, 3, 2, 2), torch.tensor([sigma]), _CONTEXT)
    assert [name for name, _ in calls] == ["high", "high", "high", "low", "low", "low"]
