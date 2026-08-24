"""Tests for src.plugin_api.native: the narrow native-engine
generation surface krea2-edit (and any future plugin driving the native
engine directly) imports from. `NativeGeneratorHandle` is a structural
Protocol, not a wrapper - these tests are the actual proof that it narrows
correctly (a stub with only the four methods it declares satisfies it; one
missing any of them does not), since a Protocol's real behavior can't be
seen just by reading it.
"""

import numpy as np
import torch

import src.plugin_api as plugin_api
from src.plugin_api.native import (
    Conditioning,
    GeneratorContext,
    GeneratorKrea2Pipe,
    NativeGeneratorHandle,
    ProgressEmitter,
    native_step_hooks,
)


class _FullGeneratorStub:
    """Declares exactly the four operations NativeGeneratorHandle requires."""

    def encode_image(self, image, *, vram_free_gb=None):
        return torch.zeros(1)

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 4, height // 8, width // 8)

    def sample(self, conditioning, latents_shape, steps, seed, cfg_scale, **kwargs):
        return torch.zeros(latents_shape)

    def decode(self, latents, *, vram_free_gb=None):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)


class _MissingDecodeStub:
    """Everything but `decode` - must NOT satisfy the handle protocol."""

    def encode_image(self, image, *, vram_free_gb=None):
        return torch.zeros(1)

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 4, height // 8, width // 8)

    def sample(self, conditioning, latents_shape, steps, seed, cfg_scale, **kwargs):
        return torch.zeros(latents_shape)


def test_full_stub_satisfies_native_generator_handle():
    assert isinstance(_FullGeneratorStub(), NativeGeneratorHandle)


def test_stub_missing_a_required_method_does_not_satisfy_the_handle():
    assert not isinstance(_MissingDecodeStub(), NativeGeneratorHandle)


def test_bare_object_does_not_satisfy_the_handle():
    assert not isinstance(object(), NativeGeneratorHandle)


def test_real_native_generator_satisfies_the_handle_without_instantiating_one():
    """Structural typing means the real class only needs the right methods to
    exist - no DiT/TE/VAE bundle (and therefore no GPU) required to prove this."""
    from src.platform.runtime.native.engine import NativeGenerator

    for method in ("encode_image", "latent_shape_for", "sample", "decode"):
        assert callable(getattr(NativeGenerator, method, None)), (
            f"NativeGenerator.{method} must exist for NativeGeneratorHandle to describe it"
        )


def test_conditioning_is_the_real_native_engine_class():
    from src.platform.runtime.native.engine import Conditioning as RealConditioning

    assert Conditioning is RealConditioning


def test_generator_krea2_pipe_is_the_real_core_pipe():
    from src.pipelines.pipes.generator.krea2.main import GeneratorKrea2Pipe as RealGeneratorKrea2Pipe

    assert GeneratorKrea2Pipe is RealGeneratorKrea2Pipe


def test_generator_context_and_progress_helpers_are_the_real_shared_ones():
    from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext as RealGeneratorContext
    from src.pipelines.pipes._shared.generation.progress import (
        ProgressEmitter as RealProgressEmitter,
        native_step_hooks as real_native_step_hooks,
    )

    assert GeneratorContext is RealGeneratorContext
    assert ProgressEmitter is RealProgressEmitter
    assert native_step_hooks is real_native_step_hooks


def test_every_native_export_is_reachable_from_the_plugin_api_top_level():
    for name in ("Conditioning", "GeneratorContext", "GeneratorKrea2Pipe",
                 "NativeGeneratorHandle", "ProgressEmitter", "native_step_hooks"):
        assert hasattr(plugin_api, name), f"src.plugin_api is missing {name}"
        assert name in plugin_api.__all__
