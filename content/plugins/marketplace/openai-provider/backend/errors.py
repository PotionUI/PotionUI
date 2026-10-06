import re
from typing import Any, List, Mapping, Optional

from src.plugin_api.cloud import CloudError, parse_retry_after

MODERATION_CODES = frozenset({"moderation_blocked", "content_policy_violation"})
BILLING_CODES = frozenset({
    "insufficient_quota",
    "credit_balance_exhausted",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
    "billing_hard_limit_reached",
    "billing_not_active",
})
RESET_HEADERS = ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

REFUSED = "The model's content filter refused this request. Try a different prompt or picture."
CREDITS = "The OpenAI account is out of credit or over its spending limit. An administrator can check billing at platform.openai.com."
AUTH = "OpenAI rejected the API key. Check it in Administration, Backends."
FORBIDDEN = "OpenAI refused this request. The key or project may not be allowed to use this model, or the organization may need to be verified."
RATE = "OpenAI is rate limiting requests. Try again shortly."
DOWN = "OpenAI is having trouble at the moment. Try again shortly."
OVERLOADED = "OpenAI is overloaded at the moment. Try again shortly."
UNKNOWN_MODEL = "OpenAI does not offer this model to this account."
BAD_SETTINGS = "OpenAI rejected the request settings."


def reset_seconds(value: Optional[str]) -> Optional[float]:
    text = (value or "").strip().lower()
    if not text:
        return None
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(number + unit for number, unit in parts) != text:
        return None
    return sum(float(number) * _UNIT_SECONDS[unit] for number, unit in parts)


def _retry_after(headers: Mapping[str, str]) -> Optional[float]:
    direct = parse_retry_after(headers.get("retry-after"))
    if direct is not None:
        return direct
    resets = [seconds for seconds in (reset_seconds(headers.get(name)) for name in RESET_HEADERS) if seconds is not None]
    return parse_retry_after(str(max(resets))) if resets else None


def _facts(error: Mapping[str, Any], headers: Mapping[str, str]) -> List[str]:
    facts: List[str] = []
    for label, field in (("code", "code"), ("type", "type"), ("param", "param")):
        if error.get(field):
            facts.append(f"{label}: {error[field]}")
    request_id = headers.get("x-request-id")
    if request_id:
        facts.append(f"request: {request_id}")
    return facts


def _is_moderation(error: Mapping[str, Any]) -> bool:
    code = str(error.get("code") or "").lower()
    if code in MODERATION_CODES:
        return True
    message = str(error.get("message") or "").lower()
    return str(error.get("type") or "") == "image_generation_user_error" and "safety" in message


def _moderation_detail(error: Mapping[str, Any], headers: Mapping[str, str]) -> str:
    facts = _facts(error, headers)
    details = error.get("moderation_details") if isinstance(error.get("moderation_details"), dict) else {}
    if details.get("moderation_stage"):
        facts.append(f"stage: {details['moderation_stage']}")
    categories = details.get("categories")
    if isinstance(categories, list) and categories:
        facts.append("categories: " + ", ".join(str(category) for category in categories))
    return "moderation refused (" + "; ".join(facts) + ")"


def map_error(status: int, headers: Mapping[str, str], body: Any) -> Optional[CloudError]:
    if status < 400:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or "").lower()
    message = str(error.get("message") or (body if isinstance(body, str) else ""))[:300]
    facts = "; ".join(_facts(error, headers))
    detail = f"HTTP {status}: {message}" + (f" ({facts})" if facts else "")
    retry_after = _retry_after(headers)
    if _is_moderation(error):
        return CloudError("refused", REFUSED, detail=_moderation_detail(error, headers))
    if code in BILLING_CODES or str(error.get("type") or "").lower() == "insufficient_quota":
        return CloudError("credits", CREDITS, detail=detail)
    if status == 401:
        return CloudError("auth", AUTH, detail=detail)
    if status == 403:
        return CloudError("refused", FORBIDDEN, detail=detail)
    if status == 429:
        return CloudError("rate_limited", RATE, detail=detail, retry_after_s=retry_after)
    if status == 404:
        return CloudError("invalid_request", UNKNOWN_MODEL, detail=detail)
    if status in (408, 504):
        return CloudError("timeout", "OpenAI took too long to answer.", detail=detail, retry_after_s=retry_after)
    if status == 503:
        return CloudError("unavailable", OVERLOADED, detail=detail, retry_after_s=retry_after)
    if status >= 500:
        return CloudError("unavailable", DOWN, detail=detail, retry_after_s=retry_after)
    if status in (400, 409, 413, 415, 422):
        return CloudError("invalid_request", BAD_SETTINGS, detail=detail)
    return None
