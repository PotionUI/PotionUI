---
category: Editor
category_order: 72
order: 10
---

# Photo Filters

A **filter** is a recipe that restyles a photo: an optional LUT, then colour steps, then spatial steps,
scaled by an intensity. The image editor is the first consumer. The same recipe also runs on the server
(`src/platform/imaging/filters/`), so a batch tool, a post-processing pipe or video can apply it later and
get the same picture.

This is the authoritative reference. The code it describes:

- Python engine: `src/platform/imaging/filters/` (torch-free: numpy and PIL only).
- TypeScript engine: `frontend/src/lib/filters/engine/` (pure, no DOM, no Svelte).
- Catalog, user filters and routes: `src/features/filters/`.
- Lint: `scripts/filter_lint.py`, shared implementation in `src/features/filters/lint.py`.

## The model

```
lut? -> colour steps -> spatial steps         (then intensity k)
```

Colour steps are pointwise (`rgb -> rgb`), so the engine folds the optional `.cube` and every colour step
into **one 3D LUT**. Spatial steps need position or noise (`vignette`, `grain`) and run on the pixels after
the LUT, in listed order.

An op is **colour** only if each output pixel depends on that pixel's RGB alone. Anything else is
**spatial**. A filter lists colour steps before spatial steps; lint and the API reject a colour step that
follows a spatial one, so a file never promises an order the engine will not honour.

Values are sRGB-encoded floats `v` in 0..1 unless an op says "linear light". `Y = 0.2126 R + 0.7152 G +
0.0722 B` on encoded values. Every colour step clamps its result to 0..1. Alpha is never touched.

## Where filters live

| Source | Where | Public id | Written by |
|---|---|---|---|
| `builtin` | `content/filters/marketplace/<name>/filter.yml` (tracked) | `<name>` | shipped with PotionUI |
| `local` | `content/filters/local/<name>/filter.yml` (git-ignored) | `<name>` | you, by hand on disk |
| `plugin` | `<plugin>/filters/<name>/filter.yml`, declared by `filters:` in `manifest.yml` | `<plugin_id>:<name>` | a plugin |
| `mine` | the `user_filters` table, one set per user | `mine:<ulid>` | the user, through **Save as filter** |

Rules:

- The app never writes into `content/`. File filters are read-only; only a user's own rows in the database
  change.
- `<name>` is the directory name and must equal `id:`. Ids match `^[a-z0-9][a-z0-9-]{0,39}$` and never
  contain `:` (that character belongs to plugin and user ids).
- Marketplace and local share one flat namespace. A local filter with the same id **overrides** the
  marketplace one; the API marks it `overrides: true`.
- A **disabled plugin's filters are not scanned**. A filter that uses an op from a disabled plugin (a local
  file filter or one of your own) stays in the list, flagged with `unavailable_ops` and `needs_plugin`, so
  the editor can show it dimmed with the plugin's name.
- Limits: `filter.yml` 64 KB, `lut.cube` 8 MB, at most 64 steps.

### `filter.yml`

```yaml
schema: 1                      # required, only 1 is supported
id: ember                      # required, equals the directory name
name: Ember                    # required, shown under the thumbnail (24 characters fit)
description: Warm skin and wood, a lifted midtone curve, a soft vignette.
group: Colour                  # strip group; built-in groups are Colour, Film, Black & white
order: 20                      # sort inside the group; ties break on name
intensity: 100                 # default slider value 0-100
tags: [warm, portrait]
author: PotionUI
license: MIT                   # REQUIRED when lut: is set
credit: ""                     # optional attribution line
lut: lut.cube                  # optional, applied first
steps:
  - op: white_balance
    temperature: 34
    tint: 6
  - op: curves
    master: [[0, 0.02], [0.25, 0.22], [0.75, 0.80], [1, 0.98]]
  - op: vignette
    amount: 22
  - op: grayscale
    enabled: false             # any step may be switched off
```

Params are flat keys; an omitted param takes the op's default. `GET /api/filters/schema` serves the JSON
schema for the structure. Per-op parameter checks come from the op registry below, so plugin ops validate
too.

## Op catalogue

<!-- filter-ops:start -->
| id | kind | params (range, default) |
|---|---|---|
| `tone` | colour | `brightness` -100..100 (0), `contrast` -100..100 (0), `saturation` -100..100 (0), `hue` -180..180 (0) |
| `exposure` | colour | `stops` -2..2 (0) |
| `white_balance` | colour | `temperature` -100..100 (0), `tint` -100..100 (0) |
| `curves` | colour | `master`, `r`, `g`, `b`: each a curve of 2..16 points (default `[[0,0],[1,1]]`) |
| `vibrance` | colour | `amount` -100..100 (0) |
| `split_tone` | colour | `shadow_hue` 0..360 (0), `shadow_sat` 0..100 (0), `highlight_hue` 0..360 (0), `highlight_sat` 0..100 (0), `balance` -100..100 (0) |
| `fade` | colour | `black_lift` 0..40 (0), `white_cap` 0..30 (0) |
| `grayscale` | colour | none |
| `invert` | colour | none |
| `vignette` | spatial | `amount` -100..100 (0), `midpoint` 0..100 (50), `feather` 0..100 (60) |
| `grain` | spatial | `amount` 0..100 (0), `size` 0.5..4 (1), `seed` 0..65535 (0) |
<!-- filter-ops:end -->

Integer params must be whole numbers. A test (`tests/features/filters/test_docs_catalogue.py`) fails when
this table drifts from the registry.

What each op does:

- **tone**: the image editor's existing Adjust tone, ported to float. Brightness is a gain `1 + b/100` with a
  clamp; contrast scales around 0.5 with a clamp; then the saturation matrix (0.213 / 0.715 / 0.072
  weights); then the hue-rotate matrix. The op map is pinned to the legacy `applyTone` kernel within one 8-bit
  level on every channel (`engine.test.ts`).
- **exposure**: gain `2^stops` in linear light.
- **white_balance**: linear-light channel gains. With `t = temperature/100` and `n = tint/100`:
  `gR = 2^(0.35t) 2^(0.125n)`, `gG = 2^(-0.25n)`, `gB = 2^(-0.35t) 2^(0.125n)`, all divided by `Y(gR, gG, gB)`
  so mid-grey luminance is kept. Positive temperature is warmer.
- **curves**: the per-channel curve, then the master curve. Interpolation is monotone cubic Hermite (PCHIP,
  Fritsch-Carlson) and is extended linearly outside the end points. Points are `[x, y]` pairs inside 0..1 with
  `x` strictly increasing.
- **vibrance**: saturation weighted toward muted colours. With `a = amount/100`, `s = max(rgb) - min(rgb)` and
  `f = max(0, 1 + a (1 - s))`: `c' = Y + (c - Y) f`.
- **split_tone**: luma-preserving tint. The tint vectors are `rgb(hue, s=1, l=0.5)` minus their own luma;
  `wh = smoothstep(0, 1, clamp(Y - balance/200))`, `ws = 1 - wh`;
  `c' = c + 0.5 (ws * sat_s * tint_s + wh * sat_h * tint_h)`.
- **fade**: `c' = lift + c (1 - lift - cap)`.
- **grayscale**: `c' = (Y, Y, Y)`. **invert**: `c' = 1 - c`.
- **vignette**: `r = hypot(x - W/2, y - H/2) / hypot(W, H) * 2` at pixel centres (corner = 1);
  `w = smoothstep(m, m + max(0.05, f), r)`. A positive amount darkens, `v' = v (1 - a w)`; a negative amount
  lightens, `v' = v + (1 - v)(-a w)`. It does not depend on resolution.
- **grain**: luma grain, the same noise on R, G and B. The cell edge is `max(1, round(size * min(W, H) / 1000))`
  pixels (rounding half up), so grain looks the same on a thumbnail and the full image. Per cell,
  `h = lowbias32(imul(cx, 73856093) ^ imul(cy, 19349663) ^ imul(seed + 1, 83492791))` and
  `n = h/2^32 + lowbias32(h ^ 0x9e3779b9)/2^32 - 1` (triangular, -1..1). Then `mid = 1 - 0.7 (2Y - 1)^2` and
  `v' = v + n * (amount/100) * 0.14 * mid`. All integer math is 32-bit unsigned, identical in JavaScript
  (`Math.imul`, `>>>`) and numpy (`uint32`).

## Intensity

Intensity `k` is 0..100 on the wire and in both engines' public functions (`k = intensity / 100` inside).
It is defined, not blended after the fact: the LUT is lerped toward identity, `node' = id + k (node - id)`,
and every spatial `amount` is multiplied by `k`. At 0 the output equals the input; at 100 it is the full
recipe. For colour steps this is exactly a blend with the original, and a slider drag only recompiles a
35,937-node table instead of re-running the ops over pixels.

## LUT (`.cube`) files

Supported subset of the Adobe/Resolve text format:

- `LUT_3D_SIZE n` with `n` from 2 to 65. 17, 33 and 65 are the usual sizes; others are accepted and
  resampled, and lint warns (`filter.lut_size`).
- `TITLE`, `#` comments and blank lines are ignored.
- `DOMAIN_MIN` / `DOMAIN_MAX` remap the input, `u = (v - min) / (max - min)`, clamped.
- Data rows are `r g b` floats with **red varying fastest**, then green, then blue.
- `LUT_1D_SIZE` and `LUT_3D_INPUT_RANGE` are rejected.
- Values are read as encoded, display-referred sRGB. Log-to-display camera LUTs are not supported.

The LUT is stage 0: the filter's base look, with `steps` as fine-tunes on top. Compilation starts from the
cube, not from identity. The lattice is `max(33, cube size)`, so a 17-point cube is upsampled to 33 and a 33
or 65 cube reproduces itself exactly. The `.cube` is never altered on disk.

A filter that ships a `.cube` must declare `license:`; built-in filters ship none.

## Compile and apply

```
compile(steps, cube, k):
  size = max(33, cube.size or 0)
  for each lattice node (red fastest):
      c = node / (size - 1)
      if cube: c = sample_cube(cube, c)               # tetrahedral
      for step in enabled colour steps: c = clamp01(step.map(c))
      node' = id + k (c - id)
apply(pixel):
  x = v/255 * (size - 1); tetrahedral interpolation; clamp; round half up to uint8
  then each enabled spatial step, amounts times k
```

Tetrahedral interpolation (the six-tetrahedron split) keeps neutral greys neutral on a 33 grid and matches
ffmpeg `lut3d` and Resolve. Numeric type is float32 for the stored lattice on both sides.

The compiled LUT is an approximation of the ops where an op has a kink between lattice nodes (a clamp in
`tone` contrast, for example): the LUT path stays within a few levels of evaluating the ops per pixel, while
the op maps themselves are pinned tightly (the `tone` map to the legacy kernel within one level).

## Parity between the browser and Python

Both engines implement exactly the algorithm above, pinned by shared golden fixtures in
`tests/fixtures/filters/golden/*.json`, generated by `tests/fixtures/filters/generate_golden.py` from the
Python engine (a test fails when the committed files differ from a fresh generation). They cover every op, the
twelve built-in filters at intensity 0, 50 and 100, spatial ops on larger images, `.cube` parsing (good and
bad), and the grain hash.

- Colour and vignette: applied 8-bit output differs by at most 1 level; compiled LUT nodes match within `1e-4`.
- Grain: **exactly equal** (integer hash).

`tests/platform/imaging/test_filter_golden_parity.py` and
`frontend/src/lib/filters/engine/engine.test.ts` read the same files. After changing an op, regenerate with
`python tests/fixtures/filters/generate_golden.py` and commit the result.

## The browser engine

`frontend/src/lib/filters/engine/` exports:

```ts
compileLut(steps, cube?, intensity = 100, extensions?): Lut
applyFilter(imageData, filter: { steps, cube? }, intensity = 100, options?: { lut?, extensions? }): imageData
applyLut(data: Uint8ClampedArray, lut): void
applySpatial(data, width, height, steps, intensity = 100, extensions?): void
parseCube(text): Cube              // throws CubeError
identityLut(size = 33): Lut
OPS, OPS_BY_ID, getOp(id, extensions?), resolveValues(spec, step), splitSteps(steps, extensions?)
```

`applyFilter` edits `imageData.data` in place (RGBA, alpha untouched) and returns the same object. Passing
`options.lut` skips compiling, which is how a slider drag reuses a table.

## Your own filters (database)

**Save as filter** is available to every user and never touches the file system. User filters are private
rows in `user_filters`, owned by one user:

- Steps only: they use the same step schema and the same validator (`validate_steps`) as `filter.yml`.
  A filter based on a LUT cannot be saved yet; the API answers `422 filter_lut_unsupported`.
- Names are 1 to 24 characters, unique per owner ignoring case (`409 filter_name_taken`).
- At most 100 per owner (`409 filter_limit_reached`), enforced in the insert statement.
- Another user's id answers `404`, never `403`.
- A user filter may use a plugin op. When that plugin is disabled the row stays and is flagged
  `unavailable_ops`.

## API

All routes need a signed-in user. There is no admin gate and no file write.

| Method | Path | Result |
|---|---|---|
| GET | `/api/filters` | the merged list (below), as plain JSON |
| GET | `/api/filters/schema` | the `filter.yml` JSON schema |
| GET | `/api/filters/{id}/lut` | the raw `.cube` of a file filter, with `ETag` (404 without a LUT) |
| GET | `/api/filters/mine` | the caller's filters, in the list item shape |
| POST | `/api/filters/mine` | `{name, description?, group?, intensity?, steps, source_id?}` creates one |
| PATCH | `/api/filters/mine/{id}` | any of `name`, `description`, `group`, `intensity`, `steps` |
| DELETE | `/api/filters/mine/{id}` | removes it |

`/mine` is registered before `/{id}/lut`. The `/mine` routes answer in the standard `{success, data}`
envelope; `GET /api/filters` and the LUT and schema routes answer with the bare payload. Errors use the
standard `{detail: {error, message}}` body. A `mine:` prefix on the id is accepted and optional.

`GET /api/filters` returns:

```json
{
  "schema": 1,
  "filters": [
    {
      "id": "ember", "name": "Ember", "description": "...", "group": "Colour", "order": 20,
      "intensity": 100, "tags": ["warm"],
      "source": "builtin", "plugin_id": null, "overrides": false, "owned": false,
      "author": "PotionUI", "license": "", "credit": "",
      "has_lut": false, "lut_size": null, "lut_url": null,
      "steps": [{"op": "white_balance", "temperature": 34, "tint": 6}],
      "kinds": {"colour": 3, "spatial": 1},
      "unavailable_ops": [], "needs_plugin": null, "backend_ok": true,
      "revision": "c1f4a9d2"
    }
  ],
  "ops": [{"id": "tone", "label": "Tone", "kind": "colour", "source": "core", "plugin_id": null,
           "params": [{"id": "brightness", "label": "Brightness", "type": "int",
                       "min": -100, "max": 100, "default": 0, "unit": null}]}],
  "groups": ["Colour", "Film", "Black & white", "Mine"],
  "load_errors": {"local/bad-one": ["steps[2].op: unknown op 'foo'"]}
}
```

- `source` is `builtin`, `local`, `plugin` or `mine`; `owned` is true only for the caller's own filters.
- `revision` is 8 hex characters: a sha1 of the steps (plus the cube for LUT filters, plus the intensity for
  your own filters). It is the cache key for thumbnails and the `ETag` of the LUT.
- `backend_ok` is false when some op has no Python implementation or is unavailable.
- `ops` lists the core ops and the ops of enabled plugins. A curve param has `type: "curve"`, no range, and a
  point list as its default.
- `load_errors` is keyed `<root>/<directory>` (for example `local/bad-one`), never a server path.
- The catalog rescans when a filter file, a `.cube`, a root directory or the set of enabled plugins changes.

## Plugins

A plugin ships filters and new ops. See [The Plugin API](plugin-api.md#contributing-photo-filters-and-filter-ops).

```yaml
filters:
  - path: filters          # <plugin>/filters/<name>/filter.yml

filter_ops:
  - id: retro-tape.scanlines     # must be "<plugin-id>.<op>"; core ops never contain a dot
    label: Scanlines
    kind: spatial                # colour | spatial
    params:
      - { id: strength, label: Strength, type: int, min: 0, max: 100, default: 30 }
    python: ops.py:Scanlines     # optional; absent means editor only
```

## Lint

`python scripts/filter_lint.py [root ...]` lints the file filters (marketplace, local and enabled-or-not plugin
roots, or the roots you pass). It exits non-zero on any error. User filters are validated at write time by the
same `validate_steps`.

| Rule id | Severity | Meaning |
|---|---|---|
| `filter.parse` | error | YAML does not parse, or the file is over 64 KB |
| `filter.schema` | error | unknown key, wrong type or value out of its range |
| `filter.id_dir` | error | `id` differs from the directory name |
| `filter.id_taken` | error | duplicate id inside one namespace (a local override of a marketplace filter is a note) |
| `filter.reserved_id` | error | the id contains `:` |
| `filter.op_unknown` | error | `op` is neither a core op nor a declared `filter_ops` entry |
| `filter.param_unknown` | error | the op has no such param |
| `filter.param_range` | error | the value is outside the param's min..max |
| `filter.curve_points` | error | fewer than 2 or more than 16 points, `x` not strictly increasing, values outside 0..1 |
| `filter.step_order` | error | a colour step follows a spatial step |
| `filter.step_count` | error | more than 64 steps |
| `filter.lut_missing` | error | the `lut:` file is absent |
| `filter.lut_path` | error | the path is not a plain `*.cube` name, or escapes the directory |
| `filter.lut_format` | error | the cube does not parse, is over 8 MB, has a size outside 2..65, is a 1D LUT, has non-finite values or the wrong row count |
| `filter.lut_license` | error | `lut:` is set without `license:` |
| `filter.lut_size` | warning | the cube size is not 17, 33 or 65 |
| `filter.no_effect` | warning | no steps and no LUT, or every step is a no-op at its defaults |
| `filter.op_no_backend` | warning | an op has no Python implementation (editor only) |
| `filter.name_len` | warning | the name is over 24 characters |
| `filter.group_new` | warning | the group is not a built-in group and no other filter uses it |
| `filter.preview_gamut` | warning | more than 5% of the compiled LUT's nodes overshoot the 0..1 range by more than 10% in some step |
