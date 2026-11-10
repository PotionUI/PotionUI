import json
from typing import Any, Dict, List

from .document import RegionalPromptDocument, pixel_box, quoted_spans


def compile_moss_prompt(prompt: str, document: RegionalPromptDocument, width: int, height: int) -> str:
    if not document.enabled:
        return prompt
    half_w, half_h = width // 2, height // 2
    items: List[Dict[str, Any]] = []
    for region in document.items:
        caption = region.caption.strip()
        if not caption:
            continue
        left, top, right, bottom = pixel_box(region, width, height)
        if right <= left or bottom <= top:
            continue
        entry: Dict[str, Any] = {"label": "object", "caption": caption}
        spans = quoted_spans(caption)
        if spans:
            entry["label"] = "text"
            entry["text"] = "\n".join(spans)
        entry["bbox"] = [top - half_h, left - half_w, bottom - half_h, right - half_w]
        items.append(entry)
    if not items:
        return prompt
    return json.dumps(
        {
            "summary": {"caption": prompt},
            "items": items,
            "bbox_coord_space": "abs_centralized",
            "bbox_format": "yxyx",
        },
        ensure_ascii=False,
    )
