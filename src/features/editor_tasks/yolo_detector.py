import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from src.features.editor_tasks.detection import Detection, DetectorUnavailable

DETECTOR_ID = "yolo"
MODEL_TYPE = "detection_bbox"
WEIGHTS = {
    "face": ("face_yolov12m.pt", "face_yolov8s.pt", "face_yolov8n.pt"),
    "hand": ("hand_yolov8s.pt", "hand_yolov8n.pt"),
}
CONFIDENCE = {"face": 0.35, "hand": 0.5}
DEVICE = "cpu"


class YoloDetector:
    kinds = frozenset(WEIGHTS)

    def __init__(self, model_roots: Any):
        self.model_roots = model_roots
        self._loaded: Dict[str, Tuple[int, Any]] = {}
        self._lock = threading.Lock()

    def weights(self, kind: str) -> Optional[Path]:
        for name in WEIGHTS[kind]:
            for entry in self.model_roots.type_dirs(MODEL_TYPE):
                candidate = Path(entry.path) / name
                if candidate.is_file():
                    return candidate
        return None

    def __call__(self, picture: Image.Image) -> List[Detection]:
        found = {kind: path for kind in WEIGHTS if (path := self.weights(kind)) is not None}
        if not found:
            raise DetectorUnavailable("No face or hand detection weights are installed")
        try:
            from src.pipelines.pipes._shared.detection.detailer_helper import DetailerHelper
        except ImportError as exc:
            raise DetectorUnavailable(f"The detection runtime is not installed: {exc}") from exc

        config = {
            "device": DEVICE,
            "detections": {kind: {"confidence": CONFIDENCE[kind], "device": DEVICE} for kind in found},
        }
        helper = DetailerHelper(config)
        detections: List[Detection] = []
        for kind, path in found.items():
            model = self._model(helper, path)
            for box, score in helper.detect_objects_scored(picture, model, kind):
                x1, y1, x2, y2 = (float(value) for value in box)
                detections.append(Detection(kind, (x1, y1, x2, y2), 0.0 if score is None else float(score)))
        return detections

    def _model(self, helper: Any, path: Path) -> Any:
        stamp = path.stat().st_mtime_ns
        with self._lock:
            held = self._loaded.get(str(path))
            if held is not None and held[0] == stamp:
                return held[1]
            try:
                model = helper.load_detector(str(path))
            except ImportError as exc:
                raise DetectorUnavailable(f"The detection runtime is not installed: {exc}") from exc
            self._loaded[str(path)] = (stamp, model)
            return model
