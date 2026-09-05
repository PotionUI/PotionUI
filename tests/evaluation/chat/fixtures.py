"""Loading and shape-checking for scenario and transcript fixtures.

A **scenario** (``scenarios/<id>.json``) is a versioned, human-authored
description of one chat workflow to evaluate: the chat mode, the tools it may
use, the app context the turn starts from, the user's turns, example canned
tool responses (documentation for anyone hand-writing a transcript or driving
a live ``run``), and the declarative ``checks`` a transcript of this scenario
must satisfy (see ``evaluator.py``).

A **transcript** (``transcripts/<name>.json``) is the recorded message
sequence of one actual (or hand-authored, for a fixture) run of a scenario:
``{"version": 1, "scenario": "<scenario id>", "messages": [...]}`` where each
message is one of:

- ``{"role": "user", "content": str}``
- ``{"role": "assistant", "content": str, "tool_calls": [{"name": str, "arguments": dict}]}``
  — ``content`` is empty when the message is pure tool-calling, and
  ``tool_calls`` is omitted (or empty) on the final answer.
- ``{"role": "tool", "name": str, "content": str, "outcome": str}`` — content
  is what the model would see (serialized ``ToolResult.data``); ``outcome``
  is the evaluator-facing ground truth ("ok" | "applied" | "pending_approval"
  | "stale" | "rejected" | "error" | "enqueued" | ...) a recorder attaches
  alongside content so truthfulness checks don't have to re-parse prose.

Both file kinds carry a ``version`` field so a future incompatible fixture
change can be caught rather than silently misread.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

FIXTURES_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = FIXTURES_DIR / "scenarios"
TRANSCRIPTS_DIR = FIXTURES_DIR / "transcripts"

SUPPORTED_SCENARIO_VERSION = 2
SUPPORTED_TRANSCRIPT_VERSION = 1

# version 2 adds the live-run contract: `live_supported` is required on every
# scenario; `live_context_metadata` (the EXACT `SendMessageRequest.context_metadata`
# payload to send on each turn) and `live_unsupported_reason` are conditionally
# required by `_check_live_run_fields` below rather than always-present keys,
# since exactly one of them applies depending on `live_supported`.
_REQUIRED_SCENARIO_KEYS = {
    "version", "id", "title", "mode", "tool_names", "context", "user_turns", "checks", "live_supported",
}
_REQUIRED_TRANSCRIPT_KEYS = {"version", "scenario", "messages"}


class FixtureError(ValueError):
    pass


def load_scenario(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text())
    missing = _REQUIRED_SCENARIO_KEYS - data.keys()
    if missing:
        raise FixtureError(f"{path}: scenario missing required key(s): {sorted(missing)}")
    if data["version"] != SUPPORTED_SCENARIO_VERSION:
        raise FixtureError(f"{path}: unsupported scenario version {data['version']}")
    if data["id"] != path.stem:
        raise FixtureError(f"{path}: scenario id {data['id']!r} does not match filename")
    if data["live_supported"]:
        if "live_context_metadata" not in data:
            raise FixtureError(f"{path}: live_supported scenarios require 'live_context_metadata' (null is a valid value)")
    elif not data.get("live_unsupported_reason"):
        raise FixtureError(f"{path}: live_supported=false requires a non-empty 'live_unsupported_reason'")
    return data


def load_transcript(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text())
    missing = _REQUIRED_TRANSCRIPT_KEYS - data.keys()
    if missing:
        raise FixtureError(f"{path}: transcript missing required key(s): {sorted(missing)}")
    if data["version"] != SUPPORTED_TRANSCRIPT_VERSION:
        raise FixtureError(f"{path}: unsupported transcript version {data['version']}")
    return data


def iter_scenario_paths() -> List[Path]:
    return sorted(SCENARIOS_DIR.glob("*.json"))


def iter_transcript_paths() -> List[Path]:
    return sorted(TRANSCRIPTS_DIR.glob("*.json"))


def load_all_scenarios() -> Dict[str, Dict[str, Any]]:
    return {p.stem: load_scenario(p) for p in iter_scenario_paths()}


def scenario_for_transcript(transcript: Dict[str, Any]) -> Dict[str, Any]:
    scenarios = load_all_scenarios()
    scenario_id = transcript["scenario"]
    if scenario_id not in scenarios:
        raise FixtureError(f"transcript references unknown scenario '{scenario_id}'")
    return scenarios[scenario_id]
