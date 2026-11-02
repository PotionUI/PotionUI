import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

_NVML_DOWN = """
import json, sys
import src.platform.runtime.gpu as gpu_module

def _nvml_missing():
    raise RuntimeError("NVML Shared Library Not Found")

gpu_module.nvmlInit = _nvml_missing
"""

_MONITOR_WITHOUT_TORCH = _NVML_DOWN + """
monitor = gpu_module.GpuMonitor()
readings = [
    monitor.available,
    monitor.get_free_vram(),
    monitor.get_total_vram(),
    monitor.get_available_vram(),
    monitor.get_vram_budget(),
    monitor.device_identity,
]
print(json.dumps({"torch_loaded": "torch" in sys.modules, "readings": [r if not hasattr(r, "uuid") else "id" for r in readings]}))
"""

_MONITOR_ADOPTS_TORCH_LATER = _NVML_DOWN + """
monitor = gpu_module.GpuMonitor()
before = [monitor.available, monitor.get_free_vram()]
import torch
GB = 1024 ** 3
torch.cuda.is_available = lambda: True
torch.cuda.is_initialized = lambda: True
torch.cuda.device_count = lambda: 1
torch.cuda.mem_get_info = lambda index: (6 * GB, 8 * GB)
torch.cuda.memory_reserved = lambda *a: 0
torch.cuda.memory_allocated = lambda *a: 0
after = [monitor.available, monitor.source, monitor.get_free_vram(), monitor.get_total_vram()]
print(json.dumps({"before": before, "after": after}))
"""

_CONTAINER_WITHOUT_TORCH = _NVML_DOWN + """
from src.bootstrap.app import run_migrations_sync
from src.bootstrap.container import build_container

run_migrations_sync()
build_container()
print(json.dumps({"loaded": [m for m in ("torch", "diffusers", "transformers") if m in sys.modules]}))
"""


def _run(script, tmp_path):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        "POTIONUI_DB_PATH": str(tmp_path / "scratch.sqlite"),
        "CUDA_VISIBLE_DEVICES": "",
    }
    for name in ("SYSTEMROOT", "HOME", "USERPROFILE", "TEMP", "TMP"):
        if name in os.environ:
            env[name] = os.environ[name]
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_monitor_without_nvml_polls_without_importing_torch(tmp_path):
    report = _run(_MONITOR_WITHOUT_TORCH, tmp_path)
    assert report["torch_loaded"] is False
    assert report["readings"] == [False, None, None, None, None, None]


def test_monitor_without_nvml_falls_back_to_torch_once_torch_is_loaded(tmp_path):
    report = _run(_MONITOR_ADOPTS_TORCH_LATER, tmp_path)
    assert report["before"] == [False, None]
    assert report["after"] == [True, "torch", 6 * 1024, 8 * 1024]


def test_building_the_container_without_nvml_imports_no_inference_stack(tmp_path):
    report = _run(_CONTAINER_WITHOUT_TORCH, tmp_path)
    assert report["loaded"] == []
