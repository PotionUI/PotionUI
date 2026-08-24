"""Tests for restore_dit_best_effort: the fits-check, streamed-model skip
(active-only, not "ever streamed"), the profiler-mark observability, and the
never-raises contract.

Regression context: production saw the restore
silently never fire. The first cut's streamed-check tested
``dit._streamer is not None`` alone, which is a PERMANENT false positive --
``_streamer`` is built once on the first ``stream_to()`` call and never reset
to ``None`` again, so any DiT that was EVER partially-resident (even a one-off
co-tenant-OOM degrade) would skip the restore forever after, even once fully
resident again. The fix checks ``_streamer.active`` instead -- see
`test_restores_when_streamer_exists_but_now_inactive`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.pipelines.pipes._shared.generation.dit_restore import restore_dit_best_effort

_MOD = "src.pipelines.pipes._shared.generation.dit_restore"


def _streamer(active: bool):
    return SimpleNamespace(active=active)


def _dit(device="cpu", estimated_vram_gb=10.0, streamer=None, module="module"):
    return SimpleNamespace(
        device=device, module=module, estimated_vram_gb=estimated_vram_gb,
        _streamer=streamer, move_to=lambda d: setattr(_dit_holder, "moved_to", d),
    )


# a tiny holder so the lambda above can record without nonlocal ceremony
class _dit_holder:
    moved_to = None


def _fake_profiler():
    """A get_profiler() stand-in that records every mark() call's (event, fields)."""
    calls = []
    profiler = SimpleNamespace(mark=lambda event, **fields: calls.append((event, fields)))
    return profiler, calls


def test_restores_when_it_fits():
    _dit_holder.moved_to = None
    dit = _dit(estimated_vram_gb=10.0)
    with patch(f"{_MOD}.effective_free_vram_gb", return_value=20.0), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0):
        restore_dit_best_effort(dit, "cuda")
    assert _dit_holder.moved_to == "cuda"


def test_restore_emits_profiler_mark():
    _dit_holder.moved_to = None
    dit = _dit(estimated_vram_gb=10.0)
    profiler, calls = _fake_profiler()
    with patch(f"{_MOD}.get_profiler", return_value=profiler), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=20.0), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0):
        restore_dit_best_effort(dit, "cuda")
    assert calls == [("dit_restore.restored", {"size_gb": 10.0, "free_gb_before": 20.0})]


def test_skips_when_it_does_not_fit():
    _dit_holder.moved_to = None
    dit = _dit(estimated_vram_gb=10.0)
    profiler, calls = _fake_profiler()
    with patch(f"{_MOD}.get_profiler", return_value=profiler), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=5.0), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0):
        restore_dit_best_effort(dit, "cuda")
    assert _dit_holder.moved_to is None
    assert calls[0][0] == "dit_restore.skip"
    assert calls[0][1]["reason"] == "insufficient_free_vram"


def test_skips_when_reserve_pushes_it_over():
    _dit_holder.moved_to = None
    dit = _dit(estimated_vram_gb=10.0)
    # 10.0 free == exactly the DiT size, but the 1GB reserve doesn't fit.
    with patch(f"{_MOD}.effective_free_vram_gb", return_value=10.0), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0):
        restore_dit_best_effort(dit, "cuda")
    assert _dit_holder.moved_to is None


def test_skips_when_streamer_is_currently_active():
    _dit_holder.moved_to = None
    dit = _dit(estimated_vram_gb=10.0, streamer=_streamer(active=True))
    profiler, calls = _fake_profiler()
    with patch(f"{_MOD}.get_profiler", return_value=profiler), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=1000.0), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0):
        restore_dit_best_effort(dit, "cuda")
    assert _dit_holder.moved_to is None
    assert calls == [("dit_restore.skip", {"reason": "partial_residency_active"})]


def test_restores_when_streamer_exists_but_now_inactive():
    # The bug this fix closes: a DiT streamed ONCE in the past (e.g. a one-off
    # co-tenant-OOM degrade) keeps its `_streamer` object forever, but once
    # `teardown()`/`offload()` has run, `.active` is False -- this DiT is
    # fully resident-placeable again and MUST restore, not skip forever.
    _dit_holder.moved_to = None
    dit = _dit(estimated_vram_gb=10.0, streamer=_streamer(active=False))
    with patch(f"{_MOD}.effective_free_vram_gb", return_value=1000.0), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0):
        restore_dit_best_effort(dit, "cuda")
    assert _dit_holder.moved_to == "cuda"


def test_skips_already_resident_dit():
    _dit_holder.moved_to = None
    dit = _dit(device="cuda:0", estimated_vram_gb=10.0)
    with patch(f"{_MOD}.effective_free_vram_gb") as mock_free:
        restore_dit_best_effort(dit, "cuda")
    mock_free.assert_not_called()
    assert _dit_holder.moved_to is None


def test_none_dit_is_a_noop():
    restore_dit_best_effort(None, "cuda")  # must not raise


def test_dit_with_no_module_is_a_noop():
    dit = _dit(module=None)
    restore_dit_best_effort(dit, "cuda")  # must not raise


def test_cannot_query_free_vram_is_a_noop():
    _dit_holder.moved_to = None
    dit = _dit(estimated_vram_gb=10.0)
    with patch(f"{_MOD}.effective_free_vram_gb", return_value=None):
        restore_dit_best_effort(dit, "cuda")
    assert _dit_holder.moved_to is None


def test_zero_estimated_size_is_a_noop():
    _dit_holder.moved_to = None
    dit = _dit(estimated_vram_gb=0.0)
    profiler, calls = _fake_profiler()
    with patch(f"{_MOD}.get_profiler", return_value=profiler), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=1000.0), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0):
        restore_dit_best_effort(dit, "cuda")
    assert _dit_holder.moved_to is None
    assert calls == [("dit_restore.skip", {"reason": "no_size_estimate", "free_gb": 1000.0})]


def test_exception_in_move_to_never_propagates():
    def boom(_d):
        raise RuntimeError("simulated OOM")

    dit = SimpleNamespace(device="cpu", module="m", estimated_vram_gb=10.0, _streamer=None, move_to=boom)
    profiler, calls = _fake_profiler()
    with patch(f"{_MOD}.get_profiler", return_value=profiler), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=1000.0), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0):
        restore_dit_best_effort(dit, "cuda")  # must not raise
    assert calls[0][0] == "dit_restore.error"
    assert "simulated OOM" in calls[0][1]["error"]
