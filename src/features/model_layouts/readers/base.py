from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from src.features.model_layouts.wsl import PathTranslator, default_translator

MAX_CONFIG_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ConfigEntry:
    model_type: str
    path: str
    key: str
    outside_root: bool = False
    section: Optional[str] = None
    is_default: bool = False


@dataclass
class ReaderResult:
    kind: str
    source_file: Optional[str] = None
    entries: List[ConfigEntry] = field(default_factory=list)
    primary_root: Optional[str] = None
    primary_outside_root: bool = False
    extra_roots: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def paths(self) -> Dict[str, List[str]]:
        grouped: Dict[str, List[str]] = {}
        for entry in self.entries:
            bucket = grouped.setdefault(entry.model_type, [])
            if entry.path not in bucket:
                bucket.append(entry.path)
        return grouped

    @property
    def outside_root(self) -> bool:
        return self.primary_outside_root or any(entry.outside_root for entry in self.entries)


class ReaderContext:
    def __init__(self, install_dir: Path, root: Optional[Union[str, Path]], translator: Optional[PathTranslator]):
        self.install_dir = Path(install_dir)
        self.root = root
        self.translator = translator or default_translator()

    def resolve(self, raw: Any, base: Path, result: ReaderResult) -> Optional[str]:
        if not isinstance(raw, str) or not raw.strip():
            return None
        translated = self.translator.translate(raw)
        if translated.path is None:
            if translated.warning:
                result.warnings.append(translated.warning)
            return None
        flavor = self.translator.flavor
        joined = translated.path if flavor.isabs(translated.path) else flavor.join(str(base), translated.path)
        return flavor.normpath(joined)

    def is_outside(self, path: str) -> bool:
        if self.root is None:
            return False
        flavor = self.translator.flavor
        target = flavor.normcase(flavor.normpath(path))
        anchor = flavor.normcase(flavor.normpath(str(self.root)))
        try:
            return flavor.commonpath([target, anchor]) != anchor
        except ValueError:
            return True

    def add_entry(
        self,
        result: ReaderResult,
        model_type: str,
        path: str,
        key: str,
        section: Optional[str] = None,
        is_default: bool = False,
    ) -> None:
        result.entries.append(
            ConfigEntry(
                model_type=model_type,
                path=path,
                key=key,
                outside_root=self.is_outside(path),
                section=section,
                is_default=is_default,
            )
        )

    def set_primary(self, result: ReaderResult, path: str) -> None:
        result.primary_root = path
        result.primary_outside_root = self.is_outside(path)


ConfigReader = Callable[[ReaderContext, Optional[str], ReaderResult], None]


def find_child_ci(base: Path, relative: str) -> Optional[Path]:
    current = Path(base)
    for segment in [part for part in relative.replace("\\", "/").split("/") if part]:
        exact = current / segment
        if exact.exists():
            current = exact
            continue
        try:
            wanted = segment.casefold()
            match = next((child for child in current.iterdir() if child.name.casefold() == wanted), None)
        except OSError:
            return None
        if match is None:
            return None
        current = match
    return current


def read_bounded_text(path: Path, result: ReaderResult, limit: int = MAX_CONFIG_BYTES) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size > limit:
            result.warnings.append(f"{path.name} is {size} bytes, over the {limit} byte limit; skipped")
            return None
        with open(path, "rb") as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        result.warnings.append(f"Could not read {path.name}: {exc}")
        return None
    if len(raw) > limit:
        result.warnings.append(f"{path.name} is over the {limit} byte limit; skipped")
        return None
    text = raw.decode("utf-8", errors="replace")
    return text[1:] if text.startswith("﻿") else text


def read_json_object(path: Path, result: ReaderResult) -> Optional[Dict[str, Any]]:
    text = read_bounded_text(path, result)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        result.warnings.append(f"{path.name} is not valid JSON: {exc}")
        return None
    if not isinstance(data, dict):
        result.warnings.append(f"{path.name} must contain a JSON object")
        return None
    return data


def lower_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    lowered: Dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(key, str):
            lowered.setdefault(key.casefold(), value)
    return lowered
