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
    assert seen["args"] == (str((tmp_path / "generations" / "v.mp4").resolve()), media.FRAMES_PER_VIDEO)
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


@pytest.mark.asyncio
async def test_extra_video_frames_are_dropped(tmp_path, monkeypatch):
    import src.features.content_safety.gate as gate

    monkeypatch.setattr(gate, "extract_video_frames", lambda path, count: [Image.new("RGB", (8, 8)) for _ in range(count + 4)])
    encoded = await media.encode_media(str(tmp_path), [{"value": "v.mp4", "media_type": "video"}])
    assert len(encoded) == media.FRAMES_PER_VIDEO


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["/etc/passwd", "../outside.png", "uploads/../../outside.png", "uploads/escape.png"])
async def test_paths_outside_the_storage_root_are_refused(tmp_path, value):
    root = tmp_path / "storage"
    (root / "uploads").mkdir(parents=True)
    save_image(tmp_path / "outside.png")
    (root / "uploads" / "escape.png").symlink_to(tmp_path / "outside.png")
    with pytest.raises(PromptWriteRefused) as caught:
        await media.encode_media(str(root), [{"value": value, "media_type": "image"}])
    assert caught.value.code == "media_outside_storage"
    assert str(caught.value) == "File 1 is not in the media storage."


@pytest.mark.asyncio
async def test_an_image_over_five_megabytes_is_refused(tmp_path):
    (tmp_path / "big.png").write_bytes(b"\0" * (media.MAX_IMAGE_BYTES + 1))
    with pytest.raises(PromptWriteRefused) as caught:
        await media.encode_media(str(tmp_path), [{"value": "big.png", "media_type": "image"}])
    assert caught.value.code == "media_too_large"


@pytest.mark.asyncio
async def test_more_files_than_the_cap_are_refused_before_reading(tmp_path):
    items = [{"value": f"missing-{n}.png", "media_type": "image"} for n in range(media.MAX_MEDIA_PER_CALL + 1)]
    with pytest.raises(PromptWriteRefused) as caught:
        await media.encode_media(str(tmp_path), items)
    assert caught.value.code == "too_much_media"
    assert media.MAX_MEDIA_PER_CALL == 4
