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

Token counting prefers a real tokenizer when one is already available
in-process for free (``counter``, e.g. ``NativeLLMClient.token_counter`` while
its checkpoint is warm) and otherwise falls back to a labelled chars-per-token
estimate (``measured=False`` everywhere the estimate was used) — this module
never claims the chars/4-style shortcut is an exact count.
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


@dataclass(frozen=True)
class BudgetOutcome:
    """What a caller needs after a successful ``enforce_budget`` call."""

    messages: List[Dict[str, Any]]
    ledger: Dict[str, Any] = field(default_factory=dict)


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
    total = 0
    measured = True
    for message in messages:
        for unit in _message_text_units(message):
            c = count_text(unit, counter)
            total += c.tokens
            measured = measured and c.measured
    return TokenCount(total, measured if messages else True)


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


def _protected_unit_count(units: List[List[Dict[str, Any]]]) -> int:
    """How many trailing units are never eligible for trimming.

    Always at least the last unit (the current user turn, or the most recent
    tool-call/result group when a round is mid-loop) plus, walking backward
    from there, every immediately preceding single system-role message — the
    per-turn context blocks (memory/contributor/resource/workspace/prompt
    state/reply-contract) are always inserted as single system messages
    stacked right before the last user message, so this protects that whole
    stack structurally without the caller having to say how many there are.
    """
    if not units:
        return 0
    protected = 1
    i = len(units) - 2
    while i >= 0:
        unit = units[i]
        if len(unit) == 1 and unit[0].get("role") == "system":
            protected += 1
            i -= 1
        else:
            break
    return protected


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
        return TrimResult([], 0, 0, True, 0, True)

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
) -> BudgetOutcome:
    """The one shared budgeting step every send path runs through.

    Accounts system text, tool schemas, a multimodal allowance and the
    reserved output tokens as fixed costs, then trims *messages* (oldest
    eligible whole group first — see ``fit_messages``) to whatever remains.
    Raises ``ContextBudgetExceededError`` when even the protected tail alone
    (plus the fixed costs) doesn't fit — the caller must not submit that
    request.
    """
    reserve = max(0, int(reserve_tokens))
    system_count = count_text(system_message, counter)
    tools_count = count_tool_schemas(tool_schemas, counter)
    image_count = multimodal_allowance(image_data, per_image_tokens=image_tokens)

    fixed_tokens = system_count.tokens + tools_count.tokens + image_count
    available_for_messages = capacity_tokens - reserve - fixed_tokens

    trim = fit_messages(messages, available_tokens=max(0, available_for_messages), counter=counter)

    estimated_tokens = fixed_tokens + trim.used_tokens
    measured = system_count.measured and tools_count.measured and trim.measured

    ledger: Dict[str, Any] = {
        "capacity_tokens": capacity_tokens,
        "capacity_source": capacity_source,
        "reserve_tokens": reserve,
        "estimated_tokens": estimated_tokens,
        "measured": measured,
        "system_tokens": system_count.tokens,
        "tool_schema_tokens": tools_count.tokens,
        "image_tokens": image_count,
        "history_tokens": trim.used_tokens,
        "messages_total": len(messages),
        "messages_sent": len(trim.messages),
        "messages_dropped": trim.dropped_messages,
        "groups_dropped": trim.dropped_groups,
    }

    if available_for_messages < 0 or not trim.fits:
        raise ContextBudgetExceededError(
            capacity_tokens=capacity_tokens,
            capacity_source=capacity_source,
            reserve_tokens=reserve,
            estimated_tokens=estimated_tokens,
            breakdown=ledger,
        )

    return BudgetOutcome(messages=trim.messages, ledger=ledger)
