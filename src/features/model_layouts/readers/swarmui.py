from __future__ import annotations

from typing import List, Optional, Tuple

from src.features.model_layouts.readers.base import (
    ReaderContext,
    ReaderResult,
    find_child_ci,
    read_bounded_text,
)

DEFAULT_FILE = "Data/Settings.fds"

FOLDER_KEY_TO_MODEL_TYPE = {
    "sdmodelfolder": "checkpoint",
    "sdlorafolder": "lora",
    "sdvaefolder": "vae",
    "sdembeddingfolder": "embedding",
    "sdcontrolnetsfolder": "controlnet",
    "sdclipfolder": "text_encoder",
}


def _indent_width(line: str) -> int:
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += 4
        else:
            break
    return width


def parse_fds(text: str) -> List[Tuple[Tuple[str, ...], str]]:
    entries: List[Tuple[Tuple[str, ...], str]] = []
    stack: List[Tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent_width(raw)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        name, separator, value = stripped.partition(":")
        if not separator:
            continue
        name = name.strip().casefold()
        value = value.strip()
        if value:
            entries.append((tuple(item[1] for item in stack) + (name,), value))
        else:
            stack.append((indent, name))
    return entries


def read(ctx: ReaderContext, file: Optional[str], result: ReaderResult) -> None:
    path = find_child_ci(ctx.install_dir, file or DEFAULT_FILE)
    if path is None:
        return
    result.source_file = str(path)
    text = read_bounded_text(path, result)
    if text is None:
        return
    paths = {key[1]: value for key, value in parse_fds(text) if len(key) == 2 and key[0] == "paths"}
    model_root = paths.get("modelroot")
    if not model_root:
        return
    roots = []
    for part in model_root.split(";"):
        if not part.strip():
            continue
        resolved = ctx.resolve(part, ctx.install_dir, result)
        if resolved:
            roots.append(resolved)
    if not roots:
        return
    ctx.set_primary(result, roots[0])
    result.extra_roots.extend(root for root in roots[1:] if root not in result.extra_roots)
    for key, model_type in FOLDER_KEY_TO_MODEL_TYPE.items():
        value = paths.get(key)
        if not value:
            continue
        for part in value.split(";"):
            resolved = ctx.resolve(part, roots[0], result)
            if resolved:
                ctx.add_entry(result, model_type, resolved, key)
