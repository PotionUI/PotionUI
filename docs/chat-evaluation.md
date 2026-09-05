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

A **scenario** (`scenarios/<id>.json`) describes one chat workflow: the chat
mode, the tools it may use (`tool_names`, resolved against the real registry —
never hand-copied), the app context the turn starts from, the user's turns,
example canned tool responses, and a `checks` list of declarative predicates a
transcript of this scenario must satisfy. A **transcript**
(`transcripts/<name>.json`) is the recorded message sequence of one run —
either the canonical `<scenario id>.good.json` or a deliberately broken fixture
used by `test_evaluator.py`. Both file kinds carry a `version` field. Full
shapes are documented in the docstrings of `fixtures.py` and `evaluator.py`.

Checks are predicates over the transcript, never an expected exact call
sequence — e.g. "a call to `search_model_prompts` with `model_id` matching the
active model occurred before the final answer," not "the model must call
these three tools in this order." See `evaluator.py`'s `_CHECKS` for the full
vocabulary (`tool_call_present`, `final_answer_contains_all`,
`final_answer_not_contains`, `max_tool_rounds`, `latest_question_reflected`,
`truthful_apply_status`, `dry_run_never_enqueues`, `error_then_recovery`,
`capability_declined`), plus one structural check that always runs regardless
of scenario: every tool call in the transcript names a real tool with
schema-valid arguments.

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

Drives the real chat HTTP API (`POST /api/chat/sessions`,
`POST /api/chat/sessions/{id}/messages`) against an **already-running**
PotionUI backend and an **existing, explicitly named** LLM configuration.
There is no default — omitting `--config` prints exactly what the command
would do and exits non-zero rather than guessing a provider. `--token` (or the
`POTIONUI_CHAT_EVAL_TOKEN` env var) is a bearer token from an existing logged-in
session; the command does not manage login itself.

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

`--variant compact|large` applies an **explicit** `provider_options.context_window`
override (4096 / 131072 tokens) to the named configuration for the duration of
the run, and restores the original value afterward — never a value inferred
from the model's own size. Equal scenarios run under both variants for a fair
comparison; omitting `--variant` runs the configuration's own defaults
unchanged.

## The report

Both entry points write the same JSON shape (documented in full in
`scripts/chat_eval.py`'s module docstring): one entry per scenario carrying
`passed` + the individual `checks`, plus, when known, the provider identity,
effective `thinking_mode`, the context ledger's `capacity_tokens` /
`capacity_source` / `accounting_tier` and component sizes (system prompt, tool
schemas, memory, history), input/output token counts, the tool-round count,
and any errors. A field this run could not actually measure is marked
`"unverified": true` rather than omitted or guessed — every field in `replay`
mode (no LLM call happens), and latency/memory in `run` mode (the HTTP round
trip doesn't expose the backend's own timing or RSS).

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
