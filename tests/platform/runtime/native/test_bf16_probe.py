"""Bite-checkable coverage for the ``bf16_cpu_heavy`` skip mechanism in
``conftest.py`` -- the probe's env override and the collection hook that
turns a failed probe into a deterministic skip."""

from __future__ import annotations

import pytest

from tests.platform.runtime.native import conftest as bf16_conftest


@pytest.fixture(autouse=True)
def _reset_probe_cache(monkeypatch):
    monkeypatch.delenv(bf16_conftest._FORCE_ENV, raising=False)
    monkeypatch.setattr(bf16_conftest, "_cached_result", None)
    yield
    monkeypatch.setattr(bf16_conftest, "_cached_result", None)


def test_force_pass_reports_usable(monkeypatch):
    monkeypatch.setenv(bf16_conftest._FORCE_ENV, "pass")
    assert bf16_conftest.cpu_bf16_is_usable() is True


def test_force_fail_reports_unusable(monkeypatch):
    monkeypatch.setenv(bf16_conftest._FORCE_ENV, "fail")
    assert bf16_conftest.cpu_bf16_is_usable() is False


def test_measurement_is_cached_after_first_call(monkeypatch):
    calls = {"n": 0}

    def _fake_probe():
        calls["n"] += 1
        return 1.0  # comfortably under the threshold -> usable

    monkeypatch.setattr(bf16_conftest, "_probe_once", _fake_probe)

    first = bf16_conftest.cpu_bf16_is_usable()
    second = bf16_conftest.cpu_bf16_is_usable()

    assert first is True
    assert second is True
    assert calls["n"] == bf16_conftest._WARMUP_ITERS + bf16_conftest._TIMED_ITERS


def test_slow_measurement_reports_unusable(monkeypatch):
    monkeypatch.setattr(bf16_conftest, "_probe_once", lambda: bf16_conftest._THRESHOLD_MS * 10)
    assert bf16_conftest.cpu_bf16_is_usable() is False


class _FakeItem:
    def __init__(self, keywords, fixturenames=()):
        self.keywords = keywords
        self.fixturenames = tuple(fixturenames)
        self.markers_added = []

    def add_marker(self, marker):
        self.markers_added.append(marker)


def test_collection_hook_skips_marked_item_when_cpu_lacks_bf16(monkeypatch):
    monkeypatch.setattr(bf16_conftest.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(bf16_conftest, "cpu_bf16_is_usable", lambda: False)

    item = _FakeItem(keywords={"bf16_cpu_heavy": True})
    bf16_conftest.pytest_collection_modifyitems(config=None, items=[item])

    assert len(item.markers_added) == 1
    assert item.markers_added[0].name == "skip"


def test_collection_hook_leaves_item_alone_when_probe_passes(monkeypatch):
    monkeypatch.setattr(bf16_conftest.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(bf16_conftest, "cpu_bf16_is_usable", lambda: True)

    item = _FakeItem(keywords={"bf16_cpu_heavy": True})
    bf16_conftest.pytest_collection_modifyitems(config=None, items=[item])

    assert item.markers_added == []


def test_collection_hook_leaves_item_alone_when_gpu_available(monkeypatch):
    monkeypatch.setattr(bf16_conftest.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(bf16_conftest, "cpu_bf16_is_usable", lambda: False)

    item = _FakeItem(keywords={"bf16_cpu_heavy": True})
    bf16_conftest.pytest_collection_modifyitems(config=None, items=[item])

    assert item.markers_added == []


def test_collection_hook_ignores_unmarked_items(monkeypatch):
    monkeypatch.setattr(bf16_conftest.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(bf16_conftest, "cpu_bf16_is_usable", lambda: False)

    item = _FakeItem(keywords={})
    bf16_conftest.pytest_collection_modifyitems(config=None, items=[item])

    assert item.markers_added == []


def test_collection_hook_skips_real_vae_fixture_user_when_cpu_lacks_bf16(monkeypatch):
    """A test that never carried the marker but draws real VAE weights runs
    the same bf16 conv2d stack - it must skip by construction, not by memory."""
    monkeypatch.setattr(bf16_conftest.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(bf16_conftest, "cpu_bf16_is_usable", lambda: False)

    item = _FakeItem(keywords={}, fixturenames=("dit_path", "vae_path"))
    bf16_conftest.pytest_collection_modifyitems(config=None, items=[item])

    assert [m.name for m in item.markers_added] == ["skip"]


def test_collection_hook_leaves_real_vae_fixture_user_alone_when_probe_passes(monkeypatch):
    monkeypatch.setattr(bf16_conftest.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(bf16_conftest, "cpu_bf16_is_usable", lambda: True)

    item = _FakeItem(keywords={}, fixturenames=("vae_path",))
    bf16_conftest.pytest_collection_modifyitems(config=None, items=[item])

    assert item.markers_added == []


def test_dit_only_fixture_user_is_not_heavy():
    assert bf16_conftest.is_bf16_cpu_heavy(_FakeItem(keywords={}, fixturenames=("dit_path",))) is False
