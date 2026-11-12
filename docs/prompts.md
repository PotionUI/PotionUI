# Prompt Expansion

A prompt that reaches the backend is a **template**, not final text. Before the pipeline is built,
the orchestrator expands it into one realization **per image**, seeded off the generation seed.

This is the authoritative reference. Where a detail depends on a source file, that file is named.

## The grammar

The prompt editor never asks a user to type this syntax — its variable and choice chips *serialize*
to it. The upshot is that a prompt copied from Civitai or A1111 parses straight into chips, and a
prompt authored in PotionUI pastes back out into those tools.

| Form | Meaning |
|---|---|
| `{a\|b\|c}` | pick one at random |
| `{0.5::a\|0.3::b\|c}` | weighted pick; omitted weights default to 1 |
| `{2$$a\|b\|c}` | pick two, without replacement |
| `{2$$ and $$a\|b\|c}` | pick two, joined by `" and "` |
| `{1-2$$ and $$a\|b\|c}` | pick between one and two |
| `${name}` | substitute a prompt variable |
| `<<if $x = "a">>…<<else>>…<<end>>` | pick text by a variable (see [Conditions](#conditions)) |

Expansion is done by the [`dynamicprompts`](https://github.com/adieyal/dynamicprompts) library
(`SamplingContext`, `SamplingMethod.RANDOM`).

### Deliberately unsupported

- **`__wildcard__`** — the `#phrasebook` chip system supersedes it, and is better: DB-backed,
  searchable, with per-generation shuffle. A bare `WildcardManager()` resolves nothing, so a stray
  `__foo__` survives as literal text (with a warning) rather than failing the generation.
- **`#` comments** — `#` is the phrasebook trigger in the editor, so a literal `#` is plain text.
- **`%{wrap}`**, parameterized wildcards, and the `~` / `@` sampler sigils.

A literal `$`, `%` or `#` (`$100`, `100% cotton`, `#5`) is plain text; only `${`, `$$` inside braces and `%{...$$...}` are grammar.
A template that still fails to parse is used verbatim and the user gets a "Check your prompt" warning toast.

An undefined `${var}` expands to the empty string rather than raising, so a typo degrades the prompt
instead of failing the generation.

## Variables

`GenerationRequest.variables` is a `name -> value` map (`src/features/generation/dto.py`). A value is
itself a template, so `{"mood": "{noir|sunlit}"}` re-samples per use. Variables are scoped to the
generate tab; there is no variables table.

The LLM chat's `manage_prompt_variables` tool (`src/features/llm/tools/builtin/prompt_variables_tool.py`)
can create, update, and remove a tab's variables, approval-gated the same way `update_form_settings`
is. A tab's variables live in its server-side draft (`/api/tab-drafts`, `src/features/tab_drafts/`), keyed
by the tab id the chat sends as `form_state.tab_id`. `get_prompt_variables` and `manage_prompt_variables`
read that draft (rendered by `src/platform/resources/prompt_variables.py`) to validate names, count, and
option caps; an approved change is applied to the draft on the server, which then pushes
`tab_variables_changed` to the user's notification socket so open tabs update. The same tools work over MCP
with an explicit `tab_id`, and `list_tab_drafts` lists the caller's tabs. Only the last rolls
(`form_state.variable_rolls`) still come from the browser. The model never invents a `${name}`: it either
references one already in the draft, or calls this tool first.

A choice option is a plain string, or `{text, when: {var, values}}` to make it eligible only
when the referenced variable last resolved to one of `values`; the wire op passes conditioned
options through unchanged, in the same shape they were sent. `when.var` must already be a choice variable on
the tab (or set earlier in the same `operations` batch), must not be the variable being
defined, and must not depend on it in turn — the validator walks the `when` edges of every
variable in scope and rejects a cycle. `when.values` must match the referenced variable's
option texts exactly; there is no fuzzy repair. Every rejection names the fix, e.g. `'dance':
when.var 'musc' is not a choice variable on this tab; choice variables: music, era.` A variable
with conditioned options resolves after the variables its `when.var`s name — `get_form_state`
and `@form` show this as a `resolves after $music` marker plus a `1 conditional` count, and
`get_prompt_variables` returns each conditioned option with its `when`.

`get_form_state` and `@form` carry a compact summary per variable, never the full option lists:
`outfit_kind: choice, shuffles each generation, 40 options (first 3: …)`, a number's range and step,
a text's length and a clipped preview, the pinned value, and one closing line pointing at the read
tool. The read-only `get_prompt_variables` tool (same file, generation mode, no approval) lists
those summary rows (optional `search` over names and option texts) or, given `names`, returns every
option with its 0-based `index` (what `pinned_index` takes), its condition, the number grid
(`min`, `max`, `step`, `count`, `decimals`) or the full text, paged with `offset`/`limit`/`total`/
`has_more`; an unknown name answers with close-match suggestions. Over MCP it takes an explicit `tab_id`.

## Conditions

A condition chooses a piece of text by the value of a variable. It is stored as plain text in the
prompt, so it round-trips through saved prompts, history and `<tool_action>` bodies:

```
portrait of a woman, <<if $age > 65>>grey hair, deep wrinkles<<else if $age < 25 and $mood in "calm", "serene">>youthful, soft skin<<else>>auburn hair<<end>>, soft window light
```

`src/features/prompt/conditions.py` (standard library only, linear time) parses and evaluates them
inside `expand_prompts`, so every generation path (Generate, batch, X/Y, both Directors, PotionAI
`run_generation`, MCP) gets them with no extra wiring.

| Operand | Tests |
|---|---|
| number variable | `= != < <= > >=` against a number, `in 18..30` / `not in 18..30` (inclusive) |
| choice or text variable | `= "x"`, `!= "x"`, `in "a", "b"`, `not in "a", "b"`, `contains "x"`, `not contains "x"`, `is empty`, `is not empty` |

- Spaces around every operator are required (`$age > 65`, never `$age>65`). Keywords are lowercase.
- A head holds 1 to 32 tests joined by `and` or by `or`, never both; use another `else if` for mixed cases.
- A quoted value holds 1 to 2,000 characters, the longest a choice option can be; a longer one makes the
  head malformed. A head holds at most 4,096 characters; a longer `<<if …>>` is not a tag and is removed.
- The first true branch wins; `<<else>>` is optional; there is no nesting.
- Numbers compare exactly (`Decimal`), so `$age > 9` is true for `10`. `=` on text is exact and
  case-sensitive on the trimmed value; `contains` ignores case.
- An undefined variable makes every test false, even `!=`, so the Otherwise text is used.
- A chosen body is inserted verbatim. If the result is empty, the spaces around it collapse and one
  neighbouring comma is dropped (`woman, <<if …>>x<<end>>, soft light` becomes `woman, soft light`);
  newlines are never removed.
- `{a|b}`, `${x}`, `(x:1.2)` and newlines are fine inside a branch. `<<hello>>`, `<lora:x:1>` and
  `<Picture 1>` are not tags and stay literal.

**Errors never fail a generation.** A tag that is not part of a well-formed block (unclosed `<<if`,
stray `<<else>>`/`<<end>>`, nesting, more than 128 blocks or 32 branches per channel, anything past
20,000 characters) is removed and the text around it is kept. A malformed head inside a good block
counts as false. Either case raises one "Check your prompt" warning per generation.

Reading a head costs up to 4,098 characters when it never reaches `>>`, so one scan of a text reads at
most 80,000 characters (`MAX_HEAD_SCAN_CHARS`, four times the 20,000-character window) of heads that
never close. Once that is spent, every further `<<if`/`<<else if` in the same scan is a malformed tag. A
prompt needs about twenty unclosed tags before this happens, and both parsers count code points
identically, so a 20 KB flood of junk tags parses in a few milliseconds on either side.

**PotionAI.** The generation mode's system prompt teaches the grammar with three examples
(`src/features/chat/modes/builtin.py`). Each turn's PROMPT STATE lists the tab's variables with the
exact values a condition compares against, choice options quoted the way a condition writes them
(`"12\" vinyl"`), and flags a broken condition under its segment. `write_condition`
(`src/features/llm/tools/builtin/condition_tool.py`, generation mode, no approval) builds a block from
structured branches with `conditions.format_block`, or checks hand-written text against the tab's
variables, and returns the text to paste. Tools that save segment text refuse malformed tags with the
exact problem and, when the slip is mechanical, the corrected text
(`src/features/llm/tools/builtin/condition_text.py`). A `<tool_action>` segment proposal written with
`{% if %}`, `elif`/`endif`, `==`, `&&`, single quotes or `${name}` inside a tag is rewritten into the
grammar before the reply is saved; anything ambiguous is left as written for the card to flag.

**Evaluation order, per image** (seed = base seed + image index):

1. the `prompt.transform` `pre` hook runs on the template, tags included (handlers must leave
   `<<…>>` intact; they may add tags);
2. every variable a condition reads is rolled once from `Random(f"{seed}:var:{name}")` and rebound as
   that literal, so `${age}` and every condition agree;
3. each block is decided and replaced by a placeholder character;
4. the rest of the template is sampled with the usual `Random(seed)`, so every other `{a|b}`
   rolls exactly as it would if the condition were not there — flipping a variable changes only the
   condition's own text;
5. the chosen body is sampled with `Random(f"{seed}:{channel}:{block}")` and put in place of its
   placeholder, then empty-result cleanup runs;
6. the text is trimmed and the `post` hook runs.

A prompt without any tag skips steps 2 to 5 and expands exactly as before. History keeps the
template (`prompt_state`, `form_data`) and the per-image resolved text (`positive_prompt`).

## Number variables

A variable of `type: "number"` has `min`, `max`, `step` and a `mode`, the same three modes as a choice:

```json
{"type": "number", "mode": "per-image", "min": 18, "max": 80, "step": 1}
```

- `pin` sends the `value` (clamped to the range); `shuffle` (default) is rolled once per Generate
  click; `per-image` is sent as `{18|19|…|80}` and the backend rolls it per image.
- Values lie on the grid `min + k*step`, formatted from the grid index with as many decimal places as
  `step` has (`0.1` gives `0.5`, never `0.5000000001`). `max` is only reachable when it is on the grid.
- The grid is capped at 200 values; a larger one is rejected with the step that would fit.
- `min`, `max`, `step` and `value` must be plain numbers below 10^12 in magnitude with at most 12
  significant digits (no exponent tricks such as `1e30`); `step > 0` with at most 6 decimal places;
  `min <= max`; and `min` may not have more decimal places than `step`. Anything else is rejected
  with a message naming the limit. A number can also be used outside conditions, e.g. `(red dress:${weight})`.

## Seeding

`src/features/generation/orchestrator.py::_expand_prompts_per_image` runs just before
`build_pipeline`. It:

1. reads `quantity` and `seed` from `form_data` (both are flat keys — `get_form('custom', ['seed'])`
   resolves to `form_data['seed']`, because `PresetProcessor` assigns the same flat dict to every
   form name);
2. resolves `seed == -1` **eagerly** via `generate_seed()` and **writes it back into `form_data`**;
3. expands image `i` with `base_seed + i`, mirroring how `seed_generator` derives its own per-image
   seeds.

Step 2 is the load-bearing one. Without it, `seed_generator` would draw an independent random seed
per image at pipe time, and the prompt expansion could never be reproduced from the recorded seed.
With it, prompts and latents move together: re-running a generation at the same seed reproduces the
same batch of prompts *and* the same images.

Only `prompts[0]` is treated as the template. Multi-prompt tabs are a separate concept; expanding
each would multiply the image count.

### Batch prompts

Send `batch_prompts: true` with more than one entry in `prompts` to run several distinct prompts as one
generation. Every entry is a template, expanded `quantity` times (so `quantity` means *per prompt*) in
prompt order. Image `i` of the whole run uses seed `base + i`. The native engine encodes all prompts in
one text-encoder pass instead of swapping models per prompt.

- **Cap:** at most 20 images in total (`len(prompts) x quantity`); over it the request gets a 400.
- **Engines:** a backend takes part only if it sets `supports_prompt_batch` (native, remote native and cloud
  do; ComfyUI and other one-prompt-per-run backends are refused with a 400). `GET /api/presets` and
  `GET /api/presets/{id}` report `supports_prompt_batch` per preset: true when every enabled backend of the
  preset's engine supports it.
- **Exclusions:** forms carrying a Video Director or Music Director document are refused (400). X/Y compare
  grids ignore the flag, so a grid cell is never a batch.
- **Storage:** the batch runs once (one queue slot, one daily-limit count) and is stored as one history entry
  per prompt. When the run ends, completed, failed or cancelled, the original entry becomes prompt 1 and each
  later prompt that produced files gets its own entry, with its own `quantity`, its first-image `seed`
  (`base + k * quantity`) and its own `prompt_state`. Files keep their ids and paths; tags, collections,
  models, input sources, per-image parameters and prompt segments follow. The run report, cost and stats
  stay with the original entry. All entries share a `batch_id` (the original generation id). If the split
  fails, the entries stay together and the generation carries a `message` saying so.
- **`prompt_state`:** if it holds a `prompts` list with one entry per authored prompt, entry `k` is merged
  into row `k`'s state (the list itself is removed); otherwise every row keeps the state unchanged.
- **Reloading a finished batch:** `GET /api/generations/history/{id}?include_batch=true` returns, in
  `data.files`, the files of every entry in the batch (original first), each with `prompt_index` (0-based),
  `prompt` (the entry's authored text) and `generation_id`. Without the flag the response is unchanged.
- **Live updates:** `gallery_update` entries (`image_urls_list`, `video_urls_list`) carry `prompt_index` and
  `prompt` while the run is in flight, on the original generation id. Progress reads
  `Generating image 5 of 12 · prompt 3 of 6` and `Reading 6 prompts`; single-prompt runs keep their text.
- **MCP / chat:** `start_generation` takes `prompts: [{positive, negative?}]` instead of `prompt` /
  `negative_prompt` (mutually exclusive) and sets `batch_prompts` for you. It is faster than one call per prompt.

Expansion never fails a generation — a malformed template (unbalanced brace, bad weight) falls back
to the literal text the user typed.

## How the expanded prompts reach the pipes

`PresetProcessor` (`src/features/presets/processor.py`) exposes the expanded prompts under the
`generation.prompts` template context root (see [Pipelines → `pipeline.yml` context](presets/pipelines.md#pipelineyml-context)):

| Reference | Value |
|---|---|
| `generation.prompts.first` | `pairs[0]` — `{positive, negative}`, the scalar every preset already used |
| `generation.prompts.pairs` | `[{positive, negative}, ...]`, one entry per image |
| `generation.prompts.positives` / `.negatives` | the same, split per channel |

Because `first` still means `pairs[0]`, **every existing preset keeps rendering unchanged**. The
per-image data rides alongside in `pairs`. An exact `{{ expression }}` scalar referencing any of
these evaluates to its native Python value (a dict or a list, not a stringified one) — there is no
separate opt-in directive; real example (`content/presets/marketplace/ZImage/modes/txt2img/pipeline.yml`):

```yaml
- name: "prompt_encoder"
  configuration:
    p_prompt:
      input: "{{ generation.prompts.first.positive }}"
      output: "{{ generation.prompts.first.positive }}"
    n_prompt:
      input: "{{ generation.prompts.first.negative }}"
      output: "{{ generation.prompts.first.negative }}"
    pairs: "{{ generation.prompts.pairs }}"

- name: "param_emitter"
  configuration:
    parameters:
      - ["positive_prompt", "{{ generation.prompts.positives }}"]
      - ["negative_prompt", "{{ generation.prompts.negatives }}"]
```

`param_emitter` stores an array of length `quantity` per-index, so `GET /{id}/params/{index}` — the
call the history modal already makes — returns the prompt **that image actually ran with**.
`generation_segments.text` keeps the authored recipe; the two answer different questions.

A preset that saves the same batch more than once (Krea-2 txt2img saves the base batch and the
inline-enhance batch) sets `passes:` to the number of gallery saves. `param_emitter` then writes
`quantity × passes` rows and tiles the per-index arrays across the passes, so every saved index —
not just the first batch — resolves a full parameter set.

`prompt_encoder` swaps each image's prompt into the preset's wrapper template (the authored prompt
plus enabled embedding tokens) via `_substitute_pair`, so the suffix survives. For image 0 the
substitution is a no-op by construction.

An upstream `prompt_expander` (the LLM one) wins: when it supplies `p_prompt` on the pipe input,
`prompt_encoder` ignores `pairs`.

## Fixed prompt text

An admin can give any installed preset row, a shipped preset or a mirror, a fixed prefix, a fixed
suffix and a fixed addition to the negative prompt. They live in `presets.fixed_prompt` as
`{"prefix", "suffix", "negative", "modes"}` and are edited through
`GET`/`PUT /api/presets/{id}/fixed-prompt` (admin only; `src/features/presets/fixed_prompt_routes.py`).
Each text is at most 2000 characters and is checked by expanding it once: a malformed `{a|b}` or
condition is refused with `400 invalid_fixed_prompt`. `modes` is `null` for every prompted mode, or
an allow-list of the preset's modes; a mode listed in `promptless_modes` is refused. A mirror does
not inherit its source's fixed text. When the instance has a banned prompt list, a prefix or suffix
containing a banned word (as written or after one expansion) is refused at save time, because content
safety scans the applied prompt and would refuse every generation; the negative is not scanned by
content safety and is not checked.

**Where it is applied.** In `GenerationOrchestrator._start_generation`, right after the user's
prompt is expanded per image and before content safety and `build_pipeline`. The user's realized
pairs are kept as the *visible* pairs; `PromptExpander.expand_fixed` expands the prefix, the suffix
and the negative once per image with the same `base_seed + i` as the user prompt, and
`src/features/generation/fixed_prompt.py` joins them:

| Channel | Applied text |
|---|---|
| positive | `prefix`, user prompt, `suffix`, the non-empty ones joined by one space |
| negative | user negative, fixed `negative`, the non-empty ones joined by `", "` |

The pipeline, the encoders and content safety see the applied pairs.

**It is not applied** when all three texts are empty, when the generation's mode is not in
`modes`, when the mode is in the preset's `promptless_modes`, when the request has no prompt, or
when `form_data` carries a `video_director` or `music_director` document: those documents hold
their own prompts in `form_data`, which is stored and returned to the user as submitted.

**Grammar.** Fixed text goes through `expand_prompts` like any prompt: `{a|b}` choices, weights,
conditions and `${variables}` from the request's variables work, and `prompt.transform` fires for
each part. Each part is expanded separately, so a variable assigned inside the user's prompt is not
visible to the prefix or suffix. Prompt-resource tokens are resolved before expansion, from the
user's prompt only, and do not work in fixed text.

**Who sees it.** A regular user never sees fixed text. Every prompt that is stored or broadcast is
the user's version: the orchestrator masks each output before handlers run and before broadcast,
replacing `positive_prompt`/`negative_prompt` parameters and `rendered_prompt` artifacts with the
visible pairs, and prompt batches take their `prompt_text` from the visible pairs. History details,
parameters by index, archive and A1111 exports, inspirations and chat context all read that stored
user version. The applied pairs are kept in `generation_applied_prompts`; only admins get them, on
`GET /api/generations/history/{id}`, `GET /api/generations/{id}/params/{index}` and
`GET /api/admin/generations/{id}`, which then carry `"fixed_prompt_applied": true`. When a prompt batch is
split into one history entry per prompt, each entry keeps only its own images' applied pairs. The live view
shows admins the user version too.

## Known limitation: ComfyUI is per-batch, not per-image

A ComfyUI preset submits **one** workflow with `batch_size = quantity`, a single prompt text node,
and `seeds[0]` (`content/plugins/marketplace/comfyui-backend/backend/pipes/comfyui/main.py`). Per-image
prompts are not expressible there.

So a ComfyUI preset consumes `pairs[0]`: **one seeded, deterministic roll shared across the batch.**
`{red|blue} hair` at quantity 4 yields four images with the same colour. This is expected, not a bug.

Making it per-image means submitting N workflows at `batch_size=1` when the expanded pairs differ —
a change to the comfyui-backend plugin's progress aggregation, output collection, and cancellation.

## Extending it: the `prompt.transform` hook

Declared in `src/features/prompt/hooks.py`. It fires **twice per image**:

- `phase="pre"` — on the authored template, before it is sampled.
- `phase="post"` — on the expanded text.

Payload: `generation_id`, `image_index`, `phase`, `seed`, `positive`, `negative`. Mutable:
`positive`, `negative`. A handler that throws is logged and skipped; it cannot fail the generation.

```python
# hooks/prompt_hooks.py
def on_transform(context: HookContext) -> HookContext:
    if context.data["phase"] == "post":
        context.data["positive"] += ", masterpiece"
    return context
```

```yaml
# manifest.yml
hooks:
  backend:
    - hook: "prompt.transform"
      handler: "hooks.prompt_hooks.on_transform"
```

## Note on `preset.yml` `wildcards:`

The `wildcards:` download list in `preset.yml` and the `path('wildcard')` resolver are now inert —
nothing reads them. They are left in place rather than removed as unrelated cleanup.
