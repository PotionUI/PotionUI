"""Per-file LoRA application evidence (``AdapterApplication``).

``apply_loras``'s aggregate ``(num_params_patched, unmatched_keys)`` hides a
stack of one working adapter and one wholly-dead one behind "some params
patched" — this is what a stack of REAL kohya-dialect LoRAs, applied to a REAL
tiny Flux DiT, proves ``apply_loras_with_report`` fixes: "applied"/"unchanged"/
"ignored" are checked by reading the weight tensor and the returned report,
not by counting calls on a mock.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.lora import (
    IgnoredContribution,
    apply_loras,
    apply_loras_with_report,
)
from vendor.gpl.comfyui.ops import pick_operations

TINY = {
    "image_model": "flux2", "hidden_size": 64, "num_heads": 2, "depth": 1,
    "depth_single_blocks": 1, "in_channels": 16, "out_channels": 16,
    "context_in_dim": 32, "axes_dim": [8, 8, 8, 8], "mlp_ratio": 3.0,
    "theta": 2000, "patch_size": 1, "qkv_bias": False, "guidance_embed": False,
}
QKV = "double_blocks.0.img_attn.qkv"
STEM = "lora_unet_double_blocks_0_img_attn_qkv"


def _build() -> torch.nn.Module:
    m = Flux.from_config(TINY, pick_operations(torch.float32, torch.float32))
    sd = {}
    g = torch.Generator().manual_seed(99)
    for k, v in m.state_dict().items():
        if k.endswith(".scale") and "norm" in k:
            sd[k] = torch.ones_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn(v.shape, dtype=v.dtype, generator=g) * 0.05
        else:
            sd[k] = v.clone()
    load_into_module(m, sd, match_model_spec(TINY))
    m.eval()
    return m


def _kohya_lora(stem: str = STEM, out: int = 192, inf: int = 64, rank: int = 4, seed: int = 1) -> dict:
    g = torch.Generator().manual_seed(seed)
    return {
        f"{stem}.lora_up.weight": torch.randn(out, rank, generator=g) * 0.1,
        f"{stem}.lora_down.weight": torch.randn(rank, inf, generator=g) * 0.1,
        f"{stem}.alpha": torch.tensor(float(rank)),
    }


def _target_weight(module: torch.nn.Module) -> torch.Tensor:
    return dict(module.named_modules())[QKV].weight.detach().clone()


# -- aggregate hides a dead adapter; the per-file report does not -----------

def test_valid_and_wholly_unmatched_stack_reports_per_file_evidence():
    m = _build()
    valid = _kohya_lora(seed=1)
    bogus = _kohya_lora(stem="lora_unet_totally_bogus", seed=2)

    patched, unmatched, reports = apply_loras_with_report(m, [(valid, 1.0), (bogus, 1.0)])

    # The aggregate alone reads as full success -- this is the bug the
    # per-file report exists to catch.
    assert patched > 0
    assert len(reports) == 2

    valid_report, bogus_report = reports
    assert valid_report.matched_params == patched
    assert not valid_report.zero_effect
    assert valid_report.unmatched_keys == 0

    assert bogus_report.zero_effect
    assert bogus_report.matched_params == 0
    assert bogus_report.unmatched_keys >= 1
    assert "lora_unet_totally_bogus" in bogus_report.unmatched_sample


def test_wholly_unmatched_stack_leaves_weights_unchanged():
    m = _build()
    before = _target_weight(m)
    bogus = _kohya_lora(stem="lora_unet_totally_bogus", seed=3)

    patched, _unmatched, reports = apply_loras_with_report(m, [(bogus, 1.0)])

    assert patched == 0
    assert len(reports) == 1
    assert reports[0].zero_effect
    assert torch.equal(_target_weight(m), before)


def test_apply_loras_keeps_its_two_tuple_contract_over_a_mixed_stack():
    """The thin wrapper must not change what existing callers already unpack."""
    m = _build()
    valid = _kohya_lora(seed=4)
    bogus = _kohya_lora(stem="lora_unet_totally_bogus", seed=5)

    result = apply_loras(m, [(valid, 1.0), (bogus, 1.0)])

    assert isinstance(result, tuple) and len(result) == 2
    patched, unmatched = result
    assert patched > 0
    assert "lora_unet_totally_bogus" in unmatched


# -- a partially-supported file: counts and a bounded sample -----------------

def test_partially_supported_file_records_unmatched_sample():
    m = _build()
    lora_sd = _kohya_lora(seed=6)
    lora_sd.update(_kohya_lora(stem="lora_unet_also_bogus", seed=7))

    _patched, _unmatched, reports = apply_loras_with_report(m, [(lora_sd, 1.0)])

    (report,) = reports
    assert not report.zero_effect
    assert report.matched_params > 0
    assert report.unmatched_keys == 1
    assert report.unmatched_sample == ("lora_unet_also_bogus",)


# -- a DoRA magnitude sidecar: ignored, not applied ---------------------------

def test_dora_scale_sidecar_is_reported_ignored_and_weights_match_plain_lora():
    plain_module = _build()
    dora_module = _build()  # same seed -> identical starting weights

    plain_lora = _kohya_lora(seed=8)
    dora_lora = dict(plain_lora)
    dora_lora[f"{STEM}.dora_scale"] = torch.randn(192, 1, generator=torch.Generator().manual_seed(9))

    _p1, _u1, (plain_report,) = apply_loras_with_report(plain_module, [(plain_lora, 1.0)])
    _p2, _u2, (dora_report,) = apply_loras_with_report(dora_module, [(dora_lora, 1.0)])

    assert plain_report.ignored == ()
    assert dora_report.ignored == (IgnoredContribution(kind="dora_scale", count=1),)
    # The DoRA magnitude was never applied -- both files patch identically.
    assert dora_report.matched_params == plain_report.matched_params
    assert torch.equal(_target_weight(plain_module), _target_weight(dora_module))
