from __future__ import annotations

import json
import os
import subprocess
import sys
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


def test_the_engine_factory_is_the_one_composition_normally_builds(tmp_path):
    report = _compose(tmp_path)

    assert report["factory_is_the_given_one"] is False
    assert report["factory_builds_a_generation_engine"] == "GenerationEngine"


def test_a_given_engine_factory_replaces_the_real_one(tmp_path):
    report = _compose(tmp_path, PROBE_FACTORY="1")

    assert report["factory_is_the_given_one"] is True


def test_the_presets_roots_default_to_the_shipped_tree(tmp_path):
    assert _compose(tmp_path)["preset_roots"] == ["content/presets/marketplace", "content/presets/local"]


def test_the_presets_root_can_be_pointed_somewhere_else(tmp_path):
    report = _compose(tmp_path, POTIONUI_PRESETS_DIR=str(tmp_path / "presets"))

    assert report["preset_roots"] == [
        (tmp_path / "presets" / "marketplace").as_posix(),
        (tmp_path / "presets" / "local").as_posix(),
    ]
