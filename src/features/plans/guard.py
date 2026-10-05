import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.features.plans.constants import (
    DEFAULT_CONTACT_LINE,
    DEFAULT_TIMEZONE,
    LEDGER_RETENTION_DAYS,
    PRUNE_INTERVAL_SECONDS,
    SETTING_CONTACT_LINE,
    SETTING_DAY_TIMEZONE,
    SETTING_EXEMPT_ADMINS,
    STATE_FULL,
    STATE_OK,
    STATE_WARN,
)
from src.features.plans.errors import LimitExceeded
from src.features.plans.hooks import PLANS_HOOKS
from src.features.plans.kinds import refusal_message
from src.features.plans.records import Plan, PlanSubject
from src.features.plans.repository import LimitEventRepository, PlanRepository, UsageRepository
from src.features.plans.resolver import EffectiveLimit, PlanResolver, Resolution
from src.features.plans.windows import local_day_text, until_text, valid_timezone, window_bounds, zone
from src.platform.database.rows import now_utc
from src.platform.plugins.limit_kinds import (
    AdmissionRequest,
    LimitKind,
    LimitKindRegistry,
    MeasureContext,
    RefusalContext,
    Usage,
    exceeded,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanSettings:
    exempt_admins: bool
    day_timezone: str
    contact_line: str


class PlanSettingsStore:

    def __init__(self, settings):
        self.settings = settings

    def read(self) -> PlanSettings:
        exempt = self.settings.get_setting(SETTING_EXEMPT_ADMINS, True)
        tz_name = self.settings.get_setting(SETTING_DAY_TIMEZONE, DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
        contact = self.settings.get_setting(SETTING_CONTACT_LINE, DEFAULT_CONTACT_LINE)
        return PlanSettings(
            exempt_admins=exempt if isinstance(exempt, bool) else str(exempt).lower() == "true",
            day_timezone=tz_name if valid_timezone(tz_name) else DEFAULT_TIMEZONE,
            contact_line=DEFAULT_CONTACT_LINE if contact is None else str(contact),
        )

    def write(self, key: str, value: Any) -> None:
        self.settings.set_setting(key, value)


class LedgerView:

    def __init__(self, events: LimitEventRepository, user_id: str, kind: str,
                 start: Optional[datetime], end: Optional[datetime], clock: Callable[[], datetime]):
        self._events = events
        self._user_id = user_id
        self._kind = kind
        self._start = start
        self._end = end
        self._clock = clock

    def total(self) -> float:
        return self._events.total(self._user_id, self._kind, self._start, self._end)

    def record(self, units: float = 1, ref_id: Optional[str] = None) -> str:
        return self._events.record(self._user_id, self._kind, units, ref_id, self._clock())

    def refund(self, ref_id: str) -> int:
        return self._events.refund(ref_id, self._clock())


@dataclass(frozen=True)
class Measured:
    used: float
    window_start: Optional[datetime]
    resets_at: Optional[datetime]


def _percent(used: float, limit: Optional[float]) -> Optional[float]:
    if limit is None:
        return None
    if limit <= 0:
        return 100.0
    return round(used / limit * 100, 1)


def _state(kind: LimitKind, used: float, limit: Optional[float]) -> str:
    if limit is None:
        return STATE_OK
    if limit <= 0 or used >= limit:
        return STATE_FULL
    return STATE_WARN if used / limit >= kind.warn_at else STATE_OK


def _number(value: float) -> float:
    return int(value) if float(value).is_integer() else value


class LimitGuard:

    def __init__(
        self,
        registry: LimitKindRegistry,
        plans: PlanRepository,
        events: LimitEventRepository,
        usage: UsageRepository,
        settings: PlanSettingsStore,
        clock: Callable[[], datetime] = now_utc,
        hook_runner: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        resolver: Optional[PlanResolver] = None,
    ):
        self.registry = registry
        self.plans = plans
        self.events = events
        self.usage = usage
        self.settings = settings
        self.clock = clock
        self.hook_runner = hook_runner
        self.resolver = resolver or PlanResolver(is_active=lambda key: registry.get(key) is not None)
        self._lock = threading.Lock()
        self._last_prune = 0.0

    def plans_by_id(self) -> Dict[str, Plan]:
        return {plan.id: plan for plan in self.plans.list_plans()}

    def resolve(self, subject: PlanSubject, plans: Optional[Mapping[str, Plan]] = None) -> Resolution:
        return self.resolver.resolve(subject, plans if plans is not None else self.plans_by_id())

    def resolution(self, user_id: str) -> Optional[Resolution]:
        subject = self.plans.subject(user_id)
        return self.resolve(subject) if subject else None

    def is_exempt(self, subject: PlanSubject, settings: Optional[PlanSettings] = None) -> bool:
        settings = settings or self.settings.read()
        return settings.exempt_admins and subject.is_admin

    def measure(self, kind: LimitKind, user_id: str, tz_name: Optional[str] = None,
                now: Optional[datetime] = None) -> Measured:
        now = now or self.clock()
        tz_name = tz_name or self.settings.read().day_timezone
        start, end = window_bounds(kind.window, tz_name, now)
        view = LedgerView(self.events, user_id, kind.key, start, end, self.clock)
        if kind.measure is None:
            used = view.total()
        else:
            result = kind.measure(MeasureContext(user_id=user_id, kind=kind.key, window_start=start,
                                                 window_end=end, events=view))
            used = float(result.used if isinstance(result, Usage) else result or 0)
        return Measured(used=used, window_start=start, resets_at=end)

    def usage_row(self, limit: EffectiveLimit, measured: Measured, *, admin_view: bool,
                  enforced: bool = True) -> Optional[Dict[str, Any]]:
        kind = self.registry.get(limit.kind)
        if kind is None:
            return None
        hide = kind.hides_values and not admin_view
        remaining = None if limit.value is None else max(0.0, limit.value - measured.used)
        candidate = limit.candidate
        return {
            "kind": kind.key,
            "label": kind.short_label or kind.label,
            "format": kind.user_format,
            "value_type": kind.value_type,
            "window": kind.window,
            "used": None if hide else _number(measured.used),
            "limit": None if hide or limit.value is None else _number(limit.value),
            "remaining": None if hide or remaining is None else _number(remaining),
            "percent": _percent(measured.used, limit.value),
            "state": _state(kind, measured.used, limit.value),
            "resets_at": measured.resets_at.isoformat() if measured.resets_at else None,
            "enforced": enforced,
            "plan": candidate.plan.ref() if candidate else None,
            "source": limit.source,
            "group": candidate.group_ref() if candidate and limit.source == "group" else None,
        }

    def _resets_label(self, kind: LimitKind, resets_at: Optional[datetime], tz_name: str, now: datetime) -> str:
        if resets_at is None:
            return ""
        if kind.window == "day":
            return f"{until_text(now, resets_at)} (at 00:00 {zone(tz_name).key})"
        return local_day_text(resets_at, tz_name)

    def _refusal(self, kind: LimitKind, limit: float, measured: Measured, settings: PlanSettings,
                 point: str, now: datetime) -> Dict[str, Any]:
        context = RefusalContext(
            kind=kind,
            used=measured.used,
            limit=limit,
            resets_at=measured.resets_at,
            resets_label=self._resets_label(kind, measured.resets_at, settings.day_timezone, now),
            contact_line=settings.contact_line,
            point=point,
        )
        hide = kind.hides_values
        return {
            "kind": kind.key,
            "code": kind.code,
            "label": kind.label,
            "format": kind.user_format,
            "used": None if hide else _number(measured.used),
            "limit": None if hide else _number(limit),
            "percent": _percent(measured.used, limit),
            "resets_at": measured.resets_at.isoformat() if measured.resets_at else None,
            "message": refusal_message(context),
        }

    def _evaluate(self, request: AdmissionRequest, subject: PlanSubject, settings: PlanSettings,
                  now: datetime) -> List[Dict[str, Any]]:
        if self.is_exempt(subject, settings):
            return []
        resolution = self.resolve(subject)
        refusals = []
        for limit in resolution.limited():
            kind = self.registry.get(limit.kind)
            if kind is None or not kind.applies_to(request):
                continue
            try:
                measured = self.measure(kind, subject.user_id, settings.day_timezone, now)
            except Exception:
                if kind.source == "core":
                    raise
                logger.exception("Limit kind %s failed to measure; not enforcing it", kind.key)
                continue
            if exceeded(limit.value, measured.used, kind.incoming_for(request)):
                refusals.append(self._refusal(kind, limit.value, measured, settings, request.point, now))
        never = [r for r in refusals if r["resets_at"] is None]
        later = sorted((r for r in refusals if r["resets_at"] is not None), key=lambda r: r["resets_at"], reverse=True)
        return never + later

    def _refuse(self, request: AdmissionRequest, refusals: List[Dict[str, Any]], settings: PlanSettings) -> None:
        error = LimitExceeded(refusals, request.point, settings.contact_line)
        if self.hook_runner is not None:
            try:
                self.hook_runner(PLANS_HOOKS.limit_exceeded, {
                    "user_id": request.user_id,
                    "point": request.point,
                    "kinds": error.kinds,
                    "codes": [r["code"] for r in refusals],
                })
            except Exception:
                logger.exception("plans.limit_exceeded hook failed")
        raise error

    def check(self, request: AdmissionRequest) -> None:
        subject = self.plans.subject(request.user_id)
        if subject is None:
            return
        settings = self.settings.read()
        refusals = self._evaluate(request, subject, settings, self.clock())
        if refusals:
            self._refuse(request, refusals, settings)

    def admit(self, request: AdmissionRequest) -> List[str]:
        with self._lock:
            subject = self.plans.subject(request.user_id)
            if subject is None:
                return []
            settings = self.settings.read()
            now = self.clock()
            refusals = self._evaluate(request, subject, settings, now)
            if refusals:
                self._refuse(request, refusals, settings)
            recorded = []
            for kind in self.registry.all():
                if kind.ledger and request.point in kind.enforce_at and kind.applies_to(request):
                    units = kind.incoming_for(request)
                    recorded.append(self.events.record(request.user_id, kind.key, units or 1, request.ref_id, now))
            self._maybe_prune(now)
            return recorded

    def refund(self, ref_id: Optional[str]) -> int:
        if not ref_id:
            return 0
        return self.events.refund(ref_id, self.clock())

    def on_generation_terminal(self, record: Any) -> None:
        state = getattr(getattr(record, "state", None), "value", getattr(record, "state", None))
        generation_id = getattr(record, "id", None)
        if not generation_id:
            return
        try:
            if state == "cancelled" and getattr(record, "started_at", None) is None:
                self.refund(generation_id)
            elif state == "failed" and not self.usage.generation_produced_output(generation_id):
                self.refund(generation_id)
        except Exception:
            logger.exception("Could not settle the limit ledger for generation %s", generation_id)

    def _maybe_prune(self, now: datetime) -> None:
        if time.monotonic() - self._last_prune < PRUNE_INTERVAL_SECONDS and self._last_prune:
            return
        self._last_prune = time.monotonic()
        try:
            self.events.prune(now - timedelta(days=LEDGER_RETENTION_DAYS))
        except Exception:
            logger.exception("Could not prune the limit ledger")
