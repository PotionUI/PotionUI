"""A1111/Forge/SD.Next/InvokeAI `styles.csv`."""
import csv
import io
import uuid
from typing import List, Optional, Union

from src.features.prompt_database.importing.models import ParsedPrompt


def parse_styles_csv(data: Union[bytes, str], *, filename: Optional[str] = None) -> List[ParsedPrompt]:
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    columns: dict = {}
    for column in reader.fieldnames:
        key = (column or "").strip().lower()
        if key == "prompt":
            columns["prompt"] = column
        elif key == "text" and "prompt" not in columns:
            # `text` is the legacy A1111 header for the same column.
            columns["prompt"] = column
        elif key == "negative_prompt":
            columns["negative_prompt"] = column
        elif key == "name":
            columns["name"] = column

    prompt_col = columns.get("prompt")
    negative_col = columns.get("negative_prompt")
    name_col = columns.get("name")

    results: List[ParsedPrompt] = []
    for row in reader:
        prompt_text = (row.get(prompt_col) or "").strip() if prompt_col else ""
        negative_text = (row.get(negative_col) or "").strip() if negative_col else ""
        if not prompt_text and not negative_text:
            continue
        name = (row.get(name_col) or "").strip() or None if name_col else None
        group_id = uuid.uuid4().hex if prompt_text and negative_text else None
        if prompt_text:
            results.append(ParsedPrompt(text=prompt_text, usage_hint="positive", name=name, group_id=group_id))
        if negative_text:
            results.append(ParsedPrompt(text=negative_text, usage_hint="negative", name=name, group_id=group_id))
    return results
