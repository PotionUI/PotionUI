from __future__ import annotations

import pytest
import torch

from src.platform.runtime.model_headers.signatures import family_from_keys
from src.platform.runtime.native.detect import arch_registry
from src.platform.runtime.native.detect import unet_detect
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from tests.fixtures.family_shape_fixtures import EXPECTED_FAMILY, family_shapes

NATIVE_IMAGE_MODEL = {
    "flux": {"flux1": "flux", "flux2": "flux2"},
    "krea2": "krea2",
    "qwen_image21": "qwen_image21",
    "qwen_image": "qwen_image",
    "wan": "wan2.1",
    "ltx": {"ltxv": "ltxv", "ltxav": "ltxav"},
    "z_image": "lumina2",
    "anima": "anima",
    "seedvr2": "seedvr2",
    "minimax_h3": "minimax_h3",
    "minimax_music3": "minimax_music3_dit",
    "yue2": "yue2",
    "trellis2": "trellis2",
}


def meta_state_dict(shapes):
    return {key: torch.empty(shape, device="meta") for key, shape in shapes.items()}


def torch_free_family(shapes):
    return family_from_keys(set(shapes), shapes.get)


def expected_native(family, variant):
    native = NATIVE_IMAGE_MODEL[family]
    if isinstance(native, dict):
        return native[variant]
    return native


@pytest.mark.parametrize("name", sorted(EXPECTED_FAMILY))
def test_native_detector_and_torch_free_dispatch_agree(name):
    shapes = family_shapes(name)
    family, variant = EXPECTED_FAMILY[name]
    config = detect_unet_config(meta_state_dict(shapes))
    found = torch_free_family(shapes)
    assert config is not None
    assert found.family == family
    assert config["image_model"] == expected_native(family, found.variant)
    if name not in ("trellis2", "wan"):
        assert arch_registry.match(config).family == found.family


def test_seedvr2_variant_agrees_with_native():
    for name in ("seedvr2_3b", "seedvr2_7b"):
        shapes = family_shapes(name)
        config = detect_unet_config(meta_state_dict(shapes))
        assert torch_free_family(shapes).variant == config["seedvr2_variant"]


def test_flux2_variant_agrees_with_native():
    for name, native in (("flux1", "flux"), ("flux2", "flux2")):
        shapes = family_shapes(name)
        assert detect_unet_config(meta_state_dict(shapes))["image_model"] == native
        assert torch_free_family(shapes).variant == {"flux": "flux1", "flux2": "flux2"}[native]


def test_wan_variant_agrees_with_native_model_type():
    for in_dim, extra in ((16, {}), (36, {"img_emb.proj.0.bias": (4,)}), (48, {})):
        shapes = family_shapes("wan", **{"patch_embedding.weight": (128, in_dim, 1, 2, 2)}, **extra)
        config = detect_unet_config(meta_state_dict(shapes))
        variant = torch_free_family(shapes).variant
        assert (config["model_type"] == "i2v") == (variant == "i2v")


@pytest.mark.parametrize("key", sorted(unet_detect.WAN_REJECT))
def test_wan_reject_keys_agree_with_native(key):
    shapes = family_shapes("wan", **{key: (4, 4)})
    with pytest.raises(NativeEngineUnsupportedError):
        detect_unet_config(meta_state_dict(shapes))
    assert torch_free_family(shapes).variant == unet_detect.WAN_REJECT[key]


def test_lumina2_width_agrees_with_native():
    z_image = family_shapes("z_image")
    assert detect_unet_config(meta_state_dict(z_image))["image_model"] == "lumina2"
    assert torch_free_family(z_image).family == "z_image"
    narrow = family_shapes("z_image", **{"cap_embedder.1.weight": (2304, 128)})
    assert detect_unet_config(meta_state_dict(narrow)) is None
    assert torch_free_family(narrow).family == "lumina2"


def test_chroma_guard_agrees_with_native():
    shapes = family_shapes("flux1")
    del shapes["img_in.weight"]
    assert detect_unet_config(meta_state_dict(shapes)) is None
    assert torch_free_family(shapes) is None


def test_unrelated_keys_match_nothing_in_either():
    shapes = {"model.embed_tokens.weight": (8, 8), "model.layers.0.self_attn.q_proj.weight": (8, 8)}
    assert detect_unet_config(meta_state_dict(shapes)) is None
    assert torch_free_family(shapes) is None


def test_every_family_exists_in_the_native_arch_registry():
    families = {spec.family for spec in arch_registry.all()}
    torch_free = {family for family, _ in EXPECTED_FAMILY.values()}
    assert torch_free - {"trellis2"} <= families
