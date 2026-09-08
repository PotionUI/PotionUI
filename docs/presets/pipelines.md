---
title: Pipelines
category: Presets / Models
category_order: 70
order: 17
---

# Pipelines

How `modes/<mode>/pipeline.yml` is built and rendered: the pipe shape, how `input:` wires
one pipe's output into another's, the three Jinja template contexts and which variables are
in scope in each, strict evaluation, the `@loop` recipe, speed-profile precedence, and the
standard native chain every shipped family uses. Every pipe's own `name:`, inputs, outputs,
and configuration keys are the generated [Pipes Reference](../pipes.md) — read this page for
how those pipes get wired together, that one for what each pipe actually takes/returns.

## Template contexts

YAML values are Jinja2 templates (`{{ ... }}` / `{% ... %}`), but **which variables are in scope depends
on where the template lives**. There are three distinct contexts — don't assume a variable from one is
available in another:

| Context | Rendered by | Available |
|---------|-------------|-----------|
| `pipeline.yml` values | `PresetProcessor.process` (at generation time) | Full set below: `form`/`request`/`generation`/`preset`/`runtime`/`paths` context roots + the `path`/`icon`/`get_speed_profile` globals. **No `paths._shared`.** |
| Form `children:` paths (form.yml / tab yml) | the loader, at load time (`src/features/presets/loader.py`) | **Only `{{ paths.preset }}`**, substituted textually. No form data, no `_shared`. |
| Field option-file paths (`file:`/`files:`/`phrasebook_source` of `select`/`resolution`) | `src/features/fields/select.py`, `resolution.py` (at option-load time) | **Only `paths.preset` and `paths._shared`.** |
| A `model`/`lora_picker` field's `filter_tags: "@config:<key>"` | `src/features/presets/configuration.py`'s `resolve_field_filter_tags` (at form-schema serve time) | **Not Jinja at all** — a bare `str.startswith('@config:')` prefix match against the preset's stored `configuration:` values. No context object; a typo in `<key>` (or a preset with no `configuration:` block) resolves to "no filtering", not an error. See [Preset Context Cheat Sheet](../preset-context.md#configkey-indirection). |

Practical consequence: form/tab YAML cannot read form data — that's pipeline-only. Make form
fields dynamic with `default:` and `reactions:`, not Jinja. Use `{{ paths._shared }}` only in a
`select`/`resolution` option-file path.

### Exact expressions vs string templates

Every templated scalar takes one of two paths (`src/platform/templating/processor.py`):

- **Exact expression** — a scalar that is, after stripping whitespace, *precisely one*
  `{{ expression }}` block (no surrounding text, no second block, no `{% %}` statement tag). It is
  compiled as an expression and evaluates to its **native Python value**: `steps: "{{ form.steps }}"`
  is an `int`, `enabled: "{{ form.enable_x | default(false) }}"` a `bool`,
  `loras: "{{ form.loras | default([]) }}"` a `list`. This is the canonical way to feed typed
  config to pipes and `@loop`.
- **String template** — anything else containing template syntax (mixed text, multiple `{{ }}`
  blocks, or any `{% %}` statement). Rendered the normal Jinja way and returned as a **string**,
  newlines preserved. Use it for prompts, labels, and genuinely stringy values.

So `"{{ form.steps }}"` is `30` (int) while `"steps: {{ form.steps }}"` is the string
`"steps: 30"`. Prefer the exact-expression form everywhere a pipe expects a typed value; the old
`{% if %}true{% else %}false{% endif %}` idiom is obsolete — write the boolean expression
directly.

### Strict evaluation — missing values are build errors

The environment is a Jinja2 **sandbox** (`ImmutableSandboxedEnvironment` — unsafe attribute access
and mutating methods are blocked) with **`StrictUndefined`**: referencing a variable or key that
doesn't exist **raises**, aborting the pipeline build with a structured error that pinpoints the
failure (`preset_id`, `source_file`, `mode`, `form_name`, `pipe_id`, `config_path`, `expression`,
cause). Nothing is silently swallowed into `None`/empty strings anymore.

The **only** way to tolerate a missing value is Jinja's `| default(...)` filter:

```yaml
steps: "{{ form.steps | default(30) }}"        # OK when the form has no steps field
steps: "{{ form.steps }}"                      # build error if steps was never submitted/defaulted
```

`| default(...)` isn't needed on every `form.<name>` reference — `bind_form`
(`src/features/forms/binding.py`) has already resolved every field the *bound* form declares to a
concrete value (the client's, an admin override, a `reactions:` result, or the field's own static
`default:`) before `pipeline.yml` ever renders, so `{{ form.<name> }}` for one of those fields
never raises and a `| default(...)` on it can never fire. A guard is load-bearing — actually
changes what renders — in exactly two cases:

- **The field doesn't exist in every form variant sharing this `pipeline.yml`.** A mode's variants
  can each declare a different field set; `bind_form` only binds the one variant a request actually
  submitted through. A field present in variant A's tree but absent from variant B's is genuinely
  undefined when a variant-B submission renders the pipeline — the guard prevents a real build
  error, and its fallback should match variant A's own `default:`.
- **The field declares no static `default:`.** An optional field with none binds to `None` when the
  client omits it (`bind_form` still inserts the key — it's `None`, not "undefined" — so this
  doesn't raise), which is rarely what a typed pipe config wants. The guard supplies the real
  fallback value here, same as it would for a plain Python `None`.

Otherwise — the field is declared (with a static `default:`) in every variant sharing the
pipeline — write the bare reference; the linter still warns when a `{{ form.<name> }}` reference
names a field that doesn't exist anywhere in the mode's form tree and has no default (see "Linting"
below), which catches a genuine typo either way.

### `pipeline.yml` context

The context roots built by `PresetProcessor.process` (verified in `src/features/presets/processor.py`):

| Reference | Value |
|-----------|-------|
| `form.<field>` | The **bound form's** values — typed and defaulted by `bind_form` before the pipeline ever renders (`form.steps` is an int, `form.loras` a list; see "Form binding and validation" below). Runtime-injected documents (`form.video_director`, prompt timelines, `form.llm`) arrive as ordinary keys of this same dict. |
| `request.mode` | The mode being generated, e.g. `'txt2img'`. |
| `request.form_name` | The form variant this submission used (see [Variants](forms.md#variants)); defaults to the mode's default variant. |
| `generation.prompts.first` | The first expanded prompt pair, `{positive, negative}`. |
| `generation.prompts.pairs` | All per-image expanded pairs (see [Prompts](../prompts.md)). |
| `generation.prompts.positives` / `.negatives` | The flattened per-side lists. |
| `generation.profile` | The `speed_profiles:` entry resolved for this request: the profile `form.speed_profile` names, or the first declared profile if the form has no matching field/value, or `{}` if the preset declares none — so `generation.profile.<key>` fails loudly (StrictUndefined) exactly like any other missing key, never silently. See [Speed profiles](manifest.md#speed-profiles). |
| `preset.id` / `preset.name` | The preset's id and display name. |
| `preset.vars` | The `vars:` mapping from `preset.yml` (e.g. `preset.vars.num_lora_slots`). |
| `runtime.settings.file_storage_directory` | The storage root, resolved **once per build** with the authenticated user (a snapshot — no live settings calls at render time). |
| `runtime.settings.nsfw` | The user's NSFW setting (same snapshot). |
| `paths.preset` | Absolute path to this preset directory. (No `paths._shared` in the pipeline context.) |

There is no `generation.seed`/`generation.quantity` (read `form.seed`/`form.quantity` — the
`seed_generator` pipe's own `seed`/`quantity` config is the only real consumer either ever had)
and no `preset.speed_profiles`/`preset.configuration` direct access (`get_speed_profile()`/
`generation.profile` above replace the first, `@config:<key>` field indirection the second — see
"Removed" below).

Allowlisted globals (registered in `src/platform/templating/processor.py`):

| Name | Signature | What it does |
|------|-----------|--------------|
| `get_speed_profile` | `get_speed_profile(profile_name, default=<raises>)` | Look up a `speed_profiles:` entry by name. Raises a clear error naming the preset and profile if missing and no `default` is given. See [Speed profiles](manifest.md#speed-profiles). |

Filters: `active_loras` (drops a `lora_picker` list's zero-strength entries — a `@loop`'s `items:`
expression filters through it, e.g. `{{ form.loras | default([]) | active_loras }}`;
missing/non-numeric/negative strengths are kept, only an exact-zero strength drops),
`strip_model_dir` (strips a model picker value's depot type directory —
`models/<vae|loras|checkpoints|...>/` — while keeping any subdirectories underneath, e.g.
`{{ form.vae | strip_model_dir }}`; `None`/`''` and a value with no such prefix pass through
as `''`/unchanged — see [Models and Backend Availability](../models.md)) plus all Jinja builtins —
`default` being the load-bearing one (see above).

Note: `device`, `dtype` and `gpu_max_vram` are **not** in the template context — they are
native-backend config, injected into every pipe by `NativeBackend.prepare_pipes`. See
[Backends and Engines](../backends.md).

**Removed** (build errors if used — the linter flags them with a migration hint): the `get_form`,
`value`/`get`, `contains`/`get_is_in`, `dict`, `setting`/`config`, `path`/`get_path_for`,
`icon`/`get_icon` globals, the `matches`/`regex_search` filter, the `@object:`/`@dict:` string
directives, the entire `input.*` context, and direct `preset.speed_profiles`/
`preset.configuration`/`generation.seed`/`generation.quantity` access. Their replacements:
`get_form('custom', ['steps'], 20)` → `{{ form.steps | default(20) }}`,
`setting('SYSTEM', 'file_storage_directory')` → `{{ runtime.settings.file_storage_directory }}`,
`input.generation.prompts.p_prompt` → `{{ generation.prompts.first.positive }}`,
`preset.speed_profiles.draft.steps` → `get_speed_profile('draft').steps` or
`generation.profile.steps`, `preset.configuration.checkpoint_tags` → a field's own
`filter_tags: "@config:checkpoint_tags"` (see [Configuration (admin-set)](manifest.md#configuration-admin-set)),
`generation.seed`/`generation.quantity` → `form.seed`/`form.quantity`. `path`/`icon`/
`matches`/`regex_search` have no replacement — a live audit found no shipped preset ever used them
(every preset's `icon:` field is a literal name, e.g. `icon: "generation"`).

### Pipe shape and `enabled:`

Minimal pipe shape (`PipeSpec`): `name` (required, the registered pipe name), optional `id`
(referenced by other pipes' `input`), `enabled`, `input` (a list of
`[name, provider_pipe_id, provider_output_var]`), and `configuration`.

`provider_pipe_id` is resolved against the step's own `id:` when it declared one, falling back to
its `name:` otherwise (`src/features/generation/engine.py`) — so a step with no `id:` is addressed
by `name:` from downstream `input` entries. When the provider's output is declared `is_array` but
the consuming input isn't, the engine extracts the **first element** automatically rather than
raising a type mismatch — the reverse (a single-valued output feeding an array input) also passes,
wrapped into a one-element array.

`enabled:` is a **real YAML bool** (`true`/`false`) or an exact `{{ expression }}` that evaluates
to a bool. **Omitted means enabled.** Anything else — a string that isn't exactly one expression
block, or an expression yielding a non-bool — is a build error. The runtime checks `is True`, so
there is no string-comparison trap.

```yaml
- name: "seed_generator"
  id: "seed_generator"
  configuration:
    seed: "{{ form.seed | default(-1) }}"
    quantity: "{{ form.quantity | default(1) }}"

- name: "controlnet_preprocessor/sdxl"
  enabled: "{{ form.enable_controlnet | default(false) }}"
  configuration:
    image: "{{ form.controlnet_1_image }}"     # only rendered when enabled is true
```

**A disabled pipe's `configuration:` is never rendered.** `enabled` resolves first; when it is
`false` the config is skipped entirely. Under strict evaluation this matters: an optional
feature's config may reference fields that only exist when the feature is on (like
`controlnet_1_image` above) without needing `| default(...)` on every line — the references are
simply never evaluated while the gate is off.


> Note: a `cache:` key is still accepted on a pipe by the schema/processor but is a no-op — model reuse
> now goes through `ModelLifecycle` (the `MODELS` built-in service), not per-pipeline cache keys.
> No shipped preset uses `cache:`; don't add it to new presets.

## The `@loop` recipe (repeated LoRA / ControlNet slots)

`@loop` expands a template N times — the idiomatic way to fan a fixed number of LoRA/ControlNet form
slots (`lora_1`, `lora_2`, ...) into a list in the pipeline. It is handled by
`PresetProcessor._process_loop` (`src/features/presets/processor.py`) and works in two places:

1. **Inside a pipeline value** (most common), as a `"@loop":` mapping whose result replaces the value.
2. **As a form field** of `type: "@loop"` whose `configuration` carries the loop config, expanding into
   concrete fields.

Loop config keys used by presets:
- `count` — how many iterations: a native integer, or an exact `{{ expression }}` that
  **evaluates to an int** (e.g. `"{{ preset.vars.num_lora_slots }}"`). Anything else is a build
  error.
- `items` — a list to iterate instead of a count: either an inline YAML list or an **exact
  `{{ expression }}`** that evaluates natively to a list/dict/range (e.g.
  `"{{ form.loras | default([]) }}"`). There is no rendered-string round-trip anymore — a string
  value that isn't exactly one expression block, or an expression yielding anything other than
  list/dict/range, aborts the build. Looping over `[]` emits nothing. Expansion is capped at
  10,000 items.
- `template` — the value evaluated once per iteration (a mapping or a list).
- `when` (optional) — an exact expression evaluated per iteration; iterations where it is falsy
  are skipped. Used to drop empty numbered slots.

Inside `template`/`when` you get a `loop` object: `loop.index` (1-based), `loop.index0`, `loop.first`,
`loop.last`, `loop.length`. With `count`, index the per-slot form fields by concatenating the index
with `~`, e.g. `form['lora_' ~ loop.index]`. With `items`, the current element is exposed as
**`item`** — note the `as:` key does *not* rename it for plain list elements (it only applies when
iterating a dict, whose 2-tuple entries can be unpacked via `as: "key, value"`), so write
`{{ item.model }}` / `{{ item['model'] }}` when looping over a list of mappings.

Two properties of everything a `@loop` template produces:
- **Exact expressions keep their native types.** `weight: "{{ item.strength }}"` yields the float
  `0.8`, not the string `"0.8"` — same rule as everywhere else (see "Exact expressions vs string
  templates"). Only a mixed/string template stringifies.
- **The result is a nested list.** A `@loop` entry inside a list (e.g. one entry of
  `node_manipulations` or `parameters`) is replaced by the list of rendered iterations. Consumers
  that support this flatten it one level (`node_manipulations` in the ComfyUI pipe does); don't use
  `@loop` inside lists whose consumer expects flat entries only (e.g. `field_mappings`).

Real example — a native model loader builds its LoRA list straight from a `lora_picker` field's
list value (`content/presets/marketplace/ZImage/modes/txt2img/pipeline.yml`):

```yaml
- name: "model_loader/z_image"
  enabled: true
  configuration:
    loras:
      "@loop":
        items: "{{ form.loras | default([]) }}"
        template:
          file_path: "{{ item.model }}"
          weight: "{{ item.strength }}"
```

### Step-windowed LoRAs

A LoRA entry may add `step_start` / `step_end` to be active only inside a range of denoise steps.
Both are **1-based and inclusive**, so "on for the first two steps" is `step_start: 1, step_end: 2`;
omit `step_start` to default to `1`, omit `step_end` to stay on through the last step. An entry with
neither key is unwindowed and behaves exactly as it always has.

Some distilled LoRAs require this. `F16/krea2-turbo-sda` states it "must only be active for the
first 2 of the 8 denoise steps, then switched off" — leaving it on for all 8 is a documented quality
collapse, not a mild approximation.

The form side is the `lora_picker` field's `allow_step_window: true` (see above), which turns on a
per-row control writing `item.step_start` / `item.step_end`; the pipeline forwards them:

```yaml
- name: "model_loader/krea2"
  enabled: true
  configuration:
    loras:
      "@loop":
        items: "{{ form.loras | default([]) }}"
        template:
          file_path: "{{ item.model }}"
          weight: "{{ item.strength }}"
          step_start: "{{ item.step_start | default(none) }}"
          step_end: "{{ item.step_end | default(none) }}"
```

A windowed entry is **not** baked into the loaded model: the loader hands it to the generator, whose
sampling loop patches it in at the window's first step and out after its last (and removes it on
error or cancellation, so the shared model cache is never left contaminated). Because the model
cache is keyed on the baked stack only, a windowed LoRA never changes the cache identity.

Only families whose generator runs the native flow-matching sampling loop honour windows — Krea-2
today. A loader that bakes LoRAs at load time **rejects** a windowed entry with an error naming the
family rather than silently applying it for the whole run. `iterate_mode` is disabled automatically
while a window is present, since a warm start resumes on a truncated schedule whose step numbering
would put every window in the wrong place.

For numbered slots (`lora_1`, `lora_2`, ...) use `count` with `~` concatenation, and a `when:` to
drop empty slots:

```yaml
"@loop":
  count: "{{ preset.vars.num_lora_slots }}"
  template: ["model", "{{ form['lora_' ~ loop.index] | default('') }}"]
  when: "{{ form['lora_' ~ loop.index] | default('') != '' }}"
```

### `items:` + `lora_picker` (the recommended idiom for list-valued fields)

A `lora_picker` field submits a ready-made list — e.g. `loras` =
`[{'model': 'models/loras/foo.safetensors', 'strength': 0.8}, {'model': 'bar.safetensors', 'strength': 1.0}]`
— so the pipeline iterates it directly with `items:` instead of probing six numbered slots with
`count` + `when`. Real example — `content/plugins/marketplace/comfyui-backend/presets/QwenImage/modes/txt2img/pipeline.yml` builds
a ComfyUI LoRA node chain (base model `37` → LoRA nodes `76`, `77`, ... → `ModelSamplingAuraFlow` `66`):

```yaml
node_manipulations:
  # Remove the workflow's baked-in LoRA node; the chain is rebuilt from the picker value
  - type: "remove_node"
    node_id: "76"

  # One add_node per selected LoRA, each chained to the previous node
  - "@loop":
      items: "{{ form.loras | default([]) }}"
      template:
        type: "add_node"
        node_id: "{{ 76 + loop.index0 }}"
        node_config:
          inputs:
            lora_name: "{{ item.model | strip_model_dir }}"
            strength_model: "{{ item.strength }}"
            model: ["{{ '37' if loop.first else (75 + loop.index0) | string }}", 0]
          class_type: "LoraLoaderModelOnly"
          _meta:
            title: "LoRA {{ loop.index }}"

  # Reconnect the downstream consumer to the end of the chain (or the base model when empty)
  - type: "update_node_input"
    node_id: "66"
    input_key: "model"
    input_value: ["{{ (75 + form.loras | default([]) | length) | string if form.loras | default([]) else '37' }}", 0]
```

With the two-item value above this renders nodes `76` (foo, model ← `["37", 0]`) and `77` (bar,
model ← `["76", 0]`) and points `66.inputs.model` at `["77", 0]`; with `loras: []` the loop emits
nothing and `66` is wired straight to `["37", 0]`. No `when:` is needed — the picker only submits
selected rows — and remember: the per-item variable is `item` (never renamed by `as:` for list
elements), and each exact-expression value keeps its native type (`strength_model` is a float;
the node-id references above are piped through `| string` deliberately, because ComfyUI link
references are string node-ids).

For flat parameter lists the same idiom is just:

```yaml
"@loop":
  items: "{{ form.loras | default([]) }}"
  template: ["model", "{{ item.model }}"]
```


## Speed profiles in `pipeline.yml`

Full reference: [preset.yml → Speed profiles](manifest.md#speed-profiles). Two equivalent idioms
read a `speed_profiles:` entry from `pipeline.yml`; prefer the second unless you need to look up a
profile OTHER than the one the current request selected:

```yaml
# Idiom 1: explicit lookup by name (loud error if the name doesn't exist)
steps: "{{ get_speed_profile(form.speed_profile | default('standard'))['steps'] }}"

# Idiom 2: generation.profile - the same lookup, already resolved once per build
steps: "{{ generation.profile.steps }}"
```

**Precedence is profile-as-baseline, form-field-overrides**: read either idiom as "the profile
supplies the default", and if the form also exposes a real field for the same knob, that field's
own value is what reaches the pipe — the profile only fills in what the form didn't ask the user
for:

```yaml
steps: "{{ form.steps | default(generation.profile.steps) }}"
```

A user who never touches the advanced `steps` field still gets the value for whichever profile is
selected; a user who does override it always wins. See
[preset.yml → Precedence: profile as baseline, form fields override](manifest.md#precedence-profile-as-baseline-form-fields-override)
for the full rule and worked example.

## The standard native chain

Every shipped `engine: native` txt2img/txt2vid preset wires the same eight-pipe shape — only the
`model_loader/<family>` and `generator/<family>` pair (and, for SDXL, `checkpoint_loader/sdxl` in
place of a `model_loader`) actually changes between families:

```
model_loader/<family> -> prompt_encoder -> seed_generator -> dynamic_prompts_renderer
  -> from_iotype -> param_emitter -> generator/<family> -> gallery
```

- **`model_loader/<family>`** loads the checkpoint/diffusion-model + text encoder + VAE (+ LoRAs)
  and outputs `model` (a bundle the generator consumes) and `text_encoder` (a `ClipTextEncoder`
  ABC the `prompt_encoder` pipe consumes — see [pipelines and the pipe contract](../pipes.md)).
- **`prompt_encoder`** turns the prompt (and this pipe's own `text_encoder` input) into
  `conditioning`. Shared across every family — it never changes per model.
- **`seed_generator`** emits `seed`; **`dynamic_prompts_renderer`** surfaces the per-image expanded
  prompt pairs as a pipe artifact (provenance, not consumed downstream);
  **`from_iotype`**/**`param_emitter`** convert the seed and record every generation parameter for
  History. All four are shared, unchanged pipes — copy them verbatim from any shipped preset.
- **`generator/<family>`** takes `model` + `conditioning` (+ `seed`) and produces `image`.
- **`gallery`** saves the output. Every shipped preset's gallery pipe omits `configuration:`
  entirely — there is no `mode: "save"` key; the pipe always saves, and `scripts/preset_new.py`'s
  scaffold no longer emits that dead key either.

`python scripts/preset_new.py <Model>/<variant> --family <family>` (see the
[Preset Authoring Guide](../presets.md#quick-start)) scaffolds this exact chain, discovering the
real `model_loader/<family>` and `generator/<family>` (or `checkpoint_loader/<family>`) pipe names
from the pipe catalog rather than guessing them — the mistake the
[tutorial](tutorial.md) opens with.

### Per-family loader/generator pipes

Generated by hand from [Pipes Reference → Families](../pipes.md#families) — regenerate this table
whenever a family's loader/generator pipe is renamed or a new family ships. `python
scripts/preset_new.py --family <name>` discovers the same pairing live from the pipe catalog, so
this table and the scaffolder can never disagree for long.

| Family | Loader pipe | Generator pipe(s) | Modality |
|---|---|---|---|
| `anima` | `model_loader/anima` | `generator/anima` | image |
| `flux` | `model_loader/flux` | `generator/flux` | image |
| `krea2` | `model_loader/krea2` | `generator/krea2` | image |
| `ltx` | `model_loader/ltx` | `generator/txt2vid_ltx`, `generator/video_ltx`, `generator/dfr_video_ltx` | video |
| `maya` | `model_loader/maya` | `generator/maya` | image |
| `minimax_h3` | `model_loader/minimax_h3` | `generator/video_minimax_h3` | video |
| `minimax_music3` | `model_loader/minimax_music3` | `generator/audio_minimax_music3` | audio |
| `qwen` | `model_loader/qwen` | `generator/qwen` | image |
| `sdxl` | `checkpoint_loader/sdxl` | `generator/sdxl` | image |
| `seedvr2` | `model_loader/seedvr2` | `generator/seedvr2` | image/video (upscale) |
| `trellis2` | `model_loader/trellis2` | `generator/trellis2` | 3d |
| `wan22` | `model_loader/wan22` | `generator/txt2vid_wan22`, `generator/img2vid_wan22`, `generator/chain_video_wan22` | video |
| `z_image` | `model_loader/z_image` | `generator/z_image` | image |

A family with more than one generator (video families, mostly) picks the mode-appropriate one per
`modes/<mode>/pipeline.yml` — `--family` scaffolds the family's first/primary generator for a
`txt2img`/`txt2vid`-shaped mode; wire an alternate generator by hand for `img2vid`/chain modes. See
each family's own page under [Models](../models/) for what the loader's checkpoint(s) actually are.
