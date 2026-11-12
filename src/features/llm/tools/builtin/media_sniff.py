from typing import Optional, Tuple

from src.features.media.media_types import sniff_media_extension

ALLOWED = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
}


def sniff_media(data: bytes) -> Optional[Tuple[str, str]]:
    extension = sniff_media_extension(data[:16])
    if extension == ".mp4" and data[8:12] == b"M4A ":
        extension = ".m4a"
    mime = ALLOWED.get(extension or "")
    return (mime, extension) if mime else None
