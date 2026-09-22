"""Numerical parity: ``QwenImage21DiT`` vs the diffusers reference transformer.

Loads ``fixtures/transformer_qwenimage21_reference.py`` (a verbatim copy of
diffusers' ``transformer_qwenimage21.py``, Apache-2.0) standalone via
``importlib`` so its own relative imports (``..attention``,
``..attention_dispatch``, ``..cache_utils``, ...) resolve against the
installed ``diffusers`` package. Skips cleanly when that fails (fixture
missing, or an incompatible diffusers version) rather than failing the build.

Weights are copied reference -> ours through
``convert_qwen_image21_state_dict`` (the same split->fused MLP conversion the
loader uses) plus a straight ``load_state_dict`` for everything else, since
every other key already matches 1:1 by name (verified by
``test_state_dict_key_parity_after_conversion`` below). The reference's own
forward contract differs from ours (it takes a pre-flattened joint
``hidden_states``/``img_shapes``/``img_mask`` VLM-slot layout rather than raw
per-image tensors), so each test builds the equivalent inputs by hand for the
plain text+target-image case (no condition images) -- ``ref_latents`` are
NOT covered here: the reference's condition-image slots are VLM-embedded
placeholders inside ``encoder_hidden_states`` (real content from a
vision-language encoder), not the zero/append mechanism this file's ``x``
convention would need, so a literal per-tensor parity test for that path
would require building a full VLM front end. Out of scope; ``ref_latents``
plumbing stays dormant/minimal per the arch's own module docstring.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.qwen_image21.model import QwenImage21DiT, convert_qwen_image21_state_dict
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from vendor.gpl.comfyui.ops import pick_operations

_FIXTURE = Path(__file__).parent / "fixtures" / "transformer_qwenimage21_reference.py"

DIM, HEADS, HEAD_DIM, LAYERS, CTX, IN_CH, OUT_CH, MLP_RATIO, AXES = 64, 4, 16, 2, 32, 8, 8, 3, (4, 6, 6)

CONFIG = {
    "image_model": "qwen_image21", "in_channels": IN_CH, "out_channels": OUT_CH,
    "inner_dim": DIM, "num_layers": LAYERS, "num_attention_heads": HEADS,
    "attention_head_dim": HEAD_DIM, "joint_attention_dim": CTX, "mlp_ratio": MLP_RATIO,
    "axes_dims_rope": AXES, "theta": 10000, "fused_mlp": True,
}


def _load_reference_module():
    if not _FIXTURE.exists():
        pytest.skip(f"reference fixture missing: {_FIXTURE}")
    spec = importlib.util.spec_from_file_location(
        "diffusers.models.transformers.transformer_qwenimage21", str(_FIXTURE),
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "diffusers.models.transformers"
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"reference fixture could not be loaded against installed diffusers: {exc}")
    return module


@pytest.fixture(scope="module")
def refmod():
    return _load_reference_module()


def _build_reference(refmod):
    torch.manual_seed(0)
    ref = refmod.QwenImage21Transformer2DModel(
        patch_size=1, in_channels=IN_CH, out_channels=OUT_CH, num_layers=LAYERS,
        attention_head_dim=HEAD_DIM, num_attention_heads=HEADS, context_in_dim=CTX,
        mlp_ratio=MLP_RATIO, axes_dims_rope=AXES, eps=1e-6, causal_condition=True,
    )
    ref.eval()
    return ref


def _build_ours(state_dict, dtype=torch.float32):
    ops = pick_operations(dtype, dtype)
    m = QwenImage21DiT.from_config(CONFIG, ops)
    converted = convert_qwen_image21_state_dict(state_dict)
    load_into_module(m, converted, match_model_spec(CONFIG))
    m.eval()
    return m


def _reference_forward(ref, x, context, timestep, attention_mask=None):
    """Build the reference's VLM-slot inputs for the plain text+target-image
    (no condition images) case and return the target's reshaped output,
    matching our ``forward``'s ``(B, out_channels, H, W)`` contract."""
    b, _c, h, w = x.shape
    l_txt = context.shape[1]
    target_tokens = h * w
    hidden_states = x.flatten(2).transpose(1, 2)
    img_shapes = [[(1, h, w)]]
    img_mask = torch.zeros(b, l_txt + target_tokens // 4, dtype=torch.bool)
    img_mask[:, l_txt:] = True
    enc_mask = attention_mask.bool() if attention_mask is not None else None
    with torch.no_grad():
        out = ref(
            hidden_states=hidden_states, encoder_hidden_states=context, timestep=timestep,
            img_shapes=img_shapes, img_mask=img_mask, encoder_hidden_states_mask=enc_mask,
            return_dict=False,
        )[0]
    return out[:, -target_tokens:].transpose(1, 2).reshape(b, OUT_CH, h, w)


def test_state_dict_key_parity_after_conversion(refmod):
    ref = _build_reference(refmod)
    converted = convert_qwen_image21_state_dict(ref.state_dict())
    with torch.device("meta"):
        m = QwenImage21DiT.from_config(CONFIG, pick_operations(torch.float32, torch.float32))
    assert set(converted.keys()) == set(m.state_dict().keys())
    for k, v in converted.items():
        assert tuple(v.shape) == tuple(m.state_dict()[k].shape), k


@pytest.mark.parametrize("h,w", [(4, 4), (8, 8), (4, 8), (8, 4), (6, 10), (12, 16), (20, 28)])
def test_forward_matches_reference_fp32(refmod, h, w):
    ref = _build_reference(refmod)
    ours = _build_ours(ref.state_dict())

    torch.manual_seed(2)
    x = torch.randn(1, IN_CH, h, w)
    context = torch.randn(1, 6, CTX)
    t = torch.tensor([0.37])

    with torch.no_grad():
        out_ours = ours(x, t, context)
    out_ref = _reference_forward(ref, x, context, t)

    torch.testing.assert_close(out_ours, out_ref, atol=1e-4, rtol=1e-4)


def test_forward_matches_reference_with_padded_text_mask(refmod):
    ref = _build_reference(refmod)
    ours = _build_ours(ref.state_dict())

    torch.manual_seed(4)
    x = torch.randn(1, IN_CH, 4, 4)
    real_len, pad_len = 5, 3
    context = torch.randn(1, real_len + pad_len, CTX)
    mask = torch.zeros(1, real_len + pad_len, dtype=torch.long)
    mask[:, :real_len] = 1
    t = torch.tensor([0.5])

    with torch.no_grad():
        out_ours = ours(x, t, context, attention_mask=mask.float())
    out_ref = _reference_forward(ref, x, context, t, attention_mask=mask)

    torch.testing.assert_close(out_ours, out_ref, atol=1e-4, rtol=1e-4)


def test_forward_matches_reference_batched():
    refmod_ = _load_reference_module()
    ref = _build_reference(refmod_)
    ours = _build_ours(ref.state_dict())

    torch.manual_seed(5)
    x = torch.randn(3, IN_CH, 4, 4)
    context = torch.randn(3, 6, CTX)
    t = torch.tensor([0.1, 0.5, 0.9])

    with torch.no_grad():
        out_ours = ours(x, t, context)
    out_ref = _reference_forward(ref, x, context, t)

    torch.testing.assert_close(out_ours, out_ref, atol=1e-4, rtol=1e-4)


def test_forward_matches_reference_bf16(refmod):
    ref = _build_reference(refmod)
    ref_sd_bf16 = {k: v.to(torch.bfloat16) for k, v in ref.state_dict().items()}
    ref_bf16 = _build_reference(refmod)
    ref_bf16.load_state_dict(ref_sd_bf16, assign=True)
    ref_bf16.eval()
    ours_bf16 = _build_ours(ref_sd_bf16, dtype=torch.bfloat16)

    torch.manual_seed(6)
    x = torch.randn(1, IN_CH, 8, 8, dtype=torch.bfloat16)
    context = torch.randn(1, 6, CTX, dtype=torch.bfloat16)
    t = torch.tensor([0.37], dtype=torch.bfloat16)

    with torch.no_grad():
        out_ours = ours_bf16(x, t, context)
    out_ref = _reference_forward(ref_bf16, x, context, t)

    torch.testing.assert_close(out_ours.float(), out_ref.float(), atol=5e-2, rtol=5e-2)
