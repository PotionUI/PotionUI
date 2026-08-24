from unittest.mock import Mock

import src.platform.runtime.model_lifecycle.manager as manager_module
from src.platform.runtime.model_lifecycle.manager import ModelLifecycleManager


def test_acquire_emits_hit_and_miss_marks(monkeypatch):
    """Smoke test: ModelLifecycleManager.acquire() reports through
    get_profiler().mark(...) on both the miss (first load) and hit
    (cached reuse) paths, without needing profiling actually enabled --
    the manager always calls mark(); it's mark() itself that no-ops when
    profiling is off. Here we replace get_profiler() outright so we don't
    depend on that gate at all."""
    fake_profiler = Mock()
    monkeypatch.setattr(manager_module, "get_profiler", lambda: fake_profiler)

    mlm = ModelLifecycleManager()
    mlm.acquire("checkpoint_loader/sdxl", "fingerprint-a", loader=lambda: object())
    mlm.acquire("checkpoint_loader/sdxl", "fingerprint-a", loader=lambda: object())

    events = [call.args[0] for call in fake_profiler.mark.call_args_list]
    assert "models.acquire.miss" in events
    assert "models.acquire.hit" in events


def test_evict_emits_evict_mark(monkeypatch):
    fake_profiler = Mock()
    monkeypatch.setattr(manager_module, "get_profiler", lambda: fake_profiler)

    mlm = ModelLifecycleManager()
    mlm.acquire("key", "fp", loader=lambda: object())
    mlm.invalidate("key")

    events = [call.args[0] for call in fake_profiler.mark.call_args_list]
    assert "models.evict" in events
