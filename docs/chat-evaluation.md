# Chat and Tool Evaluation

Developer tooling for comparing the built-in AI chat's real workflows — reading
form state, proposing edits, researching prompts, mapping a ComfyUI import,
starting a generation — across explicitly selected model configurations. Not
a public feature: no UI, no automatic routing, and no single benchmark score.
It exists to answer "did switching configuration A for configuration B change
how chat actually behaves on these workflows", with the deterministic parts
checked by code and the subjective parts left to a human, never a model.

## Layout

```
tests/evaluation/chat/
  fixtures.py           # loading + shape-checking for scenarios and transcripts
  tool_snapshot.py       # the real ToolRegistry (builtin + ComfyUI import tools)
  evaluator.py            # the check vocabulary and evaluate_transcript()
  scenarios/<id>.json     # versioned scenario fixtures
  transcripts/<name>.json # canned transcripts scored against a scenario
  test_evaluator.py               # good transcripts pass, broken ones fail their check
  test_turn_replay_contract.py    # the interruption/reconnect scenario against the
                                   # REAL ChatTurnRegistry (public API only)
scripts/chat_eval.py      # the CLI: replay (default) and run
```

A **scenario** (`scenarios/<id>.json`, version 2) describes one chat workflow:
the chat mode, the tools it may use (`tool_names`, resolved against the real
registry — never hand-copied), the app context the turn starts from, the
user's turns, example canned tool responses, a `checks` list of declarative
predicates a transcript of this scenario must satisfy, and its **live-run
contract**: `live_supported` (bool) plus either `live_context_metadata` (the
exact `SendMessageRequest.context_metadata` payload `chat_eval.py run` sends
on every turn — `null` when no injected context is needed at all) or, when
`live_supported` is `false`, a `live_unsupported_reason` explaining why this
scenario's fixture state can't be faithfully reproduced against a real
backend (a fictional model id that won't resolve in the real model index, an
outcome — "applied"/"stale" — reachable only through the tool-approval
endpoint `run` never calls, an induced tool error, ...). A **transcript**
(`transcripts/<name>.json`, version 2) is the recorded message sequence of
one run, tagged with an explicit `capture` provenance — `"replay"` for the
canonical `<scenario id>.good.json` or a deliberately broken fixture used by
`test_evaluator.py`, `"live"` for a capture `chat_eval.py run` saves under
`--transcripts-dir`. `capture` is never inferred or defaulted — every check
that must behave differently for a real capture vs. an authored fixture
(`budget_pressure_observed` today) reads it directly rather than guessing.
Full shapes are documented in the docstrings of `fixtures.py`, `evaluator.py`,
and `scripts/chat_eval.py`.

Checks are predicates over the transcript, never an expected exact call
sequence — e.g. "a call to `search_model_prompts` with `model_id` matching the
active model occurred before the final answer," not "the model must call
these three tools in this order." See `evaluator.py`'s `_CHECKS` for the full
vocabulary (`tool_call_present`, `final_answer_contains_all`,
`final_answer_not_contains`, `max_tool_rounds`, `latest_question_reflected`,
`truthful_apply_status`, `dry_run_never_enqueues`, `error_then_recovery`,
`capability_declined`, `budget_pressure_observed`), plus one structural check
that always runs regardless of scenario: every tool call names a tool
available to THAT scenario (not just anything in the registry) with
schema-valid arguments — type, required, enum, numeric bounds
(`minimum`/`maximum`/`exclusiveMinimum`/`exclusiveMaximum`), array length
(`minItems`/`maxItems`), and nested object/array shapes, checked against the
tool's real JSON schema. `budget_pressure_observed` (`{capacity_tokens,
reserve_tokens}`) proves a scenario's history genuinely exceeded a stated
compact capacity and was actually trimmed, gated on the transcript's own
`capture` provenance (`"live"` or `"replay"`, see below): a `"live"` capture
is scored ONLY from its own real `transcript["budget_ledger"]` and reported
`unverified` when that ledger is absent — NEVER recomputed locally, since
that would silently substitute this evaluator's own token estimate for
whatever the real provider actually did; a `"replay"` fixture (which never
has a real ledger by construction) is recomputed over its own prior
conversation via the SAME real `context_budget.enforce_budget` used
elsewhere. Never a prose "long history" label taken on faith either way.
`error_then_recovery` requires a
POSITIVELY successful call after the last error — never a `pending_approval`/
`stale`/`rejected` outcome, which is evidence the action wasn't actually
carried out, not evidence of recovery. A property schema carrying a
constraint this validator doesn't implement (`pattern`, `format`,
`uniqueItems`, `const`, ...) is never silently treated as satisfied — the
whole `tool_calls_valid` check is marked `unverified` instead when that
happens and no violation was otherwise found (see `_HANDLED_CONSTRAINT_KEYWORDS`).
A `CheckResult` carrying `unverified: true` is reported but never gates
`ScenarioEvaluation.passed` — used for a constraint the validator can't
decide, and for `max_tool_rounds` when scoring a live capture whose wire
protocol doesn't expose real round boundaries (see `evaluate_transcript`'s
`round_boundaries_known`).

## Running it

### `replay` (default, fully offline)

```bash
PYTHONPATH=./venv/lib/python3.12/site-packages:. python scripts/chat_eval.py
# equivalently:
PYTHONPATH=./venv/lib/python3.12/site-packages:. python scripts/chat_eval.py replay
```

Loads every scenario fixture, confirms its declared tools resolve against the
real registry, and scores each scenario's canned `<id>.good` transcript. No
network call, no provider, no side effect. Exits non-zero if any scenario's
canned transcript stops passing its own checks (an evaluator regression) —
this is what a CI-adjacent sanity check would run. `--scenario <id>` (repeatable)
restricts to specific scenarios; `--out <path>` writes the report JSON to a
file instead of printing it to stdout.

The `long_history_latest_question` scenario's pressure is not a side fixture:
its padding Q&A pairs ARE the scenario's own `user_turns` (so a live `run`
actually sends every one of them as a real turn to the same session before
the final question) and the SAME sequence makes up the `.good` transcript's
message history (so `replay`'s `evaluate_transcript` scores the final answer
against that pressurized conversation, via the `budget_pressure_observed`
check above). `context.budget_pressure` states the capacity assumption
explicitly (`capacity_tokens`, `capacity_source`, `reserve_tokens`);
`test_long_history_budget_pressure.py` is the direct, standalone proof that
this history exceeds it through the real `context_budget.enforce_budget` and
that the current turn's own question survives the trim.

The pytest suite covers the same fixtures plus negative cases:

```bash
PYTHONPATH=./venv/lib/python3.12/site-packages:. python -m pytest tests/evaluation/chat -q --no-cov
```

### `run --config <id or name>` (a bounded live comparison)

```bash
PYTHONPATH=./venv/lib/python3.12/site-packages:. python scripts/chat_eval.py run \
  --config my-ollama-config --base-url http://localhost:7680 --token "$POTIONUI_CHAT_EVAL_TOKEN"
```

Drives the real chat HTTP API (`POST /api/chat/sessions`, then the SSE
streaming send endpoint `POST /api/chat/sessions/{id}/messages/stream`)
against an **already-running** PotionUI backend and an **existing,
explicitly named** LLM configuration. There is no default — omitting
`--config` prints exactly what the command would do and exits non-zero
rather than guessing a provider. `--token` (or the `POTIONUI_CHAT_EVAL_TOKEN`
env var) is a bearer token from an existing logged-in session; the command
does not manage login itself.

**Only `live_supported: true` scenarios are actually driven live.** For
every other scenario, the fixture's specific canned state (a fictional model
id, a wizard draft revision, an induced tool error, an "applied"/"stale"
outcome reachable only through the approval endpoint `run` never calls) can't
be faithfully reproduced against a real backend's real state — running it
anyway and hoping the checks happen to pass would be dishonest. Those
scenarios are reported instead: `http_completed: null`, `passed: false`, and
a `live_supported` check carrying the fixture's own `live_unsupported_reason`
— counted in the report's `summary.unsupported`, distinct from
`summary.failed` (a scenario that WAS attempted and did not pass).

For a supported scenario, EVERY turn's raw evidence is captured — the exact
request payload sent and the exact raw SSE text/parsed events received,
including any error — and saved alongside the reconstructed transcript (the
same shape `evaluator.py` scores, built from the turn's persisted `done`
event's `assistant_message.tool_executions` — the same record `chatStream.ts`'s
`applyDone` reads, not the incremental `tool_start`/`tool_end` deltas meant
for live UI rendering) into ONE artifact file per scenario run:
`<transcripts_dir>/<run_id>/<variant or 'base'>/<config_id>/<scenario_id>.json`,
where `run_id` is unique per `run` invocation. This means a base+variant
comparison of the SAME scenario always gets two SEPARATE artifact files —
earlier this command wrote every config/variant's evidence to the same
`{scenario_id}.live.json`, so a comparison run silently had the later
config's evidence overwrite the earlier one's. The report's `transcript`
field names a row's own artifact file, and re-loading that file's
`transcript` key and re-running `evaluate_transcript` on it reproduces that
row's exact score with no extra argument — the artifact's `transcript`
carries its own `round_boundaries_known: false` and `budget_ledger` (the real
backend's accounting for its last completed turn, when one exists) as data,
not a caller-only flag (see `tests/scripts/test_chat_eval_cli.py`'s
`TestBaseAndVariantProduceDistinctArtifacts`). `http_completed` (did every
turn finish without an HTTP/stream error) is reported separately from
`passed` (did the captured transcript pass its scenario's checks) — a turn
can complete successfully over HTTP and still fail a task check.

Per-turn evidence is written to the artifact even when a turn fails partway
through: each attempt is classified `{"stage": "http"|"transport"|"decode"|
"framing", "error": str, "turn_index": int}` (`http` — the request itself
failed; `transport` — a lower-level read failure, with any partial bytes the
exception carries preserved as `raw_sse_text` rather than lost; `decode` —
the response arrived but a `data:` line wasn't valid JSON; `framing` — the
SSE lines parsed but the event sequence itself signals failure or never
produced a `done`), and a scenario stops at the first failing turn rather
than pressing on with a conversation built on a reply that never arrived.
The artifact is written REGARDLESS — a scenario that fails on turn 2 of 5
still keeps turns 1's real evidence and turn 2's failure record, linked from
its own report row, never silently dropped. Such a row reports
`http_completed: false`, `passed: false`, and an explicit `unverified`
placeholder for every check the scenario declared (its evidence was never
reached — a different thing from "failed") rather than silently scoring a
truncated conversation as if it were complete (see
`tests/scripts/test_chat_eval_cli.py`'s `TestFailureSafeArtifacts`).

Every mutating TOOL a scenario can reach (`update_form_settings`,
`start_generation`, `propose_form_changes`, `run_generation`, ...) is already
approval-gated in the real tool loop — its `execute()` only returns a
`pending_approval` preview, and `run` never calls the tool-approval endpoint,
so those calls always stay a dry preview. The one builtin tool that mutates
state *without* an approval gate is `write_memory` (see `docs/chat-memory.md`)
— it is excluded from every session's `enabled_tools` for that reason. This
covers the TOOL loop, not every way a turn can write durable state — see
"Evaluation memory policy" immediately below for the other one. `run` never
calls this repository's own inference code directly, but an explicitly
selected `native` configuration's checkpoint IS loaded lazily by the backend
itself on first use, in-process, exactly as it would for any other real chat
turn — this command doesn't add inference, it just doesn't avoid the
inference the backend would run anyway for the configuration the caller named.

### Evaluation memory policy

Excluding `write_memory` from `enabled_tools` does not close every path a
live turn can persist state through: `ChatReflectionGenerator`
(`src/features/chat/reflection.py`) fires a BACKGROUND pass — a mechanism
entirely separate from the tool loop, unaffected by which tools are enabled —
after every 4th user message in a session whose configuration has
`memory_reflection` on, extracting and writing durable-memory facts straight
from the conversation. This pack's own `long_history_latest_question`
scenario alone sends 26 turns, well past that threshold, so a run against a
configuration with reflection on risks writing benchmark facts into the
user's real memory.

`run` therefore refuses to evaluate ANY configuration with `memory_reflection`
on — checked in a preflight step BEFORE any chat session or message is
created for ANY selected config (the base `--config`, an explicit
`--variant-config`, or a generated `--variant` clone), never partway through
a scenario. The refusal names the offending config and asks for a dedicated
evaluation configuration with reflection disabled; it never mutates the
caller's own configuration to force it off. A configuration generated by
`--variant`'s auto-clone always has `memory_reflection` forced to `false` at
creation, regardless of the source config's own setting — see
`clone_config_request_body`. Every artifact's header records what this
preflight verified for that config
(`{"memory_reflection_required_off": true, "config_memory_reflection": ...}`).

This policy does not cover a background reflection pass already in flight
from an EARLIER turn in a session this run happens to reuse (`run` always
creates a fresh session, so this does not occur in practice) or a change to
the configuration's own reflection setting made through some other path
after this preflight already ran.

`--variant compact|large` runs the same scenarios again against a SECOND
configuration, using an **explicit** `provider_options.context_window` test
budget (4096 / 131072 tokens — a label for the comparison, never a claim
about the model's real capability, and never a value inferred from its size).
This **never mutates the caller's own `--config`**: by default it creates a
temporary, isolated clone via `POST /api/llm/configurations` and deletes it
again once the run finishes. Cloning cannot carry over a stored API key
(`LLMConfigResponse` never returns one — see `src/features/llm/dto.py`), so a
provider that needs one won't authenticate under an auto-clone; pass
`--variant-config <id>` naming an existing, separately configured comparison
configuration instead (used as-is — no clone, no window override, no
deletion). Omitting both `--variant` and `--variant-config` runs `--config`'s
own defaults, unchanged, exactly once.

## The report

Both entry points write the same JSON shape (documented in full in
`scripts/chat_eval.py`'s module docstring, `version: 2`): one entry per
scenario carrying `http_completed` (HTTP/stream success; always `null` in
`replay`, since no HTTP call happens there) separately from `passed` (task
correctness — did the transcript pass `evaluate_transcript`'s checks) plus
the individual `checks` (each with its own `unverified` flag), and, when
known, the provider identity (`config_id`/`type`/`model`/`is_default` — no
fabricated "revision" field; LLM configurations don't have one), effective
`thinking_mode`, the context ledger's `capacity_tokens` / `capacity_source` /
`accounting_tier` (the ledger's `accounting` field: `"estimate"`,
`"chat_template"`, ... — see `src/features/llm/context_budget.py`) and its
`measured` qualifier (whether that tier came from an exact chat-template
recount or a fragment estimate), plus component sizes (system prompt, tool
schemas, memory, history), input/output token counts, `turns_with_tools`
(user turns that used at least one tool — a coarse measure) separately from
`tool_executions` (the precise total count of individual tool calls), and
`actual_rounds` (the exact count of real tool-loop decision rounds) —
`unverified: false` for `replay`'s canned transcripts (authored with one
assistant tool-calls message per real round) but ALWAYS `unverified: true`
for a live `run` capture, because the live SSE wire protocol emits one
`status: thinking` event per TURN, not per round, so real round boundaries
can't be recovered from it; `max_tool_rounds` itself is likewise reported
`unverified` rather than scored against the unreliable per-turn grouping when
checking a live capture. `tool_durations_ms` and `behavior_trace_steps`
retain the REAL, backend-measured per-tool and per-phase timings the
persisted `assistant_message` already carries (labeled `measured: true`,
unlike the always-unverified `latency_ms`/`memory_mb` below, which this
script has no way to measure itself). The summary distinguishes
`unsupported` (a scenario reported but never attempted, because its fixture
isn't `live_supported`) from `failed` (a scenario that WAS attempted and did
not pass) — `passed + failed + unsupported == total` always.

A metric this run could not actually measure is marked `"unverified": true`
(or, for a value that might legitimately be a known `null`, a
`{"value": ..., "unverified": bool}` pair — used for token counts and
latency/memory) rather than silently defaulted to `0`/`None` and presented as
a known value. Every field is unverified in `replay` mode; in `run` mode,
latency/memory always are (the HTTP round trip exposes neither the backend's
own timing nor its RSS), and any per-field value the provider itself didn't
report is marked unverified rather than guessed.

## Human review rubric

Task correctness (the checks above) is deliberately kept separate from
subjective quality — tone, prompt craft, whether an enhancement's result is
actually *good* — which no model judges here. When comparing two
configurations' `run` reports, use this rubric per scenario, filling in the
raw evidence link (the report's `transcript` path, or a saved copy of the
live conversation) rather than a bare score:

```markdown
### Scenario: <id>          Configs compared: <A> vs <B>

| Question                                              | A | B | Notes |
|--------------------------------------------------------|---|---|-------|
| Tone and clarity of the final answer                    |   |   |       |
| Prompt/creative quality (enhancement, search synthesis) |   |   |       |
| Did it ask for clarification when it should have?       |   |   |       |
| Anything surprising or concerning in the transcript?     |   |   |       |

Evidence: <path to report/transcript A>, <path to report/transcript B>
```

## Ground rule

A default app prompt (a chat mode's system prompt, a tool's `description`/
`hint`) changes only after comparative evidence from this pack — a `run`
report showing the change actually helps on a real scenario — never from
intuition alone.
