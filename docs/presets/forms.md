---
title: Forms
category: Presets / Models
category_order: 70
order: 16
---

# Forms

`form.yml`, tabs, variants, field shapes and typed defaults, external option files,
reactions, and the `bind_form`/422 validation contract. The live, always-current field-type
list is [Preset Context Cheat Sheet → Built-in field types](../preset-context.md#built-in-field-types)
and Help → Documentation → Field Types in the running app; the `model_type` values a `model`
field's `configuration.model_type` accepts are the generated
[`model_type` values table](../preset-context.md#model_type-values). For the `@loop` recipe
that turns a `lora_picker` field's list value into pipeline config, see
[Pipelines → The `@loop` recipe](pipelines.md#the-loop-recipe-repeated-lora--controlnet-slots).

## Modes and the form root

Each mode is a self-contained generation flow (e.g. `txt2img`, `img2img`, `inpaint`, `txt2vid`).

- `modes/<mode>/pipeline.yml` is the ordered pipe list (see [Template contexts](pipelines.md#template-contexts) in Pipelines).
- `modes/<mode>/form.yml` is the mode's DEFAULT form root. It is usually a single `tabs` container whose
  tab bodies are pulled in from `tabs/*.yml`. Additional form variants live under
  `modes/<mode>/variants/<name>/form.yml`. The form's `name:` is what a request's `form_name`
  selects (and what `request.form_name` reports in the pipeline context).

`form.yml` (root, using a tabs container — the `children` string is a Jinja path to the tab body):

```yaml
name: "custom"
fields:
  - type: "tabs"
    children:
      - type: "tab"
        label: "Generation"
        configuration:
          icon: "{{ icon('model') }}"
        children: "{{ paths.preset }}/modes/txt2img/tabs/generation.yml"
      - type: "tab"
        label: "LoRA"
        children: "{{ paths.preset }}/modes/txt2img/tabs/lora.yml"
```

A `tabs/*.yml` file holds the fields for that tab under a `fields:` key. `children:` accepts both
`{{ paths.preset }}` (the preset's own directory) and `{{ paths._shared }}` (the shared tree, the same
root option files use), so a fragment reused across presets can live under `content/presets/_shared/`,
e.g. `children: "{{ paths._shared }}/tabs/advanced.yml"`. `preset_lint` reports an error when the
fragment a `children:` path names does not exist.


## Variants

A mode can ship more than one form — the default `modes/<mode>/form.yml` plus any number of
`modes/<mode>/variants/<name>/form.yml` — each is a **variant** of that mode (e.g. a `custom` form
with every knob exposed, and a `quick` form with just prompt + a speed profile picker). A form's
identity is `form.yml`'s own `name:` field, falling back to the variant directory name
(`default` for `modes/<mode>/form.yml`, otherwise the `variants/<name>/` name) when `name:` is
absent — that name is what a request's `form_name` selects and what binds the submitted values
into the pipeline's `form` context.

`form.yml` accepts optional **display metadata**, read by `operations.get_available_modes` (`GET
/api/presets/{id}/modes`) to describe each variant to the frontend:

```yaml
name: "quick"
label: "Quick"
description: "Prompt and a speed profile - everything else uses sane defaults."
examples:
  - "public/examples/quick-1.png"   # must live under public/, like media.gallery
default: false
order: 1
fields:
  - name: "prompt"
    type: "textbox"
    # ...
```

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `label` | no | string | Display name; falls back to the variant's own `name` (title-cased) when absent. |
| `description` | no | string (markdown) | Shown in the variant picker. |
| `examples` | no | list of string | Paths, same rules as `media.*` (relative, under `public/`, allowed extension - schema-validated). `scripts/preset_lint.py` also checks each one exists on disk. |
| `default` | no | bool | Default `false`. See "Default resolution" below. |
| `order` | no | int | Default `0`. Sort key alongside `name` (see below). |

### Discovery and the `GET /api/presets/{id}/modes` shape

`operations.get_available_modes` always returns a `variants` list per mode, even
when the mode has exactly one form — this is a fixed contract with the frontend, not an
optimization to special-case away:

```json
{
  "preset_id": "...",
  "modes": [
    {
      "name": "txt2img",
      "label": "Txt2Img",
      "variants": [
        {
          "name": "custom",
          "label": "Custom",
          "description": null,
          "examples": [],
          "default": true,
          "order": 0
        },
        {
          "name": "quick",
          "label": "Quick",
          "description": "Prompt and a speed profile - everything else uses sane defaults.",
          "examples": ["public/examples/quick-1.png"],
          "default": false,
          "order": 1
        }
      ],
      "source_plugin": null
    },
    {
      "name": "img2img",
      "label": "Img2Img",
      "variants": [{ "name": "custom", "label": "Custom", "description": null, "examples": [], "default": true, "order": 0 }],
      "source_plugin": "some-plugin"
    }
  ],
  "default_mode": "txt2img"
}
```

Variants are always sorted by `(order, name)`. `default_mode` is unchanged by any of this - it
picks the mode, not the variant. `source_plugin` is `null` for a mode the preset declares itself,
or the contributing plugin's id for one merged in via `preset_modes:` (see "Plugin-contributed
modes" above).

### Default resolution

Exactly one variant per mode is the default, resolved the same way everywhere it matters
(`operations.get_available_modes`'s `variants[].default`, `get_form_schema`'s form-name fallback, and
`PresetProcessor`'s `form_name` context variable - see below):

1. The first form (after sorting by `(order, name)`) with `default: true` wins.
2. If no form declares `default: true`, the first form after sorting is the default.

A mode with more than one form marked `default: true` is a lint **error**
(`scripts/preset_lint.py`) - a mode has exactly one default variant.

### Selecting a variant

`GET /api/presets/{id}/form?mode=txt2img&form_name=quick` selects a variant explicitly; omitting
`form_name` resolves the mode's default variant (above) - this is unchanged from the existing
`form_name` parameter, now with a well-defined default instead of "whichever form loaded first".

A generation request also carries `form_name` (`GenerationRequest.form_name`, optional) so a
submission from a non-default variant renders through the correct one. It flows to
`pipeline.yml` as `request.form_name`:

```yaml
- name: "generator/sdxl"
  enabled: true
  configuration:
    # e.g. skip an expensive stage entirely for the "quick" variant
    detail_pass: "{{ request.form_name != 'quick' }}"
```

When a request omits `form_name`, `PresetProcessor` resolves it to the mode's default variant -
the same rule `get_form_schema` uses, so `{{ request.form_name }}` is never empty/`None` for a
mode that has at least one form.

## Form fields

Field `type` is an **opaque, registry-validated string** — the preset schema does not enumerate types.
The authoritative list is the field-type registry (`src/platform/plugins/field_types.py` populated by
`src/features/fields/builtin.py`) and is served at runtime from **`GET /api/fields/types`**. Plugins add
their own types via the manifest `field_types:` section, so query the endpoint for the live set rather
than assuming a fixed list.

The built-in types (from `src/features/fields/builtin.py`; the generated, always-current list is
in the [Preset Context Cheat Sheet](../preset-context.md#built-in-field-types)):

| Category | Types |
|----------|-------|
| Text | `string`, `textbox` |
| Numeric | `number`, `integer`, `slider`, `stepper`, `seed`, `resolution` |
| Boolean | `boolean`, `checkbox` |
| Options-backed | `select`, `checkbox_group`, `tags`, `model` (alias `models`), `lora_picker`, `sampler`, `schedule`, `cloud_options` |
| Media | `image`, `video`, `audio`, `media`, `file` |
| Widgets | `carousel`, `llm`, `alert`, `markdown`, `header`, `section`, `gate`, `prompt_timeline`, `camera_shot` |
| Layout containers | `tabs`, `tab`, `row`, `group`, `accordion` |

Common field shapes (all from real presets under `content/presets/marketplace/`):

Model picker (`type: "model"` — the canonical name; `model_type` selects the model directory — see
the generated [`model_type` values table](../preset-context.md#model_type-values) for the full set):

```yaml
- name: "model"
  type: "model"
  label: "Model"
  ai_hint: "The base checkpoint. Realistic models produce photorealistic results..."
  configuration:
    model_type: "checkpoint"        # checkpoint | lora | vae | text_encoder | upscaler | ...
    allow_info_modal: true
    placeholder: "Select model..."
```

`recommendations` (a `model` field's list of downloadable models offered when the picker is empty)
supports two entry shapes:

```yaml
configuration:
  model_type: "upscaler"
  filter_tags: "@config:upscaler_tags"    # see "Configuration (admin-set)" above
  recommendations:
    - name: "RealESRGAN 4x+ Anime"         # provider-less (today's shape) - always shown
      link: "https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x4plus_anime_6B.pth"
      size: "17.9 MB"
      sha256: "f872d837d3c90ed2e05227bed711af5671a6fd1c9f7d7e91c911a61f155e99da"
      description: "Optimized for anime-style images"
    - name: "Some Civitai Checkpoint"       # provider-backed - dropped if the provider isn't
      provider: "civitai"                  # installed/enabled (see docs/providers.md)
      ref: '{"model_id": "12345", "version_id": "67890"}'
      size: "6.5 GB"
      description: "..."
    - name: "FLUX.1-dev"                     # provider-backed, huggingface-provider shape
      provider: "huggingface"
      ref: '{"repo": "black-forest-labs/FLUX.1-dev", "file": "flux1-dev.safetensors", "revision": "main"}'
      size: "23.8 GB"
      description: "..."
```

Provider-backed entries are validated against the live provider registry at form-schema
serialization time (`src/features/fields/model.py`) — an entry naming an absent/disabled provider is
silently dropped, never sent to the frontend. `ref` is an **opaque, provider-native string** (core
does not validate its inner shape); when a download is requested, core reads it as a set of known
conventions, not a single contract - `ModelController._parse_ref` tries, in order: (1) an
already-final `{"provider_model_id", "provider_version_id"}` pair, passed straight through; (2)
civitai's natural `{"model_id", "version_id"}`, mapped directly; (3) huggingface's natural
`{"repo", "file", "revision"?}`, mapped to `provider_model_id=repo`,
`provider_version_id="{revision or 'main'}@{file}"` (the one HF-specific shim - see
`content/plugins/marketplace/huggingface-provider/provider/huggingface_provider.py`'s `get_download_url`
docstring for why its version id is shaped that way); (4) otherwise the whole string is used as
`provider_model_id` with no version. Every surviving entry gains `installed: bool` (a best-effort
filename match against the local model library, documented in `Model._is_recommendation_installed`).

Downloading a recommendation: `POST /api/models/downloads` body `{name, model_type, provider?,
ref?, link?, sha256?}` → `{"download_id": "..."}` (admin-only); poll
`GET /api/models/downloads/{download_id}` → `{"status": "pending|running|completed|failed",
"progress": <0..1 or null>, "error": <string|null>}` — collapsed from the download queue's
richer internal status vocabulary (`downloading`/`paused` → `running`, `cancelled` → `failed`) so
this endpoint's contract stays small. For a provider-backed request, the
endpoint itself resolves the URL first via the provider registry's `get_download_url(provider_model_id,
provider_version_id)` (the download worker only ever consumes a plain URL + `provider_id`
for its auth step - it has no `{provider, ref}` resolution of its own), then hands
the resolved URL + `provider_id` to the core download queue (`src/features/downloads/`) so the
worker's provider-authenticated download still applies.

Mixed media list (`type: "media"` — one multi-item field that accepts several kinds, from
MiniMax-H3's `refs` mode):

```yaml
- name: "references"
  type: "media"
  label: "References"
  merge_from: ["reference_videos", "reference_audios"]
  configuration:
    multi: true
    accepted_types: ["image", "video", "audio"]
    max_items_by_kind: { image: 9, video: 3, audio: 3 }
```

`accepted_types` lists the kinds an item may be. `max_items_by_kind` caps each kind on its own
(a kind left out has no cap of its own); `max_items` stays the cap on the whole list. Each item is
stored with its kind in `type` (`image`/`video`/`audio`); an item without one is classified by its
file extension. The list keeps the order the user gave it. A pipeline that needs one kind reads a
filtered view with the `media_of_kind` filter, for example
`items: "{{ form.references | default([], true) | media_of_kind('video') }}"`, which also keeps
that kind's numbering for prompt resources (see
[Prompt resources](manifest.md#prompt-resources)). `merge_from` is how this field took over the
two retired single-kind fields from saved sessions and generations.

LoRA picker (`type: "lora_picker"` — a repeatable list of model + strength rows, replacing the old
pattern of six hand-written `lora_N` / `lora_N_strength` field pairs):

```yaml
- name: "loras"
  type: "lora_picker"
  label: "LoRAs"
  configuration:
    model_type: "lora"            # model directory to pick from (default: "lora")
    placeholder: "Select a LoRA..."
    allow_info_modal: true        # per-row model info modal
    show_triggers: true           # show the LoRA's trigger words
    strength_min: -2.0            # strength slider bounds/step (defaults: -2.0 / 2.0 / 0.1)
    strength_max: 2.0
    strength_step: 0.1
    strength_default: 1.0         # strength for newly added rows (default: 1.0)
    max_items: 6                  # maximum number of rows (default: 6)
    row_fields:                    # per-row configuration fields (default: none) - see below
      - name: "step_start"
        type: "number"
        label: "From step"
        default: null
  default:                        # optional default rows (same shape as the runtime value)
    - { model: "some_lora.safetensors", strength: 1.0 }
```

The submitted value is a **list of `{model, strength}` mappings** containing only rows with a
selected model — it may be empty (`[]`). Consume it in `pipeline.yml` with an `items:`-based
`@loop` (see "The `@loop` recipe" below); no `when:` filtering is needed because unselected rows
are never submitted.

`row_fields` declares extra per-row controls, shown behind a Configuration gear on each row,
using any registered field type: `{name, type, label, default, description, configuration}` -
the same shape as a top-level field declaration. A submitted row's value lands under `name`
(falling back to `default`); a key with no matching declaration is dropped even if it somehow
arrives on the wire. Only correct for a row key the target model family's loader/generator
actually reads — Krea-2's `step_start`/`step_end` (see "Step-windowed LoRAs" below) and
MiniMax-H3's `audio` — a family that ignores the key rejects a non-default value outright rather
than silently applying it.

Camera-shot autocomplete (`type: "camera_shot"` — a display-only viewfinder picker that inserts or
copies a shot-describing phrase into the prompt, for when you know the shot you want but forget how
to word it):

```yaml
- name: "camera"
  type: "camera_shot"
  label: "Camera & Shot"
  configuration:
    categories: [angle, distance, orientation]   # which categories to show, in order.
                                                  # Optional — defaults to the image categories
                                                  # (angle, distance, orientation). Video presets
                                                  # add `motion`.
    vocabulary:                                   # optional per-preset phrase overrides, keyed by
                                                  # canonical shot key. Unset keys use the built-in
      overhead: "from the ceiling, top-down"      # default phrase. Curate these for the model the
      over_shoulder: "over-the-shoulder shot"     # preset targets — that is the per-model story.
```

The canonical taxonomy (categories, shot keys, and default phrases) is built in
(`src/features/fields/camera_shot_taxonomy.py`). The field **stores no form value** — it is a helper
surface, so it needs no `pipeline.yml` wiring and never appears in generation params. `preset_lint`
warns on a `vocabulary`/`categories` key that isn't in the taxonomy (e.g. a misspelled shot key).
The picker offers a tile grid (default) and a 3D orbit view (drag/scroll/arrow keys) — both drive
the same selection and compose a combined phrase; the 3D pose quantizes to the nearest canonical
shots. See `content/presets/marketplace/SDXL/modes/txt2img/tabs/camera.yml` for a minimal working example and
`content/presets/marketplace/Krea2/modes/txt2img/tabs/camera.yml` for a fully-curated vocabulary.

Categorized tag picker (`type: "tags"` — a `{category: [tags]}` structured value, e.g. YuE2's Style
tags field: language/genre/tempo pick one, instrumentation/voice/mood/more layer on several):

```yaml
- name: "style"
  type: "tags"
  label: "Style tags"
  required: true
  configuration:
    separator: ", "                            # default ", "
    allow_custom: true                          # field-level default; a category's own
                                                 # allow_custom overrides it
    max_tags: 24                                # optional, across every category combined
    categories: "@config:style_categories"      # or an inline list:
    #  - { key: "language", label: "Language", multi: false, tags: ["English", "Japanese"] }
    #  - { key: "instrumental", label: "Instr.", multi: true, tags: ["acoustic piano"] }
    #  - { key: "more", label: "More", multi: true, tags: [] }   # free tail — see below
```

The wire value a client submits is either the structured map (`{"language": ["English"], ...}`) or
a plain separator-joined string — the same shape older sessions/an LLM-authored `form_data`/a
recipe's `smoke.form` already send for a free-text tags field. A string is split back into
categories: each token is matched (case-insensitively) against the first category whose `tags`
list contains it, respecting `multi: false` (a single-value category keeps its first match; any
further token that would also match it overflows to the tail instead), and anything that matches
no category's list lands in the **last declared category** — that's the "free tail" a `more`
category conventionally provides. `bind_form` binds two values: `form.<name>` is every category's
tags, in declared order, joined by `separator` (blank/duplicate tags dropped) — what `pipeline.yml`
reads unchanged, `{{ form.style }}` — and `form.<name>_tags` is the structured map, for a pipe that
wants per-category access.

`required` means at least one non-blank tag anywhere, not that every category is filled.
`allow_custom: false` (field-level, or per-category) rejects a tag outside that category's declared
list. `categories` accepts the same `"@config:<key>"` indirection as a `model` field's
`filter_tags` (see "Configuration (admin-set)" in `manifest.md`) — the admin-editable knob there is
declared with `type: "tag_categories"`, which (unlike `model_tags`) also accepts a shipped
`default:` so the field renders a real vocabulary before any admin ever touches it.

Slider (with an optional `reactions:` block — see "Reactions"):

```yaml
- name: "cfg"
  type: "slider"
  label: "CFG"
  default: 4
  configuration: { min: 1, max: 30, step: 0.5 }
```

Seed (note the **native** number — a quoted `"-1"` is a schema error, see "Field `default:` typing"):

```yaml
- name: "seed"
  type: "seed"
  label: "Seed"
  default: -1
```

Select with inline options:

```yaml
- name: "voice_gender"
  type: "select"
  label: "Voice Gender"
  default: "male"
  configuration:
    options:
      - { value: "male", label: "Male" }
      - { value: "female", label: "Female" }
```

Sampler / schedule pickers (`type: "sampler"` / `type: "schedule"`) — dropdowns whose options come
from the sampler/schedule registries (`src/platform/plugins/sampling.py`), not a
preset-authored list. `configuration.family` narrows the catalog to one model family (`anima`,
`flux`, `krea2`, `ltx`, `minimax_h3`, `qwen_image`, `wan`, `z_image`, `seedvr2`, `minimax_music3`,
or another family a plugin registers); omitted/empty matches only family-agnostic entries.
`configuration.include`/`exclude` narrow further — `include` also fixes display order:

```yaml
- type: "row"
  children:
    - name: "sampler"
      type: "sampler"
      label: "Sampler"
      default: "euler"
      configuration:
        family: "flux"
        exclude: ["lcm"]
    - name: "schedule"
      type: "schedule"
      label: "Schedule"
      default: "simple"
      configuration:
        family: "flux"
```

Resolution loading grouped option files (note `{{ paths._shared }}`):

```yaml
- name: "resolution"
  type: "resolution"
  label: "Resolution"
  configuration:
    files:
      - { path: "{{ paths._shared }}/resolutions/sdxl.yml", group: "SDXL" }
      - { path: "{{ paths._shared }}/resolutions/social_media.yml", group: "Social Media" }
  default: "896x1152"
```

Layout containers (`row`, `group`, `header`) nest fields via `children:`:

```yaml
- type: "row"
  children:
    - name: "steps"
      type: "slider"
      label: "Steps"
      default: 20
      configuration: { min: 1, max: 50, step: 1 }
    - name: "cfg"
      type: "slider"
      label: "CFG"
      default: 4
      configuration: { min: 1, max: 30, step: 0.5 }
```

Section (`type: "section"`) is a mono-uppercase-title divider with a trailing hairline rule. Its
shape decides its behavior — no separate `foldable` flag: without `children:` it's a flat divider (no
fold, no chevron, no click target); with `children:` it becomes a foldable container like
`group`/`accordion`, but lighter — the title row is the toggle, and `configuration.collapsed: true`
starts it folded (ignored on a childless section, which has no fold state). `configuration.collapsed`
is only the *initial* state: once a user folds or unfolds a section, the frontend remembers their
choice in their session (scoped per preset + mode), and that remembered state wins on every later
visit — `collapsed` only applies the first time a given section is seen:

```yaml
- type: "section"
  label: "Sampling"

- type: "section"
  label: "Advanced"
  configuration:
    collapsed: true
  children:
    - name: "strength"
      type: "slider"
      label: "Strength"
      configuration: { min: 0, max: 1, step: 0.05 }
```

`configuration.badge` (trailing meta text) and `configuration.tooltip`/`configuration.experimental`
apply to both shapes.

Gate (`type: "gate"`) is a card that owns a boolean **and** the fields that boolean governs — unlike
`section`/`group`/`accordion` it keeps its `name` and carries a real value. Off, the card shows the
label, an optional `Experimental` chip, a static one-line `configuration.summary`, and the toggle;
`children:` are not rendered. On, the card expands and renders `children:` inline. `summary` is a
plain string, not a template — it does not interpolate field values:

```yaml
- name: "enhance"
  type: "gate"
  label: "Enhance"
  default: false
  configuration:
    experimental: true
    summary: "Second pass at 2048×2048 · Balanced (2 steps)"
  children:
    - name: "enhance_resolution"
      type: "resolution"
    - name: "enhance_detail"
      type: "select"
```

A gate replaces the older pattern of a sibling checkbox plus a `reactions: [{ when: { field:
"<checkbox>", equals: false }, then: { set_disabled: true } }]` block repeated on every field it
gates — the gate owns that dependency once, instead of each child restating it.

A row child's `width:` sets its grid-`fr` weight, either a number or an `"a/b"` fraction string —
here `steps` takes 3 parts of the row to `cfg`'s 2 (equivalent to `width: 3` / `width: 2`):

```yaml
- type: "row"
  children:
    - name: "steps"
      type: "slider"
      label: "Steps"
      width: "3/5"
    - name: "cfg"
      type: "slider"
      label: "CFG"
      width: "2/5"
```

`full_width: true` stretches a field to fill its column instead of hugging its content — the
`stepper` control is the one built-in type this currently matters for:

```yaml
- type: "row"
  children:
    - name: "seed"
      type: "seed"
      width: "4/5"
    - name: "quantity"
      type: "stepper"
      width: "1/5"
      full_width: true
```

Field keys understood by the schema (`FieldSpec`): `type` (required), `name`, `label`, `description`,
`ai_hint`, `configuration`, `required`, `default`, `when`, `input`, `save_into`
(`session`|`settings`), `interactive`, `container`, `visible`, `reactions`, `listeners`,
`children` (a list of nested fields, or a `{{ paths.preset }}/...` or `{{ paths._shared }}/...` path string to an external file),
`audience`, `width`, `full_width`, `hidden_when_video_director`, `capability`, `formula`, `merge_from`. The schema is `extra="forbid"` — the
removed `value:` initializer key is a load error.

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `audience` | no | `"simple"` \| `"advanced"` | Default `"simple"`. Lets the frontend hide `"advanced"` fields behind a toggle, without a separate form/mode. Applies to every field, including nested `children` (tab bodies, `@loop`-expanded rows) — each is serialized independently, so a child's own `audience:` is what's read, not its parent's. |
| `formula` | no | string \| `false` | The formula group this field belongs to, an id from `formulas.groups` in `preset.yml`. On a container it applies to every descendant without its own `formula:`; `false` opts a field out. Emitted to the frontend only when set. See [Formula groups](#formula-groups). |
| `width` | no | number \| `"a/b"` string | A field's fractional share of the row it sits in, read by the frontend as a CSS grid `fr` weight for a `type: "row"` container's child. Either a positive number used directly as the weight (`width: 2`) or a string fraction of positive numbers (`width: "3/5"`). Absent/`null` takes the default weight. Emitted to the frontend exactly as authored (a string stays a string) — the frontend does its own parsing. |
| `full_width` | no | bool | Default `false`. Stretch the field to fill its column/track instead of hugging its content. Has no visible effect on field types that already fill their column (most of them) — today it only matters for controls that hug their content, such as `stepper`. Emitted to the frontend only when `true`. |
| `hidden_when_video_director` | no | bool | Default `false`. Hides this field in the rendered form whenever the Video Director editor is active for the current preset mode (`vars.video_director.preset_modes`) — for a field whose value the director's own document overrides once attached, but that still needs to render for a mode usable outside the director too. Rendering-only, same contract as `audience`: the value/default stays in `formData` and still submits. Emitted to the frontend only when `true`. |
| `merge_from` | no | list of strings | Retired field names this field absorbs from saved form data. When any listed key is present in a submission or a hydrated session, the field's value becomes its own list followed by each listed key's list, in the listed order, and the listed keys are dropped; the merged list then passes the field's normal validation. Untouched when none of the listed keys is present. Entries must be non-empty, unique and not the field's own name. Emitted to the frontend only when set. Prompt markers that name a listed key (`@[old_key:item]`, in form values, prompts, prompt segments and Video Director shot prompts) are rewritten to the field's own name, item key unchanged. |

### Formula groups

A **formula** is a saved subset of one mode's settings that a user reapplies in another session.
The preset decides which fields a formula may hold: `preset.yml` declares the groups
(see [preset.yml Reference → Formulas](manifest.md#formulas)), and the form files say which group
each field belongs to with `formula:`.

```yaml
fields:
  - name: "speed_profile"
    type: "select"
    formula: "speed"
  - type: "section"
    label: "Sampling"
    formula: "speed"
    children:
      - name: "steps"
        type: "slider"
      - name: "manual_sigmas"
        type: "textbox"
        formula: false
  - type: "section"
    label: "Models"
    formula: "models"
    children:
      - name: "model"
        type: "model"
```

- `formula:` goes on a field or on a container (`tabs`, `tab`, `section`, `row`, `group`,
  `accordion`, `gate`). A container's value applies to every descendant that has no `formula:`
  of its own; `formula: false` opts a descendant out.
- A field with no `formula:` and no annotated ancestor is in no group, so a formula never saves
  or changes it. Seeds, quantities, prompts and media stay out this way.
- A `gate` is a field: its own boolean joins the group together with its children.
- Annotate a shared tab fragment once: every mode that includes it offers the same groups.
  Plugin-contributed modes annotate their own form files against the target preset's catalog.
- Input and display types (`image`, `video`, `audio`, `media`, `file`, `prompt_timeline`,
  `camera_shot`, `seed`, `alert`, `markdown`, `header`) can never be in a group.
- A field whose reaction is driven by another field (`set_value`, `set_disabled`,
  `update_options`, `update_validation`) must share that field's group, so applying a formula
  never fights the reaction engine. Reactions that only set visibility or narrow a model
  picker's `set_filter_tags` are exempt, so a speed profile that filters the checkpoint list
  can stay in `speed` while the picker stays in `models`.

`GET /api/presets/{id}/form?mode=&form_name=` adds the resolved groups for that mode and variant,
with inheritance applied, in catalog order:

```json
"formulas": {"groups": [{"id": "speed", "label": "Speed and sampling", "description": null,
  "preselect": true, "fields": ["speed_profile", "steps"]}]}
```

The block is left out when the mode resolves to no groups, and the Generate page then shows no
Formulas control. `python scripts/preset_lint.py --formulas <preset>` prints the same groups for
every mode and variant.

### Field `default:` typing

`default:` is the **one** initializer key (the old `value:` is gone), and it must be a **native
YAML value of the field's type** — validated at preset load by `FieldSpec`
(`src/features/presets/schema.py`, `_validate_typed_default`):

- `slider` / `number` / `seed` → a real number (`default: 30`, `default: -1`; `"30"` is an error);
- `integer` → a real int;
- `checkbox` / `boolean` → a real bool (`default: false`; `"false"` is an error);
- `select` → a scalar; `string` / `textbox` → a string;
- **A field's `default:` is never Jinja-rendered** — a `default:` containing `{{` is a schema
  error. Make form fields dynamic with `reactions:`, not templates.

Defaults pass through to the frontend and to `bind_form` **as-is**, including falsy values
(`false`, `0`, `""`, `[]`) — there is no truthiness collapse.

This is narrower than "form definitions never render Jinja": a **container** field's
(`tabs`/`tab`/`row`/`group`/`accordion`) `configuration:` values ARE Jinja-rendered, against a
`{paths: {preset}}`-only context (`Container._process_configuration_templates`,
`src/features/fields/container.py:42`) — the same idiom `{{ icon('...') }}` in a tab's `label`
configuration relies on. `default:` is the one form-definition value that is never templated;
container `configuration:` values are the (narrow) exception.


## External option files

Option lists (camera angles, resolutions, samplers, ...) live in YAML files instead of being inlined,
so they can be shared and edited centrally. Two locations:

- **Preset-local:** `files/form/*.yml` inside the preset, referenced with `{{ paths.preset }}/files/form/...`.
- **Shared:** `content/presets/_shared/**` referenced with `{{ paths._shared }}/...` (e.g.
  `{{ paths._shared }}/resolutions/sdxl.yml`, `{{ paths._shared }}/comfyui/form/samplers/all.yml`).
  Use `_shared` for vocabulary reused across presets (resolutions, ComfyUI samplers/schedulers) —
  see the generated [`content/presets/_shared/**` option files listing](../preset-context.md#contentpresets_shared-option-files)
  for every file and its first entries.

`{{ paths._shared }}` is defined in **field option-file path** templates — the `file:` / `files:` /
`phrasebook_source` values of `select`/`resolution` fields, which are rendered separately when options
are loaded (`src/features/fields/select.py`, `resolution.py`, whose context is exactly
`{paths: {preset, _shared}}`) — and in form `children:` paths. See "Template contexts" below for the
full picture — `_shared` is **not** available in `pipeline.yml`.

An option file is a flat list of `{value, label}` (extra keys are allowed and used by some widgets):

```yaml
# files/form/angles.yml
- { label: "Dutch angle", value: "dutch angle" }
- { label: "From above",  value: "from above" }
- { label: "From below",  value: "from below" }
```

```yaml
# content/presets/_shared/resolutions/sdxl.yml
- { value: "1024x1024", ratio: [1, 1], description: "Square" }
- { value: "896x1152",  ratio: [7, 9], description: "Portrait" }
- { value: "1216x832",  ratio: [19, 13], description: "Wide Landscape" }
```

Reference them from a field with `configuration.file: { path: ... }` (single),
`configuration.files: [{ path, group }]` (grouped), or `configuration.phrasebook_source: "..."`.
Literal (non-Jinja) paths are checked for existence by the linter.


## Reactions (conditional field behavior)

Reactions make fields respond to other fields' values (show/hide, set values, swap options). The form
is drawn by the frontend engine (`frontend/src/lib/form/reactions.ts`); the backend validates their
shape at preset-load time via `ReactionSpec`/`ConditionSpec`/`ActionSpec` in schema.py, and `bind_form`
evaluates the same `when` conditions on a submission for the three actions that bind server-side:
`set_value`, `update_validation` and `set_visibility` (see below). A field carries a list under
`reactions:`:

```yaml
reactions:
  - when: { field: "generation_preset", in: ["gp_sdxl_anime", "gp_illustrious_anime"] }
    then: { set_value: 5 }
  - when: { field: "generation_preset", in: ["gp_sdxl_realistic"] }
    then: { set_value: 4 }
```

**`when`** is one of:
- a single condition — sugar form `{ field: <name>, <operator>: <value> }` or explicit form
  `{ field: <name>, operator: <op>, value: <value> }`;
- a list of conditions — implicit **AND** across all of them;
- a logical group — `{ logic: "AND" | "OR", conditions: [ ... ] }`.

**`then`** (an `Action`) sets at least one of: `set_visibility` (bool), `set_value` (any),
`set_disabled` (bool), `update_options` (`[{label, value}]`), `update_validation` (object),
`set_filter_tags` (a `model`/`lora_picker` field's `filter_tags` — see below).

`set_filter_tags` is the one action resolved server-side rather than applied verbatim: like a
field's own static `filter_tags:`, its value is either a literal tag-ID list or `"@config:<key>"`
indirection, and — since reactions carry no DB access on the frontend — the backend resolves that
indirection to a concrete tag-ID list (or `null`) at schema-serve time, the same way
`resolve_field_filter_tags` resolves the field's static `filter_tags:` (see "`@config:<key>`
indirection in form fields" above). This lets one field's value (e.g. a speed/quality profile)
narrow another field's model options — without a preset ever naming a model filename — while
condition evaluation and action application both stay frontend-only, same as every other action.

`update_validation` also binds server-side for two keys, so a submission is refused (422 with the
field's error) while the reaction's condition holds: `required: true` makes the field required, and
`mask_required: true` on an `allow_inpaint` image field requires its painted `<name>_inpaint_mask`.
An optional `message` replaces the default error text. Both are checked by `bind_form`; the rest of
the object only reaches the frontend.

**A hidden field is never required.** A field the form hides - by its own static `visible: false`, by
its own `set_visibility: false` reaction, or because a container it sits in (a `section`, `row`,
`group`, `tab`) is hidden the same way - is not required: neither its static `required: true` nor an
`update_validation` `required`/`mask_required` applies while it is hidden, on the server and in the
browser alike (the browser never shows a hidden field, so it cannot be filled). Its other checks
(ranges, options, media containment) still run, and its value is still submitted. Use this to make
fields optional in a state where the run does not use them, keyed on the same condition the pipeline
uses, rather than dropping `required: true`. The Qwen-Image-2.1 control mode hides its whole Models
section while "Only extract the guide" is on, because that run never loads the image model:

```yaml
- type: "section"
  label: "Models"
  reactions:
    - when:
        logic: "AND"
        conditions:
          - { field: "guide_only", equals: true }
          - { field: "guide_extract", equals: true }
          - { field: "guide", not_in: ["none", "grayscale"] }
      then: { set_visibility: false }
  children: [...]   # diffusion_model, text_encoder, vae, control_model - all required: true
```

The **closed set of 12 operators** (source: `OPERATORS` in schema.py, mirrored in reactions.ts):
`equals`, `not_equals`, `in`, `not_in`, `greater_than`, `less_than`, `greater_than_or_equals`,
`less_than_or_equals`, `contains`, `not_contains`, `is_empty`, `is_not_empty`.


### Cloud model controls

A preset that drives a hosted model (`engine: cloud`, see [Cloud Models](../cloud-models.md)) cannot
hard-code its controls, because each model offers different parameters. Three pieces let a form follow
the model the user picked:

**`tasks` on a `model` field.** With `model_type: "cloud"`, `configuration.tasks: [txt2img]` keeps only
models whose provider catalog lists at least one of those tasks. It is sent as `?tasks=` to
`GET /api/presets/{id}/models` and ignored for every other model type.

**`capability:`** binds a field to one parameter or media input of the chosen model. It is a field key
next to `audience` and `width` (not inside `configuration`), and it is emitted to the frontend as
`capability` on the field's schema:

```yaml
- name: "aspect_ratio"
  type: "select"
  label: "Aspect ratio"
  capability: { model_field: "model", param: "aspect_ratio" }

- name: "references"
  type: "image"
  label: "Reference images"
  capability: { model_field: "model", input: "reference" }
```

| Key | Notes |
|-----|-------|
| `model_field` | Required. The `name` of the field that holds the model (a `model` field with `model_type: "cloud"`). |
| `param` | A canonical parameter (`prompt`, `negative_prompt`, `count`, `seed`, `aspect_ratio`, `resolution`, `size`, `duration_s`, `fps`, `quality`, `output_format`, `background`, `guidance`, `steps`, `strength`, `generate_audio`, `lyrics`, `voice`, `instrumental`, `enhance_prompt`) or a provider extra `x.<wire name>`. |
| `input` | A media role: `reference`, `first_frame`, `last_frame`, `mask`, `source_image`, `source_video`, `source_audio`. |

Give exactly one of `param` and `input`. Any other key, an unknown parameter or role, or a
capability with neither is a schema error (`preset_lint` reports it).

The frontend reads the chosen model's capabilities from `GET /api/cloud/models/{model_id}/capabilities`
and, for each bound field: hides it (and does not submit it) when the model lacks that parameter or
input for the mode's task, replaces a `select`'s options with the model's enum values, and applies the
model's minimum, maximum, step and integer rules to a number control. The server applies the same
capabilities in `bind_form`, pinned to the backend the job was routed to: a value for something the
model lacks is dropped (and listed in `stripped`), and an enum value the model does not offer, a number
out of range, a non-integer for an integer range, a non-boolean for a boolean or a missing required
parameter is a `field_errors` entry in the 422. The mode's task is the literal `task:` of its
`cloud_generate` pipe.

**`cloud_options`** renders the model's leftover parameters as standard controls: every `x.` extra
and, unless `configuration.include_unbound` is `false`, every canonical parameter no other field in the
form is bound to. Bind it to the model field only:

```yaml
- name: "provider_options"
  type: "cloud_options"
  label: "Provider options"
  capability: { model_field: "model" }
  configuration: { include_unbound: true }
```

Its value is an object keyed by full parameter name (`{"x.style": "noir"}`). Unknown keys are dropped,
values are validated against the model's entry, and the result reaches the `cloud_generate` pipe as
its `options`. `include_unbound` is its only configuration key.

Reusable field blocks for these controls ship under `content/presets/_shared/cloud/` and are pulled in
with `children: "{{ paths._shared }}/cloud/tabs/image.yml"`; `python scripts/preset_new.py ... --engine
cloud --driver cloud.<key>` assembles a preset from them.

## Form binding and validation (`bind_form`)


Before any pipeline renders, the submitted `form_data` is bound against the mode's form tree
(`bind_form`, `src/features/forms/binding.py`). Author-facing summary of what it does:

1. resolves the form variant (`form_name`; unknown → error, absent → the mode's default);
2. **strips unknown keys** (logged, not an error) — only declared fields reach the pipeline;
3. applies each field's `default:` server-side, **typed** — a slider default arrives as a number,
   a checkbox as a bool, so `{{ form.steps }}` is an `int` without any casting;
4. **validates leaves**: `required` (skipped for a field the form hides - see "A hidden field is
   never required" under Reactions), numeric `min`/`max` ranges, select values against the
   declared options, checkbox values must be bools, model/media values must have the right shape;
5. coerces string numerics from older clients (`"8"` → `8`) — one deliberate leniency, logged;
6. for a cloud preset, applies the chosen model's capabilities (see "Cloud model controls" above);
7. resolves model references and **canonicalizes media paths** (uploads referenced by a
   media/image field are containment-checked against the storage root and rewritten to their
   canonical form — preset YAML never builds storage paths itself).

Validation failures reject the generation request with a **422** (`generation_controller.py`)
whose `detail` carries the per-field contract instead of one opaque string:

```json
{
  "error": "form_validation_failed",
  "field_errors": { "steps": ["must be <= 150"], "sampler": ["'foo' is not one of the options"] },
  "coercions": ["cfg: coerced string '7' to number 7"],
  "stripped": ["some_unknown_key"],
  "message": "..."
}
```

`field_errors` maps each offending field name to its messages (frontends render them inline);
`coercions`/`stripped` report the lenient fixes that were applied. Nothing invalid ever reaches a
pipe.

