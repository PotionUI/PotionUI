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

A preset for a hosted (cloud) model is scaffolded differently: its form and pipeline are complete
from the start, assembled from the shared blocks in `content/presets/_shared/cloud/`.

```bash
python scripts/preset_new.py MyProvider/standard --engine cloud --driver cloud.myprovider \
  --modes txt2img,edit,txt2video,img2video --root content/plugins/local/my-provider/presets
```

Each mode gets a model picker for its task and capability-bound controls (they follow what the chosen
model supports), plus the standard pipeline `dynamic_prompts_renderer` -> `seed_generator` ->
`media_loader` (edit and image-to-video only) -> `param_emitter` -> `cloud_generate` -> `gallery`.
`preset_lint` warns (`cloud_pipeline_shape`) when an `engine: cloud` mode has no `cloud_generate` pipe,
no literal `task`, or a `task` its model picker does not offer. A plugin's own tests can check its
presets against `src.plugin_api.cloud.CLOUD_BLOCKS`, the list of shared blocks.

### Reference pages

This guide is split across several pages under `docs/presets/` — see
[Reading order](#reading-order) below for what to read first. Two generated pages back all of
them: [Pipes Reference](pipes.md) (every pipe's `name:` key, inputs, outputs, and configuration)
and the [Preset Context Cheat Sheet](preset-context.md) (every template global/filter/context
root, `model_type` value, built-in field type, and `_shared` option file) — both regenerated from
the running code by `python scripts/pipes_reference.py` and drift-checked in CI. The same data,
read live against a running install's actually enabled plugins, is also in-app under Administration →
Documentation → **Pipes** / **Field Types** / **Template Functions** / **Output Types**.

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
├── public/                    # the ONLY directory served over HTTP (see presets/manifest.md)
│   ├── cover.png              #   cover image, carousel option images, gallery examples
│   └── examples/*.png
├── files/
│   └── form/
│       └── *.yml              # preset-local form option files (see presets/forms.md)
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

Shared vocabulary (resolutions, ComfyUI samplers/schedulers) and tab bodies identical across
presets live once under `content/presets/_shared/` (`resolutions/`, `tabs/`, ...) and are referenced
from any preset via `{{ paths._shared }}` (see
[External option files](presets/forms.md#external-option-files)). Two modes of one preset that need
the same tab or option file keep one copy and point the other mode at it with
`{{ paths.preset }}/modes/<mode>/tabs/<file>.yml`. Do not copy a file byte-for-byte between presets
or modes; the form.yml entry point of each mode is the only per-mode file that stays duplicated.

`content/presets/_shared/native/` holds the blocks the native image families repeat: the seed and
quantity row (`seed_quantity.yml`), the turbo/base Sampling fields (`sampling_turbo_base.yml`,
parameterised through `vars:`) and the Step Cache row (`step_cache.yml`). Pass per-model values
with the `file` + `vars` form of `children:` (see
[Modes and the form root](presets/forms.md#modes-and-the-form-root)).

Rules the loader (`src/features/presets/loader.py`) and linter (`src/features/presets/linter.py`) enforce:

- `preset.yml` is required and must validate.
- Every mode name in `preset.yml` `modes:` **must** have a matching `modes/<mode>/` directory
  (missing directory → lint **error**). A `modes/<mode>/` directory not listed in `modes:` is a
  **warning** (orphan).
- A mode's `form.yml` may set `short_description:`, one plain sentence (about 80 characters) saying what
  the mode does. The Generate page's mode menu shows it under the mode name and
  `GET /api/presets/<id>/modes` serves it as `modes[].short_description` (the default variant's
  value; each `variants[]` entry carries its own). Over 100 characters → **warning**; a preset with
  two or more modes where a mode has neither `short_description` nor `description` → **info**.
- A mode's `pipeline.yml` may be the single line `pipeline_from: <mode>` instead of a `pipeline:`
  list. The loader then uses that sibling mode's pipeline, rendered against this mode's own form,
  so two modes that differ only in their form share one pipeline file. The target must be a
  sibling mode directory that has a `pipeline:` list of its own (no chain of pointers, no cycle,
  no other keys beside `pipeline_from`); anything else is a lint **error**. Goldens still render
  each mode separately.
- Users never see a raw mode key: everywhere the UI names a mode it shows a plain name, served as
  `modes[].label` (`name` stays the key). The name comes from, in order:
  1. `mode_label:` in the mode's own `modes/<mode>/form.yml` (not a variant's form; `label:` there
     names the form variant, not the mode). Set it only when the shared name would be wrong for
     your preset, e.g. MiniMax-H3's `refs` is "References to Video".
  2. The shared map in `src/features/presets/mode_names.json`, read by `mode_labels.py`
     (`txt2img` → "Text to Image", `img2video` → "Image to Video", `video_upscale` → "Video
     Upscale", …). The labels are also served at `GET /api/presets/mode-labels` for surfaces that only
     have a stored key (History, admin lists).
  3. The key title-cased, with `_` and `-` turned into spaces (`face_swap` → "Face Swap"). A key that
     falls through to this step with no `mode_label:` → **info**. Add it to the map if it is a
     well-known name, or give the mode a `mode_label:`.
- A mode can also carry an icon, served as `modes[].icon` and shown next to the name in the Generate
  page's mode menu only (never on the closed select). It resolves the same way: `mode_icon:` in
  `modes/<mode>/form.yml`, then the map's `icon` for the key (`txt2img` → `image`, `img2video` →
  `video`, `inpaint` → `brush`, `upscale` → `expand`, …), then no icon. The name must be one of the
  app's icons (`frontend/src/lib/utils/IconLibrary.ts`); an unknown `mode_icon:` → **warning**, and the
  menu shows that mode without an icon.
- `id` must be unique across the whole scanned tree (duplicate → error).
- Literal (non-templated) option-file paths referenced from a form must exist on disk (missing → warning).


## Reading order

Authoring a **native** preset from scratch? Start with the
[tutorial](presets/tutorial.md) — it walks the real eight-pipe Z-Image chain end to end and is the
fastest way to get the pipe names, config keys, and required inputs right on the first try. Come
back here for the rest, in the order most authors need them:

1. [Tutorial: Your first native preset](presets/tutorial.md) — scaffold, wire, lint, render.
2. [preset.yml Reference](presets/manifest.md) — the manifest: `vars:`, speed profiles, admin-set
   `configuration:`, media, hardware guidance, LLM chat context, prompt resources, and the
   [formula groups](presets/manifest.md#formulas) users can save and reapply.
3. [Forms](presets/forms.md) — `form.yml`, tabs, variants, field types, typed defaults, external
   option files, reactions, and the `bind_form`/422 validation contract.
4. [Pipelines](presets/pipelines.md) — `pipeline.yml`: pipe shape, `enabled:`, the three template
   contexts, strict evaluation, the `@loop` recipe, and the standard native chain with a
   per-family loader/generator table.
5. [Requirements](presets/requirements.md) — the live-checked `requirements:` list (as opposed to
   the static `requires:` guidance in the manifest reference above) and its effect on generation
   routing.
6. [ComfyUI Presets](presets/comfyui.md) — everything above still applies; what's different for
   `engine: comfyui`.
7. [Plugin-Contributed Presets](presets/plugins.md) — a plugin shipping its own preset, or
   contributing a mode onto a preset it doesn't own.
8. [Testing Presets](presets/testing.md) — `tests.yml`, the suite runner, and
   `scripts/preset_lint.py`.
9. [Troubleshooting](presets/troubleshooting.md) — every linter/build-error message class, with
   its cause and fix.

Reaching for a pipe's exact `name:`, inputs, or config keys, or a template global/filter/context
root, a `model_type` value, or a built-in field type? Those are the two generated reference pages
linked below, not this list — they're regenerated from the running code, so they're never stale.

## Prompt resources

A mode can let the prompt reference its media pickers: `prompt_resources:` in `preset.yml` maps a
field to a token such as `"<Picture @>"`, and a segment's `@[field:item key]` marker becomes that
token with `@` set to the item's current 1-based position in the field (or, for a `media` field
mapped once per kind, its position among that kind's items). A marker pointing at a
removed item or an unmapped field fails the submission with a field-level 422. Full contract:
[preset.yml Reference → Prompt resources](presets/manifest.md#prompt-resources).

## Model types in `model` fields

A `model` or `lora_picker` field's `model_type` must be one of the values listed in
[Preset Context](preset-context.md#model_type-values). `undefined` is not one of them: it is the
state of a file PotionUI could not classify ("Needs a type" in Admin → Models), and no field can
ask for it. A `diffusion_model` field on the native engine also lists full checkpoints of the
supported families; see [How a model's type is decided](models.md#how-a-models-type-is-decided).

## When the negative prompt applies

Many models only use the negative prompt under some settings: true CFG above 1, or NAG switched
on. A preset says when with `negative_prompt.applies_when`, a condition over the mode's form
fields written in the same `when:` grammar as [form reactions](presets/forms.md) (a single
`{field, <operator>: value}`, a list meaning AND, or `{logic: AND|OR, conditions: [...]}`, nesting
allowed), or a literal `false` for a mode whose pipeline never uses the negative. Put it at the
top of a mode's `form.yml`; a mode that declares none falls back to the same key in `preset.yml`.

```yaml
# modes/txt2img/form.yml
negative_prompt:
  applies_when:
    logic: OR
    conditions:
      - { field: cfg, greater_than: 1 }
      - logic: AND
        conditions:
          - { field: nag_enabled, equals: true }
          - { field: nag_scale, greater_than: 1 }
fields:
  ...
```

- **Generate page.** The condition is evaluated against the form's current values, after
  reactions. While it is false the negative prompt section is hidden; it comes back as soon as
  the condition holds. With no declaration the negative is always shown.
- **Generation record.** The same condition is evaluated against the bound form when the
  pipeline builds and is exposed as `generation.negative_applied` (`true`/`false`, `None` when
  undeclared). Wire it into the encoder so history marks an unused negative as not applied:

  ```yaml
  - name: "prompt_encoder"
    configuration:
      guidance_scale: "{{ form.cfg }}"
      negative_applied: "{{ generation.negative_applied }}"
  ```

  `negative_applied` only labels the record. Whether the negative is actually encoded still
  follows `guidance_scale`/`nag_scale`, so the declaration must describe what the pipeline does,
  not change it.
- **Lint.** A field in `applies_when` that the mode's form does not declare is an error. A
  declaration on a mode whose `prompt_encoder` has no `negative_applied` is a warning.

Write the condition from the pipeline, not from the field labels: read what feeds the encoder's
`guidance_scale` and `nag_scale`. A preset that always uses its negative (SDXL at its usual CFG)
declares nothing. A preset that never takes one sets `vars.supports_negative_prompt: false`
instead.

## Elixir contract

Elixirs chain preset modes together ([Elixirs](elixirs.md)), so every mode needs a contract:
which media it takes in, which media it produces, whether it takes a prompt. The contract is
derived from the mode itself, and an optional `elixir.yml` overrides it.

**Derivation.** For each mode the contract is built from the loaded form and pipeline:

- `label` is the mode's display name (`mode_label` in `form.yml`, else the standard name).
- `inputs` has one port per `image` or `video` field of the mode, found in any form variant. The
  port name is the field name, the label is the field's label in sentence case, the media type is
  the field type, and the target is `{kind: form_field, field: <name>}`. A field with
  `hidden_when_video_director: true` is skipped in a Video Director mode.
- `prompt` is true unless the mode is listed in `vars.promptless_modes`.
- `quantity_field` is the numeric field (`stepper`, `number`, `integer`, `slider`) that a pipe's
  `quantity:` reads through `form.<name>`. If several qualify, a field named `quantity` wins;
  otherwise none is set.
- `outputs` come from the `gallery` pipes that are always on: an `image` input gives an `images`
  port ("Final images"), a `video` input gives a `videos` port ("Final videos"), both with the
  selector `{is_final: true, is_derived: false}`. A gallery with `derived: true`, with
  `enabled: false`, or with an `enabled:` expression is not a final output.

A mode is not offered when the derivation is not confident: an `audio` or `media` field (no
elixir media type), a field of the same name with different types across variants, a field name
that clashes with an output port (`images`, `videos`), audio-only output, or no always-on
gallery that takes an image or video. `python scripts/preset_lint.py` (and
`GET /api/developer/presets/lint`) reports each one as an info line naming the reason.

**Override.** `modes/<mode>/elixir.yml` sits inside the mode directory, for core, local and
plugin presets alike:

```yaml
schema_version: 1
enabled: true
label: "Upscale video"
prompt: true
quantity_field: "quantity"
inputs:
  input_video:
    label: "Clip to upscale"
  mask: null
  first_frame:
    label: "First frame"
    media_type: "image"
    target: {kind: "video_director_role", role: "first"}
outputs:
  videos:
    label: "Upscaled videos"
```

Every key is optional except `schema_version` (always `1`). Scalars replace the derived value;
`quantity_field: null` removes it. `inputs` and `outputs` merge port by port: a listed port
overrides only the keys it names (a `target` or `selector` is replaced whole), `null` removes a
derived port, and a port that was not derived must name every key (`label`, `media_type`, plus
`target` for inputs). Naming `inputs` or `outputs` at all makes you the owner of that side, so
a derivation problem there no longer hides the mode. `enabled: false` hides the mode.

The file is validated with the same models as the contract. An invalid `elixir.yml`, or a target
that names a field the mode does not have, is a lint error and the mode is not offered. The
Video Director targets (`kind: video_director_role`, `role: first|last`) need a mode listed in
`vars.video_director.preset_modes`, and the media type must be `image`.

## Regional prompting

`vars.regional_prompt` offers the layout editor on the generate page for the listed modes:

```yaml
vars:
  regional_prompt:
    modes: [txt2img]
    size_field: resolution
```

`modes` are mode ids of the preset and `size_field` is the `resolution` field the canvas takes its shape from. The option names no prompt format. The client sends `form_data.regional_prompt` as `{enabled, items: [{x, y, w, h, caption}]}` with `x`, `y`, `w`, `h` as fractions of the canvas from the top-left corner; the form binder keeps it only for a declared mode and drops it otherwise. The mode's pipeline must contain a `regional_prompt/*` stage, which turns the document into whatever the model reads; the linter reports a missing mode, a missing stage and a `size_field` that is not a `resolution` field of that mode. Wire the stage before `prompt_encoder` with `enabled: "{{ (form.regional_prompt | default({})).enabled | default(false) }}"` and feed its `pairs` output to the encoder's optional `pairs` input: the encoder falls back to its configured `pairs` when the stage did not run.

## Display names and mirrors (admin-set)

Admins can change how an installed preset is presented and can offer one preset's files under
several entries. Neither touches the YAML: both live on the installed preset's row in the
`presets` table and are laid over the scanned templates by the loader
(`src/features/presets/overlays.py`).

**Display overrides.** Any installed preset (a preset file or a mirror) can carry a display
`name` (1-80 characters), `description` (up to 4000) and `cover`. Each replaces the YAML value
when set; `null` falls back to it. The cover is either a `public/...` file of the preset or an
uploaded file (`display/<id>.<ext>`: PNG, JPEG or WebP up to 10 MB, MP4 or WebM up to 1.5 MB),
stored under `<storage>/preset_display/<row id>/` and served at
`/api/media/presets/{preset_id}/display/<file>`. Every screen reads the name from the template,
so a rename shows up everywhere, including old generations. Uninstalling a preset drops its
display overrides and uploaded cover.

**Mirrors.** A mirror is an installed preset with its own generated id that uses another
installed preset file's folder. The loader registers a copy of the source template under the
mirror's id with the mirror's name, description and cover, the modes the mirror offers (all by
default) with its first mode first, and only the requirements of those modes. Everything else
(pipelines, forms, styles, vars, engine, plugin-contributed modes) is the source's, so the
mirror follows every update of the source files. A new mirror starts with a copy of the source's
configuration and form overrides; from then on they are its own, which is how "Anime" gets a
different default model or LoRA list than "Realistic". Like any preset, a new mirror is visible
to nobody until it is assigned to users or groups.

Rules:

- The source must be an installed preset file, never another mirror.
- A preset file that has mirrors cannot be uninstalled until its mirrors are removed
  (`409 preset_has_mirrors`).
- Removing a mirror deletes its row and assignments and keeps a tombstone with its name, so
  history still shows what a generation was made with.
- If the source files disappear outside the admin panel (plugin disabled, files deleted), the
  mirror stays in the admin list as `source_missing` and is hidden from users. A mirror whose
  offered modes all vanished is `modes_missing`.

Things that describe the files, not the label, look through a mirror to its source
(`files_preset_id` in `src/features/presets/templates.py`, also exported by
`src.plugin_api.presets`): model scopes, recipes and install modes, styles and style previews,
and the media thumbnail cache. Requirements are checked on the mirror itself, because its
defaults can name other files than the source's. A mirror gets no derived install recipe of its
own, and a derived recipe id comes from the preset file's YAML `name`, so renaming a preset
keeps its recipe history.

Admin API (all admin-only): `GET /api/presets/{id}/admin`, `PUT /api/presets/{id}/display`,
`POST /api/presets/{id}/display/cover`, `POST /api/presets/{id}/mirrors`,
`PUT /api/presets/{id}/modes`.

## See also

- [Backends and Engines](backends.md) — `engine:`, how a preset's pipes get executed.
- [Providers](providers.md) — how presets get the models they reference without declaring downloads.
- [Video Director](video-director.md) — `vars.video_director`, the composition-mode capability
  declaration for native video presets.
- [Hardware Requirements](user/hardware-requirements.md) — the measured per-family VRAM/RAM table
  `requires:` values must be sourced from.
