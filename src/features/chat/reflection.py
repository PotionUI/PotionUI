"""
Memory reflection: a background pass that extracts durable user facts from a
chat session's transcript into persistent memory notes.

Fires after a turn (see ``ConversationRunner._start_reflection_task``) as a
fire-and-forget task — a slow or failed reflection call must never delay or
break the response the user is waiting on. Gated by the session's LLM config
(``memory_reflection``, default on) and by a minimum number of unreflected
user messages, so it doesn't fire on every single turn.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.features.chat.dto import SessionResponse
from src.features.chat.memory_compaction import MemoryCompactor
from src.features.llm import trace_collector
from src.features.llm.tools.builtin.utils import resolve_active_model_id, resolve_active_preset_id
from src.features.llm_memory import operations as memory_operations
from src.features.llm_memory.operations import MAX_CONTENT_LENGTH

logger = logging.getLogger(__name__)

# Fires once this many user messages have arrived since the last reflection
# (or since the session started, if it has never been reflected).
MIN_UNREFLECTED_USER_MESSAGES = 4

MAX_TRANSCRIPT_CHARS = 12000

# The model is told to keep facts well under the manager's hard cap so a
# little formatting overhead from write_note never trips MAX_CONTENT_LENGTH;
# _persist_items truncates defensively regardless, since instructions in a
# prompt are not a length guarantee.
_TARGET_CONTENT_CHARS = 400

_METADATA_KEY = "memory_reflection"
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


class ChatReflectionGenerator:
    """Extracts and persists durable memory notes from a session's transcript.

    Takes the owning ``ChatManager`` rather than its collaborators directly:
    ``llm_memory_repository`` is late-bound onto the manager by the composition
    root *after* construction (see ``ChatManager``'s docstring), so reading it
    back through ``self._m`` at call time - instead of capturing it in
    ``__init__`` - is what makes that late binding actually take effect here.
    """

    def __init__(self, manager):
        self._m = manager
        # Compaction rides along after a reflection persist, never the
        # interactive write_memory path - see MemoryCompactor's docstring.
        self._compactor = MemoryCompactor(manager)

    def should_reflect(self, session: SessionResponse, messages: List[Any]) -> bool:
        """Whether a reflection pass is due for this session right now."""
        if not self._m.llm_memory_repository or not session.llm_config_id:
            return False
        config = self._m.llm_service.repository.get_configuration(session.llm_config_id)
        if not config or not getattr(config, "memory_reflection", False):
            return False
        return self._unreflected_user_count(session, messages) >= MIN_UNREFLECTED_USER_MESSAGES

    async def reflect(self, session_id: str, form_state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Extract durable facts from the session and persist them as memory notes.

        Re-validates ``should_reflect`` itself (it is started as a detached
        background task, so nothing upstream has necessarily just checked this).
        ``form_state`` is the triggering turn's form state (same shape as
        ``context_metadata["form_state"]``) - it is not derivable from the
        session alone, so the caller threads it through from the live turn.
        Returns the list of saved notes (possibly empty); never raises.
        """
        session = self._m.chat_repository.get_session(session_id)
        if not session:
            return []
        messages = self._m.chat_repository.get_messages(session_id)
        if not self.should_reflect(session, messages):
            return []

        transcript = self._build_transcript(session, messages)
        if not transcript:
            return []

        active_preset, active_model = self._resolve_active_context(form_state)
        prompt = self._build_prompt(active_preset, active_model)

        try:
            with trace_collector.activate(session_id, session.user_id, purpose="memory_reflection"):
                response = await self._m.llm_service.generate_with_history(
                    messages=[{"role": "user", "content": f"{prompt}\n\n---\n\n{transcript}"}],
                    llm_id=session.llm_config_id,
                    options_override={"max_tokens": 800, "temperature": 0.2, "think": False},
                )
        except Exception as e:
            logger.warning(f"Memory reflection failed for session {session_id}: {e}")
            return []

        items = self._parse_items(response.content if response else None)
        saved = self._persist_items(
            session.user_id, items,
            active_preset_id=active_preset[0] if active_preset else None,
            active_model_id=active_model[0] if active_model else None,
        )

        self._m.chat_repository.record_memory_reflection(session_id, messages[-1].id)

        logger.info(f"Memory reflection saved {len(saved)} note(s) for session {session_id}")

        if saved:
            await self._compactor.compact_after_reflection(session.user_id, session_id, session.llm_config_id)

        return saved

    # --- bookkeeping ---

    def _reflected_up_to(self, session: SessionResponse) -> Optional[str]:
        return (session.metadata or {}).get(_METADATA_KEY, {}).get("reflected_up_to_message_id")

    def _unreflected_messages(self, session: SessionResponse, messages: List[Any]) -> List[Any]:
        reflected_up_to = self._reflected_up_to(session)
        if not reflected_up_to:
            return messages
        for i, m in enumerate(messages):
            if m.id == reflected_up_to:
                return messages[i + 1:]
        return messages

    def _unreflected_user_count(self, session: SessionResponse, messages: List[Any]) -> int:
        return sum(1 for m in self._unreflected_messages(session, messages) if m.role == "user")

    def _build_transcript(self, session: SessionResponse, messages: List[Any]) -> str:
        lines = [
            f"{m.role.capitalize()}: {m.content}"
            for m in self._unreflected_messages(session, messages)
            if m.role in ("user", "assistant") and (m.content or "").strip()
        ]
        return "\n\n".join(lines)[:MAX_TRANSCRIPT_CHARS]

    # --- active context / prompt ---

    def _resolve_active_context(
        self, form_state: Optional[Dict[str, Any]]
    ) -> Tuple[Optional[Tuple[str, str]], Optional[Tuple[str, str]]]:
        """Resolve the turn's active preset/model into (id, label) pairs for the prompt.

        Best-effort: a label lookup failure still yields the id with itself as
        the label rather than dropping the context entirely, since the id
        alone is what ``_validate_scope`` actually checks a reported
        scope_ref against.
        """
        preset_id = resolve_active_preset_id(form_state)
        active_preset = None
        if preset_id:
            label = preset_id
            if self._m.preset_manager:
                try:
                    label = self._m.preset_manager.get_preset(preset_id).get("name") or preset_id
                except Exception:
                    pass
            active_preset = (preset_id, label)

        model_id = resolve_active_model_id(form_state, self._m.model_index_manager)
        active_model = None
        if model_id:
            label = model_id
            if self._m.model_index_manager:
                try:
                    model = self._m.model_index_manager.model_repo.get_by_id(
                        model_id, include_providers=False, include_tags=False,
                    )
                    if model and model.filename:
                        label = model.filename
                except Exception:
                    pass
            active_model = (model_id, label)

        return active_preset, active_model

    @staticmethod
    def _build_prompt(
        active_preset: Optional[Tuple[str, str]], active_model: Optional[Tuple[str, str]],
    ) -> str:
        """Compose the reflection prompt with this turn's actual active ids.

        The model is given the real preset/model id and told to reuse it
        verbatim for a scoped fact; ``_validate_scope`` then checks the id it
        reports against these same values, so a hallucinated id can never
        make it into a persisted note.
        """
        if active_preset:
            preset_line = (
                f'- Active preset: "{active_preset[1]}" (id: {active_preset[0]}) - use scope '
                f'"preset" with scope_ref exactly "{active_preset[0]}" for facts tied only to it.'
            )
        else:
            preset_line = '- No active preset in this conversation - never use scope "preset".'

        if active_model:
            model_line = (
                f'- Active model: "{active_model[1]}" (id: {active_model[0]}) - use scope '
                f'"model" with scope_ref exactly "{active_model[0]}" for facts tied only to it.'
            )
        else:
            model_line = '- No active model in this conversation - never use scope "model".'

        return (
            "Review this conversation and extract durable facts worth remembering for "
            "FUTURE, unrelated conversations with this user - not facts about this one "
            "exchange.\n\n"
            "Look for: preferences the user states outright, corrections the user "
            "makes to your work, and requests repeated more than once. Ignore anything "
            "tied to a single generation - a seed, a one-off prompt, a result the user "
            "reacted to only once.\n\n"
            f"{preset_line}\n{model_line}\n\n"
            "For each fact, pick a scope: 'global' for something true everywhere, or "
            "'preset'/'model' for something tied ONLY to the active preset/model named "
            "above - never invent an id, only ever the exact one given above.\n\n"
            f"Keep each fact's content under {_TARGET_CONTENT_CHARS} characters.\n\n"
            "Reply with a JSON array only, no other text. Each item:\n"
            '{"scope": "global"|"preset"|"model", "scope_ref": "<the exact id given '
            'above, or null for global>", "key": "<short snake_case identifier>", '
            '"content": "<the fact, written as a general statement>", '
            '"why_generalizes": "<one line: why this applies beyond this conversation>"}\n\n'
            "If nothing durable came up, reply with an empty array: []"
        )

    # --- parsing / persistence ---

    @staticmethod
    def _parse_items(text: Optional[str]) -> List[Dict[str, Any]]:
        """Leniently extract a JSON array of memory items from model output."""
        if not text:
            return []
        stripped = _THINK_BLOCK_RE.sub("", text).strip()
        match = _JSON_ARRAY_RE.search(stripped)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [entry for entry in data if isinstance(entry, dict)]

    @staticmethod
    def _slugify(text: str) -> str:
        """Derive a stable key so re-reflection updates the same note instead
        of duplicating it, even if the model phrases the category differently
        across runs."""
        slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
        return slug[:60] or "reflection_note"

    def _persist_items(
        self,
        user_id: str,
        items: List[Dict[str, Any]],
        active_preset_id: Optional[str] = None,
        active_model_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        saved: List[Dict[str, Any]] = []
        for item in items:
            key = item.get("key")
            content = item.get("content")
            if not isinstance(key, str) or not key.strip() or not isinstance(content, str) or not content.strip():
                continue
            scope, scope_ref = self._validate_scope(
                item.get("scope"), item.get("scope_ref"), active_preset_id, active_model_id,
            )
            try:
                note = memory_operations.write_note(
                    self._m.llm_memory_repository,
                    user_id=user_id,
                    key=self._slugify(key),
                    # Defensive truncation: the prompt asks for shorter notes, but a
                    # prompt instruction is not a length guarantee, and write_note
                    # rejects anything over MAX_CONTENT_LENGTH outright.
                    content=content.strip()[:MAX_CONTENT_LENGTH],
                    scope=scope,
                    scope_ref=scope_ref,
                )
            except ValueError as e:
                logger.info(f"Reflection item dropped: {e}")
                continue
            saved.append(note.to_dict())
        return saved

    @staticmethod
    def _validate_scope(
        scope: Any,
        scope_ref: Any,
        active_preset_id: Optional[str],
        active_model_id: Optional[str],
    ) -> Tuple[str, Optional[str]]:
        """Accept a 'preset'/'model' scope only when scope_ref names exactly the
        id this turn's form state actually resolved for that scope. Anything
        else - an invalid scope, a mismatched or hallucinated scope_ref, or no
        active id at all for that scope - falls back to 'global' rather than
        being trusted or dropped, since VALID_SCOPES membership alone says
        nothing about whether the referenced preset/model is the one this
        conversation was actually about."""
        if scope == "preset" and active_preset_id and scope_ref == active_preset_id:
            return "preset", active_preset_id
        if scope == "model" and active_model_id and scope_ref == active_model_id:
            return "model", active_model_id
        return "global", None
