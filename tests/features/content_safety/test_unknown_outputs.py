from dataclasses import dataclass

import pytest
from PIL import Image

from src.pipelines.outputs import GenerationOutput, TextGenerationOutput
from tests.features.content_safety.fakes import build


@dataclass(kw_only=True)
class PluginPreviewOutput(GenerationOutput):
    image: object = None
    temporary: bool = True


@dataclass(kw_only=True)
class PluginTextWithCover(TextGenerationOutput):
    cover: object = None


def pixels():
    return Image.new("RGB", (2, 2))


async def test_a_plugin_output_with_an_image_is_rated_and_suppressed_when_flagged():
    manager, tagger, _ = build("blocked", [0.95])
    output = PluginPreviewOutput(image=pixels())

    outcome = await manager.gate_output("g1", "u1", output)

    assert tagger.calls == [1]
    assert outcome.output is output
    assert output._preview_suppressed is True


async def test_a_safe_plugin_image_is_delivered_unflagged():
    manager, _, _ = build("blocked", [0.05])
    output = PluginPreviewOutput(image=pixels())

    await manager.gate_output("g1", "u1", output)

    assert output._preview_suppressed is False


async def test_a_flagged_final_plugin_image_is_dropped():
    manager, _, _ = build("blocked", [0.95])
    output = PluginPreviewOutput(image=pixels(), temporary=False)

    outcome = await manager.gate_output("g1", "u1", output)

    assert outcome.output is None
    assert outcome.blocked == 1


async def test_a_plugin_output_without_rateable_media_is_reduced_to_the_allowlist():
    manager, tagger, _ = build("blocked", [0.0])
    output = PluginPreviewOutput(image=None)

    outcome = await manager.gate_output("g1", "u1", output)

    assert tagger.calls == []
    assert outcome.output is output
    assert output._preview_suppressed is True


async def test_a_subclass_of_a_core_type_counts_as_unknown():
    manager, tagger, _ = build("blocked", [0.95])
    output = PluginTextWithCover(title="t", text="x", cover=pixels())

    await manager.gate_output("g1", "u1", output)

    assert tagger.calls == [1]
    assert output._preview_suppressed is True


async def test_core_non_media_outputs_pass_untouched():
    manager, tagger, _ = build("blocked", [0.95])
    output = TextGenerationOutput(title="t", text="x")

    await manager.gate_output("g1", "u1", output)

    assert tagger.calls == []
    assert not hasattr(output, "_preview_suppressed")


async def test_allowed_users_are_not_touched_by_unknown_output_handling():
    manager, tagger, _ = build("allowed", [0.95])
    output = PluginPreviewOutput(image=pixels())

    await manager.gate_output("g1", "u1", output)

    assert tagger.calls == []
    assert not hasattr(output, "_preview_suppressed")
