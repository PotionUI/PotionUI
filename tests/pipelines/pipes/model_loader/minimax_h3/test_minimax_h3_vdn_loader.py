"""The VDN wiring in ``model_loader/minimax_h3``: the branch is attached to the
freshly loaded DiT under its own cache identity, and an adapter carrying AdaLN
rows trained against the FULL checkpoint is translated onto a pruned DiT rather
than silently dropped.

Real tiny models on CPU, real safetensors files on disk -- ``load_dit`` has to
actually run for any of this to be observable, so the fake ``MODELS`` service
here calls its loader (unlike ``test_minimax_h3_model_loader.py``'s, which never
does).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
from safetensors.torch import save_file

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.model_loader.minimax_h3 import vdn as loader_vdn
from src.pipelines.pipes.model_loader.minimax_h3.main import ModelLoaderMinimaxH3Pipe
from src.platform.runtime.native.arch.minimax_h3.vdn import (
    MiniMaxH3LinearBranch,
    MiniMaxH3VdnAttention,
    vdn_attached,
)
from src.platform.runtime.native.engine import NativeModel

from tests.platform.runtime.native.arch.test_minimax_h3_model import (
    TINY_COMMON,
    TINY_FULL,
    _build_ready,
    _fp32_ops,
)

# The pruned twin needs more curve rows than table columns for the affine fit to
# be determined at all (fit_affine_grid refuses G < k + 1).
TINY_PRUNED = dict(TINY_COMMON, pruned=True, time_embed_dim=6, adaln_curve_grid=9)

DENSE_T_DIM = TINY_FULL["time_embed_dim"]
FREQ_DIM = TINY_FULL["freq_dim"]
TE_HIDDEN = TINY_FULL["time_embed_hidden_dim"]
RANK = 2


# --- fixtures on disk -------------------------------------------------------

def _touch(path: Path) -> str:
    save_file({"placeholder": torch.zeros(4)}, str(path))
    return str(path)


def _branch_file(path: Path, config: dict) -> str:
    proto = MiniMaxH3LinearBranch(
        config["hidden_size"], config["num_attention_heads"], config["attention_head_dim"], _fp32_ops(),
    )
    state = {}
    for index in range(config["num_layers"]):
        for tail, value in proto.state_dict().items():
            state[f"transformer_blocks.{index}.attn.{tail}"] = torch.randn_like(value) * 0.02
    save_file(state, str(path))
    return str(path)


def _time_embedder_file(path: Path) -> str:
    save_file({
        "time_embedder.proj_in.weight": torch.randn(TE_HIDDEN, FREQ_DIM),
        "time_embedder.proj_in.bias": torch.randn(TE_HIDDEN),
        "time_embedder.proj_out.weight": torch.randn(DENSE_T_DIM, TE_HIDDEN),
        "time_embedder.proj_out.bias": torch.randn(DENSE_T_DIM),
    }, str(path))
    return str(path)


def _adaln_adapter_file(path: Path, config: dict) -> str:
    """A turbo-shaped adapter: attention rows plus the dense-dialect AdaLN tier."""
    hidden = config["hidden_size"]
    inner = config["num_attention_heads"] * config["attention_head_dim"]
    state = {
        "transformer_blocks.0.attn.orig.to_q.lora_A.turbo.weight": torch.randn(RANK, hidden) * 0.02,
        "transformer_blocks.0.attn.orig.to_q.lora_B.turbo.weight": torch.randn(inner, RANK) * 0.02,
    }
    for index in range(config["num_layers"]):
        state[f"transformer_blocks.{index}.adaln_proj.linear.lora_A.turbo.weight"] = (
            torch.randn(RANK, DENSE_T_DIM) * 0.02
        )
        state[f"transformer_blocks.{index}.adaln_proj.linear.lora_B.turbo.weight"] = (
            torch.randn(6 * hidden * 3, RANK) * 0.02
        )
    state["norm_out.linear.lora_A.turbo.weight"] = torch.randn(RANK, DENSE_T_DIM) * 0.02
    state["norm_out.linear.lora_B.turbo.weight"] = torch.randn(2 * hidden, RANK) * 0.02
    save_file(state, str(path))
    return str(path)


class _Models:
    """A ``MODELS`` service that really runs each component's loader.

    It also KEEPS what it loaded: the bundle holds its components through
    ``WeakModelRef``, so a service that dropped them would hand the test a
    bundle of ``None``.
    """

    def __init__(self):
        self.calls = []
        self.loaded = {}

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        self.loaded[key] = loader()
        return self.loaded[key]

    def is_cached(self, key):
        return False


def _paths(tmp_path: Path) -> dict:
    return {
        "model": {"file_path": _touch(tmp_path / "dit.safetensors")},
        "text_encoder": {"file_path": _touch(tmp_path / "te.safetensors")},
        "video_vae": {"file_path": _touch(tmp_path / "vvae.safetensors")},
        "audio_vae": {"file_path": _touch(tmp_path / "avae.safetensors")},
    }


def _run(tmp_path: Path, dit_config: dict, **over):
    config = ModelLoaderMinimaxH3Pipe.get_default_config()
    config.update(_paths(tmp_path))
    config.update(over)
    config["device"] = "cpu"

    def fake_load(self, path, kind, **kwargs):
        if kind == "diffusion_model":
            return NativeModel("diffusion_model", _build_ready(dit_config),
                               estimated_vram_gb=1.0, compute_dtype=torch.float32)
        return NativeModel(kind, SimpleNamespace(), estimated_vram_gb=0.5, compute_dtype=torch.float32)

    models = _Models()
    emitted = []
    with patch("src.platform.runtime.native.engine.NativeEngineLoader.load", fake_load):
        out = ModelLoaderMinimaxH3Pipe(config).process(
            PipeInput(input={"MODELS": models}), emitted.append,
        )
    return models, out, emitted


# --- attach and identity ----------------------------------------------------

def test_the_branch_is_attached_to_the_loaded_dit(tmp_path):
    branch = _branch_file(tmp_path / "branch.safetensors", TINY_FULL)
    _, out, _ = _run(tmp_path, TINY_FULL, vdn_module={"file_path": branch})

    module = out.output["model"].dit.module
    assert vdn_attached(module) is True
    assert all(isinstance(block.attn, MiniMaxH3VdnAttention) for block in module.blocks)


def test_no_branch_configured_leaves_the_dit_dense(tmp_path):
    _, out, _ = _run(tmp_path, TINY_FULL)
    assert vdn_attached(out.output["model"].dit.module) is False


def test_a_vdn_dit_never_shares_a_cache_entry_with_the_plain_one(tmp_path):
    branch = _branch_file(tmp_path / "branch.safetensors", TINY_FULL)
    plain, _, _ = _run(tmp_path, TINY_FULL)
    with_vdn, _, _ = _run(tmp_path, TINY_FULL, vdn_module={"file_path": branch})

    plain_key, plain_fp = next(c for c in plain.calls if c[0].startswith("native/dit/"))
    vdn_key, vdn_fp = next(c for c in with_vdn.calls if c[0].startswith("native/dit/"))
    assert plain_key != vdn_key
    assert vdn_key.endswith("+vdn")
    assert plain_fp != vdn_fp
    assert f"vdn={branch}" in vdn_fp


def test_the_sidecar_is_part_of_the_dit_identity(tmp_path):
    sidecar = _time_embedder_file(tmp_path / "te_sidecar.safetensors")
    without, _, _ = _run(tmp_path, TINY_FULL)
    with_sidecar, _, _ = _run(tmp_path, TINY_FULL, dense_time_embedder={"file_path": sidecar})
    assert dict(without.calls) != dict(with_sidecar.calls)
    fingerprint = next(fp for key, fp in with_sidecar.calls if key.startswith("native/dit/"))
    assert f"te={sidecar}" in fingerprint


def test_the_branch_bytes_are_charged_to_the_dit_vram_estimate(tmp_path):
    branch = _branch_file(tmp_path / "branch.safetensors", TINY_FULL)
    _, out, _ = _run(tmp_path, TINY_FULL, vdn_module={"file_path": branch})
    assert out.output["model"].dit.estimated_vram_gb > 1.0


def test_the_attach_report_reaches_the_generation_outputs(tmp_path):
    branch = _branch_file(tmp_path / "branch.safetensors", TINY_FULL)
    _, _, emitted = _run(tmp_path, TINY_FULL, vdn_module={"file_path": branch})
    lines = [getattr(o, "state", "") for o in emitted]
    assert any("VDN branch attached to 2 block(s)" in line for line in lines)


def test_a_quantised_ops_namespace_still_holds_the_unquantised_branch():
    """The branch ships bf16 with no scale sidecars; ``fp8_ops.Linear`` keeps
    every scale as a non-persistent buffer, so it assign-loads unchanged and
    runs through the plain cast path. Pinned because the alternative -- building
    the branch under a second namespace -- would be a real design change."""
    from vendor.gpl.comfyui.ops import fp8_ops

    with torch.device("meta"):
        linear = fp8_ops.Linear(8, 4, bias=False, dtype=torch.bfloat16)
    assert list(linear.state_dict()) == ["weight"]

    weight = torch.randn(4, 8, dtype=torch.bfloat16)
    linear.load_state_dict({"weight": weight}, strict=True, assign=True)
    assert linear.weight.data_ptr() == weight.data_ptr()
    assert linear.weight_scale is None

    x = torch.randn(3, 8, dtype=torch.bfloat16)
    assert torch.equal(linear(x), torch.nn.functional.linear(x, weight))


# --- dense AdaLN translation ------------------------------------------------

def test_a_dense_adaln_adapter_on_a_pruned_dit_is_translated(tmp_path):
    adapter = _adaln_adapter_file(tmp_path / "turbo.safetensors", TINY_PRUNED)
    sidecar = _time_embedder_file(tmp_path / "te_sidecar.safetensors")
    _, out, emitted = _run(
        tmp_path, TINY_PRUNED,
        loras=[{"file_path": adapter, "weight": 1.0}],
        dense_time_embedder={"file_path": sidecar},
        vdn_module={"file_path": _branch_file(tmp_path / "branch.safetensors", TINY_PRUNED)},
    )
    dit = out.output["model"].dit
    attachment = dit._vdn_attachment
    assert attachment.adaln_residual is not None
    assert attachment.adaln_residual >= 0.0
    assert any("fit residual" in getattr(o, "state", "") for o in emitted)

    # Nothing was dropped: the AdaLN rows landed as weight deltas and the
    # constant half landed on the bias.
    report = dit._active_lora_application[0]
    assert report.unmatched_keys == 0
    assert report.matched_params > 0


def test_the_translated_rows_actually_change_the_pruned_adaln_weights(tmp_path):
    adapter = _adaln_adapter_file(tmp_path / "turbo.safetensors", TINY_PRUNED)
    sidecar = _time_embedder_file(tmp_path / "te_sidecar.safetensors")

    baseline = _build_ready(TINY_PRUNED)
    before_weight = baseline.blocks[0].adaln_proj.linear.weight.clone()
    before_bias = baseline.blocks[0].adaln_proj.linear.bias.clone()
    before_final_bias = baseline.final_layer.adaln_proj.linear.bias.clone()

    model = NativeModel("diffusion_model", baseline, estimated_vram_gb=1.0, compute_dtype=torch.float32)
    reports, residual = loader_vdn.apply_loras_with_adaln_translation(
        model, [{"file_path": adapter, "weight": 1.0}], "TEST",
        dense_time_embedder_path=sidecar,
    )

    assert residual is not None
    assert not torch.equal(baseline.blocks[0].adaln_proj.linear.weight, before_weight)
    assert not torch.equal(baseline.blocks[0].adaln_proj.linear.bias, before_bias)
    # The final layer's own AdaLN target rides the same translation --
    # `norm_out.linear` is the diffusers name for it.
    assert not torch.equal(baseline.final_layer.adaln_proj.linear.bias, before_final_bias)
    assert reports[0].unmatched_keys == 0


def test_a_translation_is_reported_even_with_no_branch_attached(tmp_path):
    """The two halves are independent: a pruned DiT can need the AdaLN
    translation without anyone asking for the VDN branch."""
    adapter = _adaln_adapter_file(tmp_path / "turbo.safetensors", TINY_PRUNED)
    sidecar = _time_embedder_file(tmp_path / "te_sidecar.safetensors")
    _, out, emitted = _run(
        tmp_path, TINY_PRUNED,
        loras=[{"file_path": adapter, "weight": 1.0}],
        dense_time_embedder={"file_path": sidecar},
    )
    attachment = out.output["model"].dit._vdn_attachment
    assert attachment.report is None
    assert attachment.adaln_residual is not None
    assert any("fit residual" in getattr(o, "state", "") for o in emitted)
    assert not any("VDN branch attached" in getattr(o, "state", "") for o in emitted)


def test_dense_adaln_rows_without_a_sidecar_name_the_setting(tmp_path):
    adapter = _adaln_adapter_file(tmp_path / "turbo.safetensors", TINY_PRUNED)
    with pytest.raises(ValueError, match="dense_time_embedder"):
        _run(tmp_path, TINY_PRUNED, loras=[{"file_path": adapter, "weight": 1.0}])


def test_a_full_dit_maps_the_same_adapter_directly_with_no_translation(tmp_path):
    adapter = _adaln_adapter_file(tmp_path / "turbo.safetensors", TINY_FULL)
    # No sidecar configured: a full DiT's AdaLN projection already takes the
    # dense width, so nothing needs translating and nothing may raise.
    _, out, _ = _run(tmp_path, TINY_FULL, loras=[{"file_path": adapter, "weight": 1.0}])
    dit = out.output["model"].dit
    assert getattr(dit, "_vdn_attachment", None) is None
    assert dit._active_lora_application[0].matched_params > 0
    assert dit._active_lora_application[0].unmatched_keys == 0


def test_a_sidecar_missing_its_tensors_is_refused(tmp_path):
    bogus = _touch(tmp_path / "not_a_sidecar.safetensors")
    with pytest.raises(ValueError, match="time_embedder.proj_in.weight"):
        loader_vdn.load_dense_time_embedder(bogus)


def test_the_sidecar_dimensions_come_from_its_own_tensors(tmp_path):
    sidecar = _time_embedder_file(tmp_path / "te_sidecar.safetensors")
    embedder = loader_vdn.load_dense_time_embedder(sidecar)
    assert embedder.freq_dim == FREQ_DIM
    assert embedder.proj_in.out_features == TE_HIDDEN
    assert embedder.proj_out.out_features == DENSE_T_DIM
