import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from src.features.cloud.contracts import CloudModelSpec, PriceLine

SOURCE_PROVIDER = "provider"
SOURCE_ESTIMATE = "estimate"
SOURCE_UNKNOWN = "unknown"

_TIER_MEGAPIXELS = {
    "512": Decimal("0.262144"),
    "1k": Decimal("1.048576"),
    "2k": Decimal("4.194304"),
    "4k": Decimal("16.777216"),
}
_DIMENSIONS = re.compile(r"^\s*(\d+)\s*[xX*]\s*(\d+)\s*$")


@dataclass(frozen=True)
class CostEstimate:
    amount_usd: Decimal
    detail: Dict[str, Any]


def _megapixels(params: Mapping[str, Any]) -> Optional[Decimal]:
    for name in ("size", "resolution"):
        value = params.get(name)
        if value is None:
            continue
        text = str(value).strip().lower()
        match = _DIMENSIONS.match(text)
        if match:
            return Decimal(int(match.group(1)) * int(match.group(2))) / Decimal(1_000_000)
        if text in _TIER_MEGAPIXELS:
            return _TIER_MEGAPIXELS[text]
    return None


def _seconds(spec: CloudModelSpec, params: Mapping[str, Any]) -> Optional[Decimal]:
    value = params.get("duration_s")
    if value is None:
        value = next((p.default for p in spec.params if p.name == "duration_s" and p.default is not None), None)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _applies(line: PriceLine, params: Mapping[str, Any]) -> bool:
    if not line.applies_to:
        return True
    return line.applies_to.lower() in {str(value).lower() for value in params.values()}


def estimate(
    spec: CloudModelSpec,
    task: Optional[str],
    params: Mapping[str, Any],
    count: int,
    outputs: int = 0,
) -> Optional[CostEstimate]:
    units = Decimal(outputs or count or 1)
    lines: List[Dict[str, Any]] = []
    skipped: List[str] = []
    total = Decimal(0)
    for line in spec.pricing:
        if not _applies(line, params):
            continue
        quantity: Optional[Decimal]
        if line.unit == "request":
            quantity = Decimal(1)
        elif line.unit == "image":
            quantity = units
        elif line.unit == "megapixel":
            megapixels = _megapixels(params)
            quantity = megapixels * units if megapixels is not None else None
        elif line.unit == "second":
            seconds = _seconds(spec, params)
            quantity = seconds * units if seconds is not None else None
        else:
            quantity = None
        if quantity is None:
            skipped.append(line.unit)
            continue
        amount = line.usd * quantity
        total += amount
        lines.append({"unit": line.unit, "usd": str(line.usd), "quantity": str(quantity), "amount": str(amount)})
    if not lines:
        return None
    return CostEstimate(amount_usd=total, detail={"task": task, "lines": lines, "skipped_units": skipped})
