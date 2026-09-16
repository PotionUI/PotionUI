from __future__ import annotations

import math
from functools import lru_cache
from typing import Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter

Point = Tuple[float, float]

_ROUNDED_RECT_RADIUS = 0.25


def oval_ring(connections: Iterable[Sequence[int]]) -> List[int]:
    neighbours: dict[int, List[int]] = {}
    for edge in connections:
        a, b = int(edge[0]), int(edge[1])
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
    if not neighbours:
        raise ValueError("face oval connections are empty")
    if any(len(v) != 2 for v in neighbours.values()):
        raise ValueError("face oval connections do not form a simple closed ring")

    start = min(neighbours)
    ring = [start]
    previous = None
    current = start
    while True:
        options = [n for n in neighbours[current] if n != previous]
        nxt = options[0]
        if nxt == start:
            break
        ring.append(nxt)
        previous, current = current, nxt
        if len(ring) > len(neighbours):
            raise ValueError("face oval connections do not form a simple closed ring")
    if len(ring) != len(neighbours):
        raise ValueError("face oval connections do not form a simple closed ring")
    return ring


@lru_cache(maxsize=1)
def mediapipe_oval_ring() -> Optional[Tuple[int, ...]]:
    try:
        from mediapipe.python.solutions.face_mesh_connections import FACEMESH_FACE_OVAL
    except Exception:
        try:
            import mediapipe as mp

            FACEMESH_FACE_OVAL = mp.solutions.face_mesh.FACEMESH_FACE_OVAL
        except Exception:
            return None
    try:
        return tuple(oval_ring(FACEMESH_FACE_OVAL))
    except ValueError:
        return None


def oval_polygon(landmarks: Sequence[Point], ring: Sequence[int]) -> Optional[List[Point]]:
    if not landmarks or not ring:
        return None
    if max(ring) >= len(landmarks):
        return None
    return [(float(landmarks[i][0]), float(landmarks[i][1])) for i in ring]


def build_face_mask(
    size: Tuple[int, int],
    *,
    polygon: Optional[Sequence[Point]] = None,
    box: Optional[Sequence[float]] = None,
    dilate: int = 0,
    feather: int = 0,
) -> Image.Image:
    width, height = int(size[0]), int(size[1])
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    dilate = max(0, int(dilate))

    if polygon and len(polygon) >= 3:
        draw.polygon(_grown(polygon, dilate), fill=255)
    else:
        if box is None:
            box = (0, 0, width, height)
        x1, y1, x2, y2 = (float(v) for v in box)
        x1, y1 = max(0.0, x1 - dilate), max(0.0, y1 - dilate)
        x2, y2 = min(float(width), x2 + dilate), min(float(height), y2 + dilate)
        radius = max(0.0, min(x2 - x1, y2 - y1) * _ROUNDED_RECT_RADIUS)
        draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=255)

    if feather > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1.0, feather / 2.0)))
    return mask


def _grown(polygon: Sequence[Point], dilate: int) -> List[Point]:
    points = [(float(x), float(y)) for x, y in polygon]
    if dilate <= 0:
        return points
    centre_x = sum(x for x, _ in points) / len(points)
    centre_y = sum(y for _, y in points) / len(points)
    grown = []
    for x, y in points:
        dx, dy = x - centre_x, y - centre_y
        distance = math.hypot(dx, dy)
        if distance <= 1e-9:
            grown.append((x, y))
            continue
        scale = (distance + dilate) / distance
        grown.append((centre_x + dx * scale, centre_y + dy * scale))
    return grown


def default_dilate(box: Sequence[float], fraction: float) -> int:
    x1, y1, x2, y2 = (float(v) for v in box)
    short_side = max(1.0, min(x2 - x1, y2 - y1))
    return int(round(short_side * max(0.0, float(fraction))))
