"""Signature detection for files that arrive with no usable extension."""

import io

import pytest
from PIL import Image

from src.features.media.media_types import MediaTypeResolver, sniff_media_extension


def _encoded(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color="purple").save(buf, format=fmt)
    return buf.getvalue()


class TestSniffMediaExtension:
    @pytest.mark.parametrize("fmt,expected", [("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp"), ("BMP", ".bmp")])
    def test_real_encoded_images(self, fmt, expected):
        assert sniff_media_extension(_encoded(fmt)) == expected

    @pytest.mark.parametrize(
        "content,expected",
        [
            (b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00", ".mp4"),
            (b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00", ".mov"),
            (b"\x1a\x45\xdf\xa3\x01\x00\x00\x00", ".webm"),
            (b"RIFF\x24\x00\x00\x00WAVEfmt ", ".wav"),
            (b"RIFF\x24\x00\x00\x00AVI LIST", ".avi"),
            (b"fLaC\x00\x00\x00\x22", ".flac"),
            (b"OggS\x00\x02\x00\x00", ".ogg"),
            (b"ID3\x04\x00\x00\x00", ".mp3"),
            (b"\xff\xfb\x90\x00", ".mp3"),
            (b"glTF\x02\x00\x00\x00", ".glb"),
            (b"GIF89a\x01\x00", ".gif"),
        ],
    )
    def test_container_signatures(self, content, expected):
        assert sniff_media_extension(content) == expected

    def test_every_sniffed_extension_is_one_a_registry_claims(self):
        """A sniffed extension nothing recognises would just re-create the bug."""
        resolver = MediaTypeResolver()
        for content in (
            _encoded("PNG"),
            b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00",
            b"RIFF\x24\x00\x00\x00WAVEfmt ",
            b"glTF\x02\x00\x00\x00",
            b"\x1a\x45\xdf\xa3\x01\x00\x00\x00",
        ):
            ext = sniff_media_extension(content)
            assert ext is not None
            assert (
                resolver.is_image(ext)
                or resolver.is_video(ext)
                or resolver.is_audio(ext)
                or resolver.is_mesh(ext)
            ), f"sniffed {ext!r} which no registry claims"

    @pytest.mark.parametrize("content", [b"", b"\x00\x01\x02\x03junk", b"RIFF\x00\x00\x00\x00NOPE"])
    def test_unrecognised_bytes_return_none(self, content):
        assert sniff_media_extension(content) is None
