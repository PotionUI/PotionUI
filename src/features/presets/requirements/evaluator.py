"""Evaluating a preset's `requirements:` against this instance.

`evaluate_preset_requirements` runs every declared requirement's checker
concurrently, each under its own timeout, and never raises: a checker that
times out, isn't registered, or throws all resolve to an "unknown"
`RequirementResult` rather than failing the whole evaluation - one broken
requirement (a plugin checker with a bug, a slow filesystem) must never hide
the others.

`RequirementsCache` sits in front of it - a preset's requirements rarely
change between requests, and several checkers here do real I/O (a DB query,
GPU counters, `importlib.metadata`), so the admin's requirements panel
doesn't re-run all of that on every render of the preset list.
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from src.features.presets.requirements.contracts import RequirementContext, RequirementResult
from src.features.presets.templates import PresetTemplate
from src.platform.plugins.requirement_checkers import RequirementCheckerRegistry

logger = logging.getLogger(__name__)

# A checker gets this long to answer before its entry resolves to "unknown" -
# one slow/hung checker (a plugin's network call, say) must never stall the
# whole preset's requirements panel.
CHECK_TIMEOUT_SECONDS = 5.0


def preset_requirements_fingerprint(preset: PresetTemplate) -> str:
    """A short, stable digest of `preset.requirements` - changes whenever the
    block's content changes (add/remove/edit an entry), independent of
    `preset.version`, so a reload that only touched `requirements:` still
    invalidates the cache without an explicit refresh."""
    raw = json.dumps(preset.requirements or [], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def _evaluate_one(
    registry: RequirementCheckerRegistry, spec: Dict[str, Any], ctx: RequirementContext
) -> RequirementResult:
    type_name = spec.get("type")
    registration = registry.get(type_name) if type_name else None
    if registration is None:
        return RequirementResult(
            status="unknown",
            detail=f"no checker registered for requirement type '{type_name}'",
        )

    try:
        return await asyncio.wait_for(
            registration.checker.check(spec, ctx), timeout=CHECK_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return RequirementResult(
            status="unknown",
            detail=f"'{type_name}' check did not complete within {CHECK_TIMEOUT_SECONDS:g}s",
        )
    except Exception as e:
        logger.warning(f"Requirement check '{type_name}' raised: {e}", exc_info=True)
        return RequirementResult(status="unknown", detail=f"'{type_name}' check failed: {e}")


async def evaluate_preset_requirements(
    registry: RequirementCheckerRegistry,
    preset: PresetTemplate,
    ctx: RequirementContext,
) -> List[RequirementResult]:
    """Run every entry in `preset.requirements` concurrently. Never raises -
    see module docstring."""
    specs = preset.requirements or []
    if not specs:
        return []
    return list(await asyncio.gather(*(_evaluate_one(registry, spec, ctx) for spec in specs)))


def summarize(results: List[RequirementResult]) -> Dict[str, int]:
    summary = {"ok": 0, "missing": 0, "unknown": 0}
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
    return summary


@dataclass
class _CacheEntry:
    results: List[RequirementResult]
    checked_at: float


class RequirementsCache:
    """Per-process cache of the last `evaluate_preset_requirements` run for
    each (preset id, requirements-block fingerprint, backend id). Process-
    local and lost on restart, same as every other in-memory catalogue cache
    in this codebase (`PresetTemplateLoader.presets`, `field_type_registry`,
    ...) - not a database."""

    def __init__(self):
        self._lock = Lock()
        self._by_key: Dict[Tuple[str, str, str], _CacheEntry] = {}

    @staticmethod
    def _key(preset: PresetTemplate, backend_id: Optional[str]) -> Tuple[str, str, str]:
        return (preset.id, preset_requirements_fingerprint(preset), backend_id or "")

    def peek_summary(self, preset: PresetTemplate, backend_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The last evaluated `{summary, checked_at}` for this preset, or
        `None` if it has never been evaluated (or a reload/requirements edit/
        backend change invalidated it). Read-only - never triggers an
        evaluation. Used by the preset list/detail endpoints so listing
        presets never runs a check."""
        with self._lock:
            entry = self._by_key.get(self._key(preset, backend_id))
        if entry is None:
            return None
        return {"summary": summarize(entry.results), "checked_at": entry.checked_at}

    async def get_or_evaluate(
        self,
        registry: RequirementCheckerRegistry,
        preset: PresetTemplate,
        ctx: RequirementContext,
        backend_id: Optional[str] = None,
        refresh: bool = False,
    ) -> Tuple[List[RequirementResult], float]:
        """The cached `(results, checked_at)` for this key, or a fresh
        evaluation when there is none yet or `refresh` is set."""
        key = self._key(preset, backend_id)
        if not refresh:
            with self._lock:
                entry = self._by_key.get(key)
            if entry is not None:
                return entry.results, entry.checked_at

        results = await evaluate_preset_requirements(registry, preset, ctx)
        checked_at = time.time()
        with self._lock:
            self._by_key[key] = _CacheEntry(results=results, checked_at=checked_at)
        return results, checked_at
