"""
Memory reflection: a background pass that extracts durable user facts from a
chat session's transcript into persistent memory notes.

Fires after a turn (see ``ConversationRunner._start_reflection_task``, which
just calls ``ChatReflectionGenerator.trigger``) as a fire-and-forget task — a
slow or failed reflection call must never delay or break the response the
user is waiting on. Gated by the session's LLM config (``memory_reflection``,
default on) and by a minimum number of unreflected user messages, so it
doesn't fire on every single turn.

A single pass never re-sends a whole unbounded transcript: it covers a
*bounded span* — whole unreflected messages up to a char budget (see
``_span_char_budget``), chunking mid-message only when one message alone
exceeds the budget (see ``_build_span``) — and records exactly how far it
got as ``{message_id, offset}`` in session metadata (see ``_METADATA_KEY``),
never claiming coverage past what was actually sent to the model and
successfully handled. ``ChatRepository.record_memory_reflection`` only ever
moves that cursor forward, so two overlapping passes (a slow one racing a
fast one) can't move it backward. Any span left uncovered by the char budget
is surfaced via ``pending_reflection_backlog`` and picked up by exactly one
coalesced follow-up pass per trigger burst (see ``trigger``/``_run_pass``) —
never an unbounded chain.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.features.chat.dto import SessionResponse
from src.features.chat.memory_compaction import MemoryCompactor
from src.features.llm import context_budget, trace_collector
from src.features.llm.tools.builtin.utils import resolve_active_model_id, resolve_active_preset_id
from src.features.llm_memory import operations as memory_operations
from src.features.llm_memory.operations import MAX_CONTENT_LENGTH

logger = logging.getLogger(__name__)

# Fires once this many user messages have arrived since the last reflection
# (or since the session started, if it has never been reflected).
MIN_UNREFLECTED_USER_MESSAGES = 4

# Upper bound on a single span's transcript, and the fallback when the
# session's LLM config declares no context window (see `_span_char_budget`).
MAX_TRANSCRIPT_CHARS = 12000

# How much of the config's declared context window a span may use, leaving
# the rest for the reflection prompt/instructions and the model's response.
_TRANSCRIPT_WINDOW_FRACTION = 0.5

_LINE_SEP = "\n\n"

# The model is told to keep facts well under the manager's hard cap so a
# little formatting overhead from write_note never trips MAX_CONTENT_LENGTH;
# _persist_items truncates defensively regardless, since instructions in a
# prompt are not a length guarantee.
_TARGET_CONTENT_CHARS = 400

_METADATA_KEY = "memory_reflection"
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


@dataclass
class _ReflectionSpan:
    """One bounded slice of the unreflected transcript, ready to send.

    ``end_message_id``/``end_offset`` is the cursor position the span
    actually covers — passed to ``record_memory_reflection`` only after the
    model call and persistence both succeed. ``end_seq`` is that message's
    index in the ``messages`` list the span was built from, purely so the
    repository can tell two spans' end positions apart without re-deriving
    message order itself (see its monotonic-advance docstring).
    ``has_backlog`` is True when the budget was hit before every unreflected
    message was covered (a mid-message chunk, or whole messages deferred).
    """

    transcript: str
    end_message_id: Optional[str] = None
    end_offset: int = 0
    end_seq: int = -1
    has_backlog: bool = False


class ChatReflectionGenerator:
    """Extracts and persists durable memory notes from a session's transcript.

    Takes the owning ``ChatRuntime`` rather than its collaborators directly:
    ``llm_memory_repository`` is late-bound onto the manager by the composition
    root *after* construction (see ``ChatRuntime``'s docstring), so reading it
    back through ``self._m`` at call time - instead of capturing it in
    ``__init__`` - is what makes that late binding actually take effect here.
    """

    def __init__(self, manager):
        self._m = manager
        # Compaction rides along after a reflection persist, never the
        # interactive write_memory path - see MemoryCompactor's docstring.
        self._compactor = MemoryCompactor(manager)
        # Per-session single-flight bookkeeping for `trigger` - in-memory
        # only (a session's flag not surviving a restart is fine, since the
        # very next turn re-triggers naturally). Never read/written outside
        # `trigger`/`_run_pass`.
        self._in_flight: set = set()
        self._rerun_requested: set = set()

    def should_reflect(self, session: SessionResponse, messages: List[Any]) -> bool:
        """Whether a *new* reflection pass is due for this session right now
        (the four-unreflected-user-message trigger threshold). Does not
        gate a coalesced follow-up continuing an already-triggered span's
        backlog - see ``_reflection_enabled`` for that."""
        if not self._reflection_enabled(session):
            return False
        return self._unreflected_user_count(session, messages) >= MIN_UNREFLECTED_USER_MESSAGES

    def _reflection_enabled(self, session: SessionResponse) -> bool:
        """The non-threshold gates: a memory store, an LLM config, and that
        config's toggle. True here says nothing about whether there is
        actually new (or any) unreflected content."""
        if not self._m.llm_memory_repository or not session.llm_config_id:
            return False
        config = self._m.llm_service.repository.get_configuration(session.llm_config_id)
        return bool(config and getattr(config, "memory_reflection", False))

    def trigger(self, session_id: str, form_state: Optional[Dict[str, Any]] = None) -> None:
        """Fire-and-forget entry point called after every turn (see
        ``ConversationRunner._start_reflection_task``).

        At most one reflection pass runs per session at a time. A trigger
        that arrives while a pass is already in flight for this session
        does not start a second one - it just records that a rerun is
        owed; the in-flight pass, on completion, runs exactly one coalesced
        follow-up (see ``_run_pass``), so any number of arrivals during a
        pass produce only one extra pass, never a queue. Independent
        sessions are unaffected by each other. Never raises - this runs
        after the response path has already returned to the caller.
        """
        try:
            if session_id in self._in_flight:
                self._rerun_requested.add(session_id)
                return
            self._in_flight.add(session_id)
            task = asyncio.create_task(self._run_pass(session_id, form_state))
            self._m._reflection_tasks.add(task)
            task.add_done_callback(self._m._reflection_tasks.discard)
        except Exception as e:
            logger.warning(f"Could not start memory reflection for session {session_id}: {e}")
            self._in_flight.discard(session_id)

    async def _run_pass(self, session_id: str, form_state: Optional[Dict[str, Any]] = None) -> None:
        """Run one pass, then - and only then - at most one coalesced
        follow-up: either a rerun requested while this pass was running, or
        backlog this same pass left behind (a mid-message chunk boundary,
        or whole messages deferred past the char budget). The follow-up
        itself never schedules another one, so a standing backlog or a
        burst of triggers can never chain into an unbounded loop.
        """
        try:
            await self.reflect(session_id, form_state)
            rerun = session_id in self._rerun_requested
            self._rerun_requested.discard(session_id)
            session = self._m.chat_repository.get_session(session_id)
            backlog = self.pending_reflection_backlog(session) if session else False
            if rerun or backlog:
                # A backlog follow-up continues the same triggered burst
                # regardless of the four-message threshold; a pure rerun
                # (no backlog) is a genuinely new trigger and is still
                # threshold-gated like any other.
                await self.reflect(session_id, form_state, require_threshold=not backlog)
        finally:
            self._in_flight.discard(session_id)

    async def reflect(
        self,
        session_id: str,
        form_state: Optional[Dict[str, Any]] = None,
        *,
        require_threshold: bool = True,
    ) -> List[Dict[str, Any]]:
        """Extract durable facts from one bounded span of the session's
        transcript and persist them as memory notes.

        Re-validates its own gating (it is started as a detached background
        task, so nothing upstream has necessarily just checked this).
        ``require_threshold=False`` skips the four-unreflected-user-message
        gate - used only for a backlog follow-up continuing a span already
        approved by an earlier trigger this same burst (see ``_run_pass``);
        the memory-store/config-toggle gates still apply either way.
        ``form_state`` is the triggering turn's form state (same shape as
        ``context_metadata["form_state"]``) - it is not derivable from the
        session alone, so the caller threads it through from the live turn.
        Returns the list of saved notes (possibly empty); never raises.
        """
        session = self._m.chat_repository.get_session(session_id)
        if not session:
            return []
        messages = self._m.chat_repository.get_messages(session_id)
        if require_threshold:
            if not self.should_reflect(session, messages):
                return []
        elif not self._reflection_enabled(session):
            return []

        span = self._build_span(session, messages)
        if not span.transcript:
            return []

        active_preset, active_model = self._resolve_active_context(form_state)
        prompt = self._build_prompt(active_preset, active_model)

        try:
            with trace_collector.activate(session_id, session.user_id, purpose="memory_reflection"):
                response = await self._m.llm_service.generate_with_history(
                    messages=[{"role": "user", "content": f"{prompt}\n\n---\n\n{span.transcript}"}],
                    llm_id=session.llm_config_id,
                    options_override={"max_tokens": 800, "temperature": 0.2, "think": False},
                )
        except Exception as e:
            # Failure: no coverage claimed, this span stays eligible. Not
            # retried immediately - the next natural trigger (a new turn,
            # or a coalesced follow-up already in flight) will see the same
            # unreflected span and try again, never a tight retry loop here.
            logger.warning(f"Memory reflection failed for session {session_id}: {e}")
            return []

        items = self._parse_items(response.content if response else None)
        saved = self._persist_items(
            session.user_id, items,
            active_preset_id=active_preset[0] if active_preset else None,
            active_model_id=active_model[0] if active_model else None,
        )

        # A valid empty extraction (the model found nothing durable) still
        # advances the cursor - only a call/persistence failure above skips
        # this. `record_memory_reflection` itself only moves the cursor
        # forward, so a slow pass finishing after a newer one can't clobber it.
        recorded = self._m.chat_repository.record_memory_reflection(
            session_id, span.end_message_id,
            offset=span.end_offset, seq=span.end_seq,
            pending_backlog=span.has_backlog,
        )
        if not recorded:
            logger.info(
                f"Memory reflection: cursor for session {session_id} not advanced "
                "(a newer pass already covers this span)"
            )

        logger.info(f"Memory reflection saved {len(saved)} note(s) for session {session_id}")

        if saved:
            await self._compactor.compact_after_reflection(session.user_id, session_id, session.llm_config_id)

        return saved

    # --- bookkeeping ---

    def _reflected_up_to_id(self, session: SessionResponse) -> Optional[str]:
        return (session.metadata or {}).get(_METADATA_KEY, {}).get("reflected_up_to_message_id")

    def _reflected_up_to_offset(self, session: SessionResponse) -> int:
        return (session.metadata or {}).get(_METADATA_KEY, {}).get("reflected_up_to_offset", 0) or 0

    def pending_reflection_backlog(self, session: SessionResponse) -> bool:
        """Whether the last reflection pass left unreflected text behind -
        a mid-message chunk boundary, or whole messages deferred past the
        char budget - for a later pass to pick up."""
        return bool((session.metadata or {}).get(_METADATA_KEY, {}).get("pending_backlog", False))

    def _unreflected_entries(self, session: SessionResponse, messages: List[Any]) -> List[Tuple[int, Any, int]]:
        """``(seq, message, start_offset)`` for every message - or partial
        message - not yet covered by the stored cursor, in transcript
        order. ``seq`` is the message's index in ``messages``: stable to
        compare across passes because a session's transcript only ever
        grows (new messages append; earlier ones keep their position), which
        is what makes it safe for ``record_memory_reflection`` to use as an
        ordering key without re-deriving message order itself.
        """
        reflected_id = self._reflected_up_to_id(session)
        if not reflected_id:
            return [(i, m, 0) for i, m in enumerate(messages)]
        offset = self._reflected_up_to_offset(session)
        for i, m in enumerate(messages):
            if m.id == reflected_id:
                entries: List[Tuple[int, Any, int]] = []
                if offset and offset < len(m.content or ""):
                    entries.append((i, m, offset))
                entries.extend((j, mm, 0) for j, mm in enumerate(messages[i + 1:], start=i + 1))
                return entries
        # Marker message no longer present (e.g. deleted) - be conservative
        # and treat the whole transcript as unreflected rather than silently
        # skip work.
        return [(i, m, 0) for i, m in enumerate(messages)]

    def _unreflected_user_count(self, session: SessionResponse, messages: List[Any]) -> int:
        return sum(1 for _, m, _ in self._unreflected_entries(session, messages) if m.role == "user")

    def _span_char_budget(self, session: SessionResponse) -> int:
        """Char budget for one span: the ``MAX_TRANSCRIPT_CHARS`` default,
        capped further when the session's LLM config declares a smaller
        context window. Reads capacity read-only via
        ``context_budget.resolve_capacity`` (the same accounting
        ``LLMGateway`` enforces on the wire) rather than re-deriving it;
        half the window is reserved for the reflection prompt and the
        model's own response.
        """
        if not session.llm_config_id:
            return MAX_TRANSCRIPT_CHARS
        config = self._m.llm_service.repository.get_configuration(session.llm_config_id)
        if not config:
            return MAX_TRANSCRIPT_CHARS
        capacity = context_budget.resolve_capacity(config)
        window_chars = int(
            capacity.capacity_tokens * _TRANSCRIPT_WINDOW_FRACTION * context_budget.DEFAULT_CHARS_PER_TOKEN
        )
        if window_chars <= 0:
            return MAX_TRANSCRIPT_CHARS
        return min(MAX_TRANSCRIPT_CHARS, window_chars)

    def _build_span(self, session: SessionResponse, messages: List[Any]) -> "_ReflectionSpan":
        """Greedily gather whole unreflected messages up to the char budget.

        Offset policy: a message that doesn't fit whole is deferred to a
        later span in full, UNLESS nothing has been included yet this pass -
        i.e. the message alone exceeds the entire budget - in which case it
        is chunked, taking as much of it (from its own ``start_offset``) as
        fits; the cursor then records the exact offset reached so the next
        span resumes mid-message rather than re-sending or skipping text.
        """
        entries = self._unreflected_entries(session, messages)
        budget = self._span_char_budget(session)
        parts: List[str] = []
        total = 0
        end_seq, end_id, end_offset = -1, None, 0
        has_backlog = False
        for seq, m, start_offset in entries:
            if m.role not in ("user", "assistant"):
                continue
            content = m.content or ""
            text = content[start_offset:]
            if not text.strip():
                end_seq, end_id, end_offset = seq, m.id, 0
                continue
            line = f"{m.role.capitalize()}: {text}"
            projected = total + (len(_LINE_SEP) if parts else 0) + len(line)
            if projected <= budget:
                parts.append(line)
                total = projected
                end_seq, end_id, end_offset = seq, m.id, 0
                continue
            if not parts:
                prefix = f"{m.role.capitalize()}: "
                room = budget - len(prefix)
                if room > 0:
                    chunk = text[:room]
                    parts.append(f"{prefix}{chunk}")
                    end_seq, end_id, end_offset = seq, m.id, start_offset + len(chunk)
            has_backlog = True
            break
        return _ReflectionSpan(_LINE_SEP.join(parts), end_id, end_offset, end_seq, has_backlog)

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
