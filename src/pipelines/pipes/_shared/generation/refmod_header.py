from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from src.platform.runtime.model_headers.reader import read_safetensors_header

MAX_PACKED_REFERENCES = 12
MAX_COPIES = MAX_PACKED_REFERENCES
MEMBER_KINDS = ("image", "video", "audio")


META_KEYS = ("refmod_meta", "audio_refmod_meta")


def _read_meta(metadata: Mapping[str, str], file_path: str) -> dict[str, Any]:
    """The upstream metadata block: one JSON string in the safetensors header
    under `refmod_meta` (audio-only files: `audio_refmod_meta`), or, for files
    written by older versions, a `<file>.json` sidecar next to the tensors."""
    for key in META_KEYS:
        if key in metadata:
            try:
                meta = json.loads(metadata[key])
            except (TypeError, ValueError) as error:
                raise ValueError(f"RefMod {file_path!r}: header {key!r} is not valid JSON") from error
            break
    else:
        sidecar = Path(file_path).with_suffix(".json")
        if not sidecar.is_file():
            raise ValueError(
                f"RefMod {file_path!r}: no {META_KEYS[0]!r} header and no {sidecar.name} sidecar -- not a RefMod file"
            )
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise ValueError(f"RefMod {file_path!r}: metadata block must be a JSON object")
    return meta


def _parse_members(meta: Mapping[str, Any], *, source: str) -> list[dict[str, Any]]:
    """`[{"kind", "name", "tensor_key"}, ...]`: a bundle (`kind == "bundle"`,
    tensors `ref_{i}` per `members` entry) or a single-member file (tensor
    `latent`, kind/name at the top level)."""
    if meta.get("kind") == "bundle":
        members = meta.get("members")
        if not isinstance(members, list) or not members:
            raise ValueError(f"RefMod bundle {source!r}: 'members' must be a non-empty list")
        parsed = []
        for index, member in enumerate(members):
            if not isinstance(member, dict) or "kind" not in member:
                raise ValueError(f"RefMod bundle {source!r}: member {index} is missing its 'kind'")
            parsed.append({"kind": member["kind"], "name": member.get("name"), "tensor_key": f"ref_{index}"})
        return parsed
    kind = meta.get("kind")
    if kind is None:
        raise ValueError(f"RefMod {source!r}: metadata has no 'kind'")
    return [{"kind": kind, "name": meta.get("name"), "tensor_key": "latent"}]


@dataclass(frozen=True)
class RefModMember:
    kind: str
    name: str | None


def read_refmod_members(file_path: str) -> tuple[RefModMember, ...]:
    try:
        stat = os.stat(file_path)
    except OSError as error:
        raise ValueError(f"RefMod {file_path!r} cannot be read: {error.strerror or error}") from error
    return _read_refmod_members(str(file_path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=256)
def _read_refmod_members(file_path: str, mtime_ns: int, size: int) -> tuple[RefModMember, ...]:
    try:
        metadata = read_safetensors_header(file_path).get("__metadata__") or {}
    except (OSError, ValueError) as error:
        raise ValueError(f"RefMod {file_path!r} is not a readable safetensors file: {error}") from error
    members = _parse_members(_read_meta(metadata, file_path), source=file_path)
    for index, member in enumerate(members):
        if member["kind"] not in MEMBER_KINDS:
            raise ValueError(
                f"RefMod bundle {file_path!r}: member {index} has kind {member['kind']!r}, expected "
                f"'image', 'video' or 'audio'"
            )
    return tuple(RefModMember(kind=member["kind"], name=member.get("name")) for member in members)


def refmod_entry_active(strength: Any) -> bool:
    try:
        return float(1.0 if strength is None or strength == "" else strength) > 0
    except (TypeError, ValueError):
        return True


def refmod_copies(value: Any, file_path: str) -> int:
    if value is None or value == "":
        return 1
    try:
        copies = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"RefMod {file_path!r}: copies must be a whole number, got {value!r}") from error
    if not copies.is_integer() or not 1 <= copies <= MAX_COPIES:
        raise ValueError(
            f"RefMod {file_path!r}: copies must be a whole number from 1 to {MAX_COPIES}, got {value!r}"
        )
    return int(copies)
