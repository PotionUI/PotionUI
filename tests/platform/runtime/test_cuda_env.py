import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.platform.runtime import cuda_env
from src.platform.runtime.cuda_env import CUDA_CACHE_MAXSIZE_BYTES, apply_cuda_env_defaults, is_wsl

REPO_ROOT = Path(__file__).resolve().parents[3]
LINUX = "6.8.0-85-generic"
WSL2 = "6.18.54.1-microsoft-standard-WSL2"


def test_defaults_applied_when_unset(tmp_path):
    env = {"POTIONUI_STORAGE_PATH": str(tmp_path)}
    applied = apply_cuda_env_defaults(env, "linux")
    assert env["CUDA_CACHE_MAXSIZE"] == str(CUDA_CACHE_MAXSIZE_BYTES)
    assert env["CUDA_CACHE_PATH"] == str(tmp_path.resolve() / "cuda_cache")
    assert (tmp_path / "cuda_cache").is_dir()
    assert env["PYTORCH_CUDA_ALLOC_CONF"] == "expandable_segments:True"
    assert set(applied) == {"CUDA_CACHE_MAXSIZE", "CUDA_CACHE_PATH", "PYTORCH_CUDA_ALLOC_CONF"}


def test_user_values_preserved(tmp_path):
    env = {
        "POTIONUI_STORAGE_PATH": str(tmp_path),
        "CUDA_CACHE_MAXSIZE": "1",
        "CUDA_CACHE_PATH": "/x",
        "PYTORCH_CUDA_ALLOC_CONF": "max_split_size_mb:128",
    }
    applied = apply_cuda_env_defaults(env, "linux")
    assert applied == {}
    assert env["CUDA_CACHE_MAXSIZE"] == "1"
    assert env["CUDA_CACHE_PATH"] == "/x"
    assert env["PYTORCH_CUDA_ALLOC_CONF"] == "max_split_size_mb:128"
    assert not (tmp_path / "cuda_cache").exists()


def test_empty_user_value_is_kept(tmp_path):
    env = {"POTIONUI_STORAGE_PATH": str(tmp_path), "PYTORCH_CUDA_ALLOC_CONF": ""}
    apply_cuda_env_defaults(env, "linux")
    assert env["PYTORCH_CUDA_ALLOC_CONF"] == ""


def test_windows_skips_expandable_segments(tmp_path):
    env = {"POTIONUI_STORAGE_PATH": str(tmp_path)}
    apply_cuda_env_defaults(env, "win32")
    assert "PYTORCH_CUDA_ALLOC_CONF" not in env
    assert "CUDA_CACHE_MAXSIZE" in env and "CUDA_CACHE_PATH" in env


def test_wsl_keeps_expandable_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(cuda_env.os, "uname", lambda: SimpleNamespace(release=WSL2), raising=False)
    env = {"POTIONUI_STORAGE_PATH": str(tmp_path)}
    apply_cuda_env_defaults(env, "linux")
    assert env["PYTORCH_CUDA_ALLOC_CONF"] == "expandable_segments:True"


@pytest.mark.parametrize("var", ["PYTORCH_ALLOC_CONF", "PYTORCH_HIP_ALLOC_CONF"])
def test_another_allocator_conf_variable_is_not_shadowed(tmp_path, var):
    env = {"POTIONUI_STORAGE_PATH": str(tmp_path), var: "expandable_segments:False"}
    applied = apply_cuda_env_defaults(env, "linux")
    assert "PYTORCH_CUDA_ALLOC_CONF" not in env
    assert env[var] == "expandable_segments:False"
    assert "PYTORCH_CUDA_ALLOC_CONF" not in applied


def test_is_wsl_reads_the_running_kernel(monkeypatch):
    monkeypatch.setattr(cuda_env.os, "uname", lambda: SimpleNamespace(release=WSL2), raising=False)
    assert is_wsl()
    monkeypatch.setattr(cuda_env.os, "uname", lambda: SimpleNamespace(release=LINUX), raising=False)
    assert not is_wsl()


@pytest.mark.parametrize(
    ("release", "expected"),
    [(WSL2, True), ("5.15.167.4-microsoft-standard-WSL2", True), ("4.4.0-19041-Microsoft", True), (LINUX, False), ("6.8.0-1021-azure", False)],
)
def test_is_wsl_from_a_kernel_release(release, expected):
    assert is_wsl(release) is expected


def test_is_wsl_is_false_without_uname(monkeypatch):
    monkeypatch.delattr(cuda_env.os, "uname", raising=False)
    assert not is_wsl()


def test_storage_defaults_to_relative_storage_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = {}
    apply_cuda_env_defaults(env, "linux")
    assert env["CUDA_CACHE_PATH"] == str(tmp_path.resolve() / "storage" / "cuda_cache")


def test_unwritable_cache_dir_skips_cache_path(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    env = {"POTIONUI_STORAGE_PATH": str(blocker)}
    apply_cuda_env_defaults(env, "linux")
    assert "CUDA_CACHE_PATH" not in env


def test_idempotent(tmp_path):
    env = {"POTIONUI_STORAGE_PATH": str(tmp_path)}
    apply_cuda_env_defaults(env, "linux")
    assert apply_cuda_env_defaults(env, "linux") == {}


@pytest.mark.parametrize("entry", ["api.py", "worker.py"])
def test_entrypoint_applies_defaults_before_first_heavy_import(entry):
    lines = (REPO_ROOT / entry).read_text().splitlines()
    call = next(i for i, line in enumerate(lines) if line.startswith("apply_cuda_env_defaults()"))
    dotenv = next(i for i, line in enumerate(lines) if line.startswith("load_dotenv()"))
    first_src_import = next(
        i for i, line in enumerate(lines)
        if line.startswith(("from src.bootstrap", "from src.features", "from src.platform.observability"))
    )
    assert dotenv < call < first_src_import


def test_module_does_not_import_torch():
    code = (
        "import sys\n"
        "from src.platform.runtime import cuda_env\n"
        "print('torch' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
    )
    assert result.stdout.strip() == "False", result.stderr
