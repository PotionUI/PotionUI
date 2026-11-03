import asyncio
import logging
from typing import Annotated, Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, BeforeValidator, Field, ValidationError, field_validator

from src.plugin_api.models import model_types

from . import core_bridge, downloads, notifications, nsfw, permissions, requests
from .downloads import MAX_ID_LENGTH, DownloadError, DownloadRequest, FileRef, Prepared
from .http import SourceError
from .permissions import Principal, current_principal, require_any, require_approve, require_request
from .requests import MAX_NOTE_LENGTH, Request
from .routes_downloads import download_http_error
from .routes_sources import source_http_error
from .schema import NsfwContext

logger = logging.getLogger("plugins.model-browser")

router = APIRouter()

require_requests_access = require_any("request", "approve")

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"success": False, "error": code, "message": message})


def _ok(data: Any) -> Dict[str, Any]:
    return {"success": True, "data": data}


def _blank_is_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


Note = Annotated[Optional[str], BeforeValidator(_blank_is_none)]


class RequestCreate(FileRef):
    note: Note = Field(None, max_length=MAX_NOTE_LENGTH)


class Decision(BaseModel):
    note: Note = Field(None, max_length=MAX_NOTE_LENGTH)


class Approval(Decision):
    model_type: Annotated[Optional[str], BeforeValidator(_blank_is_none)] = None
    assign_group_id: Annotated[Optional[str], BeforeValidator(_blank_is_none)] = Field(None, max_length=MAX_ID_LENGTH)

    @field_validator("model_type")
    @classmethod
    def _model_type(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in model_types():
            raise ValueError(f"unknown model type '{value[:64]}'")
        return value

    @field_validator("assign_group_id")
    @classmethod
    def _group_id(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and any(ord(char) < 0x20 or ord(char) == 0x7F or char == "\\" for char in value):
            raise ValueError("must not contain a backslash or a control character")
        return value


def _validate(model: Any, body: Any, what: str) -> Any:
    try:
        return model.model_validate(body if body is not None else {})
    except ValidationError as error:
        raise _error(400, "invalid_request", downloads.describe_validation_error(error, what)) from None


def _statuses(values: Optional[List[str]]) -> Optional[List[str]]:
    wanted = [part.strip() for value in values or [] for part in value.split(",") if part.strip()]
    unknown = [status for status in wanted if status not in requests.STATUSES]
    if unknown:
        raise _error(
            400,
            "invalid_request",
            f"Unknown request status '{unknown[0][:32]}': use one of {', '.join(requests.STATUSES)}",
        )
    return list(dict.fromkeys(wanted)) or None


def _get_request(request_id: str) -> Request:
    request = requests.get(request_id)
    if request is None:
        raise _error(404, "not_found", "Request not found")
    return request


def _not_open(request_id: str) -> HTTPException:
    current = requests.get(request_id)
    status = current.status if current is not None else "gone"
    return _error(409, "not_open", f"This request is no longer open (it is {status})")


OWN_REQUEST_MESSAGE = "You cannot decide on your own request: cancel it instead"


def _refuse_own_request(request: Request, principal: Principal) -> None:
    if request.user_id == principal.user.id and not principal.is_admin:
        raise _error(403, "forbidden", OWN_REQUEST_MESSAGE)


def _installed_for(prepared: Prepared) -> Tuple[bool, bool]:
    sha256 = prepared.file.sha256
    if not sha256:
        return False, False
    model = core_bridge.find_model_by_sha256(sha256)
    if model is None or model.get("is_available", True) is False:
        return False, False
    if prepared.principal.is_admin:
        return True, True
    return True, model.get("id") in set(core_bridge.visible_model_ids(prepared.principal.user.id))


def _notify_approvers(request: Request, requester_name: str) -> None:
    try:
        recipients = permissions.approver_ids()
    except Exception:
        logger.warning("[MODEL-BROWSER] Could not list who may approve request %s", request.id, exc_info=True)
        return
    notifications.request_received(recipients, request, requester_name)


@router.post("/requests")
async def create_request(
    body: Any = Body(...),
    principal: Principal = Depends(require_request),
) -> Dict[str, Any]:
    asked = _validate(RequestCreate, body, "request")
    user = principal.user
    try:
        prepared = await downloads.prepare_download(
            principal, DownloadRequest.model_validate(asked.model_dump(exclude={"note"})), purpose="request"
        )
        installed, visible = await asyncio.to_thread(_installed_for, prepared)
    except DownloadError as exc:
        raise download_http_error(exc) from None
    except SourceError as exc:
        raise source_http_error(exc) from None
    except Exception:
        logger.exception("[MODEL-BROWSER] Could not check the request of %s for %s %s", user.username, asked.source, asked.model_id)
        raise _error(500, "internal_error", "The request could not be made; the server log has the details") from None
    if visible:
        raise _error(409, "already_available", "This file is already installed and in your models")

    detail, version, file = prepared.detail, prepared.version, prepared.file
    existing = await asyncio.to_thread(requests.find_open, user.id, detail.source, detail.id, version.id, file.id)
    if existing is not None:
        return _ok({"request": requests.for_viewer(existing, prepared.ctx), "already_requested": True})

    snapshot = requests.build_snapshot(detail, version, file, prepared.model_type, already_installed=installed)
    created = await asyncio.to_thread(
        lambda: requests.create(
            user_id=user.id,
            source=detail.source,
            source_model_id=detail.id,
            source_version_id=version.id,
            file_id=file.id,
            snapshot=snapshot,
            note=asked.note,
        )
    )
    logger.info("[MODEL-BROWSER] %s requested %s (%s %s, request %s)", user.username, file.name, detail.source, detail.id, created.id)
    await asyncio.to_thread(_notify_approvers, created, user.username)
    return _ok({"request": requests.for_viewer(created, prepared.ctx), "already_requested": False})


@router.get("/requests")
def list_requests(
    status: Optional[List[str]] = Query(None),
    all_users: Optional[bool] = Query(None, alias="all"),
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    principal: Principal = Depends(require_requests_access),
) -> Dict[str, Any]:
    statuses = _statuses(status)
    can_approve = permissions.can_approve(principal)
    everyone = can_approve and all_users is not False
    rows = requests.list_rows(user_id=None if everyone else principal.user.id, statuses=statuses, limit=limit)
    ctx = nsfw.resolve_nsfw(principal.user)
    names = requests.Usernames()
    return _ok(
        {
            "requests": [requests.for_viewer(row, ctx, names) for row in rows],
            "open_count": requests.open_count_for(principal),
            "can_approve": can_approve,
        }
    )


MAX_PENDING_KEYS = 5000


@router.get("/requests/pending")
def pending_requests(principal: Principal = Depends(require_requests_access)) -> Dict[str, Any]:
    rows = requests.list_rows(user_id=principal.user.id, statuses=[requests.OPEN], limit=MAX_PENDING_KEYS)
    return _ok(
        {
            "keys": [
                {
                    "source": row.source,
                    "model_id": row.source_model_id,
                    "version_id": row.source_version_id,
                    "file_id": row.file_id,
                }
                for row in rows
            ]
        }
    )


async def _queue_for_requester(
    request: Request, approval: Approval, principal: Principal
) -> Tuple[Dict[str, Any], str, NsfwContext]:
    approver = principal.user
    requester = await asyncio.to_thread(permissions.user_by_id, request.user_id)
    if requester is None:
        raise _error(409, "requester_gone", "The user who made this request no longer exists: decline it instead")
    model_type = approval.model_type or request.snapshot.get("mb_type")
    try:
        file_request = DownloadRequest(
            source=request.source,
            model_id=request.source_model_id,
            version_id=request.source_version_id or "",
            file_id=request.file_id,
            model_type=model_type,
            assign_group_id=approval.assign_group_id,
        )
    except ValidationError as error:
        raise _error(400, "invalid_request", downloads.describe_validation_error(error, "request")) from None
    try:
        requester_principal = await asyncio.to_thread(permissions.principal_for, requester)
        viewer_ctx = await asyncio.to_thread(nsfw.resolve_nsfw, approver)
        prepared = await downloads.prepare_download(
            requester_principal, file_request, purpose="request", on_behalf=True
        )
        answer = await downloads.queue_prepared(
            prepared,
            request_id=request.id,
            created_by=approver.id,
            viewer_ctx=viewer_ctx,
            viewer_admin=principal.is_admin,
        )
    except DownloadError as exc:
        raise download_http_error(exc) from None
    except SourceError as exc:
        raise source_http_error(exc) from None
    except Exception:
        logger.exception("[MODEL-BROWSER] Could not queue request %s for %s", request.id, requester.username)
        raise _error(500, "internal_error", "The request could not be approved; the server log has the details") from None
    return answer, prepared.assign_to, viewer_ctx


def _reopen(request_id: str) -> None:
    try:
        requests.update(
            request_id, only_from=(requests.APPROVED,), status=requests.OPEN, decided_by=None, decision_note=None
        )
    except Exception:
        logger.exception("[MODEL-BROWSER] Could not reopen request %s after a failed approval", request_id)


@router.post("/requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    body: Any = Body(None),
    principal: Principal = Depends(require_approve),
) -> Dict[str, Any]:
    approval = _validate(Approval, body, "approval")
    approver = principal.user
    request = await asyncio.to_thread(_get_request, request_id)
    _refuse_own_request(request, principal)
    if not request.is_open:
        raise await asyncio.to_thread(_not_open, request.id)
    claimed = await asyncio.to_thread(
        lambda: requests.set_status(
            request.id,
            requests.APPROVED,
            decided_by=approver.id,
            decision_note=approval.note,
            only_from=(requests.OPEN,),
        )
    )
    if claimed is None:
        raise await asyncio.to_thread(_not_open, request.id)
    try:
        answer, assign_to, viewer_ctx = await _queue_for_requester(claimed, approval, principal)
    except BaseException:
        await asyncio.to_thread(_reopen, claimed.id)
        raise
    approved = (
        await asyncio.to_thread(
            lambda: requests.update(claimed.id, sha256=answer["pending"]["sha256"], assign_to=assign_to)
        )
        or claimed
    )
    logger.info(
        "[MODEL-BROWSER] %s approved request %s of %s (%s)",
        approver.username,
        approved.id,
        approved.user_id,
        "already installed" if answer["already_installed"] else "already queued" if answer["already_queued"] else "queued",
    )
    await asyncio.to_thread(
        lambda: notifications.request_approved(
            approved,
            approver.username,
            already_installed=answer["already_installed"],
            already_queued=answer["already_queued"],
        )
    )
    return _ok({"request": requests.for_viewer(approved, viewer_ctx), **answer})


@router.post("/requests/{request_id}/reject")
def reject_request(
    request_id: str,
    body: Any = Body(None),
    principal: Principal = Depends(require_approve),
) -> Dict[str, Any]:
    decision = _validate(Decision, body, "decision")
    approver = principal.user
    request = _get_request(request_id)
    _refuse_own_request(request, principal)
    rejected = requests.set_status(
        request.id,
        requests.REJECTED,
        decided_by=approver.id,
        decision_note=decision.note,
        only_from=(requests.OPEN,),
    )
    if rejected is None:
        raise _not_open(request.id)
    logger.info("[MODEL-BROWSER] %s declined request %s of %s", approver.username, rejected.id, rejected.user_id)
    notifications.request_rejected(rejected, approver.username)
    return _ok({"request": requests.for_viewer(rejected, nsfw.resolve_nsfw(approver))})


@router.delete("/requests/{request_id}")
def cancel_request(request_id: str, principal: Principal = Depends(current_principal)) -> Dict[str, Any]:
    user = principal.user
    request = _get_request(request_id)
    if request.user_id != user.id:
        raise _error(403, "forbidden", "Only the user who made a request can cancel it")
    cancelled = requests.set_status(request.id, requests.CANCELLED, only_from=(requests.OPEN,))
    if cancelled is None:
        raise _not_open(request.id)
    logger.info("[MODEL-BROWSER] %s cancelled request %s", user.username, cancelled.id)
    return _ok({"request": requests.for_viewer(cancelled, nsfw.resolve_nsfw(user))})
