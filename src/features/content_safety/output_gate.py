import inspect
import os
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

from src.features.content_safety.constants import POLICY_BLOCKED
from src.features.generation.content_blocked_output import ContentBlockedGenerationOutput
from src.pipelines import outputs as core_outputs
from src.pipelines.outputs import (
    CompareImagesGenerationOutput,
    GalleryGenerationOutput,
    GenerationOutput,
    ImageGenerationOutput,
    VideoGenerationOutput,
)

KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_COMPARE = "compare"

_VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".gif")
_KNOWN_OUTPUT_TYPES = frozenset(
    cls
    for cls in vars(core_outputs).values()
    if inspect.isclass(cls) and issubclass(cls, GenerationOutput) and cls.__module__ == core_outputs.__name__
)


@dataclass
class GateItem:
    kind: str
    target: Any
    final: bool
    gallery: bool = False
    pixels: Optional[List[Any]] = None
    path: Optional[str] = None

    def video_file(self) -> str:
        return self.path or str(self.target.video_path)

    def images(self) -> List[Any]:
        if self.pixels is not None:
            return self.pixels
        if self.kind == KIND_IMAGE:
            image = getattr(self.target, "image", None)
            return [image] if image is not None else []
        if self.kind == KIND_COMPARE:
            return [pair[1] for pair in (self.target.compare, self.target.to) if pair and pair[1] is not None]
        return []


@dataclass
class Verdict:
    item: GateItem
    score: Optional[float]
    flagged: bool


@dataclass
class GateOutcome:
    output: Optional[GenerationOutput]
    verdicts: List[Verdict] = field(default_factory=list)
    blocked: int = 0
    finals: int = 0
    unavailable: bool = False


def _is_final(target: Any) -> bool:
    return getattr(target, "temporary", True) is False


def is_unknown_output(output: GenerationOutput) -> bool:
    if isinstance(output, (ImageGenerationOutput, VideoGenerationOutput, CompareImagesGenerationOutput, GalleryGenerationOutput)):
        return False
    return type(output) not in _KNOWN_OUTPUT_TYPES and not isinstance(output, ContentBlockedGenerationOutput)


def _generic_items(output: GenerationOutput) -> List[GateItem]:
    from PIL import Image

    pixels: List[Any] = []
    videos: List[str] = []
    for value in vars(output).values():
        candidates = value if isinstance(value, (list, tuple)) else [value]
        for candidate in candidates:
            if isinstance(candidate, Image.Image):
                pixels.append(candidate)
            elif isinstance(candidate, (str, os.PathLike)) and str(candidate).lower().endswith(_VIDEO_SUFFIXES):
                videos.append(str(candidate))
    final = getattr(output, "temporary", True) is False
    items: List[GateItem] = []
    if pixels:
        items.append(GateItem(KIND_IMAGE, output, final, pixels=pixels))
    items += [GateItem(KIND_VIDEO, output, final, path=video) for video in videos]
    return items


def gate_items(output: GenerationOutput) -> List[GateItem]:
    if isinstance(output, ImageGenerationOutput):
        return [GateItem(KIND_IMAGE, output, _is_final(output))]
    if isinstance(output, VideoGenerationOutput):
        return [GateItem(KIND_VIDEO, output, _is_final(output))]
    if isinstance(output, CompareImagesGenerationOutput):
        return [GateItem(KIND_COMPARE, output, False)]
    if isinstance(output, GalleryGenerationOutput):
        items = [GateItem(KIND_IMAGE, image, _is_final(image), gallery=True) for image in output.images or []]
        items += [GateItem(KIND_VIDEO, video, _is_final(video), gallery=True) for video in output.videos or []]
        return items
    if is_unknown_output(output):
        return _generic_items(output)
    return []


def mark(target: Any, nsfw: bool, flagged: bool, suppressed: bool = False) -> None:
    target._content_nsfw = nsfw
    target._content_flagged = flagged
    target._preview_suppressed = suppressed


def apply_verdicts(
    output: GenerationOutput,
    items: Sequence[GateItem],
    scores: Optional[Sequence[float]],
    threshold: float,
    mode: str,
    unavailable: bool,
) -> GateOutcome:
    blocking = mode == POLICY_BLOCKED
    verdicts: List[Verdict] = []
    keep_images: List[Any] = []
    keep_videos: List[Any] = []
    blocked = 0
    finals = 0
    kept = 0
    kept_flagged = False

    for index, item in enumerate(items):
        score = None if unavailable or scores is None else scores[index]
        flagged = True if score is None else score >= threshold
        verdicts.append(Verdict(item, score, flagged))
        counted = not getattr(item.target, "isArtifact", False)

        if blocking and item.final and unavailable:
            return GateOutcome(None, verdicts, unavailable=True)

        if blocking and flagged:
            if item.final:
                blocked += 1 if counted else 0
                continue
            if item.gallery:
                continue
            mark(item.target, True, True, suppressed=True)
            kept += 1
            continue

        mark(item.target, flagged, flagged and not blocking)
        kept += 1
        if item.final and counted:
            finals += 1
        if item.gallery:
            kept_flagged = kept_flagged or flagged
            (keep_images if item.kind == KIND_IMAGE else keep_videos).append(item.target)

    if isinstance(output, GalleryGenerationOutput):
        output.images = keep_images
        output.videos = keep_videos
        if not keep_images and not keep_videos and not output.audios and not output.meshes:
            return GateOutcome(None, verdicts, blocked=blocked)
        output._content_nsfw = kept_flagged
        output._content_flagged = kept_flagged and not blocking
        output._preview_suppressed = False
        return GateOutcome(output, verdicts, blocked=blocked, finals=finals)

    if kept == 0:
        return GateOutcome(None, verdicts, blocked=blocked)
    return GateOutcome(output, verdicts, blocked=blocked, finals=finals)
