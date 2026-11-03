import logging
from typing import Annotated, Any, Callable, Dict, List, Literal, NoReturn, Optional, Sequence

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from src.plugin_api import User, get_current_admin_user
from src.plugin_api.models import model_types as registered_model_types

from . import core_bridge, nsfw, permissions_store, requests
from .permissions import CAPABILITIES, Principal, current_principal
from .permissions_store import DEFAULT_SOURCES, SOURCE_IDS, Row
from .settings import CORE_USER_NSFW_SETTING

logger = logging.getLogger("plugins.model-browser")

router = APIRouter()


def _only(allowed: Callable[[], Sequence[str]], what: str) -> Callable[[List[str]], List[str]]:
    def check(values: List[str]) -> List[str]:
        unknown = sorted(set(values) - set(allowed()))
        if unknown:
            raise ValueError(f"unknown {what}: {', '.join(unknown)}")
        return values

    return check


class ParamsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: Annotated[List[str], AfterValidator(_only(lambda: SOURCE_IDS, "source"))] = Field(
        default_factory=lambda: sorted(DEFAULT_SOURCES)
    )
    model_types: Annotated[List[str], AfterValidator(_only(registered_model_types, "model type"))] = Field(
        default_factory=list
    )
    max_file_gb: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    assign_to: Literal["user", "group"] = "user"
    allow_remote: bool = Field(default=False, strict=True)


class GroupUpdate(BaseModel):
    group_id: str
    capabilities: Annotated[List[str], AfterValidator(_only(lambda: CAPABILITIES, "capability"))]
    params: ParamsUpdate = Field(default_factory=ParamsUpdate)


class PermissionsUpdate(BaseModel):
    groups: List[GroupUpdate]


@router.get("/me")
def get_me(principal: Principal = Depends(current_principal)) -> Dict[str, Any]:
    user, grant = principal.user, principal.grant
    context = nsfw.resolve_nsfw(user)
    groups = core_bridge.list_groups() if principal.is_admin else core_bridge.user_groups(user.id)
    return _ok(
        {
            "user": {"id": user.id, "username": user.username, "is_admin": principal.is_admin},
            "capabilities": sorted(grant.capabilities),
            "params": {
                "sources": sorted(grant.sources),
                "model_types": sorted(grant.model_types),
                "max_file_gb": grant.max_file_gb,
                "assign_to": grant.assign_to,
                "allow_remote": grant.allow_remote,
                "unrestricted": grant.unrestricted,
            },
            "request_params": {
                "sources": sorted(grant.request_params.sources),
                "model_types": sorted(grant.request_params.model_types),
                "max_file_gb": grant.request_params.max_file_gb,
                "assign_to": grant.request_params.assign_to,
            },
            "nsfw": {
                "ceiling": context.ceiling,
                "mode": context.mode,
                "allowed_modes": list(context.allowed_modes),
                "max_level": context.max_level,
                "hide_poi": context.hide_poi,
                "setting_key": CORE_USER_NSFW_SETTING,
            },
            "groups": [{"id": group["id"], "name": group["name"]} for group in groups],
            "requests": {"open_count": _open_requests(principal)},
        }
    )


def _open_requests(principal: Principal) -> int:
    try:
        return requests.open_count_for(principal)
    except Exception:
        logger.warning("[MODEL-BROWSER] Could not count the open requests", exc_info=True)
        return 0


@router.get("/permissions")
def get_permissions(admin: User = Depends(get_current_admin_user)) -> Dict[str, Any]:
    return _ok(_permissions(core_bridge.list_groups()))


@router.put("/permissions")
def put_permissions(
    body: Dict[str, Any] = Body(...),
    admin: User = Depends(get_current_admin_user),
) -> Dict[str, Any]:
    try:
        update = PermissionsUpdate.model_validate(body)
    except ValidationError as error:
        _fail(400, "invalid_request", _describe(error))

    groups = core_bridge.list_groups()
    known = {group["id"] for group in groups}
    rows: Dict[str, Row] = {}
    for entry in update.groups:
        if entry.group_id not in known:
            _fail(400, "unknown_group", f"There is no user group with the id '{entry.group_id}'")
        if entry.group_id in rows:
            _fail(400, "invalid_request", f"The user group '{entry.group_id}' is listed more than once")
        rows[entry.group_id] = Row.from_json(entry.model_dump())

    permissions_store.save_rows(rows.values(), admin.id)
    logger.info(
        "[MODEL-BROWSER] %s saved the permissions of %d user group(s); the other groups use their defaults",
        admin.username,
        len(rows),
    )
    return _ok(_permissions(groups))


def _permissions(groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = permissions_store.load_rows()
    gone = set(rows) - {group["id"] for group in groups}
    if gone:
        permissions_store.delete_rows(gone)
        logger.info(
            "[MODEL-BROWSER] Deleted the permissions of user groups that no longer exist: %s",
            ", ".join(sorted(gone)),
        )
    return {
        "capabilities": list(CAPABILITIES),
        "sources": list(SOURCE_IDS),
        "model_types": list(registered_model_types()),
        "groups": [_group_entry(group, permissions_store.row_or_default(rows, group["id"])) for group in groups],
    }


def _group_entry(group: Dict[str, Any], row: Row) -> Dict[str, Any]:
    return {
        "group_id": group["id"],
        "name": group["name"],
        "is_system": group["is_system"],
        "capabilities": sorted(row.capabilities),
        "params": row.params_json(),
    }


def _ok(data: Any) -> Dict[str, Any]:
    return {"success": True, "data": data}


def _fail(status_code: int, error: str, message: str) -> NoReturn:
    raise HTTPException(status_code=status_code, detail={"success": False, "error": error, "message": message})


def _describe(error: ValidationError) -> str:
    problems = []
    for item in error.errors(include_url=False):
        where = ".".join(str(part) for part in item["loc"])
        problems.append(f"{where}: {item['msg']}" if where else item["msg"])
    return "Invalid permissions: " + "; ".join(problems)
