"""Memory tools: write, read, and delete persistent LLM memory notes."""

import json
import logging
from typing import Any, Dict, Optional

from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import resolve_active_model_id, resolve_active_preset_id
from src.features.llm.tools.errors import unexpected
from src.features.llm_memory import operations as memory_operations

logger = logging.getLogger(__name__)


def _truncate(text: str, limit: int = 200) -> str:
    return text[:limit] + ("..." if len(text) > limit else "")


def _resolve_scope_ref(scope: str, scope_ref: Any, context: ToolContext) -> Any:
    """Auto-resolve scope_ref from the active preset/model when omitted."""
    if scope_ref:
        return scope_ref
    form_state = context.session_metadata.get("form_state")
    if scope == "preset":
        return resolve_active_preset_id(form_state)
    if scope == "model":
        return resolve_active_model_id(form_state, context.model_index_manager)
    return None


def _resolve_target_note(context: ToolContext, kwargs: Dict[str, Any]):
    """Resolve the note an update_memory call targets, by note_id or by
    (scope, key). Returns (note, error) -- exactly one is None.
    """
    note_id = kwargs.get("note_id")
    if note_id:
        try:
            note = memory_operations.get_note(context.llm_memory_repository, user_id=context.user_id, note_id=note_id)
        except Exception as e:
            return None, unexpected("update_memory", "fetch note", e)
        if note is None:
            return None, f"Memory note with id '{note_id}' not found"
        return note, None

    scope = kwargs.get("scope")
    key = kwargs.get("key")
    if not scope or not key:
        return None, "Provide either note_id, or both scope and key, to address the note"

    scope_ref = _resolve_scope_ref(scope, kwargs.get("scope_ref"), context)
    try:
        note = memory_operations.get_note_by_key(
            context.llm_memory_repository, user_id=context.user_id, key=key, scope=scope, scope_ref=scope_ref,
        )
    except ValueError as e:
        return None, str(e)
    except Exception as e:
        return None, unexpected("update_memory", "fetch note", e)
    if note is None:
        ref_desc = f" (scope_ref='{scope_ref}')" if scope_ref else ""
        return None, f"No memory note found with key '{key}' at scope '{scope}'{ref_desc}"
    return note, None


def _mentions(name: Any, content: str) -> bool:
    return isinstance(name, str) and bool(name.strip()) and name.strip().lower() in content.lower()


def _global_scope_nudge(context: ToolContext, content: str) -> Optional[str]:
    """A note saved at 'global' scope while the active session preset/model's
    name shows up in its own content is a candidate for over-broad scoping --
    return a teaching nudge for the caller to weigh, never rewrite the scope
    server-side (the note may genuinely be global and just mention the preset
    in passing, and silent rewriting would misfile it either way).
    """
    form_state = context.session_metadata.get("form_state")

    preset_id = resolve_active_preset_id(form_state)
    if preset_id and context.preset_manager:
        try:
            preset_data = context.preset_manager.get_preset(preset_id)
            preset_name = preset_data.get("preset", preset_data).get("name")
        except Exception:
            preset_name = None
        if _mentions(preset_name, content):
            return (
                f"this note mentions '{preset_name}' but was saved at global scope, so it will "
                f"surface on every preset, not just {preset_name}. If it only applies there, delete "
                f"this note and re-save with scope='preset' (scope_ref='{preset_id}')."
            )

    model_id = resolve_active_model_id(form_state, context.model_index_manager)
    if model_id and context.model_index_manager:
        try:
            model = context.model_index_manager.model_repo.get_by_id(model_id)
            model_name = model.display_name if model else None
        except Exception:
            model_name = None
        if _mentions(model_name, content):
            return (
                f"this note mentions '{model_name}' but was saved at global scope, so it will "
                f"surface for every model, not just {model_name}. If it only applies there, delete "
                f"this note and re-save with scope='model' (scope_ref='{model_id}')."
            )

    return None


class WriteMemoryTool(BaseTool):
    """Saves a persistent memory note for recall across chat sessions."""

    icon = "brain"

    @property
    def name(self) -> str:
        return "write_memory"

    @property
    def group(self) -> str:
        return "Memory"

    @property
    def user_description(self) -> str:
        return "Saves a note about you the assistant can recall in later chats."

    @property
    def hint(self) -> str:
        return (
            "Remember the PATTERN, not the instance — something true of the user's next ten "
            "generations, not just this one. The moments to call this: the user corrects you "
            "('no, I always want...'), repeats a request you've now seen twice, or states a "
            "preference outright. Check the notes already in context first — if one "
            "covers this topic, call update_memory with its scope and key instead of a new "
            "one. "
            "scope is REQUIRED and decides where the note surfaces again — pick the narrowest "
            "scope the fact is actually true at. Decision rule: if the fact names or only applies "
            "to one preset, model, or mode, it MUST be scoped to it; 'global' is only for facts "
            "true everywhere, on every preset and every model. Contrast: "
            "{\"scope\": \"global\", \"content\": \"prefers painterly fantasy scenes, dislikes "
            "photorealism\"} (true no matter what preset is open — global is correct); "
            "{\"scope\": \"preset\", \"content\": \"on Krea-2 Turbo, cfg 1 washes out reds — keep "
            "cfg above 2\"} (only true on that preset — scope_ref auto-resolves to the active "
            "session preset, omit it); {\"scope\": \"model\", \"scope_ref\": \"<model id>\", "
            "\"content\": \"responds well to cfg 3.5 with short prompts\"} (only true for that "
            "checkpoint). A preset's image/video mode is finer than 'preset' scope but there is no "
            "separate scope for it — name the mode in the content instead, e.g. \"in video mode, "
            "keep duration under 4s to avoid OOM\" at scope='preset'. "
            "Bad — do not save these: {\"content\": \"generated a castle at seed 1234\"} (one "
            "generation, not a pattern); {\"content\": \"user asked for a dragon\"} (a single "
            "request, not a preference). If it won't hold next week, don't save it. "
            "Saves immediately, no approval needed."
        )

    @property
    def description(self) -> str:
        return (
            "Save a persistent memory note that will be available across chat sessions. "
            "Content must describe a lasting pattern (a preference, a quirk, a habit) rather "
            "than a fact about one generation — seeds and generation ids are rejected outright, "
            "and at global scope a bare parameter dump with no descriptive text is rejected too. "
            "Notes are identified by a key and MUST be explicitly scoped: globally (true on every "
            "preset and model), to the active preset, or to the active model — pick 'global' only "
            "when the fact holds regardless of what preset or model is open; a fact that names or "
            "only applies to one preset/model must use that scope instead. Writing to an existing "
            "key updates the note. Saves immediately — no user approval required."
        )

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "A short identifier for this note (e.g. 'preferred_style', 'cfg_preference').",
                },
                "content": {
                    "type": "string",
                    "description": "The note content to remember.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["global", "preset", "model"],
                    "description": (
                        "REQUIRED, choose deliberately -- do not default to 'global'. 'global' is "
                        "only for facts true regardless of preset or model. 'preset' is for anything "
                        "tied to the active preset (e.g. a setting that only behaves this way on this "
                        "preset) -- scope_ref auto-resolves, omit it. 'model' is for anything tied to "
                        "the active checkpoint/LoRA -- scope_ref auto-resolves, omit it. If the note "
                        "names or only applies to one preset/model, it MUST use that scope."
                    ),
                },
                "scope_ref": {
                    "type": "string",
                    "description": (
                        "Preset or model ID to associate with a scoped note. "
                        "Auto-resolved from the active preset/model if omitted."
                    ),
                },
            },
            "required": ["key", "content", "scope"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Persist the memory note immediately."""
        if not context.llm_memory_repository:
            return ToolResult(success=False, data="", error="Memory manager not available")

        key = kwargs.get("key")
        content = kwargs.get("content")
        if not key:
            return ToolResult(success=False, data="", error="key is required")
        if not content:
            return ToolResult(success=False, data="", error="content is required")

        scope = kwargs.get("scope")
        if not scope:
            return ToolResult(
                success=False, data="",
                error=(
                    "scope is required -- there is no default. Choose the narrowest scope this fact "
                    "is actually true at: 'global' only if it holds regardless of preset or model, "
                    "'preset' if it names or only applies to the active preset (omit scope_ref, it "
                    "auto-resolves), 'model' if it's tied to the active checkpoint/LoRA (omit "
                    "scope_ref, it auto-resolves). Do not pick 'global' by default."
                ),
            )
        scope_ref = _resolve_scope_ref(scope, kwargs.get("scope_ref"), context)

        if scope in ("preset", "model") and not scope_ref:
            return ToolResult(
                success=False, data="",
                error=f"scope_ref is required for {scope}-scoped notes and could not be auto-resolved",
            )

        try:
            note = memory_operations.write_note(
                context.llm_memory_repository,
                user_id=context.user_id,
                key=key,
                content=content,
                scope=scope,
                scope_ref=scope_ref,
            )
            payload = {
                "action": "write_memory",
                "success": True,
                "note_id": str(note.id),
                "key": note.key,
                "scope": note.scope,
                "message": f"Saved to memory: '{note.key}'.",
            }
            if note.scope == "global":
                nudge = _global_scope_nudge(context, content)
                if nudge:
                    payload["scope_hint"] = nudge
            return ToolResult(success=True, data=json.dumps(payload))
        except ValueError as e:
            return ToolResult(success=False, data="", error=str(e))
        except Exception as e:
            logger.error(f"write_memory failed: {e}")
            return ToolResult(success=False, data="", error=unexpected("write_memory", "save", e))


class ReadMemoryTool(BaseTool):
    """Reads persistent memory notes to recall information from previous sessions."""

    icon = "brain"

    @property
    def name(self) -> str:
        return "read_memory"

    @property
    def group(self) -> str:
        return "Memory"

    @property
    def user_description(self) -> str:
        return "Reads notes the assistant saved in earlier chats."

    @property
    def hint(self) -> str:
        return (
            "Relevant memory is already injected into context automatically at the start of "
            "each conversation — call this for a scope not shown there, an explicit refresh, "
            "or to check existing notes before saving."
        )

    @property
    def description(self) -> str:
        return (
            "Read persistent memory notes from previous sessions. "
            "Can filter by scope ('global', 'preset', 'model', or 'all' for every scope) "
            "and optionally by scope_ref (preset or model ID). Returns all matching notes."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["all", "global", "preset", "model"],
                    "description": "Filter by scope. 'all' returns global, active-preset, and active-model notes.",
                    "default": "all",
                },
                "scope_ref": {
                    "type": "string",
                    "description": (
                        "Preset or model ID to filter scoped notes. "
                        "Auto-resolved from the active preset/model if omitted."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.llm_memory_repository:
            return ToolResult(success=False, data="", error="Memory manager not available")

        scope = kwargs.get("scope", "all")
        scope_ref = kwargs.get("scope_ref")
        form_state = context.session_metadata.get("form_state")
        preset_ref = scope_ref or resolve_active_preset_id(form_state)
        model_ref = scope_ref or resolve_active_model_id(form_state, context.model_index_manager)

        try:
            if scope == "all":
                all_notes = memory_operations.read_notes(
                    context.llm_memory_repository,
                    user_id=context.user_id,
                    scope="global",
                )
                if preset_ref:
                    all_notes += memory_operations.read_notes(
                        context.llm_memory_repository,
                        user_id=context.user_id,
                        scope="preset",
                        scope_ref=preset_ref,
                    )
                if model_ref:
                    all_notes += memory_operations.read_notes(
                        context.llm_memory_repository,
                        user_id=context.user_id,
                        scope="model",
                        scope_ref=model_ref,
                    )
            else:
                resolved_ref = preset_ref if scope == "preset" else model_ref if scope == "model" else None
                filter_kwargs = {"user_id": context.user_id, "scope": scope}
                if resolved_ref:
                    filter_kwargs["scope_ref"] = resolved_ref
                all_notes = memory_operations.read_notes(context.llm_memory_repository, **filter_kwargs)

            notes_data = [note.to_dict() for note in all_notes]

            return ToolResult(
                success=True,
                data=json.dumps({
                    "notes": notes_data,
                    "count": len(notes_data),
                    "scope_filter": scope,
                }),
            )
        except Exception as e:
            logger.error(f"read_memory failed: {e}")
            return ToolResult(success=False, data="", error=unexpected("read_memory", "read", e))


class DeleteMemoryTool(BaseTool):
    """Removes a persistent memory note."""

    icon = "brain"

    @property
    def name(self) -> str:
        return "delete_memory"

    @property
    def group(self) -> str:
        return "Memory"

    @property
    def user_description(self) -> str:
        return "Deletes one of the assistant's saved memory notes."

    @property
    def hint(self) -> str:
        return "When the user wants to remove a previously saved memory note"

    @property
    def description(self) -> str:
        return (
            "Delete a persistent memory note by its ID. "
            "Returns a preview of the note that will be deleted for user approval."
        )

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "The ID of the memory note to delete.",
                },
            },
            "required": ["note_id"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Proposal phase: fetch note and show preview for confirmation."""
        if not context.llm_memory_repository:
            return ToolResult(success=False, data="", error="Memory manager not available")

        note_id = kwargs.get("note_id")
        if not note_id:
            return ToolResult(success=False, data="", error="note_id is required")

        try:
            note = memory_operations.get_note(
                context.llm_memory_repository,
                user_id=context.user_id,
                note_id=note_id,
            )
        except Exception as e:
            logger.error(f"delete_memory fetch failed: {e}")
            return ToolResult(success=False, data="", error=unexpected("delete_memory", "fetch note", e))

        if note is None:
            return ToolResult(
                success=False, data="",
                error=f"Memory note with id '{note_id}' not found",
            )

        proposal = {
            "note_id": note_id,
            "key": note.key,
            "content": note.content[:200] + ("..." if len(note.content) > 200 else ""),
            "scope": note.scope,
        }
        if note.scope_ref:
            proposal["scope_ref"] = note.scope_ref

        preview = ToolApprovalPreview(
            action="Delete memory note",
            target=f"'{note.key}'",
            summary=_truncate(note.content),
        )

        return ToolResult(
            success=True,
            data=json.dumps({
                "action": "delete_memory",
                "proposal": proposal,
                "message": "The following memory note will be permanently deleted. Please confirm.",
            }),
            preview=preview,
        )

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        """Mutation phase: permanently delete the note."""
        if not context.llm_memory_repository:
            return ToolResult(success=False, data="", error="Memory manager not available")

        note_id = kwargs.get("note_id")
        if not note_id:
            return ToolResult(success=False, data="", error="note_id is required")

        try:
            success = memory_operations.delete_note(
                context.llm_memory_repository,
                user_id=context.user_id,
                note_id=note_id,
            )
            if not success:
                return ToolResult(
                    success=False, data="",
                    error=f"Memory note with id '{note_id}' could not be deleted (not found)",
                )
            return ToolResult(
                success=True,
                data=json.dumps({
                    "action": "delete_memory",
                    "success": True,
                    "note_id": note_id,
                    "message": f"Memory note {note_id} deleted successfully.",
                }),
            )
        except Exception as e:
            logger.error(f"delete_memory delete failed: {e}")
            return ToolResult(success=False, data="", error=unexpected("delete_memory", "delete", e))


class UpdateMemoryTool(BaseTool):
    """Edits an existing persistent memory note's key/content in place."""

    icon = "brain"

    @property
    def name(self) -> str:
        return "update_memory"

    @property
    def group(self) -> str:
        return "Memory"

    @property
    def user_description(self) -> str:
        return "Edits one of the assistant's saved memory notes."

    @property
    def hint(self) -> str:
        return (
            "Address the note by the scope and key you already see in context — no need to "
            "read_memory first. Prefer this over delete_memory + write_memory when correcting "
            "or refining an existing note. The user approves before anything changes."
        )

    @property
    def description(self) -> str:
        return (
            "Edit an existing persistent memory note, changing its key and/or content. Address "
            "the note either by 'note_id', or by 'scope' + 'key' (the address shown alongside "
            "every note already injected into your context) — no need to call read_memory first. "
            "At least one of 'new_key' or 'content' must be given. Requires user approval, "
            "showing the note's current values alongside the proposed change."
        )

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": (
                        "The ID of the memory note to edit, if known. Usually unnecessary — "
                        "prefer 'scope' + 'key' to address the note directly."
                    ),
                },
                "scope": {
                    "type": "string",
                    "enum": ["global", "preset", "model"],
                    "description": (
                        "The scope of the note to edit, as shown alongside it in your injected "
                        "memory context. Required (with 'key') when note_id is not given."
                    ),
                },
                "key": {
                    "type": "string",
                    "description": (
                        "The note's CURRENT key, as shown in your injected memory context. "
                        "Required (with 'scope') when note_id is not given."
                    ),
                },
                "scope_ref": {
                    "type": "string",
                    "description": (
                        "Preset or model ID for a scoped note. Auto-resolved from the active "
                        "preset/model if omitted."
                    ),
                },
                "new_key": {
                    "type": "string",
                    "description": "New key to rename this note to. Omit to keep the current key.",
                },
                "content": {
                    "type": "string",
                    "description": "New note content. Omit to keep the current content.",
                },
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Proposal phase: fetch the note and show an old -> new preview for confirmation."""
        if not context.llm_memory_repository:
            return ToolResult(success=False, data="", error="Memory manager not available")

        new_key = kwargs.get("new_key")
        new_content = kwargs.get("content")
        if not new_key and not new_content:
            return ToolResult(
                success=False, data="",
                error="At least one of 'new_key' or 'content' must be given to update the note",
            )

        note, error = _resolve_target_note(context, kwargs)
        if error:
            return ToolResult(success=False, data="", error=error)

        resolved_key = new_key or note.key
        resolved_content = new_content or note.content

        items = []
        items.append(f"key: '{note.key}' -> '{resolved_key}'" if new_key else f"key: '{note.key}' (unchanged)")
        items.append(
            f"content: '{_truncate(note.content)}' -> '{_truncate(resolved_content)}'"
            if new_content else "content: (unchanged)"
        )

        preview = ToolApprovalPreview(
            action="Edit memory note",
            target=f"'{note.key}'",
            items=items,
        )

        return ToolResult(
            success=True,
            data=json.dumps({
                "action": "update_memory",
                "note_id": note.id,
                "old": {"key": note.key, "content": note.content, "scope": note.scope},
                "new": {"key": resolved_key, "content": resolved_content},
                "message": "The following memory note will be updated. Please confirm.",
            }),
            preview=preview,
        )

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        """Mutation phase: apply the confirmed key/content update."""
        if not context.llm_memory_repository:
            return ToolResult(success=False, data="", error="Memory manager not available")

        note, error = _resolve_target_note(context, kwargs)
        if error:
            return ToolResult(success=False, data="", error=error)

        new_key = kwargs.get("new_key") or note.key
        new_content = kwargs.get("content") or note.content

        try:
            updated = memory_operations.update_note(
                context.llm_memory_repository,
                user_id=context.user_id,
                note_id=note.id,
                key=new_key,
                content=new_content,
            )
            if updated is None:
                return ToolResult(
                    success=False, data="",
                    error=f"Memory note with id '{note.id}' could not be updated (not found)",
                )
            return ToolResult(
                success=True,
                data=json.dumps({
                    "action": "update_memory",
                    "success": True,
                    "note_id": note.id,
                    "key": updated.key,
                    "message": f"Memory note {note.id} updated successfully.",
                }),
            )
        except ValueError as e:
            return ToolResult(success=False, data="", error=str(e))
        except Exception as e:
            logger.error(f"update_memory update failed: {e}")
            return ToolResult(success=False, data="", error=unexpected("update_memory", "update", e))
