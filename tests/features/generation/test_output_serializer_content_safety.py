from PIL import Image

from src.features.generation.content_blocked_output import ContentBlockedGenerationOutput
from src.features.generation.error_classification import classification_for_code
from src.features.generation.failure import failure_for_code
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.pipelines.outputs import (
    CompareImagesGenerationOutput,
    GalleryGenerationOutput,
    ImageGenerationOutput,
    ProgressGenerationOutput,
)


def serialize(output):
    return GenerationOutputSerializer(generation_id="g1", preset_id="p").serialize_output(output)


def image_output(temporary=False, is_artifact=False):
    return ImageGenerationOutput(
        image=Image.new("RGB", (8, 8)), temporary=temporary, isArtifact=is_artifact, label="Control"
    )


def test_unmarked_media_reports_all_flags_off():
    message = serialize(image_output(temporary=True))

    assert message["nsfw"] is False
    assert message["content_flagged"] is False
    assert message["preview_suppressed"] is False
    assert message["image"]


def test_flagged_output_carries_the_flags():
    output = image_output()
    output._content_nsfw = True
    output._content_flagged = True

    message = serialize(output)

    assert message["nsfw"] is True
    assert message["content_flagged"] is True
    assert message["image"]


def test_suppressed_preview_has_no_pixels_and_no_urls():
    output = image_output(temporary=True)
    output._content_nsfw = True
    output._preview_suppressed = True

    message = serialize(output)

    assert message["preview_suppressed"] is True
    assert "image" not in message
    assert "path" not in message
    assert message["type"] == "workbench_update"


def test_suppressed_artifact_keeps_its_label_but_loses_the_image():
    output = image_output(temporary=True, is_artifact=True)
    output._preview_suppressed = True

    message = serialize(output)

    assert message["type"] == "pipe_artifact"
    assert message["artifact_data"] == {"label": "Control"}


def test_suppressed_compare_artifact_loses_both_images():
    image = Image.new("RGB", (8, 8))
    output = CompareImagesGenerationOutput(index=0, compare=("a", image), to=("b", image))
    output._preview_suppressed = True

    message = serialize(output)

    assert message["artifact_data"] == {"label": None}
    assert message["preview_suppressed"] is True


def test_gallery_reports_flags_and_per_image_flags():
    flagged = image_output()
    flagged._saved_path = "outputs/2026-01-01/g1/0.png"
    flagged._content_nsfw = True
    flagged._content_flagged = True
    gallery = GalleryGenerationOutput(images=[flagged])
    gallery._content_nsfw = True
    gallery._content_flagged = True

    message = serialize(gallery)

    assert message["nsfw"] is True
    assert message["content_flagged"] is True
    assert message["image_urls_list"][0]["content_flagged"] is True


def test_non_media_messages_carry_no_content_flags():
    message = serialize(ProgressGenerationOutput(state="running"))

    assert "nsfw" not in message
    assert "preview_suppressed" not in message


def test_content_blocked_notice_payload():
    message = serialize(ContentBlockedGenerationOutput(blocked_count=2, total=4, pipe_id=3))

    assert message["type"] == "content_blocked"
    assert message["generation_id"] == "g1"
    assert message["pipe_id"] == 3
    assert message["blocked_count"] == 2
    assert message["total"] == 4


def test_new_error_categories_have_user_copy_and_hints():
    for code in ("banned_prompt", "content_blocked", "content_check_unavailable"):
        classification = classification_for_code(code)

        assert classification.category == code
        assert classification.summary
        assert classification.suggestions


def test_banned_prompt_message_never_names_a_word():
    failure = failure_for_code("banned_prompt")

    assert failure.error_code == "banned_prompt"
    assert failure.raw_error is None
    assert "content policy" in failure.message


import re
from dataclasses import dataclass
from pathlib import Path

import pytest

import src.features.generation.handlers
from src.features.generation.output_serializer import ALLOWED_SUPPRESSED_KEYS
from src.features.generation.output_types import OutputTypeSpec, output_type_registry
from src.pipelines.outputs import (
    AudioGenerationOutput,
    GenerationOutput,
    MeshGenerationOutput,
    VideoGenerationOutput,
)

_BASE64 = re.compile(r"[A-Za-z0-9+/=]{80,}")


def strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings_in(key)
            yield from strings_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings_in(item)


def assert_leaks_nothing(message):
    assert set(message) <= ALLOWED_SUPPRESSED_KEYS
    for text in strings_in(message):
        assert "/api/media" not in text
        assert not text.startswith("data:")
        assert not _BASE64.search(text)


def saved_image():
    output = image_output()
    output._saved_path = "outputs/2026-01-01/g1/0.png"
    return output


def saved_video():
    output = VideoGenerationOutput(video_path=Path("clip.mp4"), temporary=False)
    output._saved_path = "outputs/2026-01-01/g1/clip.mp4"
    return output


def full_gallery():
    return GalleryGenerationOutput(
        images=[saved_image()],
        videos=[saved_video()],
        audios=[AudioGenerationOutput(audio_path=Path("a.wav"), temporary=False)],
        meshes=[MeshGenerationOutput(mesh_path=Path("m.glb"), temporary=False)],
    )


def compare_output():
    image = Image.new("RGB", (8, 8))
    return CompareImagesGenerationOutput(index=0, compare=("a", image), to=("b", image))


@pytest.mark.parametrize("factory", [
    lambda: image_output(temporary=True),
    saved_image,
    lambda: image_output(temporary=True, is_artifact=True),
    saved_video,
    full_gallery,
    compare_output,
    lambda: AudioGenerationOutput(audio_path=Path("a.wav"), temporary=False),
    lambda: MeshGenerationOutput(mesh_path=Path("m.glb"), temporary=False),
])
def test_a_suppressed_output_keeps_only_allowlisted_keys(factory):
    output = factory()
    output._preview_suppressed = True

    message = serialize(output)

    assert message["preview_suppressed"] is True
    assert_leaks_nothing(message)


def test_the_unsuppressed_gallery_does_carry_its_media():
    message = serialize(full_gallery())

    assert message["images"] and message["video_urls_list"] is not None
    assert "images" not in ALLOWED_SUPPRESSED_KEYS


@dataclass(kw_only=True)
class PluginPreviewOutput(GenerationOutput):
    image: object
    temporary: bool = True


def serialize_plugin_preview(output, ctx):
    return {"cover": "data:image/png;base64," + "A" * 200, "url": "/api/media/x.png", "extra_key": "value"}


@pytest.fixture
def plugin_type():
    spec = OutputTypeSpec(
        output_cls=PluginPreviewOutput, key="plugin_preview",
        message_type="workbench_update", serializer=serialize_plugin_preview,
    )
    output_type_registry.register(spec)
    yield spec
    output_type_registry._by_key.pop("plugin_preview", None)
    output_type_registry._by_cls.pop(PluginPreviewOutput, None)


def test_a_suppressed_plugin_output_cannot_smuggle_extra_keys(plugin_type):
    output = PluginPreviewOutput(image=Image.new("RGB", (2, 2)))
    output._preview_suppressed = True

    message = serialize(output)

    assert_leaks_nothing(message)
    assert "extra_key" not in message
