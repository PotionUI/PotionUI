"""Guess an import format from a filename extension, falling back to sniffing bytes."""
import re
from typing import Optional, Union

_EXTENSIONS = {
    "csv": "styles_csv",
    "json": "style_json",
    "yaml": "wildcard_yaml",
    "yml": "wildcard_yaml",
    "txt": "lines",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "webp": "image",
}


def _looks_like_image(data: bytes) -> bool:
    if data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff"):
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def detect_format(filename: Optional[str], data: Union[bytes, str]) -> str:
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].strip().lower()
        if ext in _EXTENSIONS:
            return _EXTENSIONS[ext]

    if isinstance(data, bytes):
        if _looks_like_image(data):
            return "image"
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return "image"
    else:
        text = data

    stripped = text.lstrip()
    if stripped[:1] in "[{":
        return "style_json"

    lines = stripped.split("\n")
    first_line = lines[0] if lines else ""
    if "," in first_line and "prompt" in first_line.lower():
        return "styles_csv"

    for index in range(len(lines) - 1):
        if re.match(r"^\s*[\w-]+:\s*$", lines[index]) and re.match(r"^\s*-\s+\S", lines[index + 1]):
            return "wildcard_yaml"

    return "lines"
