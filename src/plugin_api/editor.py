from typing import Callable, Iterable, Sequence

from PIL import Image

from src.features.editor_tasks.detection import Detection, DetectorUnavailable
from src.features.editor_tasks.registry import TaskDefinition
from src.platform.plugins.runtime_registries import get_container

__all__ = ["Detection", "DetectorUnavailable", "TaskDefinition", "register_detector", "unregister_detector"]


def register_detector(
    detector_id: str,
    kinds: Iterable[str],
    run: Callable[[Image.Image], Sequence[Detection]],
    source: str,
) -> None:
    get_container().editor_detector_registry.register(detector_id, kinds, run, source)


def unregister_detector(detector_id: str) -> bool:
    return get_container().editor_detector_registry.unregister(detector_id)
