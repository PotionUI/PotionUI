from typing import Optional

from src.features.plans.errors import LimitExceeded
from src.platform.database.rows import now_utc
from src.platform.plugins.limit_kinds import (
    AdmissionRequest,
    LimitEventsView,
    LimitKind,
    MeasureContext,
    Refusal,
    RefusalContext,
    Usage,
)
from src.platform.plugins.runtime_registries import get_container

__all__ = [
    "AdmissionRequest",
    "LimitEvents",
    "LimitEventsView",
    "LimitExceeded",
    "LimitKind",
    "MeasureContext",
    "Refusal",
    "RefusalContext",
    "Usage",
]


class LimitEvents:

    @staticmethod
    def record(user_id: str, kind: str, units: float = 1, ref_id: Optional[str] = None) -> str:
        return get_container().plans.events.record(user_id, kind, units, ref_id, now_utc())

    @staticmethod
    def refund(ref_id: str) -> int:
        return get_container().plans.guard.refund(ref_id)
