"""Core's batch tools, registered on the shared phrasebook operation registry
so they are peers of plugin-provided ones."""
from typing import Any, Dict, List, Type

from pydantic import BaseModel, Field, ValidationError

from src.features.phrasebook.dto import PhrasebookFindMode, PhrasebookValueField
from src.features.phrasebook.operations import batch
from src.platform.plugins.phrasebook_ops import (
    BatchOperationError,
    BatchOutcome,
    BatchPreview,
    PhrasebookBatchContext,
    PhrasebookBatchOperation,
    PhrasebookBatchOperationDefinition,
    PhrasebookOperationRegistry,
)


class ReplaceParams(BaseModel):
    find: str
    replace: str = ""
    mode: PhrasebookFindMode = PhrasebookFindMode.CONTAINS
    case_sensitive: bool = False
    fields: List[PhrasebookValueField] = Field(default_factory=lambda: ["label", "value"])


class SetActiveParams(BaseModel):
    is_active: bool


class MoveParams(BaseModel):
    category_id: str


def _params(model: Type[BaseModel], params: Dict[str, Any]):
    try:
        return model.model_validate(params or {})
    except ValidationError as e:
        raise BatchOperationError("invalid_params", str(e)) from e


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class ReplaceOperation(PhrasebookBatchOperation):
    supports_preview = True

    async def preview(self, ctx: PhrasebookBatchContext, value_ids: List[str], params: Dict[str, Any]) -> BatchPreview:
        p = _params(ReplaceParams, params)
        result = batch.preview_replace(
            ctx, value_ids, p.find, p.replace,
            mode=p.mode.value, case_sensitive=p.case_sensitive, fields=p.fields,
        )
        return BatchPreview(**result)

    async def run(self, ctx: PhrasebookBatchContext, value_ids: List[str], params: Dict[str, Any]) -> BatchOutcome:
        p = _params(ReplaceParams, params)
        result = batch.replace_values(
            ctx, getattr(ctx, "plugins", None), value_ids, p.find, p.replace,
            mode=p.mode.value, case_sensitive=p.case_sensitive, fields=p.fields,
        )
        return BatchOutcome(
            updated=result["updated"], skipped=result["skipped"],
            message=f"Replaced in {_plural(len(result['updated']), 'value')}",
        )


class SetActiveOperation(PhrasebookBatchOperation):
    async def run(self, ctx: PhrasebookBatchContext, value_ids: List[str], params: Dict[str, Any]) -> BatchOutcome:
        p = _params(SetActiveParams, params)
        result = batch.set_values_active(ctx, value_ids, p.is_active)
        verb = "Activated" if p.is_active else "Deactivated"
        return BatchOutcome(updated=result["updated"], message=f"{verb} {_plural(len(result['updated']), 'value')}")


class MoveOperation(PhrasebookBatchOperation):
    async def run(self, ctx: PhrasebookBatchContext, value_ids: List[str], params: Dict[str, Any]) -> BatchOutcome:
        p = _params(MoveParams, params)
        result = batch.move_values(ctx, value_ids, p.category_id)
        return BatchOutcome(updated=result["updated"], message=f"Moved {_plural(len(result['updated']), 'value')}")


class DeleteOperation(PhrasebookBatchOperation):
    async def run(self, ctx: PhrasebookBatchContext, value_ids: List[str], params: Dict[str, Any]) -> BatchOutcome:
        result = batch.delete_values(ctx, getattr(ctx, "plugins", None), value_ids)
        return BatchOutcome(deleted=result["deleted"], message=f"Deleted {_plural(len(result['deleted']), 'value')}")


CORE_OPERATIONS = (
    ("replace", "Replace…", ReplaceOperation),
    ("set_active", "Activate / Deactivate", SetActiveOperation),
    ("move", "Move to…", MoveOperation),
    ("delete", "Delete", DeleteOperation),
)


def register_core_batch_operations(registry: PhrasebookOperationRegistry) -> None:
    for op_id, label, cls in CORE_OPERATIONS:
        if registry.get(op_id) is None:
            registry.register(PhrasebookBatchOperationDefinition(
                op_id=op_id, label=label, backend=cls(), frontend_component=None, source="core",
            ))
