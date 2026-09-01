---
category: Presets / Models
category_order: 70
order: 10
---

# Preset Authoring Guide

A **preset** teaches PotionUI how to drive one image/video/audio model: what form the user
fills in, and what pipeline runs when they hit generate. Presets are plain YAML on disk, validated
against a strict schema (`src/features/presets/schema.py`) and linted by `scripts/preset_lint.py`.

This is the authoritative reference. Everything here is verified against the current code; where a
detail depends on a source file, that file is named so you can double-check.

## Quick start

```bash
# Scaffold a schema-valid skeleton (mints a fresh ULID id)
python scripts/preset_new.py MyModel/standard --category image --modes txt2img

# Validate (exit 0 = clean; errors fail, warnings don't)
python scripts/preset_lint.py content/presets/marketplace/MyModel/standard
```

Then fill in `modes/<mode>/pipeline.yml` with the real pipes and flesh out the form.

## Canonical layout

Presets live under `content/presets/marketplace/<Model>/` (optionally `content/presets/marketplace/<Model>/<variant>/`
when a model ships more than one variant) — the tracked, shipped presets — or under
`content/presets/local/<Model>/<variant>/`, a `.gitignored` root scanned exactly the same way, for a
preset that's yours and not meant to be committed. `<Model>` and `<variant>` are free-form
directory names (e.g. `WAN_2_2/official`, or just `SDXL`, `zImage`). **Nothing parses the
directory at all** — the engine is read from `engine:` in `preset.yml`, and neither the root
nor the `<Model>`/`<variant>` names carry any meaning to the loader. See
[Backends and Engines](backends.md).

A plugin can ship its own presets by declaring a `presets:` root in its
`manifest.yml` (see [Backends and Engines](backends.md)); those roots are scanned
exactly like the core tree when the plugin is enabled. Presets that only make
sense with a given plugin installed live with it — e.g. the `comfyui` presets
ship inside the `comfyui-backend` plugin. A preset keeps the identity in its own
`preset.yml` (`id:`) regardless of which root it lives under, so moving it
between roots does not change its id. `{{ paths._shared }}` always resolves
against the core `content/presets/_shared` tree, so a plugin preset can still reference
the shared form/resolution fragments there.

```
content/presets/marketplace/<Model>[/<variant>]/    # or content/presets/local/<Model>[/<variant>]/
├── preset.yml                 # REQUIRED manifest
├── description.md             # optional long-form description (Markdown)
├── public/                    # the ONLY directory served over HTTP (see "Preset media")
│   ├── cover.png              #   cover image, carousel option images, gallery examples
│   └── examples/*.png
├── files/
│   └── form/
│       └── *.yml              # preset-local form option files (see "External option files")
└── modes/
    └── <mode>/                # one dir per mode listed in preset.yml `modes:`
        ├── pipeline.yml       # the pipe list for this mode (Jinja2-templated)
        ├── form.yml           # the DEFAULT form variant (usually a tabs container)
        ├── tabs/
        │   └── *.yml          # tab bodies referenced from form.yml
        └── variants/          # OPTIONAL — additional named form variants
            └── <name>/
                ├── form.yml
                └── tabs/*.yml
```

Shared vocabulary (resolutions, ComfyUI samplers/schedulers) lives once under
`content/presets/_shared/` and is referenced from any preset via `{{ paths._shared }}` (see below).

Rules the loader (`src/features/presets/loader.py`) and linter (`src/features/presets/linter.py`) enforce:

- `preset.yml` is required and must validate.
- Every mode name in `preset.yml` `modes:` **must** have a matching `modes/<mode>/` directory
  (missing directory → lint **error**). A `modes/<mode>/` directory not listed in `modes:` is a
  **warning** (orphan).
- `id` must be unique across the whole scanned tree (duplicate → error).
- Literal (non-templated) option-file paths referenced from a form must exist on disk (missing → warning).

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
| `engine` | yes | string | The protocol this preset's pipes speak: `native` (in-process diffusers pipes) or `comfyui` (a ComfyUI server), or an engine contributed by a plugin. Scalar, not a list — a preset has exactly one engine. See [Backends and Engines](backends.md). |
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
Director](video-director.md) composition modes (`t2v`/`i2v`/`flf`/`director`/`chain`) a
native video preset supports, plus per-mode capability flags (director audio/IC-LoRA,
chain per-segment LoRAs, keyframe limits) and defaults (fps, duration). See
[Video Director → Preset capability declaration](video-director.md#preset-capability-declaration)
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
sets. `preset.speed_profiles` is also in context directly (mirroring `preset.vars`), so a literal
lookup works too — `get_speed_profile()` is the recommended idiom because a **missing profile name
raises a clear error naming the preset and the profile**, instead of a bare attribute error:

```yaml
- name: "generator/txt2vid_wan22"
  enabled: true
  configuration:
    steps: "{{ get_speed_profile(form.speed_profile | default('standard'))['steps'] }}"
    cfg: "{{ get_speed_profile(form.speed_profile | default('standard'))['guidance'] }}"
```

Because each value above is exactly one `{{ ... }}` expression, it evaluates to its **native**
type — `steps` arrives at the pipe as an `int`, not a string (see
[Exact expressions vs string templates](#exact-expressions-vs-string-templates)).

`get_speed_profile(name, default=...)` also accepts an explicit `default` (e.g. `{}`) to suppress
the error for an optional/experimental profile reference instead of failing generation.

### Rules

- Known keys are typed and validated at schema (preset-load) time; unknown keys are a lint
  **warning**, not a load failure — put forward-compat data under `extra:` to silence it.
- `scripts/preset_lint.py` also warns when a preset declares `speed_profiles` but nothing in its
  `modes/` (no `get_speed_profile()` call, no literal profile name) appears to reference them — a
  declared-but-unused block is almost always a mistake (a forgotten form field, or a leftover from
  a refactor).
- `loras` entries follow the same `{file, weight}` shape used elsewhere in this doc (the `@loop`
  recipe) — consume them with an `items:`-based `@loop` in `pipeline.yml`, same as a `lora_picker`
  field's value.

### Precedence: profile as baseline, form fields override

The idiom in every shipped preset that uses `speed_profiles:` is:

```yaml
steps: "{{ get_speed_profile(form.speed_profile | default('standard'))['steps'] }}"
```

Read this as **the profile supplies the baseline value**, and if the form itself has an explicit
field for the same knob (e.g. an advanced-only `steps` slider under `audience: advanced`), that
field's value is what actually reaches `pipeline.yml` — the profile only fills in what the form
didn't ask the user for. Concretely: `form.speed_profile | default('standard')` resolves *which*
profile is selected (falling back to `'standard'` only when the form has no `speed_profile`
field/value at all), and `get_speed_profile(...)['steps']` is that profile's baseline `steps`. A
preset that also exposes a real `steps` field should read that field with its own fallback
*sourced from* the selected profile, not a hardcoded literal, e.g.
`{{ form.steps | default(get_speed_profile(form.speed_profile | default('standard'))['steps']) }}`
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
tag-ID list at form-schema time; a missing/empty resolved value means **no filtering** (backward
compatible with presets that never declare `configuration:` at all). Filtering itself is OR
semantics — a model matches if it carries *any* of the listed tags, not all of them (contrast with
the library picker's own multi-tag browsing filter, which requires all). The frontend passes the
resolved list to `GET /api/presets/{id}/models` as `any_tag_ids` (comma-separated) to actually
filter the option list.

`scripts/preset_lint.py` errors if a field references `@config:<key>` for a key the preset's
`configuration:` block never declares — the same "cross-file reference must resolve" treatment
`files/form/*.yml` option-file references get.

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

## Modes, pipelines and forms

Each mode is a self-contained generation flow (e.g. `txt2img`, `img2img`, `inpaint`, `txt2vid`).

- `modes/<mode>/pipeline.yml` is the ordered pipe list (see "Template contexts" below).
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

A `tabs/*.yml` file holds the fields for that tab under a `fields:` key.

## Plugin-contributed modes

A plugin can contribute one or more MODES to an already-installed preset it doesn't own, via its
`manifest.yml`'s `preset_modes:` section - distinct from `presets:` (a plugin shipping a whole new
preset, see "Plugin API" below). This is how, e.g., a plugin can add an `img2img` mode to a preset
that only shipped `txt2img`, without forking or editing that preset.

```yaml
# manifest.yml
preset_modes:
  - target: "01KX46YCC5RB5EGYY38SBMVKR5"   # the target preset's id
    modes_root: "contributed"              # a dir in the plugin, relative to the plugin root
```

`modes_root` is a directory laid out exactly like a preset root minus `preset.yml` - it must
contain a `modes/<name>/` subtree (`pipeline.yml` + `form.yml`(`/variants`)), same shape as a core
mode. There is no separate per-mode enable list: every mode dir found under `modes_root/modes/` is
contributed to `target`. A contributed mode is schema-validated through the exact same code path a
core mode is (no second validator) - a broken `pipeline.yml`/`form.yml` in a contribution fails to
load exactly like a broken core one would, with the error attributed to the plugin (see below).

**Provenance**: a contributed mode's `source_plugin` (the contributing plugin's id) is carried on
its `ModeTemplate` and surfaced in the `GET /api/presets/{id}/modes` contract (see below) and the
mode picker (a quiet "Contributed by `<plugin>`" tooltip - no pill, no color, since this is
provenance, not state).

**Merge timing**: contributions are applied in a pass over already-loaded presets, after the
normal core+plugin-preset load pass completes - so a contribution's target must be a preset that
loaded successfully in the same run.

**Absent target, no error**: if `target` isn't installed, or its owning plugin (or the
contributing plugin itself) is disabled, the contribution is simply absent - not a load error. A
plugin targeting a preset the user doesn't have is normal, not a misconfiguration.

**Collision rule (deterministic, never silent)**:

1. A contributed mode name that collides with a **core** mode of the target preset always loses -
   the core preset stays intact, and the contribution is rejected with a load error attributed to
   the plugin.
2. A contributed mode name that collides with **another contribution** (from the same or a
   different plugin) resolves by a fixed, reproducible order: contributions are processed sorted
   by **plugin id (ascending)**, then by declaration order within each plugin's own
   `preset_modes:` list, then by the contributed mode-directory listing order. The first
   contribution to claim a `(target, mode name)` pair wins; every later one is rejected with a
   load error attributed to its plugin, naming the plugin that won the name.

Rejected contributions never take down the target preset - `load_errors` (surfaced by
`scripts/preset_lint.py` and `GET /api/developer/presets/lint`) records the rejection keyed to the
contributing plugin, and the target preset otherwise loads and serves normally.

**Speed profiles and admin configuration are inherited, not per-contribution** - a contributed
mode's pipeline reads `get_speed_profile(...)` and its fields resolve `@config:<key>` filter_tags
exactly like a core mode's would, because both read from the SAME merged `PresetTemplate` /
`preset_id` the target already is - there is no separate per-contribution `speed_profiles:` or
`configuration:` block, and none is needed. A contributed mode whose fields reference a
`configuration:` key the target never declares degrades the same way any preset's own
under-configured field does (no filtering, not an error) - and the same lint cross-check that
catches an undeclared `@config:` reference on a core preset's field catches it here too.

**Real example**: `content/plugins/marketplace/krea2-edit/` contributes an `edit` mode onto the native
Krea-2 preset (`content/presets/marketplace/Krea2/`) - instruction-based image editing is a mode of Krea 2, not
a separate model, so it ships as a contribution rather than the standalone preset it used to be.
Its fields keep their existing `@config:checkpoint_tags`/`text_encoder_tags`/`vae_tags`/`lora_tags`
indirection unchanged, since the native Krea2 preset already declares those same four keys.

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

The ~28 built-in types (from `src/features/fields/builtin.py`):

| Category | Types |
|----------|-------|
| Text | `string`, `textbox` |
| Numeric | `number`, `integer`, `slider`, `seed`, `resolution` |
| Boolean | `boolean`, `checkbox` |
| Options-backed | `select`, `checkbox_group`, `model` (alias `models`), `lora_picker` |
| Media | `image`, `video`, `audio`, `media`, `file` |
| Widgets | `carousel`, `llm`, `alert`, `markdown`, `header`, `section`, `gate`, `prompt_timeline`, `camera_shot` |
| Layout containers | `tabs`, `tab`, `row`, `group`, `accordion` |

Common field shapes (all from real presets under `content/presets/marketplace/`):

Model picker (`type: "model"` — the canonical name; `model_type` selects the model directory):

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
  default:                        # optional default rows (same shape as the runtime value)
    - { model: "some_lora.safetensors", strength: 1.0 }
```

The submitted value is a **list of `{model, strength}` mappings** containing only rows with a
selected model — it may be empty (`[]`). Consume it in `pipeline.yml` with an `items:`-based
`@loop` (see "The `@loop` recipe" below); no `when:` filtering is needed because unselected rows
are never submitted.

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
`children` (a list of nested fields, or a `{{ paths.preset }}/...` path string to an external file),
`audience`, `width`, `full_width`, `hidden_when_video_director`. The schema is `extra="forbid"` — the
removed `value:` initializer key is a load error.

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `audience` | no | `"simple"` \| `"advanced"` | Default `"simple"`. Lets the frontend hide `"advanced"` fields behind a toggle, without a separate form/mode. Applies to every field, including nested `children` (tab bodies, `@loop`-expanded rows) — each is serialized independently, so a child's own `audience:` is what's read, not its parent's. |
| `width` | no | number \| `"a/b"` string | A field's fractional share of the row it sits in, read by the frontend as a CSS grid `fr` weight for a `type: "row"` container's child. Either a positive number used directly as the weight (`width: 2`) or a string fraction of positive numbers (`width: "3/5"`). Absent/`null` takes the default weight. Emitted to the frontend exactly as authored (a string stays a string) — the frontend does its own parsing. |
| `full_width` | no | bool | Default `false`. Stretch the field to fill its column/track instead of hugging its content. Has no visible effect on field types that already fill their column (most of them) — today it only matters for controls that hug their content, such as `stepper`. Emitted to the frontend only when `true`. |
| `hidden_when_video_director` | no | bool | Default `false`. Hides this field in the rendered form whenever the Video Director editor is active for the current preset mode (`vars.video_director.preset_modes`) — for a field whose value the director's own document overrides once attached, but that still needs to render for a mode usable outside the director too. Rendering-only, same contract as `audience`: the value/default stays in `formData` and still submits. Emitted to the frontend only when `true`. |

### Field `default:` typing

`default:` is the **one** initializer key (the old `value:` is gone), and it must be a **native
YAML value of the field's type** — validated at preset load by `FieldSpec`
(`src/features/presets/schema.py`, `_validate_typed_default`):

- `slider` / `number` / `seed` → a real number (`default: 30`, `default: -1`; `"30"` is an error);
- `integer` → a real int;
- `checkbox` / `boolean` → a real bool (`default: false`; `"false"` is an error);
- `select` → a scalar; `string` / `textbox` → a string;
- **Jinja is never rendered in form definitions** — a `default:` containing `{{` is a schema error.
  Make form fields dynamic with `reactions:`, not templates.

Defaults pass through to the frontend and to `bind_form` **as-is**, including falsy values
(`false`, `0`, `""`, `[]`) — there is no truthiness collapse.

## External option files

Option lists (camera angles, resolutions, samplers, ...) live in YAML files instead of being inlined,
so they can be shared and edited centrally. Two locations:

- **Preset-local:** `files/form/*.yml` inside the preset, referenced with `{{ paths.preset }}/files/form/...`.
- **Shared:** `content/presets/_shared/**` referenced with `{{ paths._shared }}/...` (e.g.
  `{{ paths._shared }}/resolutions/sdxl.yml`, `{{ paths._shared }}/comfyui/form/samplers/all.yml`).
  Use `_shared` for vocabulary reused across presets (resolutions, ComfyUI samplers/schedulers).

`{{ paths._shared }}` is only defined in **field option-file path** templates — the `file:` / `files:` /
`phrasebook_source` values of `select`/`resolution` fields, which are rendered separately when options
are loaded (`src/features/fields/select.py`, `resolution.py`, whose context is exactly
`{paths: {preset, _shared}}`). See "Template contexts" below for the full picture — `_shared` is **not**
available in `pipeline.yml` or in form `children:` paths.

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
            lora_name: "{{ item.model | replace('models/loras/', '') }}"
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

## Reactions (conditional field behavior)

Reactions make fields respond to other fields' values (show/hide, set values, swap options). They are
evaluated **only in the frontend** engine (`frontend/src/lib/form/reactions.ts`); the backend validates
their shape at preset-load time via `ReactionSpec`/`ConditionSpec`/`ActionSpec` in schema.py (there is no
runtime backend reaction engine). A field carries a list under `reactions:`:

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

The **closed set of 12 operators** (source: `OPERATORS` in schema.py, mirrored in reactions.ts):
`equals`, `not_equals`, `in`, `not_in`, `greater_than`, `less_than`, `greater_than_or_equals`,
`less_than_or_equals`, `contains`, `not_contains`, `is_empty`, `is_not_empty`.

## Template contexts

YAML values are Jinja2 templates (`{{ ... }}` / `{% ... %}`), but **which variables are in scope depends
on where the template lives**. There are three distinct contexts — don't assume a variable from one is
available in another:

| Context | Rendered by | Available |
|---------|-------------|-----------|
| `pipeline.yml` values | `PresetProcessor.process` (at generation time) | Full set below: `form`/`request`/`generation`/`preset`/`runtime`/`paths` context roots + the `path`/`icon`/`get_speed_profile` globals. **No `paths._shared`.** |
| Form `children:` paths (form.yml / tab yml) | the loader, at load time (`src/features/presets/loader.py`) | **Only `{{ paths.preset }}`**, substituted textually. No form data, no `_shared`. |
| Field option-file paths (`file:`/`files:`/`phrasebook_source` of `select`/`resolution`) | `src/features/fields/select.py`, `resolution.py` (at option-load time) | **Only `paths.preset` and `paths._shared`.** |

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

Give every optional form-field reference a `| default(...)` whose fallback matches the field's own
`default:` — the linter warns when a `{{ form.<name> }}` reference names a field that doesn't
exist in the mode's form tree and has no default (see "Linting" below).

### `pipeline.yml` context

The context roots built by `PresetProcessor.process` (verified in `src/features/presets/processor.py`):

| Reference | Value |
|-----------|-------|
| `form.<field>` | The **bound form's** values — typed and defaulted by `bind_form` before the pipeline ever renders (`form.steps` is an int, `form.loras` a list; see "Form binding and validation" below). Runtime-injected documents (`form.video_director`, prompt timelines, `form.llm`) arrive as ordinary keys of this same dict. |
| `request.mode` | The mode being generated, e.g. `'txt2img'`. |
| `request.form_name` | The form variant this submission used (see [Variants](#variants)); defaults to the mode's default variant. |
| `generation.prompts.first` | The first expanded prompt pair, `{positive, negative}`. |
| `generation.prompts.pairs` | All per-image expanded pairs (see [Prompts](prompts.md)). |
| `generation.prompts.positives` / `.negatives` | The flattened per-side lists. |
| `generation.seed` | The resolved base seed (never `-1` here). |
| `generation.quantity` | Number of images requested. |
| `preset.id` / `preset.name` | The preset's id and display name. |
| `preset.vars` | The `vars:` mapping from `preset.yml` (e.g. `preset.vars.num_lora_slots`). |
| `preset.speed_profiles` | The `speed_profiles:` mapping (e.g. `preset.speed_profiles.draft.steps`); prefer `get_speed_profile()` for the clear-error-on-missing behavior. |
| `preset.configuration` | Admin-set configuration values (see "Configuration (admin-set)"). |
| `runtime.settings.file_storage_directory` | The storage root, resolved **once per build** with the authenticated user (a snapshot — no live settings calls at render time). |
| `runtime.settings.nsfw` | The user's NSFW setting (same snapshot). |
| `paths.preset` | Absolute path to this preset directory. (No `paths._shared` in the pipeline context.) |

Allowlisted globals (registered in `src/platform/templating/processor.py`):

| Name | Signature | What it does |
|------|-----------|--------------|
| `path` | `path(path_type, file_name=None)` | Resolve a resource path (models, loras, ...). (alias: `get_path_for`) |
| `icon` | `icon(name)` | Resolve a UI icon token (used in form labels). |
| `get_speed_profile` | `get_speed_profile(profile_name, default=<raises>)` | Look up a `speed_profiles:` entry by name. Raises a clear error naming the preset and profile if missing and no `default` is given. See [Speed profiles](#speed-profiles). |

Filters: `matches` (regex search; alias `regex_search`) plus all Jinja builtins — `default` being
the load-bearing one (see above).

Note: `device`, `dtype` and `gpu_max_vram` are **not** in the template context — they are
native-backend config, injected into every pipe by `NativeBackend.prepare_pipes`. See
[Backends and Engines](backends.md).

**Removed** (build errors if used — the linter flags them with a migration hint): the `get_form`,
`value`/`get`, `contains`/`get_is_in`, `dict`, `setting`/`config` globals, the `@object:`/`@dict:`
string directives, and the entire `input.*` context. Their replacements are the native context
roots above: `get_form('custom', ['steps'], 20)` → `{{ form.steps | default(20) }}`,
`setting('SYSTEM', 'file_storage_directory')` → `{{ runtime.settings.file_storage_directory }}`,
`input.generation.prompts.p_prompt` → `{{ generation.prompts.first.positive }}`.

### Pipe shape and `enabled:`

Minimal pipe shape (`PipeSpec`): `name` (required, the registered pipe name), optional `id`
(referenced by other pipes' `input`), `enabled`, `input` (a list of
`[name, provider_pipe_id, provider_output_var]`), and `configuration`.

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

### Form binding and validation (`bind_form`)

Before any pipeline renders, the submitted `form_data` is bound against the mode's form tree
(`bind_form`, `src/features/forms/binding.py`). Author-facing summary of what it does:

1. resolves the form variant (`form_name`; unknown → error, absent → the mode's default);
2. **strips unknown keys** (logged, not an error) — only declared fields reach the pipeline;
3. applies each field's `default:` server-side, **typed** — a slider default arrives as a number,
   a checkbox as a bool, so `{{ form.steps }}` is an `int` without any casting;
4. **validates leaves**: `required`, numeric `min`/`max` ranges, select values against the
   declared options, checkbox values must be bools, model/media values must have the right shape;
5. coerces string numerics from older clients (`"8"` → `8`) — one deliberate leniency, logged;
6. resolves model references and **canonicalizes media paths** (uploads referenced by a
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

> Note: a `cache:` key is still accepted on a pipe by the schema/processor but is a no-op — model reuse
> now goes through `ModelLifecycle` (the `MODELS` built-in service), not per-pipeline cache keys.
> No shipped preset uses `cache:`; don't add it to new presets.

## Testing presets

A preset may ship a `tests.yml` next to its `preset.yml` describing end-to-end test cases: real
generations run through the real pipeline, with pass/fail assertions on the output. This is
different from linting — linting checks that a preset's YAML is well-formed and internally
consistent without running anything; a `tests.yml` case actually generates an image/video and
checks what came out.

```yaml
schema: 1

cases:
  - name: "flux-klein-baseline-fast"
    mode: "txt2img"
    seed: 12345
    tags: ["fast"]
    form:
      prompt: "a small red boat on a calm lake at sunset, photograph"
      steps: 10
      guidance: 3.5
      sampler: "euler"
      resolution: "1024x1024"
    models:
      diffusion_model: { sha256: "3f9a1c...e02b" }
      text_encoder: { sha256: "88d0e4...1a77" }
      vae: { sha256: "c1a2b3...90fe" }
    checks:
      min_outputs: 1
      resolution: "1024x1024"
      not_black: true
```

The schema (`src/features/presets/tests_schema.py`) is intentionally **`extra: forbid`** everywhere,
unlike `SpeedProfile`'s deliberately loose `extra: allow` above — a typo'd key in a test case
(`sha265`, `min_outpts`) would silently produce a case that "passes" without checking what it was
supposed to, which is worse than not having the test at all. There is no soft-failure story here
the way there is for a stray `speed_profiles` key.

### `TestCase` fields

- **`name`** (required) — kebab-case, unique within the file. Case reports and the runner's HTML
  gallery output are keyed by this.
- **`mode`** (required) — must be one of the preset's declared `modes:`.
- **`kind`** — `"image"` (default) or `"video"`. A hint for the runner's check semantics, not a
  behavior change by itself: `"image"` cases use `checks` exactly as documented below;
  `"video"` exists so a video preset's `not_black` doesn't vacuously pass on zero image outputs —
  the runner grows video-shaped checks (e.g. frame count in place of `min_outputs`) keyed off this
  field. Declaring `kind: "video"` on a case whose preset actually produces video is the only thing
  required today; the rest is forward-compat.
- **`seed`** (required) — pins the generation for determinism. Do **not** also set `seed` inside
  `form:` — this field is what the runner uses, and it always wins; a duplicate `form.seed` is
  redundant at best and misleading at worst.
- **`form`** — partial form values; anything not set here falls back to the preset form's own
  defaults, same as a real user leaving a field untouched. Two conventions the runner follows:
  - a `prompt` key inside `form` maps to the generation request's top-level prompt (prompt text
    is not itself a preset form field — see `GenerationRequest.prompt` in
    `src/features/generation/dto.py`);
  - every other key is a literal preset form-field name (e.g. `steps`, `resolution`, `sampler`,
    or a model-selector field like `diffusion_model`) — check the preset's own `modes/*/tabs/*.yml`
    for the exact names it exposes.
- **`tags`** — defaults to `["fast"]`. Free-form; the runner and CI use these to select subsets
  (e.g. run only `fast`-tagged cases on every PR, run everything nightly). The one convention with
  special meaning is `needs-model` (see below).
- **`models`** — maps a form-field name (matching a `type: "model"` field, e.g. `diffusion_model`,
  `vae`, or SDXL's single `model`) to a `ModelRef`. The runner resolves each by `sha256` against
  the local model index and injects the resolved local path into `form` under that same key before
  submission — so a case's `form:` block never hardcodes a filename/path itself, and the suite
  keeps working across a checkpoint being renamed or re-downloaded. The injected value is the
  models-table row's `file_path`, matching exactly what a `model`/`lora_picker` form field stores
  when a real user picks a model in the UI (see `docs/models.md`) — no `model:<id>`-style
  indirection. **A `form:` key must never also appear in `models:`** for the same case (the linter
  errors on this — see below); `models:` owns that key entirely.
- **`checks`** — optional; all fields default (`min_outputs: 1`, `resolution: null` i.e.
  unchecked, `not_black: true`, `max_seconds: null` i.e. unchecked). `resolution` accepts either a
  bare `"WxH"` string (every output must match it) or a list of `"WxH"` strings (each output must
  match one of the listed sizes, order-independent) — the list form covers a case whose outputs
  legitimately differ in size, e.g. a batch that mixes a hires-pass output with base-resolution
  ones.

### `ModelRef` / sha256 and Hugging Face convention

Models are referenced by **content hash, not filename** — `ModelRef.sha256` is a required,
64-hex-digit string. An optional `hf: { repo, file }` gives the runner a Hugging Face
repo/filename to fetch from if the hash isn't found in the local model index; after download the
runner verifies the fetched file's hash still matches before using it.

```yaml
models:
  diffusion_model:
    sha256: "3f9a1c2b...e02b"
    hf: { repo: "black-forest-labs/FLUX.2-klein-9B", file: "flux2-klein-9b.safetensors" }
```

**Placeholder convention**: a case whose model(s) aren't available in the current checkout yet
should use the placeholder sha256 `"0" * 64` (64 zero digits — a value no real `sha256sum` output
can ever produce) for every `ModelRef` it needs, and add the **`needs-model`** tag. The runner
skips (does not fail) a case carrying that placeholder; the linter treats a placeholder sha256
*without* the `needs-model` tag as a warning (it looks like a real, passing test but never actually
ran). All three shipped example suites below use this convention for their DiT/text-encoder/VAE
weights, since those large checkpoint files aren't always present on every dev checkout.

Computing a real hash is one `sha256sum path/to/checkpoint.safetensors` away. For a preset whose
model files are large, hash only the ONE primary checkpoint the case can't run without (the
diffusion model / single checkpoint) rather than every referenced component — if that one file is
present, hash it for real; if it's missing, the whole case needs the placeholder anyway since it
can't run without it.

### Shipped examples

Three presets ship a `tests.yml` today, each with one `fast`-tagged smoke case (low steps,
smallest available resolution, pinned seed):

- `content/presets/marketplace/Flux2/tests.yml` — three cases: a baseline, a maintainer Klein turbo
  session, and a case exercising a sprint knob (`form.step_cache_threshold`) on top of the baseline.
  Two are `needs-model` (placeholder hashes) on a checkout without the Klein DiT/TE/VAE files
  downloaded.
- `content/presets/marketplace/QwenImage/tests.yml` — one baseline case, also `needs-model`.
- `content/presets/marketplace/SDXL/tests.yml` — one baseline case with a **real** sha256 (SDXL loads
  a single checkpoint file, and this preset's default checkpoint is present on disk), so this case
  is expected to actually run rather than be skipped.

### Running the suite

The standalone runner (`scripts/preset_test_suite.py`) resolves each case's models, submits the
generation through the real API, applies `checks:`, and writes an HTML gallery of the results. See
`python scripts/preset_test_suite.py --help` for the current CLI (tag filtering, preset/mode
selection, output location).

### Linting `tests.yml`

`scripts/preset_lint.py` validates every `tests.yml` it finds alongside the schema checks above.
It:

- warns (informational only) when a preset has **no** `tests.yml` at all — most presets don't have
  one yet, this isn't an error;
- errors on a `tests.yml` that fails to parse or fails schema validation, naming the offending case;
- errors on **duplicate case names** within a file;
- errors on a **malformed `sha256`** (not 64 hex digits — the all-zero placeholder is exempt by
  construction, since it *is* 64 hex digits);
- errors when a case's `mode:` **isn't declared** in the preset's `preset.yml` `modes:`;
- errors when a `form:` key **collides with a `models:` key** in the same case;
- warns when a case uses the placeholder sha256 without the `needs-model` tag.

### Linting `pipeline.yml` templates

`scripts/preset_lint.py` parses each mode's `pipeline.yml` as YAML (comments never trip a check)
and enforces the template contract described under "Template contexts":

- **Deleted context is an error.** Any scalar whose `{{ }}`/`{% %}` regions reference the removed
  surface — `get_form(`, `value(`, `setting(`/`config(`, `contains(`, the `input.*` context, or a
  bare `@object:`/`@dict:` directive — is flagged with a migration hint. Strict evaluation makes
  every one of these a hard build failure, so lint catches them before a user does.
- **String `enabled:` must be an exact expression.** A pipe-level `enabled:` that is a string but
  not exactly one `{{ expression }}` block is an **error** — it would string-render instead of
  evaluating to a bool, so the pipe could never be enabled. Use a YAML bool or a single
  expression.
- **`@loop` `items:` strings must be exact expressions.** `items` is evaluated natively to a
  list/dict/range; a mixed/string template there is an **error** (a literal YAML list is fine).
- **`{{ form.<name> }}` references must resolve.** The linter walks the mode's real form tree —
  every variant, external `tabs/*.yml` fragments, and statically-expanded `@loop` field
  generators (so `controlnet_2_model` from a `count: 3` loop is known) — plus the
  runtime-injected keys (`video_director`, `timeline`, `llm`, `prompt_timeline`). A reference to
  a field that doesn't exist anywhere in that tree **and** has no `| default(...)` in its
  expression is a **warning**: under strict evaluation it becomes a runtime build error the first
  time the field is absent.

### Linting field `default:` values in external tab fragments

`form.yml`'s own fields are schema-validated wherever the form is loaded, but most fields live in
external `tabs/*.yml` fragments referenced via `children:`. The linter follows those references
and runs each fragment's `fields:` through the same `FieldSpec` validation the loader uses, so a
typed-default mistake — `default: "30"` on a slider, `default: "true"` on a checkbox, Jinja inside
a `default:` — surfaces as a lint **error** with the schema's message instead of only failing at
preset load.

## Linting

Validate presets before relying on them:

```bash
python scripts/preset_lint.py                    # lint content/presets/marketplace/ and content/presets/local/
python scripts/preset_lint.py content/presets/marketplace/SDXL   # lint a subtree
python scripts/preset_lint.py --fix              # migrate preset.yml to canonical schema, then lint
```

The linter (`src/features/presets/linter.py`) exits non-zero only if there are **errors** (warnings do
not fail the run). It checks: manifest validity (via the schema), unique ids, that every declared
mode has a directory on disk, that no mode directory is orphaned (warning), that literal
option-file references exist (warning), the pipeline-template contract and external-fragment field
defaults (the two sections above), media/variant/speed-profile cross-checks, and — if the preset
ships one — that its `tests.yml` is well-formed (see "Testing presets" above). On the default run
(no explicit paths) it also cross-checks every discovered plugin's `preset_modes:` contributions
against the presets found under the scanned trees — the same collision rules "Plugin-contributed
modes" documents, so a rejected/colliding contribution shows up here without booting the app;
passing explicit paths skips this cross-check, same as it already skips plugin-owned `presets:`
roots. `--fix` performs a comment-preserving migration of legacy `preset.yml` files (adds
`schema: 1`, converts `modes:` mapping → list, moves inline `description:` to `description.md`,
writes an explicit `engine:`, infers `category:`, deletes dead keys); the inferred `category` is
always printed for human review.

The same check is exposed over HTTP at **`GET /api/developer/presets/lint`** for the developer UI.

## See also

- [Backends and Engines](backends.md) — `engine:`, how a preset's pipes get executed.
- [Providers](providers.md) — how presets get the models they reference without declaring downloads.
- [Video Director](video-director.md) — `vars.video_director`, the composition-mode capability
  declaration for native video presets.
- [Hardware Requirements](user/hardware-requirements.md) — the measured per-family VRAM/RAM table
  `requires:` values must be sourced from.
