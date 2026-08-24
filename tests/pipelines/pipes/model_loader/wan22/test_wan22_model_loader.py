"""Tests for the model_loader/wan22 pipe (dual-expert acquire scheme)."""

from __future__ import annotations

from types import SimpleNamespace

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.wan22.main import ModelLoaderWan22Pipe
from src.pipelines.pipes.model_loader.wan22.bundle import WanModelBundle
from src.pipelines.pipes.model_loader.wan22.wan_clip import WanClipTextEncoder


class _FakeModels:
    def __init__(self):
        self.calls = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=14.0)


def _config(dual=True):
    cfg = ModelLoaderWan22Pipe.get_default_config()
    cfg.update({
        "high_noise_model": {"file_path": "/m/wan_high.safetensors", "name": "high"},
        "text_encoder": {"file_path": "/m/umt5.safetensors", "name": "umt5"},
        "vae": {"file_path": "/m/wan_vae.safetensors", "name": "vae"},
    })
    if dual:
        cfg["low_noise_model"] = {"file_path": "/m/wan_low.safetensors", "name": "low"}
    return cfg


def _run(cfg):
    models = _FakeModels()
    out = ModelLoaderWan22Pipe(config=cfg).process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out


def test_name_and_outputs():
    assert ModelLoaderWan22Pipe.name == "model_loader"
    out = {o.name: o.io_type for o in ModelLoaderWan22Pipe.outputs()}
    assert out["model"] == IOType.MODEL and out["text_encoder"] == IOType.TEXT_ENCODER


def test_dual_expert_four_acquires():
    models, out = _run(_config(dual=True))
    keys = [k for k, _ in models.calls]
    assert keys == [
        "native/dit//m/wan_high.safetensors",
        "native/dit//m/wan_low.safetensors",
        "native/te//m/umt5.safetensors",
        "native/vae//m/wan_vae.safetensors",
    ]
    bundle = out.output["model"]
    assert isinstance(bundle, WanModelBundle)
    assert bundle.is_dual_expert
    assert isinstance(out.output["text_encoder"], WanClipTextEncoder)


def test_single_expert_three_acquires():
    models, out = _run(_config(dual=False))
    keys = [k for k, _ in models.calls]
    assert len(keys) == 3
    assert "native/dit//m/wan_low.safetensors" not in keys
    assert out.output["model"].is_dual_expert is False


def test_per_expert_loras_bust_only_their_dit():
    """Wan 2.2 LoRA pairs are per-expert: the HIGH file must only bust/apply to
    the high DiT and the LOW file to the low DiT (never cross-applied)."""
    def fps(loras_high, loras_low):
        cfg = _config(dual=True)
        cfg["loras_high"] = loras_high
        cfg["loras_low"] = loras_low
        models, _ = _run(cfg)
        return dict(models.calls)

    high = "native/dit//m/wan_high.safetensors"
    low = "native/dit//m/wan_low.safetensors"
    te = "native/te//m/umt5.safetensors"
    vae = "native/vae//m/wan_vae.safetensors"

    no_lora = fps([], [])
    high_only = fps([{"model": "/m/motion_high.safetensors", "strength": 0.9}], [])
    # High LoRA moves ONLY the high expert's fingerprint...
    assert no_lora[high] != high_only[high]
    assert "motion_high.safetensors@0.9" in high_only[high]
    assert no_lora[low] == high_only[low]

    low_only = fps([], [{"model": "/m/motion_low.safetensors", "strength": 0.8}])
    # ...and the low LoRA only the low expert's.
    assert no_lora[low] != low_only[low]
    assert "motion_low.safetensors@0.8" in low_only[low]
    assert no_lora[high] == low_only[high]

    # TE and VAE are untouched by either.
    assert no_lora[te] == high_only[te] == low_only[te]
    assert no_lora[vae] == high_only[vae] == low_only[vae]


def test_bundle_carries_its_own_base_lora_stacks():
    # generator/chain_video_wan22 composes a segment override on top of these --
    # the bundle must expose the ACTIVE (filtered) base stacks it was built with.
    cfg = _config(dual=True)
    cfg["loras_high"] = [{"model": "/m/lightning_high.safetensors", "strength": 1.0}]
    cfg["loras_low"] = [{"model": "/m/lightning_low.safetensors", "strength": 1.0}, {"model": "/m/zero.safetensors", "strength": 0.0}]
    _, out = _run(cfg)
    bundle = out.output["model"]
    assert bundle.loras_high == [{"file_path": "/m/lightning_high.safetensors", "weight": 1.0}]
    # The zero-weight entry is filtered out, matching acquire_wan_dit's own filtering.
    assert bundle.loras_low == [{"file_path": "/m/lightning_low.safetensors", "weight": 1.0}]


def test_bundle_lora_stacks_default_empty():
    _, out = _run(_config(dual=True))
    bundle = out.output["model"]
    assert bundle.loras_high == []
    assert bundle.loras_low == []


def test_shared_te_vae_keys_are_path_derived():
    # A different DiT but the same TE/VAE path -> same TE/VAE cache keys (reuse).
    m1, _ = _run(_config(dual=False))
    cfg2 = _config(dual=False)
    cfg2["high_noise_model"] = {"file_path": "/m/other_high.safetensors", "name": "other"}
    m2, _ = _run(cfg2)
    te1 = next(k for k, _ in m1.calls if k.startswith("native/te/"))
    te2 = next(k for k, _ in m2.calls if k.startswith("native/te/"))
    assert te1 == te2  # shared UMT5 reused across Wan presets
