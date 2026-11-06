"""Chat context assembly.

Builds everything that surrounds the raw conversation before it reaches the
model: the effective system prompt (mode prompt or explicit override) together
with the allowed tool set, and the per-turn context blocks folded into the
current user turn (@resource snapshots, the mode's context contributor and
recalled persistent memory) via ``attach_context``. Also
serves @resource autocomplete suggestions.

"""

import asyncio
import inspect
import logging
import re
import traceback
from typing import Any, Callable, Collection, Dict, Iterable, List, Optional, Tuple

from src.features.chat.modes import ChatMode
from src.features.chat.reply_contract import REPLY_CONTRACT_REMINDER
from src.features.llm.harness.build import attach_context
from src.features.llm.harness.messages import Message
from src.features.llm import context_budget
from src.features.llm.tools.governance import ToolGovernanceRepository, ToolScopeResolution, resolve_tool_scope
from src.features.llm.tools.use_tools import deferred_tool_groups
from src.features.llm.ttl_cache import TTLCache
from src.features.llm_memory import operations as memory_operations
from src.features.presets import operations
from src.features.prompt.resources import describe_prompt_resources
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
_CONDITION_OPEN = re.compile(r"<<if(?=\s|>)")
_CONDITION_END = "<<end>>"
_CONDITION_ELIDED = "<<if …>>"
_CONDITION_EXTEND_FACTOR = 4
_PROMPT_STATE_MAX_BLOCK_CHARS = 4000
_PROMPT_STATE_MAX_CONDITION_PROBLEMS = 2
_PROMPT_STATE_CONDITION_PROBLEM_CHARS = 300
_PROMPT_STATE_SKIPPED_LONG = "     condition check skipped (segment too long to check)"
_PROMPT_STATE_SKIPPED_BUDGET = "     condition check skipped (enough condition text was checked this turn)"
_PROMPT_STATE_MAX_PROMPTS = 20
_PROMPT_STATE_PREVIEW_CHARS = 60
_PROMPT_STATE_NAME_CHARS = 40
_PROMPT_STATE_OVERVIEW_MAX_CHARS = 2000
PROMPT_BATCH_ACTIONS_CAPABILITY = "prompt_batch_actions"
_PROMPT_STATE_HEADER = "PROMPT STATE (current editor contents; truncated):"
_PROMPT_STATE_HEADER_WITH_TOOL = (
    "PROMPT STATE (current editor contents; truncated — "
    "get_current_segments returns full text):"
)

# Why a mode tool that isn't in the resolved allowed set was withheld, and the
# actionable hint for each (what a user/admin does to change it). "unavailable"
# has no hint: is_available() is about missing form context (e.g. no document
# loaded), not a setting to flip.
TOOL_WITHHELD_HINTS = {
    "off_by_toggle": "turn on Tools in the chat header",
    "disabled_in_mode": "enable it in this mode's Tools panel",
    "disabled_by_admin": "ask an admin to enable it in Admin → LLM configurations → toolset",
    "opted_out": "re-enable it in this mode's Tools panel",
    "unavailable": None,
}

_TOOL_UNAVAILABLE_LABELS = {
    "off_by_toggle": "Tools are off",
    "disabled_in_mode": "disabled for this mode",
    "disabled_by_admin": "disabled by an admin",
    "opted_out": "off in your tool preferences",
    "unavailable": "not available right now",
}


PARTIAL_TOOLS_BLOCK = (
    "Some tools are unavailable this turn; use only the tools you were given. "
    "Never announce an action you can't perform."
)

def empty_memory_result() -> Dict[str, Any]:
    return {
        "note_ids": [],
        "by_scope": {"global": 0, "preset": 0, "model": 0, "mode": 0, "session": 0},
        "by_scope_dropped": {"global": 0, "preset": 0, "model": 0, "mode": 0, "session": 0},
        "injected_chars": 0,
    }


def _cap_text(text: str, limit: int, suffix: str = "…") -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + suffix


def _cap_guidance(
    text: str, limit: int = _WORKSPACE_MAX_GUIDANCE_CHARS, full_available: bool = True,
) -> str:
    suffix = "… (call get_model_info for the full guidance)" if full_available else "…"
    return _cap_text(text, limit, suffix)

def _tool_offered(offered_tools: Optional[Collection[str]], name: str) -> bool:
    return offered_tools is None or name in offered_tools


def _condition_straddle_end(compact: str, limit: int) -> Optional[int]:
    for opening in _CONDITION_OPEN.finditer(compact):
        if opening.start() >= limit:
            return None
        closing = compact.find(_CONDITION_END, opening.end())
        if closing < 0:
            return None
        end = closing + len(_CONDITION_END)
        if end <= limit:
            continue
        return end if end <= limit * _CONDITION_EXTEND_FACTOR else None
    return None


def _drop_dangling_condition(cut: str) -> str:
    for opening in _CONDITION_OPEN.finditer(cut):
        if cut.find(_CONDITION_END, opening.end()) < 0:
            cut = cut[:opening.start()].rstrip()
            break
    partial = cut.rfind("<<")
    if partial >= 0 and ">>" not in cut[partial:]:
        cut = cut[:partial].rstrip()
    return cut or _CONDITION_ELIDED


def _truncate_prompt_content(text: str, limit: int) -> Tuple[str, int, bool]:
    """Whole-word truncate a segment's content; returns (shown, total_chars, truncated)."""
    compact = " ".join((text or "").split())
    total = len(compact)
    if total <= limit:
        return compact, total, False
    end = _condition_straddle_end(compact, limit)
    if end is not None:
        return compact[:end], total, end < total
    cut = compact[:limit]
    if compact[limit] != " ":
        sp = cut.rfind(" ")
        if sp > 0:
            cut = cut[:sp]
    return _drop_dangling_condition(cut.rstrip()), total, True


def _render_prompt_state_line(seq: int, seg: Dict[str, Any]) -> str:
    num = f"{seq:02d}"
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


def _render_prompt_overview(
    context_metadata: Optional[Dict[str, Any]], segments_offered: bool
) -> List[str]:
    from src.features.llm.tools.builtin.utils import prompt_batch_supported

    metadata = context_metadata if isinstance(context_metadata, dict) else {}
    if not prompt_batch_supported(metadata.get("form_state")):
        return []
    batch = metadata.get("prompt_batch")
    prompts = batch.get("prompts") if isinstance(batch, dict) else None
    if not isinstance(prompts, list) or len(prompts) < 2:
        return []
    active = batch.get("active")
    if not isinstance(active, int) or isinstance(active, bool):
        active = 0
    lines = [f"Prompts ({len(prompts)}, active {active}):"]
    used = len(lines[0])
    for entry in prompts[:_PROMPT_STATE_MAX_PROMPTS]:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        raw_name = entry.get("name")
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else f"Prompt {index + 1}"
        name, _, name_cut = _truncate_prompt_content(name, _PROMPT_STATE_NAME_CHARS)
        if name_cut:
            name += "…"
        raw_segments = entry.get("segments")
        entry_segments = [s for s in raw_segments if isinstance(s, dict)] if isinstance(raw_segments, list) else []
        preview = next(
            (
                s["content"]
                for s in entry_segments
                if not s.get("negative")
                and s.get("enabled", True)
                and isinstance(s.get("content"), str)
                and s["content"].strip()
            ),
            "",
        )
        shown, _, truncated = _truncate_prompt_content(preview, _PROMPT_STATE_PREVIEW_CHARS)
        mark = "*" if index == active else ""
        count = len(entry_segments)
        line = f'  {index}{mark} "{name}" ({count} segment{"" if count == 1 else "s"})'
        if shown:
            line += f': "{shown}{"…" if truncated else ""}"'
        if used + len(line) + 1 > _PROMPT_STATE_OVERVIEW_MAX_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    if segments_offered:
        lines.append("Another prompt's segments: get_current_segments with prompt_index.")
    lines.append(f"The segment lists below belong to prompt {active}.")
    return lines


def _format_triggers(triggers: Any) -> str:
    if isinstance(triggers, (list, tuple)):
        return ", ".join(str(t) for t in triggers)
    return str(triggers)


def _classify_withheld_tools(
    mode_tools: List[Any],
    allowed_names: List[str],
    unavailable: Tuple[str, ...],
    tools_enabled: bool,
    disabled_tools: List[str],
    governance_snapshot: Dict[str, Any],
) -> Dict[str, str]:
    """Reason each of ``mode_tools`` missing from ``allowed_names`` was withheld.

    Mirrors the AND of filters ``resolve_session_prompt_and_tools`` applies
    (governance, ``is_available``, the session's ``tools_enabled``/
    ``disabled_tools``) but picks one reason per tool for a human/model-facing
    message, in the same precedence those filters are ANDed in: unavailable
    (missing form context) beats the toggle beats governance, since fixing the
    toggle wouldn't unblock a tool that's unavailable anyway. The frontend's
    global Tools toggle sets ``tools_enabled`` False (nothing offered at all);
    its per-mode Tools panel adds names to ``disabled_tools`` (this tool
    specifically unticked) — see ``UnifiedAIChat.svelte``'s tools payload.
    """
    allowed_set = set(allowed_names)
    reasons: Dict[str, str] = {}
    for tool in mode_tools:
        name = tool.name
        if name in allowed_set:
            continue
        if name in unavailable:
            reasons[name] = "unavailable"
        elif not tools_enabled:
            reasons[name] = "off_by_toggle"
        elif name in disabled_tools:
            reasons[name] = "disabled_in_mode"
        else:
            row = governance_snapshot.get(name)
            admin_enabled = True
            if isinstance(row, dict):
                admin_enabled = row.get("enabled", True)
            elif row is not None:
                admin_enabled = row[0]
            reasons[name] = "disabled_by_admin" if not admin_enabled else "opted_out"
    return reasons


def _prompt_purpose_line(preset_template: Any, mode: Optional[str]) -> Optional[str]:
    if preset_template is None or not mode:
        return None
    prompt_vars = (getattr(preset_template, "vars", None) or {}).get("prompt") or {}
    mode_vars = (prompt_vars.get("modes") or {}).get(mode) or {}
    label = mode_vars.get("label")
    if not isinstance(label, str) or not label.strip():
        return None
    line = f"The prompt box in this mode is for: {label.strip()}"
    placeholder = mode_vars.get("placeholder")
    if isinstance(placeholder, str) and placeholder.strip():
        line += f' (for example: "{placeholder.strip()}")'
    return line + ". Exact text and spoken words belong in the form fields, not the prompt."


class ChatContextBuilder:
    """Assembles the system prompt, tool set and injected context blocks."""

    def __init__(self, manager):
        self._m = manager
        self._prompt_tools_cache: TTLCache[Tuple[Any, ...], Tuple[str, List[str], Dict[str, List[str]]]] = TTLCache(
            _PROMPT_TOOLS_CACHE_TTL_SECONDS
        )

    def resolve_tool_scope(self, session, form_state: Optional[Dict[str, Any]] = None) -> ToolScopeResolution:
        mode = self._m.chat_mode_registry.require(session.mode)
        registry = self._m.tool_executor.tool_registry if self._m.tool_executor else None
        mode_tools = registry.get_for_mode(mode) if registry else []
        governance_repo = getattr(self._m, "tool_governance_repository", None)
        if not isinstance(governance_repo, ToolGovernanceRepository):
            governance_repo = None
        return resolve_tool_scope(
            mode_tools,
            governance_repo,
            getattr(session, "llm_config_id", None),
            getattr(session, "user_id", None),
            form_state,
        )

    def resolve_session_prompt_and_tools(
        self,
        session,
        form_state: Optional[Dict[str, Any]] = None,
        tool_tier: str = context_budget.TOOL_TIER_LARGE,
        loaded_groups: Iterable[str] = (),
        scope: Optional[ToolScopeResolution] = None,
    ) -> Tuple[str, List[str], ChatMode, Dict[str, List[str]]]:
        """Resolve the session's mode into a system prompt and allowed tool names.

        The mode determines the tool set (mode tools + global tools); each tool
        is then filtered through its ``is_available(form_state)`` predicate
        (declarative, e.g. a tool that only applies when a particular document
        is loaded on the form); the session's ``tools_enabled``/``disabled_tools``
        metadata acts as a further subtractive filter. The system prompt is the
        session's explicit custom message when stored, otherwise the mode
        prompt with the allowed tools' hints substituted.

        The (prompt, allowed_names) result is memoized per (mode, tools-filter
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
        from src.features.llm.tools.builtin.utils import prompt_batch_supported

        mode = self._m.chat_mode_registry.require(session.mode)

        session_metadata = getattr(session, 'metadata', None) or {}
        tools_enabled = session_metadata.get('tools_enabled', True)
        disabled_tools = session_metadata.get('disabled_tools') or []
        custom_system_message = session_metadata.get('system_message')

        registry = self._m.tool_executor.tool_registry if self._m.tool_executor else None
        if scope is None:
            scope = self.resolve_tool_scope(session, form_state)
        mode_tools = list(scope.tools)
        mode_tool_names = [t.name for t in mode_tools]
        unavailable = scope.unavailable
        governance_snapshot = scope.governance_snapshot
        user_disabled = scope.user_disabled
        llm_config_id = getattr(session, "llm_config_id", None)

        cache_key = (
            mode.id,
            tools_enabled,
            tuple(sorted(disabled_tools)),
            custom_system_message or "",
            unavailable,
            llm_config_id,
            tuple(sorted(governance_snapshot.items())),
            tuple(sorted(user_disabled & set(mode_tool_names))),
            tool_tier,
            tuple(sorted(set(loaded_groups))),
            prompt_batch_supported(form_state),
        )
        cached = self._prompt_tools_cache.get(cache_key)
        if cached is not None:
            prompt, allowed_names, deferred = cached
            return prompt, allowed_names, mode, deferred

        allowed_names = scope.allowed_names(tools_enabled, disabled_tools)
        deferred = deferred_tool_groups(
            [t for t in mode_tools if t.name in allowed_names],
            tool_tier,
            mode.core_groups,
            always_core=mode.tool_names or (),
            loaded_groups=loaded_groups,
        )
        deferred_names = {name for names in deferred.values() for name in names}
        allowed_names = [name for name in allowed_names if name not in deferred_names]

        if custom_system_message:
            prompt = custom_system_message
        elif mode_tools and not allowed_names and not deferred:
            # The mode has tools but the session disabled all of them — a
            # tool-centric mode prompt would mislead the model, so fall back to
            # the config default/style resolution downstream.
            prompt = ""
        else:
            hints = registry.get_tool_hints_text(allowed_names) if (registry and allowed_names) else ""
            capabilities = list(allowed_names)
            if prompt_batch_supported(form_state) and "get_current_segments" in allowed_names:
                capabilities.append(PROMPT_BATCH_ACTIONS_CAPABILITY)
            prompt = self._m.chat_mode_registry.resolve_system_prompt(mode, hints, capabilities)

        self._prompt_tools_cache.set(cache_key, (prompt, allowed_names, deferred))
        return prompt, allowed_names, mode, deferred

    def withheld_tools_for_session(
        self,
        session,
        form_state: Optional[Dict[str, Any]] = None,
        scope: Optional[ToolScopeResolution] = None,
    ) -> Dict[str, str]:
        """Reasons the session's mode declares tools that ``allowed_names`` omits.

        A same-shaped live read as ``resolve_session_prompt_and_tools`` (mode
        tools, ``is_available``, ``tools_enabled``/``disabled_tools``,
        governance) — not itself memoized, same as the governance snapshot
        that method already re-reads every call regardless of its own
        prompt/allowed-names cache hit. Empty when the mode declares no tools
        or every declared tool is allowed.
        """
        session_metadata = getattr(session, 'metadata', None) or {}
        tools_enabled = session_metadata.get('tools_enabled', True)
        disabled_tools = session_metadata.get('disabled_tools') or []

        if scope is None:
            scope = self.resolve_tool_scope(session, form_state)
        mode_tools = list(scope.tools)
        if not mode_tools:
            return {}

        allowed_names = scope.allowed_names(tools_enabled, disabled_tools)
        return _classify_withheld_tools(
            mode_tools, allowed_names, scope.unavailable, tools_enabled, disabled_tools, scope.governance_snapshot
        )

    # --- @Resource helpers ---

    def build_resource_context(
        self,
        user_id: str,
        mode_id: Optional[str],
        form_state: Optional[Dict[str, Any]] = None,
        is_admin: bool = False,
    ) -> ResourceContext:
        """Build the dependency bundle handed to resource providers."""
        return ResourceContext(
            user_id=user_id,
            mode_id=mode_id,
            is_admin=is_admin,
            model_index_manager=self._m.tool_services.model_index_manager,
            phrasebook_category_repository=self._m.tool_services.phrasebook_category_repository,
            phrasebook_value_repository=self._m.tool_services.phrasebook_value_repository,
            phrasebook_search=self._m.phrasebook_search,
            preset_collaborators=self._m.tool_services.preset_collaborators,
            preset_lookup=self._m.preset_lookup,
            preset_form_schema_lookup=self._m.preset_form_schema_lookup,
            generation_repository=self._m.generation_repository,
            generation_parameter_repository=self._m.generation_parameter_repository,
            generation_model_repository=self._m.generation_model_repository,
            form_state=form_state,
            prompt_variables=self._draft_variable_reader(user_id, form_state),
        )

    def prompt_state_variable_reader(self, user_id: str, context_metadata: Optional[Dict[str, Any]]):
        return self._draft_variable_reader(user_id, (context_metadata or {}).get("form_state"))

    def _draft_variable_reader(self, user_id: str, form_state: Optional[Dict[str, Any]]):
        drafts = self._m.tool_services.tab_drafts
        tab_id = form_state.get("tab_id") if isinstance(form_state, dict) else None
        if drafts is None or not isinstance(tab_id, str) or not tab_id:
            return None

        def read() -> Dict[str, Any]:
            try:
                return drafts.variables(user_id, tab_id)
            except Exception:
                logger.warning("Could not read the tab draft for the variable summary", exc_info=True)
                return {}

        return read

    async def resolve_message_resources(
        self,
        resources: Optional[List[str]],
        user_id: str,
        mode_id: str,
        context_metadata: Optional[Dict[str, Any]] = None,
        is_admin: bool = False,
    ) -> List[ResolvedResource]:
        """Snapshot-resolve @resource refs attached to a message. Never raises.

        ``context_metadata.form_state`` is threaded onto the ResourceContext so
        the @form provider can resolve live form values at send time.
        """
        if not resources or not self._m.resource_registry:
            return []
        form_state = (context_metadata or {}).get("form_state")
        ctx = self.build_resource_context(user_id, mode_id, form_state=form_state, is_admin=is_admin)
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
        conversation_history: List[Message],
        resolved: List[ResolvedResource],
    ) -> None:
        """Fold the resource snapshot into the current user turn."""
        if not resolved:
            return
        block = (
            "The user attached these resources (snapshot at send time):\n\n"
            + "\n\n---\n\n".join(r.content for r in resolved)
        )
        attach_context(conversation_history, "resource", block)

    async def inject_contributor_block(
        self,
        conversation_history: List[Message],
        session,
        context_metadata: Optional[Dict[str, Any]],
        user_id: str,
    ) -> None:
        """Fold the mode's context-contributor block into the current user turn.

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
        attach_context(conversation_history, "contributor", result)

    @staticmethod
    def inject_reply_contract_reminder_block(
        conversation_history: List[Message],
        mode: Optional[ChatMode],
        tool_tier: str = context_budget.TOOL_TIER_STANDARD,
    ) -> None:
        """Fold a short reply-contract reminder into the current user turn.

        A rule stated once near the top of a long system prompt loses to recency;
        this restates it right next to the turn it governs, every send. Composes
        with the mode's context-contributor block rather than replacing it — both
        fold into the same user turn, so calling this after
        ``inject_contributor_block`` just adds a second block, closer to the
        end of the user's content. No-op for modes with ``structured_reply``
        off.
        """
        if not mode or not mode.structured_reply or tool_tier == context_budget.TOOL_TIER_COMPACT:
            return
        attach_context(conversation_history, "reply_contract", REPLY_CONTRACT_REMINDER)

    @staticmethod
    def inject_tool_availability_block(
        conversation_history: List[Message],
        offered: Optional[List[str]],
        withheld: Dict[str, str],
    ) -> None:
        """Fold a block into the current user turn warning the model about
        tools it cannot call this turn.

        Without this the mode's own prompt (written for the tool-driven case) is
        the model's only guidance, and a model told nothing about a missing tool
        narrates calling it anyway — the reported failure mode: "Firing all 5
        caption updates now… All five are proposed" with no tool step in the
        trace and nothing in the approval dock. No-op when nothing is withheld
        (``withheld`` empty).
        """
        if not withheld:
            return
        if not offered:
            reasons = sorted({_TOOL_UNAVAILABLE_LABELS.get(r, r) for r in withheld.values()})
            hints = sorted({h for r in withheld.values() if (h := TOOL_WITHHELD_HINTS.get(r))})
            text = (
                f"Tools are unavailable in this conversation ({'; '.join(reasons)}). "
                "You cannot call any tool, propose changes, or fetch anything: say so "
                "in one sentence and tell the user how to enable them"
                + (f" ({'; '.join(hints)})" if hints else "")
                + ". Never announce an action you cannot perform."
            )
        else:
            text = PARTIAL_TOOLS_BLOCK
        attach_context(conversation_history, "tool_availability", text)

    def inject_memory_block(
        self,
        conversation_history: List[Message],
        context_metadata: Optional[Dict[str, Any]],
        user_id: str,
        write_memory_available: bool = True,
        mode_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fold eagerly-recalled memory notes into the current user turn.

        Reads global notes plus notes scoped to the active preset/model (resolved from
        `context_metadata['form_state']`) and to the active mode (``mode_id`` — the
        session's own mode, e.g. from `session.mode`, NOT derived from `form_state`:
        a plugin-mode session has no Generate form open at all). Failures are logged
        and never break the send.

        ``write_memory_available`` gates the "call write_memory to add more" nudge in
        the block header — omitted when the session can't call write_memory, so the
        injected block never points at a disabled tool.

        Returns a summary of what was injected — ``{"note_ids": [...], "by_scope":
        {"global": n, "preset": n, "model": n, "mode": n}, "by_scope_dropped":
        {"global": n, "preset": n, "model": n, "mode": n}, "injected_chars": n}`` —
        so callers can record it in the chat behavior-trace manifest and account it
        against the history token budget. ``by_scope_dropped`` counts notes beyond
        the per-group cap that were left out of the block entirely (a group over the
        cap also gets an explicit "+N older notes not shown" line so the omission is
        visible to the model, not just to the trace). ``injected_chars`` is the
        length of the block actually inserted, 0 when nothing was read/injected.
        """
        empty = empty_memory_result()
        if not self._m.tool_services.llm_memory_repository:
            return empty
        try:
            from src.features.llm.tools.builtin.utils import (
                resolve_active_model_id,
                resolve_active_preset_id,
            )

            groups: List[tuple] = []
            by_scope = {"global": 0, "preset": 0, "model": 0, "mode": 0, "session": 0}
            note_ids: List[str] = []

            global_notes = memory_operations.read_notes(self._m.tool_services.llm_memory_repository, user_id=user_id, scope="global")
            if global_notes:
                groups.append(("global", global_notes))
                by_scope["global"] = len(global_notes)
                note_ids.extend(note.id for note in global_notes if note.id)

            form_state = (context_metadata or {}).get("form_state")

            preset_ref = resolve_active_preset_id(form_state)
            if preset_ref:
                preset_notes = memory_operations.read_notes(
                    self._m.tool_services.llm_memory_repository, user_id=user_id, scope="preset", scope_ref=preset_ref,
                )
                if preset_notes:
                    groups.append(("this preset", preset_notes))
                    by_scope["preset"] = len(preset_notes)
                    note_ids.extend(note.id for note in preset_notes if note.id)

            model_ref = resolve_active_model_id(form_state, self._m.tool_services.model_index_manager)
            if model_ref:
                model_notes = memory_operations.read_notes(
                    self._m.tool_services.llm_memory_repository, user_id=user_id, scope="model", scope_ref=model_ref,
                )
                if model_notes:
                    groups.append(("this model", model_notes))
                    by_scope["model"] = len(model_notes)
                    note_ids.extend(note.id for note in model_notes if note.id)

            if mode_id:
                mode_notes = memory_operations.read_notes(
                    self._m.tool_services.llm_memory_repository, user_id=user_id, scope="mode", scope_ref=mode_id,
                )
                if mode_notes:
                    groups.append(("this mode", mode_notes))
                    by_scope["mode"] = len(mode_notes)
                    note_ids.extend(note.id for note in mode_notes if note.id)

            session_ref = form_state.get("session_id") if isinstance(form_state, dict) else None
            if session_ref:
                session_notes = memory_operations.read_notes(
                    self._m.tool_services.llm_memory_repository, user_id=user_id, scope="session", scope_ref=session_ref,
                )
                if session_notes:
                    groups.append(("this session", session_notes))
                    by_scope["session"] = len(session_notes)
                    note_ids.extend(note.id for note in session_notes if note.id)

            if not groups:
                return empty

            max_notes_per_group = MEMORY_MAX_NOTES_PER_GROUP
            max_content_len = MEMORY_MAX_CONTENT_LEN
            header = (
                "Things you remember about this user (persistent memory — background "
                "context: apply it only where the user's current request is silent, "
                "never to override what they just asked for; call write_memory to add "
                "more):"
                if write_memory_available
                else (
                    "Things you remember about this user (persistent memory — "
                    "background context: apply it only where the user's current "
                    "request is silent, never to override what they just asked for):"
                )
            )
            scope_by_label = {
                "global": "global", "this preset": "preset", "this model": "model", "this mode": "mode",
                "this session": "session",
            }
            by_scope_dropped = {"global": 0, "preset": 0, "model": 0, "mode": 0, "session": 0}
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
            attach_context(conversation_history, "memory", block)

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
        conversation_history: List[Message],
        context_metadata: Optional[Dict[str, Any]],
        offered_tools: Optional[Collection[str]] = None,
    ) -> Dict[str, Any]:
        """Fold a snapshot of the active Generate-form workspace into the current user turn.

        Reads the CURRENT turn's ``context_metadata['form_state']`` and emits one
        compact system block: the active preset (by name)/mode/variant, the selected
        checkpoint with its admin prompting guidance, every active (non-zero-strength)
        LoRA with its trigger words and guidance, and one steering line naming what the
        model may do to the form (``update_form_settings`` normally; when a Video
        Director document is active, prompt VERSIONS only via the
        ``update_director_segment`` tag — everything else on that document is
        user-only) — this is the same signal the tools' own availability is gated on,
        restated for the model. This is the guidance the system prompt promises; injecting it
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
        through the same ``self._m.preset_collaborators`` already used for
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
        if not isinstance(form_state, dict) or not form_state or not self._m.tool_services.model_index_manager:
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
            field_meta = build_model_field_metadata(self._m.tool_services.preset_collaborators, form_state)

            preset_template = None
            if preset_id and self._m.tool_services.preset_collaborators:
                try:
                    preset_template = self._m.tool_services.preset_collaborators.file_repo.find_preset_by_id(preset_id)
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
                form_data, self._m.tool_services.model_index_manager
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
            model_info_offered = _tool_offered(offered_tools, "get_model_info")
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

            prompt_purpose = _prompt_purpose_line(preset_template, mode)
            if prompt_purpose:
                lines.append(prompt_purpose)

            if video_director and video_director.get("active"):
                lines.append(
                    "Video Director: active — you may only suggest per-shot prompt "
                    "VERSIONS via the update_director_segment tag; shot count, durations, "
                    "media, mode, and settings are user-only. Each shot is a SEPARATE "
                    "generation: write every shot's prompt as a complete, standalone "
                    "description of what is on screen during that shot only, timed from its "
                    "own 00:00 to its own duration. Never mention another shot, never write "
                    "that a shot continues, follows or picks up from the previous one, never "
                    "carry a timestamp across shots. Joined shots already start from the "
                    "previous shot's last frames on the backend; the prompt describes the new "
                    "shot as if the scene simply began there."
                )
                lines.extend(self._render_video_director_summary(
                    video_director.get("doc") or {}, video_director.get("capabilities") or {},
                    form_data, preset_id, mode, variant,
                ))
            else:
                lines.append(
                    "Video Director: no document active — form changes go through update_form_settings."
                    if _tool_offered(offered_tools, "update_form_settings")
                    else "Video Director: no document active."
                )

            if music_director and music_director.get("active"):
                lines.append(
                    "Music Director: active — form changes go through update_music_director."
                    if _tool_offered(offered_tools, "update_music_director")
                    else "Music Director: active."
                )
                lines.extend(self._render_music_director_summary(music_director.get("doc") or {}))

            if checkpoint is not None:
                lines.append(f"Checkpoint: {checkpoint['name']}")
                guidance = checkpoint["info"].get("prompting_guidance")
                guidance_allowed = allowed_guidance_fields is None or checkpoint["field_name"] in allowed_guidance_fields
                if guidance and guidance_allowed:
                    lines.append(f"  Guidance: {_cap_guidance(guidance, guidance_chars, model_info_offered)}")
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
                        lines.append(f"    {_cap_guidance(detail, guidance_chars, model_info_offered)}")
                        if info.get("prompting_guidance"):
                            guidance_included = True

            if form_context_mode != "off" and preset_id:
                lines.extend(self._render_form_context(preset_id, mode, variant, form_context_mode))

            lines.extend(self._render_prompt_resources(preset_template, mode, form_data))

            block = "\n".join(lines)
            attach_context(conversation_history, "workspace", block)

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
        if self._m.tool_services.preset_collaborators and preset_id:
            try:
                schema_data = operations.get_form_schema(
                    self._m.tool_services.preset_collaborators, preset_id, mode=mode, form_name=variant
                )
                props = (schema_data.get("form_schema") or {}).get("properties")
                media_fields = sorted(media_field_names(props))
            except Exception:
                logger.warning(
                    "form schema lookup failed for preset '%s' mode '%s'; Video Director media fields empty",
                    preset_id, mode, exc_info=True,
                )
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

    @staticmethod
    def _render_prompt_resources(
        preset_template: Any,
        mode: Optional[str],
        form_data: Any,
    ) -> List[str]:
        declared = getattr(preset_template, "prompt_resources", None) if preset_template else None
        if not isinstance(declared, dict) or not mode:
            return []
        resources = declared.get(mode)
        if not isinstance(resources, list) or not resources:
            return []
        return describe_prompt_resources(resources, form_data if isinstance(form_data, dict) else {})

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
        if not self._m.tool_services.preset_collaborators:
            return []
        try:
            schema_data = operations.get_form_schema(
                self._m.tool_services.preset_collaborators, preset_id, mode=mode, form_name=variant
            )
        except Exception:
            logger.warning(
                "form schema lookup failed for preset '%s' mode '%s'; form context block empty",
                preset_id, mode, exc_info=True,
            )
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
        conversation_history: List[Message],
        context_metadata: Optional[Dict[str, Any]],
        offered_tools: Optional[Collection[str]] = None,
        variable_reader: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        """Fold a compact structural snapshot of the current prompt editor into the current user turn.

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
        from src.features.llm.tools.builtin.condition_text import (
            CONDITION_MARKERS,
            MAX_CHECKED_CHARS,
            Budget,
            condition_problems,
            condition_variable_lines,
        )
        from src.features.llm.tools.builtin.utils import video_director_active

        if video_director_active((context_metadata or {}).get("form_state")):
            return

        segments_offered = _tool_offered(offered_tools, "get_current_segments")
        overview = _render_prompt_overview(context_metadata, segments_offered)

        segments = (context_metadata or {}).get("segments")
        if not isinstance(segments, list):
            segments = []
        if not segments and not overview:
            return

        positives = [s for s in segments if isinstance(s, dict) and not s.get("negative")]
        negatives = [s for s in segments if isinstance(s, dict) and s.get("negative")]
        if not positives and not negatives and not overview:
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

        lines = [_PROMPT_STATE_HEADER_WITH_TOOL if segments_offered else _PROMPT_STATE_HEADER]
        lines.extend(overview)
        if len(template_names) == 1:
            lines.append(f'Template: "{template_names[0]}"')
        elif template_names:
            lines.append("Templates: " + ", ".join(f'"{n}"' for n in template_names))

        variables = variable_reader() if variable_reader is not None else None
        emitted = 0
        budget = Budget()
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
                content = seg.get("content") or ""
                if any(marker in content for marker in CONDITION_MARKERS):
                    if len(content) > MAX_CHECKED_CHARS:
                        section.append(_PROMPT_STATE_SKIPPED_LONG)
                    elif not budget.take(content):
                        section.append(_PROMPT_STATE_SKIPPED_BUDGET)
                    else:
                        problems = condition_problems(content, variables or None)[:_PROMPT_STATE_MAX_CONDITION_PROBLEMS]
                        section.extend(
                            f"     condition problem: {_cap_text(problem, _PROMPT_STATE_CONDITION_PROBLEM_CHARS)}"
                            for problem in problems
                        )
                emitted += 1
            if section:
                lines.append(title)
                lines.extend(section)
        if overflow:
            lines.append(
                f"…and {overflow} more segments — call get_current_segments."
                if segments_offered
                else f"…and {overflow} more segments."
            )

        variable_lines = condition_variable_lines(variables)
        block_limit = _PROMPT_STATE_MAX_BLOCK_CHARS - sum(len(line) + 1 for line in variable_lines)
        block = "\n".join(lines)
        if len(block) > block_limit:
            head = block[:block_limit]
            if "\n" in head:
                head = head.rsplit("\n", 1)[0]
            block = head.rstrip() + (
                "\n…(truncated — call get_current_segments for full segment text)"
                if segments_offered
                else "\n…(truncated)"
            )
        if variable_lines:
            block = "\n".join([block, *variable_lines])
        attach_context(conversation_history, "prompt_state", block)

    async def suggest_resources(
        self,
        query: str,
        mode_id: Optional[str],
        user_id: str,
        limit: int = 15,
        is_admin: bool = False,
    ) -> List[ResourceSuggestion]:
        """Suggest @resource completions for the chat input dropdown."""
        if not self._m.resource_registry:
            return []
        mode = self._m.chat_mode_registry.get(mode_id or 'generation') or self._m.chat_mode_registry.get('generation')
        ctx = self.build_resource_context(user_id, mode.id if mode else mode_id, is_admin=is_admin)
        return await self._m.resource_registry.suggest(query, mode, ctx, limit=limit)
