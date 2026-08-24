"""Tests for the generator/seedvr2 pipe.

Covers the config surface, the one-step restoration math on a mocked DiT module
(33-channel ``[z | cond | flag]`` input, single forward at t=1000, ``x0 = z - v``),
the input-noise blend, and the area-resize / color-fix helpers. No real model or
GPU is touched — the DiT module is a capturing fake and encode/decode are stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import torch

from src.pipelines.pipes.generator.seedvr2.main import (
    GeneratorSeedVR2Pipe,
    SeedVR2NativeGenerator,
    SEEDVR2_TIMESTEP,
)
from src.pipelines.pipes.generator.seedvr2.resize import prepare_input, target_area, divisible_crop
from src.pipelines.pipes.generator.seedvr2.color_fix import color_correct


# -- config -----------------------------------------------------------------

def test_config_surface_and_defaults():
    cfg = GeneratorSeedVR2Pipe.get_default_config()
    assert cfg["scale"] == 2.0
    assert cfg["color_correction"] == "wavelet"
    assert cfg["latent_noise_scale"] == 0.0 and cfg["input_noise_scale"] == 0.0
    names = {s.name for s in GeneratorSeedVR2Pipe.configuration()}
    assert {"scale", "target_short_side", "color_correction",
            "latent_noise_scale", "input_noise_scale"} <= names


def test_color_correction_choices():
    spec = next(s for s in GeneratorSeedVR2Pipe.configuration() if s.name == "color_correction")
    assert set(spec.choices) == {"wavelet", "adain", "none"}


def test_inputs_have_image_video_model_seed():
    specs = {i.name: i for i in GeneratorSeedVR2Pipe.inputs()}
    assert {"image", "video", "model", "seed"} == set(specs)
    # image/video are alternatives (mode-dependent), so neither is hard-required;
    # process() raises when both are absent.
    assert not specs["image"].required
    assert not specs["video"].required
    assert specs["model"].required


# -- one-step math ----------------------------------------------------------

class _CapturingModule:
    """Fake DiT: records the forward call and returns a constant v-prediction."""

    def __init__(self, v_value, out_channels=16):
        self.v_value = v_value
        self.out_channels = out_channels
        self.calls = []

    def __call__(self, vid, timestep, txt):
        self.calls.append({"vid": vid, "timestep": timestep, "txt": txt})
        b, _c, t, h, w = vid.shape
        return torch.full((b, self.out_channels, t, h, w), self.v_value, dtype=vid.dtype)


def _make_generator(module, cond_latent):
    """Build a SeedVR2NativeGenerator bypassing __init__, with encode/decode stubbed."""
    gen = SeedVR2NativeGenerator.__new__(SeedVR2NativeGenerator)
    gen.dit = SimpleNamespace(module=module, compute_dtype=torch.float32, estimated_vram_gb=1.0)
    gen.device_plan = SimpleNamespace(dit_device="cpu", vae_device="cpu", te_device="cpu")
    gen.placement = None  # -> _resident() returns True
    gen.te = None
    # upscale() routes stills through the tiled clip path (the video branch's
    # machinery) since the 4K image-OOM fix — stub those, not encode_image/decode.
    gen.vae = SimpleNamespace(offload=lambda: None)
    gen._encode_clip = lambda clip, device, **_: cond_latent.clone()
    decoded = {}
    def _decode(x0, device=None, **_):
        decoded["x0"] = x0.clone()
        return np.zeros((1, cond_latent.shape[-2] * 8, cond_latent.shape[-1] * 8, 3), dtype=np.uint8)
    gen._decode_clip = _decode
    gen._build_placement = lambda shape: None
    gen._move_dit_to_gpu = lambda device: None
    gen._stream_dit_to_gpu = lambda device, shape: None
    gen._maybe_compile = lambda: None
    return gen, decoded


def test_one_step_builds_33ch_input_and_x0_equals_z_minus_v():
    h = w = 4
    cond = torch.zeros((1, 16, 1, h, w))
    module = _CapturingModule(v_value=0.5)
    gen, decoded = _make_generator(module, cond)
    txt = torch.ones((58, 5120))

    gen.upscale(np.zeros((h * 8, w * 8, 3), np.uint8), txt, seed=0, latent_noise_scale=0.0)

    call = module.calls[0]
    vid = call["vid"]
    assert vid.shape == (1, 33, 1, h, w)          # 16 noise + 16 cond + 1 flag
    # channel layout: [z | cond | flag]
    z = vid[:, :16]
    assert torch.equal(vid[:, 16:32], cond)        # conditioning latent, untouched
    assert torch.all(vid[:, 32:33] == 1.0)         # all-ones task flag
    assert float(call["timestep"].flatten()[0]) == SEEDVR2_TIMESTEP
    # x0 = z - v  (v == 0.5 everywhere)
    expected_x0 = z - 0.5
    assert torch.allclose(decoded["x0"], expected_x0)


# -- torch.compile hook: SeedVR2NativeGenerator reuses the inherited
# NativeGenerator._maybe_compile() (same gated, reversible regional
# torch.compile as the image path) right after each of its own
# _move_dit_to_gpu calls -- unlike the image path, this subclass never goes
# through NativeGenerator.sample(), so the base class's own call site never
# fires for it on its own. -----------------------------------------------

def test_upscale_offers_a_resident_placement_to_maybe_compile():
    cond = torch.zeros((1, 16, 1, 4, 4))
    module = _CapturingModule(v_value=0.0)
    gen, _decoded = _make_generator(module, cond)
    calls = []
    gen._maybe_compile = lambda: calls.append("compile")
    txt = torch.ones((58, 5120))

    gen.upscale(np.zeros((32, 32, 3), np.uint8), txt, seed=0)

    assert calls == ["compile"]


def test_upscale_never_offers_a_streamed_placement_to_maybe_compile():
    cond = torch.zeros((1, 16, 1, 4, 4))
    module = _CapturingModule(v_value=0.0)
    gen, _decoded = _make_generator(module, cond)
    gen._build_placement = lambda shape: SimpleNamespace(dit=SimpleNamespace(resident=False))
    gen.dit.offload = lambda: None  # not-resident path offloads at the end of upscale()
    calls = []
    gen._maybe_compile = lambda: calls.append("compile")
    txt = torch.ones((58, 5120))

    gen.upscale(np.zeros((32, 32, 3), np.uint8), txt, seed=0)

    assert calls == []


def test_seed_is_deterministic():
    cond = torch.zeros((1, 16, 1, 4, 4))
    txt = torch.ones((58, 5120))
    m1 = _CapturingModule(0.0); g1, _ = _make_generator(m1, cond)
    m2 = _CapturingModule(0.0); g2, _ = _make_generator(m2, cond)
    g1.upscale(np.zeros((32, 32, 3), np.uint8), txt, seed=7)
    g2.upscale(np.zeros((32, 32, 3), np.uint8), txt, seed=7)
    assert torch.equal(m1.calls[0]["vid"], m2.calls[0]["vid"])


def test_latent_noise_perturbs_conditioning_channels():
    cond = torch.ones((1, 16, 1, 4, 4))  # non-zero so a change is visible
    txt = torch.ones((58, 5120))
    m0 = _CapturingModule(0.0); g0, _ = _make_generator(m0, cond)
    mN = _CapturingModule(0.0); gN, _ = _make_generator(mN, cond)
    g0.upscale(np.zeros((32, 32, 3), np.uint8), txt, seed=3, latent_noise_scale=0.0)
    gN.upscale(np.zeros((32, 32, 3), np.uint8), txt, seed=3, latent_noise_scale=0.5)
    clean = m0.calls[0]["vid"][:, 16:32]
    noised = mN.calls[0]["vid"][:, 16:32]
    assert torch.equal(clean, cond)                # scale=0 leaves cond untouched
    assert not torch.equal(noised, cond)           # scale>0 perturbs it
    # z (noise channels) stays identical — drawn before the latent-noise step.
    assert torch.equal(m0.calls[0]["vid"][:, :16], mN.calls[0]["vid"][:, :16])


# -- input-noise blend ------------------------------------------------------

def test_input_noise_no_op_at_zero_and_changes_at_scale():
    arr = np.full((16, 16, 3), 120, np.uint8)
    same = GeneratorSeedVR2Pipe._apply_input_noise(arr, 0.0, seed=1)
    assert np.array_equal(same, arr)
    noised = GeneratorSeedVR2Pipe._apply_input_noise(arr, 1.0, seed=1)
    assert noised.dtype == np.uint8 and noised.shape == arr.shape
    assert not np.array_equal(noised, arr)


# -- helpers ----------------------------------------------------------------

def test_area_resize_and_divisible_crop():
    from PIL import Image
    # scale 2 -> area quadruples
    assert round(target_area(100, 60, 2.0, 0)) == 200 * 120
    # short-side wins over scale
    assert round(target_area(100, 60, 3.0, 120)) == 200 * 120
    out = prepare_input(Image.new("RGB", (101, 63)), 2.0, 0, 16)
    assert out.width % 16 == 0 and out.height % 16 == 0


def test_color_correct_modes():
    rng = np.random.default_rng(0)
    target = rng.integers(0, 255, (48, 48, 3), dtype=np.uint8)
    source = np.full((48, 48, 3), 40, np.uint8)
    assert np.array_equal(color_correct(target, source, "none"), target)
    adain = color_correct(target, source, "wavelet")
    assert adain.shape == target.shape and adain.dtype == np.uint8
    # adain pulls the channel mean toward the source
    a = color_correct(target, source, "adain")
    assert abs(a.mean() - 40) < abs(float(target.mean()) - 40)
    # mismatched source size is resized internally
    small = color_correct(target, np.full((24, 24, 3), 40, np.uint8), "wavelet")
    assert small.shape == target.shape
