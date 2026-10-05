from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_RUN = Path(__file__).resolve().parent / "run.py"
_HARNESS_DIR = _RUN.parents[1] / "harness"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

_spec = importlib.util.spec_from_file_location("e2e_ui_run_seeding", _RUN)
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


@pytest.fixture
def chunk_env(monkeypatch, tmp_path):
    events = []

    class FakeApp:
        def __init__(self, **kwargs):
            events.append(("app", kwargs.get("extra_env")))
            self.instance = SimpleNamespace(port=8099, db_path=tmp_path / "db")
            self.base_url = "http://127.0.0.1:8099"
            self.username = "owner"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(run, "ThrowawayApp", FakeApp)
    monkeypatch.setattr(run.cloud_fake, "prepare", lambda app: events.append(("prepare", None)))
    monkeypatch.setattr(run, "start_preview", lambda *a, **k: events.append(("preview", None)) or MagicMock())
    monkeypatch.setattr(run, "wait_for_preview", lambda *a, **k: None)
    monkeypatch.setattr(run, "stop_preview", lambda proc: 0)
    monkeypatch.setattr(run, "teardown_backend", lambda *a, **k: None)
    monkeypatch.setattr(run, "collect_backend_log", lambda *a, **k: None)
    monkeypatch.setattr(run, "collect_videos", lambda names: None)
    monkeypatch.setattr(run, "collect_failure_artifacts", lambda names: None)
    monkeypatch.setattr(run, "ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(run, "PreviewMonitor", lambda proc: SimpleNamespace(start=lambda: SimpleNamespace(stop=lambda: None, died_unexpectedly=False)))
    monkeypatch.setattr(run, "run_playwright_watched", lambda *a, **k: 0)
    return events


def args():
    return argparse.Namespace(models_dir=None, port=None, keep=False, headed=False, preview_port=None, build_dir=None)


def test_a_cloud_fake_spec_chunk_points_the_backend_at_the_plugin_and_seeds_it_before_the_preview(chunk_env):
    code = run.run_chunk(chunk_names=["cloud-fake-generate"], chunk_index=1, total_chunks=1, args=args())

    assert code == 0
    assert [name for name, _ in chunk_env] == ["app", "prepare", "preview"]
    assert chunk_env[0][1] == run.cloud_fake.plugin_env()


def test_any_other_chunk_boots_the_plain_backend_without_the_plugin(chunk_env):
    run.run_chunk(chunk_names=["admin-cloud-catalog", "models"], chunk_index=1, total_chunks=1, args=args())

    assert [name for name, _ in chunk_env] == ["app", "preview"]
    assert chunk_env[0][1] is None


def test_a_compare_spec_chunk_gets_the_seeded_fake_backend_too(chunk_env):
    code = run.run_chunk(chunk_names=["xy-compare-grid"], chunk_index=1, total_chunks=1, args=args())

    assert code == 0
    assert [name for name, _ in chunk_env] == ["app", "prepare", "preview"]
    assert chunk_env[0][1] == run.cloud_fake.plugin_env()
