"""Model-aware request budgeting shared by every ``LLMGateway`` send path.

The chat history token budget (``conversation._apply_history_budget``) trims
*displayed* history against an operator-configured setting before any of the
per-turn context blocks (contributor, resource, memory, workspace, prompt
state, reply-contract reminder) or tool schemas are added, and it never looks
at what the target model can actually hold. That leaves the request the model
receives unbounded: a tool loop resends the whole growing message list every
round with no re-check, so a long tool conversation can silently exceed the
model's real context window.

This module is the one place request size is measured and enforced against
that real capacity. ``LLMGateway`` calls ``enforce_budget`` immediately before
every wire call (the first turn AND every tool-loop round AND the final
wrap-up — see ``LLMGateway._budgeted``), so no provider path can bypass it.
``ChatContextBuilder``/``ConversationRunner`` also call it once, before the
first call, purely to report an honest pre-flight number into the persisted
context ledger (see ``ConversationRunner._build_context_ledger``) — same
function, same numbers, not a second heuristic.

Capacity is read from explicit configuration only (``provider_options`` —
either the generic ``context_window`` key or, for an Ollama config, the
existing ``num_ctx`` knob already wired into the request). Nothing here ever
infers a model's window from its name or parameter count: an unconfigured
capacity is reported honestly as ``capacity_source="unknown"`` with a
conservative, clearly-labelled default (see ``UNKNOWN_CAPACITY_TOKENS``)
rather than a guess dressed up as a fact.

Token counting has three tiers, reported per turn as ``accounting`` in the
ledger:

- ``"chat_template"`` / ``"chat_template_shrunk"`` — a whole-request count via
  a warm native checkpoint's own tokenizer, applying its chat template (and,
  when tools are given, the SAME prompt-injected tool text the real send
  folds into the system message — see ``MessagesCounter``) to the
  actually-kept system + history exactly as ``NativeLLMClient`` would before
  generating (see ``NativeLLMClient.messages_token_counter``). The only tiers
  that report ``measured=True`` (and only when no image is attached — a
  successful exact count already includes the tools, so nothing else is
  estimated) — everything else is honestly an estimate, never presented as
  exact. The fragment-based trim below decides an initial candidate; when a
  whole-request counter is available, ``enforce_budget`` then RECOUNTS that
  candidate exactly and, if it's over budget, drops the next-oldest eligible
  unit and recounts again (bounded by how many eligible units remain) —
  ``"chat_template"`` means the first recount already fit,
  ``"chat_template_shrunk"`` means one or more extra units had to go (see
  ``chat_template_extra_dropped`` in the ledger). This can go either way
  relative to the fragment estimate: the exact count may fit a candidate the
  fragment/framing estimate thought didn't (framing is deliberately
  conservative), or it may need to drop more than the fragment estimate
  alone would have.
- ``"fragments+framing"`` — a real per-fragment tokenizer (``counter``, e.g.
  ``NativeLLMClient.token_counter``) summed message-by-message, plus
  ``FRAMING_TOKENS_PER_MESSAGE`` per message. A per-fragment sum never sees
  the chat template's role/special-token wrapping, so even with a real
  tokenizer behind it this tier is always ``measured=False`` — it is what
  drives the incremental per-unit trimming decision (a whole-request
  chat-template count can't tell you what ONE candidate message costs). Also
  the tier a whole-request counter falls back to if it raises partway
  through the recount loop (``"chat_template_fallback"``, in the ledger's
  ``accounting`` — the fit decision it fell back to is genuinely the plain
  fragment one, so the reported number is that one, not a half-shrunk
  chat-template attempt).
- ``"estimate"`` — no tokenizer at all (Ollama/OpenAI never have one
  in-process): the labelled chars-per-token heuristic throughout.

An attached image always forces ``measured=False`` (its cost is inherently an
estimate — see ``multimodal_allowance``) regardless of which tier text
accounting used.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.features.llm.repository import LLMConfig

logger = logging.getLogger(__name__)

# Conservative labelled fallback for `count_text` when no real tokenizer is
# available: real chat text tokenizes closer to ~4 chars/token, but a smaller
# divisor means the estimate never UNDER-counts by much, which matters since
# this number gates whether a request is even submitted.
DEFAULT_CHARS_PER_TOKEN = 3.5

# Conservative per-message allowance for the chat-template role/special-token
# wrapping a per-fragment count never sees (e.g. a `<|im_start|>role\n ...
# <|im_end|>\n`-style wrapper) — applied once per message whenever accounting
# falls back to summing fragments (with or without a real per-fragment
# tokenizer), so that tier never quietly under-reports what the wire actually
# carries. This is also why "fragments+framing" is never `measured=True`: the
# allowance is itself an estimate layered on top of whatever the fragments
# measured.
FRAMING_TOKENS_PER_MESSAGE = 4

# Capacity used when a config carries no explicit context-window
# configuration and no provider metadata is available — deliberately close to
# the long-standing `chat_history_token_budget` default (8000) so an
# unconfigured deployment's behaviour doesn't change much, while still being
# clearly labelled `capacity_source="unknown"` rather than passed off as read
# from the model.
UNKNOWN_CAPACITY_TOKENS = 8192

# Per-image allowance used when the provider has no known per-image token
# cost of its own — deliberately conservative (near OpenAI's documented
# low-detail image cost) since undercounting here is what lets a request
# through that the provider then rejects.
DEFAULT_IMAGE_TOKEN_ESTIMATE = 1100

# Reserved-output fallback when no config/override is available (only reached
# from a caller — e.g. a direct context_budget unit test — that has no
# LLMConfig at hand; every real call site passes the config's own max_tokens).
DEFAULT_RESERVE_TOKENS = 1024

TokenCounter = Callable[[str], int]
# A whole-request counter: (system_message, kept_messages, tool_schemas) ->
# exact token count of the ACTUALLY PREPARED request via a real chat template
# (see `NativeLLMClient.messages_token_counter`) — when tool_schemas is given
# and the provider is prompt-injected-tools, the count already includes the
# tool text folded into the system message exactly as the real send builds
# it, so `enforce_budget` never adds a separate tool-schema estimate on top
# of a count this counter produced. Unlike `TokenCounter` this can't price a
# single candidate message in isolation, so it is only used on the final kept
# set `fit_messages` already decided on (and its shrink-loop candidates —
# see `enforce_budget`), never inside the incremental per-unit trimming walk
# itself. A provider that can't reproduce its own prepared tool-request shape
# should raise rather than approximate — `enforce_budget` then falls back to
# the fragment/framing tier exactly as it does for any other counter failure.
MessagesCounter = Callable[[Optional[str], List[Dict[str, Any]], Optional[List[Dict[str, Any]]]], int]


@dataclass(frozen=True)
class CapacityInfo:
    """The effective context-window size for one call, and where it came from."""

    capacity_tokens: int
    source: str  # "config" | "provider_metadata" | "unknown"


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    measured: bool  # True only when a real tokenizer produced this number


@dataclass(frozen=True)
class TrimResult:
    messages: List[Dict[str, Any]]
    dropped_messages: int
    dropped_groups: int
    fits: bool
    used_tokens: int
    measured: bool
    # The same selection as `messages`, grouped back into atomic units
    # (oldest-first) instead of flattened — what `enforce_budget`'s exact
    # whole-request recount loop shrinks from, one unit at a time, without
    # having to re-derive grouping from the flat list.
    kept_units: List[List[Dict[str, Any]]]
    # How many of the trailing `kept_units` are protected (the current-turn
    # stack — see `_protected_unit_count`) and therefore never eligible for
    # the recount loop to drop either.
    protected_count: int


@dataclass(frozen=True)
class BudgetOutcome:
    """What a caller needs after a successful ``enforce_budget`` call."""

    messages: List[Dict[str, Any]]
    ledger: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccountingInputs:
    """The (capacity, reserve, tokenizer(s), per-image override) bundle for
    one ``LLMConfig`` — built ONCE by ``LLMGateway.accounting_inputs_for``
    and passed to ``enforce_budget`` by every caller (every real send AND
    ``ConversationRunner``'s pre-flight ledger check), so the two can never
    compute different numbers for the same request.
    """

    capacity: CapacityInfo
    reserve_tokens: int
    counter: Optional[TokenCounter] = None
    messages_counter: Optional[MessagesCounter] = None
    image_tokens_override: Optional[int] = None


class ContextBudgetExceededError(Exception):
    """Even after trimming every eligible older message, the request still
    doesn't fit the model's context window.

    Carries the numbers that produced the refusal so a caller can surface an
    actionable message instead of letting an oversized request reach the
    provider and fail opaquely (or, worse, silently truncate something that
    matters).
    """

    def __init__(
        self,
        *,
        capacity_tokens: int,
        capacity_source: str,
        reserve_tokens: int,
        estimated_tokens: int,
        breakdown: Dict[str, Any],
    ) -> None:
        self.capacity_tokens = capacity_tokens
        self.capacity_source = capacity_source
        self.reserve_tokens = reserve_tokens
        self.estimated_tokens = estimated_tokens
        self.available_tokens = max(0, capacity_tokens - reserve_tokens)
        self.over_by_tokens = estimated_tokens - self.available_tokens
        self.breakdown = breakdown
        super().__init__(
            f"Context budget exceeded: this turn needs an estimated "
            f"{estimated_tokens} tokens but only {self.available_tokens} are "
            f"available ({capacity_tokens}-token capacity [{capacity_source}] "
            f"minus {reserve_tokens} reserved for output), over by "
            f"{self.over_by_tokens} tokens even after trimming every eligible "
            f"older message."
        )


def resolve_capacity(config: "LLMConfig") -> CapacityInfo:
    """The effective context-window size for *config*.

    Reads explicit configuration only, in order:
    1. ``provider_options.context_window`` — generic, any provider type.
    2. ``provider_options.num_ctx`` — the existing Ollama context-size knob
       (see ``ollama_wire.OLLAMA_OPTION_KEYS``), honoured here too so setting
       it continues to mean the same thing it already does on the wire.
    3. ``UNKNOWN_CAPACITY_TOKENS``, labelled ``capacity_source="unknown"`` —
       never a guess derived from the model's name or parameter count.
    """
    provider_opts = getattr(config, "provider_options", None) or {}

    explicit = provider_opts.get("context_window")
    if _is_positive_number(explicit):
        return CapacityInfo(int(explicit), "config")

    if getattr(config, "type", None) == "ollama":
        num_ctx = provider_opts.get("num_ctx")
        if _is_positive_number(num_ctx):
            return CapacityInfo(int(num_ctx), "config")

    return CapacityInfo(UNKNOWN_CAPACITY_TOKENS, "unknown")


def resolve_image_token_override(config: "LLMConfig") -> Optional[int]:
    """An operator-configured override for the per-image token cost —
    ``provider_options.image_token_estimate``, documented alongside
    ``context_window``/``num_ctx``. ``None`` when unset or not a positive
    number; ``multimodal_allowance`` then falls back to
    ``DEFAULT_IMAGE_TOKEN_ESTIMATE``.
    """
    provider_opts = getattr(config, "provider_options", None) or {}
    value = provider_opts.get("image_token_estimate")
    return int(value) if _is_positive_number(value) else None


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def count_text(text: Optional[str], counter: Optional[TokenCounter]) -> TokenCount:
    """Token count for *text*: the real tokenizer when *counter* is given and
    doesn't raise, else the labelled chars-per-token estimate."""
    text = text or ""
    if counter is not None:
        try:
            return TokenCount(max(0, int(counter(text))), True)
        except Exception:
            logger.debug("[ContextBudget] token counter failed; falling back to estimate", exc_info=True)
    return TokenCount(math.ceil(len(text) / DEFAULT_CHARS_PER_TOKEN), False)


def _message_text_units(message: Dict[str, Any]) -> List[str]:
    """Every piece of *message* that actually reaches the wire as text —
    content, plus the serialised tool_calls a dispatching assistant turn
    carries (real request payload, not just what's shown in the UI)."""
    units = [message.get("content") or ""]
    tool_calls = message.get("tool_calls")
    if tool_calls:
        try:
            units.append(json.dumps(tool_calls))
        except (TypeError, ValueError):
            units.append(str(tool_calls))
    return units


def count_messages(messages: List[Dict[str, Any]], counter: Optional[TokenCounter]) -> TokenCount:
    """Sum of per-fragment costs across *messages*, plus
    ``FRAMING_TOKENS_PER_MESSAGE`` per message for the chat-template wrapping
    a per-fragment sum can't see. Always ``measured=False`` — see the module
    docstring's "fragments+framing" tier: the framing allowance is itself an
    estimate no matter how the fragments themselves were counted, so this is
    never presented as an exact count. (``enforce_budget`` uses a real
    whole-request chat-template count instead, when one is available, for
    the number it actually reports/enforces; this function only drives the
    incremental per-unit trimming decision, which needs a per-candidate
    cost a whole-request count can't give.)
    """
    total = 0
    for message in messages:
        for unit in _message_text_units(message):
            total += count_text(unit, counter).tokens
    if messages:
        total += FRAMING_TOKENS_PER_MESSAGE * len(messages)
    return TokenCount(total, False)


def count_tool_schemas(
    tool_schemas: Optional[List[Dict[str, Any]]], counter: Optional[TokenCounter]
) -> TokenCount:
    if not tool_schemas:
        return TokenCount(0, True)
    try:
        text = json.dumps(tool_schemas)
    except (TypeError, ValueError):
        text = str(tool_schemas)
    return count_text(text, counter)


def multimodal_allowance(image_data: Optional[str], *, per_image_tokens: Optional[int] = None) -> int:
    """The extra token cost of one attached image — a provider-known cost
    when the caller has one, else ``DEFAULT_IMAGE_TOKEN_ESTIMATE``. Zero when
    no image is attached (the truthiness of *image_data* is all that
    matters — it may be a raw path, not yet base64-decoded)."""
    if not image_data:
        return 0
    return per_image_tokens if per_image_tokens is not None else DEFAULT_IMAGE_TOKEN_ESTIMATE


def _atomic_units(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Group *messages* into the smallest units that must be dropped whole.

    An assistant turn that dispatched tool calls is grouped with the ``tool``
    result messages that immediately follow it — dropping only half of that
    pair would send an assistant turn referencing tool_call_ids with no
    matching result (invalid on every wire format) or a tool result with no
    call to answer. Every other message is its own unit.
    """
    units: List[List[Dict[str, Any]]] = []
    i, n = 0, len(messages)
    while i < n:
        message = messages[i]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            group = [message]
            j = i + 1
            while j < n and messages[j].get("role") == "tool":
                group.append(messages[j])
                j += 1
            units.append(group)
            i = j
        else:
            units.append([message])
            i += 1
    return units


def _current_turn_anchor(units: List[List[Dict[str, Any]]]) -> Optional[int]:
    """Index of the unit that anchors the current turn: the LAST user-role
    unit in the list.

    A user message is always its own atomic unit — `_atomic_units` only ever
    groups an assistant tool-calls message with the `tool` results that
    follow it, never a user message with anything else — so the last unit
    whose sole message has role "user" reliably identifies the turn every
    later unit (its injected context blocks before it, its tool rounds and
    any trailing nudge after it) belongs to, even across several prior
    turns of history.
    """
    for i in range(len(units) - 1, -1, -1):
        unit = units[i]
        if len(unit) == 1 and unit[0].get("role") == "user":
            return i
    return None


def _protected_unit_count(units: List[List[Dict[str, Any]]]) -> int:
    """How many trailing units belong to the current turn and are never
    eligible for trimming.

    The current turn is not simply "the last unit": once a tool loop is
    running, the workflow appends more units AFTER the user message that
    started the turn — the tool-call/result group(s) each round produces,
    and a trailing system nudge appended fresh on the next/final request
    (see ``ToolWorkflow._next_request``/``_final_request``). Protecting only
    the physically-last unit would let the budget silently trim the user's
    own question (and its injected context blocks) out from under a
    still-running tool loop, while reporting the request as fitting.

    Protected, in full — everything from the current-turn anchor (see
    ``_current_turn_anchor``) to the end of the list, PLUS, walking backward
    from the anchor, every immediately preceding single system-role
    message: the per-turn context blocks (memory/contributor/resource/
    workspace/prompt state/reply-contract) always stack directly before the
    user message that triggered them, so this protects that whole stack
    structurally without the caller having to say how many blocks there
    are. No user-role unit found at all (should not happen in practice)
    degrades to protecting just the last unit.
    """
    if not units:
        return 0
    anchor = _current_turn_anchor(units)
    if anchor is None:
        return 1
    protected_from = anchor
    i = anchor - 1
    while i >= 0:
        unit = units[i]
        if len(unit) == 1 and unit[0].get("role") == "system":
            protected_from = i
            i -= 1
        else:
            break
    return len(units) - protected_from


def fit_messages(
    messages: List[Dict[str, Any]],
    *,
    available_tokens: int,
    counter: Optional[TokenCounter],
) -> TrimResult:
    """Keep whole tool-call/result groups from the tail while they fit
    *available_tokens*, dropping the oldest first. Never mutates *messages*.
    """
    units = _atomic_units(messages)
    if not units:
        return TrimResult([], 0, 0, True, 0, True, kept_units=[], protected_count=0)

    protected_count = _protected_unit_count(units)
    protected_units = units[len(units) - protected_count:]
    older_units = units[: len(units) - protected_count]

    protected_counts = [count_messages(u, counter) for u in protected_units]
    kept_units_reversed = list(reversed(protected_units))
    used = sum(c.tokens for c in protected_counts)
    measured = all(c.measured for c in protected_counts) if protected_counts else True

    for unit in reversed(older_units):
        c = count_messages(unit, counter)
        if used + c.tokens > available_tokens:
            break
        kept_units_reversed.append(unit)
        used += c.tokens
        measured = measured and c.measured

    kept_units = list(reversed(kept_units_reversed))
    kept_messages = [m for unit in kept_units for m in unit]
    total_messages = sum(len(u) for u in units)
    kept_message_count = sum(len(u) for u in kept_units)

    return TrimResult(
        messages=kept_messages,
        dropped_messages=total_messages - kept_message_count,
        dropped_groups=len(units) - len(kept_units),
        fits=used <= available_tokens,
        used_tokens=used,
        measured=measured,
        kept_units=kept_units,
        protected_count=protected_count,
    )


def enforce_budget(
    *,
    capacity_tokens: int,
    capacity_source: str,
    reserve_tokens: int,
    system_message: Optional[str],
    messages: List[Dict[str, Any]],
    tool_schemas: Optional[List[Dict[str, Any]]] = None,
    image_data: Optional[str] = None,
    image_tokens: Optional[int] = None,
    counter: Optional[TokenCounter] = None,
    messages_counter: Optional[MessagesCounter] = None,
) -> BudgetOutcome:
    """The one shared budgeting step every send path runs through.

    Accounts system text, tool schemas, a multimodal allowance and the
    reserved output tokens as fixed costs, then trims *messages* (oldest
    eligible whole group first, current-turn units always protected — see
    ``fit_messages``) to an initial candidate. When *messages_counter* is
    given, that candidate is then RECOUNTED exactly (system + kept history +
    tool schemas, via the real chat template — see ``MessagesCounter``)
    regardless of what the fragment estimate concluded: if the exact count
    already fits, it wins even over a fragment estimate that thought
    otherwise (framing is deliberately conservative); if it's over, the
    next-oldest eligible unit is dropped and it's recounted again, bounded by
    how many eligible (non-protected) units remain — see the module
    docstring's ``"chat_template"``/``"chat_template_shrunk"`` tiers. Because
    a successful exact count already includes the tool text (when
    *tool_schemas* is given and the provider is prompt-injected-tools), it is
    used as the WHOLE total for that tier — the separate ``tools_count``
    estimate (JSON-size based) is reported in the ledger for visibility but
    never added a second time on top. A *messages_counter* that raises
    mid-recount abandons the exact path entirely and falls back to the plain
    fragment-based decision (``"chat_template_fallback"``, which DOES add the
    separate tool-schema estimate, same as ``"fragments+framing"``/
    ``"estimate"``) rather than reporting a half-shrunk result. Raises
    ``ContextBudgetExceededError`` when even the protected tail alone doesn't
    fit, by whichever accounting produced the final decision — the caller
    must not submit that request.
    """
    reserve = max(0, int(reserve_tokens))
    system_count = count_text(system_message, counter)
    tools_count = count_tool_schemas(tool_schemas, counter)
    image_count = multimodal_allowance(image_data, per_image_tokens=image_tokens)

    fixed_tokens = system_count.tokens + tools_count.tokens + image_count
    available_for_messages = capacity_tokens - reserve - fixed_tokens
    available_total = capacity_tokens - reserve

    trim = fit_messages(messages, available_tokens=max(0, available_for_messages), counter=counter)

    accounting = "fragments+framing" if counter is not None else "estimate"
    final_units = trim.kept_units
    final_fits = trim.fits
    final_history_and_system_tokens = system_count.tokens + trim.used_tokens
    chat_template_extra_dropped = 0
    exact_includes_tools = False

    if messages_counter is not None:
        kept_units = list(trim.kept_units)
        counter_ok = True
        exact_combined = final_history_and_system_tokens  # overwritten on the first successful recount
        while True:
            flat = [m for unit in kept_units for m in unit]
            try:
                exact_combined = max(0, int(messages_counter(system_message, flat, tool_schemas)))
            except Exception:
                logger.debug(
                    "[ContextBudget] messages_counter failed mid-recount; falling back to the "
                    "fragment-based decision", exc_info=True,
                )
                counter_ok = False
                break
            if exact_combined + image_count <= available_total:
                break
            if len(kept_units) <= trim.protected_count:
                break
            kept_units.pop(0)
            chat_template_extra_dropped += 1

        if counter_ok:
            accounting = "chat_template_shrunk" if chat_template_extra_dropped else "chat_template"
            final_units = kept_units
            final_history_and_system_tokens = exact_combined
            final_fits = exact_combined + image_count <= available_total
            exact_includes_tools = True
        else:
            accounting = "chat_template_fallback"
            chat_template_extra_dropped = 0
            # final_units/final_fits/final_history_and_system_tokens stay at
            # the fragment-based values already set above.

    final_messages = [m for unit in final_units for m in unit]
    all_units = _atomic_units(messages)
    total_messages = sum(len(u) for u in all_units)
    final_message_count = sum(len(u) for u in final_units)
    total_groups = len(all_units)

    tools_added_to_total = 0 if exact_includes_tools else tools_count.tokens
    estimated_tokens = final_history_and_system_tokens + tools_added_to_total + image_count
    measured = exact_includes_tools and image_count == 0

    ledger: Dict[str, Any] = {
        "capacity_tokens": capacity_tokens,
        "capacity_source": capacity_source,
        "reserve_tokens": reserve,
        "estimated_tokens": estimated_tokens,
        "measured": measured,
        "accounting": accounting,
        "chat_template_extra_dropped": chat_template_extra_dropped,
        "system_tokens": system_count.tokens,
        # A JSON-size estimate, reported for visibility even in a
        # "chat_template*" tier where it is NOT part of `estimated_tokens` —
        # the exact recount already folded the real tool text into the
        # system message it counted (see `exact_includes_tools` above).
        "tool_schema_tokens": tools_count.tokens,
        "image_tokens": image_count,
        # Always the fragment/framing figure of the FINAL kept set, even in
        # a "chat_template*" tier — a whole-request count can't be cleanly
        # split back into a history-only share without a second tokenize
        # pass; `estimated_tokens` is the authoritative total, this is
        # diagnostic granularity only.
        "history_tokens": count_messages(final_messages, counter).tokens,
        "messages_total": total_messages,
        "messages_sent": final_message_count,
        "messages_dropped": total_messages - final_message_count,
        "groups_dropped": total_groups - len(final_units),
    }

    if not final_fits:
        raise ContextBudgetExceededError(
            capacity_tokens=capacity_tokens,
            capacity_source=capacity_source,
            reserve_tokens=reserve,
            estimated_tokens=estimated_tokens,
            breakdown=ledger,
        )

    return BudgetOutcome(messages=final_messages, ledger=ledger)
