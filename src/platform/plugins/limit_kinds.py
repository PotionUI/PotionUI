import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Tuple

VALUE_TYPES = ("bytes", "count", "usd")
FORMATS = ("bytes", "count", "percent")
WINDOWS = ("none", "day", "month")
POINTS = ("submit", "upload")
BYTES_INPUT_SCALE = 1024 ** 3

_CORE_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_PLUGIN_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*\.[a-z0-9][a-z0-9_.-]*$")

_DEFAULT_FORMATS = {"bytes": "bytes", "count": "count", "usd": "percent"}
_DEFAULT_UNITS = {"bytes": "GB", "count": "", "usd": "USD"}
_DEFAULT_ICONS = {"bytes": "database", "count": "bolt", "usd": "calendar"}


class DuplicateLimitKindError(ValueError):
    pass


class InvalidLimitKindError(ValueError):
    pass


@dataclass(frozen=True)
class Usage:
    used: float
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str


@dataclass(frozen=True)
class AdmissionRequest:
    point: str
    user_id: str
    incoming_bytes: Optional[int] = None
    engine: Optional[str] = None
    backend_id: Optional[str] = None
    preset_id: Optional[str] = None
    ref_id: Optional[str] = None


class LimitEventsView(Protocol):
    def total(self) -> float: ...

    def record(self, units: float = 1, ref_id: Optional[str] = None) -> str: ...

    def refund(self, ref_id: str) -> int: ...


@dataclass(frozen=True)
class MeasureContext:
    user_id: str
    kind: str
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    events: LimitEventsView


@dataclass(frozen=True)
class RefusalContext:
    kind: "LimitKind"
    used: float
    limit: float
    resets_at: Optional[datetime]
    resets_label: str
    contact_line: str
    point: str


@dataclass(frozen=True)
class LimitKind:
    key: str
    label: str
    value_type: str
    window: str = "none"
    enforce_at: Tuple[str, ...] = ("submit",)
    measure: Optional[Callable[[MeasureContext], Any]] = None
    ledger: bool = False
    short_label: str = ""
    description: str = ""
    unit: Optional[str] = None
    format: Optional[str] = None
    admin_only_values: Optional[bool] = None
    applies: Optional[Callable[[AdmissionRequest], bool]] = None
    incoming: Optional[Callable[[AdmissionRequest], Optional[float]]] = None
    refusal: Optional[Callable[[RefusalContext], str]] = None
    refusal_message: str = ""
    refusal_code: str = ""
    warn_at: float = 0.8
    icon: str = ""
    source: str = "core"

    @property
    def user_format(self) -> str:
        if self.hides_values:
            return "percent"
        return self.format or _DEFAULT_FORMATS[self.value_type]

    @property
    def hides_values(self) -> bool:
        if self.admin_only_values is not None:
            return self.admin_only_values
        return self.value_type == "usd" or self.format == "percent"

    @property
    def code(self) -> str:
        return self.refusal_code or f"{self.key}_exceeded"

    @property
    def input_scale(self) -> int:
        return BYTES_INPUT_SCALE if self.value_type == "bytes" else 1

    @property
    def display_unit(self) -> str:
        return self.unit if self.unit is not None else _DEFAULT_UNITS[self.value_type]

    @property
    def display_icon(self) -> str:
        if self.icon:
            return self.icon
        return "layers" if self.source != "core" else _DEFAULT_ICONS[self.value_type]

    def applies_to(self, request: AdmissionRequest) -> bool:
        if request.point not in self.enforce_at:
            return False
        if self.applies is None:
            return True
        return bool(self.applies(request))

    def incoming_for(self, request: AdmissionRequest) -> Optional[float]:
        if self.incoming is not None:
            return self.incoming(request)
        if self.ledger and request.point == "submit":
            return 1
        if self.value_type == "bytes" and request.point == "upload":
            return request.incoming_bytes
        return None


def exceeded(limit: float, used: float, incoming: Optional[float]) -> bool:
    if incoming is None:
        return used >= limit
    return used + incoming > limit


def validate_kind(kind: LimitKind) -> None:
    pattern = _CORE_KEY if kind.source == "core" else _PLUGIN_KEY
    if not pattern.match(kind.key or ""):
        raise InvalidLimitKindError(f"Limit kind key '{kind.key}' is not valid for source '{kind.source}'")
    if not kind.label:
        raise InvalidLimitKindError(f"Limit kind '{kind.key}' has no label")
    if kind.value_type not in VALUE_TYPES:
        raise InvalidLimitKindError(f"Limit kind '{kind.key}' has unknown value_type '{kind.value_type}'")
    if kind.window not in WINDOWS:
        raise InvalidLimitKindError(f"Limit kind '{kind.key}' has unknown window '{kind.window}'")
    if kind.format is not None and kind.format not in FORMATS:
        raise InvalidLimitKindError(f"Limit kind '{kind.key}' has unknown format '{kind.format}'")
    if not kind.enforce_at or any(point not in POINTS for point in kind.enforce_at):
        raise InvalidLimitKindError(f"Limit kind '{kind.key}' must be enforced at submit and/or upload")
    if kind.measure is None and not kind.ledger:
        raise InvalidLimitKindError(f"Limit kind '{kind.key}' needs a measure or ledger: true")
    if kind.ledger and kind.window == "none":
        raise InvalidLimitKindError(f"Limit kind '{kind.key}' keeps a ledger, so it needs a day or month window")
    if not 0 < kind.warn_at <= 1:
        raise InvalidLimitKindError(f"Limit kind '{kind.key}' warn_at must be above 0 and at most 1")
    if kind.value_type == "usd" and kind.format in ("bytes", "count"):
        raise InvalidLimitKindError(f"Limit kind '{kind.key}' measures money and cannot render as {kind.format}")


class LimitKindRegistry:

    def __init__(self):
        self._kinds: Dict[str, LimitKind] = {}
        self._lock = threading.Lock()

    def register(self, kind: LimitKind) -> None:
        validate_kind(kind)
        with self._lock:
            if kind.key in self._kinds:
                raise DuplicateLimitKindError(f"Limit kind already registered: '{kind.key}'")
            self._kinds[kind.key] = kind

    def unregister_source(self, source: str) -> None:
        with self._lock:
            for key in [k for k, kind in self._kinds.items() if kind.source == source]:
                del self._kinds[key]

    def get(self, key: str) -> Optional[LimitKind]:
        return self._kinds.get(key)

    def all(self) -> List[LimitKind]:
        with self._lock:
            kinds = list(self._kinds.values())
        return sorted(kinds, key=lambda kind: (kind.source != "core", kind.source))

    def has_source(self, source: str) -> bool:
        return any(kind.source == source for kind in self.all())


limit_kind_registry = LimitKindRegistry()
