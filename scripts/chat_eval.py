#!/usr/bin/env python3
"""Chat and tool evaluation pack: reproducible scenario replay and a bounded
live comparison across explicitly selected model configurations.

Developer tooling only — no public UI, no automatic routing, no benchmark
score. See ``docs/chat-evaluation.md`` for the full write-up; this docstring
covers the two entry points and the report schema.

Entry points
------------

``replay`` (the DEFAULT when no subcommand is given) — fully offline. Loads
every scenario fixture under ``tests/evaluation/chat/scenarios/``, validates
its shape and that its declared tools resolve against the real tool registry
(``tests/evaluation/chat/tool_snapshot.py``), and scores each scenario's
canned ``<id>.good`` transcript against the scenario's declared checks (see
``tests/evaluation/chat/evaluator.py``). Exits non-zero on any evaluator
regression. No network, no provider, no side effects.

``run --config <llm config id or name>`` — drives the REAL chat HTTP API
(session create + the SSE streaming send endpoint) against an ALREADY
RUNNING PotionUI backend and an EXISTING, explicitly named LLM configuration.
Refuses to run without an explicit ``--config`` (prints what it would do and
exits non-zero). Only scenarios whose fixture declares ``live_supported:
true`` are actually driven live — for every other scenario a scenario's own
canned fixture state (a specific model id, a wizard draft, an induced tool
error, ...) cannot be faithfully reproduced against a real backend's real
state, so it is reported as unsupported (``http_completed: null``,
``passed: false``) rather than silently skipped or falsely marked passed.
For a live-supported scenario, the EXACT ``SendMessageRequest.context_metadata``
payload the scenario fixture declares (``live_context_metadata``) is sent on
every turn — the same field the real frontend uses to carry `form_state`
into ``ToolContext.session_metadata`` (see
``src/features/chat/conversation.py`` and
``src/features/llm/tools/builtin/form_context_tool.py``); most scenarios need
none at all (the necessary facts are in the user's own message text).

Every mutating tool a scenario can reach (``update_form_settings``,
``start_generation``, ``propose_form_changes``, ``run_generation``, ...) is
already approval-gated in the real tool loop — its ``execute()`` only
returns a ``pending_approval`` preview. ``run`` never calls the
tool-approval endpoint, so those calls always stay a dry preview. The one
builtin tool that mutates state WITHOUT an approval gate is ``write_memory``
(see ``docs/chat-memory.md``); it is excluded from every session's
``enabled_tools`` for that reason (``_DRY_RUN_EXCLUDED_TOOLS``). ``run``
never calls this repository's own inference code directly, but an
explicitly selected ``native`` configuration's checkpoint IS loaded lazily
by the backend itself on first use, in-process, exactly as it would for any
other real chat turn — this command doesn't add inference, it just doesn't
avoid the inference the backend would run anyway for the config the caller
named.

``--variant compact|large`` runs the same scenarios again against a SECOND
configuration for comparison, using an EXPLICIT ``provider_options.context_window``
override (4096 / 131072 tokens — a test budget label, not a claim about the
model's real capability) — never a value inferred from the model's size, and
NEVER a mutation of the caller's own ``--config``: by default this command
creates a temporary, isolated CLONE of ``--config`` via ``POST
/api/llm/configurations`` and deletes it again once the comparison run
finishes (``finally``-guarded). Cloning cannot carry over a stored API key
(``LLMConfigResponse`` never returns one, by design — see
``src/features/llm/dto.py``), so a provider that needs one won't authenticate
under an auto-clone; pass ``--variant-config <id>`` naming an existing,
separately configured comparison configuration instead (used as-is, no
clone, no window override, no deletion).

Artifacts (``run`` only)
------------------------
Every scenario run against every config/variant gets its OWN artifact file —
``<transcripts_dir>/<run_id>/<variant or 'base'>/<config_id>/<scenario_id>.json``,
where ``run_id`` is unique per ``cmd_run`` invocation — so a base+variant
comparison never has one config's evidence silently overwrite the other's
(the pre-fix behavior: every config/variant wrote the SAME
``{scenario_id}.live.json``, so after a base+variant run both report rows
pointed at whichever config ran last). The artifact holds the RAW per-turn
evidence — the exact request payload sent and the exact raw SSE text/parsed
events received for every turn, including any error — alongside the
reconstructed ``transcript`` (the same shape ``evaluate_transcript`` scores).
A report row's ``transcript`` field names its own artifact file, and
re-loading that file and re-running ``evaluate_transcript`` on its
``transcript`` key reproduces that row's exact ``checks``/``passed`` — see
``tests/scripts/test_chat_eval_cli.py``'s base+variant test.

Report JSON schema (both entry points write the same shape; see
``build_report``)
------------------------------------------------------------------------
::

    {
      "version": 2,
      "generated_at": "<ISO 8601 UTC>",
      "mode": "replay" | "run",
      "app_commit": "<git rev-parse HEAD, short> | null",
      "results": [
        {
          "scenario": "<scenario id>",
          "scenario_version": 2,
          "transcript": "<fixture path>" | "<saved live ARTIFACT path>" | null,
          "variant": "compact" | "large" | "variant-config" | null,
          "http_completed": true | false | null,   # null: never attempted (unsupported live, or replay)
          "passed": true | false,                  # task correctness (evaluate_transcript), not just HTTP success
          "checks": [{"check": "...", "passed": true, "detail": "...", "unverified": false}],
          "provider": {"unverified": true} |
                      {"config_id": "...", "type": "...", "model": "...",
                       "is_default": bool, "unverified": false},
          "thinking_mode": null | {"requested": ..., "effective": "..."},
          "context": {"unverified": true} |
                      {"capacity_tokens": int, "capacity_source": "...",
                       "accounting_tier": "...", "measured": bool,
                       "system_prompt_tokens": int, "tool_schema_tokens": int,
                       "memory_tokens": int, "history_tokens": int, "unverified": false},
          "tokens": {"input": {"value": int|null, "unverified": bool},
                     "output": {"value": int|null, "unverified": bool}},
          "turns_with_tools": int,     # user turns that used >=1 tool (coarse — see note below)
          "tool_executions": int,      # total individual tool calls, across all turns
          "actual_rounds": {"value": int|null, "unverified": bool},
          "tool_durations_ms": [{"tool": "...", "duration_ms": int, "measured": true}],
          "behavior_trace_steps": {"value": [{"step": "...", "duration_ms": int}]|null, "measured": bool},
          "errors": ["..."],
          "latency_ms": {"value": null, "unverified": true},
          "memory_mb": {"value": null, "unverified": true}
        }
      ],
      "summary": {"total": int, "passed": int, "failed": int, "unsupported": int}
    }

``turns_with_tools`` counts USER TURNS that used at least one tool — a
coarse measure, kept separate from ``tool_executions`` (the precise total
count of individual tool calls) so the two are never conflated.
``actual_rounds`` is the exact count of real tool-loop decision rounds
(one LLM call that may dispatch one or more tool calls) — computable and
``unverified: false`` for a ``replay`` canned transcript (authored with one
assistant tool-calls message per real round), but ALWAYS ``unverified: true``
for a ``run`` live capture: the live SSE wire protocol emits exactly one
``status: thinking`` event per TURN, not per round (see
``src/features/chat/conversation.py``), so there is no reliable signal to
recover real intra-turn round boundaries from — reporting "unverified" beats
silently deriving a value from the folded per-turn tool-call grouping that
might read as a pass when the real round count was actually higher. A
scenario's ``max_tool_rounds`` check is likewise reported ``unverified``
(see ``evaluate_transcript``'s ``round_boundaries_known``) rather than scored
against that same unreliable grouping when evaluating a live capture.
``tool_durations_ms``/``behavior_trace_steps`` retain the REAL, backend-measured
per-tool and per-phase timings the persisted ``assistant_message`` already
carries (from its last turn, when there is more than one) — labeled
``measured: true`` because, unlike ``latency_ms``/``memory_mb`` below, this
is an actual number the backend reported, not something this script inferred.

The summary distinguishes ``unsupported`` (a scenario whose fixture declares
``live_supported: false``, reported but never actually attempted) from
``failed`` (a scenario that WAS attempted — replay's canned transcript, or a
live-supported scenario driven for real — and did not pass); ``passed`` +
``failed`` + ``unsupported`` always sums to ``total``.

``"unverified": true`` (or a per-field ``{"value": ..., "unverified": true}``
pair) marks a metric this run could not actually measure — never a silently
guessed number or a bare ``0``/``null`` presented as a known value. Every
field is unverified in ``replay`` mode (no LLM call happens); in ``run`` mode,
latency/memory always are (the HTTP round trip exposes neither), and a token
count or capability flag the provider itself didn't report is marked
unverified rather than defaulted to zero or omitted.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.evaluation.chat import fixtures, tool_snapshot  # noqa: E402
from tests.evaluation.chat.evaluator import evaluate_transcript  # noqa: E402

REPORT_VERSION = 2

# The one builtin tool that mutates durable state without an approval gate
# (see docs/chat-memory.md's write_memory tool) - excluded from every `run`
# session so a live comparison can never write anything, without needing to
# monkeypatch the real backend's tool execution.
_DRY_RUN_EXCLUDED_TOOLS = {"write_memory"}

_VARIANT_CONTEXT_WINDOWS = {
    "compact": 4096,
    "large": 131072,
}

DEFAULT_LIVE_TRANSCRIPTS_DIR = "chat_eval_live_transcripts"


def _git_commit() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unverified_block() -> Dict[str, Any]:
    return {"unverified": True}


def _unverified_tokens_block() -> Dict[str, Any]:
    return {
        "input": {"value": None, "unverified": True},
        "output": {"value": None, "unverified": True},
    }


def _unverified_actual_rounds() -> Dict[str, Any]:
    return {"value": None, "unverified": True}


def _unverified_behavior_trace_steps() -> Dict[str, Any]:
    return {"value": None, "measured": False}


def _empty_result(scenario_id: str, scenario_version: Any, variant: Optional[str], check: str, detail: str) -> Dict[str, Any]:
    return {
        "scenario": scenario_id, "scenario_version": scenario_version, "transcript": None, "variant": variant,
        "http_completed": None, "passed": False,
        "checks": [{"check": check, "passed": False, "detail": detail, "unverified": False}],
        "provider": _unverified_block(), "thinking_mode": None, "context": _unverified_block(),
        "tokens": _unverified_tokens_block(), "turns_with_tools": 0, "tool_executions": 0,
        "actual_rounds": _unverified_actual_rounds(), "tool_durations_ms": [],
        "behavior_trace_steps": _unverified_behavior_trace_steps(),
        "errors": [detail], "latency_ms": {"value": None, "unverified": True}, "memory_mb": {"value": None, "unverified": True},
    }


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------

def _turns_with_tools_count(messages: List[Dict[str, Any]]) -> int:
    """User turns (delimited by ``role: "user"`` messages) that used at least
    one tool anywhere before the next user message — a coarse per-TURN
    measure, distinct from ``actual_rounds`` (the exact per-ROUND count)."""
    count = 0
    turn_has_tool = False
    started = False
    for message in messages:
        if message.get("role") == "user":
            if started and turn_has_tool:
                count += 1
            turn_has_tool, started = False, True
        elif message.get("role") == "assistant" and message.get("tool_calls"):
            turn_has_tool = True
    if started and turn_has_tool:
        count += 1
    return count


def _replay_result(scenario_id: str, scenario: Dict[str, Any], tool_schemas: Dict[str, Dict]) -> Dict[str, Any]:
    transcript_path = fixtures.TRANSCRIPTS_DIR / f"{scenario_id}.good.json"
    transcript = fixtures.load_transcript(transcript_path)
    evaluation = evaluate_transcript(scenario, transcript, tool_schemas)
    # A canned fixture is authored with one assistant tool-calls message per
    # REAL tool-loop round, so this count is precise (unverified: False) —
    # unlike a live capture, where the wire protocol can't tell rounds apart.
    actual_rounds = sum(1 for m in transcript["messages"] if m.get("role") == "assistant" and m.get("tool_calls"))
    tool_executions = sum(len(m.get("tool_calls") or []) for m in transcript["messages"] if m.get("role") == "assistant")
    errors = [
        m.get("content", "")
        for m in transcript["messages"]
        if m.get("role") == "tool" and m.get("outcome") == "error"
    ]
    return {
        "scenario": scenario_id,
        "scenario_version": scenario["version"],
        "transcript": str(transcript_path.relative_to(REPO_ROOT)),
        "variant": None,
        "http_completed": None,  # replay never makes an HTTP call in the first place
        "passed": evaluation.passed,
        "checks": [asdict(r) for r in evaluation.results],
        "provider": _unverified_block(),
        "thinking_mode": None,
        "context": _unverified_block(),
        "tokens": _unverified_tokens_block(),
        "turns_with_tools": _turns_with_tools_count(transcript["messages"]),
        "tool_executions": tool_executions,
        "actual_rounds": {"value": actual_rounds, "unverified": False},
        "tool_durations_ms": [],
        "behavior_trace_steps": _unverified_behavior_trace_steps(),
        "errors": errors,
        "latency_ms": {"value": None, "unverified": True},
        "memory_mb": {"value": None, "unverified": True},
    }


def _is_unsupported(result: Dict[str, Any]) -> bool:
    return any(c["check"] == "live_supported" and not c["passed"] for c in result["checks"])


def build_report(mode: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    unsupported = sum(1 for r in results if _is_unsupported(r))
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed - unsupported
    return {
        "version": REPORT_VERSION,
        "generated_at": _now_iso(),
        "mode": mode,
        "app_commit": _git_commit(),
        "results": results,
        "summary": {"total": total, "passed": passed, "failed": failed, "unsupported": unsupported},
    }


def cmd_replay(args: argparse.Namespace) -> int:
    scenarios = fixtures.load_all_scenarios()
    tool_schemas = tool_snapshot.schemas_by_name()

    if args.scenario:
        missing = [s for s in args.scenario if s not in scenarios]
        if missing:
            print(f"Unknown scenario id(s): {missing}. Known: {sorted(scenarios)}", file=sys.stderr)
            return 2
        selected = {s: scenarios[s] for s in args.scenario}
    else:
        selected = scenarios

    results = []
    for scenario_id in sorted(selected):
        try:
            results.append(_replay_result(scenario_id, selected[scenario_id], tool_schemas))
        except fixtures.FixtureError as e:
            results.append(_empty_result(scenario_id, selected[scenario_id].get("version"), None, "fixture_load", str(e)))

    report = build_report("replay", results)
    _emit_report(report, args.out)

    for r in results:
        status = "UNSUPPORTED" if _is_unsupported(r) else ("PASS" if r["passed"] else "FAIL")
        print(f"[{status}] {r['scenario']}")
        for check in r["checks"]:
            if not check["passed"] or check.get("unverified"):
                tag = "unverified" if check.get("unverified") else "failed"
                print(f"    - ({tag}) {check['check']}: {check['detail']}")

    summary = report["summary"]
    print(f"\n{summary['passed']}/{summary['total']} scenarios passed "
          f"({summary['failed']} failed, {summary['unsupported']} unsupported)")
    return 0 if summary["failed"] == 0 else 1


def _emit_report(report: Dict[str, Any], out: Optional[str]) -> None:
    text = json.dumps(report, indent=2)
    if out:
        Path(out).write_text(text + "\n")
        print(f"Report written to {out}", file=sys.stderr)
    else:
        print(text)


# --------------------------------------------------------------------------
# run — a bounded live comparison against an explicitly selected config
# --------------------------------------------------------------------------

class _HttpError(RuntimeError):
    pass


def _http_request(method: str, url: str, token: Optional[str], payload: Optional[Dict[str, Any]], accept: str) -> str:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json", "Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read().decode()
    except urllib.error.HTTPError as e:
        raise _HttpError(f"{method} {url} -> HTTP {e.code}: {e.read().decode(errors='replace')}") from e
    except urllib.error.URLError as e:
        raise _HttpError(f"{method} {url} -> {e}") from e


def _http_json(method: str, url: str, token: Optional[str], payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return json.loads(_http_request(method, url, token, payload, accept="application/json"))


def _http_text(method: str, url: str, token: Optional[str], payload: Optional[Dict[str, Any]] = None) -> str:
    return _http_request(method, url, token, payload, accept="text/event-stream")


def _check_success(result: Dict[str, Any], context: str) -> None:
    if "success" in result and not result["success"]:
        raise _HttpError(f"{context}: {result.get('error')}: {result.get('message')}")


def parse_sse_events(text: str) -> List[Dict[str, Any]]:
    """Parse the backend's SSE wire format: ``id: <seq>\\nevent: <type>\\ndata: <json>\\n\\n``
    (see ``ChatController._sse_response.formatted`` in ``src/features/chat/routes.py``).

    Pure and network-free so it is directly unit-testable.
    """
    events: List[Dict[str, Any]] = []
    event_type = "message"
    data_lines: List[str] = []

    def flush() -> None:
        if data_lines:
            events.append({"event": event_type, "data": json.loads("\n".join(data_lines))})

    for line in text.splitlines():
        if line == "":
            flush()
            event_type, data_lines[:] = "message", []
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        # "id:" lines mirror seq into data.seq per the backend's own comment; ignored here.
    flush()
    return events


def _outcome_from_tool_execution(execution: Dict[str, Any]) -> Optional[str]:
    if execution.get("pending_approval"):
        return "pending_approval"
    success = (execution.get("result") or {}).get("success")
    if success is False:
        return "error"
    if success is True:
        return "ok"
    return None


def _messages_from_assistant_message(assistant_message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The persisted ``assistant_message`` (from a ``done`` event, or replayed
    from an artifact) -> transcript messages (evaluator.py's shape), never
    including the user's own message (the caller already has that text).

    Built from ``assistant_message.tool_executions`` (the same persisted
    record ``chatStream.ts``'s ``applyDone`` reads) rather than reconstructing
    state from the live ``tool_start``/``tool_end`` deltas — those exist for
    incremental UI rendering, the persisted record is the source of truth.
    """
    messages: List[Dict[str, Any]] = []
    executions = assistant_message.get("tool_executions") or []
    if executions:
        tool_calls = [{"name": te.get("tool_name"), "arguments": te.get("arguments") or {}} for te in executions]
        messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
        for te in executions:
            result = te.get("result") or {}
            messages.append({
                "role": "tool",
                "name": te.get("tool_name"),
                "content": result.get("data") or result.get("error") or "",
                "outcome": _outcome_from_tool_execution(te),
                # Retained (not read by evaluator.py's checks) so a report can
                # surface the backend's own REAL per-tool timing instead of
                # discarding it - see _run_scenario_live's tool_durations_ms.
                "duration_ms": te.get("duration_ms"),
            })
    messages.append({"role": "assistant", "content": assistant_message.get("content") or ""})
    return messages


def turn_transcript_messages(
    user_text: str, events: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """One turn's SSE events -> transcript messages (evaluator.py's shape) +
    the persisted ``assistant_message`` from ``done`` (``None`` if it never arrived).
    Pure and network-free so it is directly unit-testable.
    """
    done = next((e for e in events if e["event"] == "done"), None)
    if done is None:
        return [{"role": "user", "content": user_text}], None
    assistant_message = done.get("data", {}).get("assistant_message") or {}
    return [{"role": "user", "content": user_text}, *_messages_from_assistant_message(assistant_message)], assistant_message


def _resolve_config(base_url: str, token: Optional[str], config_ref: str) -> Dict[str, Any]:
    """Resolve --config by id first, then by exact name, against the real
    ``GET /api/llm/configurations[/{id}]`` envelopes (``APIResponse`` — see
    ``src/platform/http/base_controller.py`` and ``LLMController`` in
    ``src/features/llm/routes.py``): a single ``LLMConfigResponse`` under
    ``data`` for the by-id form, ``{"configurations": [...], "default_provider": ...}``
    under ``data`` for the list form.
    """
    try:
        result = _http_json("GET", f"{base_url}/api/llm/configurations/{config_ref}", token)
        if result.get("success") and result.get("data"):
            return result["data"]
    except _HttpError:
        pass

    result = _http_json("GET", f"{base_url}/api/llm/configurations", token)
    _check_success(result, "GET /api/llm/configurations")
    configs = (result.get("data") or {}).get("configurations") or []
    matches = [c for c in configs if c.get("name") == config_ref]
    if not matches:
        raise _HttpError(f"No LLM configuration found with id or name '{config_ref}'")
    if len(matches) > 1:
        raise _HttpError(f"Multiple LLM configurations named '{config_ref}'; pass the config id instead")
    return matches[0]


def clone_config_request_body(config: Dict[str, Any], context_window: int) -> Dict[str, Any]:
    """A full ``LLMConfigRequest`` body (see ``src/features/llm/dto.py``) cloning
    ``config`` with an explicit ``provider_options.context_window`` test-budget
    override. ``api_key`` is deliberately absent: ``LLMConfigResponse`` never
    returns a stored key (``api_key_set`` only), so a clone of a config that
    needs one will not authenticate — use ``--variant-config`` for those.

    ``memory_reflection`` is always forced to ``False`` regardless of the
    source config's own setting — see ``_MEMORY_POLICY_NOTE``: a generated
    clone exists only for this evaluation run and must never leave a
    background-reflection artifact behind in the user's durable memory.
    """
    provider_options = dict(config.get("provider_options") or {})
    provider_options["context_window"] = context_window
    return {
        "name": f"{config.get('name', config.get('id'))} [chat-eval {context_window}-token test budget]",
        "type": config["type"],
        "enabled": config.get("enabled", True),
        "base_url": config.get("base_url", ""),
        "model": config.get("model", ""),
        "system_message": config.get("system_message", ""),
        "temperature": config.get("temperature", 0.7),
        "max_tokens": config.get("max_tokens", 1000),
        "timeout": config.get("timeout", 30),
        "supports_vision": config.get("supports_vision", False),
        "disable_system_prompt": config.get("disable_system_prompt", False),
        "memory_reflection": False,
        "provider_options": provider_options,
    }


# Evaluation memory policy: every configuration this script actually drives a
# session against (the resolved --config, an explicit --variant-config, or a
# generated clone) must have memory_reflection off. ChatReflectionGenerator
# (src/features/chat/reflection.py) fires a background pass after every 4th
# user message in a session whose config has memory_reflection on, extracting
# and PERSISTING durable-memory facts from the conversation regardless of
# which tools ran or were excluded - write_memory being excluded from
# enabled_tools (see _DRY_RUN_EXCLUDED_TOOLS) does not stop it, because
# reflection is a separate mechanism from the tool loop entirely. This
# pack's own long_history_latest_question scenario sends 26 turns, well past
# that threshold, so a config with reflection on would risk writing benchmark
# facts into the user's real memory on every live run. Checked BEFORE any
# session or message is created for ANY selected config - refusing the whole
# run rather than silently skipping the offending config.
_MEMORY_POLICY_NOTE = (
    "chat_eval.py requires memory_reflection: false on every configuration it evaluates - see "
    "docs/chat-evaluation.md's 'Evaluation memory policy' section."
)


def _preflight_memory_policy(configs_to_check: List[Tuple[str, Dict[str, Any]]]) -> Optional[str]:
    """``configs_to_check``: ``[(cli_flag_label, config), ...]``. Returns an
    error message (never raises) for the first config with reflection on, or
    ``None`` if every config passes. Never mutates the offending config —
    the fix is a dedicated evaluation configuration, not forcing this one off
    out from under the user.
    """
    for label, config in configs_to_check:
        if config.get("memory_reflection", True):
            return (
                f"Refusing to run: the configuration named by {label} ('{config.get('id')}', "
                f"{config.get('name')!r}) has memory_reflection enabled.\n{_MEMORY_POLICY_NOTE}\n"
                "Create a dedicated evaluation configuration with memory_reflection disabled and pass "
                f"it via {label} - this command will not disable it on your existing configuration."
            )
    return None


def _artifact_path(transcripts_dir: Path, run_id: str, variant: Optional[str], config_id: str, scenario_id: str) -> Path:
    """A unique path per (run, config/variant, scenario) — never shared, so a
    base+variant comparison can't have one config's evidence silently
    overwrite the other's (every run used to write the SAME
    ``{scenario_id}.live.json`` regardless of which config produced it)."""
    return transcripts_dir / run_id / (variant or "base") / config_id / f"{scenario_id}.json"


def _attempt_turn(
    base_url: str, token: Optional[str], session_id: str, payload: Dict[str, Any], turn_index: int,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Attempt one turn against the real streaming endpoint. NEVER raises —
    every failure mode becomes a structured ``failure`` record IN the
    returned evidence, so a caller can always persist what actually happened
    (a first-turn success followed by a later failure included) instead of
    an unhandled exception discarding the whole scenario's evidence.

    Returns ``(turn_evidence, assistant_message_or_None)``. ``turn_evidence``
    always carries ``request``; ``raw_sse_text``/``events`` are filled in as
    far as the attempt got before failing (so a request that DID reach the
    backend, or bytes that WERE received, are never silently lost).
    ``failure`` is ``None`` on success, else
    ``{"stage": "http"|"transport"|"decode"|"framing", "error": str, "turn_index": int}``:

    - ``"http"``: the HTTP request itself failed (non-2xx status, DNS/connect
      failure) — see ``_HttpError``.
    - ``"transport"``: a lower-level read failure escaped that wrapping
      (connection reset, incomplete read, timeout mid-stream); any partial
      bytes the exception carries (e.g. ``http.client.IncompleteRead.partial``)
      are preserved as ``raw_sse_text`` rather than lost.
    - ``"decode"``: the full response text was received but a ``data:`` line
      failed to parse as JSON (malformed/truncated SSE payload).
    - ``"framing"``: the SSE lines parsed fine but the event sequence itself
      is incomplete or signals failure — an explicit ``error`` event, or no
      ``done``/``error`` event at all.
    """
    evidence: Dict[str, Any] = {"request": payload, "raw_sse_text": None, "events": None, "error": None, "failure": None}
    url = f"{base_url}/api/chat/sessions/{session_id}/messages/stream"

    try:
        raw_text = _http_text("POST", url, token, payload)
    except _HttpError as e:
        evidence["failure"] = {"stage": "http", "error": str(e), "turn_index": turn_index}
        return evidence, None
    except Exception as e:  # noqa: BLE001 - a transport failure that escaped _HttpError's own wrapping
        partial = getattr(e, "partial", None)
        if isinstance(partial, (bytes, bytearray)):
            evidence["raw_sse_text"] = partial.decode(errors="replace")
        evidence["failure"] = {"stage": "transport", "error": str(e), "turn_index": turn_index}
        return evidence, None

    evidence["raw_sse_text"] = raw_text
    try:
        events = parse_sse_events(raw_text)
    except Exception as e:  # noqa: BLE001 - malformed/truncated SSE data
        evidence["failure"] = {"stage": "decode", "error": str(e), "turn_index": turn_index}
        return evidence, None
    evidence["events"] = events

    error_event = next((e for e in events if e["event"] == "error"), None)
    if error_event is not None:
        evidence["error"] = json.dumps(error_event.get("data"))
        evidence["failure"] = {"stage": "framing", "error": evidence["error"], "turn_index": turn_index}
        return evidence, None

    done = next((e for e in events if e["event"] == "done"), None)
    if done is None:
        evidence["failure"] = {"stage": "framing", "error": "no 'done' event received", "turn_index": turn_index}
        return evidence, None

    return evidence, done.get("data", {}).get("assistant_message") or {}


def _run_scenario_live(
    base_url: str, token: Optional[str], config: Dict[str, Any], scenario_id: str,
    scenario: Dict[str, Any], variant: Optional[str], tool_schemas: Dict[str, Dict],
    transcripts_dir: Path, run_id: str,
) -> Dict[str, Any]:
    if not scenario.get("live_supported"):
        result = _empty_result(
            scenario_id, scenario["version"], variant, "live_supported",
            scenario.get("live_unsupported_reason", "this scenario is not marked live_supported"),
        )
        result["provider"] = {
            "config_id": config.get("id"), "type": config.get("type"), "model": config.get("model"),
            "is_default": config.get("is_default"), "unverified": False,
        }
        return result

    enabled_tools = None
    if scenario["tool_names"] is not None:
        enabled_tools = [t for t in scenario["tool_names"] if t not in _DRY_RUN_EXCLUDED_TOOLS]

    session = _http_json("POST", f"{base_url}/api/chat/sessions", token, {
        "llm_config_id": config["id"], "mode": scenario["mode"], "enabled_tools": enabled_tools,
        "name": f"chat-eval:{scenario_id}",
    })
    _check_success(session, "POST /api/chat/sessions")
    session_id = session["data"]["id"]

    context_metadata = scenario.get("live_context_metadata")
    all_messages: List[Dict[str, Any]] = []
    all_executions: List[Dict[str, Any]] = []
    turns_evidence: List[Dict[str, Any]] = []
    errors: List[str] = []
    last_assistant: Optional[Dict[str, Any]] = None

    for turn_index, turn_text in enumerate(scenario["user_turns"]):
        payload: Dict[str, Any] = {"content": turn_text}
        if context_metadata is not None:
            payload["context_metadata"] = context_metadata

        # Evidence is appended UNCONDITIONALLY, before failure is even known,
        # so a turn that fails (at any stage - transport, decode, framing)
        # still leaves its attempted request (and whatever partial evidence
        # was received) in the artifact rather than being lost to an
        # exception that unwound past the point turns_evidence would have
        # been appended.
        turn_evidence, assistant_message = _attempt_turn(base_url, token, session_id, payload, turn_index)
        turns_evidence.append(turn_evidence)
        all_messages.append({"role": "user", "content": turn_text})

        if assistant_message is None:
            errors.append(f"turn {turn_index} {turn_text!r}: {turn_evidence['failure']}")
            break  # the conversation is now incomplete - later turns build on a reply that never arrived

        all_messages.extend(_messages_from_assistant_message(assistant_message))
        last_assistant = assistant_message
        all_executions.extend(assistant_message.get("tool_executions") or [])

    http_completed = not errors

    behavior_trace = ((last_assistant or {}).get("metadata") or {}).get("behavior_trace") or {}
    ledger = behavior_trace.get("context_ledger") or {}
    budget = ledger.get("budget") or {}
    prompt_tokens = (last_assistant or {}).get("prompt_tokens")
    completion_tokens = (last_assistant or {}).get("completion_tokens")
    behavior_trace_steps = behavior_trace.get("steps")

    transcript_record = {
        "version": 2, "scenario": scenario_id, "messages": all_messages,
        # Provenance: a live capture never gets a locally-recomputed budget
        # pressure verdict (see evaluator.py's budget_pressure_observed) -
        # only its own real backend ledger counts as evidence.
        "capture": "live",
        # The live wire protocol doesn't expose real intra-turn round
        # boundaries (see evaluate_transcript's docstring) - stored IN the
        # transcript so re-scoring a saved artifact later reproduces this
        # without the caller having to remember to pass a flag.
        "round_boundaries_known": False,
        # The real backend's own accounting for the last completed turn, when
        # available - evaluate_transcript's budget_pressure_observed check
        # prefers this over recomputing locally. {} (falsy) when no ledger
        # was ever produced (e.g. the conversation never completed a turn).
        "budget_ledger": budget,
    }

    # ALWAYS write the artifact - even a scenario that failed partway through
    # keeps whatever turns did complete, plus every attempted turn's evidence
    # (including the failing one), linked from its own report row.
    artifact_path = _artifact_path(transcripts_dir, run_id, variant, config["id"], scenario_id)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "version": 1, "run_id": run_id, "scenario": scenario_id,
        "config_id": config.get("id"), "variant": variant,
        # Records what the memory policy actually verified for THIS config at
        # preflight time (or forced, for a generated clone) - see
        # _preflight_memory_policy/_MEMORY_POLICY_NOTE and
        # docs/chat-evaluation.md's "Evaluation memory policy" section. This
        # does not cover a background reflection pass already in flight from
        # a PRIOR turn in a session this run reuses (it never does) or any
        # server-side override of the config after preflight ran.
        "memory_policy": {
            "memory_reflection_required_off": True,
            "config_memory_reflection": config.get("memory_reflection"),
            "note": _MEMORY_POLICY_NOTE,
        },
        "turns": turns_evidence,
        "transcript": transcript_record,
    }
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n")

    if http_completed:
        evaluation = evaluate_transcript(scenario, transcript_record, tool_schemas)
        task_passed = evaluation.passed
        checks = [asdict(r) for r in evaluation.results]
    else:
        # An incomplete conversation is never scored as if it were a complete
        # one: report exactly what failed, plus an explicit unverified
        # placeholder for every check this scenario declared - its evidence
        # was never reached, which is a different thing from "failed".
        task_passed = False
        checks = [{"check": "http_completed", "passed": False, "detail": "; ".join(errors), "unverified": False}]
        checks.extend(
            {
                "check": c.get("type", "unknown"), "passed": False, "unverified": True,
                "detail": "conversation ended before this check's evidence was available - see the "
                          "artifact's per-turn 'failure' record",
            }
            for c in scenario.get("checks", [])
        )

    return {
        "scenario": scenario_id,
        "scenario_version": scenario["version"],
        "transcript": str(artifact_path),
        "variant": variant,
        "http_completed": http_completed,
        "passed": task_passed,
        "checks": checks,
        "provider": {
            "config_id": config.get("id"), "type": config.get("type"), "model": config.get("model"),
            "is_default": config.get("is_default"), "unverified": False,
        },
        "thinking_mode": behavior_trace.get("thinking_mode"),
        "context": {
            "capacity_tokens": budget.get("capacity_tokens"),
            "capacity_source": budget.get("capacity_source"),
            "accounting_tier": budget.get("accounting"),
            "measured": budget.get("measured"),
            "system_prompt_tokens": ledger.get("system_prompt", {}).get("est_tokens"),
            "tool_schema_tokens": ledger.get("tool_schemas", {}).get("est_tokens"),
            "memory_tokens": ledger.get("memory", {}).get("est_tokens"),
            "history_tokens": ledger.get("history", {}).get("est_tokens"),
            "unverified": False,
        } if ledger else _unverified_block(),
        "tokens": {
            "input": {"value": prompt_tokens, "unverified": prompt_tokens is None},
            "output": {"value": completion_tokens, "unverified": completion_tokens is None},
        },
        "turns_with_tools": _turns_with_tools_count(all_messages),
        "tool_executions": len(all_executions),
        # Never derived from the folded per-turn tool_calls grouping - see
        # evaluate_transcript's round_boundaries_known and this script's
        # module docstring.
        "actual_rounds": _unverified_actual_rounds(),
        "tool_durations_ms": [
            {"tool": te.get("tool_name"), "duration_ms": te.get("duration_ms"), "measured": True}
            for te in all_executions if te.get("duration_ms") is not None
        ],
        # Real, backend-measured phase timings from the LAST turn's persisted
        # behavior_trace (present when the provider/mode records one).
        "behavior_trace_steps": (
            {"value": behavior_trace_steps, "measured": True} if behavior_trace_steps
            else _unverified_behavior_trace_steps()
        ),
        "errors": errors,
        "latency_ms": {"value": None, "unverified": True},
        "memory_mb": {"value": None, "unverified": True},
    }


def cmd_run(args: argparse.Namespace) -> int:
    if not args.config:
        print(
            "Refusing to run without an explicit --config <llm config id or name>.\n"
            "This command drives the REAL chat API against an EXISTING, explicitly selected\n"
            "LLM configuration - there is no default provider to fall back to.\n\n"
            "What this would do once --config is given:\n"
            f"  1. Resolve the LLM configuration named/id'd by --config against {args.base_url}\n"
            "  2. For each selected LIVE-SUPPORTED scenario (others are reported as unsupported,\n"
            "     never passed), create a chat session with the scenario's own live_context_metadata\n"
            "     and tools minus write_memory, stream its user turns, and never call the\n"
            "     tool-approval endpoint - so every approval-gated mutating tool stays a dry preview.\n"
            "  3. If --variant is given (and --variant-config is not), create a temporary, isolated\n"
            "     clone of --config with an explicit provider_options.context_window test budget,\n"
            "     run the same scenarios against it, then delete the clone - the caller's own config\n"
            "     is never mutated. --variant-config <id> uses an existing separate config instead.\n"
            "  4. Save each scenario's raw request/SSE evidence and reconstructed transcript to its\n"
            "     own artifact file (never shared across configs/variants/scenarios) and write a\n"
            "     report JSON referencing it (see this script's module docstring for the schema).",
            file=sys.stderr,
        )
        return 2

    scenarios = fixtures.load_all_scenarios()
    if args.scenario:
        missing = [s for s in args.scenario if s not in scenarios]
        if missing:
            print(f"Unknown scenario id(s): {missing}. Known: {sorted(scenarios)}", file=sys.stderr)
            return 2
        selected = {s: scenarios[s] for s in args.scenario}
    else:
        selected = scenarios

    tool_schemas = tool_snapshot.schemas_by_name()
    transcripts_dir = Path(args.transcripts_dir)
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"

    try:
        config = _resolve_config(args.base_url, args.token, args.config)
    except _HttpError as e:
        print(f"Could not resolve --config {args.config!r}: {e}", file=sys.stderr)
        return 1

    variant_config: Optional[Dict[str, Any]] = None
    if args.variant_config:
        try:
            variant_config = _resolve_config(args.base_url, args.token, args.variant_config)
        except _HttpError as e:
            print(f"Could not resolve --variant-config {args.variant_config!r}: {e}", file=sys.stderr)
            return 1

    # Evaluation memory policy, checked BEFORE any session or message is
    # created for ANY selected config (a generated clone is exempt - see
    # clone_config_request_body, which always forces it off by construction).
    configs_to_check = [("--config", config)]
    if variant_config is not None:
        configs_to_check.append(("--variant-config", variant_config))
    policy_error = _preflight_memory_policy(configs_to_check)
    if policy_error:
        print(policy_error, file=sys.stderr)
        return 1

    configs_to_run: List[Tuple[Optional[str], Dict[str, Any]]] = [(None, config)]
    created_variant_id: Optional[str] = None
    try:
        if variant_config is not None:
            configs_to_run.append((args.variant or "variant-config", variant_config))
        elif args.variant:
            window = _VARIANT_CONTEXT_WINDOWS[args.variant]
            body = clone_config_request_body(config, window)
            try:
                create_result = _http_json("POST", f"{args.base_url}/api/llm/configurations", args.token, body)
                _check_success(create_result, "POST /api/llm/configurations (variant clone)")
                created_variant_id = create_result["data"]["id"]
                variant_config = _resolve_config(args.base_url, args.token, created_variant_id)
                print(
                    f"Created temporary comparison config '{created_variant_id}' ({body['name']!r}) - "
                    "never mutating the caller's own --config.", file=sys.stderr,
                )
                configs_to_run.append((args.variant, variant_config))
            except _HttpError as e:
                print(f"Could not create the '{args.variant}' comparison clone: {e}", file=sys.stderr)
                return 1

        results = []
        for variant_label, cfg in configs_to_run:
            for scenario_id in sorted(selected):
                try:
                    results.append(_run_scenario_live(
                        args.base_url, args.token, cfg, scenario_id, selected[scenario_id],
                        variant_label, tool_schemas, transcripts_dir, run_id,
                    ))
                except _HttpError as e:
                    results.append(_empty_result(scenario_id, selected[scenario_id]["version"], variant_label, "live_run", str(e)))
    finally:
        if created_variant_id:
            try:
                _http_json("DELETE", f"{args.base_url}/api/llm/configurations/{created_variant_id}", args.token)
                print(f"Deleted temporary comparison config '{created_variant_id}'.", file=sys.stderr)
            except _HttpError as e:
                print(f"WARNING: failed to delete temporary comparison config '{created_variant_id}': {e}", file=sys.stderr)

    report = build_report("run", results)
    _emit_report(report, args.out)
    summary = report["summary"]
    print(
        f"\n{summary['passed']}/{summary['total']} scenario run(s) passed "
        f"({summary['failed']} failed, {summary['unsupported']} unsupported)"
    )
    return 0 if summary["failed"] == 0 else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command")

    replay = subparsers.add_parser("replay", help="Offline: validate fixtures and score canned transcripts (default)")
    replay.add_argument("--scenario", action="append", help="Restrict to this scenario id (repeatable)")
    replay.add_argument("--out", help="Write the report JSON to this path instead of stdout")
    replay.set_defaults(func=cmd_replay)

    run = subparsers.add_parser("run", help="Bounded live comparison against an explicitly selected LLM configuration")
    run.add_argument("--config", help="LLM configuration id or name to drive (required)")
    run.add_argument("--scenario", action="append", help="Restrict to this scenario id (repeatable)")
    run.add_argument("--variant", choices=sorted(_VARIANT_CONTEXT_WINDOWS), help="Run again under a second, comparison configuration")
    run.add_argument("--variant-config", help="Use this EXISTING config for --variant instead of an auto-cloned one")
    run.add_argument("--base-url", default="http://localhost:7680", help="Base URL of an already-running PotionUI backend")
    run.add_argument("--token", help="Bearer token for an existing logged-in session (or set POTIONUI_CHAT_EVAL_TOKEN)")
    run.add_argument("--transcripts-dir", default=DEFAULT_LIVE_TRANSCRIPTS_DIR, help="Directory to save captured live transcripts into")
    run.add_argument("--out", help="Write the report JSON to this path instead of stdout")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["replay", *(argv or [])])
    if args.command == "run" and not args.token:
        import os
        args.token = os.environ.get("POTIONUI_CHAT_EVAL_TOKEN")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
