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
regression (a fixture that should pass no longer does, or a scenario/tool
mismatch). No network, no provider, no side effects.

``run --config <llm config id or name>`` — drives the REAL chat HTTP API
(session create + send-message) against an ALREADY RUNNING PotionUI backend
and an EXISTING, explicitly named LLM configuration. Refuses to run without
an explicit ``--config`` (prints what it would do and exits non-zero). Every
mutating tool a scenario can reach (``update_form_settings``,
``start_generation``, ``propose_form_changes``, ``run_generation``, ...) is
gated behind chat's own tool-approval step — this command never calls the
approve-tool endpoint, so every one of those tools stops at its
``pending_approval`` preview and nothing is ever applied. The one builtin
tool that is NOT approval-gated and DOES mutate state is ``write_memory``; it
is excluded from every session's ``enabled_tools`` here for that reason (see
``_DRY_RUN_EXCLUDED_TOOLS``). ``run`` never calls this repository's own
inference code and never loads a model itself — the selected provider must
already be configured and running by whoever operates that backend.

Report JSON schema (both entry points write the same shape; see
``build_report``)
------------------------------------------------------------------------
::

    {
      "version": 1,
      "generated_at": "<ISO 8601 UTC>",
      "mode": "replay" | "run",
      "app_commit": "<git rev-parse HEAD, short> | null",
      "results": [
        {
          "scenario": "<scenario id>",
          "scenario_version": 1,
          "transcript": "<fixture path>" | "live",
          "variant": "compact" | "large" | null,
          "passed": true,
          "checks": [{"check": "...", "passed": true, "detail": "..."}],
          "provider": {"unverified": true} |
                      {"config_id": "...", "type": "...", "model": "...",
                       "revision": "...|null", "unverified": false},
          "thinking_mode": null | {"requested": ..., "effective": "..."},
          "context": {"unverified": true} |
                      {"capacity_tokens": int, "capacity_source": "...",
                       "accounting_tier": "...", "system_prompt_tokens": int,
                       "tool_schema_tokens": int, "memory_tokens": int,
                       "history_tokens": int, "unverified": false},
          "tokens": {"unverified": true} |
                    {"input": int|null, "output": int|null, "unverified": false},
          "tool_rounds": int,
          "errors": ["..."],
          "latency_ms": {"value": null, "unverified": true},
          "memory_mb": {"value": null, "unverified": true}
        }
      ],
      "summary": {"total": int, "passed": int, "failed": int}
    }

``"unverified": true`` marks a metric this run could not actually measure
(every field in ``replay`` mode, since no LLM call happens; latency/memory
in ``run`` mode, since the HTTP round trip doesn't expose the backend's own
timing/RSS) — never a silently-omitted or guessed number.
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
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.evaluation.chat import fixtures, tool_snapshot  # noqa: E402
from tests.evaluation.chat.evaluator import evaluate_transcript  # noqa: E402

REPORT_VERSION = 1

# The one builtin tool that mutates durable state without an approval gate
# (see docs/chat-memory.md's write_memory tool) - excluded from every `run`
# session so a live comparison can never write anything, without needing to
# monkeypatch the real backend's tool execution.
_DRY_RUN_EXCLUDED_TOOLS = {"write_memory"}

_VARIANT_CONTEXT_WINDOWS = {
    "compact": 4096,
    "large": 131072,
}


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
        "passed": evaluation.passed,
        "checks": [asdict(r) for r in evaluation.results],
        "provider": _unverified_block(),
        "thinking_mode": None,
        "context": _unverified_block(),
        "tokens": _unverified_block(),
        "tool_rounds": tool_rounds,
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
            results.append({
                "scenario": scenario_id, "scenario_version": selected[scenario_id].get("version"),
                "transcript": None, "variant": None, "passed": False,
                "checks": [{"check": "fixture_load", "passed": False, "detail": str(e)}],
                "provider": _unverified_block(), "thinking_mode": None, "context": _unverified_block(),
                "tokens": _unverified_block(), "tool_rounds": 0, "errors": [str(e)],
                "latency_ms": {"value": None, "unverified": True}, "memory_mb": {"value": None, "unverified": True},
            })

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


def _http_json(method: str, url: str, token: Optional[str], payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        raise _HttpError(f"{method} {url} -> HTTP {e.code}: {e.read().decode(errors='replace')}") from e
    except urllib.error.URLError as e:
        raise _HttpError(f"{method} {url} -> {e}") from e


def _resolve_config(base_url: str, token: Optional[str], config_ref: str) -> Dict[str, Any]:
    """Resolve --config by id first, then by exact name, against /api/llm/configurations."""
    try:
        result = _http_json("GET", f"{base_url}/api/llm/configurations/{config_ref}", token)
        config = result.get("data", result)
        if config:
            return config
    except _HttpError:
        pass
    result = _http_json("GET", f"{base_url}/api/llm/configurations", token)
    configs = result.get("data", result) or []
    matches = [c for c in configs if c.get("name") == config_ref]
    if not matches:
        raise _HttpError(f"No LLM configuration found with id or name '{config_ref}'")
    if len(matches) > 1:
        raise _HttpError(f"Multiple LLM configurations named '{config_ref}'; pass the config id instead")
    return matches[0]


def _run_scenario_live(
    base_url: str, token: Optional[str], config: Dict[str, Any], scenario_id: str,
    scenario: Dict[str, Any], variant: Optional[str],
) -> Dict[str, Any]:
    enabled_tools = None
    if scenario["tool_names"] is not None:
        enabled_tools = [t for t in scenario["tool_names"] if t not in _DRY_RUN_EXCLUDED_TOOLS]

    session = _http_json("POST", f"{base_url}/api/chat/sessions", token, {
        "llm_config_id": config["id"],
        "mode": scenario["mode"],
        "enabled_tools": enabled_tools,
        "name": f"chat-eval:{scenario_id}",
    })
    session_data = session.get("data", session)
    session_id = session_data["id"]

    last_assistant: Optional[Dict[str, Any]] = None
    tool_rounds = 0
    errors: List[str] = []
    for turn_text in scenario["user_turns"]:
        response = _http_json(
            "POST", f"{base_url}/api/chat/sessions/{session_id}/messages", token,
            {"content": turn_text},
        )
        payload = response.get("data", response)
        last_assistant = payload.get("assistant_message")
        if last_assistant is None:
            errors.append(f"no assistant_message returned for turn: {turn_text!r}")
            continue
        tool_rounds += len(last_assistant.get("tool_executions") or [])

    behavior_trace = (last_assistant or {}).get("metadata", {}).get("behavior_trace", {}) if last_assistant else {}
    ledger = behavior_trace.get("context_ledger", {})
    budget = ledger.get("budget", {})
    thinking_mode = behavior_trace.get("thinking_mode")

    return {
        "scenario": scenario_id,
        "scenario_version": scenario["version"],
        "transcript": "live",
        "variant": variant,
        "passed": not errors,
        "checks": [{"check": "live_turns_completed", "passed": not errors, "detail": "; ".join(errors) or "all turns completed"}],
        "provider": {
            "config_id": config.get("id"), "type": config.get("type"),
            "model": config.get("model"), "revision": config.get("revision"), "unverified": False,
        },
        "thinking_mode": thinking_mode,
        "context": {
            "capacity_tokens": budget.get("capacity_tokens"),
            "capacity_source": budget.get("capacity_source"),
            "accounting_tier": budget.get("accounting"),
            "system_prompt_tokens": ledger.get("system_prompt", {}).get("est_tokens"),
            "tool_schema_tokens": ledger.get("tool_schemas", {}).get("est_tokens"),
            "memory_tokens": ledger.get("memory", {}).get("est_tokens"),
            "history_tokens": ledger.get("history", {}).get("est_tokens"),
            "unverified": not budget,
        } if ledger else _unverified_block(),
        "tokens": {
            "input": (last_assistant or {}).get("prompt_tokens"),
            "output": (last_assistant or {}).get("completion_tokens"),
            "unverified": last_assistant is None,
        },
        "tool_rounds": tool_rounds,
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
            "  2. For each selected scenario, create a chat session in that scenario's mode\n"
            "     with the scenario's tools minus write_memory (the only non-approval-gated\n"
            "     mutating builtin tool), send its user turns, and never call the tool-approval\n"
            "     endpoint - so every approval-gated mutating tool call stays a dry preview.\n"
            "  3. If --variant is given, temporarily PATCH that config's "
            "provider_options.context_window\n     for the duration of the run and restore it afterward.\n"
            "  4. Write a report JSON (see this script's module docstring for the schema).",
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

    try:
        config = _resolve_config(args.base_url, args.token, args.config)
    except _HttpError as e:
        print(f"Could not resolve --config {args.config!r}: {e}", file=sys.stderr)
        return 1

    original_provider_options = dict(config.get("provider_options") or {})
    patched = False
    try:
        if args.variant:
            context_window = _VARIANT_CONTEXT_WINDOWS[args.variant]
            new_options = dict(original_provider_options)
            new_options["context_window"] = context_window
            _http_json("PUT", f"{args.base_url}/api/llm/configurations/{config['id']}", args.token, {
                "provider_options": new_options,
            })
            patched = True
            print(
                f"Temporarily set provider_options.context_window={context_window} on "
                f"config '{config['id']}' for the '{args.variant}' variant.", file=sys.stderr,
            )

        results = []
        for scenario_id in sorted(selected):
            try:
                results.append(_run_scenario_live(args.base_url, args.token, config, scenario_id, selected[scenario_id], args.variant))
            except _HttpError as e:
                results.append({
                    "scenario": scenario_id, "scenario_version": selected[scenario_id]["version"],
                    "transcript": "live", "variant": args.variant, "passed": False,
                    "checks": [{"check": "live_run", "passed": False, "detail": str(e)}],
                    "provider": _unverified_block(), "thinking_mode": None, "context": _unverified_block(),
                    "tokens": _unverified_block(), "tool_rounds": 0, "errors": [str(e)],
                    "latency_ms": {"value": None, "unverified": True}, "memory_mb": {"value": None, "unverified": True},
                })
    finally:
        if patched:
            try:
                _http_json("PUT", f"{args.base_url}/api/llm/configurations/{config['id']}", args.token, {
                    "provider_options": original_provider_options,
                })
                print(f"Restored config '{config['id']}' provider_options.", file=sys.stderr)
            except _HttpError as e:
                print(f"WARNING: failed to restore provider_options on '{config['id']}': {e}", file=sys.stderr)

    report = build_report("run", results)
    _emit_report(report, args.out)
    print(f"\n{report['summary']['passed']}/{report['summary']['total']} scenarios completed without error")
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
    run.add_argument("--variant", choices=sorted(_VARIANT_CONTEXT_WINDOWS), help="Apply a context-window override for comparison")
    run.add_argument("--base-url", default="http://localhost:7680", help="Base URL of an already-running PotionUI backend")
    run.add_argument("--token", help="Bearer token for an existing logged-in session (or set POTIONUI_CHAT_EVAL_TOKEN)")
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
