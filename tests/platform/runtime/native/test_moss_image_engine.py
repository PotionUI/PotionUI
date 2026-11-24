from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.moss_image.model import MossImage
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.engine import Conditioning, NativeGenerator, NativeModel
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas
from src.platform.runtime.native.text_encoders.base import NativeTextEncoder
from src.platform.runtime.native.kernels import pick_operations

DIM, HEADS, FFN, DEPTH, TEXT_DIM, FREQ, CHANNELS, SEQ = 64, 2, 96, 3, 48, 32, 16, 5

CONFIG = {
    "image_model": "moss_image", "hidden_size": DIM, "head_dim": DIM // HEADS, "num_heads": HEADS,
    "ffn_dim": FFN, "depth": DEPTH, "context_in_dim": TEXT_DIM, "in_channels": CHANNELS,
    "freq_dim": FREQ, "rope_base": 10000.0,
}
SHAPE = (1, CHANNELS, 4, 3)


class _NoTE(NativeTextEncoder):
    role = "stub"

    def encode(self, texts):
        raise AssertionError("conditioning is supplied directly")


def _generator():
    torch.manual_seed(0)
    module = MossImage.from_config(CONFIG, pick_operations(torch.float32, torch.float32)).eval()
    for param in module.parameters():
        if param.ndim > 1:
            param.data.normal_(0.0, 0.05)
        else:
            param.data.uniform_(0.5, 1.5)
    calls = []

    def record(_module, args, kwargs):
        calls.append({"sigma": float(args[1].flatten()[0]), **kwargs})

    module.register_forward_pre_hook(record, with_kwargs=True)
    spec = match_model_spec(CONFIG)
    dit = NativeModel("diffusion_model", module, spec=spec, compute_dtype=torch.float32)
    vae_module = torch.nn.Identity()
    vae_module.z_channels = CHANNELS // 4
    vae = NativeModel("vae", vae_module, spec=spec, compute_dtype=torch.float32)
    return NativeGenerator(dit, _NoTE(), vae), calls


def _cond():
    return {"context": torch.randn(1, SEQ, TEXT_DIM), "y": None, "attention_mask": None}


def _sample(gen, **guidance):
    return gen.sample(
        Conditioning(_cond(), _cond()), SHAPE, steps=3, seed=1, cfg_scale=3.0,
        guidance_options={"cfg_zero_star": False, **guidance},
    )


def test_uncond_skip_layers_reach_the_arch_on_the_negative_pass_only():
    gen, calls = _generator()
    out = _sample(gen, uncond_skip_layers=[1])
    assert out.shape == SHAPE
    assert len(calls) == 6
    assert ["skip_layers" in c for c in calls] == [False, True] * 3
    assert all(c["skip_layers"] == frozenset({1}) for c in calls[1::2])


def test_an_empty_uncond_skip_layers_override_turns_the_skip_off():
    gen, calls = _generator()
    _sample(gen, uncond_skip_layers=[])
    assert len(calls) == 6
    assert all("skip_layers" not in c for c in calls)


def test_the_negative_pass_is_degraded_so_the_output_differs_from_plain_cfg():
    plain_gen, _ = _generator()
    skip_gen, _ = _generator()
    torch.manual_seed(5)
    plain = _sample(plain_gen, uncond_skip_layers=[])
    torch.manual_seed(5)
    skipped = _sample(skip_gen, uncond_skip_layers=[1])
    assert (plain - skipped).abs().max() > 1e-4


def test_the_spec_schedule_drives_the_sampler_sigmas():
    gen, calls = _generator()
    _sample(gen, uncond_skip_layers=[])
    expected = build_sigmas(3, shift=5.0, schedule="moss_grid")
    seen = [c["sigma"] for c in calls[::2]]
    assert seen == pytest.approx([float(s) for s in expected[:-1]], abs=1e-6)


def _unforced_sample(gen, **guidance):
    torch.manual_seed(5)
    cond = _cond()
    uncond = _cond()
    return gen.sample(
        Conditioning(cond, uncond), SHAPE, steps=3, seed=1, cfg_scale=3.0,
        guidance_options={"uncond_skip_layers": [], **guidance},
    )


def test_cfg_zero_star_is_off_by_default_for_the_family_and_an_explicit_true_overrides_it():
    default_gen, _ = _generator()
    off_gen, _ = _generator()
    on_gen, _ = _generator()
    default = _unforced_sample(default_gen)
    off = _unforced_sample(off_gen, cfg_zero_star=False)
    on = _unforced_sample(on_gen, cfg_zero_star=True)
    assert torch.equal(default, off)
    assert (default - on).abs().max() > 1e-5
