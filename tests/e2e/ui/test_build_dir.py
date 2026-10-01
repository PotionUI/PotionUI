from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

_RUN = Path(__file__).resolve().parent / "run.py"
_HARNESS_DIR = _RUN.parents[1] / "harness"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

_spec = importlib.util.spec_from_file_location("e2e_ui_run_build_dir", _RUN)
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)

LIVE_BUILD = run.REPO_ROOT / "frontend" / "build"


def test_default_build_dir_is_the_harness_folder_not_the_live_build():
    assert run.HARNESS_BUILD_DIR == run.FRONTEND_DIR / ".e2e-build"
    assert run.HARNESS_BUILD_DIR != LIVE_BUILD


def test_build_env_points_the_frontend_build_at_the_given_folder(tmp_path):
    assert run.build_env(tmp_path)["E2E_BUILD_DIR"] == str(tmp_path)


def test_build_output_is_looked_up_inside_the_build_dir(tmp_path):
    assert not run.has_build_output(tmp_path)
    (tmp_path / ".svelte-kit" / "output" / "client").mkdir(parents=True)
    assert run.has_build_output(tmp_path)


def test_run_build_hands_the_harness_folder_to_npm(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, cwd=None, env=None):
        seen["env"] = env
        return type("P", (), {"returncode": 0})()

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    run.run_build(tmp_path)
    assert seen["env"]["E2E_BUILD_DIR"] == str(tmp_path)


def test_skip_build_checks_the_harness_folder_never_the_live_build(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "ARTIFACTS_DIR", tmp_path / "artifacts")
    args = argparse.Namespace(skip_build=True, build_only=False, build_dir=tmp_path / "empty")
    with pytest.raises(run.StageError):
        run._run_locked(args, [], [], [], 1, 1, 1)
