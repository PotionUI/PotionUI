"""`artifacts.fetch` - download whatever `artifacts.plan` found missing.

By the time this step ever runs, consent has structurally already happened:
the run cannot leave `awaiting_consent` (where `artifacts.plan` parks it) and
reach this later step without `RecipeRunner.grant_consent` having been
called (see the state machine in `records.py`), so this executor doesn't need
to re-derive "what was approved" - it just re-checks presence (a previous
attempt may have downloaded some of this already) and fetches the rest.

"who fetches" is the core download queue (`src.features.downloads`),
injected by the composition root - guided setup works out of the box, no
plugin required. "where do we get it from" stays capability-driven:
`src.features.providers.registry.ProviderRegistry` (HASH_LOOKUP/
DOWNLOAD_URL/SEARCH via marketplace-provider plugins) resolves a concrete
URL for an artifact the recipe doesn't hardcode one for.

Reuses the download queue's existing worker rather than building a second
download engine: `DownloadWorker._download_file` already resumes a partial
download via an HTTP Range request and `_verify_checksum` already verifies
SHA256 (see `src/features/downloads/worker.py`) - this executor only queues
(`queue_model_download`) and polls (`get_download`) that existing machinery,
updating this attempt's progress fields as the download advances.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.features.models.recommendations import find_installed_model, format_size
from src.features.models.repository import ModelRepository
from src.features.recipes.executors._artifact_lookup import (
    find_artifact_model,
    find_slot_model,
    same_size_candidates,
)
from src.features.recipes.executors._async_bridge import run_sync
from src.features.recipes.executors._provider_credentials import (
    credential_prompt_for_provider,
    resolve_provider_registry,
)
from src.features.recipes.executors.base import StepContext, StepResult
from src.features.recipes.variants import resolve_selection

_POLL_INTERVAL_SECONDS = 2.0
# A slow-but-moving download (a multi-GB file on a home connection routinely
# takes well over an hour) must never fail just for taking a long time - only
# a genuinely stuck one should. So the deadline is on *stalled* byte progress,
# not wall-clock total: this many seconds with no forward movement in
# `downloaded_bytes` fails the step; any movement at all resets the clock.
_STALL_SECONDS = 600

# Substrings of the download worker's own failure messages (see
# `DownloadWorker._download_file`/`_auth_failure_message`) that mean "this
# failed because of missing/invalid credentials, not a real network/file
# problem" - matched structurally (HTTP status, HTML-instead-of-a-file), never
# against a provider name.
_AUTH_FAILURE_SIGNALS = ("HTTP 401", "HTTP 403", "returned HTML instead of a file")


def resolve_download_url(artifact, get_registry: Callable[[], Any]) -> Tuple[Optional[str], str]:
    provider_hint = artifact.provider_hint or {}

    explicit_url = provider_hint.get("download_url")
    if explicit_url:
        return explicit_url, "the recipe's own source"

    registry = get_registry()
    if registry is None:
        return None, "no marketplace provider plugin is available to look up a source"

    source = provider_hint.get("source")
    model_id = provider_hint.get("model_id")
    if source and model_id:
        try:
            url = run_sync(registry.get_download_url(source, model_id, provider_hint.get("version_id")))
        except Exception:
            url = None
        if url:
            return url, f"{source} (matched by id)"

    checksum = artifact.checksum.value if artifact.checksum else None
    if checksum:
        try:
            info = run_sync(registry.get_model_by_hash_any(checksum))
        except Exception:
            info = None
        if info and info.download_url:
            return info.download_url, f"{info.provider_id} (matched by checksum)"

    if source:
        query = artifact.display_name or artifact.filename
        try:
            results = run_sync(
                registry.search_models(source, query, model_type=artifact.model_type, limit=1)
            )
        except Exception:
            results = []
        if results and results[0].download_url:
            return results[0].download_url, f"{source} (matched by search)"

    return None, "no checksum, direct source, or provider id/search match was available"


_ACTIVE_STATUSES = ("pending", "downloading", "paused")
_TERMINAL_OK = "completed"
_TERMINAL_BAD = ("failed", "cancelled")


def _status_of(download) -> str:
    status = getattr(download, "status", None)
    return str(getattr(status, "value", status) or "")


class _PlanSource:
    def __init__(self, entry: Dict[str, Any]):
        self.id = entry.get("slot_id")
        self.filename = entry.get("filename")
        self.model_type = entry.get("model_type")
        self.display_name = entry.get("display_name") or self.filename
        self.size_bytes = entry.get("size_bytes")
        self.provider_hint = dict(entry.get("provider_hint") or {})
        self.checksum = _Checksum(entry.get("sha256"))
        self.gated = bool(entry.get("gated"))
        self.license_url = entry.get("license_url")


class _Checksum:
    def __init__(self, value):
        self.value = value


class ArtifactsFetchExecutor:
    def __init__(
        self,
        download_queue,
        model_repository: ModelRepository,
        provider_registry_factory=None,
        type_manager=None,
        digests=None,
        targets=None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.download_queue = download_queue
        self.model_repository = model_repository
        self._type_manager = type_manager
        self._digests = digests
        self._targets = targets
        self._sleep = sleep
        self._clock = clock
        # Lazy/optional: resolving the provider registry needs async
        # discovery/init this constructor shouldn't force (see
        # `src.features.providers.registry.ensure_providers_discovered`).
        # Injectable for tests; defaults to the real module-level registry.
        self._provider_registry_factory = provider_registry_factory

    def execute(self, context: StepContext) -> StepResult:
        if "plan" in context.step.params:
            return self._execute_plan(context)
        artifact_ids: List[str] = list(context.step.params.get("artifact_ids") or [])
        if not artifact_ids:
            return StepResult.fail(
                "ARTIFACTS_FETCH_MISCONFIGURED",
                "This step doesn't say which artifacts to fetch.",
            )

        to_fetch = []
        for artifact_id in artifact_ids:
            artifact = context.recipe.get_artifact(artifact_id)
            if artifact is None:
                return StepResult.fail(
                    "ARTIFACTS_FETCH_MISCONFIGURED",
                    f"This step references an artifact ('{artifact_id}') the recipe doesn't declare.",
                )
            target = self._target(artifact, context.selections.get(artifact_id), context.report_progress)
            if target is not None:
                to_fetch.append(target)

        if not to_fetch:
            return StepResult.ok({"fetched": [], "message": "Nothing left to download."})

        service = self.download_queue
        if service is None:
            return StepResult.fail(
                "NO_DOWNLOAD_MANAGER",
                "The download queue is not wired into this instance, so this file can't be fetched automatically.",
            )

        fetched: List[Dict[str, Any]] = []
        for artifact in to_fetch:
            url, note = self._resolve_download_url(artifact)
            if not url:
                return StepResult.fail(
                    "ARTIFACT_SOURCE_UNRESOLVED",
                    f"Couldn't work out where to download "
                    f"'{artifact.display_name or artifact.filename}' from ({note}).",
                    suggested_repair=(
                        "Download it manually via Administration -> Models, or give the recipe "
                        "a checksum or a direct source so it can be found automatically."
                    ),
                )

            checksum = artifact.checksum.value if artifact.checksum else None
            provider_id = (artifact.provider_hint or {}).get("source")

            try:
                download = run_sync(
                    service.queue_model_download(
                        url=url,
                        model_type=artifact.model_type,
                        filename=artifact.filename,
                        checksum_sha256=checksum,
                        provider_id=provider_id,
                        created_by=context.owner_user_id,
                    )
                )
            except Exception as exc:
                return StepResult.fail(
                    "ARTIFACT_DOWNLOAD_FAILED",
                    f"Downloading '{artifact.display_name or artifact.filename}' failed to start: {exc}",
                )

            error = self._wait_for_completion(service, download.id, artifact, context.report_progress)
            if error:
                return StepResult.fail(
                    "ARTIFACT_DOWNLOAD_FAILED",
                    error,
                    suggested_repair=self._auth_repair_hint(artifact, error),
                )

            fetched.append(
                {
                    "id": artifact.id,
                    "filename": artifact.filename,
                    "display_name": artifact.display_name or artifact.filename,
                    "download_id": download.id,
                    "source": note,
                }
            )

        return StepResult.ok({"fetched": fetched})

    def _get_provider_registry(self):
        return resolve_provider_registry(self._provider_registry_factory)

    def _plan_files(self, plans: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str], str]:
        files: Dict[Tuple[str, str], Dict[str, Any]] = {}
        destination = None
        target = None
        engine = "native"
        for plan in plans:
            destination = destination or plan.get("destination_backend_id")
            target = target or plan.get("target")
            engine = plan.get("engine") or engine
            for entry in plan.get("files") or []:
                key = (entry.get("model_type") or "", (entry.get("filename") or "").lower())
                files.setdefault(key, entry)
        return list(files.values()), destination, target, engine

    def _present(self, entry: Dict[str, Any]) -> bool:
        return find_installed_model(
            self.model_repository,
            entry.get("model_type") or "",
            entry.get("filename") or "",
            sha256=entry.get("sha256"),
            size_bytes=entry.get("size_bytes"),
            digests=self._digests,
            hash_candidates="never",
            type_manager=self._type_manager,
        ) is not None

    def _disk_problem(self, files: List[Dict[str, Any]], engine: str, target: Optional[str]) -> Optional[str]:
        if self._targets is None or not files:
            return None
        option, _ = self._targets.resolve(engine, target)
        if option is None:
            return None
        profile = self._targets.profile(option, {f.get("model_type") or "" for f in files})
        needed: Dict[str, int] = {}
        volumes = {}
        for entry in files:
            disk = profile.disk_for(entry.get("model_type") or "")
            if disk is None or disk.usable_bytes is None:
                continue
            volumes[disk.id] = disk
            needed[disk.id] = needed.get(disk.id, 0) + (entry.get("size_bytes") or 0)
        for disk_id, total in needed.items():
            disk = volumes[disk_id]
            if total > disk.usable_bytes:
                return (
                    f"Not enough free space on {option.label} for these files: they need {format_size(total)}, "
                    f"but only {format_size(disk.usable_bytes)} can be used ({format_size(disk.free_bytes)} free, "
                    f"{format_size(disk.reserve_bytes)} kept spare)."
                )
        return None

    def _active_index(self, service) -> Dict[Tuple[str, Optional[str]], List[Any]]:
        repo = getattr(service, "repo", None)
        index: Dict[Tuple[str, Optional[str]], List[Any]] = {}
        if repo is None:
            return index
        try:
            from src.features.downloads.models import DownloadStatus

            for status in (DownloadStatus.PENDING, DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED):
                for row in repo.get_all(status=status) or []:
                    key = ((getattr(row, "filename", "") or "").lower(), getattr(row, "destination_backend_id", None))
                    index.setdefault(key, []).append(row)
        except Exception:
            return {}
        return index

    @staticmethod
    def _in_type_dir(service, row, model_type: str) -> bool:
        resolver = getattr(service, "resolver", None)
        path = getattr(row, "destination_path", None)
        if resolver is None or not path:
            return True
        try:
            type_dir = Path(os.path.realpath(resolver.write_dir(model_type).path))
            parent = Path(os.path.realpath(Path(path).parent))
        except Exception:
            return False
        return parent == type_dir or type_dir in parent.parents

    def _adoptable(self, service, index, entry: Dict[str, Any], url: str, destination: Optional[str]):
        sha = (entry.get("sha256") or "").lower()
        for row in index.get(((entry.get("filename") or "").lower(), destination), []):
            same_source = getattr(row, "url", None) == url or (sha and (getattr(row, "checksum_sha256", None) or "").lower() == sha)
            if not same_source:
                continue
            if destination is None and not self._in_type_dir(service, row, entry.get("model_type") or ""):
                continue
            return row
        return None

    def _fail_after_queueing(self, service, queued, code: str, detail: str, suggested_repair: Optional[str] = None) -> StepResult:
        self._cancel_unfinished(service, queued)
        return StepResult.fail(code, detail, suggested_repair=suggested_repair)

    def _execute_plan(self, context: StepContext) -> StepResult:
        plans = context.plans(context.step.params.get("plan"))
        if not plans:
            return StepResult.fail("ARTIFACTS_FETCH_NO_PLAN", "There is no approved install plan to download from.")
        files, destination, target, engine = self._plan_files(plans)
        pending = [entry for entry in files if not self._present(entry)]
        if not pending:
            return StepResult.ok({"fetched": [], "files": [], "message": "Nothing left to download."})
        service = self.download_queue
        if service is None:
            return StepResult.fail(
                "NO_DOWNLOAD_MANAGER",
                "The download queue is not wired into this instance, so these files can't be fetched automatically.",
            )
        problem = self._disk_problem(pending, engine, target)
        if problem:
            return StepResult.fail(
                "NOT_ENOUGH_DISK",
                problem,
                suggested_repair="Free some space, or go back and pick fewer modes or a smaller file.",
            )
        index = self._active_index(service)
        queued: List[Tuple[Dict[str, Any], Any, str]] = []
        for entry in pending:
            if context.is_cancelled():
                break
            source = _PlanSource(entry)
            url, note = self._resolve_download_url(source)
            if not url:
                return self._fail_after_queueing(
                    service,
                    queued,
                    "ARTIFACT_SOURCE_UNRESOLVED",
                    f"Couldn't work out where to download '{source.display_name}' from ({note}).",
                    suggested_repair=(
                        "Download it manually via Administration -> Models, or give the recipe "
                        "a checksum or a direct source so it can be found automatically."
                    ),
                )
            adopted = self._adoptable(service, index, entry, url, destination)
            if adopted is not None:
                queued.append((entry, adopted, "an earlier attempt"))
                continue
            try:
                download = run_sync(
                    service.queue_model_download(
                        url=url,
                        model_type=source.model_type,
                        filename=source.filename,
                        checksum_sha256=source.checksum.value,
                        provider_id=source.provider_hint.get("source"),
                        created_by=context.owner_user_id,
                        destination_backend_id=destination,
                    )
                )
            except Exception as exc:
                return self._fail_after_queueing(
                    service,
                    queued,
                    "ARTIFACT_DOWNLOAD_FAILED",
                    f"Downloading '{source.display_name}' failed to start: {exc}",
                )
            queued.append((entry, download, note))
        return self._wait_for_all(service, queued, context)

    def _cancel_unfinished(self, service, queued) -> None:
        for _, download, _ in queued:
            try:
                current = service.get_download(download.id)
                if _status_of(current) in _ACTIVE_STATUSES:
                    run_sync(service.cancel_download(download.id))
            except Exception:
                continue

    def _wait_for_all(self, service, queued, context: StepContext) -> StepResult:
        last_total_bytes = -1
        last_progress_at = self._clock()
        last_sample: Optional[Tuple[float, int]] = None
        while True:
            if context.is_cancelled():
                self._cancel_unfinished(service, queued)
                return StepResult.fail("RUN_CANCELLED", "The install was cancelled.")
            rows = []
            failure = None
            for entry, download, note in queued:
                current = service.get_download(download.id)
                status = _status_of(current)
                size = entry.get("size_bytes") or getattr(current, "total_bytes", None)
                done = getattr(current, "downloaded_bytes", None) or 0
                if status == _TERMINAL_OK and size:
                    done = size
                rows.append(
                    {
                        "slot_id": entry.get("slot_id"),
                        "display_name": entry.get("display_name") or entry.get("filename"),
                        "filename": entry.get("filename"),
                        "size_bytes": size,
                        "downloaded_bytes": done,
                        "status": status or "pending",
                        "download_id": download.id,
                        "speed": getattr(current, "speed_bytes_per_sec", None),
                        "source": note,
                    }
                )
                if status in _TERMINAL_BAD and failure is None:
                    error = getattr(current, "error_message", None)
                    name = entry.get("display_name") or entry.get("filename")
                    failure = (entry, f"Downloading '{name}' {status}" + (f": {error}" if error else "."))
            downloaded = sum(r["downloaded_bytes"] or 0 for r in rows)
            total = sum(r["size_bytes"] or 0 for r in rows) or None
            now = self._clock()
            speeds = [r["speed"] for r in rows if r["status"] == "downloading" and r["speed"]]
            speed = sum(speeds) if speeds else None
            if speed is None and last_sample is not None and now > last_sample[0]:
                speed = max(0.0, (downloaded - last_sample[1]) / (now - last_sample[0])) or None
            last_sample = (now, downloaded)
            files_done = sum(1 for r in rows if r["status"] == _TERMINAL_OK)
            view = [{k: v for k, v in r.items() if k not in ("speed", "source")} for r in rows]
            context.report_progress(
                progress_current=downloaded,
                progress_total=total,
                progress_unit="bytes",
                safe_output={
                    "files": view,
                    "files_done": files_done,
                    "files_total": len(rows),
                    "speed_bytes_per_sec": int(speed) if speed else None,
                    "eta_seconds": int((total - downloaded) / speed) if speed and total else None,
                },
            )
            if failure is not None:
                entry, message = failure
                return self._fail_after_queueing(
                    service,
                    queued,
                    "ARTIFACT_DOWNLOAD_FAILED",
                    message,
                    suggested_repair=self._auth_repair_hint(_PlanSource(entry), message),
                )
            if files_done == len(rows):
                return StepResult.ok(
                    {
                        "fetched": [
                            {
                                "id": r["slot_id"],
                                "filename": r["filename"],
                                "display_name": r["display_name"],
                                "download_id": r["download_id"],
                                "source": r["source"],
                            }
                            for r in rows
                        ],
                        "files": view,
                        "message": f"Downloaded {len(rows)} file{'s' if len(rows) != 1 else ''}.",
                    }
                )
            if downloaded > last_total_bytes:
                last_total_bytes = downloaded
                last_progress_at = now
            elif now - last_progress_at >= _STALL_SECONDS:
                minutes = _STALL_SECONDS // 60
                return self._fail_after_queueing(
                    service,
                    queued,
                    "ARTIFACT_DOWNLOAD_FAILED",
                    f"The downloads made no progress for {minutes} minutes - the connection may have stalled.",
                )
            self._sleep(_POLL_INTERVAL_SECONDS)

    def _target(self, artifact, variant_id: Optional[str], report: Optional[Callable[..., None]] = None):
        if artifact.variants and artifact.get_variant(variant_id) is None:
            found = find_slot_model(
                self.model_repository, artifact, type_manager=self._type_manager,
                digests=self._digests, hash_candidates="never",
            )
            if found is not None:
                return None
        resolved = resolve_selection(artifact, variant_id)
        if find_artifact_model(
            self.model_repository, resolved, self._type_manager, self._digests, "never"
        ) is not None:
            return None
        if self._verified_among_candidates(resolved, report):
            return None
        return resolved

    def _verified_among_candidates(self, resolved, report: Optional[Callable[..., None]]) -> bool:
        if not same_size_candidates(resolved, self._digests):
            return False

        def progress(checked: int, total: int) -> None:
            report(progress_current=checked, progress_total=total, progress_unit="files")

        self._digests.find_by_digest(
            resolved.checksum.value, resolved.size_bytes, "paced", on_progress=progress if report is not None else None
        )
        return find_artifact_model(
            self.model_repository, resolved, self._type_manager, self._digests, "never"
        ) is not None

    def _auth_repair_hint(self, artifact, message: str) -> Optional[str]:
        """A concrete repair suggestion when `message` looks like a missing-
        credential failure (see `_AUTH_FAILURE_SIGNALS`) - `None` for any
        other failure, since a bad URL or a flaky connection has nothing to
        do with Administration -> Plugins. Names the actual provider when it
        can be resolved from the artifact's own `provider_hint.source`
        (never a hardcoded provider id); falls back to generic wording
        otherwise."""
        if not any(signal in message for signal in _AUTH_FAILURE_SIGNALS):
            return None
        source = (artifact.provider_hint or {}).get("source")
        info = credential_prompt_for_provider(self._get_provider_registry(), source) if source else None
        if info:
            return f"Add your {info['name']} API key in Administration -> Plugins -> {info['name']}, then retry setup."
        return "Add an API key for this download's provider in Administration -> Plugins, then retry setup."

    def _resolve_download_url(self, artifact) -> Tuple[Optional[str], str]:
        return resolve_download_url(artifact, self._get_provider_registry)

    # --- progress polling ----------------------------------------------------

    def _wait_for_completion(
        self, service, download_id: str, artifact, report_progress=None
    ) -> Optional[str]:
        """Block (this executor is synchronous - see `_async_bridge.py`)
        until `download_id` reaches a terminal state, or `downloaded_bytes`
        goes `_STALL_SECONDS` without advancing. Returns an error message on
        failure/stall, `None` on success. There is no wall-clock ceiling on
        top of the stall check - a download that keeps moving, however
        slowly, keeps running; only genuinely stuck progress fails the step.
        `get_download` is a plain sync repository read (see
        `DownloadQueue.get_download`), so no async bridging is needed for
        the poll loop itself, only for the initial `queue_model_download`
        call.

        Each tick also reports interim progress (`downloaded_bytes`/
        `total_bytes` off the same `Download` row - see
        `src/features/downloads/models.py`) via
        `report_progress`, so the attempt's progress fields advance while
        this call is still in flight (`StepContext.report_progress`, wired by
        `executors/registry.py`). The `_POLL_INTERVAL_SECONDS` (2s) cadence
        this loop already runs at is throttling enough on its own - one DB
        write roughly every two seconds, well under the "~1/second" budget -
        so no extra debouncing is needed here.
        """
        report_progress = report_progress or (lambda *a, **k: None)
        last_progress_bytes: Optional[int] = None
        last_progress_at = time.monotonic()
        while True:
            download = service.get_download(download_id)
            status = getattr(download, "status", None)
            status_value = getattr(status, "value", status)
            downloaded_bytes = getattr(download, "downloaded_bytes", None)
            total_bytes = getattr(download, "total_bytes", None)
            if downloaded_bytes is not None or total_bytes is not None:
                report_progress(
                    progress_current=downloaded_bytes,
                    progress_total=total_bytes,
                    progress_unit="bytes",
                )
            if status_value == "completed":
                return None
            if status_value in ("failed", "cancelled"):
                error = getattr(download, "error_message", None)
                name = artifact.display_name or artifact.filename
                return f"Downloading '{name}' {status_value}" + (f": {error}" if error else ".")

            now = time.monotonic()
            if downloaded_bytes is not None and (
                last_progress_bytes is None or downloaded_bytes > last_progress_bytes
            ):
                last_progress_bytes = downloaded_bytes
                last_progress_at = now
            elif now - last_progress_at >= _STALL_SECONDS:
                name = artifact.display_name or artifact.filename
                minutes = _STALL_SECONDS // 60
                return (
                    f"Downloading '{name}' made no progress for {minutes} minutes "
                    f"(last status: {status_value}) - the connection may have stalled."
                )

            time.sleep(_POLL_INTERVAL_SECONDS)
