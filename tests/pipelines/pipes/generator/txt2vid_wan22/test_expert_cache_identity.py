"""``_ExpertRouter.cache_identity`` — the step-cache key denoise() reads so the
high and low experts never share a FirstBlockCache. Pure CPU: no module is ever
called, only selected."""

import torch

from src.pipelines.pipes.generator.chain_video_wan22.main import _ChainForward
from src.pipelines.pipes.generator.img2vid_wan22.main import _I2VForward
from src.pipelines.pipes.generator.txt2vid_wan22.main import _ExpertRouter


class _Dit:
    def __init__(self, effective_revision: int) -> None:
        self.module = object()
        self.effective_revision = effective_revision


def _router(low=True, boundary=0.875):
    return _ExpertRouter(_Dit(1), _Dit(2) if low else None, boundary, "cpu")


def test_identity_follows_the_selected_expert():
    r = _router()
    assert r.cache_identity(0.95) != r.cache_identity(0.5)
    assert r.cache_identity(0.95) == r.cache_identity(0.9)
    assert r.cache_identity(0.5) == r.cache_identity(0.1)


def test_boundary_equality_agrees_with_the_router_selection():
    # `_select` routes sigma == boundary to the LOW expert (the dispatch is
    # `sigma_val > boundary` -> high). The cache key must not disagree.
    r = _router(boundary=0.875)
    assert r._select(0.875) is r.low
    assert r.cache_identity(0.875) == r.cache_identity(0.4)
    assert r.cache_identity(0.875) != r.cache_identity(0.876)


def test_single_expert_model_has_one_identity_everywhere():
    r = _router(low=False)
    assert r.cache_identity(1.0) == r.cache_identity(0.0)


def test_identity_moves_when_the_expert_weights_are_revised():
    r = _router()
    before = r.cache_identity(0.95)
    r.high.effective_revision = 99
    assert r.cache_identity(0.95) != before


def test_i2v_and_chain_wrappers_forward_the_identity():
    r = _router()
    concat = torch.zeros(1, 20, 1, 2, 2)
    assert _I2VForward(r, concat).cache_identity(0.95) == r.cache_identity(0.95)
    assert _ChainForward(r, concat).cache_identity(0.4) == r.cache_identity(0.4)
