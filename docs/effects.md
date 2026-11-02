---
category: Editor
category_order: 72
order: 20
---

# Video Effects

An **effect** is a media tool defined by one folder: an `effect.yml`, an optional GLSL preview shader and
optional Python renders. Effects register into the media-tools registry as `ToolSpec`s with `group: effects`,
so runs, presets (`media_tool` pipe), chat, MCP and automation pick them up with no extra endpoint.

The code:

- Contract, loader, derive language, template compiler: `src/platform/media_tools/effects/`.
- Core effects: `src/platform/media_tools/effects/builtin/<id>/` (film grain, fix colours).
- Planner (one ffmpeg graph, or ffmpeg segments joined to Python stages by raw pipes):
  `src/platform/media_tools/fastpath.py`, `src/platform/media_tools/streaming.py`.
- Routes: `src/features/media_tools/routes.py`.

## The folder

```
<id>/
  effect.yml        the definition (below)
  preview.frag      one `vec4 effect(vec4 color, vec2 uv)` function
  render.py         torch / numpy renders, optional
  analyze.py        an analysis function, optional
```

### `effect.yml`

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | `^[a-z][a-z0-9_]*$`. A plugin effect is registered as `<plugin-id>.<id>` |
| `label`, `description`, `icon` | `label` | Served verbatim in the catalog |
| `group` | yes | Section in the Effects panel: `colour`, `texture`, `motion`, `transitions` (open set) |
| `accepts` | yes | `[video]` or `[video, image]` |
| `time` | yes | `none`, `seconds` (reads `u_time`) or `frame` (reads `u_frame`) |
| `requires` | no | `min_duration`, `has_audio`, as media tools |
| `params` | no | Media-tools params: `integer`, `float`, `select`, `checkbox`, `color`, `anchor` |
| `derive` | no | Ordered `name: expression` map in the derive language |
| `analysis` | no | `{python: file.py:fn, keys: [param…], values: [name…]}` |
| `preview` | no | `{shader: file.frag, textures: […], gain: expression}` |
| `render` | yes | Ordered candidates: `- ffmpeg: {expr, vf, af}`, `- torch: file.py:fn`, `- numpy: file.py:fn` |
| `parity` | yes | `media`, `cases` (`params`, `frames`), `tolerance`, optional `analysis`, `fixtures` |

Names (params, derive entries, analysis values) are unique within an effect, match `^[a-z][a-z0-9_]*$`, and may
not be `media`, `pi`, `time` or a derive function name.

## Served shapes

### Catalog: the `effect` block

`GET /api/media-tools?kind=video&scope=editor` returns every tool as today. Each tool carries `effect`: `null`
for ordinary tools, and this object for effects:

```json
{
  "id": "film_grain",
  "label": "Film grain",
  "icon": "editor-effects",
  "accepts": ["video", "image"],
  "group": "effects",
  "params": [{"name": "amount", "type": "float", "label": "Amount", "default": 30, "min": 0, "max": 100}],
  "source": "core",
  "effect": {
    "group": "texture",
    "time": "frame",
    "shader": {"url": "/api/media-tools/effects/film_grain/shader", "etag": "\"5c1d0e8a2f43b9a7\""},
    "uniforms": [
      {"name": "u_amount", "type": "float", "from": "param", "key": "amount"},
      {"name": "u_size", "type": "float", "from": "param", "key": "size"},
      {"name": "u_color", "type": "bool", "from": "param", "key": "color"},
      {"name": "u_seed", "type": "int", "from": "param", "key": "seed"},
      {"name": "u_strength", "type": "float", "from": "derive", "key": "strength"},
      {"name": "u_cell", "type": "float", "from": "derive", "key": "cell"}
    ],
    "textures": [],
    "derive": [
      {"name": "strength", "expr": "amount / 100 * 0.14 * 255"},
      {"name": "cell", "expr": "max(1, floor(size * min(media.width, media.height) / 1000 + 0.5))"}
    ],
    "gain": null,
    "analysis": false,
    "analysis_keys": []
  }
}
```

| Field | Shape |
|---|---|
| `group` | The effect's panel group (the tool's own `group` stays `effects`) |
| `time` | `none` \| `seconds` \| `frame` |
| `shader` | `{url, etag}`, or `null` when the effect has no shader (it previews through `POST /preview/frame`) |
| `uniforms` | Ordered list of `{name, type, from, key}`. `from` is `param`, `derive` or `analysis`; `key` is the param, derive or analysis name |
| `textures` | `[{uniform, from, kind}]`, `kind` `lut3d` \| `image2d` (empty until LUT params exist) |
| `derive` | Ordered list of `{name, expr}`; evaluate in order |
| `gain` | A derive expression of `time` scaling the element volume during playback, or `null` |
| `analysis` | `true` when the effect needs `POST /api/media-tools/effects/analysis` values before it can draw |
| `analysis_keys` | The params the analysis depends on (fix colours: `["white_balance", "contrast"]`). Values stay valid while these params, the source and the window are unchanged, so other param changes need no new request |

A tool with `group: effects` and `effect: null` is an effect without a definition folder (for example a plugin
media tool listed in the Effects menu). It has no shader and previews through `POST /preview/frame`, like an effect
whose `shader` is `null`.

Uniform types by source:

| Source | `type` | Value |
|---|---|---|
| `float` param | `float` | the number |
| `integer` param | `int` | the number |
| `checkbox` param | `bool` | the flag |
| `color` param | `vec3` | sRGB 0..1 from `#rrggbb` |
| `select` param | `int` | index of the value in the param's `options` |
| `anchor` param | `vec2` | 0..1, `top_left` = (0, 0), `center` = (0.5, 0.5), `bottom_right` = (1, 1) |
| derive / analysis value | `float` | the number; booleans become 0 / 1; a string value has no uniform value |

Anchors: `top_left` (0, 0), `top` (0.5, 0), `top_right` (1, 0), `left` (0, 0.5), `center` (0.5, 0.5),
`right` (1, 0.5), `bottom_left` (0, 1), `bottom` (0.5, 1), `bottom_right` (1, 1).

Built-in uniforms declared by the host prelude: `sampler2D u_input`, `vec2 u_resolution`, `float u_time`,
`int u_frame`, `float u_fps`, `float u_duration`.

### Shader text

`GET /api/media-tools/effects/{tool_id}/shader`

- `200`, `Content-Type: text/plain; charset=utf-8`, `X-Content-Type-Options: nosniff`,
  `Cache-Control: private, no-cache`, `ETag` equal to the catalog's `shader.etag`. The body is the author's
  `.frag` source only; the host adds the prelude and `main()`.
- `304` with no body when `If-None-Match` matches the ETag.
- `404` `{"error": "not_found"}` for an unknown tool, a tool that is not an effect, or an effect with no shader.
- `401` without a session.

`tool_id` is the registered id (`film_grain`, `retro-tape.scanlines`); the URL never carries a plugin id of
its own.

### Analysis values

`POST /api/media-tools/effects/analysis`

```json
{
  "input": {"item_id": "01J…"},
  "steps": [
    {"tool": "trim", "params": {"start": 1, "end": 5}},
    {"tool": "fix_colors", "params": {"strength": 80}}
  ],
  "step_index": 1
}
```

`step_index` indexes `steps` as sent. Hidden steps before it are skipped, as in a render. The values are measured
on the source within the window the step sees (after the trims and speed changes before it), and memoised per
user, source, window and the effect's `analysis.keys`.

```json
{"success": true, "data": {"values": {"gain_r": 1.12, "gain_g": 0.97, "gain_b": 0.91, "black": 0.04, "white": 0.93}, "cached": false}}
```

| Status | `error` | When |
|---|---|---|
| `400` | `invalid_params` (`field: step_index`) | the index is out of range, the step is hidden, or its tool has no analysis |
| `400` | `invalid_params` | a param of any step is invalid (`step_index` names it) |
| `400` | `unknown_tool` | a step names an unknown tool |
| `400` | `unsupported_media` | the input is not a video |
| `401` | | no session |
| `404` | `not_found` | the input does not exist or belongs to someone else |
| `429` | `preview_busy` | too many previews are rendering |

## The derive language

A Python expression subset with Python's semantics, evaluated by `src/platform/media_tools/effects/derive.py` and by
the browser's parser. The shared goldens in `tests/fixtures/effects/derive/cases.json` pin both: every expression,
value and error phase (`parse` or `eval`) there must agree.

| Element | Rule |
|---|---|
| Literals | numbers (`1`, `0.5`, `1e3`), strings in single or double quotes, `True`, `False` |
| Names | params, earlier derive entries, analysis values, `media`, `pi`; `time` only in `preview.gain` |
| Attributes | one level: `media.width`, `media.height`, `media.fps`, `media.duration`; an anchor's `.x` and `.y` |
| Arithmetic | `+ - * / ** %` and unary `-` `+`, with Python precedence (`-2 ** 2` is `-4`, `**` is right-associative); `+` also joins two strings |
| `/`, `%` | true division and floored modulo (`-1 % 3` is `2`); by zero is an error |
| Comparisons | `== != < <= > >=`, chained like Python (`0 < x < 1`); strings order among themselves; `==` between a string and a number is `False` |
| Logic | `and` / `or` return the deciding operand like Python (`0 or 5` is `5`); `not` returns a boolean |
| Conditional | `a if c else b` |
| Functions | `min(a, b, …)` and `max(a, b, …)` (two or more values, all numbers or all strings), `clamp(v, lo, hi)`, `abs`, `floor`, `round(x)` / `round(x, digits)`, `sqrt`, `sin`, `cos`, `pow(a, b)` |
| `round` | Python's: half to even (`round(2.5)` is `2`), `round(2.675, 2)` is `2.67` |
| Errors (`eval`) | a result that is not a finite real (`(-8) ** 0.5`, `1e400`), `sqrt` of a negative number, an unknown name or attribute, any name starting with `_` (`media.__proto__`), a wrong number of function arguments, text mixed with numbers |
| Errors (`parse`) | anything outside the grammar: subscripts, lambdas, calls to other functions, keyword arguments, two-level attributes |

Booleans count as `0` / `1` in arithmetic. The `media` values are those at the effect's place in the stack:
the frame size entering the step, the source frame rate, and the output duration at that step.

### Frame size in derive

`media.width` and `media.height` are the real frame size on the server and the preview pass size in the browser,
which is capped at 1920 on the long side and may step down to 1280 or 960. An effect must therefore stay
scale-invariant: anything spatial is written relative to the frame (grain's `cell` is `size * min(w, h) / 1000`,
a vignette's falloff is relative to the diagonal), so the preview at a reduced size looks like the full render. A
quantity that rounds to whole pixels (grain's `cell`) can differ by a pixel between the two sizes; that is the
expected preview drift, and the parity goldens are computed at the parity `media` size, where both agree exactly.

## Templates

An `ffmpeg` candidate is data. `vf` and `af` are lists of `{filter: {option: value}}` entries with an optional
`when:` derive expression; `expr` holds named sub-expressions referenced as `{name}`.

- Filters and option keys come from an allowlist: `colorchannelmixer colorlevels eq hue curves vignette fade
  lut3d perspective gblur unsharp noise hflip vflip`, audio `afade volume`.
- Never: `movie amovie sendcmd asendcmd zmq azmq subtitles ass drawtext ladspa lv2 frei0r dnn_* arnndn haldclut
  signature vidstab*`, any option naming a file except the host-bound `lut3d.file`.
- Placeholders `{name}` and `{name.attr}` take params, derive and analysis values and `media.*`. Values are
  numbers (`%.6g`), booleans (`1`/`0`), colours (`0xrrggbb`), enum words from a fixed list and host-staged asset
  paths. A `string` param never enters a template.
- After substitution each option is tokenised: numbers, `+ - * / ^ ( ) ,`, the filter's runtime variables and
  the ffmpeg functions `clip if ifnot min max abs sin cos pow sqrt floor ceil round lerp gte lte gt lt eq`.
  Each value is single-quoted; the grammar admits no `'`, `\`, `:`, `[`, `]` or `;`.
- A compiled step is at most 16 KB. Every template is compiled against a dummy binding when the effect loads, so
  a template using a denied filter never registers.

## Rendering

The host picks the first available candidate in listed order: `ffmpeg` when every filter it uses exists in the
local ffmpeg; `torch` when torch imports (on CUDA only with at least 1 GB free and no generation running,
otherwise on CPU); `numpy` always.

A stack whose steps all render through ffmpeg runs as one filtergraph. Steps with a Python candidate split the
plan into ffmpeg segments joined by rgb24 pipes; neighbouring Python stages compose, 8 frames per batch. The video
is decoded and encoded once, colour conversion is pinned (`scale=in_color_matrix=auto:in_range=auto,format=gbrp`
before the first RGB effect, `scale=out_color_matrix=bt709:out_range=tv` before the encoder) and the source audio
is kept with every `af` in stack order.

Python renders see `FrameBatch` (`frames` N×H×W×3 uint8, `indices` counted from the stage's first frame,
`times` = `index / fps`, `fps`, `duration`, and `tensor` N×3×H×W float 0..1 for torch) and return the same shape.
