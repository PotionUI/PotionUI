from __future__ import annotations

import logging

import pytest
import torch

from src.platform.runtime.native.arch.minimax_music3 import lm_graph
from src.platform.runtime.native.arch.minimax_music3.ar_loop import generate
from src.platform.runtime.native.arch.minimax_music3.lm_graph import StaticLmStep
from tests.platform.runtime.native.arch.test_minimax_music3_ar_loop import _build_lm


@pytest.fixture(autouse=True)
def _reset_warning():
    lm_graph._warned_capture_failed = False
    yield
    lm_graph._warned_capture_failed = False


def _prefilled(pruned: bool, seed: int, length: int = 4, extra: int = 6):
    lm = _build_lm(pruned=pruned, seed=seed)
    ids = torch.randint(0, 500, (2, length), generator=torch.Generator().manual_seed(seed + 1))
    cache = lm.new_kv_cache(max_len=length + extra, device=torch.device("cpu"), dtype=torch.float32)
    with torch.inference_mode():
        lm.prefill(ids, cache)
    return lm, cache


def _inputs(lm, count: int, seed: int):
    g = torch.Generator().manual_seed(seed)
    return [torch.randn(2, 1, lm.cfg.hidden_size, generator=g) for _ in range(count)]


class TestStaticStepMatchesSlicedStep:
    @pytest.mark.parametrize("pruned", [True, False])
    def test_every_position_matches_and_caches_agree(self, pruned):
        lm, cache_ref = _prefilled(pruned, seed=3)
        _, cache_static = _prefilled(pruned, seed=3)
        runner = StaticLmStep(lm, cache_static, capture=False)
        with torch.inference_mode():
            for x in _inputs(lm, 5, seed=9):
                reference = lm.step(x, cache_ref)
                actual = runner(x)
                torch.testing.assert_close(actual, reference, atol=1e-5, rtol=1e-4)
        assert cache_static.filled_len == cache_ref.filled_len
        for k_ref, k_static in zip(cache_ref.keys, cache_static.keys):
            torch.testing.assert_close(k_static, k_ref, atol=1e-6, rtol=0)
        for v_ref, v_static in zip(cache_ref.values, cache_static.values):
            torch.testing.assert_close(v_static, v_ref, atol=1e-6, rtol=0)

    def test_a_step_never_attends_past_its_own_position(self):
        lm, cache = _prefilled(True, seed=4)
        runner = StaticLmStep(lm, cache, capture=False)
        x = _inputs(lm, 1, seed=1)[0]
        with torch.inference_mode():
            first = runner(x)
            for keys, values in zip(cache.keys, cache.values):
                keys[:, :, cache.filled_len:, :] = 1e3
                values[:, :, cache.filled_len:, :] = 1e3
            cache.filled_len -= 1
            second = runner(x)
        torch.testing.assert_close(first, second, atol=1e-6, rtol=0)

    def test_a_full_cache_raises_instead_of_writing_out_of_range(self):
        lm, cache = _prefilled(True, seed=5, extra=1)
        runner = StaticLmStep(lm, cache, capture=False)
        x = _inputs(lm, 1, seed=2)[0]
        with torch.inference_mode():
            runner(x)
            with pytest.raises(RuntimeError):
                runner(x)


class TestGenerateParity:
    @pytest.mark.parametrize("pruned", [True, False])
    def test_graph_lm_step_flag_matches_the_sliced_loop_on_cpu(self, pruned):
        lm = _build_lm(pruned=pruned, seed=21)
        ids = torch.randint(0, 500, (2, 4), generator=torch.Generator().manual_seed(22))
        base_gen = torch.Generator().manual_seed(7)
        base = generate(lm, ids.clone(), base_gen, max_frames=5)
        flag_gen = torch.Generator().manual_seed(7)
        flagged = generate(lm, ids.clone(), flag_gen, max_frames=5, graph_lm_step=True)
        torch.testing.assert_close(flagged, base, atol=1e-5, rtol=1e-4)
        assert torch.equal(base_gen.get_state(), flag_gen.get_state())


class TestCaptureFallback:
    def test_capture_failure_warns_once_and_keeps_generating_eagerly(self, monkeypatch, caplog):
        lm, cache_ref = _prefilled(True, seed=6)
        _, cache = _prefilled(True, seed=6)
        runner = StaticLmStep(lm, cache, capture=False)
        runner._capture = True

        def boom(self_, inputs):
            raise RuntimeError("operation not permitted when stream is capturing")

        monkeypatch.setattr(StaticLmStep, "_capture_graph", boom)
        with caplog.at_level(logging.WARNING, logger=lm_graph.logger.name), torch.inference_mode():
            for x in _inputs(lm, 3, seed=11):
                torch.testing.assert_close(runner(x), lm.step(x, cache_ref), atol=1e-5, rtol=1e-4)
            other = StaticLmStep(lm, _prefilled(True, seed=6)[1], capture=False)
            other._capture = True
            other(_inputs(lm, 1, seed=12)[0])
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "running the LM step eagerly" in warnings[0].getMessage()
        assert not runner.graphed

    def test_capture_is_attempted_once_per_runner(self, monkeypatch):
        lm, cache = _prefilled(True, seed=8)
        runner = StaticLmStep(lm, cache, capture=False)
        runner._capture = True
        calls = []

        def boom(self_, inputs):
            calls.append(1)
            raise RuntimeError("x")

        monkeypatch.setattr(StaticLmStep, "_capture_graph", boom)
        with torch.inference_mode():
            for x in _inputs(lm, 3, seed=13):
                runner(x)
        assert len(calls) == 1

    def test_capture_is_off_on_cpu(self):
        lm, cache = _prefilled(True, seed=9)
        assert StaticLmStep(lm, cache, capture=True)._capture is False


class _FakeGraph:
    def __init__(self, runner):
        self.runner = runner
        self.replays = 0

    def replay(self):
        self.replays += 1
        self.runner._output.copy_(self.runner._run(self.runner._inputs))


class TestReplayPlumbing:
    def test_replay_reads_the_static_input_and_position_buffers_and_returns_a_copy(self, monkeypatch):
        lm, cache_ref = _prefilled(True, seed=10)
        _, cache = _prefilled(True, seed=10)
        runner = StaticLmStep(lm, cache, capture=False)
        runner._capture = True

        def fake_capture(self_, inputs):
            self_._inputs = inputs.detach().clone()
            self_._output = torch.zeros_like(inputs)
            self_._graph = _FakeGraph(self_)

        monkeypatch.setattr(StaticLmStep, "_capture_graph", fake_capture)
        outputs = []
        with torch.inference_mode():
            for x in _inputs(lm, 4, seed=14):
                reference = lm.step(x, cache_ref)
                out = runner(x)
                outputs.append(out)
                torch.testing.assert_close(out, reference, atol=1e-5, rtol=1e-4)
        assert runner.graphed
        assert runner._graph.replays == 4
        assert len({o.data_ptr() for o in outputs}) == 4
        assert cache.filled_len == cache_ref.filled_len

    def test_close_drops_the_graph(self):
        lm, cache = _prefilled(True, seed=11)
        runner = StaticLmStep(lm, cache, capture=False)
        runner._graph = object()
        runner.close()
        assert not runner.graphed
