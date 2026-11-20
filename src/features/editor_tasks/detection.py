import asyncio
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Sequence, Tuple

from PIL import Image

from src.features.editor_tasks import regions
from src.features.editor_tasks.dto import DetectionRequest
from src.features.editor_tasks.registry import TaskRegistry
from src.platform.http.errors import DomainError, NotFoundError
from src.platform.media_tools.fastpath import prepare_image

CORE_SOURCE = "core"
KINDS = ("face", "hand")
BOX_PAD = 0.1
MAX_DETECTIONS = 64
SAME_BOX_IOU = 0.6

Box = Tuple[float, float, float, float]


class DetectorUnavailable(Exception):
    pass


class DetectorRegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class Detection:
    kind: str
    box: Box
    score: float


Detector = Callable[[Image.Image], Sequence[Detection]]


@dataclass(frozen=True)
class RegisteredDetector:
    id: str
    kinds: FrozenSet[str]
    run: Detector
    source: str


class DetectorRegistry:
    def __init__(self) -> None:
        self._detectors: Dict[str, RegisteredDetector] = {}
        self._lock = threading.RLock()

    def register(self, detector_id: str, kinds: Iterable[str], run: Detector, source: str = CORE_SOURCE) -> None:
        wanted = frozenset(kinds)
        if not detector_id:
            raise DetectorRegistrationError("a detector needs an id")
        if not wanted or not wanted <= set(KINDS):
            raise DetectorRegistrationError(f"detector '{detector_id}' kinds must be a non-empty subset of {KINDS}")
        with self._lock:
            held = self._detectors.get(detector_id)
            if held is not None and held.source != source:
                raise DetectorRegistrationError(f"detector '{detector_id}' is already registered by '{held.source}'")
            self._detectors[detector_id] = RegisteredDetector(detector_id, wanted, run, source)

    def unregister(self, detector_id: str) -> bool:
        with self._lock:
            return self._detectors.pop(detector_id, None) is not None

    def unregister_source(self, source: str) -> int:
        if source == CORE_SOURCE:
            return 0
        with self._lock:
            gone = [key for key, item in self._detectors.items() if item.source == source]
            for key in gone:
                del self._detectors[key]
            return len(gone)

    def list(self) -> List[RegisteredDetector]:
        with self._lock:
            return list(self._detectors.values())

    def detect(self, picture: Image.Image) -> List[Detection]:
        found: List[Detection] = []
        reasons: List[str] = []
        ran = False
        for detector in self.list():
            try:
                found.extend(item for item in detector.run(picture) if item.kind in detector.kinds)
                ran = True
            except DetectorUnavailable as exc:
                reasons.append(str(exc))
        if not ran:
            raise DetectorUnavailable("; ".join(reasons) or "No detector is installed")
        return merge_duplicates(found)


def iou(a: Box, b: Box) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    inner = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inner
    return inner / union if union > 0 else 0.0


def merge_duplicates(items: List[Detection]) -> List[Detection]:
    kept: List[Detection] = []
    for item in sorted(items, key=lambda entry: -entry.score):
        if all(other.kind != item.kind or iou(other.box, item.box) < SAME_BOX_IOU for other in kept):
            kept.append(item)
    return kept


def normalise(items: List[Detection], size: Tuple[int, int]) -> List[Dict[str, Any]]:
    width, height = size
    boxes: List[Dict[str, Any]] = []
    for item in items:
        x1, y1, x2, y2 = item.box
        pad_x, pad_y = (x2 - x1) * BOX_PAD, (y2 - y1) * BOX_PAD
        left, top = max(0.0, x1 - pad_x) / width, max(0.0, y1 - pad_y) / height
        right, bottom = min(float(width), x2 + pad_x) / width, min(float(height), y2 + pad_y) / height
        if right <= left or bottom <= top:
            continue
        boxes.append(
            {
                "kind": item.kind,
                "box": {
                    "x": round(left, 4),
                    "y": round(top, 4),
                    "width": round(right - left, 4),
                    "height": round(bottom - top, 4),
                },
                "score": round(float(item.score), 3),
            }
        )
    boxes.sort(key=lambda entry: (entry["box"]["y"], entry["box"]["x"]))
    return [{"id": f"d{index}", **entry} for index, entry in enumerate(boxes[:MAX_DETECTIONS], start=1)]


class EditorDetectionService:
    def __init__(self, registry: DetectorRegistry, tasks: TaskRegistry, media_tools: Any):
        self.registry = registry
        self.tasks = tasks
        self.media_tools = media_tools

    async def detect(self, user: Any, task_id: str, body: DetectionRequest) -> Dict[str, Any]:
        if self.tasks.get(task_id) is None:
            raise NotFoundError("editor_task_not_found", f"Unknown editor task '{task_id}'")
        return await asyncio.to_thread(self._detect, user, body)

    def _detect(self, user: Any, body: DetectionRequest) -> Dict[str, Any]:
        with self.media_tools.render_picture(user, body.input, body.steps) as path:
            picture = regions.cap_picture(prepare_image(path).convert("RGB"))
        try:
            items = self.registry.detect(picture)
        except DetectorUnavailable as exc:
            raise DomainError("detector_unavailable", str(exc), 503) from exc
        return {
            "picture": {"width": picture.width, "height": picture.height},
            "detections": normalise(items, picture.size),
        }
