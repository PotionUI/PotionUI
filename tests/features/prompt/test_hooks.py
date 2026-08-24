"""The prompt domain's hook declarations."""

from src.platform.plugins.hooks import hooks_registry
from src.features.prompt.hooks import PROMPT_HOOKS


def test_transform_hook_is_declared():
    assert PROMPT_HOOKS.transform == "prompt.transform"
    assert hooks_registry.get("prompt.transform") is not None


def test_transform_hook_is_a_backend_hook():
    spec = hooks_registry.get("prompt.transform")
    assert spec.type == "backend"


def test_transform_hook_exposes_both_channels_as_mutable():
    spec = hooks_registry.get("prompt.transform")
    assert set(spec.mutable) == {"positive", "negative"}


def test_transform_hook_documents_its_payload():
    spec = hooks_registry.get("prompt.transform")
    assert set(spec.payload) == {
        "generation_id",
        "image_index",
        "phase",
        "seed",
        "positive",
        "negative",
    }
