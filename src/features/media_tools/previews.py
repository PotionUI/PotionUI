import hashlib
import json
import shutil
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from src.features.media_tools.dto import PreviewClip

FRAME_CACHE_BYTES = 64 * 1024 * 1024
FRAME_CACHE_ENTRIES = 256
ESTIMATE_CACHE_ENTRIES = 64
CLIP_CACHE_ENTRIES = 24
PREVIEW_ID_LENGTH = 32


def digest(parts: Any) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_preview_id(value: str) -> bool:
    return len(value) == PREVIEW_ID_LENGTH and all(char in "0123456789abcdef" for char in value)


@dataclass
class FramePreview:
    data: bytes
    media_type: str
    cached: bool


@dataclass
class _Clip:
    user_id: str
    path: Path
    dto: PreviewClip


class PreviewCache:
    def __init__(
        self,
        clip_dir_factory: Callable[[], Path],
        frame_bytes: int = FRAME_CACHE_BYTES,
        frame_entries: int = FRAME_CACHE_ENTRIES,
        clip_entries: int = CLIP_CACHE_ENTRIES,
    ):
        self._lock = threading.Lock()
        self._frames: "OrderedDict[Tuple[str, str], Tuple[bytes, str]]" = OrderedDict()
        self._frame_bytes = 0
        self._frame_budget = frame_bytes
        self._frame_entries = frame_entries
        self._estimates: "OrderedDict[Tuple, Any]" = OrderedDict()
        self._clips: "OrderedDict[Tuple[str, str], _Clip]" = OrderedDict()
        self._clip_ids: Dict[str, Tuple[str, str]] = {}
        self._clip_entries = clip_entries
        self._clip_dir_factory = clip_dir_factory
        self._clip_dir: Optional[Path] = None

    def frame(self, user_id: str, key: str) -> Optional[FramePreview]:
        with self._lock:
            found = self._frames.get((user_id, key))
            if found is None:
                return None
            self._frames.move_to_end((user_id, key))
            return FramePreview(data=found[0], media_type=found[1], cached=True)

    def store_frame(self, user_id: str, key: str, data: bytes, media_type: str) -> None:
        if len(data) > self._frame_budget:
            return
        with self._lock:
            previous = self._frames.pop((user_id, key), None)
            if previous is not None:
                self._frame_bytes -= len(previous[0])
            self._frames[(user_id, key)] = (data, media_type)
            self._frame_bytes += len(data)
            while self._frames and (
                self._frame_bytes > self._frame_budget or len(self._frames) > self._frame_entries
            ):
                _, (evicted, _) = self._frames.popitem(last=False)
                self._frame_bytes -= len(evicted)

    def memo(self, scope: str) -> Callable[[tuple, Callable[[], Any]], Any]:
        def remember(key: tuple, compute: Callable[[], Any]) -> Any:
            full = (scope, key)
            with self._lock:
                if full in self._estimates:
                    self._estimates.move_to_end(full)
                    return self._estimates[full]
            value = compute()
            with self._lock:
                self._estimates[full] = value
                while len(self._estimates) > ESTIMATE_CACHE_ENTRIES:
                    self._estimates.popitem(last=False)
            return value

        return remember

    def clip(self, user_id: str, key: str) -> Optional[PreviewClip]:
        with self._lock:
            found = self._clips.get((user_id, key))
            if found is None or not found.path.is_file():
                return None
            self._clips.move_to_end((user_id, key))
            return found.dto

    def clip_file(self, user_id: str, preview_id: str) -> Optional[Tuple[Path, str]]:
        with self._lock:
            slot = self._clip_ids.get(preview_id)
            found = self._clips.get(slot) if slot else None
            if found is None or found.user_id != user_id or not found.path.is_file():
                return None
            return found.path, found.dto.mime_type

    def store_clip(
        self, user_id: str, key: str, produced: Path, mime_type: str, **metadata: Any
    ) -> PreviewClip:
        preview_id = uuid.uuid4().hex
        with self._lock:
            directory = self._directory()
            dest = directory / f"{preview_id}{produced.suffix.lower()}"
            shutil.move(str(produced), str(dest))
            dto = PreviewClip(
                id=preview_id, url=f"/api/media-tools/previews/{preview_id}", mime_type=mime_type, **metadata
            )
            replaced = self._clips.pop((user_id, key), None)
            if replaced is not None:
                self._forget(replaced)
            self._clips[(user_id, key)] = _Clip(user_id=user_id, path=dest, dto=dto)
            self._clip_ids[preview_id] = (user_id, key)
            while len(self._clips) > self._clip_entries:
                _, evicted = self._clips.popitem(last=False)
                self._forget(evicted)
            return dto

    def clear(self) -> None:
        with self._lock:
            for clip in self._clips.values():
                clip.path.unlink(missing_ok=True)
            self._clips.clear()
            self._clip_ids.clear()
            self._frames.clear()
            self._frame_bytes = 0
            self._estimates.clear()

    def _forget(self, clip: _Clip) -> None:
        self._clip_ids.pop(clip.dto.id, None)
        clip.path.unlink(missing_ok=True)

    def _directory(self) -> Path:
        if self._clip_dir is None or not self._clip_dir.is_dir():
            self._clip_dir = self._clip_dir_factory()
        return self._clip_dir
