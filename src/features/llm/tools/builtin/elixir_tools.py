import inspect
import json
from typing import Any, Dict, List, Optional

from src.features.elixirs.errors import AssistantRefused
from src.features.elixirs.contact_sheet import contact_sheet, load_tile, located
from src.features.elixirs.identity import asset_key
from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.operation_args import operations_from

ELIXIRS_MODE = "elixirs"
NO_SERVICE = "Elixirs are not available on this server."
NO_OPERATIONS = "No operations provided. Pass 'operations', a list like [{\"op\": \"set_settings\", \"step\": \"n1\", \"fields\": {\"steps\": 30}}]."
CALL_EXAMPLE = (
    '<tool_call>{"name": "edit_elixir", "arguments": {"revision": 4, "operations": '
    '[{"op": "set_settings", "step": "n1", "fields": {"steps": 30}}, '
    '{"op": "set_writer", "step": "n2", "instruction": "Describe the product in one sentence."}]}}</tool_call>'
)
NO_VISION_NOTE = (
    "This chat's model cannot see images, so only the outputs' details are listed. "
    "Ask the user which outputs to keep before you pick."
)
MAX_SHOWN = 12


def elixir_id_of(context: ToolContext) -> Optional[str]:
    bound = context.session_metadata.get("elixir") if isinstance(context.session_metadata, dict) else None
    value = bound.get("elixir_id") if isinstance(bound, dict) else None
    return value if isinstance(value, str) and value else None


def _failure(message: str) -> ToolResult:
    return ToolResult(success=False, data="", error=message)


def _value(value: Any) -> str:
    if value is None:
        return "(not set)"
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= 120 else text[:119] + "…"


def _row_text(row: Dict[str, Any]) -> str:
    old, new = row.get("old_value"), row.get("new_value")
    if old is None and new is not None and row["field_name"].startswith("new"):
        return f"{row['step']}: {row['field_name']} — {new}"
    if new is None and row["field_name"].startswith("remove"):
        return f"{row['step']}: remove {old}"
    return f"{row['step']}: {row['field_name']} {_value(old)} → {_value(new)}"


async def _supports_vision(context: ToolContext) -> bool:
    repository, llm_id = context.llm_repository, context.llm_id
    if repository is None or not llm_id:
        return False
    try:
        config = repository.get_configuration(llm_id)
        if inspect.isawaitable(config):
            config = await config
    except Exception:
        return False
    return bool(config and getattr(config, "supports_vision", False))


class _ElixirTool(BaseTool):
    modes = [ELIXIRS_MODE]
    icon = "flask"
    group = "Generation"

    def service(self, context: ToolContext):
        return context.elixirs

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if self.service(context) is None:
            return _failure(NO_SERVICE)
        try:
            return await self.run(context, elixir_id_of(context), kwargs)
        except AssistantRefused as refused:
            return _failure(str(refused))

    async def run(self, context: ToolContext, elixir_id: Optional[str], kwargs: Dict[str, Any]) -> ToolResult:
        raise NotImplementedError


class GetElixirTool(_ElixirTool):
    name = "get_elixir"
    read_only = True
    user_description = "Reads the open elixir: its steps, connections, problems and last brew."

    @property
    def description(self) -> str:
        return (
            "Read the elixir open in the user's Elixir tab: its revision, each step (id, kind preset/writer/files, "
            "label, column left to right, preset and mode, session mode follow/copy/new with the session's name, "
            "a writer's instruction and writes_for), the connections, its problems, the last brew and the steps "
            "waiting for a choice. Pass include_presets=true to list the preset modes an elixir can use (what each "
            "takes and makes). Call it before any edit; edit_elixir needs its revision."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "include_presets": {"type": "boolean", "description": "Also list the preset modes that can be added as steps."},
            },
            "required": [],
        }

    async def run(self, context, elixir_id, kwargs) -> ToolResult:
        summary = self.service(context).summary(context.user_id, elixir_id, include_presets=bool(kwargs.get("include_presets")))
        return ToolResult(success=True, data=json.dumps(summary))


class GetStepSettingsTool(_ElixirTool):
    name = "get_step_settings"
    read_only = True
    user_description = "Reads one elixir step's settings."

    @property
    def description(self) -> str:
        return (
            "Read one step's effective settings: a preset step's prompt, negative prompt and form fields with "
            "their values and types (a following step reads its session), or a writer's full instruction and "
            "chips. Pass 'fields' to read only some form fields."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "step": {"type": "string", "description": "The step id from get_elixir."},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Only these form fields."},
            },
            "required": ["step"],
        }

    async def run(self, context, elixir_id, kwargs) -> ToolResult:
        names = kwargs.get("fields")
        result = self.service(context).step_settings(context.user_id, elixir_id, kwargs.get("step"), names if isinstance(names, list) else None)
        return ToolResult(success=True, data=json.dumps(result))


class GetChoiceOutputsTool(_ElixirTool):
    name = "get_choice_outputs"
    read_only = True
    user_description = "Shows the outputs of a step that waits for you to choose which ones continue."

    @property
    def description(self) -> str:
        return (
            "List the outputs of a step waiting for a choice in the latest brew: each output's number, id, type "
            "and whether it is picked, per step it can go to. When this chat's model can see images you also get "
            "one image with the outputs numbered; otherwise ask the user which to keep. Pass step when several "
            "steps wait, offset to page past the first 12."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "step": {"type": "string", "description": "The waiting step's id. Optional when only one step waits."},
                "to_step": {"type": "string", "description": "The step the outputs go to, when the waiting step feeds several."},
                "offset": {"type": "integer", "description": "Outputs to skip. Defaults to 0."},
            },
            "required": [],
        }

    async def run(self, context, elixir_id, kwargs) -> ToolResult:
        service = self.service(context)
        lane = service.lane_for(context.user_id, elixir_id, kwargs.get("step"), kwargs.get("to_step"))
        offset = kwargs.get("offset") if isinstance(kwargs.get("offset"), int) and kwargs.get("offset") > 0 else 0
        entries = lane["eligible"]
        picked = set(lane["picked"])
        listed = []
        for number, entry in enumerate(entries[offset:offset + MAX_SHOWN], start=offset + 1):
            row = {"number": number, "id": asset_key(entry), "type": entry.get("media_type"), "picked": asset_key(entry) in picked}
            if entry.get("original_filename"):
                row["file"] = entry["original_filename"]
            if entry.get("media_type") == "text":
                row["text"] = (entry.get("text") or "")[:400]
            listed.append(row)
        payload: Dict[str, Any] = {
            "step": lane["step"],
            "to": lane["to"],
            "outputs": listed,
            "total": len(entries),
            "picked_count": len(picked),
            "has_more": offset + len(listed) < len(entries),
        }
        if lane.get("error"):
            payload["problem"] = lane["error"]
        image = None
        media = [(row["number"], entries[row["number"] - 1]) for row in listed if row["type"] in ("image", "video")]
        if media:
            if await _supports_vision(context):
                image = self._sheet(service, context, media)
                payload["image"] = "The attached image shows these outputs; each tile's number matches 'number'." if image else "The outputs could not be read to show them."
            else:
                payload["note"] = NO_VISION_NOTE
        return ToolResult(success=True, data=json.dumps(payload), image_data=image)

    @staticmethod
    def _sheet(service, context: ToolContext, media) -> Optional[str]:
        root = service.storage_root() if service.storage_root else context.storage_dir()
        if not root:
            return None
        tiles = []
        for number, entry in media:
            resolved = service.resolve_media(context.user_id, entry)
            path = located(root, (resolved or {}).get("value"))
            tiles.append((number, load_tile(path, entry.get("media_type")) if path else None))
        return contact_sheet(tiles)


class EditElixirTool(_ElixirTool):
    name = "edit_elixir"
    user_description = "Changes the open elixir's steps, connections and settings after you approve."
    requires_approval = True

    @property
    def description(self) -> str:
        return (
            "Change the open elixir in one batch; the user approves before anything is saved, and a rejected "
            "operation cancels the whole batch. Pass the revision from get_elixir. Operations: "
            "add_step {after: step id or null for a new lane, preset_id, mode, title?, with_writer?}; "
            "add_start {before: a lane's first step, preset_id+mode, or kind 'writer'}; "
            "insert_between {before: the later step, preset_id, mode}; "
            "add_writer {for_step, instruction?}; remove {step}; "
            "connect {from, to, port?, from_port?} (a writer's text goes to 'prompt' or 'negative_prompt'); "
            "set_settings {step, fields: {name: value}, prompt?, negative_prompt?} (a following step saves into "
            "its session, a copy or new step keeps the change on the step); "
            "set_writer {step, instruction?, variables?, writes_for?} (${name} uses a writer variable, {a|b} is an "
            "inline choice; variables merge by name, null removes: {\"act\": {\"type\": \"choice\", \"options\": "
            "[\"run\", \"jump\"], \"mode\": \"shuffle\"}}; never send phrasebook chips); "
            "set_session {step, mode: follow|copy|new, session?: saved session name or id}. "
            "Preset modes: get_elixir with include_presets=true; field names: get_step_settings."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "revision": {"type": "integer", "description": "The elixir revision get_elixir returned."},
                "operations": {
                    "type": "array",
                    "description": "Changes applied in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": ["add_step", "add_start", "insert_between", "add_writer", "remove", "connect", "set_settings", "set_writer", "set_session"],
                            },
                            "step": {"type": "string"},
                            "after": {"type": ["string", "null"]},
                            "before": {"type": "string"},
                            "for_step": {"type": "string"},
                            "from": {"type": "string"},
                            "to": {"type": "string"},
                            "port": {"type": "string"},
                            "from_port": {"type": "string"},
                            "preset_id": {"type": "string"},
                            "mode": {"type": "string"},
                            "kind": {"type": "string", "enum": ["preset", "writer"]},
                            "title": {"type": "string"},
                            "with_writer": {"type": "boolean"},
                            "fields": {"type": "object"},
                            "prompt": {"type": "string"},
                            "negative_prompt": {"type": "string"},
                            "instruction": {"type": "string"},
                            "variables": {"type": "object"},
                            "writes_for": {"type": ["string", "null"]},
                            "session": {"type": "string"},
                        },
                        "required": ["op"],
                    },
                },
            },
            "required": ["revision", "operations"],
        }

    def _operations(self, kwargs: Dict[str, Any]) -> List[Any]:
        operations, problem = operations_from(kwargs.get("operations"), CALL_EXAMPLE, NO_OPERATIONS)
        if problem:
            raise AssistantRefused(problem)
        return operations

    async def run(self, context, elixir_id, kwargs) -> ToolResult:
        prepared = self.service(context).prepare(context.user_id, elixir_id, kwargs.get("revision"), self._operations(kwargs))
        rows = prepared.plan.rows
        if not rows:
            return _failure("These operations change nothing. Check the step ids and values with get_elixir.")
        errors = [problem["message"] for problem in prepared.problems if problem.get("severity") == "error"]
        note = None
        if errors:
            note = f"After this change the elixir has {len(errors)} problem{'s' if len(errors) != 1 else ''}: " + "; ".join(errors[:3])
        followed = [write.session_name for write in prepared.plan.session_writes.values()]
        if followed:
            saved_to = f"Saves into the followed session{'s' if len(followed) > 1 else ''} {', '.join(sorted(set(followed)))}."
            note = f"{saved_to} {note}" if note else saved_to
        preview = ToolApprovalPreview(
            action="Edit elixir",
            target=prepared.record.name,
            items=[_row_text(row) for row in rows],
            note=note,
            summary=f"{len(rows)} change{'s' if len(rows) != 1 else ''} to {prepared.record.name}",
        )
        data = {"status": "pending_approval", "revision": prepared.record.revision, "proposed_changes": rows, "problems_after": prepared.problems[:10]}
        return ToolResult(success=True, data=json.dumps(data), preview=preview)

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        service = self.service(context)
        if service is None:
            return _failure(NO_SERVICE)
        try:
            applied = service.apply(context.user_id, elixir_id_of(context), kwargs.get("revision"), self._operations(kwargs))
        except AssistantRefused as refused:
            return _failure(str(refused))
        payload = {
            "status": "applied",
            "revision": applied["revision"],
            "applied_changes": applied["rows"],
            "summary": f"Applied {len(applied['rows'])} change{'s' if len(applied['rows']) != 1 else ''}. The elixir is now at revision {applied['revision']}.",
            "problems": applied["problems"],
        }
        if applied["sessions_written"]:
            payload["sessions_written"] = applied["sessions_written"]
        return ToolResult(success=True, data=json.dumps(payload))


class ChooseOutputsTool(_ElixirTool):
    name = "choose_outputs"
    user_description = "Picks which outputs of a waiting step continue, after you approve. It never continues the brew."
    requires_approval = True

    @property
    def description(self) -> str:
        return (
            "Pick which outputs of a step waiting for a choice go on to the next step, by the ids or numbers from "
            "get_choice_outputs. The pick replaces the current one. It does not continue the brew: tell the user "
            "to press Continue."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "step": {"type": "string", "description": "The waiting step's id. Optional when only one step waits."},
                "to_step": {"type": "string", "description": "The step the outputs go to, when the waiting step feeds several."},
                "outputs": {
                    "type": "array",
                    "items": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
                    "description": "Output ids or numbers to keep.",
                },
            },
            "required": ["outputs"],
        }

    async def run(self, context, elixir_id, kwargs) -> ToolResult:
        service = self.service(context)
        lane = service.lane_for(context.user_id, elixir_id, kwargs.get("step"), kwargs.get("to_step"))
        picked = service.resolve_picks(lane, kwargs.get("outputs"))
        numbers = {asset_key(entry): index for index, entry in enumerate(lane["eligible"], start=1)}
        names = {asset_key(entry): entry.get("original_filename") for entry in lane["eligible"]}
        preview = ToolApprovalPreview(
            action="Pick outputs",
            target=f"at {lane['step']} for {', '.join(lane['to'])}",
            items=[f"#{numbers[key]} {names.get(key) or key}" for key in picked],
            note=f"{len(picked)} of {lane['output_count']} outputs. Continue stays with you.",
            summary=f"Pick {len(picked)} of {lane['output_count']} outputs",
        )
        data = {"status": "pending_approval", "step": lane["step"], "to": lane["to"], "picked": picked}
        return ToolResult(success=True, data=json.dumps(data), preview=preview)

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        service = self.service(context)
        if service is None:
            return _failure(NO_SERVICE)
        try:
            chosen = service.choose(context.user_id, elixir_id_of(context), kwargs.get("step"), kwargs.get("outputs"), kwargs.get("to_step"))
        except AssistantRefused as refused:
            return _failure(str(refused))
        chosen["status"] = "applied"
        chosen["summary"] = f"Picked {chosen['count']} of {chosen['of']} outputs at {chosen['step']}. Tell the user to press Continue when ready."
        return ToolResult(success=True, data=json.dumps(chosen))


ELIXIR_TOOLS = (GetElixirTool, GetStepSettingsTool, GetChoiceOutputsTool, EditElixirTool, ChooseOutputsTool)
