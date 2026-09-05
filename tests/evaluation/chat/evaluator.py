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
  scenario: every ``tool_calls`` entry names a real tool and its arguments
  satisfy that tool's JSON-schema ``parameters`` (see ``_validate_arguments``
  for the schema subset understood).
- **Scenario checks**, declared in the scenario fixture's ``checks`` list and
  dispatched by ``type`` through ``_CHECKS``. See each ``_check_*`` function's
  docstring for its parameters.

Nothing here calls an LLM or judges subjective quality — that is deliberately
out of scope (see ``docs/chat-evaluation.md``'s rubric section).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class CheckResult:
    check: str
    passed: bool
    detail: str


@dataclass
class ScenarioEvaluation:
    scenario_id: str
    passed: bool
    results: List[CheckResult] = field(default_factory=list)

    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if not r.passed]


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
        if isinstance(value, py_type) and not (t == "integer" and isinstance(value, bool)):
            return True
    return False


def _validate_arguments(schema: Dict[str, Any], arguments: Dict[str, Any], path: str = "") -> List[str]:
    """Minimal JSON-Schema-subset validator: type, required, enum, array items.

    Deliberately not a full draft validator (no ``oneOf``/``$ref``/format) —
    the tool schemas in this codebase are simple object/array/string/enum
    shapes, and a stricter validator would need constant upkeep for every
    schema feature a tool never actually uses.
    """
    errors: List[str] = []
    if not isinstance(arguments, dict):
        return [f"{path or '<args>'}: expected an object, got {type(arguments).__name__}"]

    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    for name in required:
        if name not in arguments:
            errors.append(f"{path}{name}: required argument missing")

    for name, value in arguments.items():
        prop_schema = properties.get(name)
        if prop_schema is None:
            continue  # additionalProperties are allowed unless a tool says otherwise
        if not _type_ok(value, prop_schema.get("type")):
            errors.append(f"{path}{name}: expected type {prop_schema.get('type')}, got {type(value).__name__}")
            continue
        enum = prop_schema.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"{path}{name}: {value!r} not in enum {enum}")
        if prop_schema.get("type") == "array" and isinstance(value, list):
            item_schema = prop_schema.get("items") or {}
            if item_schema.get("type") == "object":
                for i, item in enumerate(value):
                    errors.extend(_validate_arguments(item_schema, item, path=f"{path}{name}[{i}]."))
    return errors


def _assistant_tool_calls(transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every ``{message_index, call}`` pair across all assistant messages."""
    calls = []
    for i, message in enumerate(transcript.get("messages", [])):
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            calls.append({"message_index": i, "call": call})
    return calls


def _check_tool_validity(transcript: Dict[str, Any], tool_schemas: Dict[str, Dict]) -> CheckResult:
    errors = []
    for entry in _assistant_tool_calls(transcript):
        call = entry["call"]
        name = call.get("name")
        schema = tool_schemas.get(name)
        if schema is None:
            errors.append(f"tool call at message {entry['message_index']}: unknown tool '{name}'")
            continue
        params = schema["function"].get("parameters", {})
        errors.extend(
            f"tool call at message {entry['message_index']} ({name}): {e}"
            for e in _validate_arguments(params, call.get("arguments") or {})
        )
    passed = not errors
    return CheckResult(
        "tool_calls_valid",
        passed,
        "all tool calls are known tools with schema-valid arguments" if passed else "; ".join(errors),
    )


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


def _check_dry_run_never_enqueues(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{tool}``: the tool's outcome is a dry preview, never an actual enqueue,
    and the final answer never claims generation started/running.
    """
    tool = params["tool"]
    messages = _tool_messages(transcript, tool)
    if not messages:
        return CheckResult(f"dry_run_never_enqueues:{tool}", False, f"no '{tool}' tool message found")
    outcome = messages[-1].get("outcome")
    live_outcomes = {"enqueued", "started", "queued", "running"}
    outcome_ok = outcome not in live_outcomes
    answer = (_final_answer(transcript) or "").lower()
    live_claims = ["generation started", "generation has started", "it's running", "queued your generation", "started generating"]
    found = [p for p in live_claims if p in answer]
    passed = outcome_ok and not found
    detail = f"outcome={outcome!r}" + (f", false claims: {found}" if found else "")
    return CheckResult(f"dry_run_never_enqueues:{tool}", passed, detail)


def _check_error_then_recovery(transcript: Dict[str, Any], params: Dict[str, Any]) -> CheckResult:
    """``{tool, acknowledgement_keywords, max_repeats?}``: after an error from
    ``tool``, either a later successful call to it or a final answer that
    acknowledges the issue (contains one of ``acknowledgement_keywords``);
    and the tool isn't retried more than ``max_repeats`` (default 2) times.
    """
    tool = params["tool"]
    max_repeats = params.get("max_repeats", 2)
    tool_msgs = _tool_messages(transcript, tool)
    errors = [m for m in tool_msgs if m.get("outcome") == "error"]
    if not errors:
        return CheckResult(f"error_then_recovery:{tool}", False, f"no error recorded for '{tool}'")
    repeats = len(tool_msgs)
    if repeats > max_repeats:
        return CheckResult(f"error_then_recovery:{tool}", False, f"'{tool}' retried {repeats} times, limit {max_repeats}")
    later_success = any(m.get("outcome") not in ("error", None) for m in tool_msgs[len(errors):] or tool_msgs)
    success_after_error = any(m.get("outcome") not in ("error",) and m is not errors[-1] for m in tool_msgs)
    answer = (_final_answer(transcript) or "").lower()
    acknowledged = any(k.lower() in answer for k in params.get("acknowledgement_keywords", []))
    passed = success_after_error or acknowledged
    return CheckResult(
        f"error_then_recovery:{tool}", passed,
        "recovered via a later successful call" if success_after_error
        else ("recovered via an acknowledging final answer" if acknowledged
              else "neither a later successful call nor an acknowledging final answer found"),
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
}


def evaluate_transcript(
    scenario: Dict[str, Any],
    transcript: Dict[str, Any],
    tool_schemas: Dict[str, Dict],
) -> ScenarioEvaluation:
    """Run the structural checks plus the scenario's declared checks."""
    results = [_check_tool_validity(transcript, tool_schemas)]
    for check in scenario.get("checks", []):
        check = dict(check)
        check_type = check.pop("type")
        handler = _CHECKS.get(check_type)
        if handler is None:
            results.append(CheckResult(check_type, False, f"unknown check type '{check_type}'"))
            continue
        results.append(handler(transcript, check))
    passed = all(r.passed for r in results)
    return ScenarioEvaluation(scenario_id=scenario.get("id", "<unknown>"), passed=passed, results=results)
