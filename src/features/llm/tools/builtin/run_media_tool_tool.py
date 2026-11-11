import asyncio
import json
from typing import Any, Dict, Hashable, List, Optional, Tuple, Union

from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.errors import teach
from src.features.llm.tools.tool_call_rescue import decode_payload, demangle_quote_tokens
from src.features.media_tools.dto import MediaToolItem, RunResult
from src.features.media_tools.service import MAX_INPUTS, MAX_STEPS
from src.features.plans.errors import LimitExceeded
from src.platform.http.errors import DomainError
from src.platform.media_tools import MediaToolRegistry, ToolSpec
from src.platform.media_tools.params import ANCHORS

RUN_TIMEOUT_SECONDS = 600.0
MODES = ("new", "replace")
DESCRIPTION_LIMIT = 90
DESCRIBED_PARAM_TYPES = ("json", "media", "string")
MAX_RESULT_ITEMS = 20
CALL_EXAMPLE = (
    '{"steps": [{"tool": "trim", "params": {"start": 0, "end": 5}}], '
    '"items": [{"item_id": "<library item id>"}]}'
)

INTRO = (
    f"Edit the user's images, videos and audio: one tool or a chain of up to {MAX_STEPS} steps run in "
    "order, each applied to every item (an item must be a kind the step accepts). Name items by "
    "item_id (a library item{{#if list_library_items}}, from list_library_items{{/if}}) or "
    "generation_file_id (a generated file{{#if search_gallery}}, the file_id from search_gallery{{/if}}). "
    "mode 'new' (default) saves results as new library items and keeps the originals; 'replace' "
    "overwrites ONE library item (not a generated file) and needs the user's approval. Returns the "
    "resulting items (id, filename, url, kind, size). Tools, id (accepts -> produces): params as "
    "name type[min..max]unit; =x is the default, ? is optional, otherwise required."
)


def _number(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _range_text(param: Dict[str, Any]) -> str:
    low, high = param.get("min"), param.get("max")
    if low is None and high is None:
        return ""
    return f"[{'' if low is None else _number(low)}..{'' if high is None else _number(high)}]"


def _type_text(param: Dict[str, Any]) -> str:
    kind = param.get("type")
    if kind == "select":
        options = param.get("options") or []
        return "|".join(
            _number(option.get("value") if isinstance(option, dict) else option) for option in options
        )
    if kind in ("integer", "float"):
        label = "int" if kind == "integer" else "num"
        return f"{label}{_range_text(param)}{param.get('unit') or ''}"
    if kind == "checkbox":
        return "bool"
    if kind == "color":
        return "color|transparent" if param.get("allow_transparent") else "color"
    if kind == "string":
        return "text"
    return str(kind)


def _when_text(when: Any) -> str:
    if isinstance(when, list):
        return " and ".join(part for part in (_when_text(item) for item in when) if part)
    if not isinstance(when, dict) or "field" not in when:
        return "conditional" if when else ""
    field = when["field"]
    operator = when.get("operator")
    value = when.get("value")
    for key in ("equals", "not_equals", "in", "not_in"):
        if key in when:
            operator, value = key, when[key]
            break
    if operator in ("equals", "not_equals"):
        return f"if {field}{'=' if operator == 'equals' else '!='}{_number(value)}"
    if operator in ("in", "not_in") and isinstance(value, list):
        word = "in" if operator == "in" else "not in"
        return f"if {field} {word} {'|'.join(_number(item) for item in value)}"
    return "conditional"


def _param_text(param: Dict[str, Any]) -> str:
    has_default = "default" in param
    default = param.get("default")
    marker = "?" if has_default and default is None else ""
    text = f"{param['name']}{marker} {_type_text(param)}"
    if has_default and default is not None:
        text += f" ={_number(default)}"
    when = _when_text(param.get("when"))
    if when:
        text += f" ({when})"
    description = " ".join(str(param.get("description") or "").split())
    if description and param.get("type") in DESCRIBED_PARAM_TYPES:
        text += f" - {description[:DESCRIPTION_LIMIT]}"
    return text


def _span_text(spec: ToolSpec) -> str:
    low, high = spec.selection_bounds()
    if high is None:
        return f"{low}+"
    return f"{low}" if low == high else f"{low}-{high}"


def _notes(spec: ToolSpec) -> List[str]:
    notes: List[str] = []
    if spec.cardinality == "one_to_many":
        notes.append("splits one item into several")
    elif spec.cardinality == "many_to_one":
        notes.append(f"joins {_span_text(spec)} items into one")
    requires = spec.requires or {}
    if requires.get("has_alpha"):
        notes.append("needs transparency")
    if requires.get("has_audio"):
        notes.append("needs an audio track")
    if requires.get("min_duration"):
        notes.append(f"needs at least {_number(requires['min_duration'])}s")
    if spec.description:
        notes.append(" ".join(spec.description.split())[:DESCRIPTION_LIMIT * 2])
    return notes


def _tool_line(spec: ToolSpec) -> str:
    produces = "" if spec.produces == "same" else f" -> {spec.produces}"
    params = "; ".join(_param_text(param) for param in spec.params) or "no params"
    line = f"- {spec.id} ({'|'.join(spec.accepts)}{produces}): {params}"
    notes = _notes(spec)
    return f"{line}. {'; '.join(notes)}" if notes else line


def describe_tools(specs: List[ToolSpec]) -> str:
    lines = [_tool_line(spec) for spec in specs]
    if any(param.get("type") == "anchor" for spec in specs for param in spec.params):
        lines.append(f"anchor values: {' '.join(ANCHORS)}")
    return "\n".join(lines)


def _objects(raw: Any, name: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    if isinstance(raw, str):
        raw, _ = decode_payload(demangle_quote_tokens(raw))
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw or not all(isinstance(entry, dict) for entry in raw):
        return None, teach(
            f"'{name}' must be a non-empty array of objects",
            f"The whole call looks like this: {CALL_EXAMPLE}",
        )
    return raw, None


def _step(entry: Dict[str, Any], index: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    tool = entry.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        return None, teach(f"Step {index + 1} has no 'tool'", "Every step is {\"tool\": <id>, \"params\": {...}}")
    params = entry.get("params")
    if isinstance(params, str):
        params, _ = decode_payload(demangle_quote_tokens(params))
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None, teach(f"Step {index + 1} 'params' must be an object", "Omit it to use the defaults")
    return {"tool": tool.strip(), "params": params}, None


def _input(entry: Dict[str, Any]) -> Dict[str, str]:
    return {
        key: str(entry[key]).strip()
        for key in ("item_id", "generation_file_id")
        if entry.get(key) not in (None, "")
    }


def _item_payload(item: MediaToolItem) -> Dict[str, Any]:
    entry = {
        "id": item.id,
        "filename": item.original_filename or item.filename,
        "url": item.url,
        "kind": item.media_type,
        "size": item.size,
        "width": item.width,
        "height": item.height,
        "duration_seconds": item.duration_seconds,
    }
    return {key: value for key, value in entry.items() if value is not None}


class RunMediaToolTool(BaseTool):
    modes = ["generation", "history"]
    icon = "scissors"
    context_group = "media"

    name = "run_media_tool"
    mcp = True
    spends = True
    group = "Generation"
    requires_approval = True
    approval_per_call = True
    user_description = "Edits images, videos and audio in your library, such as trim, crop, resize, rotate or speed."

    def __init__(self, registry: MediaToolRegistry):
        self._registry = registry

    def _runnable(self) -> List[ToolSpec]:
        return [spec for spec in self._registry.list() if spec.impl is not None]

    def schema_revision(self) -> Optional[Hashable]:
        return self.description

    @property
    def description(self) -> str:
        return f"{INTRO}\n{describe_tools(self._runnable())}"

    @property
    def parameters(self) -> Dict[str, Any]:
        tool_ids = [spec.id for spec in self._runnable()]
        tool_field: Dict[str, Any] = {"type": "string", "description": "Tool id from the list."}
        if tool_ids:
            tool_field["enum"] = tool_ids
        return {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_STEPS,
                    "description": "Steps in the order they run.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": tool_field,
                            "params": {
                                "type": "object",
                                "description": "Params named in the tool list; omit for the defaults.",
                            },
                        },
                        "required": ["tool"],
                    },
                },
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_INPUTS,
                    "description": "The media to edit; each entry has exactly one of item_id or generation_file_id.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_id": {"type": "string", "description": "Library item id."},
                            "generation_file_id": {"type": "string", "description": "Generated file id."},
                        },
                    },
                },
                "mode": {
                    "type": "string",
                    "enum": list(MODES),
                    "default": "new",
                    "description": "'new' keeps the original; 'replace' overwrites one library item and needs approval.",
                },
            },
            "required": ["steps", "items"],
        }

    def approval_required(self, arguments: Dict[str, Any]) -> bool:
        return str((arguments or {}).get("mode") or "new").strip().lower() == "replace"

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        request = self._request(context, kwargs)
        if isinstance(request, ToolResult):
            return request
        steps, inputs, mode = request
        if mode == "replace":
            return await self._propose(context, steps, inputs)
        return await self._run(context, steps, inputs, mode)

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        request = self._request(context, kwargs)
        if isinstance(request, ToolResult):
            return request
        steps, inputs, mode = request
        return await self._run(context, steps, inputs, mode)

    def _request(
        self, context: ToolContext, kwargs: Dict[str, Any]
    ) -> Union[ToolResult, Tuple[List[Dict[str, Any]], List[Dict[str, str]], str]]:
        if context.media_tool_service is None:
            return ToolResult(success=False, data="", error="Media tools are not available on this server.")
        mode = str(kwargs.get("mode") or "new").strip().lower()
        if mode not in MODES:
            return ToolResult(
                success=False, data="", error=teach(f"'mode' {mode!r} is not valid", "Use 'new' or 'replace'")
            )
        raw_steps, problem = _objects(kwargs.get("steps"), "steps")
        if problem:
            return ToolResult(success=False, data="", error=problem)
        steps: List[Dict[str, Any]] = []
        for index, entry in enumerate(raw_steps):
            step, problem = _step(entry, index)
            if problem:
                return ToolResult(success=False, data="", error=problem)
            steps.append(step)
        raw_items, problem = _objects(kwargs.get("items"), "items")
        if problem:
            return ToolResult(success=False, data="", error=problem)
        return steps, [_input(entry) for entry in raw_items], mode

    async def _run(
        self, context: ToolContext, steps: List[Dict[str, Any]], inputs: List[Dict[str, str]], mode: str
    ) -> ToolResult:
        try:
            result = await context.media_tool_service.run_sync(
                context.user_id, steps, inputs, {"mode": mode}, timeout=RUN_TIMEOUT_SECONDS
            )
        except DomainError as exc:
            return ToolResult(success=False, data="", error=self._failure(exc.code, exc.message, exc.extra))
        except LimitExceeded as exc:
            return ToolResult(success=False, data="", error=teach("The library limit was reached", str(exc)))
        if not result.ok:
            return ToolResult(success=False, data="", error=self._run_failure(result))
        total = len(result.items)
        items = [_item_payload(item) for item in result.items[:MAX_RESULT_ITEMS]]
        if result.summary is not None and not total:
            message = result.summary.message
        elif result.replaced:
            message = "Replaced the original library item with the edited version."
        else:
            message = f"Saved {total} new library item(s); the originals are unchanged."
        payload: Dict[str, Any] = {
            "status": "completed", "replaced": result.replaced, "count": total, "items": items, "message": message,
        }
        if total > len(items):
            payload["truncated"] = True
            payload["message"] += f" Only the first {len(items)} are listed."
        return ToolResult(success=True, data=json.dumps(payload))

    async def _propose(
        self, context: ToolContext, steps: List[Dict[str, Any]], inputs: List[Dict[str, str]]
    ) -> ToolResult:
        try:
            resolved = await asyncio.to_thread(
                context.media_tool_service.check, context.user_id, steps, inputs, {"mode": "replace"}
            )
        except DomainError as exc:
            return ToolResult(success=False, data="", error=self._failure(exc.code, exc.message, exc.extra))
        name = resolved[0].original_filename or "the library item"
        preview = ToolApprovalPreview(
            action="Replace original",
            target=name,
            items=[self._step_text(step) for step in steps],
            note="The original file is overwritten and cannot be restored.",
        )
        return ToolResult(
            success=True,
            data=json.dumps({
                "action": self.name,
                "proposal": {"steps": steps, "items": inputs, "mode": "replace"},
                "message": f"'{name}' will be overwritten by its edited version. Please confirm.",
            }),
            preview=preview,
        )

    def _step_text(self, step: Dict[str, Any]) -> str:
        spec = self._registry.get(step["tool"])
        label = spec.label if spec is not None else step["tool"]
        params = ", ".join(f"{key}={_number(value)}" for key, value in step["params"].items())
        return f"{label} ({params})" if params else f"{label} (default settings)"

    def _failure(self, code: str, message: str, extra: Optional[Dict[str, Any]] = None) -> str:
        extra = extra or {}
        where = []
        if extra.get("step_index") is not None:
            where.append(f"step {int(extra['step_index']) + 1}")
        if extra.get("field"):
            where.append(f"'{extra['field']}'")
        head = f"{code} ({', '.join(where)})" if where else code
        text = f"{head}: {message.rstrip('.')}."
        if code == "unknown_tool":
            return f"{text} Tools: {', '.join(spec.id for spec in self._runnable())}."
        if code == "not_found":
            return f"{text} Use an item_id from the user's library or a generation_file_id of one of their generated files."
        if code == "replace_not_allowed":
            return f"{text} Use mode 'new' instead."
        return text

    def _run_failure(self, result: RunResult) -> str:
        if result.error is None:
            return f"{result.status}: the run did not finish."
        return self._failure(result.error.code, result.error.message)
