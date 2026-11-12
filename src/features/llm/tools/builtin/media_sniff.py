from typing import Optional, Tuple

_FIXED = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"\x1a\x45\xdf\xa3", "video/webm", ".webm"),
    (b"OggS", "audio/ogg", ".ogg"),
    (b"fLaC", "audio/flac", ".flac"),
    (b"ID3", "audio/mpeg", ".mp3"),
)
_MPEG_AUDIO_SYNC = (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


def sniff_media(data: bytes) -> Optional[Tuple[str, str]]:
    head = data[:16]
    for magic, mime, extension in _FIXED:
        if head.startswith(magic):
            return mime, extension
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav", ".wav"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return "video/quicktime", ".mov"
        if brand == b"M4A ":
            return "audio/mp4", ".m4a"
        return "video/mp4", ".mp4"
    if head[:2] in _MPEG_AUDIO_SYNC:
        return "audio/mpeg", ".mp3"
    return None
