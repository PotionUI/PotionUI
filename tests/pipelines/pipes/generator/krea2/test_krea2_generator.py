"""Tests for the generator/krea2 pipe.

`NativeGenerator` / `make_device_plan` are patched with light fakes so the
build_context/generate_one contract and — the point of this file — the
resolution snapping are exercised without loading weights. Krea-2 is the family
that surfaced the crash: its patchify (``build_stream_inputs``) has no internal
pad, so a non-multiple axis (1080 -> latent 135) fails the rearrange outright.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.outputs import GalleryGenerationOutput, ImageGenerationOutput, ParamGenerationOutput
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.krea2.main import GeneratorKrea2Pipe


@dataclass(frozen=True)
class _FakeSpec:
    family: str = "krea2"
    variant: str = "krea2_turbo"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16})
    sampling_settings: dict = field(default_factory=lambda: {"guidance": "none"})


class _FakeGenerator:
    instances: list["_FakeGenerator"] = []

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.spec = _FakeSpec()
        self.sample_calls = []
        _FakeGenerator.instances.append(self)

    def snap_resolution(self, width, height):
        from src.platform.runtime.native.resolution import snap_resolution
        return snap_resolution(width, height, 8, 2)  # Krea-2 granularity 16px

    def latent_shape_for(self, width, height, batch=1):
        # Krea-2 uses the Qwen/Wan causal-3D VAE -> 5D latent (B, 16, 1, H//8, W//8).
        return (batch, 16, 1, height // 8, width // 8)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append({"conditioning": conditioning, "latents_shape": latents_shape, **kw})
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)

    def encode_image(self, pixels, **_):
        self.encode_image_calls = getattr(self, "encode_image_calls", [])
        self.encode_image_calls.append(pixels)
        h, w = pixels.shape[0], pixels.shape[1]
        return torch.zeros(1, 16, 1, h // 8, w // 8)


def _cond_model():
    return SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={})


def _bundle(te_cache_key=None):
    return SimpleNamespace(
        dit=SimpleNamespace(estimated_vram_gb=26.0),
        te_encoder=object(),
        vae=object(),
        te_cache_key=te_cache_key,
    )


class _FakeModelsService:
    """Records evict_dead_weight(key) calls; returns True (evicted) by default."""

    def __init__(self, evict_result=True, raise_on_evict=False):
        self.evict_calls: list[str] = []
        self._evict_result = evict_result
        self._raise = raise_on_evict

    def evict_dead_weight(self, key: str) -> bool:
        self.evict_calls.append(key)
        if self._raise:
            raise RuntimeError("boom")
        return self._evict_result


def _make_pipe(**over):
    cfg = GeneratorKrea2Pipe.get_default_config()
    cfg.update(over)
    return GeneratorKrea2Pipe(config=cfg)


def _pipe_input(quantity=1, seeds=(1,), te_cache_key=None, models=None, image=None):
    inp = {
        "model": _bundle(te_cache_key=te_cache_key),
        "conditioning": [_cond_model() for _ in range(quantity)],
        "seed": list(seeds),
    }
    if models is not None:
        inp["MODELS"] = models
    if image is not None:
        inp["image"] = [image]
    return PipeInput(input=inp)


def setup_function(_):
    _FakeGenerator.instances.clear()


def test_metadata():
    assert GeneratorKrea2Pipe.name == "generator"
    assert GeneratorKrea2Pipe.outputs()[0].io_type == IOType.IMAGE


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_resolution_snapped_to_granularity():
    # The reported crash: 1080 -> latent 135 (odd) breaks the patchify. Snap to
    # 1072 (multiple of 16) before it reaches sample().
    pipe = _make_pipe(resolution="1920x1080")
    pipe.process(_pipe_input(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert gen.sample_calls[0]["latents_shape"] == (1, 16, 1, 1072 // 8, 1920 // 8)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_exact_multiple_unchanged():
    pipe = _make_pipe(resolution="1024x1024")
    ctx = pipe.build_context(_pipe_input())
    assert (ctx.extra["width"], ctx.extra["height"]) == (1024, 1024)


# --- mu_schedule (BE-CFG-KREA2) --------------------------------------------


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_mu_schedule_default_leaves_schedule_settings_untouched():
    # Default "fixed" is a byte-identical no-op: schedule_settings stays exactly
    # what FlowMatchGeneratorPipe.build_context set it to (None -- no preset
    # ever populates it today).
    pipe = _make_pipe()
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra.get("schedule_settings") is None


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_mu_schedule_dynamic_overrides_fixed_mu_with_anchored_shift():
    pipe = _make_pipe(mu_schedule="dynamic")
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra["schedule_settings"] == {
        "fixed_mu": None,
        "dynamic_shift": {"x1_px": 256, "x2_px": 1280, "y1": 0.5, "y2": 1.15, "align": 16},
    }


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_mu_schedule_dynamic_reaches_sample_call():
    pipe = _make_pipe(mu_schedule="dynamic", quantity=1)
    pipe.process(_pipe_input(seeds=(1,)), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    ss = gen.sample_calls[0]["schedule_settings"]
    assert ss["fixed_mu"] is None
    assert ss["dynamic_shift"]["y2"] == pytest.approx(1.15)


def test_get_default_config_cfg_is_inert_off_not_zero():
    # BE-CFG-KREA2: guidance mode is now unconditionally "cfg" (registry.py), so
    # the "off" value that collapses TrueCFG to a single forward is 1.0, not
    # 0.0 -- 0.0 would run a garbage-only (pure-uncond) forward whenever a
    # negative gets encoded (e.g. NAG on). Guard against reintroducing 0.0.
    cfg = GeneratorKrea2Pipe.get_default_config()
    assert cfg["guidance"] == pytest.approx(1.0)
    assert cfg["mu_schedule"] == "fixed"


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_emits_image_gallery():
    pipe = _make_pipe(quantity=1)
    emitted = []
    pipe.process(_pipe_input(seeds=(7,)), lambda o: emitted.append(o))
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
    assert len(gallery) == 1
    assert isinstance(gallery[0].images[0], ImageGenerationOutput)
    assert gallery[0].images[0].seed == 7


# --- live step previews (workbench) ---------------------------------------
#
# The txt2img path wires a PreviewHook alongside ProgressHook; the sampler fires
# ``on_step`` per step with the running x0 estimate, and the hook decodes a cheap
# RGB preview and emits it as a *temporary* ImageGenerationOutput (workbench-only).
# These fakes drive the hooks the way ``denoise`` does so the cadence + the
# error-isolation contract can be exercised on CPU.


class _HookDrivingGenerator(_FakeGenerator):
    """``sample`` that fires the passed hooks per step (like the real loop)."""

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append({"conditioning": conditioning, "latents_shape": latents_shape, **kw})
        hooks = kw.get("hooks") or []
        total = int(kw.get("steps", 8))
        x0 = torch.zeros(latents_shape)
        for i in range(total):
            for h in hooks:
                h.on_step(i, total, x0, 1.0, x0)
        return torch.zeros(latents_shape)


class _BadX0Generator(_HookDrivingGenerator):
    """Drives hooks with a rank the previewer rejects -> decode raises."""

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append({"conditioning": conditioning, "latents_shape": latents_shape, **kw})
        bad = torch.zeros(8, 8)  # 2D: latent_to_rgb raises on this
        for h in (kw.get("hooks") or []):
            h.on_step(0, 1, bad, 1.0, bad)
        return torch.zeros(latents_shape)


def _previews(emitted):
    return [o for o in emitted if isinstance(o, ImageGenerationOutput) and o.temporary]


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _HookDrivingGenerator)
def test_emits_step_previews_at_cadence():
    # Default 8 steps, every_n=5 -> previews at step 1 (first always fires so
    # the workbench comes alive immediately), step 5 (i=4), and the final step 8.
    pipe = _make_pipe(quantity=1, steps=8)
    emitted = []
    pipe.process(_pipe_input(seeds=(7,)), emitted.append)
    previews = _previews(emitted)
    assert len(previews) == 3
    assert all(p.image is not None for p in previews)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _HookDrivingGenerator)
def test_preview_flag_disables_previews():
    pipe = _make_pipe(quantity=1, steps=8, preview=False)
    emitted = []
    pipe.process(_pipe_input(seeds=(7,)), emitted.append)
    assert _previews(emitted) == []
    # ...but the final gallery still lands.
    assert any(isinstance(o, GalleryGenerationOutput) for o in emitted)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _BadX0Generator)
def test_preview_error_does_not_break_generation():
    pipe = _make_pipe(quantity=1)
    emitted = []
    pipe.process(_pipe_input(seeds=(7,)), emitted.append)  # must not raise
    assert _previews(emitted) == []                        # decode failed -> no preview
    assert any(isinstance(o, GalleryGenerationOutput) for o in emitted)  # generation completed


# --- TE eviction before sampling ----------------------------------
#
# By generator time prompt_encoder has already produced the conditioning, so the
# multi-GB Qwen3-VL TE is dead weight through sampling+decode. The generator
# releases it via bundle.te_cache_key + MODELS.evict_dead_weight -- mirrors the
# qwen / LTX idle-TE pattern. The krea2-edit plugin subclasses this
# pipe and inherits the same release (its own coverage lives in the plugin test).


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_te_evicted_when_cache_key_and_models_present():
    models = _FakeModelsService()
    pipe = _make_pipe()
    pipe.process(_pipe_input(te_cache_key="native/te/x.safetensors", models=models), lambda o: None)
    assert models.evict_calls == ["native/te/x.safetensors"]


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_no_eviction_without_a_cache_key():
    models = _FakeModelsService()
    pipe = _make_pipe()
    pipe.process(_pipe_input(te_cache_key=None, models=models), lambda o: None)
    assert models.evict_calls == []


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_no_eviction_without_a_models_service():
    pipe = _make_pipe()
    # No "MODELS" key in pipe_input at all -- must not raise.
    result = pipe.process(_pipe_input(te_cache_key="native/te/x.safetensors", models=None), lambda o: None)
    assert result.output["image"]


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_eviction_failure_does_not_fail_the_generation():
    models = _FakeModelsService(raise_on_evict=True)
    pipe = _make_pipe()
    result = pipe.process(_pipe_input(te_cache_key="native/te/x.safetensors", models=models), lambda o: None)
    assert result.output["image"]                             # generation still completed
    assert models.evict_calls == ["native/te/x.safetensors"]  # eviction was attempted


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_eviction_happens_once_per_generation_call_not_per_seed():
    models = _FakeModelsService()
    pipe = _make_pipe(quantity=3)
    pipe.process(_pipe_input(quantity=3, seeds=(1, 2, 3), te_cache_key="native/te/x.safetensors", models=models), lambda o: None)
    assert models.evict_calls == ["native/te/x.safetensors"]  # not 3x


def test_generator_declares_the_models_service_input():
    names = {s.name for s in GeneratorKrea2Pipe.inputs()}
    assert "MODELS" in names


# --- step_cache (FBCache) config --------------------------------


def test_step_cache_declared_in_configuration():
    spec = next(s for s in GeneratorKrea2Pipe.configuration() if s.name == "step_cache")
    assert spec.param_type is dict


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_sample_receives_step_cache_options():
    step_cache = {"rel_threshold": 0.12, "warmup_steps": 2}
    pipe = _make_pipe(step_cache=step_cache)
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["step_cache_options"] == step_cache


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_step_cache_defaults_to_none():
    pipe = _make_pipe()
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["step_cache_options"] is None


# --- NAG: _attach_nag wiring through generate_one -----------------
#
# The attach happens in the SHARED FlowMatchGeneratorPipe.generate_one (see
# tests/pipelines/pipes/_shared/generation/test_flow_generator_pipe.py for
# _attach_nag's own unit coverage); these tests exercise it end-to-end
# through the Krea-2 pipe, checking what actually reaches gen.sample().


def _cond_model_with_negative():
    return SimpleNamespace(
        embeds={"context": torch.ones(1, 4, 8)},
        n_embeds={"context": torch.zeros(1, 3, 8), "attention_mask": torch.ones(1, 3, dtype=torch.long)},
    )


def _pipe_input_with_negative(quantity=1, seeds=(1,)):
    inp = {
        "model": _bundle(),
        "conditioning": [_cond_model_with_negative() for _ in range(quantity)],
        "seed": list(seeds),
    }
    return PipeInput(input=inp)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_nag_scale_above_one_attaches_nag_context_to_cond():
    pipe = _make_pipe(nag_scale=1.5, nag_tau=2.0, nag_alpha=0.25)
    pipe.process(_pipe_input_with_negative(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    cond = gen.sample_calls[0]["conditioning"].cond
    assert torch.equal(cond["nag_context"], torch.zeros(1, 3, 8))
    assert cond["nag"] == {"scale": 1.5, "tau": 2.0, "alpha": 0.25}


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_default_nag_scale_does_not_attach_nag_context():
    pipe = _make_pipe()  # default nag_scale == 1.0
    pipe.process(_pipe_input_with_negative(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    cond = gen.sample_calls[0]["conditioning"].cond
    assert "nag_context" not in cond


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_nag_scale_above_one_without_negative_encoding_is_noop():
    # cond_model.n_embeds == {} (no negative encoded) -> `or None` -> _attach_nag no-ops.
    pipe = _make_pipe(nag_scale=1.5)
    pipe.process(_pipe_input(), lambda o: None)  # default fixture: n_embeds={}
    gen = _FakeGenerator.instances[-1]
    cond = gen.sample_calls[0]["conditioning"].cond
    assert "nag_context" not in cond


# --- refine_tail: whole-frame enhance sigma-tail slicing ----------
#
# `refine_tail` slices the tail of the official fixed-mu 8-step Euler grid
# (build_sigmas(8, fixed_mu=<spec>)) into ctx.extra["sigmas"], which the shared
# Img2ImgGeneratorMixin.maybe_img2img forwards to gen.sample() unchanged (see
# tests/pipelines/pipes/_shared/test_img2img_helper.py for that seam's own
# coverage). These tests exercise the Krea-2-specific slicing/validation/
# provenance end-to-end through the pipe.


@dataclass(frozen=True)
class _FakeSpecWithFixedMu(_FakeSpec):
    sampling_settings: dict = field(default_factory=lambda: {"guidance": "none", "fixed_mu": 1.15})


class _FakeGeneratorWithFixedMu(_FakeGenerator):
    """Same fake as _FakeGenerator, but its spec carries Krea-2's real fixed_mu."""

    def __init__(self, dit, te, vae, device_plan=None, **_):
        super().__init__(dit, te, vae, device_plan)
        self.spec = _FakeSpecWithFixedMu()


def _official_grid():
    from src.platform.runtime.native.sampling.flow_schedule import build_sigmas
    return build_sigmas(8, fixed_mu=1.15)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGeneratorWithFixedMu)
def test_refine_tail_off_by_default_no_sigmas_passed():
    # img2img mode but refine_tail left at its "" default -> byte-neutral,
    # exactly like every other img2img generation today.
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="img2img")
    pipe.process(_pipe_input(image=src), lambda o: None)
    gen = _FakeGeneratorWithFixedMu.instances[-1]
    assert gen.sample_calls[0]["sigmas"] is None


@pytest.mark.parametrize("refine_tail,tail_len", [("subtle", 2), ("balanced", 3), ("strong", 4)])
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGeneratorWithFixedMu)
def test_refine_tail_slices_the_official_grid_tail(refine_tail, tail_len):
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="img2img", refine_tail=refine_tail)
    pipe.process(_pipe_input(image=src), lambda o: None)
    gen = _FakeGeneratorWithFixedMu.instances[-1]
    sigmas = gen.sample_calls[0]["sigmas"]
    expected = _official_grid()[-tail_len:]
    assert sigmas is not None
    assert torch.allclose(torch.as_tensor(sigmas, dtype=torch.float32), expected, atol=1e-6)
    assert len(sigmas) == tail_len
    assert float(sigmas[-1]) == 0.0


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGeneratorWithFixedMu)
def test_refine_tail_in_txt2img_mode_rejected_loudly():
    pipe = _make_pipe(mode="txt2img", refine_tail="balanced")
    with pytest.raises(ValueError, match="img2img"):
        pipe.process(_pipe_input(), lambda o: None)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_refine_tail_without_fixed_mu_in_spec_rejected_loudly():
    # Plain _FakeGenerator's spec has no "fixed_mu" key -- refine_tail must
    # fail loudly rather than silently falling back to a derived schedule.
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="img2img", refine_tail="subtle")
    with pytest.raises(ValueError, match="fixed_mu"):
        pipe.process(_pipe_input(image=src), lambda o: None)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGeneratorWithFixedMu)
def test_refine_tail_forces_denoise_positive_even_when_configured_zero():
    # denoise<=0 is img2img_denoise's own no-op guard; refine_tail must not be
    # silently defeated by a preset that leaves denoise at 0 (it's otherwise
    # fully inert once sigmas is set).
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="img2img", refine_tail="subtle", denoise=0.0)
    pipe.process(_pipe_input(image=src), lambda o: None)
    gen = _FakeGeneratorWithFixedMu.instances[-1]
    assert gen.encode_image_calls  # the refine actually ran (not short-circuited)
    assert gen.sample_calls[0]["denoise_strength"] > 0.0


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGeneratorWithFixedMu)
def test_refine_tail_emits_provenance_params():
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="img2img", refine_tail="balanced", quantity=2)
    emitted = []
    pipe.process(_pipe_input(quantity=2, seeds=(1, 2), image=src), emitted.append)
    params = {o.name: o.values for o in emitted if isinstance(o, ParamGenerationOutput)}
    assert params["refine_tail"] == ["balanced", "balanced"]
    assert len(params["refine_tail_sigmas"]) == 2
    expected = [round(float(v), 6) for v in _official_grid()[-3:].tolist()]
    assert params["refine_tail_sigmas"][0] == expected
    assert params["refine_tail_sigmas"][1] == expected


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGeneratorWithFixedMu)
def test_refine_tail_off_emits_no_provenance_params():
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="img2img")
    emitted = []
    pipe.process(_pipe_input(image=src), emitted.append)
    names = {o.name for o in emitted if isinstance(o, ParamGenerationOutput)}
    assert "refine_tail" not in names
    assert "refine_tail_sigmas" not in names
