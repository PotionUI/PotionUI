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
*bounded span* — whole unreflected messages up to a char budget priced from
the SAME accounting ``LLMGateway`` enforces on the real wire call, after
reserving room for the fixed reflection prompt and the response (see
``_resolve_span_budget``); when even that reservation exceeds the config's
window, no span runs this pass. That estimate is only a starting candidate:
``_fit_span_to_real_request`` then validates the EXACT candidate request
against ``LLMGateway.estimate_context_budget`` — the same whole-request
check ``_budgeted`` runs before every real send, given the same resolved
system message and response reserve — and shrinks it (bounded) if it
doesn't fit, so a non-trivial configured system message or template framing
overhead the initial estimate couldn't see never produces a request that
fails the gateway's own enforcement on every trigger. A message alone
exceeding the budget is chunked mid-message (see ``_build_span``), and the
pass records exactly how far it got as ``{message_id, offset, seq}`` in
session metadata (see
``_METADATA_KEY``) — ``offset`` is chars of that message covered so far, and
is the message's FULL length (never 0) when it's covered in full, so a pass
that later completes a message a previous pass had only partially chunked
always compares as later than that partial position, never regressing it.
``ChatRepository.record_memory_reflection`` only ever moves that cursor
forward by that ``(seq, offset)`` ordering, so two overlapping passes (a
slow one racing a fast one) can't move it backward. Any span left uncovered
by the char budget is surfaced via ``pending_reflection_backlog`` and picked
up by exactly one coalesced follow-up pass per trigger burst (see
``trigger``/``_run_pass``) — never an unbounded chain. A malformed/missing
model response and a call/persistence failure both claim no coverage (the
span stays eligible for the next natural trigger, never retried
immediately); a syntactically valid empty extraction (the model genuinely
found nothing) still advances the cursor.
"""

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass, field
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

# Upper bound on a single span's transcript, and the fallback when a span's
# budget can't be resolved from real accounting (see `_resolve_span_budget`).
MAX_TRANSCRIPT_CHARS = 12000

_LINE_SEP = "\n\n"

# The explicit response reservation for a reflection call - passed to BOTH
# the real `generate_with_history` call AND `LLMGateway.accounting_inputs_for`
# (see `_resolve_span_budget`), so the budget check and the real send can
# never reserve a different amount for the output than each other.
_REFLECTION_OPTIONS_OVERRIDE = {"max_tokens": 800, "temperature": 0.2, "think": False}

# Bounded halving attempts in `_fit_span_to_real_request` before giving up on
# this pass - never an unbounded shrink loop.
_MAX_SPAN_SHRINK_STEPS = 6

# The model is told to keep facts well under the manager's hard cap so a
# little formatting overhead from write_note never trips MAX_CONTENT_LENGTH;
# _persist_items truncates defensively regardless, since instructions in a
# prompt are not a length guarantee.
_TARGET_CONTENT_CHARS = 400

_METADATA_KEY = "memory_reflection"
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

# --- content-overlap backstop (see `_restates_single_prompt`) ---
#
# A candidate whose content is mostly just the words of one generation
# request, wearing a "preference" sentence as a costume, is rejected even
# when it otherwise passes the kind/evidence grounding check below (a model
# can legally quote its own one-off prompt verbatim, which satisfies
# `_validate_fact_grounding`'s "stated" quote check without the CONTENT
# actually being a preference). This is the independent second layer that
# catches that case.
_CONTENT_WORD_RE = re.compile(r"[a-zA-Z']{2,}")

# Generic English function words - carry no subject-matter signal either way.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "is", "are", "was", "were", "be", "been", "being", "this", "that",
    "these", "those", "i", "you", "he", "she", "it", "we", "they", "my",
    "your", "his", "her", "its", "our", "their", "as", "by", "from", "into",
    "about", "than", "then", "so", "if", "not", "no", "do", "does", "did",
    "have", "has", "had", "will", "would", "can", "could", "should", "just",
    "also", "very", "one", "some",
})

# Words a model reaches for to dress up a one-off request as a standing
# preference ("user LIKES to GENERATE beautiful girls...") - stripped
# separately from `_STOPWORDS` because they carry preference-framing
# signal, not subject-matter signal, and would otherwise pad the overlap
# denominator in the note's favor.
_PREFERENCE_FRAMING_WORDS = frozenset({
    "user", "likes", "like", "prefers", "prefer", "wants", "want",
    "generate", "generates", "generating", "generated", "generation",
    "creates", "creating", "create", "created", "produces", "producing",
    "produce", "produced", "asks", "asked", "asking", "requests",
    "requested", "requesting", "tends", "typically", "usually", "always",
    "often", "generally",
})

# A candidate note needs at least this many content words before the
# overlap check applies at all - too few and any overlap looks total.
_MIN_CONTENT_WORDS_FOR_OVERLAP_CHECK = 3

# >= this fraction of the note's own content words showing up in ANY ONE
# user turn means the note is that turn's subject matter, not a pattern -
# UNLESS that turn itself already reads as a preference statement in the
# user's own words (see `_looks_like_a_preference_statement`): a note that
# closely restates "I always want short captions" is a legitimate close
# paraphrase of something the user actually said as a preference, not a
# generation request laundered into one - the overlap alone can't tell
# those apart, so the cue check is what draws the line.
_CONTENT_OVERLAP_REJECT_THRESHOLD = 0.6

# Cues that mark a TURN (not the candidate note) as already being phrased as
# a standing preference/habit rather than a one-off request - deliberately a
# different, smaller set than `_PREFERENCE_FRAMING_WORDS` above: a plain
# generation request can legitimately contain "generate"/"create" without
# being a preference statement, but these cues are specific to a user
# describing what they generally want.
_PREFERENCE_CUE_RE = re.compile(
    r"\b(prefer\w*|always|never|instead|rather|usually|typically|generally|"
    r"tend\w*|habit\w*|don'?t|from now on|every time)\b",
    re.IGNORECASE,
)


def _looks_like_a_preference_statement(turn_text: str) -> bool:
    return bool(_PREFERENCE_CUE_RE.search(turn_text))


def _normalize_word(word: str) -> str:
    """A crude plural fold (girls -> girl, tshirts -> tshirt) applied
    identically to note content and turn text, so the two sides compare on
    the same footing - not meant to be linguistically correct, only
    consistent."""
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _content_words(text: str) -> frozenset:
    words = (_normalize_word(w.lower()) for w in _CONTENT_WORD_RE.findall(text))
    return frozenset(w for w in words if w not in _STOPWORDS and w not in _PREFERENCE_FRAMING_WORDS)


@dataclass
class _ReflectionSpan:
    """One bounded slice of the unreflected transcript, ready to send.

    ``end_message_id``/``end_offset`` is the cursor position the span
    actually covers — passed to ``record_memory_reflection`` only after the
    model call and persistence both succeed. ``end_offset`` counts
    characters of ``end_message_id`` covered so far: when that message is
    covered IN FULL it is the message's whole content length (never 0 -
    0 would compare as *earlier* than a prior partial offset over the same
    message, which is exactly what let a completing pass regress a chunk
    boundary it had already recorded). ``end_seq`` is that message's index
    in the ``messages`` list the span was built from, purely so the
    repository can tell two spans' end positions apart without re-deriving
    message order itself (see its monotonic-advance docstring).
    ``has_backlog`` is True when the budget was hit before every unreflected
    message was covered (a mid-message chunk, or whole messages deferred).

    ``user_turn_texts`` maps the 1-based turn number shown in the transcript
    (``"[3] User: ..."``, see ``_build_span``) to the exact text that turn
    contributed to THIS span - used both to build the prompt's turn numbers
    and to verify a "stated" fact's quote actually appears in the turn it
    cites (see ``_validate_fact_grounding``).
    """

    transcript: str
    end_message_id: Optional[str] = None
    end_offset: int = 0
    end_seq: int = -1
    has_backlog: bool = False
    user_turn_texts: Dict[int, str] = field(default_factory=dict)


@dataclass
class _SpanBudget:
    """How many transcript chars one span may carry, and the accounting
    that produced the number (see ``_resolve_span_budget``).

    ``exhausted`` is True when there is no room even for the fixed
    reflection prompt plus the response reservation, before a single
    character of transcript is considered - `span_chars` is 0 in that case
    and no span can run at all this pass.
    """

    span_chars: int
    capacity_tokens: int
    reserve_tokens: int
    prompt_tokens: int
    exhausted: bool = False


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
        # `trigger`/`_run_pass`. `_latest_form_state` is updated on EVERY
        # trigger (not just the one that started the running task) so a
        # coalesced follow-up covering genuinely new messages reflects them
        # under the most recently active preset/model, not a stale one from
        # whichever trigger happened to start the task - see `_run_pass`.
        self._in_flight: set = set()
        self._rerun_requested: set = set()
        self._latest_form_state: Dict[str, Optional[Dict[str, Any]]] = {}

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
            self._latest_form_state[session_id] = form_state
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

        Context for the follow-up: a BACKLOG follow-up is still finishing
        the very same span (a chunk boundary mid-message) this pass was
        built under, so it keeps this pass's own ``form_state``. A pure
        RERUN follow-up covers messages that arrived after this pass
        started, so it uses whichever ``form_state`` the most recent
        `trigger` call recorded - never the one this task happened to be
        created with, which could be stale by the time the follow-up runs.
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
                follow_up_form_state = form_state if backlog else self._latest_form_state.get(session_id, form_state)
                await self.reflect(session_id, follow_up_form_state, require_threshold=not backlog)
        finally:
            self._in_flight.discard(session_id)
            self._latest_form_state.pop(session_id, None)

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

        # Prompt first: its own token cost has to come out of the budget
        # before we know how much room is left for transcript text.
        active_preset, active_model, active_mode = self._resolve_active_context(session, form_state)
        prompt = self._build_prompt(active_preset, active_model, active_mode)

        config = self._m.llm_service.repository.get_configuration(session.llm_config_id) if session.llm_config_id else None

        budget = self._resolve_span_budget(prompt, config)
        if budget.exhausted:
            # No coverage claimed - nothing changed about the messages
            # themselves, so nothing here would resolve on an immediate
            # retry; only a smaller model, a larger configured
            # context_window, or a shorter active-preset/model label
            # (which lengthens the prompt) changes this outcome.
            logger.warning(
                f"Memory reflection: session {session_id}'s configured context window "
                f"({budget.capacity_tokens} tokens) can't fit the fixed reflection prompt "
                f"({budget.prompt_tokens} tokens) plus the {budget.reserve_tokens}-token "
                "response reservation - no span can run this pass."
            )
            return []

        span = self._build_span(session, messages, budget.span_chars)
        if not span.transcript:
            return []

        # `budget.span_chars` is only a starting estimate - it prices the
        # fixed prompt (and, when a real whole-request counter is
        # available, the configured system message and template overhead
        # too - see `_resolve_span_budget`) but the ONLY way to know this
        # exact candidate will actually clear the real send's enforcement
        # is to ask it: validate against `LLMGateway.estimate_context_budget`
        # (the same call `_budgeted` makes before every real send) and
        # shrink if it doesn't fit, so this pass never repeatedly builds a
        # request the gateway is guaranteed to reject.
        span, exhausted = self._fit_span_to_real_request(session, messages, config, prompt, span)
        if exhausted:
            logger.warning(
                f"Memory reflection: session {session_id}'s real request budget rejected "
                "every candidate span down to the smallest attempted - no span can run this pass."
            )
            return []
        if not span.transcript:
            return []

        try:
            with trace_collector.activate(session_id, session.user_id, purpose="memory_reflection"):
                response = await self._m.llm_service.generate_with_history(
                    messages=[{"role": "user", "content": f"{prompt}\n\n---\n\n{span.transcript}"}],
                    llm_id=session.llm_config_id,
                    options_override=_REFLECTION_OPTIONS_OVERRIDE,
                )
        except Exception as e:
            # Failure: no coverage claimed, this span stays eligible. Not
            # retried immediately - the next natural trigger (a new turn,
            # or a coalesced follow-up already in flight) will see the same
            # unreflected span and try again, never a tight retry loop here.
            logger.warning(f"Memory reflection failed for session {session_id}: {e}")
            return []

        items = self._parse_items(response.content if response else None)
        if items is None:
            # The model's output had no reasonable interpretation (missing,
            # not JSON, no array found, or a non-array top level) - distinct
            # from a VALID empty array, which is a real "nothing durable"
            # answer. No coverage claimed either way: the span stays
            # discoverable and is picked up by the next natural trigger or
            # coalesced follow-up, never retried immediately from here.
            logger.warning(
                f"Memory reflection: unparseable model output for session {session_id}; no coverage claimed"
            )
            return []

        saved = self._persist_items(
            session.user_id, items,
            active_preset_id=active_preset[0] if active_preset else None,
            active_model_id=active_model[0] if active_model else None,
            active_mode_id=active_mode[0] if active_mode else None,
            user_turn_texts=span.user_turn_texts,
        )

        # A valid empty extraction (the model found nothing durable) still
        # advances the cursor - only the malformed-output and call-failure
        # cases above skip this. `record_memory_reflection` itself only
        # moves the cursor forward, so a slow pass finishing after a newer
        # one can't clobber it; a raised persistence failure is treated the
        # same as a rejected (non-monotonic) write - no coverage claimed.
        try:
            recorded = self._m.chat_repository.record_memory_reflection(
                session_id, span.end_message_id,
                offset=span.end_offset, seq=span.end_seq,
                pending_backlog=span.has_backlog,
            )
        except Exception as e:
            logger.warning(f"Memory reflection: failed to persist cursor for session {session_id}: {e}")
            recorded = False
        if not recorded:
            logger.info(
                f"Memory reflection: cursor for session {session_id} not advanced "
                "(a newer pass already covers this span, or the write failed)"
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

    def _resolve_span_budget(self, prompt: str, config: Optional[Any]) -> "_SpanBudget":
        """A STARTING ESTIMATE of how many transcript chars one span may
        carry, after reserving room for the fixed reflection prompt and the
        model's response - using the SAME accounting ``LLMGateway`` enforces
        on the real wire call (``LLMGateway.accounting_inputs_for``, given
        the actual ``_REFLECTION_OPTIONS_OVERRIDE`` this call will use).
        When a whole-request counter is available (``inputs.messages_counter``
        - the provider's real chat-template framing, INCLUDING the configured
        system message), it prices the fixed prompt AS a whole request rather
        than as bare prompt text, so a non-trivial configured system message
        or template overhead is already accounted for here rather than
        discovered only when the real send rejects the candidate. Falls back
        to ``count_text`` on the prompt alone when no whole-request counter
        exists. This is still only a candidate to start from, never the last
        word - ``_fit_span_to_real_request`` validates (and shrinks) the
        actual candidate against the real budget check before it is ever
        sent, so an estimate that undercounts something (a per-message
        framing cost this whole-request count doesn't capture, say) still
        can't produce a doomed-to-fail request.

        Degrades to the previous window-only estimate (still reserving the
        response and the prompt's char-estimated cost) when the collaborator
        doesn't implement the real gateway accounting - never fails the pass
        over a missing test double or a provider hook raising.
        """
        if config is None:
            return _SpanBudget(MAX_TRANSCRIPT_CHARS, 0, 0, 0)
        system_message = "" if getattr(config, "disable_system_prompt", False) else getattr(config, "system_message", None)
        try:
            inputs = self._m.llm_service.accounting_inputs_for(config, _REFLECTION_OPTIONS_OVERRIDE)
            capacity_tokens = int(inputs.capacity.capacity_tokens)
            reserve_tokens = int(inputs.reserve_tokens)
            if inputs.messages_counter is not None:
                prompt_tokens = int(inputs.messages_counter(
                    system_message, [{"role": "user", "content": prompt}], None,
                ))
            else:
                prompt_tokens = context_budget.count_text(prompt, inputs.counter).tokens
        except Exception:
            capacity_tokens = context_budget.resolve_capacity(config).capacity_tokens
            reserve_tokens = _REFLECTION_OPTIONS_OVERRIDE["max_tokens"]
            prompt_tokens = math.ceil(len(prompt) / context_budget.DEFAULT_CHARS_PER_TOKEN)

        available_tokens = capacity_tokens - reserve_tokens - prompt_tokens
        if available_tokens <= 0:
            return _SpanBudget(0, capacity_tokens, reserve_tokens, prompt_tokens, exhausted=True)
        span_chars = min(MAX_TRANSCRIPT_CHARS, int(available_tokens * context_budget.DEFAULT_CHARS_PER_TOKEN))
        return _SpanBudget(span_chars, capacity_tokens, reserve_tokens, prompt_tokens)

    def _fit_span_to_real_request(
        self, session: SessionResponse, messages: List[Any], config: Optional[Any], prompt: str, span: "_ReflectionSpan",
    ) -> Tuple["_ReflectionSpan", bool]:
        """Validate ``span`` against the REAL whole-request budget the final
        send enforces (``LLMGateway.estimate_context_budget`` - the exact
        public method ``_budgeted`` calls before every real send, given the
        SAME resolved system message and ``_REFLECTION_OPTIONS_OVERRIDE``
        reserve this call will use), shrinking (halving the char budget and
        re-chunking via ``_build_span``) up to ``_MAX_SPAN_SHRINK_STEPS``
        times when it doesn't. ``_resolve_span_budget``'s estimate can still
        undercount something the real send pays for - so this is the actual
        gate, not a second heuristic layered on top of a guess.

        Degrades to trusting ``span`` as given when the collaborator doesn't
        implement the real budget check (a bare test double) - never blocks
        a pass over a missing accounting hook. Returns ``(span, exhausted)``;
        ``exhausted`` is True only when even the smallest attempted candidate
        (down to an empty transcript) still doesn't fit.
        """
        if config is None:
            return span, False
        system_message = "" if getattr(config, "disable_system_prompt", False) else getattr(config, "system_message", None)
        char_budget = len(span.transcript)
        for _ in range(_MAX_SPAN_SHRINK_STEPS + 1):
            if not span.transcript:
                return span, True
            fits = self._candidate_fits_real_budget(config, system_message, prompt, span.transcript)
            if fits is None or fits:
                return span, False
            char_budget = char_budget // 2
            if char_budget < 1:
                return span, True
            span = self._build_span(session, messages, char_budget)
        return span, True

    def _candidate_fits_real_budget(
        self, config: Any, system_message: Optional[str], prompt: str, transcript: str,
    ) -> Optional[bool]:
        """``True``/``False`` when the real gateway can answer whether this
        EXACT candidate request fits; ``None`` when it can't be asked at all
        (the collaborator doesn't implement ``estimate_context_budget`` - a
        bare test double, or a provider hook raising some other way)."""
        candidate = [{"role": "user", "content": f"{prompt}\n\n---\n\n{transcript}"}]
        try:
            self._m.llm_service.estimate_context_budget(
                config, system_message, candidate, options_override=_REFLECTION_OPTIONS_OVERRIDE,
            )
            return True
        except context_budget.ContextBudgetExceededError:
            return False
        except Exception:
            return None

    def _build_span(self, session: SessionResponse, messages: List[Any], char_budget: int) -> "_ReflectionSpan":
        """Greedily gather whole unreflected messages up to ``char_budget``
        (see ``_resolve_span_budget``).

        Offset policy: a message that doesn't fit whole is deferred to a
        later span in full, UNLESS nothing has been included yet this pass -
        i.e. the message alone exceeds the entire budget - in which case it
        is chunked, taking as much of it (from its own ``start_offset``) as
        fits; the cursor then records the exact offset reached so the next
        span resumes mid-message rather than re-sending or skipping text. A
        message covered IN FULL records its whole content length as the
        offset, never 0 - see ``_ReflectionSpan``'s docstring for why.

        Each USER turn actually included (whole or chunked) is numbered
        sequentially from 1, e.g. ``"[3] User: ..."`` - assistant turns are
        unnumbered. The number is only consumed when the turn's text is
        actually appended, never for a turn deferred to backlog, so numbers
        stay dense within a span. See ``_ReflectionSpan.user_turn_texts``.
        """
        entries = self._unreflected_entries(session, messages)
        parts: List[str] = []
        total = 0
        end_seq, end_id, end_offset = -1, None, 0
        has_backlog = False
        user_turn_texts: Dict[int, str] = {}
        next_turn_no = 1
        for seq, m, start_offset in entries:
            if m.role not in ("user", "assistant"):
                continue
            content = m.content or ""
            text = content[start_offset:]
            if not text.strip():
                end_seq, end_id, end_offset = seq, m.id, len(content)
                continue
            is_user = m.role == "user"
            prefix = f"[{next_turn_no}] User: " if is_user else "Assistant: "
            line = f"{prefix}{text}"
            projected = total + (len(_LINE_SEP) if parts else 0) + len(line)
            if projected <= char_budget:
                parts.append(line)
                total = projected
                end_seq, end_id, end_offset = seq, m.id, len(content)
                if is_user:
                    user_turn_texts[next_turn_no] = text
                    next_turn_no += 1
                continue
            if not parts:
                room = char_budget - len(prefix)
                if room > 0:
                    chunk = text[:room]
                    parts.append(f"{prefix}{chunk}")
                    end_seq, end_id, end_offset = seq, m.id, start_offset + len(chunk)
                    if is_user:
                        user_turn_texts[next_turn_no] = chunk
                        next_turn_no += 1
            has_backlog = True
            break
        return _ReflectionSpan(_LINE_SEP.join(parts), end_id, end_offset, end_seq, has_backlog, user_turn_texts)

    # --- active context / prompt ---

    def _resolve_active_context(
        self, session: SessionResponse, form_state: Optional[Dict[str, Any]]
    ) -> Tuple[Optional[Tuple[str, str]], Optional[Tuple[str, str]], Optional[Tuple[str, str]]]:
        """Resolve the turn's active preset/model/mode into (id, label) pairs
        for the prompt.

        Preset/model come from ``form_state`` (the live turn's Generate-page
        context, only meaningful when that page is actually open). Mode
        comes from ``session.mode`` instead - a stable, immutable field set
        at session creation (``ChatMode.id``) - not from the frontend, since
        a plugin-mode session (e.g. lora-dataset) has no Generate form open
        at all and would otherwise have nothing to scope to but global.

        Best-effort: a label lookup failure still yields the id with itself as
        the label rather than dropping the context entirely, since the id
        alone is what ``_validate_scope`` actually checks a reported
        scope_ref against.
        """
        preset_id = resolve_active_preset_id(form_state)
        active_preset = None
        if preset_id:
            label = preset_id
            if self._m.preset_collaborators:
                try:
                    label = self._m.preset_collaborators.get_preset(preset_id).get("name") or preset_id
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

        active_mode = None
        mode_id = getattr(session, "mode", None)
        if mode_id:
            label = mode_id
            registry = getattr(self._m, "chat_mode_registry", None)
            if registry is not None:
                try:
                    mode = registry.get(mode_id)
                    if mode is not None and getattr(mode, "name", None):
                        label = mode.name
                except Exception:
                    pass
            active_mode = (mode_id, label)

        return active_preset, active_model, active_mode

    @staticmethod
    def _build_prompt(
        active_preset: Optional[Tuple[str, str]],
        active_model: Optional[Tuple[str, str]],
        active_mode: Optional[Tuple[str, str]],
    ) -> str:
        """Compose the reflection prompt with this turn's actual active ids.

        The model is given the real preset/model/mode id and told to reuse it
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

        if active_mode:
            mode_line = (
                f'- Active mode: "{active_mode[1]}" (id: {active_mode[0]}) - use scope '
                f'"mode" with scope_ref exactly "{active_mode[0]}" for facts tied only to it '
                "(e.g. a habit specific to this chat mode's own workflow, not the Generate form)."
            )
        else:
            mode_line = '- No active mode in this conversation - never use scope "mode".'

        return (
            "Review this conversation and extract durable facts worth remembering for "
            "FUTURE, unrelated conversations with this user - not facts about this one "
            "exchange.\n\n"
            "The SUBJECT of a generation request - what the user asked to be drawn, "
            "written, or rendered this one time - is NEVER itself a preference, no "
            "matter how you phrase it. A fact must be either something the user said "
            "about themselves or their workflow, or something that recurred across "
            "separate requests - never the content of a single request restated as if "
            "it were a taste.\n\n"
            "Look for: preferences the user states outright, corrections the user "
            "makes to your work, and requests repeated more than once. Ignore anything "
            "tied to a single generation - a seed, a one-off prompt, a result the user "
            "reacted to only once.\n\n"
            f"{preset_line}\n{model_line}\n{mode_line}\n\n"
            "Each user turn below is numbered, like '[3] User: ...' - use these numbers "
            "as evidence.\n\n"
            "For each fact, pick a scope: 'global' for something true everywhere, or "
            "'preset'/'model'/'mode' for something tied ONLY to the active preset/model/"
            "mode named above - never invent an id, only ever the exact one given "
            "above.\n\n"
            "For each fact, also set 'kind':\n"
            "- 'stated': the user said this outright, in their own words, in ONE turn. "
            "'evidence' is that turn's number as a one-item array, e.g. [3]. 'quote' is "
            "the exact sentence copied verbatim from that turn that the fact rests on.\n"
            "- 'recurring': not stated outright, but the SAME pattern showed up across "
            "separate requests. 'evidence' lists at least 2 distinct turn numbers where "
            "it appeared, e.g. [1, 4]. No 'quote' needed.\n"
            "A fact backed by neither a verbatim quote nor two separate occurrences is "
            "not durable - do not report it.\n\n"
            f"Keep each fact's content under {_TARGET_CONTENT_CHARS} characters.\n\n"
            "Reply with a JSON array only, no other text. Each item:\n"
            '{"scope": "global"|"preset"|"model"|"mode", "scope_ref": "<the exact id '
            'given above, or null for global>", "key": "<short snake_case identifier>", '
            '"content": "<the fact, written as a general statement>", '
            '"kind": "stated"|"recurring", "evidence": [<turn number>, ...], '
            '"quote": "<verbatim quote - only when kind is stated>"}\n\n'
            "If nothing durable came up, reply with an empty array: []"
        )

    # --- parsing / persistence ---

    @staticmethod
    def _parse_items(text: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """Leniently extract a JSON array of memory items from model output.

        Returns ``None`` when the output has no reasonable interpretation as
        an extraction - missing text, no JSON array found, invalid JSON, or
        a non-array top level - distinct from a genuinely empty array
        (``[]``), which returns ``[]``: the caller treats these as different
        outcomes (a malformed/missing response claims no coverage; a valid
        empty extraction does).
        """
        if not text:
            return None
        stripped = _THINK_BLOCK_RE.sub("", text).strip()
        match = _JSON_ARRAY_RE.search(stripped)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, list):
            return None
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
        active_mode_id: Optional[str] = None,
        user_turn_texts: Optional[Dict[int, str]] = None,
    ) -> List[Dict[str, Any]]:
        user_turn_texts = user_turn_texts or {}
        saved: List[Dict[str, Any]] = []
        for item in items:
            key = item.get("key")
            content = item.get("content")
            if not isinstance(key, str) or not key.strip() or not isinstance(content, str) or not content.strip():
                continue
            content = content.strip()

            grounding_issue = self._validate_fact_grounding(item, user_turn_texts)
            if grounding_issue:
                logger.info(f"Reflection item '{key}' dropped: {grounding_issue}")
                continue

            scoped = self._validate_scope(
                item.get("scope"), item.get("scope_ref"),
                active_preset_id, active_model_id, active_mode_id,
            )
            if scoped is None:
                logger.info(
                    f"Reflection item '{key}' dropped: scope '{item.get('scope')}' "
                    f"(scope_ref '{item.get('scope_ref')}') does not match this turn's "
                    "active preset/model/mode - never rewritten to global"
                )
                continue
            scope, scope_ref = scoped

            if self._restates_single_prompt(content, user_turn_texts):
                logger.info(
                    f"Reflection item '{key}' dropped: content mostly restates a single "
                    "user turn's request rather than describing a lasting pattern"
                )
                continue

            try:
                note = memory_operations.write_note(
                    self._m.llm_memory_repository,
                    user_id=user_id,
                    key=self._slugify(key),
                    # Defensive truncation: the prompt asks for shorter notes, but a
                    # prompt instruction is not a length guarantee, and write_note
                    # rejects anything over MAX_CONTENT_LENGTH outright.
                    content=content[:MAX_CONTENT_LENGTH],
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
        active_mode_id: Optional[str] = None,
    ) -> Optional[Tuple[str, Optional[str]]]:
        """Accept a 'preset'/'model'/'mode' scope only when scope_ref names
        exactly the id this turn's actual context resolved for that scope.
        Anything else - an invalid scope name, a mismatched or hallucinated
        scope_ref, or no active id at all for that scope - is DROPPED
        (returns ``None``), never silently rewritten to 'global': 'global' is
        only ever what the model itself deliberately reported, since a
        preset/model/mode-shaped fact with no honest home is not evidence the
        fact is universally true."""
        if scope == "global":
            return "global", None
        if scope == "preset" and active_preset_id and scope_ref == active_preset_id:
            return "preset", active_preset_id
        if scope == "model" and active_model_id and scope_ref == active_model_id:
            return "model", active_model_id
        if scope == "mode" and active_mode_id and scope_ref == active_mode_id:
            return "mode", active_mode_id
        return None

    @staticmethod
    def _validate_fact_grounding(item: Dict[str, Any], user_turn_texts: Dict[int, str]) -> Optional[str]:
        """Code-enforced backstop for extraction quality (see module docstring
        addition in the reflection prompt): a 'stated' fact must trace to a
        verbatim quote in the turn it cites, and a 'recurring' fact must cite
        at least 2 distinct turns. Returns ``None`` when the item is properly
        grounded, else a human-readable reason for the drop.

        Only checks that GROUNDING is present and internally consistent - it
        does not (and cannot) verify a 'recurring' fact's cited turns
        actually contain the pattern; that's beyond what code can check
        without re-reading the model's own judgment. The separate
        ``_restates_single_prompt`` check catches the complementary failure
        mode: a 'stated' fact whose quote is technically real but whose
        CONTENT is just that one turn's request restated as a preference.
        """
        kind = item.get("kind")
        if kind not in ("stated", "recurring"):
            return "missing or invalid 'kind' (must be 'stated' or 'recurring')"

        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(e, int) and not isinstance(e, bool) for e in evidence
        ):
            return "missing or invalid 'evidence' (must be a non-empty array of turn numbers)"

        if kind == "recurring":
            if len(set(evidence)) < 2:
                return "'recurring' fact needs at least 2 distinct turn numbers as evidence"
            return None

        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            return "'stated' fact needs a 'quote' that appears verbatim in the cited turn"
        turn_text = user_turn_texts.get(evidence[0])
        if not turn_text or not ChatReflectionGenerator._normalized_contains(turn_text, quote):
            return "'stated' fact's quote does not appear verbatim in the turn it cites"
        return None

    @staticmethod
    def _normalized_contains(haystack: str, needle: str) -> bool:
        """Whitespace/case-normalized substring check - a quote is still
        "verbatim" across incidental whitespace/case differences a model's
        own transcription can introduce."""
        def _norm(s: str) -> str:
            return re.sub(r"\s+", " ", s.strip().lower())

        normalized_needle = _norm(needle)
        return bool(normalized_needle) and normalized_needle in _norm(haystack)

    @staticmethod
    def _restates_single_prompt(content: str, user_turn_texts: Dict[int, str]) -> bool:
        """True when ``content``'s own (stopword/framing-stripped) words are
        mostly just the words of ONE user turn in the span - i.e. the note is
        that turn's subject matter wearing a preference sentence, not an
        actual pattern.

        Skips a turn that already reads as a preference statement in the
        user's own words (``_looks_like_a_preference_statement``): closely
        restating "I always want short captions" is a legitimate paraphrase
        of a preference the user actually stated, not a generation request
        laundered into one - word overlap alone can't distinguish those, so
        this is the deciding factor for a high-overlap turn.

        See the module-level constants above for the exact thresholds and
        why they're set where they are.
        """
        content_words = _content_words(content)
        if len(content_words) < _MIN_CONTENT_WORDS_FOR_OVERLAP_CHECK:
            return False
        for turn_text in user_turn_texts.values():
            turn_words = _content_words(turn_text)
            if not turn_words:
                continue
            overlap = len(content_words & turn_words)
            if overlap / len(content_words) < _CONTENT_OVERLAP_REJECT_THRESHOLD:
                continue
            if _looks_like_a_preference_statement(turn_text):
                continue
            return True
        return False
