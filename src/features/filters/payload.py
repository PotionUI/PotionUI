from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import quote

from src.features.filters.records import UserFilter
from src.features.filters.schema import (
    FilterDefinition,
    SOURCE_MINE,
    has_python_impl,
    kinds_of,
    revision_of,
    step_op_ids,
)
from src.platform.database.rows import dt_iso
from src.platform.imaging.filters import OpSpec

PAYLOAD_SCHEMA = 1
USER_ID_PREFIX = "mine:"


def lut_url(public_id: str) -> str:
    return f"/api/filters/{quote(public_id, safe=':')}/lut"


def availability(
    steps: List[Mapping[str, Any]],
    known_ops: Mapping[str, OpSpec],
    enabled_ops: Mapping[str, OpSpec],
) -> Dict[str, Any]:
    unavailable = [op_id for op_id in step_op_ids(steps) if op_id not in enabled_ops]
    needs_plugin: Optional[str] = None
    for op_id in unavailable:
        spec = known_ops.get(op_id)
        needs_plugin = spec.plugin_id if spec is not None and spec.plugin_id else (op_id.split(".", 1)[0] if "." in op_id else None)
        if needs_plugin:
            break
    backend_ok = not unavailable and all(has_python_impl(enabled_ops[op_id]) for op_id in step_op_ids(steps))
    return {
        "kinds": kinds_of(steps, known_ops),
        "unavailable_ops": unavailable,
        "needs_plugin": needs_plugin,
        "backend_ok": backend_ok,
    }


def file_item(
    definition: FilterDefinition,
    known_ops: Mapping[str, OpSpec],
    enabled_ops: Mapping[str, OpSpec],
) -> Dict[str, Any]:
    has_lut = definition.lut is not None
    return {
        "id": definition.public_id,
        "name": definition.name,
        "description": definition.description,
        "group": definition.group,
        "order": definition.order,
        "intensity": definition.intensity,
        "tags": list(definition.tags),
        "source": definition.source,
        "plugin_id": definition.plugin_id,
        "overrides": definition.overrides,
        "owned": False,
        "author": definition.author,
        "license": definition.license,
        "credit": definition.credit,
        "has_lut": has_lut,
        "lut_size": definition.lut_size,
        "lut_url": lut_url(definition.public_id) if has_lut else None,
        "steps": definition.steps,
        **availability(definition.steps, known_ops, enabled_ops),
        "revision": definition.revision,
    }


def user_revision(steps: List[Mapping[str, Any]], intensity: int) -> str:
    return revision_of(steps, f"|{intensity}")


def user_item(
    user_filter: UserFilter,
    known_ops: Mapping[str, OpSpec],
    enabled_ops: Mapping[str, OpSpec],
) -> Dict[str, Any]:
    return {
        "id": f"{USER_ID_PREFIX}{user_filter.id}",
        "name": user_filter.name,
        "description": user_filter.description or "",
        "group": user_filter.group_name,
        "order": 0,
        "intensity": user_filter.intensity,
        "tags": [],
        "source": SOURCE_MINE,
        "plugin_id": None,
        "overrides": False,
        "owned": True,
        "author": "",
        "license": "",
        "credit": "",
        "has_lut": False,
        "lut_size": None,
        "lut_url": None,
        "steps": user_filter.steps,
        **availability(user_filter.steps, known_ops, enabled_ops),
        "revision": user_revision(user_filter.steps, user_filter.intensity),
        "created_at": dt_iso(user_filter.created_at),
        "updated_at": dt_iso(user_filter.updated_at),
    }
