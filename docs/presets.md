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

### Reference pages

This guide is split across several pages under `docs/presets/` — see
[Reading order](#reading-order) below for what to read first. Two generated pages back all of
them: [Pipes Reference](pipes.md) (every pipe's `name:` key, inputs, outputs, and configuration)
and the [Preset Context Cheat Sheet](preset-context.md) (every template global/filter/context
root, `model_type` value, built-in field type, and `_shared` option file) — both regenerated from
the running code by `python scripts/pipes_reference.py` and drift-checked in CI. The same data,
read live against a running install's actually enabled plugins, is also in-app under Help →
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

Shared vocabulary (resolutions, ComfyUI samplers/schedulers) lives once under
`content/presets/_shared/` and is referenced from any preset via `{{ paths._shared }}` (see
[External option files](presets/forms.md#external-option-files)).

Rules the loader (`src/features/presets/loader.py`) and linter (`src/features/presets/linter.py`) enforce:

- `preset.yml` is required and must validate.
- Every mode name in `preset.yml` `modes:` **must** have a matching `modes/<mode>/` directory
  (missing directory → lint **error**). A `modes/<mode>/` directory not listed in `modes:` is a
  **warning** (orphan).
- `id` must be unique across the whole scanned tree (duplicate → error).
- Literal (non-templated) option-file paths referenced from a form must exist on disk (missing → warning).


## Reading order

Authoring a **native** preset from scratch? Start with the
[tutorial](presets/tutorial.md) — it walks the real eight-pipe Z-Image chain end to end and is the
fastest way to get the pipe names, config keys, and required inputs right on the first try. Come
back here for the rest, in the order most authors need them:

1. [Tutorial: Your first native preset](presets/tutorial.md) — scaffold, wire, lint, render.
2. [preset.yml Reference](presets/manifest.md) — the manifest: `vars:`, speed profiles, admin-set
   `configuration:`, media, hardware guidance, LLM chat context.
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

## See also

- [Backends and Engines](backends.md) — `engine:`, how a preset's pipes get executed.
- [Providers](providers.md) — how presets get the models they reference without declaring downloads.
- [Video Director](video-director.md) — `vars.video_director`, the composition-mode capability
  declaration for native video presets.
- [Hardware Requirements](user/hardware-requirements.md) — the measured per-family VRAM/RAM table
  `requires:` values must be sourced from.
