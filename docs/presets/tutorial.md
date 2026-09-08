---
title: "Tutorial: Your First Native Preset"
category: Presets / Models
category_order: 70
order: 14
---

# Tutorial: Your First Native Preset

An end-to-end walkthrough of a native `txt2img` preset, built the way the shipped
`content/presets/marketplace/ZImage/` preset actually is: three tabs (Generation, LoRA, Advanced)
and the real eight-pipe chain. Every pipe name and config key below comes from the generated
[Pipes Reference](../pipes.md) — this tutorial exists because guessing them (a plausible-looking
`cfg` instead of the real `guidance`, a made-up `text_encoder_loader` pipe that doesn't exist, a
missing `prompt_encoder`) is the single most common way a hand-authored native preset fails to
build. If you only read one page before writing a preset, read this one, then keep
[Pipes Reference](../pipes.md) open in a second tab while you work.

## What you're building

A minimal but complete Z-Image `txt2img` preset: a scaffold, a real pipeline wired pipe-by-pipe, a
three-tab form, and a passing lint + render. It is deliberately smaller than the shipped ZImage
preset (no step-cache, no spectral-progressive knob, one resolution group instead of three) — once
this works, [Pipelines](pipelines.md) and [Forms](forms.md) cover everything left out.

## 1. Scaffold it

```bash
python scripts/preset_new.py MyZImage/standard --family z_image --root content/presets/local
```

`--family z_image` (see [Pipelines → The standard native chain](pipelines.md#the-standard-native-chain))
looks up the real `model_loader/z_image` and `generator/z_image` pipes in the pipe catalog and
scaffolds the standard eight-pipe chain wired to them, plus a Generation tab with seed, quantity,
resolution, and the family's model pickers — already using the right `model_type` values and the
generator's real config keys/defaults. Omit `--family` (or pass an unrecognized one) and you get
the older bare skeleton — one `gallery` pipe and a placeholder comment — which is what you scaffold
for a brand-new pipe family that doesn't exist yet.

This writes to `content/presets/local/MyZImage/standard/` (`.gitignored` — see
[Canonical layout](../presets.md#canonical-layout)) so nothing here touches the shipped tree.

## 2. The pipeline

The scaffold's `modes/txt2img/pipeline.yml` already has this chain wired; here's what each pipe
does and why it's in this order, cross-referenced against [Pipes Reference](../pipes.md):

```
model_loader/z_image → prompt_encoder → seed_generator → dynamic_prompts_renderer
  → from_iotype → param_emitter → generator/z_image → gallery
```

**`model_loader/z_image`** — loads the checkpoint set. Its required config
(`diffusion_model`/`text_encoder`/`vae`, each `{file_path, name}`) comes straight from the form's
model pickers; `loras` is a list built by an `@loop` over the `lora_picker` field's value (see
[Pipelines → The `@loop` recipe](pipelines.md#the-loop-recipe-repeated-lora--controlnet-slots)).
Outputs `model` (consumed by the generator) and `text_encoder` (consumed by `prompt_encoder` —
this is the one pipe-to-pipe dependency that isn't obvious from the chain diagram alone):

```yaml
- name: "model_loader/z_image"
  enabled: true
  configuration:
    diffusion_model: { file_path: "{{ form.diffusion_model }}", name: "{{ form.diffusion_model }}" }
    text_encoder: { file_path: "{{ form.text_encoder }}", name: "{{ form.text_encoder }}" }
    vae: { file_path: "{{ form.vae }}", name: "{{ form.vae }}" }
    loras:
      "@loop":
        items: "{{ form.loras }}"
        template:
          file_path: "{{ item.model }}"
          weight: "{{ item.strength }}"
```

**`prompt_encoder`** — turns the prompt into `conditioning`. It needs `model_loader/z_image`'s
`text_encoder` output wired in via `input:` (this is a **required** input — the pipe cannot build
this conditioning itself, it only encodes what a text encoder gives it); `p_prompt`/`n_prompt` read
the first expanded prompt pair, `pairs` carries every per-image expansion, and `guidance_scale`
mirrors the generator's own CFG value (Z-Image's true-CFG needs the negative encoded whenever
guidance is above 1.0):

```yaml
- name: "prompt_encoder"
  input:
    - ["text_encoder", "model_loader/z_image", "text_encoder"]
  enabled: true
  configuration:
    p_prompt: { input: "{{ generation.prompts.first.positive }}", output: "{{ generation.prompts.first.positive }}" }
    n_prompt: { input: "{{ generation.prompts.first.negative }}", output: "{{ generation.prompts.first.negative }}" }
    pairs: "{{ generation.prompts.pairs }}"
    quantity: "{{ form.quantity }}"
    guidance_scale: "{{ form.cfg }}"
```

**`seed_generator`** — emits `seed` from `form.seed`/`form.quantity`. No inputs; every preset
copies this pipe verbatim.

**`dynamic_prompts_renderer`** — a provenance pipe: surfaces the same expanded `pairs` as a pipe
artifact so History records exactly what each image's prompt actually rendered to (`{a|b}`
choices, `${var}` substitutions). It produces no config the generator consumes — it's parallel to
the chain, not upstream of it.

**`from_iotype`** — converts the `seed_generator` pipe's `SEED`-typed output into a plain value
`param_emitter` can record. `input: [["seed", "seed_generator", "seed"]]`, `configuration: {from: "seed"}`.

**`param_emitter`** — records every generation parameter (models, prompts, CFG, steps, sampler,
resolution) as `[key, value]` pairs for History, reading the same `from_iotype`-converted seed:

```yaml
- name: "param_emitter"
  input:
    - ["seed", "from_iotype", "seed"]
  enabled: true
  configuration:
    quantity: "{{ form.quantity }}"
    parameters:
      - ["model", "{{ form.diffusion_model }}"]
      - ["model", "{{ form.text_encoder }}"]
      - ["model", "{{ form.vae }}"]
      - ["positive_prompt", "{{ generation.prompts.positives }}"]
      - ["negative_prompt", "{{ generation.prompts.negatives }}"]
      - ["cfg", "{{ form.cfg }}"]
      - ["steps", "{{ form.steps }}"]
      - ["resolution", "{{ form.resolution }}"]
```

**`generator/z_image`** — the pipe that actually samples. Takes `model` (from `model_loader/z_image`)
and `conditioning` (from `prompt_encoder`) as **required** inputs, plus `seed`. Its config keys —
verified against [Pipes Reference → `generator/z_image`](../pipes.md#pipe-generator-z-image), not
guessed — are `steps` (int, default `8`), `guidance` (float, default `1.0` — **not** `cfg`, that
name belongs to the form field, the pipe's own key is `guidance`), `sampler` (default `"euler"`),
`resolution` (default `"1024x1024"`), and `quantity`:

```yaml
- name: "generator/z_image"
  input:
    - ["model", "model_loader/z_image", "model"]
    - ["conditioning", "prompt_encoder", "conditioning"]
    - ["seed", "seed_generator", "seed"]
  enabled: true
  configuration:
    steps: "{{ form.steps }}"
    guidance: "{{ form.cfg }}"
    sampler: "{{ form.sampler }}"
    resolution: "{{ form.resolution }}"
    quantity: "{{ form.quantity }}"
```

**`gallery`** — saves the output. Takes `image` from the generator and `seed` from
`seed_generator`; no `configuration:` needed (there's no `mode: "save"` key — the pipe always
saves).

## 3. The form: three tabs

`modes/txt2img/form.yml` is a `tabs` container pulling in three external tab files — this is the
shape every shipped preset with more than a couple of fields uses (see
[Forms → Modes and the form root](forms.md#modes-and-the-form-root)):

```yaml
name: "custom"
fields:
  - type: "tabs"
    children:
      - type: "tab"
        label: "Generation"
        children: "{{ paths.preset }}/modes/txt2img/tabs/generation.yml"
      - type: "tab"
        label: "LoRA"
        children: "{{ paths.preset }}/modes/txt2img/tabs/lora.yml"
      - type: "tab"
        label: "Advanced"
        audience: "advanced"
        children: "{{ paths.preset }}/modes/txt2img/tabs/advanced.yml"
```

**`tabs/generation.yml`** — seed + quantity, resolution, and the three required model pickers.
`model_type` values (`diffusion_model`/`text_encoder`/`vae`) come from the generated
[`model_type` values table](../preset-context.md#model_type-values), not guesswork — a wrong
`model_type` means the picker lists the wrong depot folder's files. (The shipped ZImage preset
also has a `speed_profile` picker feeding `get_speed_profile()`/`generation.profile` — see
[preset.yml Reference → Speed profiles](manifest.md#speed-profiles) — left out here to keep this
first pass to one concept at a time; every field below reads straight off `form.<name>` instead.)

```yaml
fields:
  - type: "row"
    children:
      - { name: "seed", type: "seed", label: "Seed", default: -1, width: "3/5" }
      - { name: "quantity", type: "stepper", label: "Quantity", default: 1, configuration: {min: 1, max: 10, step: 1}, width: "2/5" }

  - name: "resolution"
    type: "resolution"
    label: "Resolution"
    configuration:
      files:
        - { path: "{{ paths._shared }}/resolutions/sdxl.yml", group: "Standard" }
    default: "1024x1024"

  - name: "diffusion_model"
    type: "model"
    label: "Diffusion Model"
    required: true
    configuration: { model_type: "diffusion_model", placeholder: "Select a Z-Image NextDiT checkpoint..." }

  - name: "text_encoder"
    type: "model"
    label: "Text Encoder"
    required: true
    configuration: { model_type: "text_encoder", placeholder: "Select the Z-Image text encoder..." }

  - name: "vae"
    type: "model"
    label: "VAE"
    required: true
    configuration: { model_type: "vae", placeholder: "Select the Z-Image VAE..." }
```

**`tabs/lora.yml`** — one `lora_picker` field. Its submitted value is exactly what
`model_loader/z_image`'s `loras` `@loop` above consumes:

```yaml
fields:
  - name: "loras"
    type: "lora_picker"
    default: []
    label: "LoRAs"
    configuration: { model_type: "lora", max_items: 3 }
```

**`tabs/advanced.yml`** — `audience: "advanced"` on the tab (set above) hides this whole tab
behind a toggle; individual fields don't need their own `audience:` unless they should be hidden
independently of the tab. Steps, sampler, and CFG, each with a `| default(...)`-free binding since
they're declared with a static `default:`:

```yaml
fields:
  - name: "steps"
    type: "slider"
    label: "Steps"
    default: 8
    configuration: { min: 1, max: 100, step: 1 }

  - name: "sampler"
    type: "select"
    label: "Sampler"
    default: "euler"
    configuration:
      options:
        - { label: "Euler", value: "euler" }
        - { label: "DPM++ 2M", value: "dpmpp_2m" }

  - name: "cfg"
    type: "slider"
    label: "CFG Scale"
    default: 1.0
    configuration: { min: 0, max: 30, step: 0.1 }
```

## 4. Lint and render it

```bash
python scripts/preset_lint.py content/presets/local/MyZImage/standard
```

Zero errors means the manifest validates, every declared mode has a directory, and every
`{{ form.<name> }}` reference in `pipeline.yml` resolves against the form tree above. Then render
the real pipeline against fixture form data — no live generation, no GPU, no model download
needed — to see every pipe config value exactly as it would render at generation time:

```bash
python scripts/preset_render.py content/presets/local/MyZImage/standard txt2img
```

A `StrictUndefined` build error here (see
[Troubleshooting → Runtime build errors](troubleshooting.md#runtime-build-errors)) almost always
means a `{{ form.<name> }}` reference to a field that isn't in the form tree, or is missing a
`| default(...)` for a field with no static `default:`.

## 5. Ship it

Move `content/presets/local/MyZImage/standard/` to `content/presets/marketplace/MyZImage/standard/`
when it's ready to be shipped (its `id:` doesn't change — see
[Canonical layout](../presets.md#canonical-layout)). Add a `tests.yml` next to `preset.yml` (see
[Testing Presets](testing.md)) so a real generation is checked automatically going forward, and add
`media:`/`requires:` (see [preset.yml Reference](manifest.md)) once you have a real cover image and
a measured VRAM floor.

## Where to go next

- [Pipelines](pipelines.md) — every pipe's config in depth, `@loop`, speed-profile precedence.
- [Forms](forms.md) — reactions, variants, the full field-type catalog.
- [preset.yml Reference](manifest.md) — `configuration:`, `llm:`, hardware guidance.
- [Pipes Reference](../pipes.md) — the generated source of truth this tutorial cross-checks
  against; use it for any family other than Z-Image.
