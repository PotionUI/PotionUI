from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
from safetensors.torch import save_file

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.model_loader.minimax_h3.main import ModelLoaderMinimaxH3Pipe
from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.control import (
    CONTROL_V1_PLACES,
    MiniMaxH3ControlBranch,
    control_attached,
)
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.errors import NativeEngineUnsupportedError

from tests.platform.runtime.native._native_ops import fp32_ops
from tests.platform.runtime.native.arch.test_minimax_h3_model import TINY_COMMON, _build_ready

DEEP_PRUNED = dict(TINY_COMMON, num_layers=50, pruned=True, time_embed_dim=6, adaln_curve_grid=5)


def _touch(path: Path) -> str:
    save_file({"placeholder": torch.zeros(4)}, str(path))
    return str(path)


def _patch_file(path: Path, unfused: bool = False) -> str:
    torch.manual_seed(2)
    branch = MiniMaxH3ControlBranch(
        MiniMaxH3Config.from_detect_config(DEEP_PRUNED), CONTROL_V1_PLACES, 16, fp32_ops(),
    )
    state = {k: torch.ones_like(v) if ".norm" in k else torch.randn_like(v) * 0.02 for k, v in branch.state_dict().items()}
    if unfused:
        state["control_blocks.0.attn.to_q.weight"] = torch.zeros(4, 4)
    save_file(state, str(path))
    return str(path)


class _Models:
    def __init__(self):
        self.calls = []
        self.loaded = {}

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        self.loaded[key] = loader()
        return self.loaded[key]

    def is_cached(self, key):
        return False


def _run(tmp_path: Path, **over):
    config = ModelLoaderMinimaxH3Pipe.get_default_config()
    for name in ("model", "text_encoder", "video_vae", "audio_vae"):
        config[name] = {"file_path": _touch(tmp_path / f"{name}.safetensors")}
    config.update(over)
    config["device"] = "cpu"

    def fake_load(self, path, kind, **kwargs):
        if kind == "diffusion_model":
            return NativeModel("diffusion_model", _build_ready(DEEP_PRUNED),
                               estimated_vram_gb=1.0, compute_dtype=torch.float32)
        return NativeModel(kind, SimpleNamespace(), estimated_vram_gb=0.5, compute_dtype=torch.float32)

    models = _Models()
    with patch("src.platform.runtime.native.engine.NativeEngineLoader.load", fake_load):
        out = ModelLoaderMinimaxH3Pipe(config).process(PipeInput(input={"MODELS": models}), lambda _: None)
    return models, out


def test_the_patch_is_attached_to_the_loaded_dit(tmp_path):
    patch_path = _patch_file(tmp_path / "control.safetensors")
    _, out = _run(tmp_path, control_module={"file_path": patch_path})
    module = out.output["model"].dit.module
    assert control_attached(module)
    assert module.control_branch.places == CONTROL_V1_PLACES
    assert module.control_info.kind == "v1"


def test_no_patch_leaves_the_dit_plain(tmp_path):
    _, out = _run(tmp_path)
    assert not control_attached(out.output["model"].dit.module)


def test_a_control_dit_has_its_own_cache_identity(tmp_path):
    patch_path = _patch_file(tmp_path / "control.safetensors")
    plain, _ = _run(tmp_path)
    with_control, _ = _run(tmp_path, control_module={"file_path": patch_path})
    plain_key, plain_fp = next(c for c in plain.calls if c[0].startswith("native/dit/"))
    key, fp = next(c for c in with_control.calls if c[0].startswith("native/dit/"))
    assert key != plain_key and key.endswith(f"+control={patch_path}")
    assert fp != plain_fp and f"control={patch_path}" in fp


def test_the_patch_bytes_are_charged_to_the_dit_estimate(tmp_path):
    patch_path = _patch_file(tmp_path / "control.safetensors")
    _, out = _run(tmp_path, control_module={"file_path": patch_path})
    assert out.output["model"].dit.estimated_vram_gb > 1.0


def test_a_patch_and_a_vdn_branch_are_refused_together(tmp_path):
    patch_path = _patch_file(tmp_path / "control.safetensors")
    other = _touch(tmp_path / "vdn.safetensors")
    with pytest.raises(ValueError, match="VDN"):
        _run(tmp_path, control_module={"file_path": patch_path}, vdn_module={"file_path": other})


def test_the_original_full_adaln_file_names_the_pruned_patch(tmp_path):
    bad = _patch_file(tmp_path / "control.safetensors", unfused=True)
    with pytest.raises(NativeEngineUnsupportedError, match="Comfy-Org/MiniMax-H3"):
        _run(tmp_path, control_module={"file_path": bad})


def test_a_file_that_is_no_control_patch_is_refused(tmp_path):
    other = _touch(tmp_path / "other.safetensors")
    with pytest.raises(NativeEngineUnsupportedError, match="not a MiniMax-H3 Fun ControlNet patch"):
        _run(tmp_path, control_module={"file_path": other})
