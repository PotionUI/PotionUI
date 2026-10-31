import json

import pytest
import base64
import io

from PIL import Image

from src.features.content_safety.output_gate import apply_verdicts, gate_items
from src.features.generation.failure import scope_live_message
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.pipelines.outputs import PreviewClipGenerationOutput
from src.platform.runtime.native.sampling.preview_clip import build_sprite_clip

import numpy as np


def make_output():
    frames = [np.full((36, 64, 3), 30 * i, dtype=np.uint8) for i in range(6)]
    clip = build_sprite_clip(frames, [0, 4, 8, 12, 16, 20], step=9, total_steps=30)
    clip.update_index, clip.updates_max, clip.every = 3, 8, 3
    return PreviewClipGenerationOutput(
        sprite=clip.sprite,
        encoded=clip.encoded,
        frames=clip.frames,
        columns=clip.columns,
        frame_width=clip.frame_width,
        frame_height=clip.frame_height,
        fps=clip.fps,
        step=clip.step,
        total_steps=clip.total_steps,
        source_indices=clip.source_indices,
        long_edge=clip.long_edge,
        update_index=clip.update_index,
        updates_max=clip.updates_max,
        every=clip.every,
        pipe_id=2,
        pipe_name="generator",
    )


def serialize(output):
    return GenerationOutputSerializer(generation_id="g1").serialize_output(output)


def test_the_message_type_and_clip_metadata():
    message = serialize(make_output())
    assert message["type"] == "workbench_preview_clip"
    assert message["generation_id"] == "g1"
    assert message["pipe_id"] == 2
    assert message["temporary"] is True
    assert (message["frames"], message["columns"]) == (6, 3)
    assert (message["frame_width"], message["frame_height"]) == (64, 36)
    assert message["fps"] == 8
    assert (message["step"], message["total_steps"]) == (9, 30)
    assert message["source_indices"] == [0, 4, 8, 12, 16, 20]


def test_the_image_is_a_webp_sprite_data_url():
    message = serialize(make_output())
    prefix = "data:image/webp;base64,"
    assert message["image"].startswith(prefix)
    sprite = Image.open(io.BytesIO(base64.b64decode(message["image"][len(prefix):])))
    assert sprite.format == "WEBP"
    assert sprite.size == (64 * 3, 36 * 2)


def test_admin_detail_reaches_admins_only():
    message = serialize(make_output())
    admin = scope_live_message(message, True)
    user = scope_live_message(message, False)
    assert admin["admin_detail"]["format"] == "WEBP"
    assert admin["admin_detail"]["update"] == 3
    assert admin["admin_detail"]["updates_max"] == 8
    assert admin["admin_detail"]["every"] == 3
    assert "admin_detail" not in user
    assert user["image"] == message["image"]
    assert "admin_detail" in message


def test_the_content_gate_scores_the_sprite_like_an_image_preview():
    output = make_output()
    items = gate_items(output)
    assert len(items) == 1
    outcome = apply_verdicts(output, items, [0.95], 0.6, "blocked", False)
    assert outcome.output is output
    assert output._preview_suppressed is True


def test_a_suppressed_clip_serializes_without_pixels():
    output = make_output()
    apply_verdicts(output, gate_items(output), [0.95], 0.6, "blocked", False)
    message = serialize(output)
    assert "image" not in message
    assert message["preview_suppressed"] is True


@pytest.mark.asyncio
async def test_a_regular_subscriber_never_receives_admin_detail_but_an_admin_does():
    from tests.features.generation.test_output_broadcaster import _broadcaster, _subscribe

    broadcaster, hub, recorder = _broadcaster()
    user = await _subscribe(hub, "user", "gen-1")
    admin = await _subscribe(hub, "admin", "gen-1", privileged=True)

    await broadcaster.handle_output("gen-1", make_output())

    assert [m["type"] for m in user.sent] == ["workbench_preview_clip"]
    assert "admin_detail" not in user.sent[0]
    assert "admin_detail" not in json.dumps(user.sent)
    assert user.sent[0]["image"].startswith("data:image/webp;base64,")
    assert admin.sent[0]["admin_detail"]["format"] == "WEBP"
