"""Tests for the ModelSpec registry and matching."""

from __future__ import annotations

import pytest

from src.platform.runtime.native.base import NativeArchModule
from src.platform.runtime.native.detect.registry import (
    ArchRegistry,
    DuplicateArchSpecError,
    ModelSpec,
    arch_registry,
    match_model_spec,
)
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.errors import NativeEngineUnsupportedError

from .conftest import flux1_sd, flux2_sd, seedvr2_dit_sd


def test_match_flux2_spec_from_detected_config():
    config = detect_unet_config(flux2_sd())
    spec = match_model_spec(config)
    assert spec.family == "flux"
    assert spec.variant == "flux2"
    assert spec.vae_target == "flux2_ae"
    assert spec.clip_targets == ["qwen3"]
    assert spec.sampling_settings["guidance"] == "embedded"
    assert spec.latent_format["latent_channels"] == 32


def test_match_flux1_spec_from_detected_config():
    config = detect_unet_config(flux1_sd())
    spec = match_model_spec(config)
    assert spec.variant == "flux1"
    assert spec.clip_targets == ["t5xxl", "clip_l"]
    assert spec.vae_target == "flux_ae"


def test_krea2_turbo_spec_uses_fixed_mu_not_dynamic_shift():
    # Official Krea-2 Turbo (the distilled checkpoint) was trained at a
    # FIXED mu=1.15 regardless of resolution (krea-ai/krea-2 sampling.py
    # `timesteps()` docstring; diffusers Krea2Pipeline `is_distilled -> mu=1.15`).
    # Guards against silently reintroducing the old resolution-dependent
    # `dynamic_shift` anchors on this spec.
    spec = arch_registry.get("krea2", "krea2_turbo")
    assert spec is not None
    assert spec.sampling_settings.get("fixed_mu") == pytest.approx(1.15)
    assert "dynamic_shift" not in spec.sampling_settings


def test_krea2_spec_uses_true_cfg_not_nocfg():
    # BE-CFG-KREA2: one ModelSpec covers both the turbo/distilled checkpoint and
    # a raw/base checkpoint (identical architecture, nothing in the state dict
    # to tell them apart) -- mirrors Z-Image's "one spec, cfg_scale picks the
    # regime" design. Turbo drives cfg_scale=1.0, which makes TrueCFG collapse
    # to a single conditional-only forward (see sampling/cfg.py's
    # `abs(scale - 1.0) < 1e-6 or uncond is None` short-circuit) -- so this is
    # safe for the shipped preset's turbo default without any preset change on
    # its own. The Krea-2 preset's speed profile picks cfg_scale per generation
    # (see content/presets/marketplace/Krea2/preset.yml and generator/krea2/main.py).
    spec = arch_registry.get("krea2", "krea2_turbo")
    assert spec is not None
    assert spec.sampling_settings["guidance"] == "cfg"


def test_no_match_raises_with_config_repr():
    with pytest.raises(NativeEngineUnsupportedError, match="no ModelSpec"):
        match_model_spec({"image_model": "totally_unknown"})


def test_match_seedvr2_spec_from_detected_config():
    config = detect_unet_config(seedvr2_dit_sd())
    spec = match_model_spec(config)
    assert spec.family == "seedvr2"
    assert spec.variant == "seedvr2_3b"
    assert spec.model_class == "src.platform.runtime.native.arch.seedvr2.model:SeedVR2"
    assert spec.vae_target == "seedvr2"
    assert spec.clip_targets == []                       # fixed prompt embeddings
    assert spec.latent_format == {"latent_channels": 16, "format": "seedvr2"}
    assert spec.sampling_settings["guidance"] == "none"  # NoCFG, one-step APT


def test_seedvr2_spec_resolves_model_class():
    # The lazy "module:Class" string must import to the real arch class.
    spec = match_model_spec(detect_unet_config(seedvr2_dit_sd()))
    from src.platform.runtime.native.arch.seedvr2.model import SeedVR2

    assert spec.resolve_model_class() is SeedVR2


def test_spec_matches_is_subset_check():
    spec = arch_registry.all()[0]
    superset = dict(spec.signature)
    superset["extra"] = "ignored"
    assert spec.matches(superset) is True
    assert spec.matches({}) is False


def test_resolve_model_class_lazy(monkeypatch):
    # model_class points at a not-yet-existing module; resolution is lazy so the
    # spec constructs fine. Resolve a stand-in via a real importable target.
    spec = ModelSpec(
        family="t", variant="v", signature={},
        model_class="collections:OrderedDict",
    )
    assert spec.resolve_model_class().__name__ == "OrderedDict"


def test_resolve_model_class_bad_format():
    spec = ModelSpec(family="t", variant="v", signature={}, model_class="no_colon")
    with pytest.raises(NativeEngineUnsupportedError, match="module:Class"):
        spec.resolve_model_class()


def test_expected_key_globs():
    spec = ModelSpec(
        family="t", variant="v", signature={}, model_class="x:Y",
        expected_missing_keys={"pos_embed*"},
        expected_unexpected_keys={"*.weight_scale"},
    )
    assert spec.key_is_expected_missing("pos_embed.table")
    assert not spec.key_is_expected_missing("img_in.weight")
    assert spec.key_is_expected_unexpected("double_blocks.0.qkv.weight_scale")
    assert not spec.key_is_expected_unexpected("double_blocks.0.qkv.weight")


def test_every_registered_spec_tolerates_comfyui_save_buffers():
    """ComfyUI-resaved checkpoints (common on civitai) carry a
    ``model_sampling.sigmas`` schedule buffer that is metadata, not weights -
    no family's integrity gate may reject it."""
    for spec in arch_registry.all():
        assert spec.key_is_expected_unexpected("model_sampling.sigmas"), spec.family


def test_registered_specs_have_resolvable_paths():
    for spec in arch_registry.all():
        assert ":" in spec.model_class
        assert spec.memory_cost_gb > 0


@pytest.mark.parametrize(
    "spec",
    arch_registry.all(),
    ids=[f"{s.family}/{s.variant}" for s in arch_registry.all()],
)
def test_every_registered_spec_resolves_to_an_arch_module(spec):
    # Without this, a typo'd model_class only surfaces at model-load time on a
    # GPU. It is what makes moving the arch modules safe.
    cls = spec.resolve_model_class()
    assert isinstance(cls, type)
    assert issubclass(cls, NativeArchModule), f"{spec.model_class} is not a NativeArchModule"


@pytest.mark.parametrize(
    "spec",
    arch_registry.all(),
    ids=[f"{s.family}/{s.variant}" for s in arch_registry.all()],
)
def test_every_registered_state_dict_map_resolves(spec):
    remap = spec.resolve_state_dict_map()
    if spec.state_dict_map is None:
        assert remap is None
    else:
        assert callable(remap)


def test_state_dict_map_defaults_to_none():
    spec = ModelSpec(family="t", variant="v", signature={}, model_class="x:Y")
    assert spec.state_dict_map is None
    assert spec.resolve_state_dict_map() is None


def test_state_dict_map_resolves_lazily():
    spec = ModelSpec(
        family="t", variant="v", signature={}, model_class="x:Y",
        state_dict_map="collections:OrderedDict",
    )
    assert spec.resolve_state_dict_map().__name__ == "OrderedDict"


def test_state_dict_map_bad_format():
    spec = ModelSpec(
        family="t", variant="v", signature={}, model_class="x:Y",
        state_dict_map="no_colon",
    )
    with pytest.raises(NativeEngineUnsupportedError, match="module:Class"):
        spec.resolve_state_dict_map()


def _spec(family="f", variant="v", **kw):
    return ModelSpec(family=family, variant=variant, signature={}, model_class="x:Y", **kw)


def test_registry_higher_priority_wins():
    reg = ArchRegistry()
    reg.register(_spec(vae_target="vendored"))
    reg.register(_spec(vae_target="provider"), provider="diffusers", priority=10)
    assert reg.get("f", "v").vae_target == "provider"
    assert len(reg.all()) == 1


def test_registry_lower_priority_does_not_displace():
    reg = ArchRegistry()
    reg.register(_spec(vae_target="high"), provider="diffusers", priority=10)
    reg.register(_spec(vae_target="low"))
    assert reg.get("f", "v").vae_target == "high"


def test_registry_equal_priority_collision_raises():
    reg = ArchRegistry()
    reg.register(_spec())
    with pytest.raises(DuplicateArchSpecError, match="f/v"):
        reg.register(_spec(), provider="other")


def test_registry_override_keeps_registration_order():
    reg = ArchRegistry()
    reg.register(_spec(variant="a"))
    reg.register(_spec(variant="b"))
    reg.register(_spec(variant="a", vae_target="new"), provider="p", priority=1)
    assert [s.variant for s in reg.all()] == ["a", "b"]


def test_registry_unregister_provider():
    reg = ArchRegistry()
    reg.register(_spec(variant="a"))
    reg.register(_spec(variant="b"), provider="plugin")
    reg.unregister_provider("plugin")
    assert [s.variant for s in reg.all()] == ["a"]
    assert reg.get("f", "b") is None


def test_registry_get_unknown_key_is_none():
    assert arch_registry.get("nope", "nope") is None


def test_ltxav_spec_accepts_both_gemma3_and_gemma4_clip_targets():
    spec = arch_registry.get("ltx", "ltxav")
    assert spec is not None
    assert spec.clip_targets == ["gemma3_12b", "gemma4_12b"]
