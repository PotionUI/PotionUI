import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

TILE = 320
COLUMNS = 4
MAX_TILES = 12
LABEL_SIZE = 40


def located(storage_root: str, value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    root = Path(storage_root).resolve()
    candidate = (root / path).resolve()
    return candidate if candidate.is_relative_to(root) and candidate.is_file() else None


def load_tile(path: Path, media_type: Optional[str]):
    from PIL import Image

    try:
        if media_type == "video":
            from src.features.content_safety.gate import extract_video_frames

            frames = extract_video_frames(str(path), 1)
            image = frames[0] if frames else None
        else:
            with Image.open(path) as opened:
                image = opened.convert("RGB")
                image.load()
    except Exception:
        logger.debug("Could not read %s for a contact sheet", path, exc_info=True)
        return None
    if image is None:
        return None
    image = image.convert("RGB")
    image.thumbnail((TILE, TILE))
    return image


def _font():
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=LABEL_SIZE)
    except TypeError:
        return ImageFont.load_default()


def contact_sheet(tiles: Sequence[Tuple[int, Any]]) -> Optional[str]:
    from PIL import Image, ImageDraw

    shown: List[Tuple[int, Any]] = [(number, image) for number, image in tiles if image is not None][:MAX_TILES]
    if not shown:
        return None
    columns = min(COLUMNS, len(shown))
    rows = (len(shown) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * TILE, rows * TILE), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = _font()
    for slot, (number, image) in enumerate(shown):
        left = (slot % columns) * TILE
        top = (slot // columns) * TILE
        sheet.paste(image, (left + (TILE - image.width) // 2, top + (TILE - image.height) // 2))
        label = str(number)
        box = draw.textbbox((0, 0), label, font=font)
        width, height = box[2] - box[0], box[3] - box[1]
        draw.rectangle((left, top, left + width + 16, top + height + 16), fill=(0, 0, 0))
        draw.text((left + 8 - box[0], top + 8 - box[1]), label, fill=(255, 255, 255), font=font)
    buffer = BytesIO()
    sheet.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("ascii")
