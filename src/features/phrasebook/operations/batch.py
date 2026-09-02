"""Core batch operations over a user's selected phrasebook values: replace
text (with a dry-run preview), toggle active, move between categories,
delete. Every function works through a `PhrasebookBatchContext`, so a
request is validated up front and written in one transaction."""
import logging
from typing import Any, Dict, List, Optional, Sequence

from src.features.phrasebook.hooks import PHRASEBOOK_HOOKS
from src.features.phrasebook.operations.matching import (
    InvalidPattern,
    Matcher,
    compile_matcher,
    substitute,
)
from src.platform.plugins.hooks import execute_hook
from src.platform.plugins.phrasebook_ops import BatchOperationError, PhrasebookBatchContext

logger = logging.getLogger(__name__)

BatchError = BatchOperationError


def _matcher(find: str, mode: str, case_sensitive: bool) -> Matcher:
    try:
        return compile_matcher(find, mode, case_sensitive)
    except InvalidPattern as e:
        raise BatchOperationError("invalid_pattern", str(e)) from e


def _substitutions(
    values: List[Dict[str, Any]], matcher: Matcher, replacement: str, fields: Sequence[str]
) -> List[Dict[str, Any]]:
    """`{id, field, before, after}` for every chosen field whose text changes."""
    items = []
    for value in values:
        for field in fields:
            before = value[field]
            try:
                after = substitute(matcher, before, replacement)
            except InvalidPattern as e:
                raise BatchOperationError("invalid_pattern", str(e)) from e
            if after != before:
                items.append({"id": value["id"], "field": field, "before": before, "after": after})
    return items


def _hook(plugins, hook: str, data: Dict[str, Any]):
    if plugins is None:
        return data, False
    return execute_hook(plugins, hook, data)


def preview_replace(
    ctx: PhrasebookBatchContext,
    value_ids: Sequence[str],
    find: str,
    replace: str,
    mode: str = "contains",
    case_sensitive: bool = False,
    fields: Sequence[str] = ("label", "value"),
) -> Dict[str, Any]:
    values = ctx.values(value_ids)
    items = _substitutions(values, _matcher(find, mode, case_sensitive), replace, fields)
    changed = list(dict.fromkeys(item["id"] for item in items))
    return {
        "items": items,
        "changed": len(changed),
        "unchanged": [v["id"] for v in values if v["id"] not in changed],
    }


def replace_values(
    ctx: PhrasebookBatchContext,
    plugins,
    value_ids: Sequence[str],
    find: str,
    replace: str,
    mode: str = "contains",
    case_sensitive: bool = False,
    fields: Sequence[str] = ("label", "value"),
) -> Dict[str, Any]:
    values = ctx.values(value_ids)
    items = _substitutions(values, _matcher(find, mode, case_sensitive), replace, fields)

    after: Dict[str, Dict[str, str]] = {}
    for item in items:
        after.setdefault(item["id"], {})[item["field"]] = item["after"]
    changed = [v for v in values if v["id"] in after]
    skipped = [v["id"] for v in values if v["id"] not in after]

    rows = []
    for value in changed:
        label = after[value["id"]].get("label", value["label"])
        text = after[value["id"]].get("value", value["value"])
        hook_data, blocked = _hook(
            plugins,
            PHRASEBOOK_HOOKS.before_update,
            {
                "type": "value",
                "value_id": value["id"],
                "old_label": value["label"],
                "new_label": label,
                "old_value": value["value"],
                "new_value": text,
                "user_id": ctx.user_id,
            },
        )
        if blocked:
            raise BatchOperationError("blocked", hook_data.get("block_reason", "Value update blocked"))
        rows.append((value["id"], label, text))

    ctx.update_value_texts(rows)

    for value_id, label, _text in rows:
        _hook(
            plugins,
            PHRASEBOOK_HOOKS.after_update,
            {"type": "value", "value_id": value_id, "label": label, "user_id": ctx.user_id},
        )

    updated = ctx.values([r[0] for r in rows]) if rows else []
    logger.info(f"Batch replace: {len(updated)} values updated, {len(skipped)} unchanged")
    return {"updated": updated, "skipped": skipped}


def set_values_active(
    ctx: PhrasebookBatchContext, value_ids: Sequence[str], is_active: bool
) -> Dict[str, Any]:
    ids = [v["id"] for v in ctx.values(value_ids)]
    ctx.set_active(ids, is_active)
    return {"updated": ctx.values(ids)}


def move_values(
    ctx: PhrasebookBatchContext, value_ids: Sequence[str], category_id: str
) -> Dict[str, Any]:
    return {"updated": ctx.move(value_ids, category_id)}


def delete_values(
    ctx: PhrasebookBatchContext, plugins, value_ids: Sequence[str]
) -> Dict[str, Any]:
    values = ctx.values(value_ids)
    for value in values:
        hook_data, blocked = _hook(
            plugins,
            PHRASEBOOK_HOOKS.before_delete,
            {
                "type": "value",
                "value_id": value["id"],
                "label": value["label"],
                "category_id": value["category_id"],
                "user_id": ctx.user_id,
            },
        )
        if blocked:
            raise BatchOperationError("blocked", hook_data.get("block_reason", "Value deletion blocked"))

    ids = [v["id"] for v in values]
    ctx.delete(ids)

    for value in values:
        _hook(
            plugins,
            PHRASEBOOK_HOOKS.after_delete,
            {"type": "value", "value_id": value["id"], "label": value["label"], "user_id": ctx.user_id},
        )
    logger.info(f"Batch delete: {len(ids)} values")
    return {"deleted": ids}
