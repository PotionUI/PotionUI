from __future__ import annotations

import inspect

import pytest
import torch

from src.platform.runtime.native.detect.registry import arch_registry, match_model_spec
from src.platform.runtime.native.families import FAMILY_CAPABILITIES, FamilyCapabilities
from src.platform.runtime.native.text_encoders import (
    CLIPLTextEncoder,
    Flux3TextEncoder,
    Gemma3TextEncoder,
    MossImageTextEncoder,
    NativeTextEncoder,
    Qwen3TextEncoder,
    Qwen25VLTextEncoder,
    T5XXLTextEncoder,
    UMT5TextEncoder,
    ZImageTextEncoder,
)
from src.platform.runtime.native.text_encoders.anima import AnimaTextEncoder
from src.platform.runtime.native.text_encoders.gemma4 import Gemma4TextEncoder
from src.platform.runtime.native.text_encoders.qwen3 import MiniMaxH3TextEncoder, Qwen3VLTextEncoder
from src.platform.runtime.native.text_encoders.qwen_image21 import QwenImage21TextEncoder

from .arch.test_flux_model import TINY_FLUX1, TINY_FLUX2, _build_ready

ENCODERS = {
    "flux1": (T5XXLTextEncoder, CLIPLTextEncoder),
    "flux2": (Qwen3TextEncoder,),
    "flux3_klein_base": (Flux3TextEncoder,),
    "flux3_klein": (Flux3TextEncoder,),
    "moss_image": (MossImageTextEncoder,),
    "krea2_turbo": (Qwen3VLTextEncoder,),
    "qwen_image21": (QwenImage21TextEncoder,),
    "qwen_image": (Qwen25VLTextEncoder,),
    "wan_t2v_14b": (UMT5TextEncoder,),
    "wan_i2v_14b": (UMT5TextEncoder,),
    "wan22_i2v_14b": (UMT5TextEncoder,),
    "wan_ti2v_5b": (UMT5TextEncoder,),
    "ltxav": (Gemma3TextEncoder, Gemma4TextEncoder),
    "ltxv": (T5XXLTextEncoder,),
    "anima": (AnimaTextEncoder,),
    "z_image": (ZImageTextEncoder,),
    "h3": (MiniMaxH3TextEncoder,),
    "h3_image": (MiniMaxH3TextEncoder,),
}

GUIDANCE_FROM_REGISTRY = {"cfg": "cfg", "embedded": "embedded", "none": "none", "flow_map": "none"}


def honours_weights(cls: type) -> bool:
    hooks = (
        cls._tokenize_weighted is not NativeTextEncoder._tokenize_weighted
        and cls._encode_ids is not NativeTextEncoder._encode_ids
    )
    return hooks or cls.encode_weighted is not NativeTextEncoder.encode_weighted


def registry_variants() -> dict[str, object]:
    return {spec.variant: spec for spec in arch_registry.all()}


def test_table_covers_exactly_the_registry_variants():
    assert set(FAMILY_CAPABILITIES) == set(registry_variants())


def test_table_values_are_typed():
    for caps in FAMILY_CAPABILITIES.values():
        assert isinstance(caps, FamilyCapabilities)
        assert caps.guidance in {"cfg", "embedded", "none"}


@pytest.mark.parametrize("variant", sorted(FAMILY_CAPABILITIES))
def test_guidance_matches_registry(variant):
    spec = registry_variants()[variant]
    assert FAMILY_CAPABILITIES[variant].guidance == GUIDANCE_FROM_REGISTRY[spec.sampling_settings["guidance"]]


@pytest.mark.parametrize("variant", sorted(FAMILY_CAPABILITIES))
def test_negative_prompt_is_cfg_with_a_text_encoder(variant):
    caps = FAMILY_CAPABILITIES[variant]
    assert caps.negative_prompt is (caps.guidance == "cfg" and variant in ENCODERS)


@pytest.mark.parametrize("variant", sorted(ENCODERS))
def test_prompt_weighting_matches_encoder_classes(variant):
    expected = FAMILY_CAPABILITIES[variant].prompt_weighting
    assert all(honours_weights(cls) for cls in ENCODERS[variant]) is expected


def test_families_without_a_text_encoder_do_not_weight():
    for variant in set(FAMILY_CAPABILITIES) - set(ENCODERS):
        assert FAMILY_CAPABILITIES[variant].prompt_weighting is False


def test_anima_weights_ride_on_the_t5_side():
    assert "t5xxl_weights" in inspect.getsource(AnimaTextEncoder.encode)


class _Stub(NativeTextEncoder):
    def __init__(self, weighted: bool) -> None:
        self.weighted = weighted
        self.encoded: list[list[str]] = []

    def encode(self, texts):
        self.encoded.append(texts)
        return {"context": torch.ones(1, 3, 2)}

    def _tokenize_weighted(self, prompt):
        if not self.weighted:
            return None
        ids = torch.tensor([[1, 2, 3]])
        weights = torch.tensor([[1.0, 1.5, 1.0]]) if prompt else torch.ones(1, 3)
        return ids, torch.ones_like(ids), weights

    def _encode_ids(self, ids, mask):
        if not self.weighted:
            return None
        return {"context": torch.ones(1, 3, 2)}


def test_base_fallback_strips_weights_when_encoder_has_no_hooks():
    stub = _Stub(weighted=False)
    out = stub.encode_weighted("a (cat:1.5)")
    assert stub.encoded == [["a cat"]]
    assert torch.equal(out["context"], torch.ones(1, 3, 2))


def test_base_path_applies_weights_when_encoder_has_hooks():
    stub = _Stub(weighted=True)
    out = stub.encode_weighted("a (cat:1.5)")
    assert stub.encoded == []
    assert honours_weights(type(stub))
    assert out["context"].shape == (1, 3, 2)


def test_flux2_klein_forward_ignores_guidance():
    model = _build_ready(TINY_FLUX2)
    x, t, ctx = torch.randn(1, 16, 16, 16), torch.tensor([0.5]), torch.randn(1, 7, 32)
    assert isinstance(model.guidance_in, torch.nn.Identity)
    base = model(x, t, ctx)
    assert torch.equal(base, model(x, t, ctx, guidance=torch.tensor([1.0])))
    assert torch.equal(base, model(x, t, ctx, guidance=torch.tensor([9.0])))


def test_flux1_forward_uses_guidance():
    model = _build_ready(TINY_FLUX1)
    x, t, ctx, y = torch.randn(1, 16, 16, 16), torch.tensor([0.5]), torch.randn(1, 7, 32), torch.randn(1, 768)
    low = model(x, t, ctx, y=y, guidance=torch.tensor([1.0]))
    high = model(x, t, ctx, y=y, guidance=torch.tensor([9.0]))
    assert not torch.equal(low, high)


def test_flux2_detect_spec_says_no_guidance():
    assert match_model_spec(TINY_FLUX2).sampling_settings["guidance"] == "none"
    assert FAMILY_CAPABILITIES["flux2"].guidance == "none"
