---
title: preset.yml Reference
category: Presets / Models
category_order: 70
order: 15
---

# preset.yml Reference

Everything a `preset.yml` manifest can declare: required fields, `vars:`, speed profiles,
admin-set configuration, media, hardware guidance, and the LLM chat context block. See the
[Preset Authoring Guide](../presets.md) for the file tree this fits into, [Forms](forms.md) for
`form.yml`/tabs/fields, and [Pipelines](pipelines.md) for how `pipeline.yml` consumes
everything declared here.

## `preset.yml` reference

The manifest is `PresetManifest` in `src/features/presets/schema.py`, validated with `extra="forbid"` —
**unknown top-level keys are rejected**. Fields:

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `schema` | yes | int | Must be `1`. |
| `id` | yes | string | Must match `^[A-Za-z0-9_-]{3,64}$` and be globally unique. Existing ids are DB-referenced — never change an id in place. |
| `name` | yes | string | Human-readable display name. |
| `version` | yes | string | Semver, e.g. `1.0.0` (regex `^\d+\.\d+\.\d+(?:[-+].+)?$`). |
| `category` | yes | enum | One of `image`, `video`, `audio`, `3d`, `utility`. |
| `engine` | yes | string | The protocol this preset's pipes speak: `native` (in-process diffusers pipes) or `comfyui` (a ComfyUI server), or an engine contributed by a plugin. Scalar, not a list — a preset has exactly one engine. See [Backends and Engines](../backends.md). |
| `tags` | no | list | Free-form strings (default `[]`). |
| `media` | no | mapping | Cover image + example gallery. See [Preset media](#preset-media). |
| `vars` | no | mapping | Preset-wide constants, read in `pipeline.yml` as `{{ preset.vars.<name> }}`. |
| `speed_profiles` | no | mapping | Named generation profiles (e.g. `draft`/`standard`/`max`), read via `get_speed_profile()`. See [Speed profiles](#speed-profiles). |
| `llm` | no | mapping | Preset/family-level prompting guide + chat-workspace context knobs. See [LLM context](#llm-context). |
| `requires` | no | mapping | Optional VRAM/RAM guidance shown at preset-choice time. See [Hardware requirements](#hardware-requirements). |
| `modes` | yes | list | Non-empty **list** of mode-name strings (each needs a `modes/<name>/` dir). |

**Removed / rejected** (do not use — they fail validation): a top-level `form:`, an inline
`description:` (put prose in `description.md`), `resolutions:`, and the old list-valued
`supported_backends:` (replaced by the scalar `engine:` — no compatibility shim). The `modes:`
mapping-with-nulls form is gone — `modes:` is a plain list.

Real example (`content/presets/marketplace/QwenImage/preset.yml`):

```yaml
schema: 1
id: "01K0W24A3RADXXABH16YQ7KF00"
name: "Qwen-Image"
version: "1.0.0"
category: "image"
engine: "native"
tags: ["qwen", "qwenimage", "local", "transformer", "text-rendering"]

vars:
  model_base: "QWEN_IMAGE"
  default_steps: 50
  default_cfg: 4.0
  num_lora_slots: 3          # referenced by an @loop in pipeline.yml

modes:
  - txt2img
```

### `vars.video_director` (native video presets)

One `vars:` key gets its own document: a `video_director` block declares which [Video
Director](../video-director.md) composition modes (`t2v`/`i2v`/`flf`/`director`/`chain`) a
native video preset supports, plus per-mode capability flags (director audio/IC-LoRA,
chain per-segment LoRAs, keyframe limits) and defaults (fps, duration). See
[Video Director → Preset capability declaration](../video-director.md#preset-capability-declaration)
for the full schema and worked examples.

### `vars.promptless_modes` (modes that need no prompt)

Some modes are purely mechanical — upscale, slow-motion, restore — and ask nothing of the
user in the prompt pane. List their mode names here and the UI hides the prompt pane entirely
for those modes and drops the "prompt required" check on the Generate button:

```yaml
vars:
  promptless_modes: [upscale, slowmo]
```

It's a pure UI hint (read from `preset.vars.promptless_modes` on the frontend); the backend
needs no schema change and rejects nothing when the prompt is empty. A promptless mode's
`pipeline.yml` **must not read `generation.prompts`** — with no prompt submitted, the array is
`[{positive: '', negative: ''}]`, so `generation.prompts.first` is `{'positive': '', 'negative': ''}`
and any per-image expansion yields empty strings rather than failing. If a mode legitimately
needs a prompt, leave it out of this list.

## Speed profiles

`speed_profiles:` is a top-level `preset.yml` mapping of **profile name -> generation-knob
overrides**, letting a preset switch several settings atomically (e.g. a one-click "Draft"
toggle that drops steps, disables true CFG, and swaps in a distilled LoRA all at once) instead of
wiring each knob to its own form field and reaction. It's top-level, not per-mode, for the same
reason `vars:` is: one preset.yml-wide bag every mode's `pipeline.yml` can read.

```yaml
speed_profiles:
  draft:
    steps: 6
    guidance: 1.0
    shift: 5.0
    loras:
      - { file: "lightx2v.safetensors", weight: 1.0 }
  standard:
    steps: 28
    guidance: 5.0
  max:
    steps: 40
    guidance: 6.5
    sampler: "dpmpp_2m"
    schedule: "karras"
```

Each profile is `SpeedProfile` in `src/features/presets/schema.py` — a **typed known-key whitelist**
(`steps` int, `guidance` float, `shift` float, `sampler` string, `schedule` string, `loras` a list
of mappings) plus a free-form `extra:` mapping for forward-compat knobs that don't warrant their
own key yet. A wrong-shaped known key (e.g. `steps: "fast"`) is a real schema error. An
**unknown** key (anything not in the whitelist and not nested under `extra:`) does **not** fail
preset load — the linter warns about it instead (`scripts/preset_lint.py`), matching the "the
whole preset shouldn't become unloadable over one soft mistake" philosophy `media:` and mode-file
checks already follow.

### Selecting a profile from a form

The selected profile arrives like any other form value — there's no separate selection mechanism.
Give the user a `select` field listing the profile names:

```yaml
# modes/txt2vid/tabs/generation.yml
- name: "speed_profile"
  type: "select"
  label: "Speed"
  default: "standard"
  configuration:
    options:
      - { value: "draft", label: "Draft (fast, lower quality)" }
      - { value: "standard", label: "Standard" }
      - { value: "max", label: "Max quality" }
```

Then consume it in `pipeline.yml` with the `get_speed_profile(name)` global (registered in
`src/platform/templating/processor.py`): look up the selected name, then read whichever knobs the profile
sets. There is no `preset.speed_profiles` direct dot access in the render context — it's a build
error (see "Removed" under [Template contexts](pipelines.md#template-contexts)) — `get_speed_profile()` is the
recommended idiom for an EXPLICIT lookup because a **missing profile name raises a clear error
naming the preset and the profile**, instead of a bare attribute error:

```yaml
- name: "generator/txt2vid_wan22"
  enabled: true
  configuration:
    steps: "{{ get_speed_profile(form.speed_profile | default('standard'))['steps'] }}"
    cfg: "{{ get_speed_profile(form.speed_profile | default('standard'))['guidance'] }}"
```

Because each value above is exactly one `{{ ... }}` expression, it evaluates to its **native**
type — `steps` arrives at the pipe as an `int`, not a string (see
[Exact expressions vs string templates](pipelines.md#exact-expressions-vs-string-templates)).

`get_speed_profile(name, default=...)` also accepts an explicit `default` (e.g. `{}`) to suppress
the error for an optional/experimental profile reference instead of failing generation.

### `generation.profile`: the resolved-profile shortcut

Every knob a preset reads from a profile repeats the same
`get_speed_profile(form.speed_profile | default('standard'))[...]` expression — `generation.profile`
(a `pipeline.yml` context root, see [Template contexts](pipelines.md#template-contexts)) is that exact lookup,
already done once per build: the profile `form.speed_profile` names, or the **first declared**
profile if the form has no `speed_profile` field/value (or its value doesn't name a declared one —
`speed_profiles:` preserves YAML declaration order end to end, and `SpeedProfile` has no
`default:`/`is_default:` marker to prefer instead), or `{}` if the preset declares no profiles at
all — so `generation.profile.steps` fails loudly (StrictUndefined) exactly like any other missing
key rather than silently. The two examples above rewrite to:

```yaml
- name: "generator/txt2vid_wan22"
  enabled: true
  configuration:
    steps: "{{ generation.profile.steps }}"
    cfg: "{{ generation.profile.guidance }}"
```

Prefer `generation.profile` for the common case (read the profile the current request selected);
reach for `get_speed_profile(name)` only to look up a profile OTHER than the resolved one, or to
get the loud missing-name error `generation.profile`'s silent first-profile fallback doesn't give you.

### Rules

- Known keys are typed and validated at schema (preset-load) time; unknown keys are a lint
  **warning**, not a load failure — put forward-compat data under `extra:` to silence it.
- `scripts/preset_lint.py` also warns when a preset declares `speed_profiles` but nothing in its
  `modes/` (no `get_speed_profile()` call, no `generation.profile` use, and no literal profile name)
  appears to reference them — a declared-but-unused block is almost always a mistake (a forgotten
  form field, or a leftover from a refactor).
- `loras` entries follow the same `{file, weight}` shape used elsewhere in this doc (the `@loop`
  recipe) — consume them with an `items:`-based `@loop` in `pipeline.yml`, same as a `lora_picker`
  field's value.

### Precedence: profile as baseline, form fields override

The idiom in every shipped preset that uses `speed_profiles:` is:

```yaml
steps: "{{ get_speed_profile(form.speed_profile | default('standard'))['steps'] }}"
```

or, equivalently, using the resolved shortcut:

```yaml
steps: "{{ generation.profile.steps }}"
```

Read this as **the profile supplies the baseline value**, and if the form itself has an explicit
field for the same knob (e.g. an advanced-only `steps` slider under `audience: advanced`), that
field's value is what actually reaches `pipeline.yml` — the profile only fills in what the form
didn't ask the user for. Concretely: `form.speed_profile | default('standard')` (or, for
`generation.profile`, whatever `form.speed_profile` names) resolves *which* profile is selected,
and `get_speed_profile(...)['steps']`/`generation.profile.steps` is that profile's baseline `steps`.
A preset that also exposes a real `steps` field should read that field with its own fallback
*sourced from* the selected profile, not a hardcoded literal, e.g.
`{{ form.steps | default(generation.profile.steps) }}`
— so changing the profile still changes the effective value for a user who never touched the
advanced `steps` field, while a user who did override it always wins.


## Configuration (admin-set)

`configuration:` is a top-level `preset.yml` mapping declaring **admin-tunable knobs** a preset
exposes, e.g. restricting a `model` field's checkpoint options to a curated set of admin `Tag`s.
Unlike `vars:`/`speed_profiles:` (author-set, shipped with the preset), `configuration:` only
declares the *schema* — key, type, label, description — the *values* are admin-set state, stored
per installed preset, and edited through the admin API rather than the YAML.

```yaml
configuration:
  checkpoint_tags:
    type: model_tags
    label: "Allowed checkpoint tags"
    description: "Only checkpoints tagged with one of these show up in the model picker"
```

Currently one type is supported: `model_tags` (value: a list of admin `Tag` IDs). The type set is
a small extensible registry (`CONFIGURATION_TYPES` in `src/features/presets/schema.py`) — an unknown
`type:` is a schema error, same treatment as an unknown `speed_profiles` known-key mistake would
get if it were typed instead of allowed.

### Admin API

- `GET /api/presets/{preset_id}/configuration` → `{"preset_id", "entries": [{"key", "type",
  "label", "description", "value"}]}` — one entry per declared key, merging the preset.yml schema
  with stored values (`value` is `null` when the admin hasn't set it yet). Empty `entries` for a
  preset that declares no `configuration:` block — not an error.
- `PUT /api/presets/{preset_id}/configuration` body `{"values": {"<key>": <value>}}` — admin-only,
  requires the preset be installed. Rejects unknown keys and type-invalid values (for
  `model_tags`: every ID must be an existing tag) as a single `invalid_configuration` error citing
  every problem found. A successful PUT merges into (not replaces) previously-set keys, and
  returns the same shape as the GET.

Values live in the `presets` table's `configuration` JSON column (migration `081`), keyed by the
installed preset's YAML `preset_id` — see `src/features/presets/configuration.py` for the
validation/merge helpers and `operations.get_preset_configuration`/`set_preset_configuration`.

### `@config:<key>` indirection in form fields

A field configuration value can defer to a preset's stored configuration instead of a literal, via
the string `"@config:<key>"`. Today this is used by the `model` field's `filter_tags:` (see the
`model` field reference above): restrict its options to models carrying at least one of the tag
IDs stored under `checkpoint_tags`:

```yaml
- name: "checkpoint"
  type: "model"
  configuration:
    model_type: "checkpoint"
    filter_tags: "@config:checkpoint_tags"
```

`filter_tags` accepts either this indirection or a literal tag-ID list. It's resolved to a concrete
tag-ID list at form-schema time; a key the preset **declares** but an admin never set means **no
filtering** (backward compatible with presets that never declare `configuration:` at all — an
unset/empty stored value is a normal, expected state). Filtering itself is OR semantics — a model
matches if it carries *any* of the listed tags, not all of them (contrast with the library picker's
own multi-tag browsing filter, which requires all). The frontend passes the resolved list to `GET
/api/presets/{id}/models` as `any_tag_ids` (comma-separated) to actually filter the option list.

A key the preset's `configuration:` block never declares at all — a typo, or `@config:` left over
after the key was renamed — is a different, authoring-time mistake: `PresetTemplateLoader.
_validate_filter_tags_directives` (`src/features/presets/loader.py`) walks every field's
`configuration.filter_tags` and reaction `then.set_filter_tags` while building the preset template
and fails the preset's LOAD, naming the preset, the field, the unknown key, and the declared keys —
so a bad reference never reaches a running preset in the first place, rather than silently degrading
to "no filtering" the way an unset (but declared) key does at serve time. So does any `"@"`-prefixed
value that isn't `"@config:..."` — it's the only `@` directive `filter_tags`/`set_filter_tags` ever
accepted. `scripts/preset_lint.py` catches the exact same mistake independently, without booting the
app, for the developer lint endpoint/CI; `resolve_filter_tags` (`src/features/presets/
configuration.py`), which actually resolves the value at form-schema-serve time, stays a pure,
unconditional prefix match — by the time it runs, the value has already been validated at load.

### Tag deletion is blocked while referenced

Deleting an admin `Tag` (`DELETE /api/tags/{tag_id}`) that's referenced by any installed preset's
stored configuration values returns **409** with
`{"error": "tag_in_use_by_preset", "used_by": [{"preset_id", "preset_name", "key"}, ...]}` instead
of succeeding. There is no force flag — unset the tag from the preset's configuration first (`PUT
.../configuration` with the tag ID removed from the relevant key), then delete it.


## Preset media

A preset can ship a **cover** (shown beside its name in the picker) and a **gallery** of
examples (shown in its detail modal). Gallery entries carry the settings that produced them,
so an example can show the prompt and seed behind the image.

```yaml
media:
  cover: "public/cover.png"
  gallery:
    - src: "public/examples/turbo-1.png"
      caption: "Turbo defaults, 8 steps"   # optional
      prompt: "a lighthouse at dusk"       # optional
      seed: 12345                          # optional
      mode: "txt2img"                      # optional; must be a declared mode
```

Both `media` and every field except `src` are optional; a preset with no `media:` renders a
neutral placeholder.


### Rules

- **Everything servable lives under `public/`.** A `src` must be a relative, forward-slash
  path whose first segment is `public`, with no `..` segments. This is enforced by the schema,
  so a bad path fails preset validation.
- **Allowed extensions**: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.mp4`, `.webm`. Video is
  permitted because `category: video` presets have moving examples. **`.svg` is rejected** — an
  inline SVG can carry script, and presets may be installed from a marketplace.
- `public/` is the **only** directory reachable over HTTP. `preset.yml` (preset root) and the
  option YAML under `files/` are read server-side and are never served. Requests for them 404.
- `scripts/preset_lint.py` additionally checks that each referenced file **exists**, warns when
  one exceeds 2 MiB or 4096 px on its longest side, and warns when a `mode:` is not declared in
  `modes:`. These are lint checks rather than schema checks on purpose: a renamed mode or a
  missing image should not make the whole preset fail to load.

### Serving

Assets are served by `GET /api/media/presets/{preset_id}/{path}`, optionally resized with
`?size=small|medium|large` (480 / 768 / 1024 px wide). Renders are cached on disk under
`storage/preset_media/` and keyed by the source file's mtime, so editing an image invalidates
both the cache entry and the `ETag`. An unknown `size` is a `400`.

`GET /api/presets` returns only `media.cover`; the full gallery comes from
`GET /api/presets/{id}`, so the list payload stays small.


## Hardware requirements

`requires:` is an optional top-level `preset.yml` mapping that surfaces hardware guidance in the
preset picker **before** a user downloads the model — so an 8 GB card discovers a preset doesn't
fit from a badge, not from an OOM after a multi-GB download. It is `PresetRequirements` in
`src/features/presets/schema.py`, `extra="forbid"`, every key optional:

```yaml
requires:
  min_vram_gb: 12          # the preset is not claimed to work below this
  recommended_vram_gb: 16  # the comfortable tier
  min_ram_gb: 16           # system RAM, separate from VRAM (see hardware-requirements.md)
```

All three are plain numbers (GB); a non-positive value is a schema error. Every key is optional,
and so is the block itself — a preset with no `requires:` loads exactly as before, and the picker
renders no badge.

**Populate this honestly, not by estimate.** The source of truth is the measured, per-family table
in `docs/user/hardware-requirements.md` (and the per-family docs under `docs/models/` it links).
Where that page marks a family **Unvalidated** or gives no measured floor for a tier, leave
`requires:` off entirely (or omit the specific key) rather than guess a number — a wrong minimum is
worse than no minimum, since it either scares off a card that would have worked or, worse, promises
one that won't. `min_vram_gb` is a functional claim ("this runs here"); `recommended_vram_gb` is a
comfort claim and can stand alone when only the comfortable tier is measured.

`requires:` is pure metadata: it never reaches `pipeline.yml`'s Jinja context (see
`PresetProcessor`'s `preset` dict below) and has no effect on generation — only on what the picker
shows.

The frontend (`PresetPicker.svelte`) renders a compact "`N GB VRAM`" badge when `min_vram_gb` (or,
failing that, `recommended_vram_gb`) is set. When the backend's `/api/system/stats` endpoint
reports a detected GPU (available to any authenticated user, not admin-gated) and its VRAM is below
`min_vram_gb`, the badge switches to a non-blocking warning — never a disabled button.


## LLM context

`llm:` is an optional top-level `preset.yml` mapping that shapes what the **chat** LLM is told
about this preset every turn. It has no effect on generation itself — it only feeds
`ChatContextBuilder.inject_workspace_block` (`src/features/chat/context_builder.py`), the system
block injected before the user's message whenever a Generate-form workspace is active.

```yaml
llm:
  guide: |                    # optional, multiline prompting guide for this preset/family
    This model prefers short, comma-separated tag phrases over full sentences.
    Always include a camera/lens phrase (e.g. "35mm, f/1.8") for realistic shots.
  context:                    # optional; defaults shown
    form: summary             # "off" | "summary" | "full"
    fields: [checkpoint, loras]   # optional; restricts which resolved models' guidance is pushed
    guidance_chars: 800       # optional; overrides the default per-model guidance cap
  modes:                      # optional; per-mode guide overrides, keyed by mode name
    refs:
      guide: |
        This mode expects a six-section reference brief, not a plain description:
        Subject / Pose / Lighting / Camera / Style / Negative — each on its own line.
```

A preset with **no `llm:` block** gets none of this — the workspace block's prior behavior is
unchanged. Declaring `llm:` (even an empty `llm: {}`) turns on two things:

- **`guide`** — a repo-authored, family-level prompting guide, injected verbatim (capped at 3000
  chars) as a "Prompting guide:" section. This is a good place for phrasing conventions a model
  responds to that don't belong on any single field or checkpoint.
- **`context.form`** (default `summary` once `llm:` is present) — appends a compact listing of the
  active mode's form fields (name, label, type, and `ai_hint` truncated to ~200 chars) to the
  workspace block. `full` additionally includes each field's range/default/options-count; `off`
  suppresses the listing entirely (but `guide`, if set, still appears).

`context.fields`, when set, narrows which form fields' *resolved model guidance* (a checkpoint's or
LoRA's `prompting_guidance`) gets included — models are still listed by name/strength/trigger words
regardless, only the guidance excerpt is gated. Omit it to keep the default auto-detect behavior
(every resolved model's guidance is eligible). `context.guidance_chars`, when set, overrides the
workspace block's default 240-character per-model guidance cap for this preset.

**`modes`** — per-mode guide overrides, keyed by mode name. When the workspace's current mode has
an entry here, its `guide` **replaces** the top-level `guide` for that turn (not concatenated) —
useful when a mode's expected prompt format is fundamentally different from the rest of the preset
(e.g. a references mode wanting a structured multi-section brief instead of a plain description). A
mode with no entry falls back to the top-level `guide` as usual. Same 3000-char cap either way. Mode
keys are free-form and not cross-validated against `modes:` — a plugin-contributed mode is a valid
key even though the schema can't see it declared anywhere.

The header line the block always shows (`Preset: <name> · Mode: ... · Variant: ...`) uses the
preset's display **name**, never its raw id — this applies to every preset, `llm:` block or not.

