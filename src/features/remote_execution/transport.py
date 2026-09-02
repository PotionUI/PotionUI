"""Core's HTTP client for one Remote Native worker.

Talks the exact route surface `src.features.remote_execution.worker.routes`
serves: handshake, submit, asset upload, the SSE event stream, cancel, and
artifact download. Every call is short-lived (`httpx.AsyncClient` per call,
mirroring `ComfyUIBackend`'s per-call `aiohttp.ClientSession` rather than a
long-lived client) except `stream_events`, which holds one connection open for
as long as the caller consumes the generator.

This module knows nothing about `RemoteExecution` rows, dispatch policy, or
artifact import - it is the wire, not the state machine.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import AsyncIterator, List, Optional

import httpx

from src.platform.worker_protocol import (
    ArtifactRefV1,
    ExecutionPackageV1,
    JobEventV1,
    ModelBundleEntryV1,
    ModelBundleManifestV1,
    WorkerInfoV1,
)
from src.platform.worker_protocol.envelope import (
    WorkerEnvelopeError,
    envelope,
    read_envelope,
)
from src.platform.worker_protocol.model_fetch import ModelFetchRequestV1
from src.platform.worker_protocol.model_inventory import ModelInventoryResponseV1

#: Chunk size for streaming a model file to the worker's staging endpoint - a
#: checkpoint is commonly multi-gigabyte, so unlike `upload_asset`'s
#: `handle.read()` (fine for the small input assets it moves), the whole file
#: must never be held in memory at once.
_MODEL_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024


class WorkerTransportError(Exception):
    """Base for every transport-layer failure talking to a worker."""


class WorkerUnreachableError(WorkerTransportError):
    """The worker could not be reached at all (connect/timeout/DNS), or an
    infra gateway answered on the worker's behalf because it isn't running.

    `reason` is `"connect"` (the default, for the httpx-level failures) or
    `"not_running"` (a gateway answered with an HTTP status a stopped or
    still-starting worker would produce, see `_gateway_answered_for_a_down_worker`).
    """

    def __init__(self, message: str, *, reason: str = "connect"):
        self.reason = reason
        super().__init__(message)


class WorkerProtocolError(WorkerTransportError):
    """The worker answered, but not with a document this build can read."""


#: Statuses an infra gateway (load balancer, reverse proxy) commonly answers
#: with when there's no worker process behind it to forward the request to.
_GATEWAY_DOWN_STATUS_CODES = frozenset({404, 502, 503, 504})


def _gateway_answered_for_a_down_worker(resp: httpx.Response) -> bool:
    """True when *resp* looks like an infra gateway's own error page rather
    than something the worker process itself produced. The worker always
    answers with a JSON body (its FastAPI app, including its own 404s) - a
    non-JSON body on one of the gateway-shaped statuses means the request
    never reached it."""
    if resp.status_code not in _GATEWAY_DOWN_STATUS_CODES:
        return False
    content_type = resp.headers.get("content-type", "")
    return "json" not in content_type.split(";", 1)[0].strip().lower()


class ArtifactVerificationError(WorkerTransportError):
    """A downloaded artifact's bytes don't match its `ArtifactRefV1`."""

    def __init__(self, artifact_id: str, reason: str):
        self.artifact_id = artifact_id
        self.reason = reason
        super().__init__(f"artifact {artifact_id!r}: {reason}")


class SubmitResponse:
    """The worker's own (non-enveloped) response body to `POST /v1/executions`."""

    def __init__(self, status_code: int, execution_id: str, outcome: str, detail: Optional[str] = None):
        self.status_code = status_code
        self.execution_id = execution_id
        self.outcome = outcome
        self.detail = detail


class WorkerTransport:
    """One configured worker, reached with a bearer token."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        connect_timeout: float = 10.0,
        request_timeout: float = 60.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = httpx.Timeout(request_timeout, connect=connect_timeout)
        self._connect_timeout = connect_timeout
        # Overridable so a test can point this at an in-process ASGI app
        # (httpx.ASGITransport) instead of a real socket - never None in
        # production use, where the default (a real connection) is what's
        # wanted.
        self._transport = transport

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _client(self, *, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def handshake(self) -> WorkerInfoV1:
        try:
            async with self._client(timeout=self._timeout) as client:
                resp = await client.get(self._url("/v1/worker"), headers=self._headers)
        except httpx.HTTPError as exc:
            raise WorkerUnreachableError(f"could not reach worker at {self._base_url}: {exc}") from exc

        if resp.status_code != 200:
            if _gateway_answered_for_a_down_worker(resp):
                raise WorkerUnreachableError(
                    f"worker is not running (gateway answered HTTP {resp.status_code} for /v1/worker)",
                    reason="not_running",
                )
            raise WorkerProtocolError(f"handshake failed: HTTP {resp.status_code}")
        try:
            info = read_envelope(resp.json())
        except (WorkerEnvelopeError, json.JSONDecodeError) as exc:
            raise WorkerProtocolError(f"handshake returned an unreadable document: {exc}") from exc
        if not isinstance(info, WorkerInfoV1):
            raise WorkerProtocolError(f"handshake returned a {type(info).__name__}, not WorkerInfoV1")
        return info

    async def submit(self, package: ExecutionPackageV1) -> SubmitResponse:
        try:
            async with self._client(timeout=self._timeout) as client:
                resp = await client.post(
                    self._url("/v1/executions"), json=envelope(package), headers=self._headers,
                )
        except httpx.HTTPError as exc:
            raise WorkerUnreachableError(f"could not reach worker at {self._base_url}: {exc}") from exc

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise WorkerProtocolError(f"submit returned an unreadable body: {exc}") from exc

        return SubmitResponse(
            status_code=resp.status_code,
            execution_id=body.get("execution_id", package.execution_id),
            outcome=body.get("outcome", "unknown"),
            detail=body.get("detail"),
        )

    async def upload_asset(self, execution_id: str, logical_id: str, source_path: Path) -> None:
        """Stream ``source_path``'s bytes to the worker as the named input asset."""
        try:
            async with self._client(timeout=httpx.Timeout(None, connect=self._connect_timeout)) as client:
                with source_path.open("rb") as handle:
                    resp = await client.post(
                        self._url(f"/v1/executions/{execution_id}/assets/{logical_id}"),
                        content=handle.read(),
                        headers=self._headers,
                    )
        except (httpx.HTTPError, OSError) as exc:
            raise WorkerUnreachableError(
                f"could not upload asset {logical_id!r} to {self._base_url}: {exc}"
            ) from exc

        if resp.status_code != 200:
            path = f"/v1/executions/{execution_id}/assets/{logical_id}"
            if _gateway_answered_for_a_down_worker(resp):
                raise WorkerUnreachableError(
                    f"worker is not running (gateway answered HTTP {resp.status_code} for {path})",
                    reason="not_running",
                )
            raise WorkerProtocolError(
                f"asset {logical_id!r} upload rejected: HTTP {resp.status_code} {resp.text}"
            )

    async def model_inventory(self, manifest: ModelBundleManifestV1) -> ModelInventoryResponseV1:
        """Ask the worker which of *manifest*'s entries it already has staged."""
        try:
            async with self._client(timeout=self._timeout) as client:
                resp = await client.post(
                    self._url("/v1/models/inventory"), json=envelope(manifest), headers=self._headers,
                )
        except httpx.HTTPError as exc:
            raise WorkerUnreachableError(f"could not reach worker at {self._base_url}: {exc}") from exc

        if resp.status_code != 200:
            if _gateway_answered_for_a_down_worker(resp):
                raise WorkerUnreachableError(
                    f"worker is not running (gateway answered HTTP {resp.status_code} for /v1/models/inventory)",
                    reason="not_running",
                )
            raise WorkerProtocolError(f"model inventory failed: HTTP {resp.status_code} {resp.text}")
        try:
            response = read_envelope(resp.json())
        except (WorkerEnvelopeError, json.JSONDecodeError) as exc:
            raise WorkerProtocolError(f"model inventory returned an unreadable document: {exc}") from exc
        if not isinstance(response, ModelInventoryResponseV1):
            raise WorkerProtocolError(
                f"model inventory returned a {type(response).__name__}, not ModelInventoryResponseV1"
            )
        return response

    async def upload_model(self, bundle_id: str, entry: ModelBundleEntryV1, source_path: Path) -> str:
        """Stream *source_path*'s bytes to the worker as the named model bundle
        entry, in fixed-size chunks (see `_MODEL_UPLOAD_CHUNK_BYTES`). Returns
        the worker-assigned transfer id."""
        try:
            async with self._client(timeout=httpx.Timeout(None, connect=self._connect_timeout)) as client:
                resp = await client.post(
                    self._url(f"/v1/models/{bundle_id}/{entry.logical_id}"),
                    content=_iter_file_chunks(source_path),
                    headers=self._headers,
                )
        except (httpx.HTTPError, OSError) as exc:
            raise WorkerUnreachableError(
                f"could not upload model {entry.logical_id!r} to {self._base_url}: {exc}"
            ) from exc

        if resp.status_code != 200:
            path = f"/v1/models/{bundle_id}/{entry.logical_id}"
            if _gateway_answered_for_a_down_worker(resp):
                raise WorkerUnreachableError(
                    f"worker is not running (gateway answered HTTP {resp.status_code} for {path})",
                    reason="not_running",
                )
            raise WorkerProtocolError(
                f"model {entry.logical_id!r} upload rejected: HTTP {resp.status_code} {resp.text}"
            )
        try:
            return resp.json()["transfer_id"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise WorkerProtocolError(
                f"model {entry.logical_id!r} upload response is missing its transfer_id"
            ) from exc

    async def list_models(self) -> List[dict]:
        """The worker depot's own listing (`GET /v1/models`) - plain JSON,
        not enveloped protocol documents (see `routes.py`'s module docstring)."""
        try:
            async with self._client(timeout=self._timeout) as client:
                resp = await client.get(self._url("/v1/models"), headers=self._headers)
        except httpx.HTTPError as exc:
            raise WorkerUnreachableError(f"could not reach worker at {self._base_url}: {exc}") from exc

        if resp.status_code != 200:
            if _gateway_answered_for_a_down_worker(resp):
                raise WorkerUnreachableError(
                    f"worker is not running (gateway answered HTTP {resp.status_code} for /v1/models)",
                    reason="not_running",
                )
            raise WorkerProtocolError(f"model listing failed: HTTP {resp.status_code} {resp.text}")
        try:
            return resp.json()["entries"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise WorkerProtocolError(f"model listing returned an unreadable body: {exc}") from exc

    async def fetch_model(self, request: ModelFetchRequestV1) -> str:
        """Ask the worker to pull *request.url* straight into its depot.
        Returns the transfer id immediately - the worker runs the download
        itself as its own background task (`POST /v1/models/fetch` -> 202)."""
        try:
            async with self._client(timeout=self._timeout) as client:
                resp = await client.post(
                    self._url("/v1/models/fetch"), json=envelope(request), headers=self._headers,
                )
        except httpx.HTTPError as exc:
            raise WorkerUnreachableError(f"could not reach worker at {self._base_url}: {exc}") from exc

        if resp.status_code != 202:
            if _gateway_answered_for_a_down_worker(resp):
                raise WorkerUnreachableError(
                    f"worker is not running (gateway answered HTTP {resp.status_code} for /v1/models/fetch)",
                    reason="not_running",
                )
            raise WorkerProtocolError(f"model fetch rejected: HTTP {resp.status_code} {resp.text}")
        try:
            return resp.json()["transfer_id"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise WorkerProtocolError("model fetch response is missing its transfer_id") from exc

    async def list_transfers(self) -> List[dict]:
        """The worker's own model-transfer registry (`GET /v1/models/transfers`) -
        the source of truth an admin sync view polls for push/fetch progress."""
        try:
            async with self._client(timeout=self._timeout) as client:
                resp = await client.get(self._url("/v1/models/transfers"), headers=self._headers)
        except httpx.HTTPError as exc:
            raise WorkerUnreachableError(f"could not reach worker at {self._base_url}: {exc}") from exc

        if resp.status_code != 200:
            if _gateway_answered_for_a_down_worker(resp):
                raise WorkerUnreachableError(
                    f"worker is not running (gateway answered HTTP {resp.status_code} for /v1/models/transfers)",
                    reason="not_running",
                )
            raise WorkerProtocolError(f"transfer listing failed: HTTP {resp.status_code} {resp.text}")
        try:
            return resp.json()["transfers"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise WorkerProtocolError(f"transfer listing returned an unreadable body: {exc}") from exc

    async def stream_events(self, execution_id: str, after: int = 0) -> AsyncIterator[JobEventV1]:
        """Yield every `JobEventV1` for ``execution_id`` with cursor > ``after``,
        journaled events first, then live events, until a terminal event closes
        the worker's stream."""
        try:
            async with self._client(timeout=httpx.Timeout(None, connect=self._connect_timeout)) as client:
                async with client.stream(
                    "GET",
                    self._url(f"/v1/executions/{execution_id}/events"),
                    params={"after": after},
                    headers=self._headers,
                ) as resp:
                    if resp.status_code != 200:
                        path = f"/v1/executions/{execution_id}/events"
                        if _gateway_answered_for_a_down_worker(resp):
                            raise WorkerUnreachableError(
                                f"worker is not running (gateway answered HTTP {resp.status_code} for {path})",
                                reason="not_running",
                            )
                        raise WorkerProtocolError(
                            f"event stream for {execution_id!r} failed: HTTP {resp.status_code}"
                        )
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            document = json.loads(line[len("data: "):])
                            event = read_envelope(document)
                        except (WorkerEnvelopeError, json.JSONDecodeError) as exc:
                            raise WorkerProtocolError(f"unreadable event on the wire: {exc}") from exc
                        if not isinstance(event, JobEventV1):
                            raise WorkerProtocolError(f"expected a JobEventV1, got {type(event).__name__}")
                        yield event
        except httpx.HTTPError as exc:
            raise WorkerUnreachableError(f"lost connection to {self._base_url}: {exc}") from exc

    async def cancel(self, execution_id: str) -> str:
        try:
            async with self._client(timeout=self._timeout) as client:
                resp = await client.post(
                    self._url(f"/v1/executions/{execution_id}/cancel"), headers=self._headers,
                )
        except httpx.HTTPError as exc:
            raise WorkerUnreachableError(f"could not reach worker at {self._base_url}: {exc}") from exc
        try:
            return resp.json().get("result", "unknown")
        except json.JSONDecodeError:
            return "unknown"

    async def download_artifact(self, artifact: ArtifactRefV1, dest_path: Path) -> None:
        """Stream ``artifact`` to ``dest_path``, verifying size and sha256 digest
        as bytes arrive. ``dest_path``'s parent must already exist and be a
        directory this process is willing to write into - this method does not
        decide containment, only integrity; the caller resolves the destination
        against a trusted root before calling this."""
        hasher = hashlib.sha256()
        size = 0
        tmp_path = dest_path.with_name(dest_path.name + ".part")

        try:
            async with self._client(timeout=httpx.Timeout(None, connect=self._connect_timeout)) as client:
                async with client.stream(
                    "GET", self._url(artifact.uri), headers=self._headers,
                ) as resp:
                    if resp.status_code != 200:
                        if _gateway_answered_for_a_down_worker(resp):
                            raise WorkerUnreachableError(
                                f"worker is not running (gateway answered HTTP {resp.status_code} for {artifact.uri})",
                                reason="not_running",
                            )
                        raise WorkerProtocolError(
                            f"artifact {artifact.artifact_id!r} download failed: HTTP {resp.status_code}"
                        )
                    with tmp_path.open("wb") as out:
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > artifact.size_bytes:
                                raise ArtifactVerificationError(
                                    artifact.artifact_id,
                                    f"size mismatch: expected {artifact.size_bytes} bytes, got more",
                                )
                            hasher.update(chunk)
                            out.write(chunk)
        except httpx.HTTPError as exc:
            tmp_path.unlink(missing_ok=True)
            raise WorkerUnreachableError(
                f"could not download artifact {artifact.artifact_id!r} from {self._base_url}: {exc}"
            ) from exc
        except ArtifactVerificationError:
            tmp_path.unlink(missing_ok=True)
            raise

        if size != artifact.size_bytes:
            tmp_path.unlink(missing_ok=True)
            raise ArtifactVerificationError(
                artifact.artifact_id, f"size mismatch: expected {artifact.size_bytes}, got {size}",
            )

        digest = hasher.hexdigest()
        if digest != artifact.digest.hex:
            tmp_path.unlink(missing_ok=True)
            raise ArtifactVerificationError(
                artifact.artifact_id, f"digest mismatch: expected {artifact.digest.hex}, got {digest}",
            )

        tmp_path.replace(dest_path)


async def _iter_file_chunks(path: Path, chunk_size: int = _MODEL_UPLOAD_CHUNK_BYTES):
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            yield chunk
