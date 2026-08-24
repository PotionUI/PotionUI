"""Chat context assembly.

Builds everything that surrounds the raw conversation before it reaches the
model: the effective system prompt (mode prompt or explicit override) together
with the allowed tool set, and the system context blocks injected right before
the last user message (@resource snapshots, the mode's context contributor and
recalled persistent memory). Also serves @resource autocomplete suggestions.

Split out of the ChatManager coordinator; it reads its collaborators (resource
registry, memory manager, model/preset managers, chat-mode registry, tool
executor) through the manager so the composition root's late binding keeps
working.
"""

import asyncio
import inspect
import logging
import traceback
from typing import Any, Dict, List, Optional, Tuple

from src.features.chat.modes import ChatMode
from src.features.chat.reply_contract import REPLY_CONTRACT_REMINDER
from src.features.llm.tools.governance import ToolGovernanceRepository, compute_allowed_tool_names
from src.features.llm.ttl_cache import TTLCache
from src.platform.resources import ResolvedResource, ResourceContext, ResourceSuggestion

logger = logging.getLogger(__name__)

# Per-group injection caps for the recalled-memory block; the memory API
# (routes.list_memory_notes) reports these to the frontend so the panel can
# mark which notes are actually injected.
MEMORY_MAX_NOTES_PER_GROUP = 20
MEMORY_MAX_CONTENT_LEN = 500

# The resolved (prompt, allowed_names) pair is invariant for a session's life
# except that the mode prompt template can be a callable reading a live admin
# setting, and the registry's tool set can change under a plugin toggle. A short
# TTL bounds that staleness without an explicit invalidation hook.
_PROMPT_TOOLS_CACHE_TTL_SECONDS = 60.0

# Admin guidance can run long; the workspace block ships an excerpt and points at
# get_model_info for the rest so a verbose guide can't flood the context.
_WORKSPACE_MAX_GUIDANCE_CHARS = 240
_WORKSPACE_CHECKPOINT_TYPES = {"checkpoint", "diffusion_model"}

# A preset's `llm.guide` is repo-authored, not admin-typed free text, but it's
# still capped generously so a very long guide can't flood the context.
_WORKSPACE_MAX_GUIDE_CHARS = 3000
_WORKSPACE_MAX_AI_HINT_CHARS = 200

# The per-turn PROMPT STATE block is a truncated structural view; full text stays
# behind get_current_segments.
_PROMPT_STATE_CONTENT_CHARS = 100
_PROMPT_STATE_MAX_SEGMENTS = 20
_PROMPT_STATE_MAX_BLOCK_CHARS = 4000
_PROMPT_STATE_HEADER = (
    "PROMPT STATE (current editor contents; truncated — "
    "get_current_segments returns full text):"
)


def _cap_text(text: str, limit: int, suffix: str = "…") -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + suffix


def _cap_guidance(text: str, limit: int = _WORKSPACE_MAX_GUIDANCE_CHARS) -> str:
    return _cap_text(text, limit, "… (call get_model_info for the full guidance)")


def _truncate_prompt_content(text: str, limit: int) -> Tuple[str, int, bool]:
    """Whole-word truncate a segment's content; returns (shown, total_chars, truncated)."""
    compact = " ".join((text or "").split())
    total = len(compact)
    if total <= limit:
        return compact, total, False
    cut = compact[:limit]
    sp = cut.rfind(" ")
    if sp > 0:
        cut = cut[:sp]
    return cut.rstrip(), total, True


def _render_prompt_state_line(seq: int, seg: Dict[str, Any]) -> str:
    num = f"{seq:02d}"
    if (seg.get("type") or "content") == "break":
        return f"  {num} ── break ──"
    parts = [f"  {num}"]
    name = seg.get("name")
    if name:
        parts.append(str(name))
    tpl = seg.get("template")
    if isinstance(tpl, dict):
        position = tpl.get("position")
        slot = tpl.get("slot")
        if position is not None:
            parts.append(f"(slot {position})")
        elif slot:
            parts.append(f"(slot {slot})")
    parts.append("[on]" if seg.get("enabled", True) else "[off]")
    parts.append(f"id={seg.get('id') or ''}")
    prefix = " ".join(parts)
    shown, total, truncated = _truncate_prompt_content(seg.get("content") or "", _PROMPT_STATE_CONTENT_CHARS)
    if truncated:
        return f'{prefix}: "{shown}…" ({total} ch)'
    return f'{prefix}: "{shown}"'


def _format_triggers(triggers: Any) -> str:
    if isinstance(triggers, (list, tuple)):
        return ", ".join(str(t) for t in triggers)
    return str(triggers)


class ChatContextBuilder:
    """Assembles the system prompt, tool set and injected context blocks."""

    def __init__(self, manager):
        self._m = manager
        self._prompt_tools_cache: TTLCache[Tuple[Any, ...], Tuple[str, List[str]]] = TTLCache(
            _PROMPT_TOOLS_CACHE_TTL_SECONDS
        )

    def resolve_session_prompt_and_tools(
        self,
        session,
        form_state: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, List[str], ChatMode]:
        """Resolve the session's mode into a system prompt and allowed tool names.

        The mode determines the tool set (mode tools + global tools); each tool
        is then filtered through its ``is_available(form_state)`` predicate
        (declarative, e.g. a tool that only applies when a particular document
        is loaded on the form); the session's ``enabled_tools`` metadata acts as
        a further subtractive filter. The system prompt is the session's
        explicit custom message when stored, otherwise the mode prompt with the
        allowed tools' hints substituted.

        The (prompt, allowed_names) result is memoized per (mode, enabled-tools
        signature, custom system message, unavailable-tools signature, the
        session's ``llm_config_id``, governance snapshot) for a short TTL —
        see ``_PROMPT_TOOLS_CACHE_TTL_SECONDS`` — since this is otherwise
        recomputed (including a possible settings-table read for the mode's
        prompt template) on every single message. Governance
        (``src.features.llm.tools.governance``) is scoped to the session's
        LLM config, not global — the governance snapshot is a live read of
        that config's admin rows + the user's (global) opt-out rows relevant
        to this mode's tools (not a memoized value), so toggling either is
        visible on the very next call even though the *result* is still
        cached for the TTL.

        A session with no ``llm_config_id`` yet degrades to pure passthrough
        on the admin-enabled/locked axis (the user's own opt-out set still
        applies).

        Raises:
            UnknownChatModeException: If the session's mode is not registered
        """
        mode = self._m.chat_mode_registry.require(session.mode)

        session_metadata = getattr(session, 'metadata', None) or {}
        enabled = session_metadata.get('enabled_tools')  # None = all mode tools
        custom_system_message = session_metadata.get('system_message')

        registry = self._m.tool_executor.tool_registry if self._m.tool_executor else None
        mode_tools = registry.get_for_mode(mode) if registry else []
        mode_tool_names = [t.name for t in mode_tools]

        unavailable = tuple(sorted(t.name for t in mode_tools if not t.is_available(form_state)))

        # isinstance-guarded rather than a bare truthiness/None check: a
        # generic Mock() collaborator (common in tests that don't care about
        # governance) auto-vivifies any attribute access, so `getattr(...,
        # None)` alone would hand back a Mock here instead of skipping this
        # block.
        governance_repo = getattr(self._m, "tool_governance_repository", None)
        if not isinstance(governance_repo, ToolGovernanceRepository):
            governance_repo = None
        user_id = getattr(session, "user_id", None)
        # Governance is per LLM config (see src.features.llm.tools.governance) -
        # a session with no config assigned yet degrades to pure passthrough
        # on the admin-enabled/locked axis, but the user's own opt-out set is
        # global and still applies regardless.
        llm_config_id = getattr(session, "llm_config_id", None)
        if governance_repo is not None:
            governance_snapshot = (
                governance_repo.get_config_snapshot(llm_config_id, mode_tool_names) if llm_config_id else {}
            )
            user_disabled = governance_repo.get_user_disabled(user_id) if user_id else set()
        else:
            governance_snapshot = {}
            user_disabled = set()
        governed = set(compute_allowed_tool_names(mode_tool_names, governance_snapshot, user_disabled))

        cache_key = (
            mode.id,
            tuple(sorted(enabled)) if enabled is not None else None,
            custom_system_message or "",
            unavailable,
            llm_config_id,
            tuple(sorted(governance_snapshot.items())),
            tuple(sorted(user_disabled & set(mode_tool_names))),
        )
        cached = self._prompt_tools_cache.get(cache_key)
        if cached is not None:
            prompt, allowed_names = cached
            return prompt, allowed_names, mode

        allowed_names = [
            t.name for t in mode_tools
            if t.name in governed and t.name not in unavailable and (enabled is None or t.name in enabled)
        ]

        if custom_system_message:
            prompt = custom_system_message
        elif mode_tools and not allowed_names:
            # The mode has tools but the session disabled all of them — a
            # tool-centric mode prompt would mislead the model, so fall back to
            # the config default/style resolution downstream.
            prompt = ""
        else:
            hints = registry.get_tool_hints_text(allowed_names) if (registry and allowed_names) else ""
            prompt = self._m.chat_mode_registry.resolve_system_prompt(mode, hints, allowed_names)

        self._prompt_tools_cache.set(cache_key, (prompt, allowed_names))
        return prompt, allowed_names, mode

    # --- @Resource helpers ---

    def build_resource_context(
        self,
        user_id: str,
        mode_id: Optional[str],
        form_state: Optional[Dict[str, Any]] = None,
    ) -> ResourceContext:
        """Build the dependency bundle handed to resource providers."""
        return ResourceContext(
            user_id=user_id,
            mode_id=mode_id,
            model_index_manager=self._m.model_index_manager,
            phrasebook_manager=self._m.phrasebook_manager,
            preset_manager=self._m.preset_manager,
            generation_repository=self._m.generation_repository,
            generation_parameter_repository=self._m.generation_parameter_repository,
            generation_model_repository=self._m.generation_model_repository,
            form_state=form_state,
        )

    async def resolve_message_resources(
        self,
        resources: Optional[List[str]],
        user_id: str,
        mode_id: str,
        context_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[ResolvedResource]:
        """Snapshot-resolve @resource refs attached to a message. Never raises.

        ``context_metadata.form_state`` is threaded onto the ResourceContext so
        the @form provider can resolve live form values at send time.
        """
        if not resources or not self._m.resource_registry:
            return []
        form_state = (context_metadata or {}).get("form_state")
        ctx = self.build_resource_context(user_id, mode_id, form_state=form_state)
        # ResourceRegistry.resolve() never raises (a failing provider is caught
        # and turned into an "error" ResolvedResource internally), so resolving
        # concurrently preserves the exact same per-resource failure semantics
        # as the old sequential loop — just without waiting on each one in turn.
        return list(
            await asyncio.gather(*(self._m.resource_registry.resolve(uri, ctx) for uri in resources))
        )

    @staticmethod
    def resources_metadata(resolved: List[ResolvedResource]) -> List[Dict[str, Any]]:
        """Serialize resolved resources for message metadata (the snapshot of record)."""
        return [
            {
                "uri": r.uri,
                "kind": r.kind,
                "title": r.title,
                "metadata": r.metadata,
                "content": r.content,
            }
            for r in resolved
        ]

    @staticmethod
    def inject_resource_block(
        conversation_history: List[Dict[str, Any]],
        resolved: List[ResolvedResource],
    ) -> None:
        """Insert one system context block immediately before the last user message."""
        if not resolved:
            return
        block = (
            "The user attached these resources (snapshot at send time):\n\n"
            + "\n\n---\n\n".join(r.content for r in resolved)
        )
        insert_at = max(len(conversation_history) - 1, 0)
        conversation_history.insert(insert_at, {"role": "system", "content": block})

    async def inject_contributor_block(
        self,
        conversation_history: List[Dict[str, Any]],
        session,
        context_metadata: Optional[Dict[str, Any]],
        user_id: str,
    ) -> None:
        """Insert the mode's context-contributor block immediately before the last user message.

        Contributor failures are logged and never break the send.
        """
        mode = self._m.chat_mode_registry.get(session.mode)
        contributor = mode.context_contributor if mode else None
        if not contributor:
            return
        try:
            result = contributor(context_metadata or {}, session, user_id)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.error(f"Context contributor for mode '{session.mode}' failed")
            logger.debug(traceback.format_exc())
            return
        if not isinstance(result, str) or not result.strip():
            return
        insert_at = max(len(conversation_history) - 1, 0)
        conversation_history.insert(insert_at, {"role": "system", "content": result})

    @staticmethod
    def inject_reply_contract_reminder_block(
        conversation_history: List[Dict[str, Any]],
        mode: Optional[ChatMode],
    ) -> None:
        """Insert a short reply-contract reminder immediately before the last user message.

        A rule stated once near the top of a long system prompt loses to recency;
        this restates it right next to the turn it governs, every send. Composes
        with the mode's context-contributor block rather than replacing it — both
        follow the same "insert immediately before the last message" idiom, so
        calling this after ``inject_contributor_block`` just adds a second system
        block, closer to the user message. No-op for modes with
        ``structured_reply`` off.
        """
        if not mode or not mode.structured_reply:
            return
        insert_at = max(len(conversation_history) - 1, 0)
        conversation_history.insert(insert_at, {"role": "system", "content": REPLY_CONTRACT_REMINDER})

    def inject_memory_block(
        self,
        conversation_history: List[Dict[str, Any]],
        context_metadata: Optional[Dict[str, Any]],
        user_id: str,
        write_memory_available: bool = True,
    ) -> Dict[str, Any]:
        """Insert eagerly-recalled memory notes immediately before the last user message.

        Reads global notes plus notes scoped to the active preset/model (resolved from
        `context_metadata['form_state']`). Failures are logged and never break the send.

        ``write_memory_available`` gates the "call write_memory to add more" nudge in
        the block header — omitted when the session can't call write_memory, so the
        injected block never points at a disabled tool.

        Returns a summary of what was injected — ``{"note_ids": [...], "by_scope":
        {"global": n, "preset": n, "model": n}, "by_scope_dropped": {"global": n,
        "preset": n, "model": n}, "injected_chars": n}`` — so callers can record it
        in the chat behavior-trace manifest and account it against the history
        token budget. ``by_scope_dropped`` counts notes beyond the per-group cap
        that were left out of the block entirely (a group over the cap also gets
        an explicit "+N older notes not shown" line so the omission is visible to
        the model, not just to the trace). ``injected_chars`` is the length of the
        block actually inserted, 0 when nothing was read/injected.
        """
        empty: Dict[str, Any] = {
            "note_ids": [],
            "by_scope": {"global": 0, "preset": 0, "model": 0},
            "by_scope_dropped": {"global": 0, "preset": 0, "model": 0},
            "injected_chars": 0,
        }
        if not self._m.llm_memory_manager:
            return empty
        try:
            from src.features.llm.tools.builtin.utils import (
                resolve_active_model_id,
                resolve_active_preset_id,
            )

            groups: List[tuple] = []
            by_scope = {"global": 0, "preset": 0, "model": 0}
            note_ids: List[str] = []

            global_notes = self._m.llm_memory_manager.read_notes(user_id=user_id, scope="global")
            if global_notes:
                groups.append(("global", global_notes))
                by_scope["global"] = len(global_notes)
                note_ids.extend(note.id for note in global_notes if note.id)

            form_state = (context_metadata or {}).get("form_state")

            preset_ref = resolve_active_preset_id(form_state)
            if preset_ref:
                preset_notes = self._m.llm_memory_manager.read_notes(
                    user_id=user_id, scope="preset", scope_ref=preset_ref,
                )
                if preset_notes:
                    groups.append(("this preset", preset_notes))
                    by_scope["preset"] = len(preset_notes)
                    note_ids.extend(note.id for note in preset_notes if note.id)

            model_ref = resolve_active_model_id(form_state, self._m.model_index_manager)
            if model_ref:
                model_notes = self._m.llm_memory_manager.read_notes(
                    user_id=user_id, scope="model", scope_ref=model_ref,
                )
                if model_notes:
                    groups.append(("this model", model_notes))
                    by_scope["model"] = len(model_notes)
                    note_ids.extend(note.id for note in model_notes if note.id)

            if not groups:
                return empty

            max_notes_per_group = MEMORY_MAX_NOTES_PER_GROUP
            max_content_len = MEMORY_MAX_CONTENT_LEN
            header = (
                "Things you remember about this user (persistent memory — use it; "
                "call write_memory to add more):"
                if write_memory_available
                else "Things you remember about this user (persistent memory — use it):"
            )
            scope_by_label = {"global": "global", "this preset": "preset", "this model": "model"}
            by_scope_dropped = {"global": 0, "preset": 0, "model": 0}
            lines = [header]
            for label, notes in groups:
                lines.append(f"[{label}]")
                for note in notes[:max_notes_per_group]:
                    content = note.content
                    if len(content) > max_content_len:
                        content = content[:max_content_len] + "…"
                    lines.append(f"- {note.key}: {content}")
                overflow = len(notes) - max_notes_per_group
                if overflow > 0:
                    scope_key = scope_by_label.get(label)
                    if scope_key:
                        by_scope_dropped[scope_key] = overflow
                    lines.append(f"  (+{overflow} older notes not shown — consolidate or prune in the memory panel)")

            block = "\n".join(lines)
            insert_at = max(len(conversation_history) - 1, 0)
            conversation_history.insert(insert_at, {"role": "system", "content": block})

            return {
                "note_ids": note_ids,
                "by_scope": by_scope,
                "by_scope_dropped": by_scope_dropped,
                "injected_chars": len(block),
            }
        except Exception:
            logger.error("Failed to inject memory block")
            logger.debug(traceback.format_exc())
            return empty

    def inject_workspace_block(
        self,
        conversation_history: List[Dict[str, Any]],
        context_metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Insert a snapshot of the active Generate-form workspace before the last user message.

        Reads the CURRENT turn's ``context_metadata['form_state']`` and emits one
        compact system block: the active preset (by name)/mode/variant, the selected
        checkpoint with its admin prompting guidance, every active (non-zero-strength)
        LoRA with its trigger words and guidance, and one steering line naming which
        tool the model must use to change the form (``update_video_director`` when a
        Video Director document is active, ``update_form_settings`` otherwise) — this
        is the same signal the tools' own availability is gated on, restated for the
        model. This is the guidance the system prompt promises; injecting it
        deterministically means the model never has to guess a prompt because a tool
        went uncalled. Guidance excerpts are capped so a verbose admin guide can't
        flood the context — the full text stays fetchable via get_model_info.

        When the active preset declares an ``llm:`` block (see docs/presets.md "LLM
        context"), two more things happen: the preset's own ``llm.guide`` (a repo-
        authored, family-level prompting guide) is included, and — unless
        ``llm.context.form`` is ``"off"`` — a compact form-schema listing (name,
        label, type, ai_hint, and for ``"full"`` also range/default/options-count) is
        appended for every field in the mode's schema. ``llm.context.fields``, when
        set, restricts which resolved models' guidance gets pushed (model/LoRA
        identity is still listed either way); ``llm.context.guidance_chars``
        overrides the per-model guidance cap. A preset with no ``llm:`` block gets
        none of this — exact prior behavior, except the header always shows the
        preset's name instead of its raw id (one extra, cheap in-memory lookup
        through the same ``self._m.preset_manager`` already used for
        ``field_meta`` below).

        When ``llm.modes[<current mode>]`` exists, its ``guide`` REPLACES
        ``llm.guide`` entirely for this turn (not concatenated) — the motivating
        case is a mode whose prompt format is incompatible with the preset's base
        guide. No override for the current mode falls back to ``llm.guide`` as
        before. Same 3000-char cap either way.

        Skipped entirely when no form_state is present (pure chat), keeping the system
        prompt's "may receive" phrasing honest. Failures are logged and never break
        the send.

        Returns a summary — ``{"preset": id|None, "checkpoint": name|None, "loras":
        [names], "guidance_included": bool}`` — so callers can record it in the chat
        behavior-trace manifest, mirroring inject_memory_block. Zeroed when nothing
        was injected.
        """
        empty: Dict[str, Any] = {
            "preset": None, "checkpoint": None, "loras": [], "guidance_included": False,
        }
        form_state = (context_metadata or {}).get("form_state")
        if not isinstance(form_state, dict) or not form_state or not self._m.model_index_manager:
            return empty
        try:
            from src.features.llm.tools.builtin.utils import (
                build_model_field_metadata,
                resolve_active_models,
            )

            form_data = form_state.get("form_data") or {}
            preset_id = form_state.get("preset")
            mode = form_state.get("mode")
            variant = form_state.get("variant")
            video_director = form_state.get("video_director")
            music_director = form_state.get("music_director")
            field_meta = build_model_field_metadata(self._m.preset_manager, form_state)

            preset_template = None
            if preset_id and self._m.preset_manager:
                try:
                    preset_template = self._m.preset_manager.file_repo.find_preset_by_id(preset_id)
                except Exception:
                    preset_template = None
            llm_spec = (getattr(preset_template, "llm", None) or {}) if preset_template else {}
            llm_context = llm_spec.get("context") or {}
            guidance_chars = llm_context.get("guidance_chars") or _WORKSPACE_MAX_GUIDANCE_CHARS
            allowed_guidance_fields = llm_context.get("fields") if llm_spec else None
            form_context_mode = llm_context.get("form", "summary") if llm_spec else "off"

            checkpoint: Optional[Dict[str, Any]] = None
            loras: List[Dict[str, Any]] = []
            for field_name, model_path, weight, model_info in resolve_active_models(
                form_data, self._m.model_index_manager
            ):
                meta = field_meta.get(field_name, {})
                model_type = model_info.get("type") or meta.get("model_type") or "unknown"
                name = model_info.get("filename") or model_path.rsplit("/", 1)[-1]
                if model_type == "lora":
                    loras.append({"name": name, "weight": weight, "info": model_info, "field_name": field_name})
                elif model_type in _WORKSPACE_CHECKPOINT_TYPES and checkpoint is None:
                    checkpoint = {"name": name, "info": model_info, "field_name": field_name}

            if (
                not preset_id and checkpoint is None and not loras
                and video_director is None and music_director is None
            ):
                return empty

            guidance_included = False
            lines = [
                "Your Generate workspace this turn (use it; do not re-ask what it already tells you):"
            ]
            if preset_id:
                preset_name = preset_template.name if preset_template else preset_id
                header = f"Preset: {preset_name}"
                if mode:
                    header += f" · Mode: {mode}"
                if variant:
                    header += f" · Variant: {variant}"
                lines.append(header)

            mode_spec = (llm_spec.get("modes") or {}).get(mode) if mode else None
            guide = mode_spec.get("guide") if mode_spec else llm_spec.get("guide")
            if guide:
                lines.append("Prompting guide:")
                lines.append(_cap_text(guide, _WORKSPACE_MAX_GUIDE_CHARS, "… (truncated)"))

            if video_director and video_director.get("active"):
                lines.append("Video Director: active — form changes go through update_video_director.")
                lines.extend(self._render_video_director_summary(
                    video_director.get("doc") or {}, video_director.get("capabilities") or {},
                    form_data, preset_id, mode, variant,
                ))
            else:
                lines.append("Video Director: no document active — form changes go through update_form_settings.")

            if music_director and music_director.get("active"):
                lines.append("Music Director: active — form changes go through update_music_director.")
                lines.extend(self._render_music_director_summary(music_director.get("doc") or {}))

            if checkpoint is not None:
                lines.append(f"Checkpoint: {checkpoint['name']}")
                guidance = checkpoint["info"].get("prompting_guidance")
                guidance_allowed = allowed_guidance_fields is None or checkpoint["field_name"] in allowed_guidance_fields
                if guidance and guidance_allowed:
                    lines.append(f"  Guidance: {_cap_guidance(guidance, guidance_chars)}")
                    guidance_included = True

            if loras:
                lines.append(f"Active LoRAs ({len(loras)}):")
                for lora in loras:
                    info = lora["info"]
                    parts = [f"- {lora['name']}"]
                    if lora["weight"] is not None:
                        parts.append(f"(strength {lora['weight']})")
                    triggers = info.get("trigger_words")
                    if triggers:
                        parts.append("— triggers: " + _format_triggers(triggers))
                    lines.append(" ".join(parts))
                    guidance_allowed = allowed_guidance_fields is None or lora["field_name"] in allowed_guidance_fields
                    detail = info.get("prompting_guidance") or info.get("description")
                    if detail and guidance_allowed:
                        lines.append(f"    {_cap_guidance(detail, guidance_chars)}")
                        if info.get("prompting_guidance"):
                            guidance_included = True

            if form_context_mode != "off" and preset_id:
                lines.extend(self._render_form_context(preset_id, mode, variant, form_context_mode))

            block = "\n".join(lines)
            insert_at = max(len(conversation_history) - 1, 0)
            conversation_history.insert(insert_at, {"role": "system", "content": block})

            return {
                "preset": preset_id,
                "checkpoint": checkpoint["name"] if checkpoint else None,
                "loras": [lora["name"] for lora in loras],
                "guidance_included": guidance_included,
            }
        except Exception:
            logger.error("Failed to inject workspace block")
            logger.debug(traceback.format_exc())
            return empty

    def _render_video_director_summary(
        self,
        doc: Dict[str, Any],
        capabilities: Dict[str, Any],
        form_data: Dict[str, Any],
        preset_id: Optional[str],
        mode: Optional[str],
        variant: Optional[str],
    ) -> List[str]:
        """Compact snapshot of an active Video Director document: shot list
        (id, prompt, length, sub_type, its own attached media) and any form
        media not yet attached to a shot -- the pool `upsert_media`'s
        `form_media` can address. Delegates the flatten/render work to
        `render_context_summary` (owns the document shape already);
        this method's own job is resolving which form fields hold media,
        from the active mode's schema, the same way `_render_form_context`
        does. Mirrors `_render_music_director_summary`'s terseness --
        get_video_director is where the model goes for the full document.
        """
        from src.features.llm.tools.builtin.video_director_tool import render_context_summary
        from src.features.llm.tools.media_values import media_field_names

        media_fields: List[str] = []
        if self._m.preset_manager and preset_id:
            try:
                schema_data = self._m.preset_manager.get_form_schema(preset_id, mode=mode, form_name=variant)
                props = (schema_data.get("form_schema") or {}).get("properties")
                media_fields = sorted(media_field_names(props))
            except Exception:
                media_fields = []

        try:
            return render_context_summary(doc, capabilities, form_data, mode, media_fields)
        except Exception:
            logger.error("Failed to render Video Director context summary")
            logger.debug(traceback.format_exc())
            return []

    @staticmethod
    def _render_music_director_summary(doc: Dict[str, Any]) -> List[str]:
        """Compact snapshot of an active Music Director document: derived
        mode (the frontend keeps `doc['mode']` in sync with
        `deriveMusicDirectorMode` on every edit, so it's read directly rather
        than recomputed here), description, one line per section (kind + its
        first lyric line), and settings. Mirrors the terseness of the
        checkpoint/LoRA lines above it -- get_music_director is where the
        model goes for the full document.
        """
        lines: List[str] = []
        mode = doc.get("mode")
        if mode:
            lines.append(f"  Mode: {mode}")
        description = doc.get("description")
        if description:
            lines.append(f"  Description: {_cap_text(description, _WORKSPACE_MAX_AI_HINT_CHARS)}")
        sections = doc.get("sections") or []
        if sections:
            lines.append(f"  Sections ({len(sections)}):")
            for section in sections:
                if not isinstance(section, dict):
                    continue
                kind = section.get("kind") or "verse"
                lyrics = (section.get("lyrics") or "").strip()
                first_line = lyrics.splitlines()[0] if lyrics else "(no lyrics yet)"
                lines.append(f"    - {kind}: {_cap_text(first_line, _WORKSPACE_MAX_AI_HINT_CHARS)}")
        settings = doc.get("settings") or {}
        if isinstance(settings, dict) and settings:
            parts = [f"duration={settings['duration']}"] if settings.get("duration") is not None else []
            for key in ("bpm", "key", "time_signature"):
                if settings.get(key) is not None:
                    parts.append(f"{key}={settings[key]}")
            if parts:
                lines.append("  Settings: " + ", ".join(parts))
        return lines

    def _render_form_context(
        self,
        preset_id: str,
        mode: Optional[str],
        variant: Optional[str],
        form_context_mode: str,
    ) -> List[str]:
        """Render a compact `llm.context.form` listing for the active mode's schema.

        ``"summary"``: name, label, type, ai_hint (truncated) per field.
        ``"full"``: summary plus range/default/options-count.
        Never raises — a schema lookup failure yields an empty listing rather than
        dropping the rest of the already-built workspace block.
        """
        if not self._m.preset_manager:
            return []
        try:
            schema_data = self._m.preset_manager.get_form_schema(preset_id, mode=mode, form_name=variant)
        except Exception:
            return []
        props = (schema_data.get("form_schema") or {}).get("properties") or {}
        if not props:
            return []

        lines = [f"Form fields (mode: {mode or 'default'}):"]
        for field_name, field_schema in props.items():
            parts = [f"- {field_name}"]
            label = field_schema.get("title")
            if label and label != field_name:
                parts.append(f'"{label}"')
            parts.append(f"[{field_schema.get('type', 'unknown')}]")
            line = " ".join(parts)
            ai_hint = field_schema.get("ai_hint")
            if ai_hint:
                line += f": {_cap_text(ai_hint, _WORKSPACE_MAX_AI_HINT_CHARS)}"
            lines.append(line)

            if form_context_mode == "full":
                details: List[str] = []
                if field_schema.get("minimum") is not None or field_schema.get("maximum") is not None:
                    details.append(f"range {field_schema.get('minimum')}-{field_schema.get('maximum')}")
                if "default" in field_schema:
                    details.append(f"default={field_schema['default']!r}")
                config = field_schema.get("configuration") or {}
                options = config.get("options")
                if isinstance(options, list):
                    details.append(f"{len(options)} options")
                elif field_schema.get("enum"):
                    details.append(f"{len(field_schema['enum'])} options")
                if details:
                    lines.append("    " + "; ".join(details))

        return lines

    @staticmethod
    def inject_prompt_state_block(
        conversation_history: List[Dict[str, Any]],
        context_metadata: Optional[Dict[str, Any]],
    ) -> None:
        """Insert a compact structural snapshot of the current prompt editor before the last user message.

        Reads the CURRENT turn's ``context_metadata['segments']`` (index/id/content/
        name/type/enabled, plus optional ``template`` provenance and a ``negative``
        flag) and emits one deterministic block listing each positive and negative
        segment — enabled state, template slot, id and a truncated content excerpt —
        so the model always knows the editor's structure without a tool call. Content
        is truncated per-segment and the list is capped; get_current_segments returns
        the full text. Rebuilt per request and never persisted.

        Skipped entirely when no segments are present, or when the Video
        Director is the active editor (``context_metadata['form_state']``) --
        there "segment #N" means a shot, and get_video_director is the
        structural snapshot for that, not this block.
        """
        from src.features.llm.tools.builtin.utils import video_director_active

        if video_director_active((context_metadata or {}).get("form_state")):
            return

        segments = (context_metadata or {}).get("segments")
        if not isinstance(segments, list) or not segments:
            return

        positives = [s for s in segments if isinstance(s, dict) and not s.get("negative")]
        negatives = [s for s in segments if isinstance(s, dict) and s.get("negative")]
        if not positives and not negatives:
            return

        template_names: List[str] = []
        seen: set = set()
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            tpl = seg.get("template")
            if isinstance(tpl, dict):
                nm = tpl.get("name")
                if nm and nm not in seen:
                    seen.add(nm)
                    template_names.append(str(nm))

        lines = [_PROMPT_STATE_HEADER]
        if len(template_names) == 1:
            lines.append(f'Template: "{template_names[0]}"')
        elif template_names:
            lines.append("Templates: " + ", ".join(f'"{n}"' for n in template_names))

        emitted = 0
        overflow = 0
        for title, group in (("Positive:", positives), ("Negative:", negatives)):
            if not group:
                continue
            section: List[str] = []
            for seq, seg in enumerate(group, start=1):
                if emitted >= _PROMPT_STATE_MAX_SEGMENTS:
                    overflow += 1
                    continue
                section.append(_render_prompt_state_line(seq, seg))
                emitted += 1
            if section:
                lines.append(title)
                lines.extend(section)
        if overflow:
            lines.append(f"…and {overflow} more segments — call get_current_segments.")

        block = "\n".join(lines)
        if len(block) > _PROMPT_STATE_MAX_BLOCK_CHARS:
            block = (
                block[:_PROMPT_STATE_MAX_BLOCK_CHARS].rstrip()
                + "\n…(truncated — call get_current_segments for full segment text)"
            )
        insert_at = max(len(conversation_history) - 1, 0)
        conversation_history.insert(insert_at, {"role": "system", "content": block})

    async def suggest_resources(
        self,
        query: str,
        mode_id: Optional[str],
        user_id: str,
        limit: int = 15,
    ) -> List[ResourceSuggestion]:
        """Suggest @resource completions for the chat input dropdown."""
        if not self._m.resource_registry:
            return []
        mode = self._m.chat_mode_registry.get(mode_id or 'generation') or self._m.chat_mode_registry.get('generation')
        ctx = self.build_resource_context(user_id, mode.id if mode else mode_id)
        return await self._m.resource_registry.suggest(query, mode, ctx, limit=limit)
