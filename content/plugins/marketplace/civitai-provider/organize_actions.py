import asyncio
from typing import Any, Callable, Coroutine, Dict, List

from src.plugin_api.hooks import run_on_app_loop
from src.plugin_api.models import ProviderFetchException, run_provider_fetch
from src.plugin_api.organize import (
    OrganizeActionBlocked,
    OrganizeActionRetry,
    OrganizeActionSkipped,
    OrganizeChange,
    OrganizeItem,
)

PROVIDER = "civitai"
RETRY_AFTER_S = 900

LABELS = {
    "not_found": "No model with this file hash on CivitAI",
    "hash_mismatch": "CivitAI answered with a different file, so the answer was ignored",
    "unreachable": "CivitAI could not be reached",
    "rate_limited": "CivitAI asked to slow down",
    "unverified": "The file is not verified yet",
    "error": "The CivitAI lookup failed",
}


def _await(make: Callable[[], Coroutine[Any, Any, Dict[str, Any]]]) -> Dict[str, Any]:
    try:
        future = run_on_app_loop(make())
    except RuntimeError:
        return asyncio.run(make())
    return future.result()


def fetch_info(item: OrganizeItem, config: Dict[str, Any], user_id: Any) -> List[OrganizeChange]:
    if not item.get("sha256"):
        raise OrganizeActionRetry("unverified", LABELS["unverified"])
    try:
        summary = _await(lambda: run_provider_fetch(PROVIDER, [item.item_id]))
    except ProviderFetchException as exc:
        text = str(exc).lower()
        if "not initialized" in text or "not found" in text:
            raise OrganizeActionBlocked("provider_unavailable", "CivitAI is not set up")
        raise OrganizeActionSkipped("vetoed", str(exc))
    if summary.get("successful"):
        return [OrganizeChange(target_type="plugin", target_id=item.item_id, target_name="CivitAI")]
    errors = summary.get("errors") or [{}]
    code = errors[0].get("code") or "error"
    if code in ("unreachable", "rate_limited"):
        raise OrganizeActionRetry(code, LABELS[code], after_s=RETRY_AFTER_S)
    if code == "no_hash":
        raise OrganizeActionRetry("unverified", LABELS["unverified"])
    if code == "error":
        raise OrganizeActionRetry("error", errors[0].get("reason") or LABELS["error"])
    raise OrganizeActionSkipped(code, LABELS.get(code) or errors[0].get("reason") or LABELS["error"])
