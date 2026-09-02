"""Hook points owned by the phrasebook domain."""

from src.platform.plugins.hooks import hooks_registry

PHRASEBOOK_HOOKS = hooks_registry.declare(
    "phrasebook", "backend",
    "before_import", "after_import",
    "before_create", "after_create",
    "before_update", "after_update",
    "before_delete", "after_delete",
    "batch.before", "batch.after",
    "find.results",
    specs={
        "batch.before": {
            "description": (
                "Fired before a phrasebook batch operation (core replace/set_active/"
                "move/delete or a plugin-registered op) runs over the selected values. "
                "A handler may rewrite `params` or `value_ids`, or veto the run by "
                "setting `blocked` (with `block_reason`), which answers 400 `blocked`."
            ),
            "payload": {
                "op": {"type": "str", "description": "Registered operation id (e.g. 'replace')"},
                "value_ids": {"type": "list[str]", "description": "Selected value ids, deduplicated, all owned by user_id"},
                "params": {"type": "dict", "description": "Operation parameters as posted by the client"},
                "user_id": {"type": "str", "description": "User running the operation"},
            },
            "mutable": ["params", "value_ids"],
            "use_when": [
                "Restrict or rewrite what a batch tool may do (e.g. forbid deleting values in a locked category)",
                "Audit bulk edits before they happen",
            ],
            "example": (
                "def on_batch_before(context: HookContext) -> HookContext:\n"
                "    if context.data['op'] == 'delete':\n"
                "        context.data['blocked'] = True\n"
                "        context.data['block_reason'] = 'Deleting is disabled'\n"
                "    return context\n"
            ),
        },
        "batch.after": {
            "description": "Fired after a phrasebook batch operation committed; observe-only.",
            "payload": {
                "op": {"type": "str", "description": "Registered operation id"},
                "value_ids": {"type": "list[str]", "description": "Value ids the operation ran over"},
                "params": {"type": "dict", "description": "Operation parameters (after batch.before)"},
                "user_id": {"type": "str", "description": "User who ran the operation"},
                "outcome": {"type": "dict", "description": "BatchOutcome as a dict: updated, skipped, deleted, message"},
            },
            "use_when": [
                "Sync bulk phrasebook edits to an external store",
                "Record metrics on batch tool usage",
            ],
            "example": (
                "def on_batch_after(context: HookContext) -> HookContext:\n"
                "    logger.info(f\"{context.data['op']}: {context.data['outcome']['message']}\")\n"
                "    return context\n"
            ),
        },
        "find.results": {
            "description": (
                "Fired after a Find & replace search computed its hits and before they "
                "are returned. A handler may annotate, reorder, drop or append entries "
                "in `categories` / `values`; each value hit carries its `matches` spans."
            ),
            "payload": {
                "query": {"type": "str", "description": "Trimmed search text"},
                "mode": {"type": "str", "description": "'contains' | 'word' | 'regex'"},
                "case_sensitive": {"type": "bool", "description": "Whether the match was case-sensitive"},
                "scope": {"type": "str", "description": "'all' | 'values' | 'categories'"},
                "include_inactive": {"type": "bool", "description": "Whether inactive rows were searched"},
                "path_prefix": {"type": "str", "description": "Category subtree restriction, '' for everywhere"},
                "fields": {"type": "list[str]", "description": "Value fields searched ('label', 'value')"},
                "limit": {"type": "int", "description": "Per-kind result cap"},
                "user_id": {"type": "str", "description": "Searching user"},
                "categories": {"type": "list[dict]", "description": "Category hits with `matches` spans"},
                "values": {"type": "list[dict]", "description": "Value hits with category info and `matches` spans"},
                "total_categories": {"type": "int", "description": "Category hit count before the limit"},
                "total_values": {"type": "int", "description": "Value hit count before the limit"},
            },
            "mutable": ["categories", "values"],
            "use_when": [
                "Append hits from an external phrase source",
                "Hide or flag values a plugin owns",
            ],
            "example": (
                "def on_find_results(context: HookContext) -> HookContext:\n"
                "    context.data['values'] = [v for v in context.data['values'] if not v['label'].startswith('_')]\n"
                "    return context\n"
            ),
        },
    },
)
