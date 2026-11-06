from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.arch.krea2.model import Krea2
from src.platform.runtime.native.arch.ltx.model import CrossAttention, LTXAVModel
from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Attention, MiniMaxH3Model
from src.platform.runtime.native.arch.moss_image.config import MossImageConfig
from src.platform.runtime.native.arch.moss_image.layers import Block as MossBlock, TextBlock as MossTextBlock, rope_bands, rope_table
from src.platform.runtime.native.arch.moss_image.model import MossImage
from src.platform.runtime.native.arch.seedvr2.config import SeedVR2Config
from src.platform.runtime.native.arch.seedvr2.model import SeedVR2
from src.platform.runtime.native.arch.z_image.model import ZImageDiT
from src.platform.runtime.native.kernels import capabilities, fused_elementwise, pick_operations
from src.platform.settings.runtime_flags import runtime_flag_values
from vendor.gpl.comfyui.ltx.rope import interleaved_freqs_cis, split_freqs_cis

from .test_flux_model import TINY_FLUX2
from .test_krea2_model import TINY as KREA2_TINY
from .test_ltx_forward import TINY_19B as LTX_TINY_19B, TINY_23 as LTX_TINY_23, _av_inputs
from .test_moss_image_parity import CONFIG as MOSS_TINY, _inputs as _moss_inputs
from .test_minimax_h3_model import TINY_FULL as H3_TINY, _fbcache_forward, _fbcache_inputs, _tiny_layout
from .test_seedvr2_model import TINY_3B as SEEDVR2_TINY
from .test_z_image_model import ZTINY

pytestmark = pytest.mark.requires_gpu

RELATIVE_TOLERANCE = 2.0 ** -6


@pytest.fixture(autouse=True)
def _gpu(monkeypatch):
    caps = capabilities()
    if not torch.cuda.is_available() or not caps.triton or not caps.device(0).at_least(8, 0):
        pytest.skip("needs a CUDA GPU of compute capability 8.0+ with Triton")
    fused_elementwise.reset_fused_elementwise_state()
    yield
    assert fused_elementwise.fused_elementwise_disabled_reason() is None


def _random(model, seed):
    g = torch.Generator().manual_seed(seed)
    return {
        k: (torch.randn(v.shape, generator=g) * 0.1).to(v.dtype) if v.is_floating_point() else v.clone()
        for k, v in model.state_dict().items()
    }


def _sharp_qk(state, seed, center=4.0):
    g = torch.Generator().manual_seed(seed)
    for k, v in state.items():
        if k.endswith(("q_norm.weight", "k_norm.weight")) or ".norm_q." in k or ".norm_k." in k:
            state[k] = (torch.randn(v.shape, generator=g) * center / 4 + center).to(v.dtype)
    return state


def _build(cls, config, seed):
    model = cls.from_config(config, pick_operations(torch.float32, torch.bfloat16))
    model.load_state_dict(_random(model, seed))
    return model.eval().to("cuda")


def _on_and_off(monkeypatch, run):
    routed = []
    real = fused_elementwise._run_fused

    def counting(name, *args):
        routed.append(name)
        return real(name, *args)

    monkeypatch.setattr(fused_elementwise, "_run_fused", counting)
    outs = {}
    for flag in (False, True):
        monkeypatch.setitem(runtime_flag_values(), "native_fused_elementwise", flag)
        torch.manual_seed(11)
        with torch.inference_mode():
            outs[flag] = run().float()
    assert routed, "the flag-on forward never took the Triton path"
    return outs[True], outs[False], routed


def _assert_close_to_unfused(fused, unfused):
    assert torch.isfinite(fused).all()
    error = (fused - unfused).abs().max()
    assert error <= RELATIVE_TOLERANCE * unfused.abs().max(), (float(error), float(unfused.abs().max()))


def test_a_flux2_forward_with_fused_kernels_matches_the_unfused_one(monkeypatch):
    model = _build(Flux, TINY_FLUX2, 1)
    x = torch.randn(1, 16, 16, 16, device="cuda").to(torch.bfloat16)
    ctx = torch.randn(1, 7, 32, device="cuda").to(torch.bfloat16)
    fused, unfused, routed = _on_and_off(monkeypatch, lambda: model(x, torch.tensor([0.5], device="cuda"), ctx))
    assert {"fused_norm_modulate", "fused_gated_residual", "fused_act_gate", "fused_qk_norm_rope"} <= set(routed)
    _assert_close_to_unfused(fused, unfused)


def test_a_z_image_forward_with_fused_kernels_matches_the_unfused_one(monkeypatch):
    model = _build(ZImageDiT, ZTINY, 2)
    x = torch.randn(2, 16, 8, 12, device="cuda").to(torch.bfloat16)
    ctx = torch.randn(2, 7, ZTINY["cap_feat_dim"], device="cuda").to(torch.bfloat16)
    t = torch.full((2,), 0.5, device="cuda")
    fused, unfused, routed = _on_and_off(monkeypatch, lambda: model(x, t, ctx))
    assert {"fused_norm_modulate", "fused_gated_residual", "fused_act_gate", "fused_qk_norm_rope"} <= set(routed)
    _assert_close_to_unfused(fused, unfused)


def test_a_krea2_reference_forward_with_fused_spans_matches_the_unfused_one(monkeypatch):
    model = _build(Krea2, KREA2_TINY, 3)
    x = torch.randn(1, 4, 8, 8, device="cuda").to(torch.bfloat16)
    te = torch.randn(1, 5, 3, KREA2_TINY["txtdim"], device="cuda").to(torch.bfloat16)
    ref = torch.randn(1, 4, 8, 8, device="cuda").to(torch.bfloat16)
    t = torch.tensor([0.5], device="cuda")
    fused, unfused, routed = _on_and_off(
        monkeypatch, lambda: model(x, t, te, ref_latents=ref, ref_timestep_zero=True),
    )
    assert {"fused_norm_modulate", "fused_gated_residual", "fused_act_gate", "fused_qk_norm_rope"} <= set(routed)
    _assert_close_to_unfused(fused, unfused)


def _rope_layouts(monkeypatch):
    seen = set()
    real = fused_elementwise._run_fused

    def recording(name, *args):
        if name == "fused_qk_norm_rope":
            seen.add(fused_elementwise.ROPE_LAYOUTS[args[-1]])
        return real(name, *args)

    monkeypatch.setattr(fused_elementwise, "_run_fused", recording)
    return seen


@pytest.mark.parametrize("seq_chunk_rows", [0, 6])
def test_a_minimax_h3_forward_with_fused_partial_rope_matches_the_unfused_one(monkeypatch, seq_chunk_rows):
    model = MiniMaxH3Model(MiniMaxH3Config.from_detect_config(H3_TINY), pick_operations(torch.bfloat16, torch.bfloat16),
                           dtype=torch.bfloat16)
    model.load_state_dict(_sharp_qk(_random(model, 4), 4))
    model = model.eval().to("cuda")
    model.post_load()
    layout = {k: v.to("cuda") for k, v in _tiny_layout(text_n=2, video_n=13, audio_n=7).items()}
    layout["position_ids"] = layout["position_ids"] * 16
    torch.manual_seed(5)
    inputs = {k: v.to("cuda", torch.bfloat16) for k, v in _fbcache_inputs(H3_TINY, layout).items()}
    ts = torch.tensor([0.2, 0.9], device="cuda")

    def run():
        outs = _fbcache_forward(model, layout, inputs, ts, seq_chunk_rows=seq_chunk_rows)
        return torch.cat([o.flatten() for o in outs])

    layouts = _rope_layouts(monkeypatch)
    fused, unfused, routed = _on_and_off(monkeypatch, run)
    assert layouts == {"half_rotate"}
    _assert_close_to_unfused(fused, unfused)


@pytest.mark.parametrize("config,layout", [
    (LTX_TINY_23, "half_cos_sin"), (dict(LTX_TINY_19B, rope_type="interleaved"), "pair_rotate"),
], ids=["split", "interleaved"])
def test_an_ltx_forward_with_fused_full_width_qk_norm_rope_matches_the_unfused_one(monkeypatch, config, layout):
    model = LTXAVModel.from_config(config, pick_operations(torch.float32, torch.bfloat16))
    model.load_state_dict(_sharp_qk(_random(model, 5), 5))
    model = model.eval().to("cuda")
    torch.manual_seed(6)
    vx, ax, context, timestep = (t.to("cuda") for t in _av_inputs(config))
    vx, ax, context = vx.to(torch.bfloat16), ax.to(torch.bfloat16), context.to(torch.bfloat16)

    def run():
        return torch.cat([o.flatten() for o in model.forward([vx, ax], timestep, context)])

    layouts = _rope_layouts(monkeypatch)
    fused, unfused, routed = _on_and_off(monkeypatch, run)
    assert layouts == {layout}
    _assert_close_to_unfused(fused, unfused)


def test_a_seedvr2_windowed_forward_with_fused_fp32_rope_matches_the_unfused_one(monkeypatch):
    model = SeedVR2(SeedVR2Config(**dict(SEEDVR2_TINY, window=(1, 2, 2))),
                    pick_operations(torch.float32, torch.bfloat16))
    state = _sharp_qk(_random(model, 7), 7)
    state.update({k: v for k, v in model.state_dict().items() if k.endswith("rope.rope.freqs")})
    model.load_state_dict(state)
    model = model.eval().to("cuda")
    vid = torch.randn(1, 8, 2, 8, 8, device="cuda").to(torch.bfloat16)
    txt = torch.randn(5, 16, device="cuda").to(torch.bfloat16)
    layouts = _rope_layouts(monkeypatch)
    fused, unfused, routed = _on_and_off(monkeypatch, lambda: model(vid, torch.tensor(0.5, device="cuda"), txt))
    assert layouts == {"pair_rotate"}
    _assert_close_to_unfused(fused, unfused)


@pytest.mark.parametrize("seq_chunk_rows", [0, 37])
def test_a_minimax_h3_attention_with_fused_partial_rope_matches_the_unfused_one(monkeypatch, seq_chunk_rows):
    attn = MiniMaxH3Attention(256, 4, 64, 1e-6, pick_operations(torch.bfloat16, torch.bfloat16), dtype=torch.bfloat16)
    attn.load_state_dict(_sharp_qk(_random(attn, 9), 9, center=1.0))
    attn = attn.eval().to("cuda")
    angle = torch.cat((torch.rand(150, 24, device="cuda") * 6.0,) * 2, -1)
    rotary = (angle.cos().to(torch.bfloat16), angle.sin().to(torch.bfloat16))
    x = torch.randn(1, 150, 256, device="cuda").to(torch.bfloat16)
    layouts = _rope_layouts(monkeypatch)
    fused, unfused, _ = _on_and_off(monkeypatch, lambda: attn(x, rotary, seq_chunk_rows=seq_chunk_rows))
    assert layouts == {"half_rotate"}
    _assert_close_to_unfused(fused, unfused)


@pytest.mark.parametrize("split", [True, False], ids=["split", "interleaved"])
def test_an_ltx_attention_with_fused_full_width_qk_norm_rope_matches_the_unfused_one(monkeypatch, split):
    heads, dim_head = 4, 64
    attn = CrossAttention(256, heads, dim_head, pick_operations(torch.float32, torch.bfloat16))
    attn.load_state_dict(_sharp_qk(_random(attn, 8), 8, center=1.0))
    attn = attn.eval().to("cuda")
    angle = torch.rand(1, 96, heads * dim_head // 2, device="cuda") * 6.0
    tables = split_freqs_cis(angle, 0, heads) if split else interleaved_freqs_cis(angle, 0)
    pe = (*(t.to(torch.bfloat16) for t in tables), split)
    x = torch.randn(1, 96, 256, device="cuda").to(torch.bfloat16)
    layouts = _rope_layouts(monkeypatch)
    fused, unfused, _ = _on_and_off(monkeypatch, lambda: attn(x, pe=pe))
    assert layouts == {"half_cos_sin" if split else "pair_rotate"}
    _assert_close_to_unfused(fused, unfused)


def _moss_weights(module, seed, scale):
    g = torch.Generator().manual_seed(seed)
    return {
        k: (torch.randn(v.shape, generator=g) * (scale if v.dim() > 1 else 0.1) + (1.0 if v.dim() == 1 else 0.0)).to(v.dtype)
        for k, v in module.state_dict().items()
    }


def _act_modes(monkeypatch):
    modes = set()
    real = fused_elementwise._run_fused

    def recording(name, *args):
        if name == "fused_act_gate":
            modes.add(fused_elementwise.ACT_MODES[args[-1]])
        return real(name, *args)

    monkeypatch.setattr(fused_elementwise, "_run_fused", recording)
    return modes


@pytest.mark.parametrize("text", [False, True], ids=["image block", "text block"])
def test_a_moss_image_block_ffn_with_fused_situ_matches_the_unfused_one(monkeypatch, text):
    dim, ffn, heads, height, width, text_len = 256, 768, 4, 12, 10, 16
    ops = pick_operations(torch.bfloat16, torch.bfloat16)
    cls = MossTextBlock if text else MossBlock
    block = cls(dim, ffn, heads, ops, dtype=torch.bfloat16)
    block.load_state_dict(_moss_weights(block, 14, 0.06))
    block = block.eval().to("cuda")
    tokens = text_len if text else height * width + text_len
    x = torch.randn(2, tokens, dim, device="cuda").to(torch.bfloat16)
    rope = rope_table(height, width, text_len, rope_bands(dim // heads, 10000.0), "cuda").to(torch.bfloat16)
    if text:
        rope = rope[:, -text_len:]
        run = lambda: block(x, rope)
    else:
        modulation = (torch.randn(2, 6, dim, device="cuda") * 0.3).to(torch.bfloat16)
        run = lambda: block(x, rope, modulation)
    modes = _act_modes(monkeypatch)
    fused, unfused, _ = _on_and_off(monkeypatch, run)
    assert modes == {"situ_split" if text else "situ"}
    _assert_close_to_unfused(fused - x.float(), unfused - x.float())


def test_a_moss_image_forward_with_fused_situ_matches_the_unfused_one(monkeypatch):
    model = MossImage(MossImageConfig.from_detect_config(MOSS_TINY), pick_operations(torch.bfloat16, torch.bfloat16),
                      dtype=torch.bfloat16)
    model.load_state_dict(_moss_weights(model, 12, 0.05))
    model = model.eval().to("cuda")
    x, context, _ = (t.to("cuda") for t in _moss_inputs(13, 6, 5, [7, 3]))
    sigma = torch.full((2,), 0.37, device="cuda")
    modes = _act_modes(monkeypatch)
    fused, unfused, routed = _on_and_off(
        monkeypatch, lambda: model(x.to(torch.bfloat16), sigma, context.to(torch.bfloat16)),
    )
    assert modes == {"situ", "situ_split"}
    _assert_close_to_unfused(fused, unfused)
