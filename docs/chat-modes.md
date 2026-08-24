# Chat Modes, Tools, Styles & @Resources

The built-in AI chat is organized around **modes**. A chat session is created in exactly
one mode and keeps it for its whole life — the mode selector locks as soon as the
conversation has messages. Each mode owns:

- a **base system prompt** (may contain the `{{TOOL_HINTS}}` placeholder, substituted with
  the hints of the session's allowed tools),
- a **tool set** (its own tools plus all *global* tools),
- optional **LLM option overrides** and a **context contributor** (see below).

The selection hierarchy in the chat header is **Provider + Model (LLM config) → Mode → Style**.

## Built-in mode

`generation` — the default. Its system prompt is code-owned
(`DEFAULT_TOOLS_SYSTEM_PROMPT_TEMPLATE` in `src/features/chat/modes/builtin.py`; not
an editable setting), and it includes all generation tools (form state, segments,
prompt search, run generation, etc.). Memory tools are global and available in every mode.

## Route resolution

The frontend resolves the mode from the current route when the chat panel opens and when
a new conversation starts: the longest matching `default_route_prefixes` entry across all
registered modes wins; the fallback is `generation`. A plugin page like
`/plugins/dataset-generator/...` therefore auto-selects that plugin's mode. Restored
sessions always keep their persisted mode regardless of the current route.

Relevant endpoints:

- `GET /api/chat/modes` — all registered modes (`id`, `name`, `description`, `icon`,
  `default_route_prefixes`, `tools`, `resource_namespaces`, `source`).
- `GET /api/chat/tools?mode=<id>` — the tools visible in a mode (each item carries
  `mode`, `modes`, `icon`, `label` for the UI).
- `POST /api/chat/sessions` — takes `mode` (default `generation`); unknown modes are
  rejected with an `unknown_mode` error.
- `GET /api/chat/sessions?mode=&search=&limit=&offset=` — history listing
  (`{sessions, total, limit, offset}`).

## Tool scoping

A tool belongs to one or more modes (`BaseTool.modes`) or is global (`modes = None`).
A session sees its mode's tools plus the global ones, all enabled by default. The
session's `enabled_tools` list is a **subtractive filter**: the user can untick individual
tools in the header dropdown; `null`/omitted means "all mode tools", `[]` means none.
Tools from other modes are never visible.

## Conversation titles

After the first user/assistant exchange the backend asks the session's own LLM for a
3–6 word title (small, non-thinking call). On the streaming endpoint the title arrives as
a final SSE event **after** `done`:

```json
{"event": "title", "data": {"session_id": "…", "name": "Fox Forest Prompt Ideas"}}
```

Clients must not treat `done` as end-of-stream. Sessions expose `title_generated`; a
failed title attempt is retried after the next exchange (up to 6 messages).

## @Resources

Typing `@` in the chat input opens a resource autocomplete backed by
`GET /api/chat/resources/suggest?query=&mode=&limit=`. Selected resources render as
chips; the message text keeps a readable token (`@models.lora.detailer` or
`@[path with spaces]`) and the request body carries `resources: [{uri}]`.

Resolution is a **snapshot at send time**: the backend resolves each URI, stores the
resolved content in the user message's `metadata.resources`
(`[{uri, kind, title, metadata, content}]`), and injects one system-role context block
immediately before the last user message. Unknown/stale URIs become error notes and never
fail the send. Because history is rebuilt per turn, older snapshots naturally age out of
context.

Built-in namespaces:

| URI | Resolves to |
|---|---|
| `models.<type>.<name>` | model metadata incl. CivitAI trigger words and description (`models.loras.x` plural alias accepted; dots in filenames handled) |
| `phrasebook.<category.path>` | a phrasebook category's description and values |
| `presets.<preset_id>[.<form_field>]` | a preset summary or one form field's option list |
| `generations.<id>` / `generations.recent` | a generation's prompt/parameters/models, or the last 5 |

A mode can restrict the visible namespaces via `resource_namespaces` (null = all).

## Per-mode LLM options & context contributors

- `ChatMode.llm_options` (e.g. `{"think": false}`, temperature, etc.) is merged over the
  LLM config for **every** call in the mode — plain chat, the tool loop, and streaming.
- `ChatMode.context_contributor` — a callable `(context_metadata, session, user_id) ->
  Optional[str]` (sync or async). A non-empty result is injected as a system context
  block right before the last user message (before the @resource block). Failures are
  logged and never break a send. This is how a plugin mode turns its page's
  `context_metadata` into model-visible context without any core changes.

## Learning loop (mode-aware)

Prompt feedback (`POST .../prompt-feedback`) tags verdicts and approved exemplars with the
session's mode (`enhancement_feedback.mode`, exemplar `metadata.mode`). The `enhance_prompt`
tool retrieves `chat_approved` exemplars only from the current mode; rows created
before this feature count as `generation`.

## Plugin registration

See `.claude/skills/plugin/SKILL.md` ("LLM chat extensions") for the authoritative
manifest reference. Summary:

```yaml
chat_modes:
  - id: "dataset-generator"
    name: "Dataset Generator"
    icon: "database"
    system_prompt_file: "prompts/mode.md"   # or inline `system_prompt:` (mutually exclusive)
    tools: ["list_models"]                  # borrow builtin tools by name
    default_route_prefixes: ["/plugins/dataset-generator"]
    context_contributor: "hooks.chat.contribute_context"
    llm_options: {}

tools:
  - class: "tools.dataset:CreateDatasetTool"  # subclass of BaseTool, no-arg constructor
    modes: ["dataset-generator"]              # omit for a global tool

resources:
  - namespace: "datasets"                     # must equal provider.namespace
    provider: "resources.datasets:DatasetResourceProvider"
    modes: ["dataset-generator"]              # omit for all modes
```

Registrations are removed on plugin disable; sessions left in a vanished mode stay
listable but return `unknown_mode` on send. Name collisions fail the plugin enable.

## Breaking change (for plugin authors)

Chat hook payloads carry `mode` instead of `session_type`
(`chat.session.before_create/after_create`, `chat.response.transform`). The session
create API takes `mode` (not `session_type`) and no longer accepts `enable_tools`.
