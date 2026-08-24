"""Key-set parity between each arch module and the real checkpoint layout.

The expected key set comes from ``_fixtures`` (captured from the real local
headers). Module key names depend only on structure/layer-count, so we build with
the real layer counts and tiny hidden sizes. A second, optional test cross-checks
the fixtures against the actual on-disk headers when the (multi-GB) files exist —
header parse only, never a full load.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from vendor.gpl.comfyui.ops import disable_weight_init as ops  # noqa: E402
from src.platform.runtime.native.text_encoders.clip_l import CLIPLModel  # noqa: E402
from src.platform.runtime.native.text_encoders.loader import _SPECS  # noqa: E402
from src.platform.runtime.native.text_encoders.qwen3 import Qwen3Model  # noqa: E402
from src.platform.runtime.native.text_encoders.t5xxl import T5XXLModel  # noqa: E402

from . import _fixtures as fx  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[5]

_CASES = {
    "qwen3": (
        fx.QWEN3_4B,
        Qwen3Model,
        {"hidden_size": 64, "num_layers": 36, "vocab_size": 151936, "intermediate_size": 128},
        "models/clip/qwen_3_4b.safetensors",
    ),
    "t5xxl": (
        fx.T5XXL,
        T5XXLModel,
        {"hidden_size": 64, "num_layers": 24, "vocab_size": 32128, "num_heads": 4, "d_kv": 16, "d_ff": 128},
        "models/clip/t5xxl_fp8_e4m3fn_scaled.safetensors",
    ),
    "clip_l": (
        fx.CLIP_L,
        CLIPLModel,
        {"hidden_size": 24, "num_layers": 12, "vocab_size": 49408, "intermediate_size": 48},
        "models/clip/clip_l.safetensors",
    ),
}


def _assert_parity(module_keys: set[str], real_keys: set[str], spec) -> None:
    bad_unexpected = [k for k in real_keys - module_keys if not spec.key_is_expected_unexpected(k)]
    bad_missing = [k for k in module_keys - real_keys if not spec.key_is_expected_missing(k)]
    assert not bad_unexpected, f"checkpoint keys not in module/allowlist: {bad_unexpected[:10]}"
    assert not bad_missing, f"module keys not in checkpoint/allowlist: {bad_missing[:10]}"


@pytest.mark.parametrize("te_type", list(_CASES))
def test_module_matches_fixture(te_type):
    fixture, model_cls, cfg, _ = _CASES[te_type]
    module = model_cls.from_config(cfg, ops)
    _assert_parity(set(module.state_dict()), fx.expand_keys(fixture), _SPECS[te_type])


@pytest.mark.requires_models
@pytest.mark.parametrize("te_type", list(_CASES))
def test_fixture_matches_real_header(te_type):
    _, _, _, rel = _CASES[te_type]
    path = _REPO_ROOT / rel
    if not path.is_file():
        pytest.skip(f"real checkpoint not present: {rel}")
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    real_keys = {k for k in header if k != "__metadata__"}
    assert fx.expand_keys(_CASES[te_type][0]) == real_keys
