import asyncio
import inspect
import mimetypes
import random
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from PIL import Image

from src.features.cloud.capability_rules import params_for_task
from src.features.cloud.clock import Clock
from src.features.cloud.contracts import (
    TERMINAL_STATES,
    CloudArtifact,
    CloudError,
    CloudJob,
    CloudModelSpec,
    CloudProvider,
    CloudRequest,
    CloudResult,
    CloudStatus,
    LocalMedia,
)
from src.features.cloud.redaction import scrub_text, secret_fragments
from src.features.cloud.repository import CloudCatalogRepository
from src.pipelines.cloud import (
    CloudRunArtifact,
    CloudRunCancelled,
    CloudRunCost,
    CloudRunError,
    CloudRunOutcome,
    CloudRunProgress,
    CloudRunRequest,
    ProgressCallback,
)
from src.platform.observability.logger import logger

CANCEL_CHECK_SECONDS = 0.5
INFLIGHT_SETTLE_SECONDS = 2.0
PROVIDER_CANCEL_WAIT_SECONDS = 1.0
PROVIDER_CANCEL_CAP_SECONDS = 5.0
CANCEL_SETTLE_SECONDS = 3.0
FIRST_POLL_SECONDS = 2.0
MAX_POLL_SECONDS = 30.0
POLL_GROWTH = 1.5
SUBMIT_ATTEMPTS = 3
CONSECUTIVE_ERROR_LIMIT = 5
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0
RETRYABLE_KINDS = frozenset({"rate_limited", "unavailable", "timeout"})
MEGABYTE = 1024 * 1024
MAX_ARTIFACT_BYTES = {"image": 64 * MEGABYTE, "video": 2048 * MEGABYTE, "audio": 256 * MEGABYTE}
FALLBACK_EXTENSIONS = {"image": ".png", "video": ".mp4", "audio": ".mp3"}
RATE_LIMIT_MESSAGE = "Waiting for provider rate limit"
UNCONFIRMED_CANCEL_MESSAGE = "Stopped waiting. The provider may still finish this job and bill it."


def _secret_values(provider: CloudProvider) -> tuple[str, ...]:
    values: list[str] = []
    try:
        values.extend(str(value) for value in type(provider).auth_headers(provider.config).values())
    except Exception:
        pass
    for name, field in type(provider.config).model_fields.items():
        extra = field.json_schema_extra
        if isinstance(extra, Mapping) and extra.get("secret"):
            value = getattr(provider.config, name, None)
            if value:
                values.append(str(value))
    return secret_fragments(values)


class CloudRunSession:
    def __init__(
        self,
        *,
        provider: CloudProvider,
        catalog: CloudCatalogRepository,
        backend_id: str,
        generation_id: str,
        timeout_seconds: float,
        clock: Clock,
        scratch_dir: Path,
        rng: Optional[random.Random] = None,
    ) -> None:
        self._provider = provider
        self._catalog = catalog
        self._backend_id = backend_id
        self._generation_id = generation_id
        self._timeout_seconds = float(timeout_seconds)
        self._clock = clock
        self._scratch = Path(scratch_dir)
        self._rng = rng or random.Random()
        self._secrets = _secret_values(provider)
        self._cancel_event: Optional[asyncio.Event] = None
        self._cancel_requested = False
        self._job: Optional[CloudJob] = None
        self._provider_cancel: Optional[asyncio.Task] = None
        self._started = 0.0
        self._deadline = 0.0
        self._cancel_confirmed: Optional[bool] = None
        self._run_number = 0
        self._run_dir = self._scratch
        self._orphan_risk = False
        self._cancel_notice: Optional[str] = None
        self._active = False
        self._settled: Optional[asyncio.Event] = None
        self._is_cancelled: Optional[Callable[[], bool]] = None
        self._on_progress: Optional[ProgressCallback] = None

    def __repr__(self) -> str:
        return f"CloudRunSession(backend={self._backend_id!r}, generation={self._generation_id!r})"

    def _event(self) -> asyncio.Event:
        if self._cancel_event is None:
            self._cancel_event = asyncio.Event()
        return self._cancel_event

    def _settled_event(self) -> asyncio.Event:
        if self._settled is None:
            self._settled = asyncio.Event()
        return self._settled

    async def cancel(self) -> None:
        self._cancel_requested = True
        self._event().set()

    async def settled_cancel_notice(self) -> Optional[str]:
        if self._active:
            try:
                await asyncio.wait_for(self._settled_event().wait(), CANCEL_SETTLE_SECONDS)
            except asyncio.TimeoutError:
                pass
        return self._cancel_notice

    def cleanup(self) -> None:
        shutil.rmtree(self._run_dir, ignore_errors=True)

    async def run(
        self,
        request: CloudRunRequest,
        *,
        on_progress: Optional[ProgressCallback] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> CloudRunOutcome:
        self._is_cancelled = is_cancelled
        self._on_progress = on_progress
        self._active = True
        self._settled_event().clear()
        try:
            return await self._run(request)
        except (CloudRunError, CloudRunCancelled):
            self.cleanup()
            raise
        except asyncio.CancelledError:
            self.cleanup()
            raise
        except Exception as error:
            self.cleanup()
            raise self._wrap_unexpected(error) from None
        finally:
            self._active = False
            self._settled_event().set()

    async def _run(self, request: CloudRunRequest) -> CloudRunOutcome:
        spec = self._spec_for(request.model)
        self._run_number += 1
        self._run_dir = self._scratch / f"run-{self._run_number}"
        self._orphan_risk = False
        self._job = None
        self._cancel_confirmed = None
        self._cancel_notice = None
        cloud_inputs = self._local_inputs(spec, request)
        self._started = self._clock.now()
        limit = self._timeout_seconds
        if spec.max_seconds:
            limit = min(limit, float(spec.max_seconds))
        self._deadline = self._started + limit

        remaining_count = max(1, request.count)
        per_job = max(1, spec.max_outputs_per_job)
        total_jobs = -(-remaining_count // per_job)
        artifacts: list[CloudRunArtifact] = []
        cost_total = Decimal("0")
        cost_source: Optional[str] = None
        seed_used: Optional[int] = None
        provider_job_id: Optional[str] = None

        for job_number in range(total_jobs):
            count = min(per_job, remaining_count)
            remaining_count -= count
            seed = request.seed + (request.count - count - remaining_count) if request.seed is not None else None
            cloud_request = CloudRequest(
                model=spec,
                task=request.task,
                prompt=request.prompt,
                count=count,
                negative_prompt=request.negative_prompt,
                seed=seed,
                params=self._supported_params(spec, request),
                inputs=cloud_inputs,
                client_reference=self._generation_id,
                idempotency_key=f"{self._generation_id}:{self._run_number}:{job_number}",
                user_ref=request.user_ref,
            )
            result = await self._run_job(cloud_request, job_number, total_jobs)
            fetched = await self._fetch_all(result, job_number, total_jobs, len(artifacts))
            artifacts.extend(fetched)
            if result.cost is not None:
                cost_total += result.cost.amount_usd
                cost_source = "provider" if cost_source in (None, "provider") and result.cost.source == "provider" else "estimate"
            if seed_used is None:
                seed_used = result.seed_used
            provider_job_id = result.provider_job_id or provider_job_id

        return CloudRunOutcome(
            artifacts=tuple(artifacts),
            cost=CloudRunCost(amount_usd=cost_total, source=cost_source) if cost_source else None,
            seed_used=seed_used if seed_used is not None else request.seed,
            provider_job_id=provider_job_id,
        )

    def _spec_for(self, slug: str) -> CloudModelSpec:
        entries = self._catalog.get_many(self._backend_id, [slug])
        if not entries:
            raise CloudRunError("invalid_request", "This model is not offered by the selected backend.")
        return entries[0].spec

    @staticmethod
    def _supported_params(spec: CloudModelSpec, request: CloudRunRequest) -> dict[str, Any]:
        offered = {param.name for param in params_for_task(spec, request.task)}
        return {
            name: value
            for name, value in request.params.items()
            if name in offered and value is not None and value != ""
        }

    def _local_inputs(self, spec: CloudModelSpec, request: CloudRunRequest) -> dict[str, list[LocalMedia]]:
        limits = {item.role: item for item in spec.inputs}
        inputs: dict[str, list[LocalMedia]] = {}
        for role, paths in request.inputs.items():
            media_spec = limits.get(role)
            if media_spec is not None and len(paths) > media_spec.max_items:
                raise CloudRunError("invalid_request", "Too many input files were supplied for this model.")
            for path in paths:
                path = Path(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    raise CloudRunError("invalid_request", "An input file could not be read.") from None
                if media_spec is not None and media_spec.max_bytes is not None and size > media_spec.max_bytes:
                    raise CloudRunError("invalid_request", "An input file is larger than this model accepts.")
                media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                inputs.setdefault(role, []).append(LocalMedia(path=path, media_type=media_type, size=size))
        return inputs

    def _wrap_unexpected(self, error: Exception) -> CloudRunError:
        if isinstance(error, asyncio.TimeoutError):
            return CloudRunError("timeout", "The provider took too long to answer.")
        logger.warning(f"[CLOUD_RUN] {self._generation_id} failed: {type(error).__name__}")
        return CloudRunError(
            "failed",
            "The provider could not complete the request.",
            detail=scrub_text(f"{type(error).__name__}: {error}", self._secrets)[:500],
        )

    def _clean(self, error: CloudRunError) -> CloudError:
        return CloudError(
            error.kind,
            error.user_message,
            detail=scrub_text(error.detail or "", self._secrets),
            retry_after_s=error.retry_after_s,
            request_sent=getattr(error, "request_sent", True),
        )

    def _elapsed(self) -> float:
        return max(0.0, self._clock.now() - self._started)

    async def _emit(self, state: str, **fields: Any) -> None:
        if self._on_progress is None:
            return
        progress = CloudRunProgress(state=state, elapsed_s=self._elapsed(), **fields)
        outcome = self._on_progress(progress)
        if inspect.isawaitable(outcome):
            await outcome

    def _cancelled(self) -> bool:
        if self._cancel_requested:
            return True
        return bool(self._is_cancelled is not None and self._is_cancelled())

    async def _pause(self, seconds: float) -> None:
        remaining = max(0.0, seconds)
        while remaining > 0:
            if self._cancelled():
                return
            step = min(remaining, CANCEL_CHECK_SECONDS)
            sleeper = asyncio.ensure_future(self._clock.sleep(step))
            waiter = asyncio.ensure_future(self._event().wait())
            try:
                await asyncio.wait({sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (sleeper, waiter):
                    if not task.done():
                        task.cancel()
            remaining -= step

    async def _checkpoint(self) -> None:
        if self._cancelled():
            await self._abort_job()
            raise CloudRunCancelled(cancel_confirmed=self._cancel_confirmed)
        if self._clock.now() >= self._deadline:
            await self._abort_job()
            raise CloudRunError("timeout", "The provider did not finish in time.")

    async def _abort_job(self) -> None:
        job, self._job = self._job, None
        if job is None:
            if self._orphan_risk:
                self._cancel_confirmed = False
                await self._warn_unconfirmed()
            return
        if not self._provider.supports_cancel:
            self._cancel_confirmed = False
            await self._warn_unconfirmed()
            return

        async def ask() -> bool:
            try:
                return bool(await asyncio.wait_for(self._provider.cancel(job), PROVIDER_CANCEL_CAP_SECONDS))
            except Exception:
                return False

        task = asyncio.ensure_future(ask())
        self._provider_cancel = task
        done, _ = await asyncio.wait({task}, timeout=PROVIDER_CANCEL_WAIT_SECONDS)
        confirmed = task.result() if task in done else None
        self._cancel_confirmed = confirmed
        if confirmed is not True:
            await self._warn_unconfirmed()

    async def _warn_unconfirmed(self) -> None:
        self._cancel_notice = UNCONFIRMED_CANCEL_MESSAGE
        await self._emit("running", message=UNCONFIRMED_CANCEL_MESSAGE)

    async def _retry_delay(self, error: CloudRunError, attempt: int) -> None:
        if error.kind == "rate_limited":
            wait = error.retry_after_s if error.retry_after_s is not None else min(BACKOFF_BASE_SECONDS * 2 ** attempt, BACKOFF_CAP_SECONDS)
            self._provider.http.pause(wait)
            await self._emit("queued", message=RATE_LIMIT_MESSAGE)
        else:
            base = error.retry_after_s if error.retry_after_s is not None else min(BACKOFF_BASE_SECONDS * 2 ** attempt, BACKOFF_CAP_SECONDS)
            wait = base + self._rng.uniform(0, base * 0.25)
        await self._pause(wait)

    async def _call(self, operation: Callable[[], Any], *, submitting: bool = False) -> Any:
        task = asyncio.ensure_future(operation())
        waiter = asyncio.ensure_future(self._event().wait())
        try:
            while True:
                left = self._deadline - self._clock.now()
                done, _ = await asyncio.wait(
                    {task, waiter},
                    timeout=max(0.0, min(CANCEL_CHECK_SECONDS, left)),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if task in done:
                    try:
                        return task.result()
                    except CloudRunError as error:
                        raise self._clean(error) from None
                if self._cancelled() or self._clock.now() >= self._deadline:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    if submitting:
                        self._orphan_risk = True
                    await self._checkpoint()
        finally:
            waiter.cancel()
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait({task}, timeout=INFLIGHT_SETTLE_SECONDS)
                except asyncio.CancelledError:
                    pass

    def _safe_to_resubmit(self, error: CloudRunError) -> bool:
        if error.kind == "rate_limited":
            return True
        if error.kind == "unavailable":
            return self._provider.idempotent_submit or not getattr(error, "request_sent", True)
        return False

    async def _submit(self, request: CloudRequest) -> CloudJob:
        attempt = 0
        while True:
            await self._checkpoint()
            try:
                return await self._call(lambda: self._provider.submit(request), submitting=True)
            except CloudRunError as error:
                attempt += 1
                if attempt >= SUBMIT_ATTEMPTS or not self._safe_to_resubmit(error):
                    raise
                await self._retry_delay(error, attempt)

    async def _run_job(self, request: CloudRequest, job_number: int, total_jobs: int) -> CloudResult:
        await self._emit("queued", message=None)
        job = await self._submit(request)
        self._job = job
        if job.result is not None:
            self._job = None
            return job.result

        delay = job.poll_after_s or FIRST_POLL_SECONDS
        errors = 0
        while True:
            await self._pause(min(delay, max(0.0, self._deadline - self._clock.now())))
            await self._checkpoint()
            try:
                status: CloudStatus = await self._call(lambda: self._provider.poll(job))
            except CloudRunError as error:
                errors += 1
                if error.kind not in RETRYABLE_KINDS or errors >= CONSECUTIVE_ERROR_LIMIT:
                    raise
                await self._retry_delay(error, errors)
                delay = 0.0
                continue
            errors = 0
            if status.state in TERMINAL_STATES:
                self._job = None
                return self._terminal(status)
            fraction = None
            if status.progress is not None:
                fraction = (job_number + min(max(status.progress, 0.0), 1.0)) / total_jobs
            await self._emit(
                "queued" if status.state == "queued" else "running",
                fraction=fraction,
                queue_position=status.queue_position,
                message=status.message,
            )
            delay = status.poll_after_s or min(max(delay, FIRST_POLL_SECONDS) * POLL_GROWTH, MAX_POLL_SECONDS)

    @staticmethod
    def _terminal(status: CloudStatus) -> CloudResult:
        if status.state == "succeeded" and status.result is not None:
            return status.result
        if status.state == "expired":
            raise CloudRunError("expired", "The result expired before it could be downloaded.", detail=status.message or "")
        if status.state == "cancelled":
            raise CloudRunError("failed", "The provider cancelled this job.", detail=status.message or "")
        raise CloudRunError("failed", "The provider could not complete the request.", detail=status.message or "")

    async def _fetch_all(self, result: CloudResult, job_number: int, total_jobs: int, offset: int) -> list[CloudRunArtifact]:
        fetched: list[CloudRunArtifact] = []
        self._run_dir.mkdir(parents=True, exist_ok=True)
        for position, artifact in enumerate(sorted(result.artifacts, key=lambda item: item.index)):
            await self._checkpoint()
            await self._emit("fetching", fraction=(job_number + position / max(1, len(result.artifacts))) / total_jobs)
            dest = self._run_dir / f"{offset + position}{self._extension(artifact)}"
            path = await self._fetch_one(artifact, dest)
            self._validate(artifact, path)
            fetched.append(CloudRunArtifact(
                modality=artifact.modality,
                index=offset + position,
                path=path,
                media_type=artifact.media_type,
            ))
        return fetched

    @staticmethod
    def _extension(artifact: CloudArtifact) -> str:
        guessed = mimetypes.guess_extension(artifact.media_type or "") if artifact.media_type else None
        return guessed or FALLBACK_EXTENSIONS[artifact.modality]

    async def _fetch_one(self, artifact: CloudArtifact, dest: Path) -> Path:
        errors = 0
        while True:
            try:
                return await self._call(
                    lambda: self._provider.fetch(artifact, dest, max_bytes=MAX_ARTIFACT_BYTES[artifact.modality])
                )
            except CloudRunError as error:
                errors += 1
                if error.kind not in RETRYABLE_KINDS or errors >= CONSECUTIVE_ERROR_LIMIT:
                    raise
                await self._retry_delay(error, errors)
                await self._checkpoint()

    @staticmethod
    def _validate(artifact: CloudArtifact, path: Path) -> None:
        unreadable = CloudRunError("failed", "The provider returned a file that could not be used.")
        try:
            if not path.is_file() or path.stat().st_size == 0:
                raise unreadable
            if artifact.modality == "image":
                with Image.open(path) as image:
                    image.verify()
        except CloudRunError:
            raise
        except Exception:
            raise unreadable from None
