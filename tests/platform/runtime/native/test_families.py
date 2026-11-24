from __future__ import annotations

from types import SimpleNamespace

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

def honours_weights(cls: type) -> bool:
    hooks = (
        cls._tokenize_weighted is not NativeTextEncoder._tokenize_weighted
        and cls._encode_ids is not NativeTextEncoder._encode_ids
    )
    return hooks or cls.encode_weighted is not NativeTextEncoder.encode_weighted


def registry_variants() -> dict[str, object]:
    return {spec.variant: spec for spec in arch_registry.all()}


def test_every_registry_variant_has_a_table_row():
    assert set(registry_variants()) <= set(FAMILY_CAPABILITIES)


def test_table_values_are_typed():
    for caps in FAMILY_CAPABILITIES.values():
        assert isinstance(caps, FamilyCapabilities)
        assert caps.guidance in {"cfg", "embedded", "none"}


EXPECTED = {
    "flux1": (False, False, "embedded"),
    "flux2": (True, False, "none"),
    "flux3_klein_base": (False, True, "cfg"),
    "flux3_klein": (False, False, "none"),
    "moss_image": (False, True, "cfg"),
    "krea2_turbo": (True, True, "cfg"),
    "qwen_image21": (False, True, "cfg"),
    "qwen_image": (True, True, "cfg"),
    "wan_t2v_14b": (False, True, "cfg"),
    "wan_i2v_14b": (False, True, "cfg"),
    "wan22_i2v_14b": (False, True, "cfg"),
    "wan_ti2v_5b": (False, True, "cfg"),
    "ltxav": (False, True, "cfg"),
    "ltxv": (False, True, "cfg"),
    "anima": (True, True, "cfg"),
    "z_image": (True, True, "cfg"),
    "seedvr2_3b": (False, False, "none"),
    "seedvr2_7b": (False, False, "none"),
    "h3": (False, False, "none"),
    "h3_image": (False, False, "none"),
    "music3": (False, False, "cfg"),
    "yue2_3b": (False, False, "cfg"),
}


@pytest.mark.parametrize("variant", sorted(EXPECTED))
def test_capabilities_match_explicit_expectation(variant):
    assert FAMILY_CAPABILITIES[variant] == FamilyCapabilities(*EXPECTED[variant])


def test_expectations_cover_the_table():
    assert set(EXPECTED) == set(FAMILY_CAPABILITIES)


@pytest.mark.parametrize("variant", sorted(ENCODERS))
def test_prompt_weighting_matches_encoder_classes(variant):
    expected = FAMILY_CAPABILITIES[variant].prompt_weighting
    assert all(honours_weights(cls) for cls in ENCODERS[variant]) is expected


def test_families_without_a_text_encoder_do_not_weight():
    for variant in set(FAMILY_CAPABILITIES) - set(ENCODERS):
        assert FAMILY_CAPABILITIES[variant].prompt_weighting is False


def test_yue2_guides_with_cfg_against_a_built_negative_prefix():
    assert FAMILY_CAPABILITIES["yue2_3b"] == FamilyCapabilities(False, False, "cfg")
    assert registry_variants()["yue2_3b"].sampling_settings["guidance"] == "cfg"


def test_anima_weights_ride_on_the_t5_side():
    qwen_seen = []

    def qwen_tok(texts, device):
        qwen_seen.append(texts)
        return torch.ones(1, 3, dtype=torch.long), torch.ones(1, 3, dtype=torch.long)

    def t5_tok(text, device):
        weights = torch.tensor([[1.0, 1.5]]) if "(" in text else torch.ones(1, 2)
        return torch.ones(1, 2, dtype=torch.long), torch.ones(1, 2, dtype=torch.long), weights

    class _Module:
        cfg = SimpleNamespace(num_hidden_layers=2)
        model = SimpleNamespace(norm=torch.nn.Identity())

        def __call__(self, ids, attention_mask, layers_to_extract, capture):
            return torch.ones(1, 1, 3, 4)

    encoder = AnimaTextEncoder(_Module(), qwen_tok, t5_tok)
    weighted = encoder.encode_weighted("a (cat:1.5)")
    plain = encoder.encode_weighted("a cat")
    assert qwen_seen[0] == ["a cat"]
    assert torch.equal(weighted["t5xxl_weights"], torch.tensor([[1.0, 1.5]]))
    assert torch.equal(plain["t5xxl_weights"], torch.ones(1, 2))
    assert torch.equal(weighted["context"], plain["context"])


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
