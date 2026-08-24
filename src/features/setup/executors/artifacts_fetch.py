"""`artifacts.fetch` - download whatever `artifacts.plan` found missing.

By the time this step ever runs, consent has structurally already happened:
the run cannot leave `awaiting_consent` (where `artifacts.plan` parks it) and
reach this later step without `SetupRunManager.grant_consent` having been
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

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.features.models.jobs import TYPE_DIR_MAP
from src.features.models.repository import ModelRepository
from src.features.setup.executors._async_bridge import run_sync
from src.features.setup.executors._provider_credentials import (
    credential_prompt_for_provider,
    resolve_provider_registry,
)
from src.features.setup.executors.base import StepContext, StepResult

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


class ArtifactsFetchExecutor:
    def __init__(
        self,
        download_manager,
        model_repository: ModelRepository,
        provider_registry_factory=None,
    ):
        self.download_manager = download_manager
        self.model_repository = model_repository
        # Lazy/optional: resolving the provider registry needs async
        # discovery/init this constructor shouldn't force (see
        # `src.features.providers.registry.ensure_providers_discovered`).
        # Injectable for tests; defaults to the real module-level registry.
        self._provider_registry_factory = provider_registry_factory

    def execute(self, context: StepContext) -> StepResult:
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
            if self.model_repository.get_by_identity(artifact.model_type, artifact.filename) is None:
                to_fetch.append(artifact)

        if not to_fetch:
            return StepResult.ok({"fetched": [], "message": "Nothing left to download."})

        service = self.download_manager
        if service is None:
            return StepResult.fail(
                "NO_DOWNLOAD_MANAGER",
                "The download queue is not wired into this instance, so this file can't be fetched automatically.",
            )

        models_dir = self._models_dir()
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

            destination_dir = str(Path(models_dir) / TYPE_DIR_MAP.get(artifact.model_type, "checkpoints"))
            checksum = artifact.checksum.value if artifact.checksum else None
            provider_id = (artifact.provider_hint or {}).get("source")

            try:
                download = run_sync(
                    service.queue_model_download(
                        url=url,
                        destination_dir=destination_dir,
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
                    "display_name": artifact.display_name or artifact.filename,
                    "download_id": download.id,
                    "source": note,
                }
            )

        return StepResult.ok({"fetched": fetched})

    def _get_provider_registry(self):
        return resolve_provider_registry(self._provider_registry_factory)

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
        provider_hint = artifact.provider_hint or {}

        explicit_url = provider_hint.get("download_url")
        if explicit_url:
            return explicit_url, "the recipe's own source"

        registry = self._get_provider_registry()
        if registry is None:
            return None, "no marketplace provider plugin is available to look up a source"

        checksum = artifact.checksum.value if artifact.checksum else None
        if checksum:
            try:
                info = run_sync(registry.get_model_by_hash_any(checksum))
            except Exception:
                info = None
            if info and info.download_url:
                return info.download_url, f"{info.provider_id} (matched by checksum)"

        source = provider_hint.get("source")
        model_id = provider_hint.get("model_id")
        if source and model_id:
            try:
                url = run_sync(registry.get_download_url(source, model_id, provider_hint.get("version_id")))
            except Exception:
                url = None
            if url:
                return url, f"{source} (matched by id)"

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

    def _models_dir(self) -> str:
        from src.platform.settings.repository import SettingRepository

        setting_repo = SettingRepository()
        model_dir_setting = setting_repo.get_setting_by_key("models_dir")
        return str(model_dir_setting.get_typed_value()) if model_dir_setting else "models"

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
        `DownloadManager.get_download`), so no async bridging is needed for
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
