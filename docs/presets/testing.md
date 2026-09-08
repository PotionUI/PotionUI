---
title: Testing Presets
category: Presets / Models
category_order: 70
order: 21
---

# Testing Presets

`tests.yml` end-to-end cases, the standalone suite runner, and `scripts/preset_lint.py`.
For `scripts/preset_render.py --root` (rendering a pipeline against fixture form data without
a live generation), see the [tutorial](tutorial.md#lint-and-render-it). For what each linter
message means and how to fix it, see [Troubleshooting](troubleshooting.md).

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


## Linting (CLI)

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
defaults (see [Troubleshooting](troubleshooting.md)), media/variant/speed-profile cross-checks, and — if the preset
ships one — that its `tests.yml` is well-formed (see "Testing presets" above). On the default run
(no explicit paths) it also cross-checks every discovered plugin's `preset_modes:` contributions
against the presets found under the scanned trees — the same collision rules [Plugin-Contributed Presets](plugins.md) documents, so a rejected/colliding contribution shows up here without booting the app;
passing explicit paths skips this cross-check, same as it already skips plugin-owned `presets:`
roots. `--fix` performs a comment-preserving migration of legacy `preset.yml` files (adds
`schema: 1`, converts `modes:` mapping → list, moves inline `description:` to `description.md`,
writes an explicit `engine:`, infers `category:`, deletes dead keys); the inferred `category` is
always printed for human review.

The same check is exposed over HTTP at **`GET /api/developer/presets/lint`** for the developer UI.

