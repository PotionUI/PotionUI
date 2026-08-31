"""The `native.remote` driver: dispatches a generation's whole pipeline to a
headless Remote Native worker (`src/features/remote_execution/worker/`) over
HTTP/SSE instead of running it in this process.

Unlike `NativeBackend`/`ComfyUIBackend` (both `InProcessBackend`s that hand
pipes to a local `PipelineExecutor`), this backend never touches a
`PipelineExecutor` at all - the whole point is that pipe execution happens on
the worker's hardware, not this host's. It extends `BaseBackend` directly and
owns its own async dispatch loop.

Collaborators this needs beyond `backend_config` (a `PipeCatalog` and the
`PluginRegistry`, to compute the fingerprints the worker handshake is checked
against) are late-bound via `bind_remote_context` rather than constructor
injection, because `BackendRegistry._create_backend_instance` builds every
backend the same uniform way (`backend_class(backend_config=config)`) - see
that method for the duck-typed call.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from src.features.backends.base_backend import BaseBackend
from src.features.generation.input_assets import collect_input_assets
from src.features.generation.package_assembly import (
    assemble_execution_package,
    build_processed_pipeline,
)
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.remote_execution.artifact_import import (
    output_for_artifact,
    resolve_import_destination,
)
from src.features.remote_execution.model_bundle_builder import (
    ModelBundleResolutionError,
    build_model_bundle,
)
from src.features.remote_execution.model_bundle_staging import find_unstaged_entries
from src.features.remote_execution.policy import RemoteExecutionPolicy
from src.features.remote_execution.records import (
    IllegalStateTransition,
    RemoteExecution,
    RemoteExecutionState,
)
from src.features.remote_execution.repository import RemoteExecutionRepository
from src.features.remote_execution.transport import (
    WorkerProtocolError,
    WorkerTransport,
    WorkerTransportError,
    WorkerUnreachableError,
)
from src.pipelines.contracts import resolve_display_title
from src.pipelines.outputs import (
    ErrorGenerationOutput,
    GenerationOutput,
    Progress,
    ProgressGenerationOutput,
)
from src.pipelines.remote_fingerprint import (
    compute_build_fingerprint,
    compute_pipe_catalog_fingerprint,
    compute_remote_plugin_bundle_fingerprint,
)
from src.platform.observability.logger import logger
from src.platform.util.ids import generate_ulid
from src.platform.worker_protocol import (
    FingerprintMismatchV1,
    JobEventV1,
    WorkerInfoV1,
)

#: A closed SSE connection before a terminal event is a dropped connection,
#: not a finished execution (see `_consume_events`) - bounded so a worker
#: that genuinely never terminates an execution still fails the dispatch
#: rather than retrying forever.
_MAX_EVENT_STREAM_RECONNECTS = 3
_EVENT_STREAM_RECONNECT_DELAY_SECONDS = 0.05


class RemoteNativeBackend(BaseBackend):
    """A configured Remote Native worker."""

    def __init__(self, backend_config, *, transport_override=None):
        super().__init__(backend_config)
        self._active: Set[str] = set()
        self._repository = RemoteExecutionRepository()
        self._policy = RemoteExecutionPolicy()
        self._pipe_catalog = None
        self._plugin_registry = None
        self._fingerprints: Dict[str, str] = {}
        self._owner = f"native-remote-backend:{backend_config.id}:{os.getpid()}"
        # Test-only seam: an httpx.AsyncBaseTransport (e.g. httpx.ASGITransport
        # pointed at a real worker app) instead of a real socket. Never set in
        # production, where WorkerTransport's own default (a real connection)
        # is what's wanted.
        self._transport_override = transport_override

    # -- late-bound collaborators -----------------------------------------

    def bind_remote_context(self, *, pipe_catalog, plugin_registry) -> None:
        """Give this backend the `PipeCatalog`/`PluginRegistry` it needs to
        compute its own compatibility fingerprints. Called once, right after
        construction, by `BackendRegistry._create_backend_instance`.

        Fingerprints are computed here and cached rather than per-dispatch:
        `compute_pipe_catalog_fingerprint` forces eager pipe discovery the
        first time it runs (see its docstring) - paying that cost once at
        bind time, not on every generation, is the whole reason the upstream
        function documents caching as the expected usage.
        """
        self._pipe_catalog = pipe_catalog
        self._plugin_registry = plugin_registry
        enabled_plugins = plugin_registry.get_enabled_plugins() if plugin_registry else ()
        self._fingerprints = {
            "pipe_catalog": compute_pipe_catalog_fingerprint(pipe_catalog),
            "plugin_bundle": compute_remote_plugin_bundle_fingerprint(pipe_catalog, enabled_plugins),
            "build": compute_build_fingerprint(os.getenv("POTIONUI_BUILD_ID") or None),
        }

    def supports_model_listing(self) -> bool:
        return True

    async def list_models(self):
        """The worker depot's own listing, not the host's - mirrors how
        ComfyUIBackend reports its own server's models."""
        from src.platform.filesystem.model_types import DIRECTORY_TO_MODEL_TYPE

        from .model_listing import BackendModel, deduplicate

        entries = await self._transport().list_models()
        models = [
            BackendModel(
                model_type=DIRECTORY_TO_MODEL_TYPE.get(
                    entry["relative_path"].split("/", 1)[0], entry["relative_path"].split("/", 1)[0],
                ),
                filename=entry["relative_path"].rsplit("/", 1)[-1],
                ref=entry["relative_path"],
                size=entry.get("size_bytes"),
                sha256=entry.get("digest"),
            )
            for entry in entries
        ]
        return deduplicate(models)

    def prepare_pipes(self, pipes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """No-op, deliberately. Unlike `NativeBackend.prepare_pipes`, this
        backend must NOT inject device/dtype/vram_limit_gb into pipe configs -
        that decision belongs to whichever worker actually executes the
        pipeline (`device_injection.py`, worker-side). Injecting it here would
        defeat the reason `native.remote` exists: a package that already
        pins a device could not run on a worker with different hardware.
        """
        return pipes

    def _transport(self) -> WorkerTransport:
        return WorkerTransport(
            self.config.base_url, self.config.worker_token,
            connect_timeout=self.config.connect_timeout_seconds,
            request_timeout=self.config.request_timeout_seconds,
            transport=self._transport_override,
        )

    # -- health / info ------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        try:
            info = await self._transport().handshake()
        except WorkerUnreachableError as exc:
            return {"status": "offline", "engine": "native", "error": str(exc)}
        except WorkerProtocolError as exc:
            return {"status": "error", "engine": "native", "error": str(exc)}

        health: Dict[str, Any] = {
            "status": "healthy",
            "engine": "native",
            "worker_id": info.worker_id,
            "provider": info.provider,
            "active_generations": len(self._active),
        }
        mismatch = self._fingerprint_mismatch(info)
        if mismatch is not None:
            health["status"] = "degraded"
            health["reason"] = (
                f"Worker '{info.worker_id}' reports a different {mismatch.domain} than this "
                "installation. Every generation dispatched here will be rejected until both "
                "sides run the same build."
            )
        catalog_note = self._catalog_mismatch_note(info)
        if catalog_note is not None:
            health["catalog_note"] = catalog_note
        return health

    async def get_system_info(self) -> Dict[str, Any]:
        try:
            info = await self._transport().handshake()
        except WorkerTransportError as exc:
            return {"engine": "native", "connected": False, "error": str(exc)}

        caps = info.capabilities
        return {
            "engine": "native",
            "connected": True,
            "worker_id": info.worker_id,
            "provider": info.provider,
            "gpus": [g.model_dump(mode="json") for g in caps.gpus],
            "cpu_count": caps.cpu_count,
            "total_memory_bytes": caps.total_memory_bytes,
            "torch_version": caps.torch_version,
            "cuda_version": caps.cuda_version,
        }

    def _fingerprint_mismatch(self, info: WorkerInfoV1) -> Optional[FingerprintMismatchV1]:
        """Pre-gate on ``build`` only - pipe_catalog/plugin_bundle are gated
        per-package by the worker now (WorkerCoordinator._fingerprint_mismatch)."""
        expected = self._fingerprints.get("build")
        actual = info.fingerprints.get("build")
        if expected is not None and expected != actual:
            return FingerprintMismatchV1(domain="build", expected=expected, actual=actual or "")
        return None

    def _catalog_mismatch_note(self, info: WorkerInfoV1) -> Optional[str]:
        mismatched = [
            domain for domain in ("pipe_catalog", "plugin_bundle")
            if self._fingerprints.get(domain) not in (None, info.fingerprints.get(domain))
        ]
        if not mismatched:
            return None
        return (
            f"Worker '{info.worker_id}'s {'/'.join(mismatched)} differs from this "
            "installation - informational only, dispatch is gated per pipeline."
        )

    # -- cancel ---------------------------------------------------------

    async def cancel_generation(self, generation_id: str) -> bool:
        if generation_id not in self._active:
            return False
        try:
            result = await self._transport().cancel(generation_id)
        except WorkerTransportError as exc:
            logger.error(f"[NATIVE_REMOTE_BACKEND] Failed to cancel {generation_id}: {exc}")
            return False

        if result == "accepted":
            # The state machine only allows a worker's eventual `cancelled`
            # event to land the row from CANCELLING, never straight from
            # RUNNING/STAGING/DISPATCHING (see LEGAL_TRANSITIONS) - without
            # this, apply_job_event would raise IllegalStateTransition the
            # moment that event arrives and the row would be stuck FAILED
            # with a state-machine error instead of CANCELLED.
            row = self._repository.get_by_id(generation_id)
            if row is not None and not row.is_terminal and row.state != RemoteExecutionState.CANCELLING:
                try:
                    self._repository.apply_state(generation_id, RemoteExecutionState.CANCELLING)
                except IllegalStateTransition:
                    # Raced ahead of _dispatch's own claim (e.g. still
                    # PENDING) - the worker's own cancel outcome still
                    # answers the caller; the row catches up via the normal
                    # event stream once dispatch actually claims it.
                    logger.warning(
                        f"[NATIVE_REMOTE_BACKEND] Could not move {generation_id} to CANCELLING "
                        f"from {row.state.value} - cancel requested before dispatch claimed the row"
                    )

        return result in ("accepted", "already_terminal")

    # -- dispatch ---------------------------------------------------------

    async def start_generation(
        self, pipeline_data: Dict[str, Any], emit: Callable[[Optional[GenerationOutput]], None],
    ) -> str:
        if not self.config.is_configured():
            # Belt and braces: disabled/unconfigured backends shouldn't be
            # selectable for dispatch in the first place (see the enable
            # guard in BackendController), but a generation must never be
            # silently swallowed if one is somehow reached anyway.
            raise RuntimeError(
                f"Backend '{self.config.id}' has no worker URL/token configured - "
                "connect or provision it before dispatching generations to it."
            )

        generation_id = pipeline_data.get('generation_id') or generate_ulid()
        pipes = pipeline_data.get('pipes')
        if not pipes:
            raise ValueError("No pipeline configuration provided")
        if self._pipe_catalog is None:
            raise RuntimeError(
                "RemoteNativeBackend has no pipe catalog bound - bind_remote_context "
                "was never called on this instance"
            )

        self._active.add(generation_id)
        asyncio.create_task(
            self._run(generation_id, pipeline_data.get('preset_id'), pipes, emit)
        )
        logger.info(f"[NATIVE_REMOTE_BACKEND] Dispatching generation {generation_id} to {self.config.base_url}")
        return generation_id

    async def _run(
        self, generation_id: str, preset_id: Optional[str], pipes: List[Dict[str, Any]],
        emit: Callable[[Optional[GenerationOutput]], None],
    ) -> None:
        try:
            await self._dispatch(generation_id, preset_id, pipes, emit)
        except Exception as exc:
            logger.error(f"[NATIVE_REMOTE_BACKEND] Generation {generation_id} failed: {exc}")
            emit(ErrorGenerationOutput(error=str(exc)))
        finally:
            self._active.discard(generation_id)
            emit(None)

    async def _dispatch(
        self, generation_id: str, preset_id: Optional[str], pipes: List[Dict[str, Any]],
        emit: Callable[[Optional[GenerationOutput]], None],
    ) -> None:
        from src.platform.settings.settings import Settings
        from src.platform.settings.repository import SettingRepository

        storage_dir = Path(Settings(SettingRepository()).get_file_storage_directory())

        processed = build_processed_pipeline(pipes, self._pipe_catalog)
        _rewritten, _manifest, sources = collect_input_assets(processed.pipes, storage_dir)
        try:
            model_bundle = await asyncio.to_thread(build_model_bundle, processed.pipes)
        except ModelBundleResolutionError as exc:
            # Fails before any row is created or the worker is ever
            # contacted - same as every other resolution error this raises,
            # just with the preset named so the operator knows which preset
            # to fix rather than only which file.
            preset_label = f"preset {preset_id!r}" if preset_id else "this pipeline"
            emit(ErrorGenerationOutput(
                error=f"Cannot dispatch {preset_label} to a remote worker: {exc}",
            ))
            return

        built = BuiltPipeline(
            generation_id=generation_id, preset_id=preset_id, preset_template=None, pipes=pipes,
        )
        package = assemble_execution_package(
            built,
            pipe_catalog=self._pipe_catalog,
            model_bundle=model_bundle,
            engine=self.engine,
            execution_id=generation_id,
            required_fingerprints=self._fingerprints,
            policy=self._policy,
            storage_dir=storage_dir,
        )

        row = self._repository.create(RemoteExecution(
            id=generation_id,
            provider=self.config.driver,
            state=RemoteExecutionState.PENDING,
            idempotency_key=generation_id,
            request_digest=str(package.request_digest),
            generation_id=generation_id,
            backend_id=self.backend_id,
            expires_at_ms=int(package.expires_at.timestamp() * 1000) if package.expires_at else None,
            metadata={'preset_id': preset_id} if preset_id else {},
        ))
        if row.is_terminal:
            # A resubmission of an already-finished execution (idempotency
            # key collision) - nothing left to dispatch.
            return

        transport = self._transport()

        try:
            info = await transport.handshake()
        except WorkerTransportError as exc:
            self._repository.apply_state(
                row.id, RemoteExecutionState.FAILED,
                error_code="worker_unreachable", error_message=str(exc),
            )
            emit(ErrorGenerationOutput(error=f"Remote worker unreachable: {exc}"))
            return

        mismatch = self._fingerprint_mismatch(info)
        if mismatch is not None:
            self._repository.apply_state(
                row.id, RemoteExecutionState.FAILED,
                error_code="fingerprint_mismatch",
                error_message=(
                    f"{mismatch.domain} fingerprint mismatch with worker {info.worker_id} "
                    f"(expected {mismatch.expected}, worker reports {mismatch.actual})"
                ),
            )
            emit(ErrorGenerationOutput(
                error=(
                    f"Remote worker's {mismatch.domain} does not match this installation "
                    "- refusing to dispatch."
                ),
            ))
            return

        # Model sync is admin configuration, never a silent side effect of a
        # user's generation - a generation that references a file the worker
        # doesn't have fails fast, naming what's missing, instead of pushing
        # it. See Admin -> Backends -> <name> -> Models.
        missing = await find_unstaged_entries(model_bundle, transport)
        if missing:
            backend_name = self.config.name or self.config.id
            filenames = ", ".join(Path(entry.relative_path).name for entry in missing)
            error_message = (
                f"Models not present on worker '{backend_name}': {filenames}. "
                f"Sync them in Admin → Backends → {backend_name} → Models."
            )
            self._repository.apply_state(
                row.id, RemoteExecutionState.FAILED,
                error_code="models_not_staged", error_message=error_message,
            )
            emit(ErrorGenerationOutput(error=error_message))
            return

        claimed = self._repository.claim_specific(row.id, self._owner, self._policy.lease_seconds)
        if claimed is None:
            # Already claimed by a concurrent call (duplicate submit racing
            # in) - nothing left for this call to do.
            return

        renew_task = asyncio.create_task(self._renew_lease_loop(row.id, claimed.lease_epoch))
        try:
            try:
                await transport.submit(package)

                if package.input_assets is not None:
                    for asset in package.input_assets.assets:
                        source_path = sources.get(asset.logical_id)
                        if source_path is None:
                            raise RuntimeError(
                                f"no local source path recorded for input asset {asset.logical_id!r}"
                            )
                        await transport.upload_asset(row.id, asset.logical_id, source_path)

                await self._consume_events(row.id, package, transport, storage_dir, emit)
            except Exception as exc:
                # Anything that goes wrong between claiming the row and a
                # terminal worker event (submit failing, an upload failing,
                # the SSE connection dying mid-stream) must still terminate
                # the row - otherwise it is stuck DISPATCHING/STAGING/RUNNING
                # forever, since nothing else in this single-slot MVP retries
                # it. A no-op when the event loop already reached a terminal
                # state (e.g. the failure happened on cleanup, after
                # `succeeded` was already applied).
                current = self._repository.get_by_id(row.id)
                if current is not None and not current.is_terminal:
                    self._repository.apply_state(
                        row.id, RemoteExecutionState.FAILED,
                        error_code="dispatch_error", error_message=str(exc),
                    )
                raise
        finally:
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass

    async def _renew_lease_loop(self, execution_id: str, epoch: int) -> None:
        interval = max(1.0, self._policy.lease_seconds / 2)
        while True:
            await asyncio.sleep(interval)
            renewed = self._repository.renew_lease(
                execution_id, self._owner, epoch, self._policy.lease_seconds,
            )
            if not renewed:
                logger.warning(
                    f"[NATIVE_REMOTE_BACKEND] Lost the lease on {execution_id} - "
                    "another owner must have reclaimed it"
                )
                return

    async def _consume_events(
        self, execution_id: str, package, transport: WorkerTransport, storage_dir: Path,
        emit: Callable[[Optional[GenerationOutput]], None],
    ) -> None:
        pipe_type_by_id = {p.pipe_id: p.pipe_type for p in package.processed_pipes.pipes}
        pipe_index_by_id = {p.pipe_id: idx for idx, p in enumerate(package.processed_pipes.pipes)}
        imports_dir = storage_dir / "remote_imports" / execution_id

        cursor = 0
        for attempt in range(_MAX_EVENT_STREAM_RECONNECTS):
            async for event in transport.stream_events(execution_id, after=cursor):
                cursor = event.cursor
                self._repository.apply_job_event(execution_id, event)
                await self._handle_event(
                    event, transport, imports_dir, pipe_type_by_id, pipe_index_by_id, emit,
                )
                if event.is_terminal:
                    return
            # The stream closed (the worker's response ended) without ever
            # delivering a terminal event - a dropped connection, not
            # necessarily a finished execution. Reconnect from the last
            # cursor we actually saw rather than treating a closed stream as
            # a completed one; the worker still has the full history in its
            # journal regardless of why this particular connection ended.
            if attempt < _MAX_EVENT_STREAM_RECONNECTS - 1:
                await asyncio.sleep(_EVENT_STREAM_RECONNECT_DELAY_SECONDS)

        raise WorkerProtocolError(
            f"event stream for {execution_id!r} closed without a terminal event after "
            f"{_MAX_EVENT_STREAM_RECONNECTS} attempts"
        )

    async def _handle_event(
        self, event: JobEventV1, transport: WorkerTransport, imports_dir: Path,
        pipe_type_by_id: Dict[str, str], pipe_index_by_id: Dict[str, int],
        emit: Callable[[Optional[GenerationOutput]], None],
    ) -> None:
        pipe_type = pipe_type_by_id.get(event.pipe_id) if event.pipe_id else None

        for artifact in event.artifacts:
            # An artifact that fails integrity verification (or can't be
            # fetched at all) must fail the generation, not be silently
            # dropped - a generation that reports success without one of its
            # declared outputs is a worse failure mode than an honest error.
            # Left to propagate out of _consume_events, where _dispatch's
            # catch-all marks the row FAILED.
            dest = resolve_import_destination(imports_dir, artifact)
            await transport.download_artifact(artifact, dest)
            output = output_for_artifact(
                artifact, dest,
                pipe_index=pipe_index_by_id.get(event.pipe_id) if event.pipe_id else None,
                pipe_type=pipe_type,
            )
            if output is not None:
                emit(output)
            else:
                logger.warning(f"[NATIVE_REMOTE_BACKEND] No local handler for artifact kind {artifact.kind!r}")

        if event.kind in ("staging", "running", "pipe_started", "pipe_progress"):
            progress = None
            if event.progress is not None:
                progress = Progress(current=int(round(event.progress * 100)), max=100)
            emit(ProgressGenerationOutput(
                state=event.kind,
                title=f"<<PIPE:{resolve_display_title(pipe_type)}>>" if pipe_type else None,
                progress=progress,
                pipe_name=pipe_type,
            ))
            return

        if event.kind in ("failed", "rejected") and event.error is not None:
            emit(ErrorGenerationOutput(error=event.error.message, detail=event.error.detail))
