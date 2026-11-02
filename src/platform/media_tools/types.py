from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional, Sequence

if TYPE_CHECKING:
    from src.platform.media_tools.base import MediaTool

MediaKind = Literal["image", "video", "audio"]
Cardinality = Literal["one_to_one", "one_to_many", "many_to_one"]

MEDIA_KINDS: tuple[str, ...] = ("image", "video", "audio")
CARDINALITIES: tuple[str, ...] = ("one_to_one", "one_to_many", "many_to_one")
DEFAULT_SCOPES: tuple[str, ...] = ("library", "history", "field")
EDITOR_SCOPE = "editor"
CORE_SOURCE = "core"


class MediaToolError(Exception):
    code = "tool_failed"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidToolParams(MediaToolError, ValueError):
    code = "invalid_params"

    def __init__(self, field: str, message: str, step_index: Optional[int] = None):
        super().__init__(message)
        self.field = field
        self.step_index = step_index


class UnsupportedMedia(MediaToolError):
    code = "unsupported_media"


class InvalidSelection(MediaToolError):
    code = "invalid_selection"


class ToolCancelled(MediaToolError):
    code = "cancelled"

    def __init__(self, message: str = "Cancelled"):
        super().__init__(message)


class ToolFailed(MediaToolError):
    code = "tool_failed"


class EmptyOutput(ToolFailed):
    def __init__(self, message: str = "The encoder produced an empty file"):
        super().__init__(message)


class FfmpegUnavailable(ToolFailed):
    code = "ffmpeg_unavailable"


@dataclass(frozen=True)
class ToolMedia:
    path: Path
    kind: MediaKind
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    fps: Optional[float] = None
    has_audio: Optional[bool] = None
    has_alpha: Optional[bool] = None
    audio_codec: Optional[str] = None
    bit_depth: Optional[int] = None

    def with_changes(self, **changes: Any) -> "ToolMedia":
        return replace(self, **changes)


@dataclass
class FilterFragment:
    vf: list[str] = field(default_factory=list)
    af: list[str] = field(default_factory=list)
    start: Optional[float] = None
    end: Optional[float] = None
    drop_audio: bool = False
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    crf: Optional[int] = None
    frames: Optional[int] = None
    copy_video: bool = False


ProgressCallback = Callable[[float, str], None]


@dataclass
class ToolSpec:
    id: str
    label: str
    icon: str = "wrench"
    accepts: tuple[str, ...] = ()
    produces: str = "same"
    cardinality: str = "one_to_one"
    params: list[dict] = field(default_factory=list)
    requires: dict = field(default_factory=dict)
    selection: Optional[dict] = None
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    editor: Optional[str] = None
    preview: bool = False
    component: Optional[str] = None
    source: str = CORE_SOURCE
    impl: Optional["MediaTool"] = None
    description: Optional[str] = None
    name_suffix: str = ""
    group: str = "edit"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "icon": self.icon,
            "accepts": list(self.accepts),
            "produces": self.produces,
            "cardinality": self.cardinality,
            "params": [dict(p) for p in self.params],
            "requires": dict(self.requires),
            "selection": dict(self.selection) if self.selection else None,
            "scopes": list(self.scopes),
            "editor": self.editor,
            "preview": self.preview,
            "component": self.component,
            "source": self.source,
            "description": self.description,
            "group": self.group,
        }

    def output_kind(self, kind: str) -> str:
        return kind if self.produces == "same" else self.produces

    def selection_bounds(self) -> tuple[int, Optional[int]]:
        selection = self.selection or {}
        default_min = 2 if self.cardinality == "many_to_one" else 1
        high = selection.get("max")
        return int(selection.get("min") or default_min), (int(high) if high else None)


class ToolContext:
    def __init__(
        self,
        scratch_dir: Path | str,
        *,
        progress: Optional[ProgressCallback] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
        runner: Any = None,
        prober: Optional[Callable[[Path], ToolMedia]] = None,
        _window: tuple[float, float] = (0.0, 1.0),
        _counter: Optional[list[int]] = None,
    ):
        from src.platform.media_tools.ffmpeg import FfmpegRunner
        from src.platform.media_tools.probe import probe as default_probe

        self.scratch_dir = Path(scratch_dir)
        self._progress = progress
        self._is_cancelled = is_cancelled
        self.runner = runner if runner is not None else FfmpegRunner()
        self._prober = prober or default_probe
        self._window = _window
        self._counter = _counter if _counter is not None else [0]

    @property
    def commands(self) -> list[list[str]]:
        return self.runner.commands

    def progress(self, fraction: float, label: str = "") -> None:
        if self._progress is None:
            return
        low, high = self._window
        clamped = min(1.0, max(0.0, float(fraction)))
        self._progress(low + (high - low) * clamped, label)

    def is_cancelled(self) -> bool:
        return bool(self._is_cancelled and self._is_cancelled())

    def ffmpeg(self, args: Sequence[str], *, duration: Optional[float] = None) -> None:
        self.runner.run(
            args,
            duration=duration,
            on_progress=self.progress,
            is_cancelled=self.is_cancelled,
        )

    def probe(self, path: Path | str, kind: Optional[str] = None) -> ToolMedia:
        return self._prober(Path(path), kind) if kind else self._prober(Path(path))

    def new_path(self, suffix: str, stem: str = "out") -> Path:
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self._counter[0] += 1
        return self.scratch_dir / f"{stem}-{self._counter[0]:03d}{suffix.lower()}"

    def scoped(self, low: float, high: float) -> "ToolContext":
        outer_low, outer_high = self._window
        span = outer_high - outer_low
        return ToolContext(
            self.scratch_dir,
            progress=self._progress,
            is_cancelled=self._is_cancelled,
            runner=self.runner,
            prober=self._prober,
            _window=(outer_low + span * low, outer_low + span * high),
            _counter=self._counter,
        )
