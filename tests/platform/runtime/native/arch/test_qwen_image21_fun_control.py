from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.arch.qwen_image21.fun_control import (
    attach_fun_control,
    convert_fun_control_state_dict,
)
from src.platform.runtime.native.arch.qwen_image21.model import QwenImage21DiT, convert_qwen_image21_state_dict
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.patch_detect import QWEN_IMAGE21_FUN_CONTROL, detect_model_patch_config
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.engine import NativeEngineLoader, RunCache
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from vendor.gpl.comfyui.ops import pick_operations

from .test_qwen_image21_parity import _load_reference_module

_FIXTURES = Path(__file__).parent / "fixtures"
_CONTROL_FIXTURE = _FIXTURES / "transformer_qwenimage21_control_reference.py"

DIM, HEADS, HEAD_DIM, LAYERS, CTX, IN_CH, MLP_RATIO, AXES = 64, 4, 16, 4, 32, 8, 3, (4, 6, 6)
CONTROL_IN = 2 * IN_CH + 1

CONFIG = {
    "image_model": "qwen_image21", "in_channels": IN_CH, "out_channels": IN_CH,
    "inner_dim": DIM, "num_layers": LAYERS, "num_attention_heads": HEADS,
    "attention_head_dim": HEAD_DIM, "joint_attention_dim": CTX, "mlp_ratio": MLP_RATIO,
    "axes_dims_rope": AXES, "theta": 10000, "fused_mlp": True,
}


@pytest.fixture(scope="module")
def controlmod():
    _load_reference_module()
    if not _CONTROL_FIXTURE.exists():
        pytest.skip(f"control reference fixture missing: {_CONTROL_FIXTURE}")
    spec = importlib.util.spec_from_file_location("qwenimage21_control_reference", str(_CONTROL_FIXTURE))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except (ImportError, AttributeError) as exc:
        pytest.skip(f"control reference fixture could not be loaded against installed diffusers: {exc}")
    return module


def _build_reference(controlmod, seed=0):
    torch.manual_seed(seed)
    ref = controlmod.QwenImage21ControlTransformer2DModel(
        control_layers=None, control_in_dim=CONTROL_IN, patch_size=1, in_channels=IN_CH, out_channels=IN_CH,
        num_layers=LAYERS, attention_head_dim=HEAD_DIM, num_attention_heads=HEADS, context_in_dim=CTX,
        mlp_ratio=MLP_RATIO, axes_dims_rope=AXES, eps=1e-6, causal_condition=True,
    )
    with torch.no_grad():
        for block in ref.control_blocks:
            for name in ("before_proj", "after_proj"):
                proj = getattr(block, name, None)
                if proj is not None:
                    proj.weight.normal_(0.0, 0.05)
                    proj.bias.normal_(0.0, 0.05)
    ref.eval()
    return ref


def _split(ref_sd):
    base = {k: v for k, v in ref_sd.items() if not k.startswith(("control_blocks.", "control_img_in."))}
    patch = {k: v for k, v in ref_sd.items() if k.startswith(("control_blocks.", "control_img_in."))}
    return base, patch


def _build_ours(ref_sd, dtype=torch.float32):
    base, patch = _split(ref_sd)
    config, merged = attach_fun_control(dict(CONFIG), convert_qwen_image21_state_dict(base), patch)
    m = QwenImage21DiT.from_config(config, pick_operations(dtype, dtype))
    load_into_module(m, merged, match_model_spec(config))
    m.eval()
    return m


def _reference_forward(ref, x, context, timestep, control_tokens, scale, attention_mask=None):
    b, _c, h, w = x.shape
    l_txt = context.shape[1]
    target_tokens = h * w
    img_mask = torch.zeros(b, l_txt + target_tokens // 4, dtype=torch.bool)
    img_mask[:, l_txt:] = True
    with torch.no_grad():
        out = ref(
            hidden_states=x.flatten(2).transpose(1, 2), encoder_hidden_states=context, timestep=timestep,
            img_shapes=[[(1, h, w)]], img_mask=img_mask,
            encoder_hidden_states_mask=attention_mask.bool() if attention_mask is not None else None,
            control_context=control_tokens, control_context_scale=scale, return_dict=False,
        )[0]
    return out[:, -target_tokens:].transpose(1, 2).reshape(b, IN_CH, h, w)


def _as_latent(tokens, h, w):
    return tokens.transpose(1, 2).reshape(tokens.shape[0], tokens.shape[2], h, w)


@pytest.mark.parametrize("h,w", [(4, 4), (4, 8), (6, 10)])
@pytest.mark.parametrize("scale", [1.0, 0.6])
def test_control_matches_the_upstream_control_transformer(controlmod, h, w, scale):
    ref = _build_reference(controlmod)
    ours = _build_ours(ref.state_dict())
    torch.manual_seed(2)
    x = torch.randn(1, IN_CH, h, w)
    context = torch.randn(1, 6, CTX)
    tokens = torch.randn(1, h * w, CONTROL_IN)
    t = torch.tensor([0.37])

    with torch.no_grad():
        got = ours(x, t, context, control_context=_as_latent(tokens, h, w), control_context_scale=scale)
    expected = _reference_forward(ref, x, context, t, tokens, scale)

    torch.testing.assert_close(got, expected, atol=1e-4, rtol=1e-4)


def test_control_matches_upstream_with_a_padded_prompt(controlmod):
    ref = _build_reference(controlmod, seed=1)
    ours = _build_ours(ref.state_dict())
    torch.manual_seed(4)
    x = torch.randn(1, IN_CH, 4, 4)
    context = torch.randn(1, 8, CTX)
    mask = torch.zeros(1, 8, dtype=torch.long)
    mask[:, :5] = 1
    tokens = torch.randn(1, 16, CONTROL_IN)
    t = torch.tensor([0.5])

    with torch.no_grad():
        got = ours(x, t, context, attention_mask=mask.float(), control_context=_as_latent(tokens, 4, 4))
    expected = _reference_forward(ref, x, context, t, tokens, 1.0, attention_mask=mask)

    torch.testing.assert_close(got, expected, atol=1e-4, rtol=1e-4)


def test_control_matches_upstream_in_bf16(controlmod):
    ref = _build_reference(controlmod, seed=3)
    sd_bf16 = {k: v.to(torch.bfloat16) for k, v in ref.state_dict().items()}
    ref.load_state_dict(sd_bf16, assign=True)
    ours = _build_ours(sd_bf16, dtype=torch.bfloat16)
    torch.manual_seed(6)
    x = torch.randn(1, IN_CH, 8, 8, dtype=torch.bfloat16)
    context = torch.randn(1, 6, CTX, dtype=torch.bfloat16)
    tokens = torch.randn(1, 64, CONTROL_IN, dtype=torch.bfloat16)
    t = torch.tensor([0.37], dtype=torch.bfloat16)

    with torch.no_grad():
        got = ours(x, t, context, control_context=_as_latent(tokens, 8, 8))
    expected = _reference_forward(ref, x, context, t, tokens, 1.0)

    torch.testing.assert_close(got.float(), expected.float(), atol=5e-2, rtol=5e-2)


def _random_sd(module):
    sd = {}
    for k, v in module.state_dict().items():
        if k.endswith(".weight") and "norm" in k:
            sd[k] = torch.ones_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.05
        else:
            sd[k] = v.clone()
    return sd


def _pair(seed=0):
    torch.manual_seed(seed)
    ops = pick_operations(torch.float32, torch.float32)
    controlled = QwenImage21DiT.from_config(
        {**CONFIG, "fun_control": {"num_blocks": 2, "control_in_dim": CONTROL_IN, "mlp_ratio": MLP_RATIO}}, ops)
    sd = _random_sd(controlled)
    load_into_module(controlled, sd, match_model_spec(CONFIG))
    plain = QwenImage21DiT.from_config(CONFIG, ops)
    load_into_module(plain, {k: v for k, v in sd.items() if not k.startswith("fun_control.")}, match_model_spec(CONFIG))
    return plain.eval(), controlled.eval()


def _inputs(seed=1, h=4, w=4):
    torch.manual_seed(seed)
    return (torch.randn(1, IN_CH, h, w), torch.tensor([0.4]), torch.randn(1, 6, CTX),
            torch.randn(1, CONTROL_IN, h, w))


def test_a_loaded_patch_without_control_is_byte_identical_to_the_plain_model():
    plain, controlled = _pair()
    x, t, context, _ = _inputs()
    with torch.no_grad():
        assert torch.equal(controlled(x, t, context), plain(x, t, context))


def test_zero_strength_is_byte_identical_to_no_control():
    plain, controlled = _pair()
    x, t, context, control = _inputs()
    with torch.no_grad():
        got = controlled(x, t, context, control_context=control, control_context_scale=0.0)
        assert torch.equal(got, plain(x, t, context))


def test_control_changes_the_prediction():
    plain, controlled = _pair()
    x, t, context, control = _inputs()
    with torch.no_grad():
        got = controlled(x, t, context, control_context=control, control_context_scale=1.0)
    assert not torch.allclose(got, plain(x, t, context))


def test_a_single_control_latent_drives_a_batch():
    _, controlled = _pair()
    x, t, context, control = _inputs()
    batch_x, batch_ctx = x.expand(2, -1, -1, -1).clone(), context.expand(2, -1, -1).clone()
    with torch.no_grad():
        single = controlled(x, t, context, control_context=control)
        batched = controlled(batch_x, t.expand(2), batch_ctx, control_context=control)
    torch.testing.assert_close(batched[0:1], single, atol=1e-5, rtol=1e-5)
    torch.testing.assert_close(batched[1:2], single, atol=1e-5, rtol=1e-5)


def test_control_without_a_loaded_patch_is_refused():
    plain, _ = _pair()
    x, t, context, control = _inputs()
    with pytest.raises(ValueError, match="Fun ControlNet"):
        plain(x, t, context, control_context=control)


def test_control_of_another_size_is_refused():
    _, controlled = _pair()
    x, t, context, _ = _inputs()
    with pytest.raises(ValueError, match="latents"):
        controlled(x, t, context, control_context=torch.randn(1, CONTROL_IN, 4, 8))


@contextmanager
def _run_cache(m):
    cache = RunCache(lambda: 1)
    m.run_cache = cache
    try:
        yield cache
    finally:
        cache.clear()
        m.run_cache = None


def test_controlled_steps_bypass_the_prefix_cache():
    _, controlled = _pair()
    x, _, context, control = _inputs()
    steps = [torch.tensor([s]) for s in (0.9, 0.5, 0.1)]
    with torch.no_grad():
        expected = [controlled(x, t, context, control_context=control) for t in steps]
        with _run_cache(controlled) as cache:
            plain_first = [controlled(x, t, context) for t in steps]
            assert len(cache) == 1
            got = [controlled(x, t, context, control_context=control) for t in steps]
            assert len(cache) == 1
            plain_again = [controlled(x, t, context) for t in steps]
    for a, b in zip(expected, got):
        assert torch.equal(a, b)
    for a, b in zip(plain_first, plain_again):
        assert torch.equal(a, b)


def _patch_sd(fused: bool, blocks: int = 2, inner: int = DIM, head_dim: int = HEAD_DIM, mlp_ratio: int = 3):
    hidden = inner * mlp_ratio
    sd = {
        "control_img_in.weight": torch.randn(inner, CONTROL_IN),
        "control_img_in.bias": torch.randn(inner),
        "control_blocks.0.before_proj.weight": torch.randn(inner, inner),
        "control_blocks.0.before_proj.bias": torch.randn(inner),
    }
    for i in range(blocks):
        p = f"control_blocks.{i}."
        for name in ("to_q", "to_k", "to_v", "to_out.0"):
            sd[p + f"attn.{name}.weight"] = torch.randn(inner, inner)
        sd[p + "attn.norm_q.weight"] = torch.ones(head_dim)
        sd[p + "attn.norm_k.weight"] = torch.ones(head_dim)
        if fused:
            sd[p + "img_mlp.gate_up.weight"] = torch.randn(2 * hidden, inner)
        else:
            sd[p + "img_mlp.gate_layer.weight"] = torch.randn(hidden, inner)
            sd[p + "img_mlp.proj.weight"] = torch.randn(hidden, inner)
        sd[p + "img_mlp.out.weight"] = torch.randn(inner, hidden)
        sd[p + "after_proj.weight"] = torch.randn(inner, inner)
        sd[p + "after_proj.bias"] = torch.randn(inner)
    return sd


@pytest.mark.parametrize("fused", [False, True], ids=["official split mlp", "comfy fused mlp"])
def test_both_published_key_layouts_are_detected(fused):
    config = detect_model_patch_config(_patch_sd(fused))
    assert config == {
        "patch": QWEN_IMAGE21_FUN_CONTROL, "num_blocks": 2, "control_in_dim": CONTROL_IN,
        "inner_dim": DIM, "attention_head_dim": HEAD_DIM, "mlp_ratio": 3, "fused_mlp": fused,
    }


def test_the_int8_layout_is_detected():
    sd = _patch_sd(True)
    for key in [k for k in sd if k.endswith(".weight") and sd[k].ndim == 2 and not k.startswith("control_img_in")]:
        sd[key] = sd[key].to(torch.int8)
        sd[key.removesuffix(".weight") + ".weight_scale"] = torch.ones(sd[key].shape[0], 1)
        sd[key.removesuffix(".weight") + ".comfy_quant"] = torch.tensor(
            list(json.dumps({"format": "int8_tensorwise", "convrot": True}).encode()), dtype=torch.uint8)
    config = detect_model_patch_config(sd)
    assert config["num_blocks"] == 2 and config["fused_mlp"] is True and config["mlp_ratio"] == 3


def test_a_qwen_image_20_fun_control_is_not_detected():
    sd = _patch_sd(False)
    sd["control_blocks.0.img_mod.1.weight"] = torch.randn(6 * DIM, DIM)
    assert detect_model_patch_config(sd) is None


def test_a_diffusion_model_is_not_detected_as_a_patch():
    with torch.device("meta"):
        m = QwenImage21DiT.from_config(CONFIG, pick_operations(torch.float32, torch.float32))
    assert detect_model_patch_config(m.state_dict()) is None


def test_split_and_fused_layouts_load_to_the_same_weights():
    split = _patch_sd(False)
    fused = convert_fun_control_state_dict(split)
    assert "control_blocks.1.img_mlp.gate_layer.weight" not in fused
    assert torch.equal(
        fused["control_blocks.1.img_mlp.gate_up.weight"],
        torch.cat([split["control_blocks.1.img_mlp.gate_layer.weight"],
                   split["control_blocks.1.img_mlp.proj.weight"]], dim=0),
    )


@pytest.mark.parametrize("change,message", [
    ({"inner": 32, "head_dim": 8}, "wide"),
    ({"blocks": 3}, "cannot pair"),
])
def test_a_mismatched_patch_is_refused(change, message):
    with pytest.raises(NativeEngineUnsupportedError, match=message):
        attach_fun_control(dict(CONFIG), {}, _patch_sd(True, **change))


def test_a_file_that_is_not_a_fun_control_patch_is_refused():
    with pytest.raises(NativeEngineUnsupportedError, match="not a Qwen-Image-2.1 Fun ControlNet"):
        attach_fun_control(dict(CONFIG), {}, {"something.weight": torch.zeros(2)})


def test_the_loader_builds_the_diffusion_model_with_its_patch(tmp_path):
    torch.manual_seed(7)
    detectable = {**CONFIG, "inner_dim": 128, "num_attention_heads": 1, "attention_head_dim": 128,
                  "axes_dims_rope": (16, 56, 56), "num_layers": 2}
    with torch.device("meta"):
        meta = QwenImage21DiT.from_config(
            {**detectable, "fun_control": {"num_blocks": 2, "control_in_dim": CONTROL_IN, "mlp_ratio": 3}},
            pick_operations(torch.float32, torch.float32))
    sd = {k: torch.randn(v.shape) * 0.05 for k, v in meta.state_dict().items()}
    base = {k: v for k, v in sd.items() if not k.startswith("fun_control.")}
    patch = {k.removeprefix("fun_control."): v for k, v in sd.items() if k.startswith("fun_control.")}
    save_file(base, str(tmp_path / "dit.safetensors"))
    save_file(patch, str(tmp_path / "patch.safetensors"))
    loader = NativeEngineLoader(device="cpu", fp8_quantize="off")

    plain = loader.load(tmp_path / "dit.safetensors", "diffusion_model")
    patched = loader.load(tmp_path / "dit.safetensors", "diffusion_model",
                          model_patch=tmp_path / "patch.safetensors")

    assert plain.module.fun_control is None
    assert len(patched.module.fun_control.control_blocks) == 2
    assert torch.equal(patched.module.fun_control.control_img_in.weight, patch["control_img_in.weight"])
    assert patched.estimated_vram_gb > plain.estimated_vram_gb


def test_a_family_without_patches_refuses_one(tmp_path):
    spec = match_model_spec(CONFIG)
    other = dataclasses.replace(spec, model_patch_map=None)
    with pytest.raises(NativeEngineUnsupportedError, match="takes no model patch"):
        NativeEngineLoader._attach_model_patch(other, dict(CONFIG), {}, tmp_path / "patch.safetensors")


def test_zero_strength_keeps_the_prefix_cache():
    _, controlled = _pair()
    x, _, context, control = _inputs()
    with torch.no_grad(), _run_cache(controlled) as cache:
        controlled(x, torch.tensor([0.9]), context, control_context=control, control_context_scale=0.0)
        assert len(cache) == 1


def _at(model, x, sigma, context, **kwargs):
    with torch.no_grad():
        return model(x, torch.tensor([sigma]), context, **kwargs)


def test_control_applies_inside_its_sigma_range_and_not_outside():
    plain, controlled = _pair()
    x, _, context, control = _inputs()
    window = {"control_context": control, "control_sigma_range": (0.5, 0.25)}
    for outside in (0.9, 0.1):
        assert torch.equal(_at(controlled, x, outside, context, **window), _at(plain, x, outside, context))
    for inside in (0.5, 0.4, 0.25):
        assert torch.equal(_at(controlled, x, inside, context, **window),
                           _at(controlled, x, inside, context, control_context=control))


@pytest.mark.parametrize("sigma", [1.0, 0.7, 0.2, 0.0])
def test_the_full_range_is_the_same_as_no_range(sigma):
    _, controlled = _pair()
    x, _, context, control = _inputs()
    assert torch.equal(_at(controlled, x, sigma, context, control_context=control, control_sigma_range=(1.0, 0.0)),
                       _at(controlled, x, sigma, context, control_context=control))


@pytest.mark.parametrize("sigma", [1.0, 0.5, 0.0])
def test_an_empty_range_is_the_same_as_no_control(sigma):
    plain, controlled = _pair()
    x, _, context, control = _inputs()
    assert torch.equal(_at(controlled, x, sigma, context, control_context=control, control_sigma_range=(0.3, 0.6)),
                       _at(plain, x, sigma, context))


def test_steps_outside_the_range_use_the_prefix_cache():
    _, controlled = _pair()
    x, _, context, control = _inputs()
    with _run_cache(controlled) as cache:
        _at(controlled, x, 0.4, context, control_context=control, control_sigma_range=(1.0, 0.5))
        assert len(cache) == 1
