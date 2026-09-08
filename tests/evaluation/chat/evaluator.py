"""Scores a canned chat transcript against a scenario's declarative checks.

A transcript is a recorded (or hand-authored, for a fixture) sequence of chat
turn messages in the same shape a real tool loop produces: user messages,
assistant messages (with or without ``tool_calls``), and tool-result
messages. This module never equates one exact call sequence with the only
correct solution — every check is a predicate over the transcript ("a call to
X with matching arguments occurred before the final answer"), not an
expected-sequence diff.

Two kinds of checks run:

- **Structural checks**, always run against every transcript regardless of
  scenario: every ``tool_calls`` entry names a tool available to THAT
  scenario (its ``tool_names``, not just anything in the registry) with
  arguments satisfying that tool's real JSON-schema ``parameters`` — type,
  required, enum, numeric bounds, and nested object/array shapes (see
  ``_validate_arguments`` for the schema subset understood).
- **Scenario checks**, declared in the scenario fixture's ``checks`` list and
  dispatched by ``type`` through ``_CHECKS``. See each ``_check_*`` function's
  docstring for its parameters.

Nothing here calls an LLM or judges subjective quality — that is deliberately
out of scope (see ``docs/chat-evaluation.md``'s rubric section).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.features.llm import context_budget


@dataclass
class CheckResult:
    check: str
    passed: bool
    detail: str
    # True for a check this evaluator genuinely cannot decide (e.g. a
    # constraint the argument validator doesn't implement, or a round-count
    # check against a live capture whose wire protocol doesn't expose real
    # round boundaries — see ``evaluate_transcript``'s ``round_boundaries_known``).
    # An unverified result is reported but never gates ``ScenarioEvaluation.passed``
    # and is excluded from ``.failures()`` — reporting "unverified" is the
    # honest alternative to silently deriving a passing (or failing) value.
    unverified: bool = False


@dataclass
class ScenarioEvaluation:
    scenario_id: str
    passed: bool
    results: List[CheckResult] = field(default_factory=list)

    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if not r.passed and not r.unverified]


# --------------------------------------------------------------------------
# Structural: tool-call validity against the real schemas
# --------------------------------------------------------------------------

_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _type_ok(value: Any, json_type: Any) -> bool:
    if json_type is None:
        return True
    types = json_type if isinstance(json_type, list) else [json_type]
    for t in types:
        if t == "null":
            if value is None:
                return True
            continue
        py_type = _JSON_TYPE_MAP.get(t)
        if py_type is None:
            return True  # unknown declared type: don't fail on our own ignorance
        if t in ("integer", "number") and isinstance(value, bool):
            continue  # bool is a Python int/float subtype but never a valid number/integer
        if isinstance(value, py_type):
            return True
    return False


def _validate_numeric_bounds(name: str, value: Any, prop_schema: Dict[str, Any], path: str) -> List[str]:
    """``minimum``/``maximum``/``exclusiveMinimum``/``exclusiveMaximum`` on a numeric property."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return []
    errors = []
    minimum = prop_schema.get("minimum")
    maximum = prop_schema.get("maximum")
    exclusive_min = prop_schema.get("exclusiveMinimum")
    exclusive_max = prop_schema.get("exclusiveMaximum")
    if minimum is not None and value < minimum:
        errors.append(f"{path}{name}: {value} is below minimum {minimum}")
    if maximum is not None and value > maximum:
        errors.append(f"{path}{name}: {value} is above maximum {maximum}")
    if exclusive_min is not None and value <= exclusive_min:
        errors.append(f"{path}{name}: {value} must be greater than {exclusive_min}")
    if exclusive_max is not None and value >= exclusive_max:
        errors.append(f"{path}{name}: {value} must be less than {exclusive_max}")
    return errors


def _validate_array_length(name: str, value: List[Any], prop_schema: Dict[str, Any], path: str) -> List[str]:
    """``minItems``/``maxItems`` on an array property (e.g. ``propose_form_changes.ops``,
    which real-schema-requires at least one op — an empty ``ops: []`` must fail)."""
    errors = []
    min_items = prop_schema.get("minItems")
    max_items = prop_schema.get("maxItems")
    if min_items is not None and len(value) < min_items:
        errors.append(f"{path}{name}: has {len(value)} item(s), below minItems {min_items}")
    if max_items is not None and len(value) > max_items:
        errors.append(f"{path}{name}: has {len(value)} item(s), above maxItems {max_items}")
    return errors


# Schema keywords this validator actually understands and checks. A property
# schema carrying any OTHER JSON-Schema constraint keyword (``pattern``,
# ``format``, ``uniqueItems``, ``const``, ``multipleOf``, ...) is not silently
# treated as satisfied — see ``_unhandled_constraints`` and its use in
# ``_check_tool_validity``, which marks the whole check ``unverified`` rather
# than claiming a constraint it never actually evaluated.
_HANDLED_CONSTRAINT_KEYWORDS = {
    "type", "enum", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minItems", "maxItems", "properties", "required", "items", "description",
    "default", "title",
}


def _unhandled_constraints(prop_schema: Dict[str, Any]) -> List[str]:
    return sorted(set(prop_schema.keys()) - _HANDLED_CONSTRAINT_KEYWORDS)


def _validate_arguments(
    schema: Dict[str, Any], arguments: Dict[str, Any], path: str = "",
) -> Tuple[List[str], List[str]]:
    """Minimal JSON-Schema-subset validator: type, required, enum, numeric
    bounds, array length (``minItems``/``maxItems``), and recursion into
    nested objects/arrays-of-objects. Returns ``(errors, unverified_notes)`` —
    ``unverified_notes`` names every property whose schema carries a
    constraint keyword this validator does not implement (see
    ``_HANDLED_CONSTRAINT_KEYWORDS``), so an unimplemented constraint is
    surfaced rather than silently treated as satisfied.

    ``bool`` is never accepted for a declared ``integer``/``number`` property
    even though Python's ``bool`` is a subtype of ``int`` (see ``_type_ok``).
    """
    errors: List[str] = []
    unverified: List[str] = []
    if not isinstance(arguments, dict):
        return [f"{path or '<args>'}: expected an object, got {type(arguments).__name__}"], []

    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    for name in required:
        if name not in arguments:
            errors.append(f"{path}{name}: required argument missing")

    for name, value in arguments.items():
        prop_schema = properties.get(name)
        if prop_schema is None:
            continue  # additionalProperties are allowed unless a tool says otherwise
        unhandled = _unhandled_constraints(prop_schema)
        if unhandled:
            unverified.append(f"{path}{name}: constraint(s) {unhandled} are not implemented by this validator")
        if not _type_ok(value, prop_schema.get("type")):
            errors.append(f"{path}{name}: expected type {prop_schema.get('type')}, got {type(value).__name__}")
            continue
        enum = prop_schema.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"{path}{name}: {value!r} not in enum {enum}")
        errors.extend(_validate_numeric_bounds(name, value, prop_schema, path))

        prop_type = prop_schema.get("type")
        types = prop_type if isinstance(prop_type, list) else [prop_type]
        if "array" in types and isinstance(value, list):
            errors.extend(_validate_array_length(name, value, prop_schema, path))
        if "object" in types and isinstance(value, dict) and prop_schema.get("properties"):
            sub_errors, sub_unverified = _validate_arguments(prop_schema, value, path=f"{path}{name}.")
            errors.extend(sub_errors)
            unverified.extend(sub_unverified)
        if "array" in types and isinstance(value, list):
            item_schema = prop_schema.get("items") or {}
            item_type = item_schema.get("type")
            item_types = item_type if isinstance(item_type, list) else [item_type]
            item_unhandled = _unhandled_constraints(item_schema)
            if item_unhandled:
                unverified.append(f"{path}{name}[items]: constraint(s) {item_unhandled} are not implemented by this validator")
            for i, item in enumerate(value):
                if "object" in item_types and isinstance(item, dict):
                    sub_errors, sub_unverified = _validate_arguments(item_schema, item, path=f"{path}{name}[{i}].")
                    errors.extend(sub_errors)
                    unverified.extend(sub_unverified)
                elif item_type is not None and not _type_ok(item, item_type):
                    errors.append(f"{path}{name}[{i}]: expected type {item_type}, got {type(item).__name__}")
    return errors, unverified


def _assistant_tool_calls(transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every ``{message_index, call}`` pair across all assistant messages."""
    calls = []
    for i, message in enumerate(transcript.get("messages", [])):
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            calls.append({"message_index": i, "call": call})
    return calls


def _check_tool_validity(
    transcript: Dict[str, Any],
    tool_schemas: Dict[str, Dict],
    allowed_names: Optional[List[str]],
) -> CheckResult:
    """Every tool call names a tool available to THIS scenario, with
    schema-valid arguments.

    ``allowed_names`` is the scenario's own ``tool_names`` (``None`` means
    every registered tool is available). A call to a tool that is real and
    correctly shaped, but simply not one this scenario declared available,
    must fail here too — a transcript that reaches for a tool the scenario
    never offered is not a valid solution just because the tool exists
    somewhere in the registry.
    """
    errors = []
    unverified_notes = []
    for entry in _assistant_tool_calls(transcript):
        call = entry["call"]
        name = call.get("name")
        if allowed_names is not None and name not in allowed_names:
            errors.append(
                f"tool call at message {entry['message_index']}: '{name}' is not in this "
                f"scenario's available tool set {allowed_names}"
            )
            continue
        schema = tool_schemas.get(name)
        if schema is None:
            errors.append(f"tool call at message {entry['message_index']}: unknown tool '{name}'")
            continue
        params = schema["function"].get("parameters", {})
        arg_errors, arg_unverified = _validate_arguments(params, call.get("arguments") or {})
        errors.extend(f"tool call at message {entry['message_index']} ({name}): {e}" for e in arg_errors)
        unverified_notes.extend(f"tool call at message {entry['message_index']} ({name}): {n}" for n in arg_unverified)
    passed = not errors
    is_unverified = bool(unverified_notes)
    if passed and not is_unverified:
        detail = "all tool calls are available to this scenario and schema-valid"
    elif passed:
        detail = "no violation found, but " + "; ".join(unverified_notes)
    else:
        detail = "; ".join(errors) + (("; " + "; ".join(unverified_notes)) if unverified_notes else "")
    return CheckResult("tool_calls_valid", passed, detail, unverified=is_unverified and passed)


# --------------------------------------------------------------------------
# Scenario checks
# --------------------------------------------------------------------------

def _final_answer(transcript: Dict[str, Any]) -> Optional[str]:
    """The last assistant message that carries content and no further tool calls."""
    messages = transcript.get("messages", [])
    for message in reversed(messages):
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            content = message.get("content")
            if content:
                return content
    return None


def _last_user_message(transcript: Dict[str, Any]) -> Optional[str]:
    for message in reversed(transcript.get("messages", [])):
        if message.get("role") == "user":
            return message.get("content")
    return None


def _tool_messages(transcript: Dict[str, Any], tool: str) -> List[Dict[str, Any]]:
    return [
        m for m in transcript.get("messages", [])
        if m.get("role") == "tool" and m.get("name") == tool
    ]


def _args_match(arguments: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    for key, value in expected.items():
        if arguments.get(key) != value:
            return False
    return True


def _check_tool_call_present(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{tool, args_contains?, min_count?}``: a call to ``tool`` occurred.

    ``args_contains`` (optional) is a subset of arguments that must match
    exactly on at least one matching call. ``min_count`` (default 1) is the
    minimum number of matching calls.
    """
    tool = params["tool"]
    args_contains = params.get("args_contains")
    min_count = params.get("min_count", 1)
    matches = [
        entry for entry in _assistant_tool_calls(transcript)
        if entry["call"].get("name") == tool
        and (args_contains is None or _args_match(entry["call"].get("arguments") or {}, args_contains))
    ]
    passed = len(matches) >= min_count
    detail = f"{len(matches)} matching call(s) to '{tool}'" + (f" with args containing {args_contains}" if args_contains else "")
    return CheckResult(f"tool_call_present:{tool}", passed, detail)


def _check_final_answer_contains_all(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{keywords}``: the final answer contains every keyword (case-insensitive)."""
    answer = _final_answer(transcript) or ""
    lowered = answer.lower()
    missing = [k for k in params["keywords"] if k.lower() not in lowered]
    passed = not missing
    return CheckResult(
        "final_answer_contains_all", passed,
        "all keywords present" if passed else f"missing from final answer: {missing}",
    )


def _check_final_answer_not_contains(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{phrases}``: the final answer contains none of the phrases (case-insensitive).

    Used for truthfulness: a transcript must not narrate an action as done
    when the recorded tool outcome says otherwise.
    """
    answer = _final_answer(transcript) or ""
    lowered = answer.lower()
    found = [p for p in params["phrases"] if p.lower() in lowered]
    passed = not found
    return CheckResult(
        "final_answer_not_contains", passed,
        "none of the forbidden phrases present" if passed else f"found forbidden phrase(s) in final answer: {found}",
    )


def _check_max_tool_rounds(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{max}``: at most ``max`` assistant messages carry tool calls (one round each)."""
    rounds = sum(1 for m in transcript.get("messages", []) if m.get("role") == "assistant" and m.get("tool_calls"))
    passed = rounds <= params["max"]
    return CheckResult("max_tool_rounds", passed, f"{rounds} tool round(s), limit {params['max']}")


def _check_latest_question_reflected(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{keywords}``: the final answer reflects the LATEST user turn, not an earlier one.

    Guards against a budget/history bug silently dropping the current
    question while an older one lingers in context.
    """
    answer = (_final_answer(transcript) or "").lower()
    matched = [k for k in params["keywords"] if k.lower() in answer]
    passed = bool(matched)
    return CheckResult(
        "latest_question_reflected", passed,
        f"final answer reflects latest question via {matched}" if passed
        else f"final answer does not mention any of {params['keywords']} from the latest user turn",
    )


def _check_truthful_apply_status(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{tool, forbidden_claims}``: if ``tool``'s recorded outcome isn't
    ``"applied"``, the final answer must not use any ``forbidden_claims`` phrase.

    The outcome comes from the tool message's own ``outcome`` field (set by
    whatever recorded the transcript), not from re-parsing ``content`` — a
    canned fixture states its outcome explicitly rather than the evaluator
    guessing it from prose.
    """
    tool = params["tool"]
    messages = _tool_messages(transcript, tool)
    if not messages:
        return CheckResult(f"truthful_apply_status:{tool}", False, f"no '{tool}' tool message found")
    outcome = messages[-1].get("outcome")
    if outcome == "applied":
        return CheckResult(f"truthful_apply_status:{tool}", True, "outcome was 'applied'; no claim restricted")
    answer = (_final_answer(transcript) or "").lower()
    found = [p for p in params["forbidden_claims"] if p.lower() in answer]
    passed = not found
    return CheckResult(
        f"truthful_apply_status:{tool}", passed,
        f"outcome '{outcome}' correctly not narrated as applied" if passed
        else f"outcome was '{outcome}' but final answer falsely claims: {found}",
    )


_DRY_RUN_OUTCOMES = {"pending_approval", "preview", "dry_run"}
_LIVE_OUTCOMES = {"enqueued", "started", "queued", "running"}


def _check_dry_run_never_enqueues(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{tool}``: the tool's outcome is POSITIVELY evidenced as a dry preview
    (one of ``_DRY_RUN_OUTCOMES``) — never an actual enqueue, and never a
    missing/unrecognized outcome either. Absence of a live-outcome label is
    not proof of a dry run; only an explicit preview/pending_approval outcome
    is. The final answer must also never claim generation started/running.
    """
    tool = params["tool"]
    messages = _tool_messages(transcript, tool)
    if not messages:
        return CheckResult(f"dry_run_never_enqueues:{tool}", False, f"no '{tool}' tool message found")
    outcome = messages[-1].get("outcome")
    outcome_ok = outcome in _DRY_RUN_OUTCOMES
    answer = (_final_answer(transcript) or "").lower()
    live_claims = ["generation started", "generation has started", "it's running", "queued your generation", "started generating"]
    found = [p for p in live_claims if p in answer]
    passed = outcome_ok and not found
    if not outcome_ok:
        detail = f"outcome={outcome!r} is not positive dry-run evidence (expected one of {sorted(_DRY_RUN_OUTCOMES)})"
    elif found:
        detail = f"outcome={outcome!r} but final answer falsely claims: {found}"
    else:
        detail = f"outcome={outcome!r} is positive dry-run evidence, no false claim in the final answer"
    return CheckResult(f"dry_run_never_enqueues:{tool}", passed, detail)


# Outcomes a call after the last error can carry that do NOT count as
# recovery: still no evidence of success (None), still failing (error), or
# evidence the action was explicitly NOT carried out (pending_approval —
# still just waiting, stale/rejected — actively not applied). A recovery
# claim needs a POSITIVELY successful outcome, never one of these.
_NON_RECOVERY_OUTCOMES = {"error", None, "pending_approval", "stale", "rejected"}


def _check_error_then_recovery(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{tool, acknowledgement_keywords, max_repeats?}``: after the LAST error
    from ``tool``, either a POSITIVELY successful later call to it (never a
    rejected/stale/pending_approval outcome — see ``_NON_RECOVERY_OUTCOMES``)
    or a final answer that acknowledges the issue (contains one of
    ``acknowledgement_keywords``); and the tool isn't retried more than
    ``max_repeats`` (default 2) times.

    A success recorded BEFORE the last error does not count as recovery — a
    transcript that succeeds, then fails again, and stops there has not
    recovered from that failure regardless of the earlier success.
    """
    tool = params["tool"]
    max_repeats = params.get("max_repeats", 2)
    tool_msgs = _tool_messages(transcript, tool)
    error_indices = [i for i, m in enumerate(tool_msgs) if m.get("outcome") == "error"]
    if not error_indices:
        return CheckResult(f"error_then_recovery:{tool}", False, f"no error recorded for '{tool}'")
    repeats = len(tool_msgs)
    if repeats > max_repeats:
        return CheckResult(f"error_then_recovery:{tool}", False, f"'{tool}' retried {repeats} times, limit {max_repeats}")
    last_error_idx = error_indices[-1]
    success_after_error = any(m.get("outcome") not in _NON_RECOVERY_OUTCOMES for m in tool_msgs[last_error_idx + 1:])
    answer = (_final_answer(transcript) or "").lower()
    acknowledged = any(k.lower() in answer for k in params.get("acknowledgement_keywords", []))
    passed = success_after_error or acknowledged
    return CheckResult(
        f"error_then_recovery:{tool}", passed,
        "recovered via a positively successful call after the last error" if success_after_error
        else ("recovered via an acknowledging final answer" if acknowledged
              else "no positively successful call after the last error, and no acknowledging final answer"),
    )


def _check_capability_declined(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{decline_keywords, false_claim_keywords}``: the final answer declines
    the unsupported capability and makes none of the false claims about it.
    """
    answer = (_final_answer(transcript) or "").lower()
    declined = any(k.lower() in answer for k in params["decline_keywords"])
    false_claims = [k for k in params.get("false_claim_keywords", []) if k.lower() in answer]
    passed = declined and not false_claims
    detail = "declines the capability with no false claim" if passed else (
        f"missing a decline phrase from {params['decline_keywords']}" if not declined
        else f"false claim(s) present: {false_claims}"
    )
    return CheckResult("capability_declined", passed, detail)


def _prior_conversation(transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every message BEFORE the final answer under evaluation — the actual
    prior conversation a real turn would hand to the budget accounting,
    never the answer currently being scored."""
    messages = [m for m in transcript.get("messages", []) if m.get("role") in ("user", "assistant", "system")]
    if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
        messages = messages[:-1]
    return messages


def _check_budget_pressure_observed(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{capacity_tokens, reserve_tokens}``: the conversation's OWN prior
    history genuinely exceeds the stated capacity and was actually trimmed —
    a prose claim of "long history" is not evidence, real accounting is.

    Gated on ``transcript["capture"]`` (``"live"`` or ``"replay"``, defaulting
    to ``"replay"`` for an older canned fixture with no such field):

    - ``"live"`` — a real captured conversation (see ``tests/e2e/harness/chat_eval.py``'s
      ``_run_scenario_live``). Only the REAL backend's own reported
      ``transcript["budget_ledger"]`` counts as evidence here; a live capture
      with no ledger is reported ``unverified``, NEVER recomputed locally —
      recomputing over a live transcript's reconstructed messages would
      silently substitute this evaluator's own token estimate for whatever
      accounting (or lack of it) the real provider actually used, which is
      not "observing" the real turn's pressure, it's guessing at it.
    - ``"replay"`` — an authored canned fixture, which never has a real
      backend ledger by construction. Recomputed via the SAME real
      ``context_budget.enforce_budget`` function over this transcript's own
      prior conversation (everything before the final answer) — never a
      second, invented heuristic. A replay fixture that DOES carry an
      attached ``budget_ledger`` (e.g. a test double) still prefers it, the
      same as a live capture would.

    Reports ``unverified`` (never a silent pass) whenever no source can
    produce a number for the capture kind at hand.
    """
    capture = transcript.get("capture", "replay")
    ledger = transcript.get("budget_ledger")

    if ledger:
        dropped = ledger.get("messages_dropped")
        source = f"the real backend's own reported context_ledger.budget ({capture} capture)"
    elif capture == "live":
        return CheckResult(
            "budget_pressure_observed", False,
            "this is a LIVE capture with no backend-reported budget ledger - pressure cannot be "
            "verified without recomputing against data the real provider never actually saw, so "
            "this is reported unverified rather than a locally-recomputed guess",
            unverified=True,
        )
    else:
        history = _prior_conversation(transcript)
        if len(history) < 2:
            return CheckResult(
                "budget_pressure_observed", False,
                "not enough prior conversation in this transcript to evaluate budget pressure",
                unverified=True,
            )
        outcome = context_budget.enforce_budget(
            capacity_tokens=params["capacity_tokens"],
            capacity_source="test",
            reserve_tokens=params["reserve_tokens"],
            system_message=None,
            messages=history,
            tool_schemas=None,
        )
        dropped = outcome.ledger["messages_dropped"]
        source = (
            f"recomputed via the real context_budget.enforce_budget over this replay transcript's own "
            f"{len(history)} prior message(s) at the stated {params['capacity_tokens']}-token capacity"
        )

    if dropped is None:
        return CheckResult(
            "budget_pressure_observed", False,
            "no messages_dropped figure available from either the live ledger or a local recompute",
            unverified=True,
        )
    passed = dropped > 0
    detail = f"{dropped} message(s) dropped under pressure ({source})" if passed else (
        f"0 messages dropped ({source}) - this transcript's prior conversation did not actually "
        "exceed the stated capacity, so no real pressure was observed"
    )
    return CheckResult("budget_pressure_observed", passed, detail)


_CHECKS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], CheckResult]] = {
    "tool_call_present": _check_tool_call_present,
    "final_answer_contains_all": _check_final_answer_contains_all,
    "final_answer_not_contains": _check_final_answer_not_contains,
    "max_tool_rounds": _check_max_tool_rounds,
    "latest_question_reflected": _check_latest_question_reflected,
    "truthful_apply_status": _check_truthful_apply_status,
    "dry_run_never_enqueues": _check_dry_run_never_enqueues,
    "error_then_recovery": _check_error_then_recovery,
    "capability_declined": _check_capability_declined,
    "budget_pressure_observed": _check_budget_pressure_observed,
}


def evaluate_transcript(
    scenario: Dict[str, Any],
    transcript: Dict[str, Any],
    tool_schemas: Dict[str, Dict],
    round_boundaries_known: Optional[bool] = None,
) -> ScenarioEvaluation:
    """Run the structural checks plus the scenario's declared checks.

    ``round_boundaries_known`` defaults to reading
    ``transcript.get("round_boundaries_known", True)`` — the qualifier lives
    IN the transcript/artifact data itself (set by whatever recorded it —
    ``tests/e2e/harness/chat_eval.py``'s ``_run_scenario_live`` stamps ``False`` onto a
    live capture) rather than being a caller-only flag, so re-scoring a saved
    artifact later reproduces the same result without the caller having to
    remember which recording path produced it. An explicit argument still
    overrides the transcript's own value when a caller genuinely needs to.

    Every canned fixture is authored with one assistant tool-calls message
    per real tool-loop round, so it defaults ``True``. A transcript whose
    per-turn tool calls were folded from a source that doesn't expose real
    intra-turn round boundaries (the live SSE wire protocol emits one
    "thinking" status per TURN, not per round — see
    ``tests/e2e/harness/chat_eval.py``'s ``turn_transcript_messages``) must carry
    ``round_boundaries_known: false``. In that case a scenario's
    ``max_tool_rounds`` check is replaced with an ``unverified`` result
    instead of being computed against the folded (and therefore unreliable)
    grouping — reporting "we can't tell" beats silently deriving a value that
    might read as a pass when the real round count was higher.
    """
    if round_boundaries_known is None:
        round_boundaries_known = transcript.get("round_boundaries_known", True)
    results = [_check_tool_validity(transcript, tool_schemas, scenario.get("tool_names"))]
    for check in scenario.get("checks", []):
        check = dict(check)
        check_type = check.pop("type")
        if check_type == "max_tool_rounds" and not round_boundaries_known:
            results.append(CheckResult(
                "max_tool_rounds", False,
                "intra-turn tool-loop round boundaries are not exposed by the live SSE wire "
                "protocol (one 'thinking' status per turn, not per round) - this check cannot "
                "be evaluated against a live capture and is reported unverified rather than "
                "derived from the folded per-turn tool-call grouping",
                unverified=True,
            ))
            continue
        handler = _CHECKS.get(check_type)
        if handler is None:
            results.append(CheckResult(check_type, False, f"unknown check type '{check_type}'"))
            continue
        results.append(handler(transcript, check))
    passed = all(r.passed for r in results if not r.unverified)
    return ScenarioEvaluation(scenario_id=scenario.get("id", "<unknown>"), passed=passed, results=results)
