import importlib
import uuid
from pathlib import Path
from typing import Any, List, Sequence, Tuple

from src.platform.util.face_boxes import ImageInput, rgb

TEMPLATE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366), (41.5493, 92.3655), (70.7299, 92.2041))
SIZE = 112
THREADS = 2
MODEL_KEY = "media/face-identity/auraface"
_BYTES_PER_GB = 1024 ** 3

Face = Tuple[ImageInput, Sequence[Sequence[float]]]


def align(image: ImageInput, landmarks: Sequence[Sequence[float]]) -> Any:
    import cv2
    import numpy as np

    points = np.asarray(landmarks, dtype=np.float32).reshape(5, 2)
    matrix, _ = cv2.estimateAffinePartial2D(points, np.asarray(TEMPLATE, dtype=np.float32), method=cv2.LMEDS)
    if matrix is None:
        raise ValueError("The five landmarks of the face give no alignment")
    return cv2.warpAffine(np.asarray(rgb(image)), matrix, (SIZE, SIZE), borderValue=0)


def runtime_imports() -> bool:
    try:
        importlib.import_module("onnxruntime")
    except ImportError:
        return False
    return True


def new_session(path: str) -> Any:
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = THREADS
    options.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(path, options, providers=["CPUExecutionProvider"])


def _fingerprint(model: Path) -> str:
    stat = model.stat()
    return f"{model}|{stat.st_mtime_ns}|{stat.st_size}"


def _unit(vector: Any) -> List[float]:
    import numpy as np

    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return (vector / norm if norm else vector).tolist()


def embed(models: Any, model: Path, faces: Sequence[Face]) -> List[List[float]]:
    import numpy as np

    inputs = [((align(image, landmarks).astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None] for image, landmarks in faces]
    if not inputs:
        return []
    lease = f"face-identity-{uuid.uuid4().hex}"
    models.begin_generation(None)
    models.begin_lease(lease)
    try:
        session = models.acquire(
            MODEL_KEY,
            _fingerprint(model),
            lambda: new_session(str(model)),
            estimated_vram_gb=model.stat().st_size / _BYTES_PER_GB,
        )
        name = session.get_inputs()[0].name
        return [_unit(session.run(None, {name: tensor})[0][0]) for tensor in inputs]
    finally:
        models.end_lease(lease)
