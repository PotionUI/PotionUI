import dataclasses
from typing import List, Optional

from src.features.filters.collaborators import FilterCollaborators
from src.features.filters.dto import (
    MAX_NAME_LENGTH,
    MAX_USER_FILTERS,
    CreateFilterRequest,
    UpdateFilterRequest,
)
from src.features.filters.errors import invalid, limit_reached, lut_unsupported, name_taken, not_found
from src.features.filters.records import DEFAULT_GROUP, UserFilter
from src.features.filters.repository import FilterLimitReached, FilterNameTaken
from src.features.filters.payload import USER_ID_PREFIX
from src.platform.imaging.filters import validate_steps


def bare_id(filter_id: str) -> str:
    return filter_id[len(USER_ID_PREFIX):] if filter_id.startswith(USER_ID_PREFIX) else filter_id


def _check_name(name: str) -> str:
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        raise invalid(f"Name must be 1 to {MAX_NAME_LENGTH} characters")
    return name


def _check_steps(collaborators: FilterCollaborators, steps: list) -> list:
    if not steps:
        raise invalid("A filter needs at least one step")
    issues = validate_steps(steps, collaborators.catalog.enabled_ops())
    if issues:
        shown = "; ".join(str(issue) for issue in issues[:3])
        more = f" (and {len(issues) - 3} more)" if len(issues) > 3 else ""
        raise invalid(f"Invalid steps: {shown}{more}")
    return [dict(step) for step in steps]


def _refuse_luts(collaborators: FilterCollaborators, request) -> None:
    if request.lut or request.has_lut:
        raise lut_unsupported()
    source_id = getattr(request, "source_id", None)
    if source_id:
        source = collaborators.catalog.get_filter(source_id)
        if source is not None and source.lut:
            raise lut_unsupported()


def list_mine(collaborators: FilterCollaborators, owner_id: str) -> List[UserFilter]:
    return collaborators.repository.list_for_owner(owner_id)


def get_mine(collaborators: FilterCollaborators, owner_id: str, filter_id: str) -> UserFilter:
    found = collaborators.repository.get_for_owner(owner_id, bare_id(filter_id))
    if found is None:
        raise not_found()
    return found


def create_filter(collaborators: FilterCollaborators, owner_id: str, request: CreateFilterRequest) -> UserFilter:
    _refuse_luts(collaborators, request)
    name = _check_name(request.name)
    steps = _check_steps(collaborators, request.steps)
    record = UserFilter(
        id="",
        owner_id=owner_id,
        name=name,
        description=(request.description or "").strip() or None,
        group_name=(request.group or DEFAULT_GROUP).strip() or DEFAULT_GROUP,
        intensity=request.intensity,
        steps=steps,
    )
    try:
        return collaborators.repository.create(record, MAX_USER_FILTERS)
    except FilterNameTaken as exc:
        raise name_taken(name) from exc
    except FilterLimitReached as exc:
        raise limit_reached(MAX_USER_FILTERS) from exc


def update_filter(
    collaborators: FilterCollaborators, owner_id: str, filter_id: str, request: UpdateFilterRequest
) -> UserFilter:
    _refuse_luts(collaborators, request)
    current = get_mine(collaborators, owner_id, filter_id)
    changes = {}
    if request.name is not None:
        changes["name"] = _check_name(request.name)
    if "description" in request.model_fields_set:
        changes["description"] = (request.description or "").strip() or None
    if request.group is not None:
        changes["group_name"] = request.group.strip() or DEFAULT_GROUP
    if request.intensity is not None:
        changes["intensity"] = request.intensity
    if request.steps is not None:
        changes["steps"] = _check_steps(collaborators, request.steps)
    updated: Optional[UserFilter]
    try:
        updated = collaborators.repository.update(dataclasses.replace(current, **changes))
    except FilterNameTaken as exc:
        raise name_taken(changes.get("name", current.name)) from exc
    if updated is None:
        raise not_found()
    return updated


def delete_filter(collaborators: FilterCollaborators, owner_id: str, filter_id: str) -> None:
    if not collaborators.repository.delete(owner_id, bare_id(filter_id)):
        raise not_found()
