"""The worker's HTTP surface. See docs/remote-native.md for the route table.

Every route depends on the bearer-token check from ``auth.py``. Protocol
documents (``WorkerInfoV1``, ``ExecutionPackageV1``, ``JobEventV1``) cross the
wire enveloped (``src.platform.worker_protocol.envelope``); the small
worker-invented response bodies (submit/cancel outcomes) do not need an
envelope of their own - they are not part of the versioned protocol contract,
just this route's own JSON shape.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from src.features.remote_execution.worker.assets import AssetStagingError
from src.features.remote_execution.worker.auth import build_token_dependency
from src.features.remote_execution.worker.capabilities import probe_capabilities
from src.features.remote_execution.worker.coordinator import CancelOutcome, SubmitOutcome
from src.features.remote_execution.worker.model_depot import ModelStagingError
from src.features.remote_execution.worker.transfers import Transfer
from src.platform.worker_protocol import WorkerInfoV1
from src.platform.worker_protocol.envelope import (
    WorkerEnvelopeError,
    envelope,
    read_envelope,
)
from src.platform.worker_protocol.execution_package import ExecutionPackageV1
from src.platform.worker_protocol.model_bundle import ModelBundleManifestV1
from src.platform.worker_protocol.model_fetch import ModelFetchRequestV1

#: artifact_id is always uuid4().hex - reject anything else before it ever
#: reaches a filesystem path.
_ARTIFACT_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")

_SUBMIT_STATUS_CODES = {
    SubmitOutcome.ACCEPTED: 202,
    SubmitOutcome.DUPLICATE: 200,
    SubmitOutcome.DIGEST_CONFLICT: 409,
    SubmitOutcome.BUSY: 429,
    SubmitOutcome.REJECTED: 200,
    SubmitOutcome.EXPIRED: 200,
}


def build_worker_router(container) -> APIRouter:
    router = APIRouter()
    require_token = Depends(build_token_dependency(container.config.token))

    @router.get("/v1/worker")
    async def get_worker_info(_=require_token):
        info = WorkerInfoV1(
            worker_id=container.config.worker_id,
            provider=container.config.provider,
            engine="native",
            capabilities=probe_capabilities(container.config.work_dir),
            fingerprints=container.coordinator.fingerprints(),
            started_at=datetime.now(timezone.utc),
        )
        return envelope(info)

    @router.post("/v1/executions")
    async def submit_execution(request: Request, _=require_token):
        body = await request.json()
        try:
            package = read_envelope(body)
        except WorkerEnvelopeError as exc:
            raise HTTPException(422, detail={"code": exc.code, **exc.detail}) from exc
        if not isinstance(package, ExecutionPackageV1):
            raise HTTPException(422, detail={"code": "wrong_kind", "expected": "execution_package"})

        result = container.coordinator.submit(package)
        status_code = _SUBMIT_STATUS_CODES[result.outcome]
        body_out = {"execution_id": result.execution_id, "outcome": result.outcome}
        if result.record is not None:
            body_out["status"] = result.record.latest_kind
        if result.detail is not None:
            body_out["detail"] = result.detail
        return _json_response(body_out, status_code)

    @router.post("/v1/executions/{execution_id}/assets/{logical_id}")
    async def upload_asset(execution_id: str, logical_id: str, request: Request, _=require_token):
        package = container.coordinator.package_for(execution_id)
        if package is None:
            raise HTTPException(404, detail="unknown execution")

        stager = container.coordinator.stager_for(execution_id)
        if stager is None:
            raise HTTPException(404, detail="unknown execution")

        entry = stager.entry_for(package, logical_id)
        if entry is None:
            raise HTTPException(404, detail=f"no such input asset '{logical_id}'")

        chunks = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > entry.size_bytes:
                raise HTTPException(
                    422,
                    detail={
                        "reason": f"size mismatch: expected {entry.size_bytes} bytes, got more",
                    },
                )
            chunks.append(chunk)

        try:
            stager.stage(entry, chunks)
        except AssetStagingError as exc:
            raise HTTPException(422, detail={"reason": exc.reason}) from exc

        return {"logical_id": logical_id, "staged": True}

    @router.get("/v1/executions/{execution_id}/events")
    async def stream_events(execution_id: str, after: int = 0, _=require_token):
        if container.coordinator.record_for(execution_id) is None:
            raise HTTPException(404, detail="unknown execution")

        async def event_stream():
            cursor = after
            while True:
                events = container.coordinator.events_after(execution_id, cursor)
                terminal = False
                for event in events:
                    cursor = event.cursor
                    terminal = terminal or event.is_terminal
                    yield f"data: {json.dumps(envelope(event))}\n\n"
                if terminal:
                    return
                record = container.coordinator.record_for(execution_id)
                if record is not None and record.is_terminal:
                    return
                await asyncio.sleep(0.02)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @router.post("/v1/executions/{execution_id}/cancel")
    async def cancel_execution(execution_id: str, _=require_token):
        outcome = container.coordinator.cancel(execution_id)
        status_code = 404 if outcome == CancelOutcome.NOT_FOUND else 200
        return _json_response({"result": outcome}, status_code)

    @router.post("/v1/models/inventory")
    async def model_inventory(request: Request, _=require_token):
        if container.model_depot is None:
            raise HTTPException(503, detail="this worker has no configured model depot")

        body = await request.json()
        try:
            manifest = read_envelope(body)
        except WorkerEnvelopeError as exc:
            raise HTTPException(422, detail={"code": exc.code, **exc.detail}) from exc
        if not isinstance(manifest, ModelBundleManifestV1):
            raise HTTPException(422, detail={"code": "wrong_kind", "expected": "model_bundle_manifest"})

        response = container.model_depot.inventory(manifest)
        return envelope(response)

    @router.post("/v1/models/{bundle_id}/{logical_id:path}")
    async def upload_model(bundle_id: str, logical_id: str, request: Request, _=require_token):
        if container.model_depot is None:
            raise HTTPException(503, detail="this worker has no configured model depot")

        entry = container.model_depot.entry_for(bundle_id, logical_id)
        if entry is None:
            raise HTTPException(
                404, detail=f"no such model entry '{logical_id}' in bundle '{bundle_id}' "
                "- call /v1/models/inventory with this bundle first",
            )

        transfer = container.model_depot.transfers.start("upload", entry.relative_path, entry.size_bytes)

        chunks = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > entry.size_bytes:
                reason = f"size mismatch: expected {entry.size_bytes} bytes, got more"
                container.model_depot.transfers.fail(transfer.id, reason)
                raise HTTPException(422, detail={"reason": reason})
            chunks.append(chunk)
            container.model_depot.transfers.progress(transfer.id, received)

        try:
            container.model_depot.stage(entry, chunks)
        except ModelStagingError as exc:
            container.model_depot.transfers.fail(transfer.id, exc.reason)
            raise HTTPException(422, detail={"reason": exc.reason}) from exc

        container.model_depot.transfers.complete(transfer.id)
        return {"logical_id": logical_id, "staged": True, "transfer_id": transfer.id}

    @router.get("/v1/models")
    async def list_models(_=require_token):
        if container.model_depot is None:
            raise HTTPException(503, detail="this worker has no configured model depot")
        return {"entries": container.model_depot.list_entries()}

    @router.post("/v1/models/fetch")
    async def fetch_model(request: Request, background_tasks: BackgroundTasks, _=require_token):
        if container.model_depot is None:
            raise HTTPException(503, detail="this worker has no configured model depot")

        body = await request.json()
        try:
            fetch_request = read_envelope(body)
        except WorkerEnvelopeError as exc:
            raise HTTPException(422, detail={"code": exc.code, **exc.detail}) from exc
        if not isinstance(fetch_request, ModelFetchRequestV1):
            raise HTTPException(422, detail={"code": "wrong_kind", "expected": "model_fetch_request"})

        try:
            transfer = container.model_depot.start_fetch(fetch_request)
        except ModelStagingError as exc:
            raise HTTPException(422, detail={"reason": exc.reason}) from exc

        background_tasks.add_task(container.model_depot.run_fetch, transfer.id, fetch_request)
        return _json_response({"transfer_id": transfer.id}, 202)

    @router.get("/v1/models/transfers")
    async def list_transfers(_=require_token):
        if container.model_depot is None:
            raise HTTPException(503, detail="this worker has no configured model depot")
        return {"transfers": [_transfer_json(t) for t in container.model_depot.transfers.list()]}

    @router.get("/v1/models/transfers/{transfer_id}")
    async def get_transfer(transfer_id: str, _=require_token):
        if container.model_depot is None:
            raise HTTPException(503, detail="this worker has no configured model depot")
        transfer = container.model_depot.transfers.get(transfer_id)
        if transfer is None:
            raise HTTPException(404, detail="unknown transfer")
        return _transfer_json(transfer)

    @router.get("/v1/artifacts/{artifact_id}")
    async def get_artifact(artifact_id: str, _=require_token):
        if not _ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise HTTPException(404, detail="unknown artifact")

        path = container.coordinator.artifact_path(artifact_id)
        if path is None:
            raise HTTPException(404, detail="unknown artifact")

        resolved = path.resolve()
        root = container.config.artifacts_dir.resolve()
        if root != resolved and root not in resolved.parents:
            raise HTTPException(404, detail="unknown artifact")
        if not resolved.is_file():
            raise HTTPException(404, detail="unknown artifact")

        return FileResponse(resolved)

    return router


def _json_response(body: dict, status_code: int) -> JSONResponse:
    return JSONResponse(body, status_code=status_code)


def _transfer_json(transfer: Transfer) -> dict:
    return {
        "id": transfer.id,
        "kind": transfer.kind,
        "relative_path": transfer.relative_path,
        "total_bytes": transfer.total_bytes,
        "received_bytes": transfer.received_bytes,
        "state": transfer.state,
        "error": transfer.error,
    }
