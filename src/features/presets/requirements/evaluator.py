"""Evaluating a preset's `requirements:` against this instance.

`evaluate_preset_requirements` runs every declared requirement's checker
concurrently, each under its own timeout, and never raises: a checker that
times out, isn't registered, or throws all resolve to an "unknown"
`RequirementResult` rather than failing the whole evaluation - one broken
requirement (a plugin checker with a bug, a slow filesystem) must never hide
the others.

`evaluate_preset_requirements_for_backends` is the multi-backend sibling: a
"host"-scoped entry (see `contracts.RequirementChecker`'s `scope` attribute)
is evaluated once for the whole preset, while a "backend"-scoped entry (an
engine-specific check, e.g. a ComfyUI custom node) is evaluated once per
enabled backend of the preset's engine - so an N-backend preset with M
host-scoped and K backend-scoped entries runs M+N*K checks, not N*(M+K).

`RequirementsCache` sits in front of both - a preset's requirements rarely
change between requests, and several checkers here do real I/O (a DB query,
GPU counters, `importlib.metadata`), so the admin's requirements panel
doesn't re-run all of that on every render of the preset list.
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from src.features.presets.requirements.contracts import RequirementContext, RequirementResult
from src.features.presets.templates import PresetTemplate
from src.platform.plugins.requirement_checkers import RequirementCheckerRegistry

logger = logging.getLogger(__name__)

# A checker gets this long to answer before its entry resolves to "unknown" -
# one slow/hung checker (a plugin's network call, say) must never stall the
# whole preset's requirements panel. A checker overrides this per-type via an
# optional `timeout_s` attribute (see contracts.RequirementChecker).
CHECK_TIMEOUT_SECONDS = 5.0


def preset_requirements_fingerprint(preset: PresetTemplate) -> str:
    """A short, stable digest of `preset.requirements` - changes whenever the
    block's content changes (add/remove/edit an entry), independent of
    `preset.version`, so a reload that only touched `requirements:` still
    invalidates the cache without an explicit refresh."""
    raw = json.dumps(preset.requirements or [], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def entry_scope(registry: RequirementCheckerRegistry, spec: Dict[str, Any]) -> str:
    """"host" or "backend" - which scope this `requirements:` entry's checker
    declares (see `contracts.RequirementChecker`'s `scope` attribute, default
    "host"). An unregistered type counts as "host": there's nothing to gain
    from re-running it once per backend when it resolves to the same
    "unknown" everywhere."""
    type_name = spec.get("type")
    registration = registry.get(type_name) if type_name else None
    if registration is None:
        return "host"
    return getattr(registration.checker, "scope", "host")


def describe_requirement_entry(registry: RequirementCheckerRegistry, spec: Dict[str, Any]) -> str:
    """A short, human label for one `requirements:` entry: the checker's own
    `describe()` when its type is registered and implements one, else the
    entry's first string-valued field besides `type`/`hint`/`optional`, else
    the type name itself. Shared by the requirements endpoint
    (`src.features.presets.operations.requirements`) and by
    `GenerationOrchestrator`'s routing-eligibility error message."""
    type_name = spec.get("type")
    registration = registry.get(type_name) if type_name else None
    if registration is not None:
        describe = getattr(registration.checker, "describe", None)
        if describe is not None:
            try:
                name = describe(spec)
            except Exception:
                name = None
            if name:
                return name
    for key, value in spec.items():
        if key in ("type", "hint", "optional"):
            continue
        if isinstance(value, str) and value:
            return value
    return type_name or "requirement"


def _split_by_scope(registry: RequirementCheckerRegistry, specs: List[Dict[str, Any]]) -> Tuple[List[int], List[int]]:
    """`(host_indices, backend_indices)` - `specs`' indices partitioned by
    `entry_scope`, each list in `specs` order."""
    host_indices: List[int] = []
    backend_indices: List[int] = []
    for i, spec in enumerate(specs):
        if entry_scope(registry, spec) == "backend":
            backend_indices.append(i)
        else:
            host_indices.append(i)
    return host_indices, backend_indices


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

    timeout_s = getattr(registration.checker, "timeout_s", CHECK_TIMEOUT_SECONDS)
    try:
        return await asyncio.wait_for(
            registration.checker.check(spec, ctx), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        return RequirementResult(
            status="unknown",
            detail=f"'{type_name}' check did not complete within {timeout_s:g}s",
        )
    except Exception as e:
        logger.warning(f"Requirement check '{type_name}' raised: {e}", exc_info=True)
        return RequirementResult(status="unknown", detail=f"'{type_name}' check failed: {e}")


async def _evaluate_many(
    registry: RequirementCheckerRegistry,
    specs: List[Dict[str, Any]],
    indices: List[int],
    ctx: RequirementContext,
) -> List[RequirementResult]:
    if not indices:
        return []
    return list(await asyncio.gather(*(_evaluate_one(registry, specs[i], ctx) for i in indices)))


async def evaluate_preset_requirements(
    registry: RequirementCheckerRegistry,
    preset: PresetTemplate,
    ctx: RequirementContext,
) -> List[RequirementResult]:
    """Run every entry in `preset.requirements` concurrently against one
    `ctx`. Never raises - see module docstring. This is the single-context
    path: a host-scoped entry ignores `ctx.backend` entirely, a
    backend-scoped entry sees whichever backend `ctx` carries (or `None`) -
    fine for checking one preset against one backend, but for several
    backends of the same engine prefer `evaluate_preset_requirements_for_backends`,
    which runs a host-scoped entry once rather than once per backend."""
    specs = preset.requirements or []
    if not specs:
        return []
    return list(await asyncio.gather(*(_evaluate_one(registry, spec, ctx) for spec in specs)))


def summarize(specs: List[Dict[str, Any]], results: List[RequirementResult]) -> Dict[str, int]:
    """Tally `results` by status - `{ok, missing, unknown, optional_missing}`.

    A "missing" entry marked `optional: true` counts toward
    `optional_missing` instead of `missing` (docs/presets.md "Requirements":
    an optional requirement's absence is advisory, never a hard failure, so
    it must not read as one in the summary a preset's "can I run this here"
    badge is built from). `ok`/`unknown` are unaffected by `optional` - only
    a miss changes bucket.

    `specs` and `results` must be the same length, in the same order
    `evaluate_preset_requirements` produced them (one spec per result)."""
    summary = {"ok": 0, "missing": 0, "unknown": 0, "optional_missing": 0}
    for spec, result in zip(specs, results):
        if result.status == "missing" and spec.get("optional", False):
            summary["optional_missing"] += 1
        else:
            summary[result.status] = summary.get(result.status, 0) + 1
    return summary


@dataclass
class _CacheEntry:
    results: List[RequirementResult]
    checked_at: float


@dataclass
class MultiBackendRequirementResults:
    """The outcome of `evaluate_preset_requirements_for_backends` /
    `RequirementsCache.get_or_evaluate_for_backends`: `preset.requirements`
    split by scope, with the host-scoped entries' results shared across every
    backend and each backend's own backend-scoped results kept separately."""

    specs: List[Dict[str, Any]]
    host_indices: List[int]
    host_results: List[RequirementResult]
    host_checked_at: float
    backend_indices: List[int]
    # backend_id -> a full, `specs`-order-aligned `_CacheEntry` (its
    # backend-scoped results merged with the shared host ones) - the same
    # shape `RequirementsCache`'s legacy per-(preset, backend) slot holds, so
    # a slot populated by either path reads back identically.
    backend_entries: Dict[str, _CacheEntry] = field(default_factory=dict)

    def full_results(self, backend_id: Optional[str]) -> List[RequirementResult]:
        """Host + this backend's own results, merged back into
        `preset.requirements` order. `backend_id=None` (or a backend never
        evaluated here) falls back to the host-only entries, with every
        backend-scoped entry read as "unknown" (no backend to check it
        against)."""
        entry = self.backend_entries.get(backend_id) if backend_id is not None else None
        if entry is not None:
            return entry.results
        host_by_index = dict(zip(self.host_indices, self.host_results))
        return [
            host_by_index.get(
                i, RequirementResult(status="unknown", detail="no backend available to check this against")
            )
            for i in range(len(self.specs))
        ]

    def summary_for(self, backend_id: Optional[str]) -> Dict[str, int]:
        return summarize(self.specs, self.full_results(backend_id))

    def checked_at_for(self, backend_id: Optional[str]) -> float:
        entry = self.backend_entries.get(backend_id) if backend_id is not None else None
        return entry.checked_at if entry is not None else self.host_checked_at

    def host_only_results(self) -> List[RequirementResult]:
        return list(self.host_results)

    def host_only_specs(self) -> List[Dict[str, Any]]:
        return [self.specs[i] for i in self.host_indices]


async def evaluate_preset_requirements_for_backends(
    registry: RequirementCheckerRegistry,
    preset: PresetTemplate,
    host_ctx: RequirementContext,
    backend_ctxs: Dict[str, RequirementContext],
) -> MultiBackendRequirementResults:
    """Evaluate `preset.requirements` once against `host_ctx` for every
    host-scoped entry, and once per `(backend_id, ctx)` in `backend_ctxs` for
    every backend-scoped entry - never re-running a host-scoped check per
    backend. Never raises - each individual check still goes through
    `_evaluate_one`'s per-check timeout/exception handling."""
    specs = preset.requirements or []
    host_indices, backend_indices = _split_by_scope(registry, specs)

    host_results = await _evaluate_many(registry, specs, host_indices, host_ctx)
    host_checked_at = time.time()
    host_by_index = dict(zip(host_indices, host_results))

    async def _for_backend(backend_id: str, ctx: RequirementContext) -> Tuple[str, _CacheEntry]:
        own_results = await _evaluate_many(registry, specs, backend_indices, ctx)
        own_by_index = dict(zip(backend_indices, own_results))
        merged = [
            host_by_index[i] if i in host_by_index else own_by_index[i]
            for i in range(len(specs))
        ]
        return backend_id, _CacheEntry(results=merged, checked_at=time.time())

    pairs = list(await asyncio.gather(
        *(_for_backend(backend_id, ctx) for backend_id, ctx in backend_ctxs.items())
    )) if backend_ctxs else []

    return MultiBackendRequirementResults(
        specs=specs,
        host_indices=host_indices,
        host_results=host_results,
        host_checked_at=host_checked_at,
        backend_indices=backend_indices,
        backend_entries=dict(pairs),
    )


class RequirementsCache:
    """Per-process cache of the last requirements evaluation for each
    (preset id, requirements-block fingerprint[, backend id]). Process-local
    and lost on restart, same as every other in-memory catalogue cache in
    this codebase (`PresetTemplateLoader.presets`, `field_type_registry`,
    ...) - not a database.

    Two independent slot kinds share this cache:
    - `_by_key[(preset_id, fingerprint, backend_id)]` - one backend's full,
      `preset.requirements`-order results (`get_or_evaluate`/`peek_summary`,
      the legacy single-context path; also written by
      `get_or_evaluate_for_backends` below, so either path's slot reads back
      identically).
    - `_host_by_key[(preset_id, fingerprint)]` - the host-scoped entries'
      shared results, independent of any backend.
    """

    def __init__(self):
        self._lock = Lock()
        self._by_key: Dict[Tuple[str, str, str], _CacheEntry] = {}
        self._host_by_key: Dict[Tuple[str, str], _CacheEntry] = {}

    @staticmethod
    def _key(preset: PresetTemplate, backend_id: Optional[str]) -> Tuple[str, str, str]:
        return (preset.id, preset_requirements_fingerprint(preset), backend_id or "")

    @staticmethod
    def _host_key(preset: PresetTemplate) -> Tuple[str, str]:
        return (preset.id, preset_requirements_fingerprint(preset))

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
        return {"summary": summarize(preset.requirements or [], entry.results), "checked_at": entry.checked_at}

    def peek_host_summary(
        self, registry: RequirementCheckerRegistry, preset: PresetTemplate
    ) -> Optional[Dict[str, Any]]:
        """The last evaluated `{summary, checked_at}` for `preset`'s
        host-scoped entries alone (independent of any backend), or `None` if
        they have never been evaluated. Read-only - never triggers an
        evaluation. The fallback the preset list/detail endpoints'
        `requirements_summary` reaches for when the preset's engine has no
        backend configured at all (see
        `src.features.presets.operations.query._peek_requirements_summary`)."""
        with self._lock:
            entry = self._host_by_key.get(self._host_key(preset))
        if entry is None:
            return None
        host_specs = [spec for spec in (preset.requirements or []) if entry_scope(registry, spec) != "backend"]
        return {"summary": summarize(host_specs, entry.results), "checked_at": entry.checked_at}

    def peek_backend_missing(
        self, registry: RequirementCheckerRegistry, preset: PresetTemplate, backend_id: str
    ) -> Optional[List[str]]:
        """The names of `backend_id`'s hard-missing (non-optional) entries
        from its last cached evaluation, or `None` if it has never been
        evaluated - the routing-eligibility check
        (`GenerationOrchestrator._narrow_backends_by_requirements`) treats
        `None` as "unknown" (kept as a candidate, with a background refresh
        scheduled) rather than as "no misses". Never triggers an evaluation."""
        with self._lock:
            entry = self._by_key.get(self._key(preset, backend_id))
        if entry is None:
            return None
        return [
            describe_requirement_entry(registry, spec)
            for spec, result in zip(preset.requirements or [], entry.results)
            if result.status == "missing" and not spec.get("optional", False)
        ]

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

    async def get_or_evaluate_for_backends(
        self,
        registry: RequirementCheckerRegistry,
        preset: PresetTemplate,
        host_ctx: RequirementContext,
        backend_ctxs: Dict[str, RequirementContext],
        refresh: bool = False,
    ) -> MultiBackendRequirementResults:
        """The cached per-scope evaluation for `preset`: the host-scoped
        entries against `host_ctx` (evaluated once, shared by every backend)
        and the backend-scoped entries against each `ctx` in `backend_ctxs`
        (evaluated once per backend id) - `refresh` forces a fresh run of
        every one of them. A backend absent from `backend_ctxs` but cached
        from an earlier call is never evicted here; it simply isn't part of
        the result this call returns."""
        specs = preset.requirements or []
        host_indices, backend_indices = _split_by_scope(registry, specs)

        host_key = self._host_key(preset)
        with self._lock:
            host_entry = None if refresh else self._host_by_key.get(host_key)
        if host_entry is None:
            host_results = await _evaluate_many(registry, specs, host_indices, host_ctx)
            host_entry = _CacheEntry(results=host_results, checked_at=time.time())
            with self._lock:
                self._host_by_key[host_key] = host_entry

        host_by_index = dict(zip(host_indices, host_entry.results))

        async def _for_backend(backend_id: str, ctx: RequirementContext) -> Tuple[str, _CacheEntry]:
            key = self._key(preset, backend_id)
            if not refresh:
                with self._lock:
                    cached = self._by_key.get(key)
                if cached is not None:
                    return backend_id, cached

            own_results = await _evaluate_many(registry, specs, backend_indices, ctx)
            own_by_index = dict(zip(backend_indices, own_results))
            merged = [
                host_by_index[i] if i in host_by_index else own_by_index[i]
                for i in range(len(specs))
            ]
            entry = _CacheEntry(results=merged, checked_at=time.time())
            with self._lock:
                self._by_key[key] = entry
            return backend_id, entry

        pairs = list(await asyncio.gather(
            *(_for_backend(backend_id, ctx) for backend_id, ctx in backend_ctxs.items())
        )) if backend_ctxs else []

        return MultiBackendRequirementResults(
            specs=specs,
            host_indices=host_indices,
            host_results=host_entry.results,
            host_checked_at=host_entry.checked_at,
            backend_indices=backend_indices,
            backend_entries=dict(pairs),
        )
