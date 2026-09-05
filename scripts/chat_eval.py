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
``enabled_tools`` for that reason (``_DRY_RUN_EXCLUDED_TOOLS``).

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
          "transcript": "<fixture path>" | "<saved live transcript path>" | null,
          "variant": "compact" | "large" | "variant-config" | null,
          "http_completed": true | false | null,   # null: never attempted (unsupported live)
          "passed": true | false,                  # task correctness (evaluate_transcript), not just HTTP success
          "checks": [{"check": "...", "passed": true, "detail": "..."}],
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
          "tool_rounds": int,       # turns that used at least one tool (coarse — see note below)
          "tool_executions": int,   # total individual tool calls, across all turns
          "errors": ["..."],
          "latency_ms": {"value": null, "unverified": true},
          "memory_mb": {"value": null, "unverified": true}
        }
      ],
      "summary": {"total": int, "passed": int, "failed": int}
    }

``tool_rounds`` counts TURNS that used at least one tool, not the exact
intra-turn tool-loop iteration count — deriving the latter would need parsing
the interleaved ``status``/``tool_start`` event sequence, which this report
does not attempt. ``tool_executions`` is the precise total of individual tool
calls and is reported separately so the two are never conflated.

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
import urllib.error
import urllib.request
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


def _empty_result(scenario_id: str, scenario_version: Any, variant: Optional[str], check: str, detail: str) -> Dict[str, Any]:
    return {
        "scenario": scenario_id, "scenario_version": scenario_version, "transcript": None, "variant": variant,
        "http_completed": None, "passed": False,
        "checks": [{"check": check, "passed": False, "detail": detail}],
        "provider": _unverified_block(), "thinking_mode": None, "context": _unverified_block(),
        "tokens": _unverified_tokens_block(), "tool_rounds": 0, "tool_executions": 0,
        "errors": [detail], "latency_ms": {"value": None, "unverified": True}, "memory_mb": {"value": None, "unverified": True},
    }


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------

def _replay_result(scenario_id: str, scenario: Dict[str, Any], tool_schemas: Dict[str, Dict]) -> Dict[str, Any]:
    transcript_path = fixtures.TRANSCRIPTS_DIR / f"{scenario_id}.good.json"
    transcript = fixtures.load_transcript(transcript_path)
    evaluation = evaluate_transcript(scenario, transcript, tool_schemas)
    tool_rounds = sum(
        1 for m in transcript["messages"]
        if m.get("role") == "assistant" and m.get("tool_calls")
    )
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
        "tool_rounds": tool_rounds,
        "tool_executions": tool_executions,
        "errors": errors,
        "latency_ms": {"value": None, "unverified": True},
        "memory_mb": {"value": None, "unverified": True},
    }


def build_report(mode: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    return {
        "version": REPORT_VERSION,
        "generated_at": _now_iso(),
        "mode": mode,
        "app_commit": _git_commit(),
        "results": results,
        "summary": {"total": total, "passed": passed, "failed": total - passed},
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
        status = "PASS" if r["passed"] else "FAIL"
        print(f"[{status}] {r['scenario']}")
        if not r["passed"]:
            for check in r["checks"]:
                if not check["passed"]:
                    print(f"    - {check['check']}: {check['detail']}")

    print(f"\n{report['summary']['passed']}/{report['summary']['total']} scenarios passed")
    return 0 if report["summary"]["failed"] == 0 else 1


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


def turn_transcript_messages(
    user_text: str, events: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """One turn's SSE events -> transcript messages (evaluator.py's shape) +
    the persisted ``assistant_message`` from ``done`` (``None`` if it never arrived).

    Built from the authoritative ``done`` event's ``assistant_message.tool_executions``
    (the same persisted record ``chatStream.ts``'s ``applyDone`` reads) rather
    than reconstructing state from the live ``tool_start``/``tool_end`` deltas —
    those exist for incremental UI rendering, ``done`` is the source of truth.
    Pure and network-free so it is directly unit-testable.
    """
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_text}]
    done = next((e for e in events if e["event"] == "done"), None)
    if done is None:
        return messages, None

    assistant_message = done.get("data", {}).get("assistant_message") or {}
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
            })

    messages.append({"role": "assistant", "content": assistant_message.get("content") or ""})
    return messages, assistant_message


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
        "memory_reflection": config.get("memory_reflection", True),
        "provider_options": provider_options,
    }


def _run_scenario_live(
    base_url: str, token: Optional[str], config: Dict[str, Any], scenario_id: str,
    scenario: Dict[str, Any], variant: Optional[str], tool_schemas: Dict[str, Dict],
    transcripts_dir: Path,
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
    tool_rounds = 0
    tool_executions_count = 0
    errors: List[str] = []
    last_assistant: Optional[Dict[str, Any]] = None

    for turn_text in scenario["user_turns"]:
        payload: Dict[str, Any] = {"content": turn_text}
        if context_metadata is not None:
            payload["context_metadata"] = context_metadata
        text = _http_text("POST", f"{base_url}/api/chat/sessions/{session_id}/messages/stream", token, payload)
        events = parse_sse_events(text)
        error_event = next((e for e in events if e["event"] == "error"), None)
        if error_event is not None:
            errors.append(f"turn {turn_text!r}: {json.dumps(error_event.get('data'))}")
            continue

        turn_messages, assistant_message = turn_transcript_messages(turn_text, events)
        all_messages.extend(turn_messages)
        if assistant_message is None:
            errors.append(f"turn {turn_text!r}: no 'done' event received")
            continue
        last_assistant = assistant_message
        executions = assistant_message.get("tool_executions") or []
        tool_executions_count += len(executions)
        if executions:
            tool_rounds += 1

    http_completed = not errors
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    transcript_record = {"version": 1, "scenario": scenario_id, "messages": all_messages}
    transcript_path = transcripts_dir / f"{scenario_id}.live.json"
    transcript_path.write_text(json.dumps(transcript_record, indent=2) + "\n")

    if http_completed:
        evaluation = evaluate_transcript(scenario, transcript_record, tool_schemas)
        task_passed = evaluation.passed
        checks = [asdict(r) for r in evaluation.results]
    else:
        task_passed = False
        checks = [{"check": "http_completed", "passed": False, "detail": "; ".join(errors)}]

    behavior_trace = ((last_assistant or {}).get("metadata") or {}).get("behavior_trace") or {}
    ledger = behavior_trace.get("context_ledger") or {}
    budget = ledger.get("budget") or {}
    prompt_tokens = (last_assistant or {}).get("prompt_tokens")
    completion_tokens = (last_assistant or {}).get("completion_tokens")

    return {
        "scenario": scenario_id,
        "scenario_version": scenario["version"],
        "transcript": str(transcript_path),
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
        "tool_rounds": tool_rounds,
        "tool_executions": tool_executions_count,
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
            "  4. Save each live transcript to a file and write a report JSON referencing it\n"
            "     (see this script's module docstring for the schema).",
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

    try:
        config = _resolve_config(args.base_url, args.token, args.config)
    except _HttpError as e:
        print(f"Could not resolve --config {args.config!r}: {e}", file=sys.stderr)
        return 1

    configs_to_run: List[Tuple[Optional[str], Dict[str, Any]]] = [(None, config)]
    created_variant_id: Optional[str] = None
    try:
        if args.variant_config:
            try:
                variant_config = _resolve_config(args.base_url, args.token, args.variant_config)
            except _HttpError as e:
                print(f"Could not resolve --variant-config {args.variant_config!r}: {e}", file=sys.stderr)
                return 1
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
                        variant_label, tool_schemas, transcripts_dir,
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
    print(f"\n{report['summary']['passed']}/{report['summary']['total']} scenario run(s) passed")
    return 0 if report["summary"]["failed"] == 0 else 1


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
