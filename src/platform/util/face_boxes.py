import os
from pathlib import Path
from typing import Any, Dict, List, Sequence, Union

from PIL import Image

Box = Dict[str, Any]
ImageInput = Union[str, Path, Image.Image]
SINGLE = (str, bytes, os.PathLike, Image.Image)

NMS = 0.3
TOP_K = 5000
SCORE = 14
LANDMARKS = range(4, 14, 2)


def listed(values: Any, name: str) -> List[Any]:
    if isinstance(values, SINGLE):
        raise TypeError(f"{name} is a list: wrap a single path or image in a list")
    return list(values)


def rgb(image: ImageInput) -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    with Image.open(image) as opened:
        return opened.convert("RGB")


def boxes(faces: Any, min_score: float) -> List[Box]:
    found = []
    for face in [] if faces is None else faces:
        x, y, width, height, score = (float(value) for value in (face[0], face[1], face[2], face[3], face[SCORE]))
        if score >= min_score and width > 0 and height > 0:
            landmarks = [[float(face[index]), float(face[index + 1])] for index in LANDMARKS]
            found.append({"x": x, "y": y, "width": width, "height": height, "score": round(score, 4), "landmarks": landmarks})
    return sorted(found, key=lambda box: box["score"], reverse=True)


def detect(model: Path, images: Sequence[ImageInput], min_score: float = 0.5) -> List[List[Box]]:
    import cv2
    import numpy as np

    detector = None
    found = []
    for image in images:
        picture = np.ascontiguousarray(cv2.cvtColor(np.asarray(rgb(image)), cv2.COLOR_RGB2BGR))
        size = (picture.shape[1], picture.shape[0])
        if detector is None:
            detector = cv2.FaceDetectorYN.create(str(model), "", size, min_score, NMS, TOP_K)
        detector.setInputSize(size)
        _, faces = detector.detect(picture)
        found.append(boxes(faces, min_score))
    return found
