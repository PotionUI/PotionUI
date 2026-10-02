from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

DIRECTOR_MODE = "director"
CONTINUE_SUB_TYPE = "chain"
TEXT_TO_VIDEO = "txt2video"
IMAGE_TO_VIDEO = "img2video"
DEFAULT_FPS = 24.0


@dataclass(frozen=True)
class CloudShot:
    index: int
    count: int
    segment_id: str
    prompt: str
    negative_prompt: str
    seed: Optional[int]
    duration_s: Any
    start_image: Optional[Path] = None
    end_image: Optional[Path] = None
    continues: bool = False

    @property
    def task(self) -> str:
        return IMAGE_TO_VIDEO if self.start_image or self.end_image or self.continues else TEXT_TO_VIDEO

    @property
    def label(self) -> str:
        return f"Shot {self.index + 1} of {self.count}"


def _seconds(value: Any) -> Any:
    seconds = round(float(value), 3)
    return int(round(seconds)) if abs(seconds - round(seconds)) < 1e-6 else seconds


def _frames_to_seconds(frames: Any, fps: float) -> Any:
    seconds = float(frames) / fps
    nearest = round(seconds)
    if abs(frames - nearest * fps) <= 0.5:
        return int(nearest)
    return _seconds(seconds)


def _media_path(entry: Mapping[str, Any]) -> Optional[Path]:
    media = entry.get("media") or {}
    path = media.get("path") if isinstance(media, Mapping) else None
    return Path(path) if path else None


def _edges(document: Mapping[str, Any]) -> Dict[tuple, Path]:
    edges: Dict[tuple, Path] = {}
    for entry in document.get("media") or []:
        if not isinstance(entry, Mapping) or entry.get("role") not in ("first", "last"):
            continue
        path = _media_path(entry)
        if path is not None:
            edges.setdefault((entry.get("segment_id"), entry["role"]), path)
    return edges


def _seed(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value == -1:
        return None
    return value


def plan_shots(document: Mapping[str, Any]) -> List[CloudShot]:
    settings = document.get("settings") or {}
    segments = [segment for segment in document.get("segments") or [] if isinstance(segment, Mapping)]
    if not segments:
        raise ValueError("The Video Director document has no shots.")
    fps = float(settings.get("fps") or DEFAULT_FPS)
    base_seed = _seed(settings.get("seed"))
    edges = _edges(document)

    if document.get("mode") != DIRECTOR_MODE:
        segment = segments[0]
        segment_id = segment.get("id") or "seg-0"
        first = edges.get((segment_id, "first")) or next((p for (_, role), p in edges.items() if role == "first"), None)
        last = edges.get((segment_id, "last")) or next((p for (_, role), p in edges.items() if role == "last"), None)
        return [CloudShot(
            index=0,
            count=1,
            segment_id=segment_id,
            prompt=str(segment.get("prompt") or ""),
            negative_prompt=str(segment.get("negative_prompt") or ""),
            seed=base_seed,
            duration_s=_seconds(settings.get("duration")) if settings.get("duration") is not None else None,
            start_image=first,
            end_image=last if first is not None else None,
        )]

    shots: List[CloudShot] = []
    for index, segment in enumerate(segments):
        segment_id = segment.get("id") or f"seg-{index}"
        first = edges.get((segment_id, "first"))
        continues = segment.get("sub_type") == CONTINUE_SUB_TYPE and first is None and index > 0
        seed = _seed(segment.get("seed"))
        if seed is None and base_seed is not None:
            seed = base_seed + index
        frames = segment.get("frames")
        shots.append(CloudShot(
            index=index,
            count=len(segments),
            segment_id=segment_id,
            prompt=str(segment.get("prompt") or ""),
            negative_prompt=str(segment.get("negative_prompt") or ""),
            seed=seed,
            duration_s=_frames_to_seconds(frames, fps) if isinstance(frames, int) and frames > 0 else None,
            start_image=first,
            end_image=edges.get((segment_id, "last")) if first is not None else None,
            continues=continues,
        ))
    return shots


def stitch_wanted(document: Mapping[str, Any]) -> bool:
    continuation = (document.get("settings") or {}).get("continuation")
    if isinstance(continuation, Mapping) and continuation.get("stitch") is False:
        return False
    return True


def kept_text(finished: int) -> str:
    if finished <= 0:
        return "No shots were finished."
    if finished == 1:
        return "Shot 1 was kept."
    return f"Shots 1-{finished} were kept."
