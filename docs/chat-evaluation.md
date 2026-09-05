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
(`transcripts/<name>.json`) is the recorded message sequence of one run —
the canonical `<scenario id>.good.json`, a deliberately broken fixture used by
`test_evaluator.py`, or a live capture `chat_eval.py run` saves under
`--transcripts-dir`. Full shapes are documented in the docstrings of
`fixtures.py`, `evaluator.py`, and `scripts/chat_eval.py`.

Checks are predicates over the transcript, never an expected exact call
sequence — e.g. "a call to `search_model_prompts` with `model_id` matching the
active model occurred before the final answer," not "the model must call
these three tools in this order." See `evaluator.py`'s `_CHECKS` for the full
vocabulary (`tool_call_present`, `final_answer_contains_all`,
`final_answer_not_contains`, `max_tool_rounds`, `latest_question_reflected`,
`truthful_apply_status`, `dry_run_never_enqueues`, `error_then_recovery`,
`capability_declined`), plus one structural check that always runs regardless
of scenario: every tool call names a tool available to THAT scenario (not
just anything in the registry) with schema-valid arguments — type, required,
enum, numeric bounds (`minimum`/`maximum`/`exclusiveMinimum`/`exclusiveMaximum`),
and nested object/array shapes, checked against the tool's real JSON schema.

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
a `live_supported` check carrying the fixture's own `live_unsupported_reason`.
For a supported scenario, the raw SSE event stream for each turn is captured,
converted into the same transcript shape `evaluator.py` scores (from the
turn's persisted `done` event — the same `assistant_message.tool_executions`
`chatStream.ts`'s `applyDone` reads, not the incremental `tool_start`/`tool_end`
deltas meant for live UI rendering), saved to a file under `--transcripts-dir`,
and scored with the exact same `evaluate_transcript` checks `replay` uses —
the report's `transcript` field names that saved file, never the bare string
`"live"`. `http_completed` (did every turn finish without an HTTP/stream
error) is reported separately from `passed` (did the captured transcript pass
its scenario's checks) — a turn can complete successfully over HTTP and still
fail a task check, and that distinction must survive into the report.

Every mutating tool a scenario can reach (`update_form_settings`,
`start_generation`, `propose_form_changes`, `run_generation`, ...) is already
approval-gated in the real tool loop — its `execute()` only returns a
`pending_approval` preview. `run` never calls the tool-approval endpoint, so
those calls always stay a dry preview; nothing is ever applied. The one
builtin tool that mutates state *without* an approval gate is `write_memory`
(see `docs/chat-memory.md`) — it is excluded from every session's
`enabled_tools` for that reason. `run` never touches this repository's own
inference code and never loads a model; the selected configuration's provider
must already be running.

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
the individual `checks`, and, when known, the provider identity (`config_id`/
`type`/`model`/`is_default` — no fabricated "revision" field; LLM
configurations don't have one), effective `thinking_mode`, the context
ledger's `capacity_tokens` / `capacity_source` / `accounting_tier` (the
ledger's `accounting` field: `"estimate"`, `"chat_template"`, ... — see
`src/features/llm/context_budget.py`) and its `measured` qualifier (whether
that tier came from an exact chat-template recount or a fragment estimate),
plus component sizes (system prompt, tool schemas, memory, history),
input/output token counts, `tool_rounds` (turns that used at least one tool —
a coarse measure) separately from `tool_executions` (the precise total count
of individual tool calls), and any errors. A metric this run could not
actually measure is marked `"unverified": true` (or, for a value that might
legitimately be a known `null`, a `{"value": ..., "unverified": bool}` pair —
used for token counts and latency/memory) rather than silently defaulted to
`0`/`None` and presented as a known value. Every field is unverified in
`replay` mode; in `run` mode, latency/memory always are (the HTTP round trip
exposes neither the backend's own timing nor its RSS), and any per-field
value the provider itself didn't report is marked unverified rather than
guessed.

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
