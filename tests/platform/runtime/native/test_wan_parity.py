"""Key-parity: the vendored WanModel's state-dict keys must EXACTLY match the
real local Wan checkpoints (build empty-weight under meta device, diff keys).

Skips when the (multi-GB) checkpoints are absent, so CI without them stays green;
runs wherever the models/ dir is populated.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.io.state_dict_utils import detect_prefix
from vendor.gpl.comfyui.ops import disable_weight_init as _ops
from src.platform.runtime.native.arch.wan.model import WanModel

_MODELS = Path("models/diffusion_models")
_PREFIXES = ["model.diffusion_model.", "diffusion_model."]
_SIDECAR = (".weight_scale", ".input_scale", ".scale_weight", ".scale_input",
            ".weight_scale_2", ".comfy_quant")

# (filename, expected variant) for representative local checkpoints.
_CASES = [
    ("wan2.2_t2v_low_noise_14B_fp16.safetensors", "wan_t2v_14b"),
    ("wan2.2_ti2v_5B_fp16.safetensors", "wan_ti2v_5b"),
    ("DasiwaWAN22I2V14BLightspeed_synthseductionHighV9.safetensors", "wan22_i2v_14b"),
]


def _header_shapes(path: Path) -> dict[str, list[int]]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        h = json.loads(f.read(n))
    return {k: h[k]["shape"] for k in h if k != "__metadata__"}


@pytest.mark.requires_models
@pytest.mark.parametrize("filename,expected_variant", _CASES)
def test_wan_key_parity_against_real_checkpoint(filename, expected_variant):
    path = _MODELS / filename
    if not path.exists():
        pytest.skip(f"checkpoint not present: {path}")

    shapes = _header_shapes(path)
    prefix = detect_prefix({k: None for k in shapes}, _PREFIXES)
    if prefix:
        shapes = {k[len(prefix):]: v for k, v in shapes.items()}

    # detection only needs a handful of real shapes; the rest can be empty.
    sd = {k: torch.zeros(0) for k in shapes}
    for k in ("head.modulation", "head.head.weight", "patch_embedding.weight",
              "text_embedding.0.weight", "blocks.0.ffn.0.weight"):
        sd[k] = torch.zeros(shapes[k])

    config = detect_unet_config(sd)
    spec = match_model_spec(config)
    assert spec.variant == expected_variant

    with torch.device("meta"):
        module = WanModel.from_config(config, _ops)

    module_keys = set(module.state_dict().keys())
    real_keys = {k for k in shapes if not k.endswith(_SIDECAR) and k != "scaled_fp8"}

    missing = module_keys - real_keys
    unexpected = real_keys - module_keys
    assert not missing, f"module expects keys absent from checkpoint: {sorted(missing)[:8]}"
    assert not unexpected, f"checkpoint has keys the module doesn't declare: {sorted(unexpected)[:8]}"
