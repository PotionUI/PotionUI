from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
import torch

from src.pipelines.references import PreEncodedReference, pre_encoded_references


def _reference(name="face", kind="image"):
    return PreEncodedReference(kind=kind, latent=torch.zeros(1, 24, 1, 2, 2), name=name, fingerprint=f"fp:{name}")


def test_plugins_get_the_same_type_through_plugin_api():
    from src.plugin_api import pipes

    assert pipes.PreEncodedReference is PreEncodedReference
    assert "PreEncodedReference" in pipes.__all__


@pytest.mark.parametrize("kind", ["image", "video", "audio"])
def test_every_packed_kind_is_accepted(kind):
    assert _reference(kind=kind).kind == kind


def test_an_unknown_kind_is_refused_by_name():
    with pytest.raises(ValueError, match="'face'.*kind"):
        _reference(kind="mask")


def test_a_non_tensor_latent_is_refused():
    with pytest.raises(TypeError, match="torch.Tensor"):
        PreEncodedReference(kind="image", latent=[0.0], name="face", fingerprint="fp")


def test_an_empty_fingerprint_is_refused():
    with pytest.raises(ValueError, match="fingerprint"):
        PreEncodedReference(kind="image", latent=torch.zeros(1), name="face", fingerprint="")


def test_a_reference_is_frozen():
    with pytest.raises(FrozenInstanceError):
        _reference().name = "other"


def test_missing_input_is_no_references():
    assert pre_encoded_references(None) == []


def test_a_single_value_and_nested_provider_lists_flatten_in_order():
    a, b, c = _reference("a"), _reference("b"), _reference("c")
    assert pre_encoded_references(a) == [a]
    assert pre_encoded_references([[a, b], c]) == [a, b, c]
    assert pre_encoded_references(([a], [], [b])) == [a, b]


def test_a_value_of_another_type_is_refused():
    with pytest.raises(TypeError, match="'reference_latents'.*Tensor"):
        pre_encoded_references([_reference(), torch.zeros(1)])
