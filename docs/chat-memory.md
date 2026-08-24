# Chat Memory

The built-in chat can remember durable facts about a user across sessions — a style
preference, a checkpoint's quirks, a habit worth not re-explaining every time. This is
**memory**: small, structured notes, distinct from a session's conversation history (which
already persists on its own) and from the ephemeral workspace/PROMPT STATE blocks a turn
injects for the form currently open.

## Scopes and identity

Every note lives at exactly one of three scopes:

- **`global`** — true regardless of what preset or model is open.
- **`preset`** — true only for one preset (`scope_ref` = preset id).
- **`model`** — true only for one checkpoint/LoRA (`scope_ref` = model id).

A note's address is the tuple `(user_id, key, scope, scope_ref)`. Writing to an address
that already has a note **updates it in place** rather than creating a duplicate — the
repository upserts on that exact tuple (`src/features/llm_memory/repository.py`).

Content is capped at 500 characters; anything longer is rejected outright with a message
telling the caller to distill the fact rather than being silently truncated. Two further
write-time rejections exist specifically to keep memory from turning into a generation log:
a note mentioning a seed or a generation ULID, and — at `global` scope only — a note that's
mostly a parameter dump (`cfg`, `steps`, `sampler`, …) with no descriptive prose (that kind
of note is legitimate at `preset`/`model` scope, where it belongs). All of this lives in
`LLMMemoryManager.write_note`/`_validate_content` (`src/features/llm_memory/manager.py`).

## How notes reach the model

Nothing has to ask for memory — relevant notes are injected into every turn automatically,
as a system block placed immediately before the user's message
(`ChatContextBuilder.inject_memory_block`, `src/features/chat/context_builder.py`). The
block groups notes under `[global]`, `[this preset]`, and `[this model]` headers, each
scope read and ordered by `updated_at DESC` (repository `list_notes`), so the
most-recently-touched facts in a scope always come first.

Each group is capped at 20 notes; if a scope has more, the extra ones are left out and the
block says so explicitly — `(+N older notes not shown — consolidate or prune in the memory
panel)` — so the omission is visible to the model, not just silently dropped. A single
note's content is clipped at 500 characters with a visible `…` if it somehow arrives longer
than the write-time cap allows.

This injection happens **before** the conversation's history token budget is applied
(`ConversationRunner._apply_history_budget`, `src/features/chat/conversation.py`), so the
memory block counts against that budget like everything else sent to the model — it no
longer escapes it for free. It's also explicitly protected from the budget's trim pass
(`min_protected=2` covers the memory block plus the current user message) so a memory block
that alone would exceed a tight budget still survives; older conversation turns are dropped
first instead.

## The tools

Four tools give the model (and, indirectly, the user) direct control over notes, alongside
the automatic injection above:

- **`write_memory`** — saves a note. `scope` is required with no default (the tool refuses
  to guess `global`); `scope_ref` auto-resolves from the session's active preset/model when
  omitted. Saves immediately, no approval needed.
- **`update_memory`** — edits an existing note's key and/or content, addressed either by
  `note_id` or by `(scope, key)` — the same address shown alongside every note already in
  context, so the model doesn't need a `read_memory` round-trip first. Requires user
  approval, showing an old → new preview.
- **`read_memory`** — an explicit read, filterable by scope (`all` returns global +
  active-preset + active-model). Mostly useful for a scope not already in context, or to
  double-check something before writing.
- **`delete_memory`** — removes a note by id. Requires user approval, showing a preview of
  what will be deleted.

The base system prompt reinforces update-over-append: once `update_memory` is available,
the model is told to check the notes already injected into context first, and call
`update_memory` on a matching one instead of writing a near-duplicate
(`DEFAULT_TOOLS_SYSTEM_PROMPT_TEMPLATE`, `src/features/chat/modes/builtin.py`) — the goal is
a memory set that stays dense rather than one that just accumulates.

Relevant endpoints (used by the memory panel, not just the tools):

- `GET /api/chat/memory?scope=&scope_ref=` — list notes.
- `POST /api/chat/memory` — create/update a note (same upsert semantics as `write_memory`).
- `PUT /api/chat/memory/{note_id}` — edit a note.
- `DELETE /api/chat/memory/{note_id}` — delete a note.

## Background reflection

Beyond what the model chooses to save mid-conversation, a background pass periodically
reviews the transcript for durable facts the model didn't explicitly call `write_memory`
for. It fires as a fire-and-forget task after a turn, once at least 4 user messages have
arrived since the session's last reflection (or since it started), gated by a per-LLM-config
`memory_reflection` toggle (default **on**) — `ChatReflectionGenerator`,
`src/features/chat/reflection.py`.

Reflection is told the turn's actual active preset/model ids and instructed to reuse them
verbatim for a scoped fact. If the model reports a `preset`/`model` scope with any other
`scope_ref` — a hallucinated id, or one that doesn't match what was actually active this
turn — the note falls back to `global` scope rather than being trusted or dropped
(`_validate_scope`). Extracted facts get a slugified key derived from the model's own label
for the fact, so re-reflecting the same topic later updates the existing note instead of
duplicating it.

## Auto-compaction

Right after a reflection pass persists at least one note, every one of the user's
`(scope, scope_ref)` groups is swept for size: any group over 15 notes gets sent to the LLM
to consolidate down to at most 10, merging duplicates and closely related facts while
preserving every distinct fact (`MemoryCompactor`, `src/features/chat/memory_compaction.py`).
Compaction never runs from the interactive `write_memory` path — only after reflection.

The result is checked for plausibility before anything is written: between 3 and 10 output
notes is accepted, anything outside that range looks like it dropped facts (too few) or
didn't actually compact (too many), and the group is left untouched instead. On success, the
consolidated notes are written and every note not among them is deleted from that group.

## Observability

Two places surface how much of the context budget memory is actually using:

- **The context ledger.** Every turn's persisted `behavior_trace` (on the assistant
  message) includes a `context_ledger`: character and estimated-token sizes for the system
  prompt, the resolved tool schemas, the memory block, and the full set of history messages
  actually sent — the same chars/4 heuristic the history budget itself uses
  (`ConversationRunner._build_context_ledger`, `src/features/chat/conversation.py`).
- **The `loading_memory` status event.** On the streaming send path, this event's `detail`
  reports `note_count`, `by_scope`, `by_scope_dropped` (per-scope overflow beyond the
  20-note cap), and `injected_chars` for that turn.

The memory panel (opened from the chat header, `ChatMemoryPanel.svelte`) lists notes grouped
by scope for browsing, adding, editing, and deleting — and also surfaces a footprint view per
group: how many notes and roughly how many chars/tokens of that scope are injected every
message, with a visible indicator when a group has grown past its 20-note injection cap (so
some of its notes are currently not making it into context at all). That's the same
`by_scope`/`by_scope_dropped` accounting described above, read back for a human.

## For operators

- The raw per-call LLM request/response trace table (`chat_llm_call_traces`, behind
  Admin → Developer's trace viewer) prunes rows older than **7 days**
  (`RETENTION_DAYS`, `src/features/llm/trace_repository.py`), checked opportunistically off
  the write path. The `context_ledger`/`behavior_trace` on a chat message is different — it's
  ordinary message metadata, so it persists indefinitely with the message, independent of
  that table's retention.
- Cost intuition: nothing in the context ledger is cached across turns — the memory block,
  the tool schemas, and the system prompt are all rebuilt and resent with every single
  message. A user with a large, sprawling memory set pays for that block's tokens on every
  turn, not just once. Scoping notes narrowly (so fewer are eligible per turn) and letting
  reflection/compaction do their job keeps that cost from creeping up on its own.
