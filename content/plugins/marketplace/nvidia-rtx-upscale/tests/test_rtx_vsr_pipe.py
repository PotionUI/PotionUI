"""Behavioral tests for the `upscaler/rtx_vsr` pipe.

Every test here mocks `nvvfx` entirely (see `fake_nvvfx` in conftest.py) and
mocks `Tensor.cuda()` to a no-op (`no_real_cuda`) - the real GPU on this host
is shared and busy, and nvvfx is not installed in this venv at all.
"""

import sys

import numpy as np
import pytest
from PIL import Image

from src.plugin_api.pipes import PipeInput


@pytest.fixture
def pipe_class(pipe_module):
    return pipe_module.RtxUpscalePipe


def _rgb_image(width: int, height: int) -> Image.Image:
    return Image.fromarray(np.zeros((height, width, 3), dtype=np.uint8), mode="RGB")


# -- round_to_8 / target_size --------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (64, 64),
    (65, 64),   # 65/8 = 8.125 -> round -> 8 -> 64
    (100, 96),  # 100/8 = 12.5 -> banker's rounding -> 12 -> 96
    (4, 8),     # rounds to 0 -> floored up to the 8px minimum
    (0, 8),
])
def test_round_to_8(pipe_module, value, expected):
    assert pipe_module.round_to_8(value) == expected


def test_target_size_forces_input_dims_for_denoise_and_deblur(pipe_module):
    """Denoise/deblur are same-resolution enhancement - `scale` must have no
    effect on the output size, even when it's far from 1.0."""
    for intent in ("denoise", "deblur"):
        assert pipe_module.target_size(intent, 4.0, 100, 200) == (96, 200)
        assert pipe_module.target_size(intent, 1.0, 100, 200) == (96, 200)


def test_target_size_scales_for_upscale_intents(pipe_module):
    for intent in ("upscale", "upscale_highbitrate"):
        assert pipe_module.target_size(intent, 2.0, 100, 200) == (
            pipe_module.round_to_8(200), pipe_module.round_to_8(400)
        )


def test_scale_config_declares_the_4x_cap(pipe_class):
    """`scale`'s min/max drive the shared config validator's cap - the pipe
    doesn't hand-roll a range check of its own."""
    spec = next(s for s in pipe_class.configuration() if s.name == "scale")

    assert spec.min_value == 1.0
    assert spec.max_value == 4.0


# -- quality mapping -------------------------------------------------------

def test_quality_mapping_covers_every_intent_and_tier(pipe_module, fake_nvvfx):
    expected = {
        ("upscale", "low"): "LOW",
        ("upscale", "medium"): "MEDIUM",
        ("upscale", "high"): "HIGH",
        ("upscale", "ultra"): "ULTRA",
        ("upscale_highbitrate", "low"): "HIGHBITRATE_LOW",
        ("upscale_highbitrate", "ultra"): "HIGHBITRATE_ULTRA",
        ("denoise", "low"): "DENOISE_LOW",
        ("denoise", "ultra"): "DENOISE_ULTRA",
        ("deblur", "low"): "DEBLUR_LOW",
        ("deblur", "ultra"): "DEBLUR_ULTRA",
    }
    for (intent, quality), attr_name in expected.items():
        level = pipe_module._quality_level(fake_nvvfx, intent, quality)
        assert level == getattr(fake_nvvfx.VideoSuperRes.QualityLevel, attr_name)
        assert level != fake_nvvfx.VideoSuperRes.QualityLevel.BICUBIC


def test_quality_mapping_falls_back_to_effects_namespace(pipe_module):
    """The reference ComfyUI node reads `nvvfx.effects.QualityLevel` instead of
    `VideoSuperRes.QualityLevel` - both spellings must resolve."""
    from enum import IntEnum
    from types import ModuleType, SimpleNamespace

    class QL(IntEnum):
        ULTRA = 4

    module = ModuleType("nvvfx")
    module.effects = SimpleNamespace(QualityLevel=QL)
    # No `VideoSuperRes` attribute at all on this fake module.

    level = pipe_module._quality_level(module, "upscale", "ultra")
    assert level == QL.ULTRA


def test_quality_mapping_missing_nvvfx_enum_raises_actionable_error(pipe_module):
    from types import ModuleType

    module = ModuleType("nvvfx")  # neither VideoSuperRes nor effects

    with pytest.raises(pipe_module.GenerationExecutionError):
        pipe_module._quality_level(module, "upscale", "ultra")


# -- missing nvvfx -----------------------------------------------------

def test_missing_nvvfx_raises_actionable_error(pipe_module, blocking_finder_cls, monkeypatch):
    finder = blocking_finder_cls(["nvvfx"])
    monkeypatch.delitem(sys.modules, "nvvfx", raising=False)
    sys.meta_path.insert(0, finder)
    try:
        with pytest.raises(pipe_module.GenerationExecutionError) as exc_info:
            pipe_module._import_nvvfx()
    finally:
        sys.meta_path.remove(finder)

    message = str(exc_info.value)
    assert "pip install nvidia-vfx" in message
    assert "570.190" in message


# -- session: reload-on-size-change and clone-before-next-run ------------

def test_ensure_loaded_reloads_only_on_size_change(pipe_module, fake_nvvfx):
    level = fake_nvvfx.VideoSuperRes.QualityLevel.ULTRA
    session = pipe_module._RtxSession(fake_nvvfx, level)
    effect = fake_nvvfx.VideoSuperRes.instances[-1]

    session.ensure_loaded(64, 64)
    assert effect.load_calls == 1

    session.ensure_loaded(64, 64)  # same size -> no reload
    assert effect.load_calls == 1

    session.ensure_loaded(128, 64)  # different size -> reload
    assert effect.load_calls == 2


def test_clone_dlpack_result_copies_before_the_buffer_is_reused(pipe_module):
    """Isolates the exact hazard NVIDIA's own `VideoSuperRes.run()` docstring
    warns about: "you must copy/clone it immediately before the next call or
    close()." A plain `torch.from_dlpack(result.image)` WITHOUT `.clone()`
    would return a view of the same storage nvvfx reuses for its next
    `run()` -- this simulates that reuse directly (mutating the same buffer
    object in place) rather than going through the full pipe, whose later
    dtype-converting ops happen to copy anyway and would mask a missing
    clone here."""
    import torch
    from types import SimpleNamespace

    buffer = torch.zeros(3, 4, 4)
    result = SimpleNamespace(image=buffer)

    cloned = pipe_module._clone_dlpack_result(result)
    buffer.fill_(1.0)  # simulates nvvfx reusing/overwriting this buffer on the next run()

    assert torch.all(cloned == 0.0), (
        "the cloned tensor changed when the source buffer was mutated -- "
        "_clone_dlpack_result is not actually copying the data out"
    )


def test_process_frame_produces_independent_output_per_call(pipe_module, fake_nvvfx, no_real_cuda):
    """End-to-end companion to the isolated clone test above: two frames
    processed through the same session/effect must not observe each other's
    data, even though the fake effect reuses one buffer across `run()` calls."""
    level = fake_nvvfx.VideoSuperRes.QualityLevel.ULTRA
    session = pipe_module._RtxSession(fake_nvvfx, level)
    session.ensure_loaded(8, 8)

    first = session.process_frame(_rgb_image(8, 8))
    first_pixels = np.array(first).copy()

    session.process_frame(_rgb_image(8, 8))

    assert np.all(first_pixels == 0), "the first frame's already-returned pixels must not change later"


# -- full process(): denoise/deblur ignore scale, cancellation -----------

def test_process_image_denoise_ignores_scale(pipe_class, fake_nvvfx, no_real_cuda):
    pipe = pipe_class({"intent": "denoise", "quality": "high", "scale": 4.0})
    outputs = []
    result = pipe.process(
        PipeInput(input={"image": [_rgb_image(40, 24)]}),
        outputs.append,
    )

    (out_image,) = result.output["image"]
    assert out_image.size == (40, 24)


def test_process_image_upscale_applies_scale(pipe_class, fake_nvvfx, no_real_cuda):
    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 2.0})
    outputs = []
    result = pipe.process(
        PipeInput(input={"image": [_rgb_image(40, 24)]}),
        outputs.append,
    )

    (out_image,) = result.output["image"]
    assert out_image.size == (80, 48)


def test_process_requires_image_or_video(pipe_module, pipe_class, fake_nvvfx):
    pipe = pipe_class(pipe_class.get_default_config())

    with pytest.raises(pipe_module.GenerationExecutionError):
        pipe.process(PipeInput(input={}), lambda o: None)


def test_cancellation_stops_before_processing_later_images(pipe_class, fake_nvvfx, no_real_cuda):
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] > 1  # let the first image through, stop before the second

    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 2.0})
    images = [_rgb_image(16, 16), _rgb_image(16, 16), _rgb_image(16, 16)]
    result = pipe.process(
        PipeInput(input={"image": images}),
        lambda o: None,
        is_cancelled,
    )

    assert len(result.output["image"]) == 1
