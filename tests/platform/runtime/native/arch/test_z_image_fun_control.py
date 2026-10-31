from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.z_image.control import (
    attach_fun_control,
    convert_fun_control_state_dict,
)
from src.platform.runtime.native.arch.z_image.model import ZImageDiT
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.patch_detect import (
    QWEN_IMAGE21_FUN_CONTROL,
    ZIMAGE_FUN_CONTROL,
    detect_model_patch_config,
)
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.kernels import pick_operations

DIM, HEADS, HEAD_DIM, FF, CAP_DIM = 256, 4, 64, 128, 8
IN_CH = 16
N_LAYERS, N_REFINER, CONTROL_LAYERS = 4, 2, 2
CONTROL_IN = 2 * IN_CH + 1
CONFIG = {
    "image_model": "lumina2", "z_image_modulation": True, "in_channels": IN_CH, "dim": DIM,
    "cap_feat_dim": CAP_DIM, "n_layers": N_LAYERS, "n_refiner_layers": N_REFINER, "n_heads": HEADS,
    "n_kv_heads": HEADS, "intermediate_size": FF, "axes_dims": (32, 16, 16), "patch_size": 2,
}


def _randomised(module: torch.nn.Module) -> dict[str, torch.Tensor]:
    sd = {}
    for k, v in module.state_dict().items():
        if "norm" in k and k.endswith(".weight"):
            sd[k] = torch.ones_like(v)
        else:
            sd[k] = torch.randn_like(v) * 0.05
    return sd


def _base_state_dict() -> dict[str, torch.Tensor]:
    torch.manual_seed(0)
    plain = ZImageDiT.from_config(CONFIG, pick_operations(torch.float32, torch.float32))
    return _randomised(plain)


def _block(first: bool, refiner_hint: bool = True, fused: bool = False) -> dict[str, torch.Tensor]:
    def w(*shape):
        return torch.randn(*shape) * 0.05

    sd = {
        "adaLN_modulation.0.weight": w(4 * DIM, 256),
        "adaLN_modulation.0.bias": w(4 * DIM),
        "attention_norm1.weight": torch.ones(DIM),
        "attention_norm2.weight": torch.ones(DIM),
        "ffn_norm1.weight": torch.ones(DIM),
        "ffn_norm2.weight": torch.ones(DIM),
        "feed_forward.w1.weight": w(FF, DIM),
        "feed_forward.w2.weight": w(DIM, FF),
        "feed_forward.w3.weight": w(FF, DIM),
    }
    if fused:
        sd["attention.qkv.weight"] = w(3 * DIM, DIM)
        sd["attention.out.weight"] = w(DIM, DIM)
        sd["attention.q_norm.weight"] = torch.ones(HEAD_DIM)
        sd["attention.k_norm.weight"] = torch.ones(HEAD_DIM)
    else:
        sd["attention.to_q.weight"] = w(DIM, DIM)
        sd["attention.to_k.weight"] = w(DIM, DIM)
        sd["attention.to_v.weight"] = w(DIM, DIM)
        sd["attention.to_out.0.weight"] = w(DIM, DIM)
        sd["attention.norm_q.weight"] = torch.ones(HEAD_DIM)
        sd["attention.norm_k.weight"] = torch.ones(HEAD_DIM)
    sd["after_proj.weight"] = w(DIM, DIM) if refiner_hint else torch.zeros(DIM, DIM)
    sd["after_proj.bias"] = w(DIM) if refiner_hint else torch.zeros(DIM)
    if first:
        sd["before_proj.weight"] = w(DIM, DIM) if refiner_hint else torch.zeros(DIM, DIM)
        sd["before_proj.bias"] = w(DIM) if refiner_hint else torch.zeros(DIM)
    return sd


def _patch_sd(layers=CONTROL_LAYERS, refiner=N_REFINER, embedder_in=4 * CONTROL_IN, dim=DIM,
              refiner_trained=True, fused=False, refiner_after_proj=True) -> dict[str, torch.Tensor]:
    torch.manual_seed(1)
    sd = {
        "control_all_x_embedder.2-1.weight": torch.randn(dim, embedder_in) * 0.05,
        "control_all_x_embedder.2-1.bias": torch.randn(dim) * 0.05,
    }
    for i in range(layers):
        for k, v in _block(i == 0, fused=fused).items():
            sd[f"control_layers.{i}.{k}"] = v
    for i in range(refiner):
        for k, v in _block(i == 0, refiner_hint=refiner_trained, fused=fused).items():
            if not refiner_after_proj and k.startswith("after_proj"):
                continue
            sd[f"control_noise_refiner.{i}.{k}"] = v
    return sd


def _build(patch=None, mutate=None):
    base = _base_state_dict()
    if patch is None:
        config, merged = dict(CONFIG), base
    else:
        config, merged = attach_fun_control(dict(CONFIG), base, patch)
    if mutate is not None:
        mutate(merged)
    module = ZImageDiT.from_config(config, pick_operations(torch.float32, torch.float32))
    load_into_module(module, merged, match_model_spec({**config}))
    return module.eval()


def _inputs(h=8, w=8, seed=3):
    g = torch.Generator().manual_seed(seed)
    x = torch.randn(1, IN_CH, h, w, generator=g)
    context = torch.randn(1, 5, CAP_DIM, generator=g)
    control = torch.randn(1, CONTROL_IN, h, w, generator=g)
    return x, torch.tensor([0.5]), context, control


def _run(module, x, t, context, **kwargs):
    with torch.no_grad():
        return module(x, t, context, **kwargs)


def test_detect_reads_the_patch_layout():
    config = detect_model_patch_config(_patch_sd())
    assert config["patch"] == ZIMAGE_FUN_CONTROL
    assert config["num_blocks"] == CONTROL_LAYERS
    assert config["num_refiner_blocks"] == N_REFINER
    assert config["embedder_in"] == 4 * CONTROL_IN
    assert config["inner_dim"] == DIM
    assert config["refiner_controlled"] and config["refiner_trained"]
    assert not config["fused_qkv"]


def test_detect_leaves_other_patches_alone():
    assert detect_model_patch_config({"something.weight": torch.zeros(2)}) is None
    assert detect_model_patch_config(_patch_sd())["patch"] != QWEN_IMAGE21_FUN_CONTROL


def test_convert_fuses_qkv_and_renames_to_the_native_layout():
    patch = _patch_sd()
    out = convert_fun_control_state_dict(patch)
    expected = torch.cat([
        patch["control_layers.1.attention.to_q.weight"],
        patch["control_layers.1.attention.to_k.weight"],
        patch["control_layers.1.attention.to_v.weight"],
    ], dim=0)
    assert torch.equal(out["layers.1.attention.qkv.weight"], expected)
    assert torch.equal(out["layers.0.attention.out.weight"], patch["control_layers.0.attention.to_out.0.weight"])
    assert "noise_refiner.1.attention.q_norm.weight" in out
    assert "x_embedder.weight" in out and "x_embedder.bias" in out
    assert not [k for k in out if k.startswith(("control_", "layers.0.attention.to_"))]


def test_attach_prefixes_the_patch_and_records_its_shape():
    config, merged = attach_fun_control(dict(CONFIG), {"base.weight": torch.zeros(1)}, _patch_sd())
    assert config["fun_control"] == {"num_blocks": CONTROL_LAYERS, "control_in_dim": CONTROL_IN}
    assert "base.weight" in merged
    assert "fun_control.x_embedder.weight" in merged
    assert "fun_control.noise_refiner.0.before_proj.weight" in merged
    assert "fun_control.layers.1.attention.qkv.weight" in merged


def test_a_natively_fused_patch_loads_too():
    module = _build(_patch_sd(fused=True))
    assert len(module.fun_control.layers) == CONTROL_LAYERS


def test_the_patch_loads_into_the_model_without_leftover_keys():
    module = _build(_patch_sd())
    assert len(module.fun_control.layers) == CONTROL_LAYERS
    assert len(module.fun_control.noise_refiner) == N_REFINER
    assert module.fun_control.control_in_dim == CONTROL_IN


@pytest.mark.parametrize("num_layers,expected", [(1, [0]), (2, [0, 2]), (4, [0, 1, 2, 3])])
def test_injection_layers_spread_evenly(num_layers, expected):
    module = _build(_patch_sd(layers=num_layers))
    assert module.fun_control.injection_layers(N_LAYERS) == expected


def test_injection_layers_match_the_released_union_layouts():
    module = _build(_patch_sd())
    control = module.fun_control
    full = type("Stub", (), {"layers": [0] * 15})
    lite = type("Stub", (), {"layers": [0] * 3})
    assert type(control).injection_layers(full, 30) == list(range(0, 30, 2))
    assert type(control).injection_layers(lite, 30) == [0, 10, 20]


def test_scale_zero_is_bit_identical_to_no_control():
    plain = _build()
    controlled = _build(_patch_sd())
    x, t, context, control = _inputs()
    reference = _run(plain, x, t, context)
    assert torch.equal(_run(controlled, x, t, context), reference)
    assert torch.equal(_run(controlled, x, t, context, control_context=control, control_context_scale=0.0), reference)
    assert torch.equal(_run(controlled, x, t, context, control_context=None, control_context_scale=1.0), reference)


def test_a_sigma_outside_the_range_is_bit_identical_to_no_control():
    plain = _build()
    controlled = _build(_patch_sd())
    x, t, context, control = _inputs()
    reference = _run(plain, x, t, context)
    out = _run(controlled, x, t, context, control_context=control, control_context_scale=1.0,
               control_sigma_range=(0.4, 0.1))
    assert torch.equal(out, reference)
    inside = _run(controlled, x, t, context, control_context=control, control_context_scale=1.0,
                  control_sigma_range=(1.0, 0.0))
    assert not torch.equal(inside, reference)


def test_control_changes_the_output_and_scales_with_strength():
    controlled = _build(_patch_sd())
    x, t, context, control = _inputs()
    reference = _run(controlled, x, t, context)
    full = _run(controlled, x, t, context, control_context=control, control_context_scale=1.0)
    half = _run(controlled, x, t, context, control_context=control, control_context_scale=0.5)
    assert torch.isfinite(full).all()
    assert not torch.equal(full, reference)
    assert (half - reference).abs().mean() < (full - reference).abs().mean()


def _silence_refiner(merged):
    for key in list(merged):
        if key.startswith("fun_control.noise_refiner.") and "after_proj" in key:
            merged[key] = torch.zeros_like(merged[key])


def _silence_layers(merged):
    for key in list(merged):
        if key.startswith("fun_control.layers.") and "after_proj" in key:
            merged[key] = torch.zeros_like(merged[key])


def test_the_refiner_hints_scale_with_the_strength():
    controlled = _build(_patch_sd(), mutate=_silence_layers)
    x, t, context, control = _inputs()
    reference = _run(controlled, x, t, context)
    full = _run(controlled, x, t, context, control_context=control, control_context_scale=1.0)
    faint = _run(controlled, x, t, context, control_context=control, control_context_scale=0.01)
    assert (full - reference).abs().mean() > 0
    assert (faint - reference).abs().mean() < 0.1 * (full - reference).abs().mean()


def test_the_joint_stack_hints_scale_with_the_strength():
    controlled = _build(_patch_sd(), mutate=_silence_refiner)
    x, t, context, control = _inputs()
    reference = _run(controlled, x, t, context)
    full = _run(controlled, x, t, context, control_context=control, control_context_scale=1.0)
    faint = _run(controlled, x, t, context, control_context=control, control_context_scale=0.01)
    assert (full - reference).abs().mean() > 0
    assert (faint - reference).abs().mean() < 0.1 * (full - reference).abs().mean()


def test_the_control_stream_carries_the_caption_then_the_refined_control_rows():
    controlled = _build(_patch_sd())
    x, t, context, control = _inputs()
    captured = {}

    def keep(label):
        def hook(_module, _args, output):
            captured[label] = output.detach().clone()
        return hook

    controlled.context_refiner[-1].register_forward_hook(keep("cap"))
    controlled.fun_control.noise_refiner[-1].register_forward_hook(keep("control"))
    controlled.noise_refiner[-1].register_forward_hook(keep("img"))
    original = controlled.fun_control.hints

    def spy(c, joint, rope, adaln_input):
        captured["c"], captured["joint"] = c.detach().clone(), joint.detach().clone()
        return original(c, joint, rope, adaln_input)

    controlled.fun_control.hints = spy
    _run(controlled, x, t, context, control_context=control)

    cap_len = captured["cap"].shape[1]
    assert torch.equal(captured["c"][:, :cap_len], captured["cap"])
    assert torch.equal(captured["c"][:, cap_len:], captured["control"])
    assert torch.equal(captured["joint"][:, :cap_len], captured["cap"])
    with torch.no_grad():
        last_hint = controlled.fun_control.noise_refiner[-1].after_proj(captured["control"])
    torch.testing.assert_close(captured["joint"][:, cap_len:], captured["img"] + last_hint)


def test_the_control_image_steers_the_output():
    controlled = _build(_patch_sd())
    x, t, context, control = _inputs()
    first = _run(controlled, x, t, context, control_context=control)
    second = _run(controlled, x, t, context, control_context=torch.randn_like(control))
    assert not torch.equal(first, second)


def test_hints_reach_the_caption_rows_as_well_as_the_image_rows():
    controlled = _build(_patch_sd(), mutate=_silence_refiner)
    plain = _build()
    x, t, context, control = _inputs()
    seen = {}

    def grab(label):
        def hook(_module, args):
            seen[label] = args[0].detach().clone()
        return hook

    controlled.layers[1].register_forward_pre_hook(grab("controlled"))
    plain.layers[1].register_forward_pre_hook(grab("plain"))
    _run(controlled, x, t, context, control_context=control)
    _run(plain, x, t, context)

    cap_len = context.shape[1] + (-context.shape[1]) % 32
    assert not torch.equal(seen["controlled"][:, :cap_len], seen["plain"][:, :cap_len])
    assert not torch.equal(seen["controlled"][:, cap_len:], seen["plain"][:, cap_len:])


def test_the_hint_added_after_a_layer_is_the_control_block_output_over_the_whole_sequence():
    controlled = _build(_patch_sd(), mutate=_silence_refiner)
    x, t, context, control = _inputs()
    captured = {}
    original = controlled.fun_control.hints

    def spy(c, joint, rope, adaln_input):
        for hint in original(c, joint, rope, adaln_input):
            captured.setdefault("hints", []).append(hint.detach().clone())
            yield hint

    controlled.fun_control.hints = spy
    outputs = []
    controlled.layers[0].register_forward_hook(lambda _m, _a, out: outputs.append(out.detach().clone()))

    def keep_next(_module, args):
        captured["next"] = args[0].detach().clone()

    controlled.layers[1].register_forward_pre_hook(keep_next)
    _run(controlled, x, t, context, control_context=control)
    torch.testing.assert_close(captured["next"], outputs[0] + captured["hints"][0])
    assert captured["hints"][0].shape == outputs[0].shape


def test_refiner_hints_land_on_the_image_stream_before_the_joint_stack():
    controlled = _build(_patch_sd())
    plain = _build()
    x, t, context, control = _inputs()
    seen = {}
    def grab(label):
        def hook(_module, args):
            seen[label] = args[0].detach().clone()
        return hook

    controlled.layers[0].register_forward_pre_hook(grab("controlled"))
    plain.layers[0].register_forward_pre_hook(grab("plain"))
    _run(controlled, x, t, context, control_context=control)
    _run(plain, x, t, context)
    cap_len = context.shape[1] + (-context.shape[1]) % 32
    assert torch.equal(seen["controlled"][:, :cap_len], seen["plain"][:, :cap_len])
    assert not torch.equal(seen["controlled"][:, cap_len:], seen["plain"][:, cap_len:])


def test_control_works_on_an_unpadded_odd_latent():
    controlled = _build(_patch_sd())
    x, t, context, control = _inputs(h=6, w=10)
    out = _run(controlled, x, t, context, control_context=control)
    assert out.shape == x.shape and torch.isfinite(out).all()


def test_a_control_context_for_the_wrong_latent_size_is_refused():
    controlled = _build(_patch_sd())
    x, t, context, _ = _inputs()
    with pytest.raises(ValueError, match="latents"):
        _run(controlled, x, t, context, control_context=torch.zeros(1, CONTROL_IN, 4, 4))


def test_a_control_context_with_the_wrong_channels_is_refused():
    controlled = _build(_patch_sd())
    x, t, context, _ = _inputs()
    with pytest.raises(ValueError, match="channels"):
        _run(controlled, x, t, context, control_context=torch.zeros(1, 16, 8, 8))


def test_control_without_a_loaded_patch_is_refused():
    plain = _build()
    x, t, context, control = _inputs()
    with pytest.raises(ValueError, match="Fun ControlNet"):
        _run(plain, x, t, context, control_context=control)


def test_union_1_0_is_refused():
    with pytest.raises(NativeEngineUnsupportedError, match="Union 1.0"):
        attach_fun_control(dict(CONFIG), {}, _patch_sd(embedder_in=4 * IN_CH, refiner_after_proj=False))


def test_union_2_0_with_untrained_refiner_blocks_is_refused():
    with pytest.raises(NativeEngineUnsupportedError, match="Union 2.0"):
        attach_fun_control(dict(CONFIG), {}, _patch_sd(refiner_trained=False))


def test_a_patch_without_refiner_control_is_refused():
    with pytest.raises(NativeEngineUnsupportedError, match="noise-refiner"):
        attach_fun_control(dict(CONFIG), {}, _patch_sd(refiner=0))


def test_a_refiner_count_that_differs_from_the_model_is_refused():
    with pytest.raises(NativeEngineUnsupportedError, match="noise-refiner"):
        attach_fun_control(dict(CONFIG), {}, _patch_sd(refiner=1))


def test_a_patch_of_another_width_is_refused():
    with pytest.raises(NativeEngineUnsupportedError, match="wide"):
        attach_fun_control(dict(CONFIG, dim=512), {}, _patch_sd())


def test_a_layer_count_that_does_not_divide_the_model_is_refused():
    with pytest.raises(NativeEngineUnsupportedError, match="cannot pair"):
        attach_fun_control(dict(CONFIG), {}, _patch_sd(layers=3))


def test_a_file_that_is_not_a_fun_control_patch_is_refused():
    with pytest.raises(NativeEngineUnsupportedError, match="not a Z-Image"):
        attach_fun_control(dict(CONFIG), {}, {"something.weight": torch.zeros(2)})


def test_the_qwen_patch_loader_refuses_a_z_image_patch():
    from src.platform.runtime.native.arch.qwen_image21.fun_control import attach_fun_control as attach_qwen

    with pytest.raises(NativeEngineUnsupportedError):
        attach_qwen({"inner_dim": DIM, "attention_head_dim": HEAD_DIM, "num_layers": N_LAYERS}, {}, _patch_sd())


def test_the_z_image_spec_accepts_a_model_patch():
    assert match_model_spec(dict(CONFIG)).resolve_model_patch_map() is attach_fun_control
