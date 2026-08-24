"""End-to-end integration test for the native engine (CPU, tiny shapes).

Builds a tiny-but-real Flux1 DiT and a flux_ae VAE, fills them with finite
weights, writes temp safetensors, and drives the full public path:
``NativeEngineLoader.load`` per kind -> ``NativeGenerator`` encode/sample/decode.
The text encoder is a stub (task #3's real TE is not needed to exercise the
engine's orchestration + adapter).
"""

from __future__ import annotations

import gc
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.engine import (
    Conditioning,
    NativeEngineLoader,
    NativeGenerator,
    NativeModel,
    _estimate_text_encoder_gb,
    _latent_frames,
)
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.text_encoders.base import NativeTextEncoder
from src.platform.runtime.native.vae.ae_2d import AutoEncoder2D
from src.platform.runtime.native.vae.causal_3d import AutoEncoderCausal3D

# Tiny Flux1 config: hidden 128 / 1 head (axes sum 128), in==out latent 16,
# context width 32, one double + one single block, embedded guidance.
_FLUX1 = {
    "image_model": "flux",
    "in_channels": 16,
    "out_channels": 16,
    "hidden_size": 128,
    "context_in_dim": 32,
    "num_heads": 1,
    "depth": 1,
    "depth_single_blocks": 1,
    "axes_dim": [16, 56, 56],
    "mlp_ratio": 4.0,
    "theta": 10000,
    "patch_size": 2,
    "qkv_bias": True,
    "guidance_embed": True,
}

_VAE = {
    "vae_type": "flux_ae",
    "in_channels": 3,
    "out_channels": 3,
    "latent_channels": 16,
    "has_quant_conv": False,
    "has_batchnorm": False,
}


def _finite_sd(module: torch.nn.Module) -> dict[str, torch.Tensor]:
    """A finite, bf16 state dict matching a module's key/shape layout."""
    torch.manual_seed(0)
    sd = {}
    for k, v in module.state_dict().items():
        if v.is_floating_point():
            sd[k] = (torch.randn(v.shape) * 0.05).to(torch.bfloat16)
        else:
            sd[k] = v.clone()
    return sd


class _StubTE(NativeTextEncoder):
    """Returns Flux1-shaped conditioning: context [1,S,32] + pooled [1,768]."""

    role = "stub"

    def __init__(self, context_dim: int = 32, seq: int = 4) -> None:
        self.context_dim = context_dim
        self.seq = seq

    def encode(self, texts):
        b = len(texts)
        return {
            "context": torch.randn(b, self.seq, self.context_dim),
            "pooled": torch.randn(b, 768),
        }


def _build_and_save(module: torch.nn.Module, path) -> None:
    """Save a finite state dict, then free the (real-arch-sized) build module.

    The module only exists to expose key/shape layout; keeping it (the flux AE
    is ~330MB fp32) would add pointless memory pressure to the shared suite.
    """
    save_file(_finite_sd(module), str(path))
    del module
    gc.collect()


@pytest.fixture(scope="module")
def dit_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("engine") / "dit.safetensors"
    _build_and_save(Flux.from_config(_FLUX1, disable_weight_init), path)
    return path


@pytest.fixture(scope="module")
def vae_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("engine") / "vae.safetensors"
    _build_and_save(AutoEncoder2D.from_config(_VAE, disable_weight_init), path)
    return path


@pytest.fixture(scope="module")
def causal3d_vae_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("engine") / "causal3d_vae.safetensors"
    _build_and_save(AutoEncoderCausal3D.from_config({}, disable_weight_init), path)
    return path


def test_loader_dit_detects_and_matches_spec(dit_path):
    loader = NativeEngineLoader(device="cpu")
    model = loader.load(dit_path, "diffusion_model")
    assert isinstance(model, NativeModel)
    assert model.kind == "diffusion_model"
    assert model.spec.variant == "flux1"
    assert model.estimated_vram_gb is not None and model.estimated_vram_gb > 0
    assert model.module.patch_size == 2


def test_loader_vae_loads(vae_path):
    loader = NativeEngineLoader(device="cpu")
    model = loader.load(vae_path, "vae")
    assert model.kind == "vae"
    assert isinstance(model.module, AutoEncoder2D)


def test_loader_vae_slices_ltx_all_in_one_checkpoint_before_sizing(tmp_path):
    """LTX ships DiT + VAE (+ audio_vae/vocoder) in one checkpoint. The VAE
    must be sized off its own ``vae.*`` slice, not the whole multi-GB file --
    see ``NativeEngineLoader._load_vae``'s slice-before-estimate comment. A
    fake oversized 'DiT' blob alongside a tiny real VAE proves the estimate
    tracks the slice, not the file."""
    import json

    from src.platform.runtime.native.vae.ltx_causal_video import LTXCausalVideoVAE

    tiny_config = {
        "_class_name": "CausalVideoAutoencoder",
        "dims": 3, "in_channels": 3, "out_channels": 3, "latent_channels": 8,
        "encoder_blocks": [["res_x", {"num_layers": 1}], ["compress_all_res", {"multiplier": 2}], ["res_x", {"num_layers": 1}]],
        "decoder_blocks": [["res_x", {"num_layers": 1, "inject_noise": False}], ["compress_all", {"residual": True, "multiplier": 2}], ["res_x", {"num_layers": 1, "inject_noise": False}]],
        "scaling_factor": 1.0, "norm_layer": "pixel_norm", "patch_size": 1,
        "latent_log_var": "uniform", "use_quant_conv": False, "causal_decoder": False,
        "timestep_conditioning": False, "encoder_base_channels": 16, "decoder_base_channels": 16,
    }
    vae_module = LTXCausalVideoVAE.from_config(tiny_config, disable_weight_init)
    vae_sd = {f"vae.{k}": (torch.randn(v.shape) * 0.02).to(torch.bfloat16) for k, v in vae_module.state_dict().items() if v.is_floating_point()}

    # A "DiT" blob orders of magnitude larger than the (few-hundred-KB) VAE
    # slice -- if the estimate were computed on the full file, it would sit
    # around dit_only_gb; sliced correctly, it must sit far below it.
    fake_dit_sd = {"model.diffusion_model.big_weight": torch.zeros(4096, 16384, dtype=torch.bfloat16)}
    dit_only_gb = (4096 * 16384 * 2) / (1024 ** 3)

    path = tmp_path / "ltx_all_in_one.safetensors"
    save_file({**fake_dit_sd, **vae_sd}, str(path), metadata={"config": json.dumps({"vae": tiny_config})})

    loader = NativeEngineLoader(device="cpu")
    model = loader.load(path, "vae")

    assert model.kind == "vae"
    assert isinstance(model.module, LTXCausalVideoVAE)
    assert model.estimated_vram_gb is not None
    assert model.estimated_vram_gb < dit_only_gb / 10


_TINY_AUDIO_VAE_CONFIG = {
    "model": {"params": {
        "ddconfig": {
            "double_z": True, "mel_bins": 64, "z_channels": 4, "resolution": 32,
            "in_channels": 2, "out_ch": 2, "ch": 8, "ch_mult": [1, 2], "num_res_blocks": 1,
            "attn_resolutions": [], "dropout": 0.0, "mid_block_add_attention": False,
            "norm_type": "pixel", "causality_axis": "height",
        },
        "sampling_rate": 16000,
    }},
    "preprocessing": {"stft": {"hop_length": 160, "filter_length": 1024}},
}

_TINY_VOCODER_CONFIG = {
    "resblock_kernel_sizes": [3], "upsample_rates": [2, 2], "upsample_kernel_sizes": [4, 4],
    "resblock_dilation_sizes": [[1, 3]], "upsample_initial_channel": 16, "stereo": True, "resblock": "1",
}


def test_loader_audio_vae_slices_ltx_all_in_one_checkpoint_before_sizing(tmp_path):
    """Same slice-before-estimate contract as the video VAE (C-0), for the
    audio VAE Kind. Both the standalone audio file and the all-in-one
    checkpoint carry the ``audio_vae.`` prefix, so no bare-key branch exists."""
    import json

    from src.platform.runtime.native.vae.ltx_audio import LTXAudioAutoencoder

    audio_module = LTXAudioAutoencoder.from_config(_TINY_AUDIO_VAE_CONFIG, disable_weight_init)
    audio_sd = {f"audio_vae.{k}": (torch.randn(v.shape) * 0.02).to(torch.bfloat16) for k, v in audio_module.state_dict().items() if v.is_floating_point()}

    fake_dit_sd = {"model.diffusion_model.big_weight": torch.zeros(4096, 16384, dtype=torch.bfloat16)}
    dit_only_gb = (4096 * 16384 * 2) / (1024 ** 3)

    path = tmp_path / "ltx_all_in_one_audio.safetensors"
    save_file({**fake_dit_sd, **audio_sd}, str(path), metadata={"config": json.dumps({"audio_vae": _TINY_AUDIO_VAE_CONFIG})})

    loader = NativeEngineLoader(device="cpu")
    model = loader.load(path, "audio_vae")

    assert model.kind == "audio_vae"
    assert isinstance(model.module, LTXAudioAutoencoder)
    assert model.estimated_vram_gb is not None
    assert model.estimated_vram_gb < dit_only_gb / 10


def test_loader_vocoder_slices_ltx_all_in_one_checkpoint_before_sizing(tmp_path):
    """Same slice-before-estimate contract as the video VAE (C-0), for the
    vocoder Kind."""
    import json

    from src.platform.runtime.native.vae.ltx_audio import LTXVocoder

    voc_module = LTXVocoder.from_config(_TINY_VOCODER_CONFIG, disable_weight_init)
    voc_sd = {f"vocoder.{k}": (torch.randn(v.shape) * 0.02).to(torch.bfloat16) for k, v in voc_module.state_dict().items() if v.is_floating_point()}

    fake_dit_sd = {"model.diffusion_model.big_weight": torch.zeros(4096, 16384, dtype=torch.bfloat16)}
    dit_only_gb = (4096 * 16384 * 2) / (1024 ** 3)

    path = tmp_path / "ltx_all_in_one_vocoder.safetensors"
    save_file({**fake_dit_sd, **voc_sd}, str(path), metadata={"config": json.dumps({"vocoder": _TINY_VOCODER_CONFIG})})

    loader = NativeEngineLoader(device="cpu")
    model = loader.load(path, "vocoder")

    assert model.kind == "vocoder"
    assert isinstance(model.module, LTXVocoder)
    assert model.estimated_vram_gb is not None
    assert model.estimated_vram_gb < dit_only_gb / 10


def test_loader_vae_never_materializes_dit_tensor_from_all_in_one(tmp_path, monkeypatch):
    """Fix 1 regression guard: ``_load_vae`` must read the ``vae.*`` slice off
    disk directly (``load_torch_file_prefixed``) rather than reading the whole
    all-in-one checkpoint (including the DiT) and discarding most of it. Proven
    by asserting the DiT's tensor key is never fetched via ``get_tensor``."""
    import json

    from src.platform.runtime.native.vae.ltx_causal_video import LTXCausalVideoVAE

    tiny_config = {
        "_class_name": "CausalVideoAutoencoder",
        "dims": 3, "in_channels": 3, "out_channels": 3, "latent_channels": 8,
        "encoder_blocks": [["res_x", {"num_layers": 1}], ["compress_all_res", {"multiplier": 2}], ["res_x", {"num_layers": 1}]],
        "decoder_blocks": [["res_x", {"num_layers": 1, "inject_noise": False}], ["compress_all", {"residual": True, "multiplier": 2}], ["res_x", {"num_layers": 1, "inject_noise": False}]],
        "scaling_factor": 1.0, "norm_layer": "pixel_norm", "patch_size": 1,
        "latent_log_var": "uniform", "use_quant_conv": False, "causal_decoder": False,
        "timestep_conditioning": False, "encoder_base_channels": 16, "decoder_base_channels": 16,
    }
    vae_module = LTXCausalVideoVAE.from_config(tiny_config, disable_weight_init)
    vae_sd = {f"vae.{k}": (torch.randn(v.shape) * 0.02).to(torch.bfloat16) for k, v in vae_module.state_dict().items() if v.is_floating_point()}
    fake_dit_sd = {"model.diffusion_model.big_weight": torch.zeros(64, 64, dtype=torch.bfloat16)}

    path = tmp_path / "ltx_all_in_one_dit_guard.safetensors"
    save_file({**fake_dit_sd, **vae_sd}, str(path), metadata={"config": json.dumps({"vae": tiny_config})})

    from safetensors import safe_open as real_safe_open

    read_keys = []

    class _CountingHandle:
        def __init__(self, handle):
            self._handle = handle

        def keys(self):
            return self._handle.keys()

        def get_tensor(self, key):
            read_keys.append(key)
            return self._handle.get_tensor(key)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self._handle.__exit__(*a)

    def _wrapped_safe_open(p, framework="pt", device="cpu"):
        return _CountingHandle(real_safe_open(p, framework=framework, device=device))

    monkeypatch.setattr(
        "src.platform.runtime.native.io.safetensors_loader.safe_open", _wrapped_safe_open
    )

    loader = NativeEngineLoader(device="cpu")
    model = loader.load(path, "vae")

    assert model.kind == "vae"
    assert "model.diffusion_model.big_weight" not in read_keys
    assert read_keys and all(k.startswith("vae.") for k in read_keys)


def test_latent_frames_is_one_for_4d_shape():
    assert _latent_frames((1, 16, 64, 64)) == 1


def test_latent_frames_extracts_t_from_5d_shape():
    assert _latent_frames((1, 16, 16, 64, 64)) == 16
    assert _latent_frames((1, 16, 1, 64, 64)) == 1  # still-image causal-3D latent


# --- ref_latents-aware placement (Qwen-Image-Edit OOM) -------------


def test_ref_hw_frames_normalizes_a_bare_tensor():
    ref = torch.zeros(1, 4, 1, 32, 48)  # (B,C,T,H,W)
    assert NativeGenerator._ref_hw_frames(ref) == [((32, 48), 1)]


def test_ref_hw_frames_normalizes_a_list_of_tensors():
    refs = [torch.zeros(1, 4, 1, 16, 16), torch.zeros(1, 4, 1, 8, 8)]
    assert NativeGenerator._ref_hw_frames(refs) == [((16, 16), 1), ((8, 8), 1)]


def test_ref_hw_frames_handles_4d_and_multi_frame_5d():
    four_d = torch.zeros(1, 4, 20, 20)  # (B,C,H,W) still-image latent
    five_d = torch.zeros(1, 4, 3, 10, 12)  # (B,C,T,H,W), T=3
    assert NativeGenerator._ref_hw_frames(four_d) == [((20, 20), 1)]
    assert NativeGenerator._ref_hw_frames(five_d) == [((10, 12), 3)]


def test_ref_hw_frames_none_and_empty_are_empty():
    assert NativeGenerator._ref_hw_frames(None) == []
    assert NativeGenerator._ref_hw_frames([]) == []


def test_ref_hw_frames_skips_shapeless_and_none_entries():
    refs = [torch.zeros(1, 4, 8, 8), None, "not a tensor"]
    assert NativeGenerator._ref_hw_frames(refs) == [((8, 8), 1)]


class _FakeComponent:
    def __init__(self, estimated_vram_gb=0.0, quant_format=None):
        self.estimated_vram_gb = estimated_vram_gb
        self.quant_format = quant_format


def _bare_generator(*, vram_gb, dit_gb=0.0, te_gb=0.0, vae_gb=0.0):
    """A NativeGenerator with only the attributes `_build_placement`/
    `_dit_weights_budget_gb` actually read -- constructed via `__new__` to
    skip `__init__` (which needs real NativeModel/DevicePlan instances this
    CPU-only test has no reason to build)."""
    gen = object.__new__(NativeGenerator)
    gen._explicit_placement = None
    gen.vram_gb = vram_gb
    gen.device_plan = SimpleNamespace(dit_device="cuda:0", te_device="cuda:0", vae_device="cuda:0")
    gen.dit = _FakeComponent(dit_gb)
    gen.te = _FakeComponent(te_gb)
    gen.vae = _FakeComponent(vae_gb)
    return gen


def test_build_placement_edit_mode_can_flip_resident_to_streaming(monkeypatch):
    """The whole point of this placement fix: an edit-mode call (with
    ref_latents riding the same DiT forward) must reserve MORE headroom than
    a txt2img call at the identical latents_shape -- enough, at the right
    budget, to flip the DiT from full residency to streaming, which the
    old (ref-blind) estimate would never have done."""
    import src.platform.runtime.native.engine as engine_mod

    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: 25.0)
    monkeypatch.setattr(
        engine_mod.get_residency_manager(), "ensure_free", lambda *a, **k: False, raising=False,
    )
    monkeypatch.setattr(
        engine_mod.get_residency_manager(), "offload_all", lambda *a, **k: False, raising=False,
    )

    # DiT sized so it fits the txt2img budget but not the edit-mode one, once
    # the (much larger, to make the effect unambiguous) ref headroom is priced in.
    gen = _bare_generator(vram_gb=25.0, dit_gb=20.0)
    latents_shape = (1, 16, 1, 96, 96)
    huge_ref = torch.zeros(1, 16, 1, 4096, 4096)  # deliberately huge -> large headroom delta

    txt2img_plan = gen._build_placement(latents_shape)
    edit_plan = gen._build_placement(latents_shape, ref_latents=[huge_ref])

    assert txt2img_plan.dit.resident is True
    assert edit_plan.dit.resident is False


def test_build_placement_none_when_no_vram_budget():
    gen = _bare_generator(vram_gb=None)
    gen.device_plan = SimpleNamespace(dit_device="cpu", te_device="cpu", vae_device="cpu")
    assert gen._build_placement((1, 16, 1, 64, 64)) is None
    assert gen._build_placement((1, 16, 1, 64, 64), ref_latents=[torch.zeros(1, 16, 1, 64, 64)]) is None


def test_dit_weights_budget_shrinks_with_ref_latents(monkeypatch):
    import src.platform.runtime.native.engine as engine_mod

    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: 50.0)
    monkeypatch.setattr(
        engine_mod.get_residency_manager(), "ensure_free", lambda *a, **k: False, raising=False,
    )

    gen = _bare_generator(vram_gb=50.0, dit_gb=20.0)
    gen._ensure_room_for = lambda need_gb, device: None  # no-op: no real residency tracked in this test

    latents_shape = (1, 16, 1, 128, 128)
    same_res_ref = torch.zeros(1, 16, 1, 128, 128)

    no_ref_budget = gen._dit_weights_budget_gb(latents_shape)
    with_ref_budget = gen._dit_weights_budget_gb(latents_shape, ref_latents=[same_res_ref])

    assert with_ref_budget < no_ref_budget
    # The reserved gap should equal ref_latents_headroom_gb's own (unfloored) term.
    from src.platform.runtime.native.memory.tiering import ref_latents_headroom_gb

    expected_gap = ref_latents_headroom_gb([((128, 128), 1)])
    assert abs((no_ref_budget - with_ref_budget) - expected_gap) < 1e-6


def test_dit_weights_budget_unaffected_when_ref_latents_absent(monkeypatch):
    import src.platform.runtime.native.engine as engine_mod

    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: 50.0)
    gen = _bare_generator(vram_gb=50.0, dit_gb=20.0)
    gen._ensure_room_for = lambda need_gb, device: None

    latents_shape = (1, 16, 1, 128, 128)
    assert gen._dit_weights_budget_gb(latents_shape) == gen._dit_weights_budget_gb(latents_shape, ref_latents=None)
    assert gen._dit_weights_budget_gb(latents_shape) == gen._dit_weights_budget_gb(latents_shape, ref_latents=[])


def test_sample_extracts_ref_latents_from_cond_dict_for_placement(monkeypatch, dit_path, vae_path):
    """Integration-level guard: `sample()` must pull `ref_latents` out of the
    `cond` dict and thread it into `_build_placement`, not silently drop it."""
    loader = NativeEngineLoader()
    dit = loader.load(dit_path, "diffusion_model")
    vae = loader.load(vae_path, "vae")
    gen = NativeGenerator(dit, _StubTE(), vae)

    seen = {}
    real_build_placement = gen._build_placement

    def _spy(latents_shape, ref_latents=None):
        seen["ref_latents"] = ref_latents
        return real_build_placement(latents_shape, ref_latents=ref_latents)

    monkeypatch.setattr(gen, "_build_placement", _spy)

    # A real (tiny) ref tensor, not a placeholder string: this drives the
    # REAL denoise loop end to end (this arch also has ref_latents support),
    # so the value must be something the DiT forward can actually consume.
    ref = torch.zeros(1, 16, 4, 4)
    cond = {"context": torch.zeros(1, 4, 32), "ref_latents": [ref]}
    gen.sample(Conditioning(cond), (1, 16, 8, 8), steps=1, seed=0, cfg_scale=1.0)

    assert seen["ref_latents"] == [ref]


def test_krea2_edit_plugin_bare_tensor_ref_shape_is_priced():
    """The krea2-edit plugin sets ``cond["ref_latents"] = ref_latent`` -- a
    BARE tensor, not a list (generator_krea2_edit/main.py). ``sample()`` prices
    it by ``ref_latents = cond_dict.get("ref_latents")`` then
    ``_ref_hw_frames(ref_latents)``; this guards that exact seam so a regression
    (key rename, list-only handling) that silently zeroes the ref headroom on
    the plugin path fails loudly rather than re-OOMing on a live 5090."""
    ref_latent = torch.zeros(1, 16, 1, 128, 128)  # Krea-2 causal-3D VAE latent
    cond = {"context": torch.zeros(1, 4, 32), "ref_latents": ref_latent}

    cond_dict, _uncond = NativeGenerator._unwrap_conditioning(Conditioning(cond=cond))
    priced = NativeGenerator._ref_hw_frames(cond_dict.get("ref_latents"))

    assert priced == [((128, 128), 1)]  # non-empty -> reference tokens are reserved for


# --- VAE encode must not retain the autograd graph ---


def test_encode_image_runs_under_no_grad_so_ref_latent_carries_no_graph():
    """The edit-mode ~16.8GB VRAM leak: ``encode_image`` is called OUTSIDE the
    sampler's own ``@torch.no_grad`` guard, so an unguarded encode returns a
    latent whose ``.grad_fn`` pins the entire encoder activation graph on-device
    -- and the edit pipes hold that latent as ``ref_latents`` for the whole
    sampling run. The wrapper must encode under ``no_grad`` so the ref latent is
    detached and the activations free the moment encode returns."""
    from src.platform.runtime.native.vae.causal_3d_v2 import AutoEncoderCausal3D_2_2

    vae_mod = AutoEncoderCausal3D_2_2.from_config({}, disable_weight_init)
    vae_mod.eval()
    with torch.no_grad():
        for p in vae_mod.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)

    # Mechanism: grad is ON by default and the VAE's params require grad, so a
    # RAW encode retains .grad_fn (the whole activation graph) -- the leak.
    raw = vae_mod.encode_image(torch.zeros(1, 3, 64, 64))
    assert raw.grad_fn is not None

    # The wrapper must detach. Bare generator whose VAE is this module, driven
    # down the self-normalizing causal-3d path (no spec/mean-std needed).
    gen = object.__new__(NativeGenerator)
    gen.device_plan = SimpleNamespace(vae_device="cpu")
    gen.vae = SimpleNamespace(
        module=vae_mod, compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
    )
    gen._is_causal3d_vae = lambda: True
    gen._is_self_normalizing_vae = lambda: True

    latent = gen.encode_image(np.zeros((64, 64, 3), dtype=np.uint8))
    assert latent.grad_fn is None
    assert latent.requires_grad is False


# --- degraded-to-streaming DiT must be torn down ------


class _RecordingDit:
    def __init__(self, is_streaming: bool, device: str = "cpu"):
        self._is_streaming = is_streaming
        self.device = device
        self.offload_calls = 0

    @property
    def is_streaming(self) -> bool:
        return self._is_streaming

    def offload(self) -> None:
        self.offload_calls += 1
        self.device = "cpu"
        self._is_streaming = False


def _gen_with_dit(dit, *, placement_resident: bool) -> NativeGenerator:
    gen = object.__new__(NativeGenerator)
    gen.placement = SimpleNamespace(dit=SimpleNamespace(resident=placement_resident))
    gen.dit = dit
    return gen


def test_native_model_is_streaming_reflects_streamer_active():
    m = NativeModel("diffusion_model", torch.nn.Linear(4, 4))
    assert m.is_streaming is False                     # no streamer placed
    m._streamer = SimpleNamespace(active=True)
    assert m.is_streaming is True
    m._streamer = SimpleNamespace(active=False)
    assert m.is_streaming is False                     # torn down


def test_release_dit_offloads_a_degraded_streamed_dit_despite_stale_resident_flag():
    """The reported leak: a co-tenant-OOM degrade in _move_dit_to_gpu switches a
    'resident' plan to streaming, but placement.dit.resident stays True. The
    teardown must key off the LIVE streamer, not the stale flag, or ~24.5GB stays
    pinned in host RAM for the life of the RAM-cached DiT."""
    dit = _RecordingDit(is_streaming=True)
    gen = _gen_with_dit(dit, placement_resident=True)
    gen._release_dit_after_sampling()
    assert dit.offload_calls == 1


def test_release_dit_offloads_a_planned_streamed_dit():
    dit = _RecordingDit(is_streaming=True)
    gen = _gen_with_dit(dit, placement_resident=False)
    gen._release_dit_after_sampling()
    assert dit.offload_calls == 1


def test_release_dit_keeps_a_cleanly_resident_dit_on_gpu():
    # Fully resident, no streamer: left on the GPU for cheap reuse (unchanged).
    dit = _RecordingDit(is_streaming=False)
    gen = _gen_with_dit(dit, placement_resident=True)
    gen._release_dit_after_sampling()
    assert dit.offload_calls == 0


# --- ref-image encode must make room before it runs -------------


def _encode_gen(dit, *, causal3d=True, vae_gb=0.3, vae_resident=True):
    gen = object.__new__(NativeGenerator)
    gen.placement = SimpleNamespace(vae=SimpleNamespace(resident=vae_resident))
    gen.dit = dit
    gen.vae = SimpleNamespace(estimated_vram_gb=vae_gb)
    gen._is_causal3d_vae = lambda: causal3d
    gen._ensure_room_for = lambda need, device: None   # foreign eviction is out of scope here
    return gen


def test_encode_need_gb_scales_with_input_pixels():
    dit = _RecordingDit(is_streaming=False)
    gen = _encode_gen(dit)
    big = gen._encode_need_gb(torch.zeros(1, 3, 1072, 1920))
    small = gen._encode_need_gb(torch.zeros(1, 3, 512, 512))
    assert big > small
    # 2MP causal-3D estimate must be large enough to exceed a 32GB card's free
    # room once a ~15GB DiT is parked -- the whole point of the proactive offload.
    assert big > 12.0


def test_ensure_room_for_encode_offloads_parked_dit_when_estimate_exceeds_free(monkeypatch):
    import src.platform.runtime.native.engine as engine_mod
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: 6.4)  # DiT parked -> little free

    dit = _RecordingDit(is_streaming=False, device="cuda:0")
    gen = _encode_gen(dit)
    gen._ensure_room_for_encode(torch.zeros(1, 3, 1072, 1920), "cuda:0")
    assert dit.offload_calls == 1


def test_ensure_room_for_encode_keeps_dit_warm_when_encode_fits(monkeypatch):
    import src.platform.runtime.native.engine as engine_mod
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: 25.0)  # plenty free

    dit = _RecordingDit(is_streaming=False, device="cuda:0")
    gen = _encode_gen(dit)
    gen._ensure_room_for_encode(torch.zeros(1, 3, 1072, 1920), "cuda:0")
    assert dit.offload_calls == 0                      # small/fitting edit keeps the DiT parked


def test_ensure_room_for_encode_is_a_noop_on_cpu(monkeypatch):
    import src.platform.runtime.native.engine as engine_mod
    # Even with a "cuda" DiT, a cpu VAE device must never offload / query VRAM.
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: (_ for _ in ()).throw(AssertionError))
    dit = _RecordingDit(is_streaming=False, device="cuda:0")
    gen = _encode_gen(dit)
    gen._ensure_room_for_encode(torch.zeros(1, 3, 512, 512), "cpu")
    assert dit.offload_calls == 0


def _encode_image_gen_with_dispatch(dispatch):
    gen = object.__new__(NativeGenerator)
    gen.device_plan = SimpleNamespace(vae_device="cpu")
    gen.vae = SimpleNamespace(compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None)
    gen._ensure_room_for_encode = lambda pixels, device: None
    gen._encode_dispatch = dispatch
    gen.free_retry_calls = 0
    gen._free_for_decode_retry = lambda device: setattr(gen, "free_retry_calls", gen.free_retry_calls + 1)
    return gen


def test_encode_image_reactive_retry_fires_once_then_succeeds():
    calls = {"n": 0}

    def dispatch(pixels, device, vram):
        calls["n"] += 1
        if calls["n"] == 1:
            raise torch.cuda.OutOfMemoryError("boom")
        return torch.zeros(1, 16, 1, 8, 8)

    gen = _encode_image_gen_with_dispatch(dispatch)
    latent = gen.encode_image(np.zeros((64, 64, 3), dtype=np.uint8))
    assert calls["n"] == 2                 # first OOM'd, retry succeeded
    assert gen.free_retry_calls == 1       # freed our DiT/TE + foreign between attempts
    assert latent.grad_fn is None          # still under the no_grad guard


def test_encode_image_reactive_retry_reraises_on_second_oom():
    calls = {"n": 0}

    def dispatch(pixels, device, vram):
        calls["n"] += 1
        raise torch.cuda.OutOfMemoryError("boom")

    gen = _encode_image_gen_with_dispatch(dispatch)
    with pytest.raises(torch.cuda.OutOfMemoryError):
        gen.encode_image(np.zeros((64, 64, 3), dtype=np.uint8))
    assert calls["n"] == 2                 # tried exactly twice (no infinite retry)
    assert gen.free_retry_calls == 1


def test_native_model_unload_drops_module(dit_path):
    model = NativeEngineLoader().load(dit_path, "diffusion_model")
    model.unload()
    assert model.module is None


class _TinyLinearModule(torch.nn.Module):
    def __init__(self, in_f, out_f):
        super().__init__()
        self.linear = torch.nn.Linear(in_f, out_f, bias=False)
        self.register_buffer("scale", torch.ones(out_f))


def test_estimate_text_encoder_gb_sums_direct_module_params_and_buffers():
    """A single-encoder text encoder (Gemma3/Qwen3/T5XXL/CLIP-L shape: a bare
    `.module` attribute) must get a real, non-None byte estimate -- this is
    the exact field `NativeModel.move_to`/`offload`'s size-gated
    `trim_host_allocator()` calls read, and it used to be hardcoded `None`
    for every text encoder (see `_load_te`'s LTX RAM-ratchet fix)."""
    class _FakeEncoder:
        def __init__(self):
            self.module = _TinyLinearModule(1000, 2000)  # 1000*2000*4B = ~7.63MB + buffer

    est_gb = _estimate_text_encoder_gb(_FakeEncoder())
    assert est_gb is not None
    expected = (1000 * 2000 * 4 + 2000 * 4) / (1024**3)
    assert abs(est_gb - expected) < 1e-6


def test_estimate_text_encoder_gb_sums_flux1_composite_t5_and_clip():
    """The Flux1 composite encoder (`.t5` + `.clip_l`, each with their own
    `.module`) must sum BOTH sub-modules' weights, not just one."""
    class _FakeSub:
        def __init__(self, module):
            self.module = module

    class _FakeComposite:
        def __init__(self):
            self.t5 = _FakeSub(_TinyLinearModule(500, 500))
            self.clip_l = _FakeSub(_TinyLinearModule(300, 300))

    est_gb = _estimate_text_encoder_gb(_FakeComposite())
    assert est_gb is not None
    expected = ((500 * 500 * 4 + 500 * 4) + (300 * 300 * 4 + 300 * 4)) / (1024**3)
    assert abs(est_gb - expected) < 1e-6


def test_estimate_text_encoder_gb_returns_none_for_unmeasurable_encoder():
    """A stub/fake encoder with no `.module`/`.t5`/`.clip_l` at all (e.g. this
    file's own `_StubTE` test double) must return None, not 0.0 -- callers
    treat None as "unknown" and 0.0 as "definitely tiny", and those are NOT
    the same claim."""
    assert _estimate_text_encoder_gb(_StubTE()) is None


def test_move_to_trims_host_allocator_after_streamer_teardown_regardless_of_destination():
    """A NativeModel transitioning OUT of partial residency (an active
    streamer torn down) must trim glibc's heap regardless of which device the
    move is headed to.

    Teardown itself materialises a fresh CPU copy of every leaf -- including
    ones that were previously GPU-resident, and previously-PINNED streamed
    leaves get a fresh UNPINNED copy (see memory/partial.py's
    ``_move_own_tensors``) -- before this same ``move_to()`` re-uploads
    everything. That churn is CPU-side even when the final destination is
    cuda, so gating the trim on "destination is cpu" (as the plain
    non-streamer offload branch does) missed it entirely: a partial->full DiT
    restore (``dit_restore.py``'s warm-start) measured a real RSS INCREASE
    during exactly this transition because it was never trimmed.
    """
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls = []
    original = lifecycle_manager.trim_host_allocator
    monkeypatch_target = "trim_host_allocator"

    def _fake_trim():
        calls.append(1)

    setattr(lifecycle_manager, monkeypatch_target, _fake_trim)
    try:
        class _FakeModule:
            def to(self, device):
                self.last_device = str(device)
                return self

        class _FakeStreamer:
            def __init__(self):
                self.active = True
                self.torn_down = False
                self.pinned_gb = 0.0

            def teardown(self):
                self.torn_down = True
                self.active = False

        model = NativeModel("diffusion_model", _FakeModule(), estimated_vram_gb=5.0)
        fake_streamer = _FakeStreamer()
        model._streamer = fake_streamer

        model.move_to("cuda:0")

        assert fake_streamer.torn_down
        assert calls, "trim_host_allocator must fire when tearing down an active streamer, even moving to cuda"
    finally:
        setattr(lifecycle_manager, monkeypatch_target, original)


def test_move_to_skips_trim_when_no_streamer_was_active():
    """The trim added for streamer-teardown must not fire on a plain move with
    no partial-residency history -- it's gated on ``teardown_from_streamer``,
    not on every ``move_to`` call. ``estimated_vram_gb`` is kept under the
    size gate here so the (separate) big-component to-cuda trim -- see
    ``test_move_to_cuda_trims_host_allocator_for_big_components`` -- doesn't
    also fire and confound this test's own assertion."""
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls = []
    original = lifecycle_manager.trim_host_allocator
    setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append(1))
    try:
        class _FakeModule:
            def to(self, device):
                return self

        model = NativeModel("diffusion_model", _FakeModule(), estimated_vram_gb=1.0)
        model.move_to("cuda:0")
        assert not calls
    finally:
        setattr(lifecycle_manager, "trim_host_allocator", original)


def test_move_to_cuda_trims_host_allocator_for_big_components():
    """The old CPU copy released on the way TO the GPU fragments the glibc
    heap exactly like the way-down (offload) direction does -- see
    ``trim_host_allocator``'s docstring (a warm LTX ``move_to(cuda)`` dropped
    RSS by only 0.87GB where ~23GB of CPU weights were released). Mirrors the
    offload branch's existing >2GB gate, and mirrors ``stream_to``'s own
    to-cuda trim for the partial-residency path."""
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls = []
    original = lifecycle_manager.trim_host_allocator
    setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append(1))
    try:
        class _FakeModule:
            def to(self, device):
                return self

        model = NativeModel("diffusion_model", _FakeModule(), estimated_vram_gb=23.0)
        model.move_to("cuda:0")
        assert calls == [1]
    finally:
        setattr(lifecycle_manager, "trim_host_allocator", original)


def test_offload_trims_host_allocator_after_streamer_teardown():
    """`offload()` has its OWN early-return branch for an active streamer
    (it never reaches `move_to()`), so `move_to()`'s teardown-trim fix does
    NOT cover it. RAM ratchet fix: a maintainer capture
    caught RSS climbing 69.8GB -> 74.1GB in the ~6s between the
    `native.offload` mark and the `streamer.teardown` mark it emits,
    immediately preceding an earlyoom kill -- exactly this untrimmed path."""
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls = []
    original = lifecycle_manager.trim_host_allocator
    setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append(1))
    try:
        class _FakeModule:
            def to(self, device):
                return self

        class _FakeStreamer:
            def __init__(self):
                self.active = True
                self.pinned_gb = 0.0

            def teardown(self):
                self.active = False

        model = NativeModel("diffusion_model", _FakeModule(), estimated_vram_gb=23.3)
        model._streamer = _FakeStreamer()

        model.offload()

        assert not model._streamer.active
        assert model.device == "cpu"
        assert calls, "trim_host_allocator must fire when offload() tears down an active streamer"
    finally:
        setattr(lifecycle_manager, "trim_host_allocator", original)


def test_offload_skips_trim_below_the_size_gate():
    """Same gate as move_to()/stream_to(): a component below the 2GB
    threshold isn't worth the trim cost."""
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls = []
    original = lifecycle_manager.trim_host_allocator
    setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append(1))
    try:
        class _FakeModule:
            def to(self, device):
                return self

        class _FakeStreamer:
            def __init__(self):
                self.active = True
                self.pinned_gb = 0.0

            def teardown(self):
                self.active = False

        model = NativeModel("diffusion_model", _FakeModule(), estimated_vram_gb=1.0)
        model._streamer = _FakeStreamer()

        model.offload()

        assert not calls
    finally:
        setattr(lifecycle_manager, "trim_host_allocator", original)


def test_unload_trims_host_allocator_after_streamer_teardown():
    """Same gap as offload()'s fix 3/3, for the lifecycle-eviction path:
    unload() tears down an active streamer inline and never routed through
    move_to() either."""
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls = []
    original = lifecycle_manager.trim_host_allocator
    setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append(1))
    try:
        class _FakeModule:
            def to(self, device):
                return self

        class _FakeStreamer:
            def __init__(self):
                self.active = True
                self.pinned_gb = 0.0

            def teardown(self):
                self.active = False

        model = NativeModel("diffusion_model", _FakeModule(), estimated_vram_gb=23.3)
        model._streamer = _FakeStreamer()

        model.unload()

        assert model.module is None
        assert calls, "trim_host_allocator must fire when unload() tears down an active streamer"
    finally:
        setattr(lifecycle_manager, "trim_host_allocator", original)


def test_unload_skips_trim_when_no_streamer_was_active():
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls = []
    original = lifecycle_manager.trim_host_allocator
    setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append(1))
    try:
        class _FakeModule:
            def to(self, device):
                return self

        model = NativeModel("diffusion_model", _FakeModule(), estimated_vram_gb=23.3)
        model.unload()

        assert model.module is None
        assert not calls
    finally:
        setattr(lifecycle_manager, "trim_host_allocator", original)


class _FakeStreamerForStreamTo:
    """Records `.apply()` calls into a shared list (so ordering against the
    pinned-cache/trim calls can be asserted) without touching real CUDA."""

    def __init__(self, calls):
        self._calls = calls
        self.active = False

    def apply(self, device, plan, *, non_blocking=True):
        self._calls.append("apply")
        self.active = True


def test_stream_to_empties_pinned_cache_before_apply_and_trims_after(monkeypatch):
    """RAM ratchet: entering partial residency must (1) release
    any stale cached pinned pool BEFORE the new pin burst (`apply()`), and (2)
    trim glibc's heap AFTER it -- mirroring `move_to()`'s post-teardown trim
    for the same "freed CPU allocations never returned to the OS" reason."""
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls: list[str] = []
    monkeypatch.setattr(lifecycle_manager, "empty_pinned_host_cache", lambda: calls.append("empty"))
    monkeypatch.setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append("trim"))

    model = NativeModel("diffusion_model", torch.nn.Linear(2, 2), estimated_vram_gb=5.0)
    model._streamer = _FakeStreamerForStreamTo(calls)

    model.stream_to("cuda:0", 1.0)

    assert calls == ["empty", "apply", "trim"], (
        "pinned cache must be emptied BEFORE the pin burst and glibc trimmed AFTER it"
    )


def test_stream_to_skips_trim_for_small_component(monkeypatch):
    """The post-apply trim is gated on component size (>2GB), same threshold
    as `move_to()`'s trim -- a small component isn't worth the tens-of-ms cost."""
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls: list[str] = []
    monkeypatch.setattr(lifecycle_manager, "empty_pinned_host_cache", lambda: calls.append("empty"))
    monkeypatch.setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append("trim"))

    model = NativeModel("vae", torch.nn.Linear(2, 2), estimated_vram_gb=0.5)
    model._streamer = _FakeStreamerForStreamTo(calls)

    model.stream_to("cuda:0", 1.0)

    assert calls == ["empty", "apply"]  # pinned cache still emptied; trim skipped


def test_stream_to_emits_host_reclaim_marks_for_both_empty_and_trim(monkeypatch):
    """Same GAP 1 coverage as the teardown-reclaim test above, for the OTHER
    named call site: `stream_to`'s pre-apply pinned-cache release AND its
    post-apply trim are BOTH now bracketed, each producing its own
    `host.reclaim` row distinguishable by `op` -- so the pinned-pool release
    is attributable to a specific call, not inferred from `apply` in between."""
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager
    import src.platform.runtime.native.engine as engine_module

    calls: list[str] = []
    monkeypatch.setattr(lifecycle_manager, "empty_pinned_host_cache", lambda: calls.append("empty"))
    monkeypatch.setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append("trim"))
    monkeypatch.setattr(engine_module, "profiling_enabled", lambda: True)
    rec = _RecordingProfilerForTrim()
    monkeypatch.setattr(engine_module, "get_profiler", lambda: rec)
    rss_values = iter([20.0, 19.8, 19.8, 18.25])
    monkeypatch.setattr(engine_module, "read_process_rss_gb", lambda: next(rss_values))

    model = NativeModel("diffusion_model", torch.nn.Linear(2, 2), estimated_vram_gb=5.0)
    model._streamer = _FakeStreamerForStreamTo(calls)

    model.stream_to("cuda:0", 1.0)

    assert calls == ["empty", "apply", "trim"]
    reclaim_events = [fields for event, fields in rec.events if event == "host.reclaim"]
    assert len(reclaim_events) == 2
    assert reclaim_events[0]["op"] == "empty_pinned_host_cache"
    assert reclaim_events[0]["site"] == "stream_to"
    assert reclaim_events[0]["rss_before_reclaim_gb"] == 20.0
    assert reclaim_events[0]["rss_after_reclaim_gb"] == 19.8
    assert reclaim_events[1]["op"] == "trim_host_allocator"
    assert reclaim_events[1]["site"] == "stream_to"
    assert reclaim_events[1]["rss_before_reclaim_gb"] == 19.8
    assert reclaim_events[1]["rss_after_reclaim_gb"] == 18.25


def test_stream_to_on_non_cuda_device_never_touches_pinned_cache_or_trim(monkeypatch):
    """`stream_to` on a non-CUDA device degrades to a plain `move_to` -- must
    not call either the pinned-cache release or the glibc trim from the
    partial-residency path (move_to has its own, independently-gated trim)."""
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls: list[str] = []
    monkeypatch.setattr(lifecycle_manager, "empty_pinned_host_cache", lambda: calls.append("empty"))

    class _FakeModule:
        def to(self, device):
            return self

    model = NativeModel("diffusion_model", _FakeModule(), estimated_vram_gb=5.0)
    model.stream_to("cpu", 1.0)

    assert "empty" not in calls


def _reclaim_calls(monkeypatch):
    """Route both host-reclaim primitives into one ordered call list."""
    import src.platform.runtime.model_lifecycle.manager as lifecycle_manager

    calls: list[str] = []
    monkeypatch.setattr(lifecycle_manager, "trim_host_allocator", lambda: calls.append("trim"))
    monkeypatch.setattr(lifecycle_manager, "empty_pinned_host_cache", lambda: calls.append("empty"))
    return calls


def test_reclaim_releases_pinned_pool_when_teardown_vacated_a_large_pin(monkeypatch):
    """The amplifier fix: a teardown that vacated a LARGE page-locked pool (the
    co-tenant-OOM degrade pins ~the whole DiT) must release CUDA's cached pinned
    pool -- `trim_host_allocator` (glibc-only) can't, so the vacated ~19GB would
    otherwise stack on top of the fresh unpinned weights and OOM-kill the box."""
    calls = _reclaim_calls(monkeypatch)
    model = NativeModel("diffusion_model", torch.nn.Linear(2, 2), estimated_vram_gb=19.0)

    model._reclaim_host_after_teardown(pinned_gb=19.0)

    assert calls == ["trim", "empty"]


def test_reclaim_keeps_warm_pinned_pool_for_a_small_streamed_tail(monkeypatch):
    """A steady-state modest streamed tail (below the release floor) keeps its
    warm pinned pool for a cheap re-pin next phase -- only glibc is trimmed."""
    calls = _reclaim_calls(monkeypatch)
    model = NativeModel("diffusion_model", torch.nn.Linear(2, 2), estimated_vram_gb=19.0)

    model._reclaim_host_after_teardown(pinned_gb=1.0)  # < _PINNED_RELEASE_FLOOR_GB

    assert calls == ["trim"]


def test_reclaim_is_a_noop_below_the_size_gate(monkeypatch):
    """A component under the 2GB gate isn't worth either reclaim primitive."""
    calls = _reclaim_calls(monkeypatch)
    model = NativeModel("vae", torch.nn.Linear(2, 2), estimated_vram_gb=1.0)

    model._reclaim_host_after_teardown(pinned_gb=19.0)

    assert calls == []


class _RecordingProfilerForTrim:
    """Captures ``mark`` calls so a test can assert the ``host.reclaim``
    event's fields without a real profile.jsonl (mirrors the
    ``_RecordingProfiler`` pattern used elsewhere in this codebase's test
    suite)."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def mark(self, event, **fields):
        self.events.append((event, fields))


def test_reclaim_emits_host_reclaim_mark_with_rss_before_after(monkeypatch):
    """GAP 1 (trim effectiveness invisibility): while profiling is enabled,
    the teardown reclaim's trim is bracketed by an RSS read on each side so a
    report can tell "trim ran and did nothing" apart from "trim wasn't
    reached" -- see `_reclaim_with_marks`."""
    import src.platform.runtime.native.engine as engine_module

    monkeypatch.setattr(engine_module, "profiling_enabled", lambda: True)
    rec = _RecordingProfilerForTrim()
    monkeypatch.setattr(engine_module, "get_profiler", lambda: rec)
    rss_values = iter([10.0, 9.5])
    monkeypatch.setattr(engine_module, "read_process_rss_gb", lambda: next(rss_values))

    calls = _reclaim_calls(monkeypatch)
    model = NativeModel("diffusion_model", torch.nn.Linear(2, 2), estimated_vram_gb=19.0)

    model._reclaim_host_after_teardown(pinned_gb=1.0)  # below the pinned-release floor

    assert calls == ["trim"]
    reclaim_events = [fields for event, fields in rec.events if event == "host.reclaim"]
    assert len(reclaim_events) == 1
    assert reclaim_events[0]["site"] == "teardown"
    assert reclaim_events[0]["op"] == "trim_host_allocator"
    assert reclaim_events[0]["rss_before_reclaim_gb"] == 10.0
    assert reclaim_events[0]["rss_after_reclaim_gb"] == 9.5


def test_reclaim_skips_rss_reads_when_profiling_disabled(monkeypatch):
    """The RSS reads (not just the mark write) must be skipped when profiling
    is off -- they are real /proc reads, not free, and this path runs on
    every big-component offload."""
    import src.platform.runtime.native.engine as engine_module

    monkeypatch.setattr(engine_module, "profiling_enabled", lambda: False)

    def _boom():
        raise AssertionError("read_process_rss_gb must not be called while profiling is disabled")

    monkeypatch.setattr(engine_module, "read_process_rss_gb", _boom)

    calls = _reclaim_calls(monkeypatch)
    model = NativeModel("diffusion_model", torch.nn.Linear(2, 2), estimated_vram_gb=19.0)

    model._reclaim_host_after_teardown(pinned_gb=1.0)

    assert calls == ["trim"]


def test_offload_releases_pinned_pool_after_a_large_pinned_teardown(monkeypatch):
    """End-to-end through offload(): a DiT that streamed a large pinned set and
    is offloaded before decode releases the pinned pool, not just trims."""
    calls = _reclaim_calls(monkeypatch)

    class _FakeModule:
        def to(self, device):
            return self

    class _FakeStreamer:
        def __init__(self):
            self.active = True
            self.pinned_gb = 19.0

        def teardown(self):
            self.active = False

    model = NativeModel("diffusion_model", _FakeModule(), estimated_vram_gb=19.0)
    model._streamer = _FakeStreamer()

    model.offload()

    assert model.device == "cpu"
    assert calls == ["trim", "empty"]


def test_guard_host_ram_raises_when_streamed_set_wont_survive(monkeypatch):
    """The survivability floor: refuse to pin a streamed set the free host RAM
    can't absorb -- a clean HostMemoryExhaustedError beats an OS OOM-kill."""
    import src.platform.runtime.native.engine as engine_mod
    from src.platform.runtime.native.errors import HostMemoryExhaustedError
    from src.platform.runtime.system_memory import SystemMemory

    monkeypatch.setattr(
        engine_mod, "get_system_memory",
        lambda: SystemMemory(total=32 * 1024 ** 3, available=int(10 * 1024 ** 3)),
    )
    model = NativeModel("diffusion_model", torch.nn.Linear(2, 2), estimated_vram_gb=19.0)

    class _Plan:
        streamed_gb = 19.0  # 19 + reserve > 10GB free -> refuse

    with pytest.raises(HostMemoryExhaustedError):
        model._guard_host_ram_for_streaming(_Plan())


def test_guard_host_ram_allows_a_streamed_set_that_fits(monkeypatch):
    """A streamed set comfortably inside free RAM must NOT trip the floor --
    healthy steady-state partial residency never sees it."""
    import src.platform.runtime.native.engine as engine_mod
    from src.platform.runtime.system_memory import SystemMemory

    monkeypatch.setattr(
        engine_mod, "get_system_memory",
        lambda: SystemMemory(total=128 * 1024 ** 3, available=int(100 * 1024 ** 3)),
    )
    model = NativeModel("diffusion_model", torch.nn.Linear(2, 2), estimated_vram_gb=19.0)

    class _Plan:
        streamed_gb = 6.0

    model._guard_host_ram_for_streaming(_Plan())  # must not raise


def test_guard_host_ram_noop_for_fully_resident_plan(monkeypatch):
    """A plan that streams nothing pins nothing -- never queries host RAM."""
    import src.platform.runtime.native.engine as engine_mod

    def _boom():
        raise AssertionError("host RAM must not be queried when nothing streams")

    monkeypatch.setattr(engine_mod, "get_system_memory", _boom)
    model = NativeModel("diffusion_model", torch.nn.Linear(2, 2), estimated_vram_gb=19.0)

    class _Plan:
        streamed_gb = 0.0

    model._guard_host_ram_for_streaming(_Plan())  # must not raise or query


def test_end_to_end_sample_and_decode(dit_path, vae_path):
    loader = NativeEngineLoader(device="cpu")
    dit = loader.load(dit_path, "diffusion_model")
    vae = loader.load(vae_path, "vae")
    te = _StubTE(context_dim=_FLUX1["context_in_dim"])

    gen = NativeGenerator(dit, te, vae)

    conditioning = gen.encode_prompt("a tiny test prompt")
    assert isinstance(conditioning, Conditioning)
    # normalized cond-dict contract: context/y/attention_mask
    assert "context" in conditioning.cond
    assert "y" in conditioning.cond and conditioning.cond["y"] is not None  # Flux1 pooled

    latent = gen.sample(
        conditioning,
        latents_shape=(1, 16, 16, 16),
        steps=2,
        seed=1234,
        cfg_scale=3.5,
        sampler="euler",
    )
    assert latent.shape == (1, 16, 16, 16)
    assert torch.isfinite(latent).all()

    images = gen.decode(latent)
    assert isinstance(images, np.ndarray)
    assert images.dtype == np.uint8
    # flux_ae is 8x spatial upscale: 16 -> 128, channels-last.
    assert images.shape == (1, 128, 128, 3)


def _capture_denoise_kwargs(monkeypatch, gen, **sample_overrides):
    """Patch engine.denoise to record its kwargs and return a valid latent."""
    import src.platform.runtime.native.engine as engine

    captured = {}
    shape = (1, 16, 16, 16)

    def _fake_denoise(model_forward, latents, cond, uncond, **kwargs):
        captured.update(kwargs)
        return torch.zeros(shape)

    monkeypatch.setattr(engine, "denoise", _fake_denoise)
    cond = gen.encode_prompt("prompt")
    gen.sample(cond, latents_shape=shape, steps=2, seed=1, cfg_scale=3.0, **sample_overrides)
    return captured


def test_sample_forwards_guidance_options_to_denoise(dit_path, vae_path, monkeypatch):
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(
        loader.load(dit_path, "diffusion_model"),
        _StubTE(context_dim=_FLUX1["context_in_dim"]),
        loader.load(vae_path, "vae"),
    )
    captured = _capture_denoise_kwargs(
        monkeypatch, gen,
        guidance_options={"cfg_zero_star": False, "zero_init_steps": 2},
    )
    assert captured["cfg_zero_star"] is False
    assert captured["zero_init_steps"] == 2


def test_sample_guidance_options_default_preserves_behaviour(dit_path, vae_path, monkeypatch):
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(
        loader.load(dit_path, "diffusion_model"),
        _StubTE(context_dim=_FLUX1["context_in_dim"]),
        loader.load(vae_path, "vae"),
    )
    # No guidance_options -> engine defaults (cfg_zero_star on, zero_init_steps 0),
    # matching denoise()'s own defaults so existing presets are unaffected.
    captured = _capture_denoise_kwargs(monkeypatch, gen)
    assert captured["cfg_zero_star"] is True
    assert captured["zero_init_steps"] == 0


def test_sample_forwards_sampler_options_and_step_cache_options_to_denoise(dit_path, vae_path, monkeypatch):
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(
        loader.load(dit_path, "diffusion_model"),
        _StubTE(context_dim=_FLUX1["context_in_dim"]),
        loader.load(vae_path, "vae"),
    )
    sampler_options = {"eta": 0.5}
    step_cache_options = {"rel_threshold": 0.12, "warmup_steps": 2, "max_consecutive_skips": 2}
    captured = _capture_denoise_kwargs(
        monkeypatch, gen,
        sampler_options=sampler_options,
        step_cache_options=step_cache_options,
    )
    assert captured["sampler_options"] == sampler_options
    assert captured["step_cache_options"] == step_cache_options


# -- explicit `sigmas=` at the sample() boundary -------------------

def test_sample_forwards_explicit_sigmas_verbatim_and_derives_effective_steps(dit_path, vae_path, monkeypatch):
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(
        loader.load(dit_path, "diffusion_model"),
        _StubTE(context_dim=_FLUX1["context_in_dim"]),
        loader.load(vae_path, "vae"),
    )
    explicit = [1.0, 0.75, 0.5, 0.25, 0.0]  # 4 effective steps
    captured = _capture_denoise_kwargs(monkeypatch, gen, sigmas=explicit)
    assert torch.equal(captured["sigmas"], torch.tensor(explicit, dtype=torch.float32))
    # nominal `steps=2` (from _capture_denoise_kwargs) is overridden: effective
    # steps is len(sigmas) - 1, forwarded as denoise()'s own `steps` kwarg.
    assert captured["steps"] == 4


def test_sample_sigmas_default_is_none_and_byte_identical(dit_path, vae_path, monkeypatch):
    gen = _apg_gen(dit_path, vae_path)
    captured = _capture_denoise_kwargs(monkeypatch, gen)
    assert captured["sigmas"] is None
    assert captured["steps"] == 2  # untouched nominal steps


@pytest.mark.parametrize("bad_sigmas,match", [
    ([[1.0, 0.5, 0.0]], "1-D"),                      # 2-D
    ([1.0], "at least 2"),                           # too short
    ([0.5, 1.0, 0.0], "strictly decreasing"),        # not monotonic
    ([1.2, 0.5, 0.0], "<= 1.0"),                      # sigma0 > 1.0
    ([1.0, 0.5, 0.1], "0.0"),                         # tail != 0.0
])
def test_sample_rejects_invalid_explicit_sigmas(dit_path, vae_path, monkeypatch, bad_sigmas, match):
    gen = _apg_gen(dit_path, vae_path)
    import src.platform.runtime.native.engine as engine_mod
    monkeypatch.setattr(engine_mod, "denoise", lambda *a, **kw: torch.zeros(1, 16, 16, 16))
    cond = gen.encode_prompt("prompt")
    with pytest.raises(ValueError, match=match):
        gen.sample(cond, latents_shape=(1, 16, 16, 16), steps=2, seed=1, cfg_scale=3.0, sigmas=bad_sigmas)


def test_sample_accepts_sigma0_below_one_and_equal_to_one(dit_path, vae_path, monkeypatch):
    gen = _apg_gen(dit_path, vae_path)
    # sigma0 < 1.0 (a genuine partial-noise refine start) and sigma0 == 1.0
    # (a full-schedule explicit list) are both valid -- only sigma0 > 1.0 rejects.
    captured = _capture_denoise_kwargs(monkeypatch, gen, sigmas=[0.5, 0.25, 0.0])
    assert captured["sigmas"] is not None
    captured = _capture_denoise_kwargs(monkeypatch, gen, sigmas=[1.0, 0.5, 0.0])
    assert captured["sigmas"] is not None


def test_sample_explicit_sigmas_skips_spectral_progressive(dit_path, vae_path, monkeypatch):
    import src.platform.runtime.native.engine as engine_mod

    def _boom(*a, **kw):
        raise AssertionError("spectral-progressive must not engage with explicit sigmas")

    monkeypatch.setattr(NativeGenerator, "_sample_spectral_progressive", _boom)
    gen = _apg_gen(dit_path, vae_path)
    captured = _capture_denoise_kwargs(
        monkeypatch, gen, sigmas=[1.0, 0.5, 0.0],
        spectral_progressive={"enabled": True, "scales": [0.5, 1.0], "transitions": [1]},
    )
    assert captured["sigmas"] is not None


def _apg_gen(dit_path, vae_path):
    loader = NativeEngineLoader(device="cpu")
    return NativeGenerator(
        loader.load(dit_path, "diffusion_model"),
        _StubTE(context_dim=_FLUX1["context_in_dim"]),
        loader.load(vae_path, "vae"),
    )


def test_sample_merges_apg_from_guidance_options_into_sampling_settings(dit_path, vae_path, monkeypatch):
    gen = _apg_gen(dit_path, vae_path)
    captured = _capture_denoise_kwargs(
        monkeypatch, gen,
        guidance_options={
            "cfg_zero_star": False, "apg_eta": 0.5, "apg_norm_threshold": 2.5,
            "apg_momentum": -0.5, "slg_scale": 3.0, "slg_layers": [9],
        },
    )
    ss = captured["sampling_settings"]
    assert ss["apg_eta"] == 0.5 and ss["apg_norm_threshold"] == 2.5 and ss["apg_momentum"] == -0.5
    # slg_* is deliberately NOT merged on the image path (no image arch skip_layers).
    assert "slg_scale" not in ss and "slg_layers" not in ss
    # cfg_zero_star still forwarded as its own denoise kwarg (not via sampling_settings).
    assert captured["cfg_zero_star"] is False


def test_sample_merges_schedule_settings_into_sampling_settings(dit_path, vae_path, monkeypatch):
    gen = _apg_gen(dit_path, vae_path)
    captured = _capture_denoise_kwargs(
        monkeypatch, gen,
        schedule_settings={
            "schedule": "beta", "schedule_options": {"alpha": 0.6, "beta": 0.6},
            "detail_strength": 0.1, "detail_start": 0.2, "detail_end": 0.8,
            "unrelated": 99,
        },
    )
    ss = captured["sampling_settings"]
    assert ss["schedule"] == "beta" and ss["schedule_options"] == {"alpha": 0.6, "beta": 0.6}
    assert ss["detail_strength"] == 0.1 and ss["detail_start"] == 0.2 and ss["detail_end"] == 0.8
    assert "unrelated" not in ss   # only whitelisted schedule keys are merged


def test_sample_merges_fixed_mu_and_dynamic_shift_into_sampling_settings(dit_path, vae_path, monkeypatch):
    # BE-CFG-KREA2: lets a preset swap a ModelSpec's mu-shift SOURCE per
    # generation (Krea-2 turbo's fixed_mu=1.15 vs a raw/base checkpoint's
    # resolution-anchored dynamic mu) without a second ModelSpec.
    gen = _apg_gen(dit_path, vae_path)
    dynamic_shift = {"x1_px": 256, "x2_px": 1280, "y1": 0.5, "y2": 1.15, "align": 16}
    captured = _capture_denoise_kwargs(
        monkeypatch, gen,
        schedule_settings={"fixed_mu": None, "dynamic_shift": dynamic_shift},
    )
    ss = captured["sampling_settings"]
    assert ss["fixed_mu"] is None
    assert ss["dynamic_shift"] == dynamic_shift


def test_sample_sampling_settings_byte_identical_by_default(dit_path, vae_path, monkeypatch):
    gen = _apg_gen(dit_path, vae_path)
    captured = _capture_denoise_kwargs(monkeypatch, gen)   # no apg / schedule opts
    # The exact ModelSpec dict is passed through unchanged (same object) -> byte-identical.
    assert captured["sampling_settings"] is gen.spec.sampling_settings


def test_sample_sampler_options_and_step_cache_options_default_to_none(dit_path, vae_path, monkeypatch):
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(
        loader.load(dit_path, "diffusion_model"),
        _StubTE(context_dim=_FLUX1["context_in_dim"]),
        loader.load(vae_path, "vae"),
    )
    # Omitted -> forwarded as None, same as before this kwarg existed (denoise()'s
    # own defaults: no sampler_options, FBCache disabled).
    captured = _capture_denoise_kwargs(monkeypatch, gen)
    assert captured["sampler_options"] is None
    assert captured["step_cache_options"] is None


# Tiny Flux2/Klein config: global modulation, no pooled vector, no guidance
# embedding, latent 128 (out hardcoded 128 by detection), patch_size 1.
_FLUX2 = {
    "image_model": "flux2",
    "in_channels": 128,
    "out_channels": 128,
    "hidden_size": 128,
    "context_in_dim": 32,
    "num_heads": 1,
    "depth": 1,
    "depth_single_blocks": 1,
    "axes_dim": [32, 32, 32, 32],
    "mlp_ratio": 3.0,
    "theta": 2000,
    "patch_size": 1,
    "qkv_bias": False,
    "guidance_embed": False,
}


class _StubKleinTE(NativeTextEncoder):
    role = "stub_klein"

    def encode(self, texts):
        return {"context": torch.randn(len(texts), 4, 32)}  # no pooled for Klein


def test_mixed_dtype_dit_selects_manual_cast(tmp_path):
    """A Krea-2-style mixed bf16/f32 checkpoint must load with manual_cast so
    f32 peripheral weights don't crash against bf16 activations."""
    module = Flux.from_config(_FLUX1, disable_weight_init)
    sd = _finite_sd(module)  # all bf16
    # promote one weight to f32 to make the checkpoint mixed-dtype.
    a_key = next(k for k in sd if k.endswith("img_in.weight") or "weight" in k)
    sd[a_key] = sd[a_key].to(torch.float32)
    p = tmp_path / "mixed.safetensors"
    save_file(sd, str(p))

    dit = NativeEngineLoader(device="cpu").load(p, "diffusion_model")
    # manual_cast layers carry comfy_cast_weights=True; disable_weight_init is False.
    assert dit.module.img_in.comfy_cast_weights is True


def test_flux2_klein_path_loads_and_samples(tmp_path, vae_path):
    module = Flux.from_config(_FLUX2, disable_weight_init)
    p = tmp_path / "klein.safetensors"
    save_file(_finite_sd(module), str(p))

    loader = NativeEngineLoader(device="cpu")
    dit = loader.load(p, "diffusion_model")
    assert dit.spec.variant == "flux2"

    gen = NativeGenerator(dit, _StubKleinTE(), loader.load(vae_path, "vae"))
    cond = gen.encode_prompt("klein prompt")
    latent = gen.sample(cond, latents_shape=(1, 128, 16, 16), steps=2, seed=7, cfg_scale=4.0)
    assert latent.shape == (1, 128, 16, 16)
    assert torch.isfinite(latent).all()


def test_seed_is_deterministic(dit_path, vae_path):
    loader = NativeEngineLoader(device="cpu")
    dit = loader.load(dit_path, "diffusion_model")
    vae = loader.load(vae_path, "vae")
    gen = NativeGenerator(dit, _StubTE(), vae)

    cond = Conditioning({"context": torch.randn(1, 4, 32), "pooled": torch.randn(1, 768)})
    kw = dict(latents_shape=(1, 16, 16, 16), steps=2, seed=99, cfg_scale=3.5)
    a = gen.sample(cond, **kw)
    b = gen.sample(cond, **kw)
    assert torch.equal(a, b)


# -- seed determinism for stochastic samplers (task #40) --------------------
#
# euler_sde / dpmpp_2m_sde / lcm draw per-step noise from
# sampler_options['generator']; absent that key the draw falls back to the
# UNSEEDED global RNG, so a same-seed re-run would silently stop being
# reproducible the moment a preset picked one of these samplers. NativeGenerator
# .sample() must auto-populate 'generator' from the request's own seeded
# torch.Generator (reusing the SAME object that drew the init noise -- see
# ensure_sampler_generator's docstring for why not a second identically-seeded
# one).

@pytest.mark.parametrize("stochastic_sampler", ["euler_sde", "dpmpp_2m_sde", "lcm"])
def test_seed_is_deterministic_for_stochastic_samplers(dit_path, vae_path, stochastic_sampler):
    loader = NativeEngineLoader(device="cpu")
    dit = loader.load(dit_path, "diffusion_model")
    vae = loader.load(vae_path, "vae")
    gen = NativeGenerator(dit, _StubTE(), vae)

    cond = Conditioning({"context": torch.randn(1, 4, 32), "pooled": torch.randn(1, 768)})
    kw = dict(latents_shape=(1, 16, 16, 16), steps=3, seed=99, cfg_scale=3.5, sampler=stochastic_sampler)
    a = gen.sample(cond, **kw)
    b = gen.sample(cond, **kw)
    assert torch.equal(a, b)


def test_different_seeds_diverge_for_stochastic_sampler(dit_path, vae_path):
    loader = NativeEngineLoader(device="cpu")
    dit = loader.load(dit_path, "diffusion_model")
    vae = loader.load(vae_path, "vae")
    gen = NativeGenerator(dit, _StubTE(), vae)

    cond = Conditioning({"context": torch.randn(1, 4, 32), "pooled": torch.randn(1, 768)})
    kw = dict(latents_shape=(1, 16, 16, 16), steps=3, cfg_scale=3.5, sampler="euler_sde")
    a = gen.sample(cond, seed=99, **kw)
    b = gen.sample(cond, seed=100, **kw)
    assert not torch.equal(a, b)


def test_sample_populates_generator_for_stochastic_sampler(dit_path, vae_path, monkeypatch):
    gen = _apg_gen(dit_path, vae_path)
    captured = _capture_denoise_kwargs(monkeypatch, gen, sampler="euler_sde")
    assert isinstance(captured["sampler_options"]["generator"], torch.Generator)


def test_sample_does_not_populate_generator_for_deterministic_sampler(dit_path, vae_path, monkeypatch):
    gen = _apg_gen(dit_path, vae_path)
    captured = _capture_denoise_kwargs(monkeypatch, gen, sampler="euler")
    assert captured["sampler_options"] is None


def test_sample_explicit_generator_in_sampler_options_is_preserved(dit_path, vae_path, monkeypatch):
    explicit_generator = torch.Generator().manual_seed(4321)
    gen = _apg_gen(dit_path, vae_path)
    captured = _capture_denoise_kwargs(
        monkeypatch, gen, sampler="euler_sde",
        sampler_options={"eta": 0.5, "generator": explicit_generator},
    )
    assert captured["sampler_options"]["generator"] is explicit_generator
    assert captured["sampler_options"]["eta"] == 0.5


def test_sample_explicit_noise_tensor_leaves_sampler_options_untouched(dit_path, vae_path, monkeypatch):
    # An explicit `noise=` tensor (golden-comparison callers) has no
    # seed-derived generator to reuse -- sampler_options must be forwarded
    # exactly as given, with no 'generator' key injected.
    gen = _apg_gen(dit_path, vae_path)
    captured = _capture_denoise_kwargs(
        monkeypatch, gen, sampler="euler_sde",
        noise=torch.zeros(1, 16, 16, 16),
        sampler_options={"eta": 0.5},
    )
    assert captured["sampler_options"] == {"eta": 0.5}


def test_vae_dispatch_routes_causal3d_and_2d(vae_path, causal3d_vae_path):
    """NativeEngineLoader.load(kind='vae') must route to the right VAE family."""
    loader = NativeEngineLoader(device="cpu")
    flux = loader.load(vae_path, "vae")
    qwen = loader.load(causal3d_vae_path, "vae")
    assert not hasattr(flux.module, "decode_image")     # 2D Flux AE
    assert hasattr(qwen.module, "decode_image")          # causal-3D (Wan2.1/Qwen)


def test_latent_shape_for_qwen_is_5d():
    """Qwen (causal-3D VAE) latent is 5D (B,C,T=1,H//8,W//8) with 16 channels."""
    import types

    from src.platform.runtime.native.engine import NativeGenerator

    class _CausalVaeStub:
        def decode_image(self, x):  # marks this as a causal-3D VAE
            return x

    dit = types.SimpleNamespace(
        spec=types.SimpleNamespace(sampling_settings={}, latent_format={}, family="qwen_image", variant="qwen_image"),
        module=types.SimpleNamespace(params=types.SimpleNamespace(in_channels=64)),
        estimated_vram_gb=1.0, compute_dtype=torch.bfloat16, quant_format=None,
    )
    vae = types.SimpleNamespace(module=_CausalVaeStub(), estimated_vram_gb=0.3,
                                compute_dtype=torch.bfloat16, quant_format=None)
    gen = NativeGenerator.__new__(NativeGenerator)
    gen.dit, gen.vae, gen.spec = dit, vae, dit.spec
    assert gen.latent_shape_for(1024, 1024) == (1, 16, 1, 128, 128)
    assert gen.latent_shape_for(512, 768, batch=2) == (2, 16, 1, 96, 64)


def test_decode_causal3d_denormalizes_and_squeezes():
    """The causal-3D decode path applies per-channel wan21 denorm + squeezes T."""
    import types

    from src.platform.runtime.native.engine import NativeGenerator
    from src.platform.runtime.native.vae.causal_3d import LATENTS_MEAN, LATENTS_STD

    seen = {}

    class _CausalVaeStub:
        def decode_image(self, x):
            seen["input"] = x.clone()
            b, c, h, w = x.shape
            return torch.zeros(b, 3, h * 8, w * 8)

    gen = NativeGenerator.__new__(NativeGenerator)
    gen.spec = types.SimpleNamespace(latent_format={"format": "wan21", "latent_channels": 16})
    latent = torch.ones(1, 16, 1, 4, 4)                  # 5D, T=1
    out = gen._decode_causal3d(_CausalVaeStub(), latent, device="cpu")
    assert seen["input"].shape == (1, 16, 4, 4)          # T squeezed
    # channel 0 denorm = 1 * std[0] + mean[0]
    expected0 = 1.0 * LATENTS_STD[0] + LATENTS_MEAN[0]
    assert abs(seen["input"][0, 0, 0, 0].item() - expected0) < 1e-4
    assert out.shape == (1, 3, 32, 32)


def test_phase_sequencing_and_oom_retry():
    """Fake-CUDA test: assert TE offloads after encode, DiT offloads before a
    non-resident decode, and decode OOM triggers a free+retry."""
    import types

    from src.platform.runtime.native.engine import NativeGenerator
    from src.platform.runtime.native.memory.device_plan import DevicePlan
    from src.platform.runtime.native.memory.tiering import ComponentPlacement, PlacementPlan
    from src.platform.runtime.native.text_encoders.base import NativeTextEncoder

    log: list = []

    class _RecModel:
        def __init__(self, name):
            self._name = name
            self.spec = None
            self.estimated_vram_gb = 18.0
            self.compute_dtype = torch.bfloat16
            self.quant_format = None
            self.module = types.SimpleNamespace()

        def move_to(self, d):
            log.append((self._name, "move_to", str(d)))

        def offload(self):
            log.append((self._name, "offload"))

    class _RecTE(NativeTextEncoder):
        def encode(self, texts):
            return {"context": torch.zeros(1, 4, 8), "attention_mask": torch.ones(1, 4)}

        def to(self, d):
            log.append(("te", "to", str(d)))
            return self

    dit = _RecModel("dit")
    dit.spec = types.SimpleNamespace(
        sampling_settings={"guidance": "embedded"}, latent_format={},
        family="flux", variant="flux2",
    )
    vae = _RecModel("vae")
    dp = DevicePlan("cuda:0", "cuda:0", "cuda:0")
    gen = NativeGenerator(dit, _RecTE(), vae, dp, vram_gb=20.0)

    # (1) encode offloads the TE (last te move is to cpu).
    gen.encode_prompt("hi")
    te_moves = [e for e in log if e[0] == "te"]
    assert te_moves[-1] == ("te", "to", "cpu")

    # (2) decode with a DiT-non-resident placement offloads the DiT first.
    gen.placement = PlacementPlan(
        dit=ComponentPlacement("cuda:0", "standard", resident=False),
        text_encoder=ComponentPlacement("cuda:0", "standard", resident=False),
        vae=ComponentPlacement("cuda:0", "standard", resident=True),
        vae_tiling=False, tier="component_offload",
    )
    calls = {"decode_once": 0}
    import numpy as np

    def fake_decode_once(latents, *, vram_free_gb=None):
        calls["decode_once"] += 1
        if calls["decode_once"] == 1:
            raise torch.cuda.OutOfMemoryError("simulated")
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)

    gen._decode_once = fake_decode_once
    log.clear()
    out = gen.decode(torch.zeros(1, 128, 8, 8))

    assert out.shape == (1, 8, 8, 3)
    assert calls["decode_once"] == 2                 # OOM -> retry
    assert ("dit", "offload") in log                 # DiT freed before/at decode


def _causal3d_tiled_gen(vae_module):
    """A bare NativeGenerator wired for the causal-3D tiled-decode helpers:
    a stub VAE module, a no-op NativeModel-like wrapper, no placement (all
    resident)."""
    import types

    from src.platform.runtime.native.engine import NativeGenerator

    vae = types.SimpleNamespace(
        module=vae_module, estimated_vram_gb=0.3, compute_dtype=torch.float32,
        move_to=lambda d: None, offload=lambda: None,
    )
    gen = NativeGenerator.__new__(NativeGenerator)
    gen.vae = vae
    gen.spec = types.SimpleNamespace(latent_format={})
    gen.placement = None
    return gen


def test_decode_causal3d_tiled_shrinks_on_oom():
    """The tiled causal-3D decode halves the tile on OOM and retries until it
    fits (a 12GB-card-style spike that only fits at a smaller tile)."""

    class _ShrinkVae:
        def __init__(self):
            self.widths_seen = []
            self.oomed = False

        def decode_image(self, x):  # marks this as a causal-3D VAE
            return x

        def decode(self, x):
            b, c, t, h, w = x.shape
            self.widths_seen.append(w)
            # OOM once on the first (largest-tile) attempt, then succeed.
            if not self.oomed:
                self.oomed = True
                raise torch.cuda.OutOfMemoryError("simulated decode spike")
            return torch.zeros(b, 3, t, h * 8, w * 8)

    module = _ShrinkVae()
    gen = _causal3d_tiled_gen(module)
    latent = torch.randn(1, 16, 1, 64, 64)             # start tile ~32, shrinks to 16

    out = gen._decode_causal3d_tiled(latent, device="cpu")

    assert out.shape == (1, 512, 512, 3)               # 64 latent * 8 -> 512 px, HWC uint8
    # The first (OOM) attempt used a larger tile than the successful retry.
    assert module.widths_seen[0] > min(w for w in module.widths_seen[1:])


def test_decode_causal3d_tiled_propagates_oom_at_floor():
    """A spike that won't fit even at the minimum tile is a genuine capacity
    limit — the OOM propagates rather than looping forever."""
    import pytest

    class _AlwaysOomVae:
        def decode_image(self, x):
            return x

        def decode(self, x):
            raise torch.cuda.OutOfMemoryError("never fits")

    gen = _causal3d_tiled_gen(_AlwaysOomVae())
    latent = torch.randn(1, 16, 1, 64, 64)
    with pytest.raises(torch.cuda.OutOfMemoryError):
        gen._decode_causal3d_tiled(latent, device="cpu")


def test_causal3d_decode_fits_true_when_vram_unqueryable():
    """On CPU / when VRAM can't be queried, never proactively tile (the untiled
    path stays byte-identical)."""
    import types

    from src.platform.runtime.native.engine import NativeGenerator

    gen = NativeGenerator.__new__(NativeGenerator)
    gen.vae = types.SimpleNamespace(estimated_vram_gb=0.3)
    gen.placement = None
    assert gen._causal3d_decode_fits(torch.zeros(1, 16, 1, 128, 128), device="cpu") is True


def test_release_gpu_offloads_models_best_effort():
    """release_gpu offloads DiT + VAE and the TE, and never raises even if a
    component's offload fails (cleanup must not mask the original error)."""
    import types

    from src.platform.runtime.native.engine import NativeGenerator

    log = []

    class _Model:
        def __init__(self, name, fail=False):
            self._name = name
            self._fail = fail

        def offload(self):
            log.append(self._name)
            if self._fail:
                raise RuntimeError("offload failed")

    gen = NativeGenerator.__new__(NativeGenerator)
    gen.dit = _Model("dit", fail=True)                 # even a failing offload is swallowed
    gen.vae = _Model("vae")
    gen.device_plan = types.SimpleNamespace(dit_device="cpu")  # _maybe_offload_te no-ops on cpu
    gen.te = types.SimpleNamespace()

    gen.release_gpu()                                   # must not raise
    assert "dit" in log and "vae" in log


def test_latent_shape_for_flux1(dit_path, vae_path):
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(loader.load(dit_path, "diffusion_model"), _StubTE(), loader.load(vae_path, "vae"))
    # flux1: 16 latent channels at image //8 (16/16 z-ratio -> no extra fold).
    assert gen.latent_shape_for(128, 128) == (1, 16, 16, 16)
    assert gen.latent_shape_for(256, 128, batch=2) == (2, 16, 16, 32)


def test_sample_accepts_bare_dict_and_tuple(dit_path, vae_path):
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(loader.load(dit_path, "diffusion_model"), _StubTE(), loader.load(vae_path, "vae"))
    cond = {"context": torch.randn(1, 4, 32), "y": torch.randn(1, 768)}
    kw = dict(latents_shape=(1, 16, 16, 16), steps=2, seed=1, cfg_scale=3.5)
    from_dict = gen.sample(cond, **kw)
    from_tuple = gen.sample((cond, None), **kw)
    assert torch.equal(from_dict, from_tuple)


def test_encode_image_2d_shape_matches_latent_shape_for(dit_path, vae_path):
    """encode_image (Flux 2D AE) returns a latent whose shape drops straight into
    sample(init_latent=...) — i.e. equals latent_shape_for()."""
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(loader.load(dit_path, "diffusion_model"), _StubTE(), loader.load(vae_path, "vae"))
    img = np.zeros((1, 128, 128, 3), dtype=np.uint8)     # shape decode() emits
    latent = gen.encode_image(img)
    assert latent.shape == gen.latent_shape_for(128, 128) == (1, 16, 16, 16)
    assert torch.isfinite(latent).all()


def test_encode_image_accepts_hwc_and_tensor(dit_path, vae_path):
    """A single HWC uint8 frame is auto-batched; a pre-normalized [-1,1] tensor is
    taken as-is. Both yield the model-native latent rank."""
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(loader.load(dit_path, "diffusion_model"), _StubTE(), loader.load(vae_path, "vae"))
    from_hwc = gen.encode_image(np.zeros((128, 128, 3), dtype=np.uint8))   # unbatched HWC
    from_tensor = gen.encode_image(torch.full((1, 3, 128, 128), -1.0))     # [-1,1] BCHW
    assert from_hwc.shape == from_tensor.shape == (1, 16, 16, 16)
    # a black uint8 frame (0) maps to -1.0, same pixels as the -1.0 tensor -> equal latent.
    assert torch.allclose(from_hwc, from_tensor, atol=1e-4)


def test_init_latent_ignored_at_strength_one(dit_path, vae_path):
    """The txt2img invariant: at denoise_strength==1 sigma0==1 so x==noise and the
    init latent is irrelevant — an img2img call must equal the txt2img call."""
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(loader.load(dit_path, "diffusion_model"), _StubTE(), loader.load(vae_path, "vae"))
    cond = Conditioning({"context": torch.randn(1, 4, 32), "pooled": torch.randn(1, 768)})
    kw = dict(latents_shape=(1, 16, 16, 16), steps=2, seed=42, cfg_scale=3.5)
    baseline = gen.sample(cond, **kw)
    with_init = gen.sample(cond, init_latent=torch.randn(1, 16, 16, 16) * 5.0, denoise_strength=1.0, **kw)
    assert torch.equal(baseline, with_init)


def test_init_latent_changes_output_below_strength_one(dit_path, vae_path):
    """Below strength 1 the init latent blends in (x = sigma0*noise + (1-sigma0)*init),
    so an img2img run must diverge from the truncated-schedule txt2img run."""
    loader = NativeEngineLoader(device="cpu")
    gen = NativeGenerator(loader.load(dit_path, "diffusion_model"), _StubTE(), loader.load(vae_path, "vae"))
    cond = Conditioning({"context": torch.randn(1, 4, 32), "pooled": torch.randn(1, 768)})
    kw = dict(latents_shape=(1, 16, 16, 16), steps=4, seed=7, cfg_scale=3.5, denoise_strength=0.5)
    txt2img = gen.sample(cond, **kw)                                        # zeros init
    img2img = gen.sample(cond, init_latent=torch.randn(1, 16, 16, 16) * 5.0, **kw)
    assert not torch.equal(txt2img, img2img)


def test_encode_image_causal3d_normalizes_and_unsqueezes():
    """The causal-3D encode path is the exact inverse of _decode_causal3d: per-channel
    wan21 normalize then a T=1 unsqueeze to the 5D (B,C,1,H,W) latent shape."""
    import types

    from src.platform.runtime.native.engine import NativeGenerator
    from src.platform.runtime.native.memory.device_plan import DevicePlan
    from src.platform.runtime.native.vae.causal_3d import LATENTS_MEAN, LATENTS_STD

    class _CausalVaeStub:
        def decode_image(self, x):                       # marks this as causal-3D
            return x

        def encode_image(self, pixels):                  # raw wan21 z == 1 everywhere
            b, _c, h, w = pixels.shape
            return torch.ones(b, 16, h // 8, w // 8)

    gen = NativeGenerator.__new__(NativeGenerator)
    gen.spec = types.SimpleNamespace(latent_format={"format": "wan21", "latent_channels": 16})
    gen.device_plan = DevicePlan("cpu", "cpu", "cpu")
    gen.vae = types.SimpleNamespace(
        module=_CausalVaeStub(), compute_dtype=torch.float32,
        move_to=lambda d: None, offload=lambda: None,
    )
    latent = gen.encode_image(np.zeros((1, 64, 64, 3), dtype=np.uint8))     # -> 8x8 latent
    assert latent.shape == (1, 16, 1, 8, 8)
    expected0 = (1.0 - LATENTS_MEAN[0]) / LATENTS_STD[0]
    assert abs(latent[0, 0, 0, 0, 0].item() - expected0) < 1e-4


def test_attention_mask_flows_to_dit(dit_path, vae_path):
    """Klein path: a mask in the cond dict must reach Flux.forward's kwargs."""
    loader = NativeEngineLoader(device="cpu")
    dit = loader.load(dit_path, "diffusion_model")
    gen = NativeGenerator(dit, _StubTE(), loader.load(vae_path, "vae"))

    # Spy short-circuits the arch: this asserts the ENGINE routes attention_mask
    # (and the injected guidance) into Flux.forward's kwargs, which is the
    # engine's responsibility. Shaping the token mask to the joint txt+img
    # sequence is the arch/TE contract, exercised in their own suites.
    seen = {}

    def spy(x, timestep, context, y=None, guidance=None, **kwargs):
        seen["attention_mask"] = kwargs.get("attention_mask")
        seen["guidance"] = guidance
        return torch.zeros_like(x)

    dit.module.forward = spy
    mask = torch.ones(1, 4, dtype=torch.bool)
    cond = {"context": torch.randn(1, 4, 32), "y": torch.randn(1, 768), "attention_mask": mask}
    gen.sample(cond, latents_shape=(1, 16, 16, 16), steps=1, seed=3, cfg_scale=3.5)

    assert seen["attention_mask"] is not None
    assert seen["attention_mask"].shape == (1, 4)
    assert seen["guidance"] is not None   # EmbeddedGuidance injected the scale


# --- on-the-fly fp8 quantise-at-load (loader policy) --------------------------

def _big_bf16_sd():
    """A DiT-shaped sd with one big quantisable Linear + peripheral tensors."""
    return {
        "blocks.0.attn.qkv.weight": torch.randn(2048, 2048, dtype=torch.bfloat16) * 0.02,
        "blocks.0.attn.qkv.bias": torch.randn(2048, dtype=torch.bfloat16),
        "norm.weight": torch.randn(2048, dtype=torch.bfloat16),
    }


def _stub_spec():
    from types import SimpleNamespace
    return SimpleNamespace(family="test", variant="tiny")


def test_fp8_off_is_a_noop():
    loader = NativeEngineLoader(device="cpu", vram_gb=8.0, fp8_quantize="off")
    sd = _big_bf16_sd()
    out, qf, dt, est = loader._maybe_quantize_fp8(sd, _stub_spec(), None, torch.bfloat16, 24.0)
    assert qf is None
    assert out["blocks.0.attn.qkv.weight"].dtype == torch.bfloat16


def test_fp8_force_quantizes_big_linear_and_emits_scale():
    loader = NativeEngineLoader(device="cpu", vram_gb=None, fp8_quantize="force")
    out, qf, dt, est = loader._maybe_quantize_fp8(_big_bf16_sd(), _stub_spec(), None, torch.bfloat16, 8.0)
    assert qf == "fp8_scaled"
    assert out["blocks.0.attn.qkv.weight"].dtype == torch.float8_e4m3fn
    assert "blocks.0.attn.qkv.weight_scale" in out
    # Peripheral tensors stay bf16; the fp8 est is smaller than the bf16 one.
    assert out["norm.weight"].dtype == torch.bfloat16
    assert out["blocks.0.attn.qkv.bias"].dtype == torch.bfloat16
    assert est < 8.0


def test_fp8_auto_only_when_bf16_wont_fit_but_fp8_would():
    spec = _stub_spec()
    # 24GB card: an 8GB bf16 DiT fits resident -> no quantise.
    ample = NativeEngineLoader(device="cpu", vram_gb=24.0, fp8_quantize="auto")
    _, qf_fits, _, _ = ample._maybe_quantize_fp8(_big_bf16_sd(), spec, None, torch.bfloat16, 8.0)
    assert qf_fits is None
    # 10GB card, 12GB bf16 DiT: doesn't fit bf16, fp8 (~tiny here) does -> quantise.
    tight = NativeEngineLoader(device="cpu", vram_gb=10.0, fp8_quantize="auto")
    _, qf_tight, _, _ = tight._maybe_quantize_fp8(_big_bf16_sd(), spec, None, torch.bfloat16, 12.0)
    assert qf_tight == "fp8_scaled"


def test_fp8_skips_already_quantized_checkpoint():
    loader = NativeEngineLoader(device="cpu", vram_gb=8.0, fp8_quantize="force")
    out, qf, _, _ = loader._maybe_quantize_fp8(_big_bf16_sd(), _stub_spec(), "fp8_scaled", torch.bfloat16, 24.0)
    assert qf == "fp8_scaled"
    assert out["blocks.0.attn.qkv.weight"].dtype == torch.bfloat16  # untouched


# ---------------------------------------------------------------------------
# _make_forward conditionally threads conditioning["ref_latents"]
# ---------------------------------------------------------------------------
#
# _make_forward's closure only ever touches `self.dit.module`, so a bare
# NativeGenerator (skipping __init__ -- no dit/te/vae/device_plan needed) with
# just that one attribute set is enough to test it in isolation, mirroring the
# already-established step_cache conditional-forwarding idiom it copies.

def test_make_forward_omits_ref_latents_when_absent():
    gen = NativeGenerator.__new__(NativeGenerator)
    calls = []

    def fake_module(x, sigma, context, **kwargs):
        calls.append(kwargs)
        return x

    gen.dit = type("Fake", (), {"module": staticmethod(fake_module)})()
    model_forward = gen._make_forward("cpu", torch.float32)

    x = torch.zeros(1)
    sigma = torch.zeros(1)
    model_forward(x, sigma, {"context": torch.zeros(1)})
    assert "ref_latents" not in calls[0]


def test_make_forward_forwards_ref_latents_when_present():
    gen = NativeGenerator.__new__(NativeGenerator)
    calls = []

    def fake_module(x, sigma, context, **kwargs):
        calls.append(kwargs)
        return x

    gen.dit = type("Fake", (), {"module": staticmethod(fake_module)})()
    model_forward = gen._make_forward("cpu", torch.float32)

    x = torch.zeros(1)
    sigma = torch.zeros(1)
    ref = torch.ones(1, 4, 1, 8, 8)
    model_forward(x, sigma, {"context": torch.zeros(1), "ref_latents": ref})
    assert calls[0]["ref_latents"] is ref


class TestMoveCondListValues:
    """ref_latents rides the cond dict as a LIST of tensors (wire contract shared
    with ComfyUI); _move_cond must move each element instead of calling tensor
    ops on the list itself — the exact crash the first GPU run of qwen edit hit."""

    def _generator(self):
        gen = NativeGenerator.__new__(NativeGenerator)
        return gen

    def test_list_of_tensors_moves_elementwise(self):
        gen = self._generator()
        cond = {
            "context": torch.randn(1, 4, 8),
            "ref_latents": [torch.randn(1, 16, 4, 4), torch.randn(1, 16, 4, 4)],
        }
        out = gen._move_cond(cond, torch.device("cpu"), torch.float16)
        assert isinstance(out["ref_latents"], list)
        assert [t.dtype for t in out["ref_latents"]] == [torch.float16, torch.float16]
        assert out["context"].dtype == torch.float16

    def test_non_tensor_scalars_pass_through(self):
        gen = self._generator()
        out = gen._move_cond({"ref_latents_method": "index", "n": 3}, torch.device("cpu"), torch.float16)
        assert out["ref_latents_method"] == "index"
        assert out["n"] == 3

    def test_none_values_dropped(self):
        gen = self._generator()
        out = gen._move_cond({"a": None, "b": torch.ones(2)}, torch.device("cpu"), torch.float32)
        assert "a" not in out and out["b"].dtype == torch.float32
