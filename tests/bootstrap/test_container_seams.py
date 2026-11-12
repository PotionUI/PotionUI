from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_PROBE = r"""
import json
from src.bootstrap.app import run_migrations_sync
from src.bootstrap.container import build_container

run_migrations_sync()
sentinel = lambda: "the stub executor"
c = build_container(**({"generation_engine_factory": sentinel} if __import__("os").environ.get("PROBE_FACTORY") else {}))
print(json.dumps({
    "factory_is_the_given_one": c.backend_registry.generation_engine_factory is sentinel,
    "factory_builds_a_generation_engine": type(c.backend_registry.generation_engine_factory()).__name__,
    "preset_roots": [p.as_posix() for p in c.preset_template_loader.preset_files_paths],
}))
"""


def _compose(tmp_path, **env):
    full_env = {
        **os.environ,
        "POTIONUI_DB_PATH": str(tmp_path / "db.sqlite"),
        "POTIONUI_STORAGE_PATH": str(tmp_path / "storage"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CUDA_VISIBLE_DEVICES": "",
        **env,
    }
    done = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def default_report(tmp_path_factory):
    return _compose(tmp_path_factory.mktemp("default"))


@pytest.fixture(scope="module")
def presets_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("presets")


@pytest.fixture(scope="module")
def seamed_report(tmp_path_factory, presets_dir):
    return _compose(tmp_path_factory.mktemp("seamed"), PROBE_FACTORY="1", POTIONUI_PRESETS_DIR=str(presets_dir))


def test_the_engine_factory_is_the_one_composition_normally_builds(default_report):
    assert default_report["factory_is_the_given_one"] is False
    assert default_report["factory_builds_a_generation_engine"] == "GenerationEngine"


def test_a_given_engine_factory_replaces_the_real_one(seamed_report):
    assert seamed_report["factory_is_the_given_one"] is True


def test_the_presets_roots_default_to_the_shipped_tree(default_report):
    assert default_report["preset_roots"] == ["content/presets/marketplace", "content/presets/local"]


def test_the_presets_root_can_be_pointed_somewhere_else(seamed_report, presets_dir):
    assert seamed_report["preset_roots"] == [
        (presets_dir / "marketplace").as_posix(),
        (presets_dir / "local").as_posix(),
    ]
