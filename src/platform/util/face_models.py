import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, Optional

FACE_MODEL = "face_detection_yunet_2023mar.onnx"
FACE_MODEL_TYPE = "detection_bbox"
IDENTITY_MODEL = "glintr100.onnx"
IDENTITY_DIR = "face_recognition/auraface"
MODEL_FILES = {
    FACE_MODEL: (232589, "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    IDENTITY_MODEL: (260694151, "a7933ea5330113b01c9b60351d8f4c33003f145d8470ac5f0e52ee2effe25c60"),
}
_verified_files: set = set()
_checks: Dict[str, threading.Lock] = {}
_checks_lock = threading.Lock()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_model_file(path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    if stamp in _verified_files:
        return True
    with _checks_lock:
        check = _checks.setdefault(str(path), threading.Lock())
    with check:
        if stamp in _verified_files:
            return True
        size, digest = MODEL_FILES[path.name]
        try:
            if not path.is_file() or stat.st_size != size or _sha256(path) != digest:
                return False
        except OSError:
            return False
        _verified_files.add(stamp)
        return True


def face_model(model_roots: Any) -> Optional[Path]:
    for entry in model_roots.type_dirs(FACE_MODEL_TYPE):
        candidate = Path(entry.path) / FACE_MODEL
        if _is_model_file(candidate):
            return candidate
    return None


def identity_model(model_roots: Any) -> Optional[Path]:
    path = model_roots.asset_dir(IDENTITY_DIR) / IDENTITY_MODEL
    return path if _is_model_file(path) else None
