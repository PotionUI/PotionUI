import asyncio
import base64
from io import BytesIO
from pathlib import Path
from typing import Any, List, Mapping, Sequence

from src.features.llm.exceptions import ImageLoadFailedException
from src.features.llm.response_processor import LLMResponseProcessor
from src.features.prompt_enhancement.operations import PromptWriteRefused

FRAMES_PER_VIDEO = 3
FRAME_EDGE = 1024


def locate(storage_root: str, value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or path.exists():
        return path
    return Path(storage_root) / value


async def encode_media(
    storage_root: str,
    media: Sequence[Mapping[str, Any]],
    frames_per_video: int = FRAMES_PER_VIDEO,
) -> List[str]:
    processor = LLMResponseProcessor()
    encoded: List[str] = []
    for index, item in enumerate(media, start=1):
        value = item.get("value")
        kind = str(item.get("media_type") or "").lower()
        if not isinstance(value, str) or not value:
            raise PromptWriteRefused("media_unreadable", f"File {index} has no stored file.")
        path = locate(storage_root, value)
        if kind == "video":
            encoded.extend(await asyncio.to_thread(video_frames, path, frames_per_video, index))
            continue
        if kind != "image":
            raise PromptWriteRefused("media_unsupported", f"File {index} is not an image or a video.")
        try:
            encoded.append(await processor.load_and_prepare_image(str(path), storage_root, max_size_mb=5))
        except ImageLoadFailedException:
            raise PromptWriteRefused("media_unreadable", f"File {index} could not be read.") from None
    return encoded


def video_frames(path: Path, count: int, index: int) -> List[str]:
    from src.features.content_safety.errors import ContentCheckUnavailable
    from src.features.content_safety.gate import extract_video_frames

    try:
        frames = extract_video_frames(str(path), count)
    except ContentCheckUnavailable:
        raise PromptWriteRefused("media_unreadable", f"Video {index} could not be read.") from None
    encoded = []
    for frame in frames:
        frame = frame.convert("RGB")
        frame.thumbnail((FRAME_EDGE, FRAME_EDGE))
        buffer = BytesIO()
        frame.save(buffer, format="JPEG", quality=90)
        encoded.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
    return encoded
