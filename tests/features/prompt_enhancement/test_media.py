import base64
from io import BytesIO

import pytest
from PIL import Image

from src.features.prompt_enhancement import media
from src.features.prompt_enhancement.operations import PromptWriteRefused


def save_image(path, size=(32, 16)):
    Image.new("RGB", size, (200, 10, 10)).save(path)


@pytest.mark.asyncio
async def test_images_are_read_relative_to_the_storage_root(tmp_path):
    (tmp_path / "uploads").mkdir()
    save_image(tmp_path / "uploads" / "a.png")
    encoded = await media.encode_media(str(tmp_path), [{"value": "uploads/a.png", "media_type": "image"}])
    assert len(encoded) == 1
    assert Image.open(BytesIO(base64.b64decode(encoded[0]))).size == (32, 16)


@pytest.mark.asyncio
async def test_videos_become_downscaled_frames(tmp_path, monkeypatch):
    seen = {}

    def frames(path, count):
        seen["args"] = (path, count)
        return [Image.new("RGB", (2048, 1024)), Image.new("RGB", (640, 480))]

    import src.features.content_safety.gate as gate

    monkeypatch.setattr(gate, "extract_video_frames", frames)
    encoded = await media.encode_media(str(tmp_path), [{"value": "generations/v.mp4", "media_type": "video"}])
    assert seen["args"] == (str(tmp_path / "generations" / "v.mp4"), media.FRAMES_PER_VIDEO)
    sizes = [Image.open(BytesIO(base64.b64decode(item))).size for item in encoded]
    assert sizes == [(1024, 512), (640, 480)]


@pytest.mark.asyncio
async def test_unreadable_video_is_refused(tmp_path, monkeypatch):
    import src.features.content_safety.gate as gate
    from src.features.content_safety.errors import ContentCheckUnavailable

    def broken(path, count):
        raise ContentCheckUnavailable("no frames")

    monkeypatch.setattr(gate, "extract_video_frames", broken)
    with pytest.raises(PromptWriteRefused) as caught:
        await media.encode_media(str(tmp_path), [{"value": "v.mp4", "media_type": "video"}])
    assert caught.value.code == "media_unreadable"
    assert str(caught.value) == "Video 1 could not be read."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("item", "code", "message"),
    [
        ({"value": "missing.png", "media_type": "image"}, "media_unreadable", "File 1 could not be read."),
        ({"value": "", "media_type": "image"}, "media_unreadable", "File 1 has no stored file."),
        ({"value": "a.wav", "media_type": "audio"}, "media_unsupported", "File 1 is not an image or a video."),
    ],
)
async def test_bad_media_is_refused(tmp_path, item, code, message):
    with pytest.raises(PromptWriteRefused) as caught:
        await media.encode_media(str(tmp_path), [item])
    assert caught.value.code == code
    assert str(caught.value) == message
