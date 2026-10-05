from typing import Any, Dict, Optional

from src.features.cloud.contracts import CLOUD_ENGINE
from src.features.plans.constants import KIND_CLOUD_SPEND, KIND_DAILY_GENERATIONS, KIND_STORAGE
from src.features.plans.repository import UsageRepository
from src.features.plans.windows import reset_label
from src.platform.plugins.limit_kinds import (
    BYTES_INPUT_SCALE,
    LimitKind,
    LimitKindRegistry,
    MeasureContext,
    RefusalContext,
    Usage,
)


def _trim(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def gigabytes(value: float) -> str:
    return _trim(value / BYTES_INPUT_SCALE)


def format_amount(kind: LimitKind, value: float) -> str:
    if kind.value_type == "bytes":
        return f"{gigabytes(value)} GB"
    if kind.value_type == "usd":
        return f"${value:.2f}"
    return _trim(value)


def _ending(context: RefusalContext) -> str:
    return f" {context.contact_line}" if context.contact_line else ""


def _storage_message(context: RefusalContext) -> str:
    used = f"{context.used / BYTES_INPUT_SCALE:.1f}"
    limit = gigabytes(context.limit)
    paused = "new generations are paused" if context.point == "submit" else "this file does not fit"
    return (
        f"Your storage is full. You have used {used} of {limit} GB, so {paused}. "
        "Delete some generations or uploads to make room, then try again. "
        f"Nothing has been deleted for you.{_ending(context)}"
    )


def _daily_message(context: RefusalContext) -> str:
    when = ""
    if context.resets_at is not None:
        when = f" You can generate again in {context.resets_label}."
    return (
        f"Daily limit reached. You have used {_trim(context.used)} of {_trim(context.limit)} generations today."
        f"{when}{_ending(context)}"
    )


def _cloud_message(context: RefusalContext) -> str:
    when = f" It resets on {context.resets_label}." if context.resets_at is not None else ""
    return f"You have used your cloud budget for this month.{when}{_ending(context)}"


def generic_message(context: RefusalContext) -> str:
    kind = context.kind
    when = ""
    if context.resets_at is not None:
        when = f" It resets {'in' if kind.window == 'day' else 'on'} {context.resets_label}."
    if kind.refusal_message:
        values = {
            "label": kind.label,
            "used": "" if kind.hides_values else format_amount(kind, context.used),
            "limit": "" if kind.hides_values else format_amount(kind, context.limit),
            "resets": context.resets_label,
            "contact": context.contact_line,
        }
        try:
            return kind.refusal_message.format_map(values).strip()
        except (KeyError, ValueError, IndexError):
            pass
    if kind.hides_values:
        return f"You have used your {kind.label.lower()} limit.{when}{_ending(context)}"
    return (
        f"{kind.label} limit reached. You have used {format_amount(kind, context.used)} of "
        f"{format_amount(kind, context.limit)}.{when}{_ending(context)}"
    )


def refusal_message(context: RefusalContext) -> str:
    if context.kind.refusal is not None:
        return context.kind.refusal(context)
    return generic_message(context)


def register_core_kinds(registry: LimitKindRegistry, usage: UsageRepository) -> None:
    def storage(context: MeasureContext) -> Usage:
        return Usage(used=usage.storage_bytes(context.user_id))

    def cloud(context: MeasureContext) -> Usage:
        return Usage(used=usage.cloud_spend(context.user_id, context.window_start, context.window_end))

    kinds = (
        LimitKind(
            key=KIND_STORAGE,
            label="Storage space",
            short_label="Storage",
            description="Generations and uploads, measured live",
            value_type="bytes",
            window="none",
            enforce_at=("submit", "upload"),
            measure=storage,
            refusal=_storage_message,
            refusal_code="storage_quota_exceeded",
            icon="database",
        ),
        LimitKind(
            key=KIND_DAILY_GENERATIONS,
            label="Generations per day",
            short_label="Generations today",
            description="Submits per user, a batch counts once",
            value_type="count",
            unit="per day",
            window="day",
            enforce_at=("submit",),
            ledger=True,
            refusal=_daily_message,
            refusal_code="daily_generations_exceeded",
            icon="bolt",
        ),
        LimitKind(
            key=KIND_CLOUD_SPEND,
            label="Cloud spend per month",
            short_label="Cloud budget",
            description="Counts cloud-provider cost. Users see a percent, never dollars.",
            value_type="usd",
            unit="USD / month",
            window="month",
            enforce_at=("submit",),
            measure=cloud,
            applies=lambda request: request.engine == CLOUD_ENGINE,
            incoming=lambda request: None,
            refusal=_cloud_message,
            refusal_code="cloud_budget_exceeded",
            admin_only_values=True,
            icon="calendar",
        ),
    )
    for kind in kinds:
        if registry.get(kind.key) is None:
            registry.register(kind)


def describe(kind: LimitKind, tz_name: Optional[str]) -> Dict[str, Any]:
    reset = None
    if kind.window != "none":
        reset = {"window": kind.window, "timezone": tz_name, "label": reset_label(kind.window, tz_name)}
    return {
        "key": kind.key,
        "label": kind.label,
        "short_label": kind.short_label or kind.label,
        "description": kind.description,
        "value_type": kind.value_type,
        "unit": kind.display_unit,
        "input_scale": kind.input_scale,
        "format": kind.user_format,
        "window": kind.window,
        "reset": reset,
        "enforce_at": list(kind.enforce_at),
        "warn_at": kind.warn_at,
        "admin_only_values": kind.hides_values,
        "icon": kind.display_icon,
        "source": kind.source,
        "plugin": kind.source != "core",
    }
