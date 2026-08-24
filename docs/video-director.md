# Video Director

Video Director is one abstract composition UI for native video presets, replacing the
patchwork of per-mode video flows with a single prompt-section takeover — the same
mechanism the chip-based prompt editor already uses for prompt relay. A preset opts in by
declaring `vars.video_director` (see below); nothing changes for a preset that doesn't.

This is the authoritative reference for the wire document and the capability declaration.
Where a detail depends on a source file, that file is named so you can double-check.

**ComfyUI presets are unaffected.** Video Director targets the `native` engine's own
composition pipes; a `comfyui` preset keeps its existing per-batch prompt relay (see
[Prompt Expansion](prompts.md)) regardless of what this document describes.

## Composition modes

A preset declares which of these it supports (`vars.video_director.modes`, below). The
frontend renders a different editor per mode, but every mode submits the same document
shape through `form_data.video_director`.

| Mode | What it produces |
|---|---|
| `t2v` | Text-to-video: one segment, no media. |
| `i2v` | Image-to-video: one segment, one `first`-role media reference. |
| `flf` | First-last-frame: one segment, exactly two media references (`first` + `last`, same segment) — the standard way to say "start on this image, end on that one." |
| `director` | Multiple segments with per-segment prompts, plus (capability permitting) keyframes, audio, and IC-LoRA reference conditioning. |

There are exactly four modes. A retired fifth, `chain`, is gone from the wire
contract: a stored pre-`director` document whose `mode` is `"chain"` is still *read*
(remapped to `director`, but only against a preset that declares `segment_routing` and a
`director` mode and no `chain` mode), and the canonical output always says `"director"`.
Nothing emits `"chain"` as a mode any more.

### The two director styles

`director` is **capability-shaped, not name-shaped**. Which of two very different editors
and validation shapes it takes is decided by one preset capability, `segment_routing`:

| | `segment_routing: true` (chain style — Wan) | no `segment_routing` (timeline style — LTX) |
|---|---|---|
| What runs | Multiple sequential generations, one per segment, stitched end-to-end with a tail-frame handoff | One generation whose timeline the segments describe |
| Segment shape | `frames` per segment (required); `steps`/`cfg`/`loras` overrides allowed | `start`/`end` seconds (required); `frames`/`steps`/`cfg`/`loras` rejected |
| Total length | Sum of per-segment `frames`; `settings.duration` is advisory and not validated | `settings.duration`, validated against `limits` |
| Keyframes | Only with `keyframes: "anywhere"` (see [`media`](#media)) | Always, capped by `max_keyframes` |
| Audio / IC-LoRA | Audio with the `audio` capability; IC-LoRA never | Audio with `audio`; IC-LoRA with `ic_lora` |
| Derived output | `sub_type` per segment + `needs_t2v_set`/`needs_i2v_set` | — |

The two styles never coexist in one preset, and no mode string distinguishes them —
everything downstream keys off `segment_routing` and the per-mode capability keys.

## The document contract

`form_data.video_director` is validated and canonicalized by
`normalize_video_director(document, capabilities, storage_dir)`
(`src/features/video_director/normalize.py`), called from
`GenerationOrchestrator.start_generation()` before a generation record is created. It
raises `VideoDirectorValidationError` — a `ValueError` subclass carrying **every** problem
found, not just the first (`str(exc)` joins them with `; `) — which the generation
controller turns into a `400 validation_error` response before anything is persisted or
queued. On success it returns a **new, canonical** dict; the input is never mutated, and
`form_data['video_director']` is replaced with the canonical version for everything
downstream (pipes, history, the persisted `Generation` row).

```jsonc
{
  "schema_version": 1,
  "mode": "t2v" | "i2v" | "flf" | "director",
  "settings": {
    "fps": 24,
    "duration": 5.0,          // not validated in a chain-style director (frame counts drive length)
    "resolution": "",         // free-form, optional
    "seed": -1,
    "continuation": {                    // chain-oriented, accepted in every mode
      "source": "tail_frames" | "last_frame",
      "overlap_frames": 4,
      "stitch": true
    }
  },
  "segments": [
    {
      "id": "seg-1",
      "prompt": "...", "negative_prompt": "",
      "start": 0.0, "end": 5.0,          // timeline style: seconds on the timeline
      "frames": 81,                       // chain style: this segment's pixel-frame count
      "seed": null, "steps": null, "cfg": null,   // chain style: per-segment overrides
      "loras": { "high": [{"model": "...", "strength": 1.0}], "low": [] },
      "sub_type": "t2v" | "i2v" | "flf" | "chain",  // chain style: optional override, see below
      "references": [                     // capability-gated, see "references" below
        { "path": "...", "relative_path": "...", "type": "image" | "video" | "audio" } |
        { "form_media": { "field": "...", "label": "..." } } |
        { "form_media": { "field": "...", "path": "..." } }
      ],
      "reference_indices": [0, 2]          // DERIVED, never sent -- see "Derived blocks"
    }
  ],
  "media": [
    {
      "id": "m-1", "role": "first" | "last" | "keyframe",
      "segment_id": "seg-1",             // required for first/last; null for keyframe
      "at": 0.0,                          // keyframe only: seconds on the director timeline
      "strength": 1.0,
      "media": { "path": "...", "relative_path": "...", "type": "image" | "video" }
    }
  ],
  "audio": [
    {
      "id": "a-1",
      "role": "condition" | "mux",       // optional, defaults to "condition"
      "start": 0.0, "trim_start": 0.0, "length": 5.0,
      "media": {...}
    }
  ],
  "ic_lora": [
    {
      "id": "ic-1",
      "lora": { "model": "...", "strength": 1.0 },
      "reference": { "path": "..." } | null,
      "strength": 1.0
    }
  ]
}
```

The canonical output carries three more blocks the normalizer **derives**; a client never
sends them and they are described under [Derived blocks](#derived-blocks) below.

### `schema_version`

Must be exactly `1`. Missing → `"missing schema_version"`. Any other value → an error
telling the caller the document was produced by a client newer than this server
understands. There is no migration path; a version bump on the wire format is a breaking
change on both ends.

### `mode`

Must be a key of `capabilities.modes` (see [Preset capability
declaration](#preset-capability-declaration) below). If `capabilities` carries no `modes`
map at all, **no mode validates** — the preset simply hasn't opted in. The error names the
allowed modes.

### `settings`

- `fps` — defaults from `capabilities.limits.default_fps` (fallback `24`), must be `1`–`60`
  (every native video generator's own `PipeConfigSpec("fps", ...)` caps there).
- `duration` — everywhere except a chain-style director, defaults from
  `capabilities.limits.default_duration` (fallback `5.0`), must be `> 0` and, when
  `capabilities.limits.max_duration` is set, no greater than it. A chain-style director's
  length comes from summing per-segment `frames` instead, so `duration` isn't validated
  there and may be absent.

  When the preset also declares `capabilities.limits.max_frames`, `duration * fps` is
  checked against it and the error names the largest duration that would fit. Within the
  cap, the normalizer writes back two extra `settings` keys — `frame_count` (the raw count
  snapped to the generator's `1 + k*8` lattice) and `effective_duration` — so a caller can
  show the frame count the generation will actually use before submitting. A preset without
  `max_frames` gets neither the check nor the two keys.
- `resolution` — passed through as-is (empty string if absent); not interpreted here.
- `seed` — `-1` or missing resolves to a freshly rolled seed via the same
  `generate_seed()` helper the prompt expander uses (`src/platform/util/latents.py`), and the roll is
  written back into the canonical document so pipes never see `-1`. An explicit non-`-1`
  seed passes through unchanged. Per-segment `seed: null` (chain style) is left `null` —
  the pipe deriving that segment's actual seed does `base_seed + index`, not the
  normalizer.

  **Precedence over the plain form seed**: the frontend's ordinary `form_data.seed` field
  (the one every non-director generation mode already uses) is not part of this document —
  it lives alongside `video_director` in `form_data`, not inside it. Before normalization,
  `GenerationOrchestrator.start_generation()` (`src/features/generation/orchestrator.py`)
  overrides `settings.seed` with `form_data['seed']` whenever that form seed is an explicit
  int other than `-1`, on a copy of the document (the original request body is never
  mutated). A `-1` or absent form seed changes nothing — the document's own `settings.seed`
  (usually `-1`, since the frontend doesn't currently set it) reaches the normalizer
  unmodified and gets randomly rolled as described above. This makes the form's seed field
  the effective source of truth for reproducibility across every composition mode,
  including a chain-style director (`base_seed + index` per segment), without any
  document-shape change.
- `continuation` — optional, chain-oriented, but accepted (and normalized) regardless of
  mode: `source` must be `"tail_frames"` or `"last_frame"` if given, `overlap_frames` a
  non-negative int (default `0`), `stitch` a bool (default `true`). `null`/absent stays
  `null` in the output.

  `overlap_frames` is otherwise **unbounded**. A preset bounds it by declaring
  `max_overlap_frames` on the mode (see [Preset capability
  declaration](#preset-capability-declaration)); exceeding it is a validation error naming
  both the submitted value and the declared maximum. Presets that don't declare it keep the
  unbounded behaviour — the frames a family can actually hand off is a property of its
  conditioning window, not something the normalizer can guess.

  **A chain-style mode that declares `continuation` EXPLICITLY as `null`** (the key
  present in `modes.director`, not merely absent — see [Preset mode
  overlays](#preset-mode-overlays)) rejects a non-null `settings.continuation` outright:
  every shot in that mode is architecturally a hard cut, so there is nothing for the
  document to configure. See [Derived blocks](#derived-blocks) for the matching effect on
  `sub_type` derivation.

### `segments`

At least one segment is required in every mode — the frontend always sends one, even for
`t2v`/`i2v`/`flf`, which conceptually only need a single implicit segment. `id` defaults
to `seg-{index}` when omitted; `prompt`/`negative_prompt` default to `""`.

**Timeline-style director**: `start`/`end` are required numbers with
`0 <= start < end <= duration`. Output segments are re-sorted by `start`; touching edges
(segment A's `end == 5.0`, segment B's `start == 5.0`) are fine, but overlapping ranges are
rejected. Prompts are literal per segment — this, combined with `media` role anchoring
(below), is how "a prompt that is also the first and last frame of a range" gets expressed:
a `first`/`last` media entry points at a `segment_id`, not at a timestamp, so a segment's
own boundary images ride along with its prompt.

**Chain-style director**: `frames` is required, an int, `1`–`257` (hard cap), and no
greater than the mode's `max_frames_per_segment` when the preset declares one. Segment
count is capped at the mode's `max_segments` (default `8`). `steps` (`1`–`150`) and `cfg`
(`0`–`30`) are optional per-segment overrides. `loras` — an object with optional
`high`/`low` lists of `{model, strength}` (`strength` clamped to `0`–`4`, non-model items
dropped) — is only accepted when the mode declares `per_segment_loras`; otherwise it's an
error, not a silent drop, because a caller that thinks its per-segment LoRA is taking
effect and isn't needs to know.

`sub_type` is an optional per-segment **override** of the routing derivation described
under [Derived blocks](#derived-blocks) — one of `t2v`/`i2v`/`flf`/`chain`, anything else
is an error. It's how a prompt-only later segment is forced to a fresh cut instead of
continuing the previous shot. An explicit `sub_type: "chain"` is itself an error when this
mode declares `continuation` as explicitly `null` (see [`settings`](#settings) above) —
that mode has no continuation to request in the first place.

**Every other shape** (`t2v`/`i2v`/`flf`, and a timeline-style director): `frames`,
`steps`, `cfg`, and `loras` must be `null`/absent — sending them is an error, not ignored,
for the same reason.

### `references` (segment)

Gated by the top-level `references`/`reference_fields` capability (see [Preset capability
declaration](#preset-capability-declaration)), not by mode or director style — every
segment in every mode carries this key. It is a per-shot **selection** from a whole-film
reference pool that lives on the preset's own form fields (named by
`capabilities.reference_fields`); the pool itself is never duplicated into the document.

- Capability absent (`null`) — a segment may not carry `references` at all; a non-null
  value is an error ("references are not supported by this preset").
- Capability `"whole"` — the entire pool applies to every segment uniformly, so there is
  nothing to select: a segment's `references` must be absent/`null`. Sending a non-null
  value (even `[]`) is an error, not a silent drop.
- Capability `"per_shot"` — a segment's `references` is `null`/absent to inherit the full
  pool, or a list to select from it. Each entry is either:
  - `{ path | relative_path, ... }` — a direct media reference, resolved exactly like
    `media[].media` (see [Path resolution](#path-resolution-and-traversal)); or
  - `{ form_media: { field, label? | path? } }` — a pointer into one of the fields named by
    `capabilities.reference_fields`, addressed the same way the chat tool's
    `upsert_media.form_media` addresses a picker item: `field` must be one of the declared
    reference fields, and exactly one of `label` (matched case-insensitively against the
    item's label/filename) or `path` picks the item out of that field's current value.

  Giving both `path`/`relative_path` and `form_media` on one entry, or neither, is an
  error, as is a `field` outside `reference_fields`, an unmatched `label`/`path`, or a
  malformed entry. Every problem is collected, the same as everywhere else in this
  contract. The canonical output resolves every entry's `media` reference to an absolute,
  on-disk path exactly like `media[].media`.

  **Every entry must resolve to an item that is actually in the pool** — a `path`-style
  entry that names a real, on-disk, storage-contained file is still rejected if that file
  isn't one of `reference_fields`' current items. `references` is a SELECTION from the
  pool, not a way to condition on an arbitrary extra file the pool never embedded; see
  [Derived blocks](#derived-blocks) for why (`reference_indices` only has positions to
  point at for pool members).

  An explicitly **empty** list (`references: []`, as opposed to `null`/absent) is also
  rejected — selecting zero references has no defined meaning; omit the field to use the
  whole pool, or list at least one selection.

### `media`

`role` is one of `first`, `last`, `keyframe`.

- `first`/`last` anchor to a segment via `segment_id`, which must reference an existing
  segment. At most one `first` and one `last` per segment.
- `keyframe` is anchored to a timestamp (`at`) rather than a segment, and is capped at the
  mode's `max_keyframes` (default `8`). Where it's legal, and what `at` is measured
  against, depends on the director style:
  - **timeline style** — always legal; `at` must be within `[0, settings.duration]`.
  - **chain style** — legal only when the mode declares `keyframes: "anywhere"`. There is
    no `settings.duration` to measure against, so `at` must be within `[0, T]` where `T` is
    the chain's total duration: the sum of the per-segment `frames` divided by
    `settings.fps`. Without that capability a keyframe entry is rejected, which is what
    every preset shipping `keyframes: "first_only"` (or nothing) still gets.
- Per-mode shape rules: `t2v` accepts none; `i2v` requires exactly one, role `first`; `flf`
  requires exactly two, one `first` and one `last`, both on the **same** segment.
- `keyframes: "first_only"` restricts a chain-style director differently from `"anywhere"`: it
  doesn't admit `keyframe` entries at all (no free-floating placement). It does **not** pin
  `first`/`last` to segment 0 — either may land on **any** segment, join-aware:
  - `first` is legal on any segment whose resolved `sub_type` isn't `"chain"` (see
    [Derived blocks](#derived-blocks)) — segment 0, or a later segment an explicit
    `sub_type: "t2v"` override cuts fresh. Attaching a start image to a segment that hasn't
    been cut is self-consistent rather than rejected: `derive_segment_sub_type` resolves
    `has_first_media` to `i2v`/`flf` regardless of position, UNLESS an explicit override on
    that same segment forces `"chain"` — that specific combination (a start image the
    override would silently discard) is rejected outright rather than accepted and dropped.
  - `last` is legal **only** paired with `first` on the SAME segment — that combination is what
    resolves the segment to the `flf` sub-type (see [Derived blocks](#derived-blocks)); the
    generator only ever reads a trailing frame off an `flf` segment, so a `last` with no
    paired `first` on that segment has no effect and is rejected rather than silently dropped.
- Inside a `director`-mode document, `first`/`last` are also gated by capability at the
  document level, independent of the per-segment rule above — a director mode with no way to
  honour an edge at all rejects the role outright instead of accepting it ungated:
  - `first` is legal when the preset declares `i2v` or `flf` (either single-shot mode implies
    the family conditions on a start image), when keyframes are legal anywhere (below), or
    under `first_only`.
  - `last` is legal when the preset declares `flf` (a chain segment carrying both `first` and
    `last` resolves to the `flf` sub-type — see [Derived blocks](#derived-blocks)) or when
    keyframes are legal anywhere.
  - Every preset shipping today declares both `i2v` and `flf` alongside `director`, so this
    changes nothing for a shipped preset; it only closes the gap for a hypothetical future
    director mode that declares none of the above.
- The frontend editor is deliberately narrower than what this validator accepts: a segment's
  own trailing-frame WELL only ever renders where that segment both opens fresh (eligible for
  `first`) **and** closes fresh (its outgoing join is a cut, or it's the last segment) — a
  segment that still continues into the next one never offers a trailing well, even though the
  generator could in principle still splice a continuation off its tail (see
  `chainSegmentEdgeAllowances` in `frontend/src/lib/utils/videoDirector.ts`). This keeps the
  UI from ever producing the confusing "this shot both ends on a chosen frame AND its tail
  feeds the next shot" state, without the validator itself needing to forbid it.
- `strength` is clamped to `[0, 1]`.
- Only **image** media is supported for `first`/`last`/`keyframe`. A `media.type` of
  `"video"` is rejected here rather than silently dropped later; a reference with no `type`
  at all counts as an image.
- `media.media` is resolved to an on-disk path (see [Path resolution](#path-resolution-and-traversal)).

### `audio`

Gated purely by capability: a non-empty `audio` list requires the mode to declare
`audio: true`, in **either** director style (empty is always fine, everywhere). Each entry:
`start >= 0`, `length > 0`, `trim_start >= 0`, and a required `media` reference.

`role` says how the family's pipes should consume the track and defaults to `"condition"`:

| `role` | Meaning |
|---|---|
| `condition` | The track feeds the generation — the model is conditioned on it. |
| `mux` | The track is laid onto the finished video, never entering the diffusion loop. |

Any other value is a validation error. The normalizer validates the value and nothing more:
whether a given preset can honour a given role is the family pipe's business, so a preset
that only muxes should say so in its `tips` rather than expecting a rejection here.

### `ic_lora`

**Timeline-style director only**, and gated by the mode's `ic_lora` capability — unlike
`audio`, this one did *not* open up to chain style. Each entry requires `lora.model`;
`reference` is an optional media reference (e.g. a source image or clip the IC-LoRA
conditions on). `lora.strength` and the entry's own `strength` are both clamped to `[0, 1]`.

### Derived blocks

On success the canonical document gains keys no client sends. They exist because the
strict template evaluator requires a pipeline's `@loop items:` to already *be* a list, so
the boundary — this normalizer — has to materialize them rather than the preset YAML
building them at render time.

**Segment routing** — only when the preset declares `segment_routing`. Every segment gets a
resolved `sub_type`, and the document gets `needs_t2v_set` / `needs_i2v_set` booleans, which
a pipeline branches its two model-set loaders on (Wan ships separate 16-channel t2v and
36-channel i2v checkpoints, so a chain opening on a fresh shot and continuing by
concatenation needs both). Resolution order, deterministic so the frontend can render the
same per-segment badge without asking the server: an explicit segment `sub_type` override
wins; else start image + end image is `flf`; else a start image is `i2v`; else the first
segment is `t2v`; else a prompt-only later segment is `chain`. `i2v`/`flf`/`chain` all draw
from the i2v checkpoint set, `t2v` from the t2v set.

A mode that declares `continuation` explicitly as `null` (see [`settings`](#settings)
above) coerces the LAST step of that derivation: a segment that would otherwise resolve to
`chain` resolves to `t2v` instead — silently, no error, because nothing was explicitly
requested. An explicit `sub_type: "chain"` override, in contrast, IS an error under the
same capability (checked earlier, before this derivation ever runs) — the difference is
between a document that never asked for continuation and one that did.

**Reference indices** — only on a segment whose `references` (see [`references`
(segment)](#references-segment) above) is a non-null selection. `reference_indices` is the
list of each selected entry's position in the PACKED reference pool — every item of the
first `reference_fields` field, then the second, and so on, each field's own item order
preserved (MiniMax-H3: images, then videos, then audio, matching the generator's own
`<Picture i>`/`<Video k>`/`<Audio j>` numbering). Duplicate selections collapse to one
index; the surviving order is the SELECTION's own order (by first occurrence), never
sorted — a generator that re-labels a subset in its presented order needs to know which
entry the caller meant first. A segment whose `references` is `null`/absent gets
`reference_indices: null` too, meaning "every reference" (the whole-pool case) — the same
convention `WindowPlan.reference_indices` uses on the generator side
(`src/pipelines/pipes/generator/video_minimax_h3/windows.py`).

**Media fields** — always, for every mode and both styles. `media_images` (ordered: image
`first`, then image `last`, then image `keyframe` sorted by `at`, then image `ic_lora`
references), `media_videos` (video `ic_lora` references only), and `media_placements`,
whose entries are `{source, index, frame, strength, role}` with `index` aligned to whichever
of the two lists `source` names and `frame` being the sentinel `"first"`/`"last"` or a frame
number (`at * fps`, rounded half-up like Jinja's `round`, not Python's banker's rounding).
Each `ic_lora` reference is routed by its own media type — an image reference becomes a
`source: "image"` placement, because the video loader is cv2-backed and cannot read a still.

### Path resolution and traversal

Every `media.media` object and `ic_lora.reference` is a `{path, relative_path, ...}`
reference, resolved the same way: if `path` is given, absolute, and exists, it's kept
as-is; otherwise the given `path`/`relative_path` is joined onto `storage_dir` (the
per-user file storage root — `SettingsManager.get_file_storage_directory(user_id)`,
`src/platform/settings/settings.py`) and resolved. The resolved path must land **inside**
`storage_dir` — anything that resolves outside it (`../../etc/passwd`-style traversal) is
rejected — and must exist on disk. The canonical output rewrites `media["path"]` to the
resolved absolute path; every other key on the reference passes through unchanged.

### Unknown-key policy

Unknown **top-level** keys on the document (anything besides `schema_version`, `mode`,
`settings`, `segments`, `media`, `audio`, `ic_lora`) are preserved verbatim in the
canonical output — a future field a newer frontend adds doesn't get silently eaten by an
older server. Unknown keys **inside** known structures (a stray field on a segment, a
media entry, etc.) are dropped; only the keys documented above survive normalization there.

## Preset capability declaration

A preset opts in by declaring `vars.video_director` in `preset.yml` (see [`vars:`](presets.md#presetyml-reference)
in the Preset Authoring Guide for how `vars:` works generally). This is the
`capabilities` dict `normalize_video_director` validates the document against — a preset
that omits a mode from `modes` simply never accepts that mode's documents, and a preset
that omits `vars.video_director` entirely accepts none of them.

```yaml
vars:
  video_director:
    preset_modes: ["video"]     # which of this preset's own modes/ this applies to
    segment_routing: true       # presence/truth = the director mode is chain-style
    modes:                      # dict keyed by composition mode; presence = enabled
      t2v: {}
      i2v: {}
      flf: {}
      director:
        per_segment_loras: true
        keyframes: "first_only" | "anywhere"
        max_segments: 8
        max_frames_per_segment: 81
        max_overlap_frames: 8
        audio: true
        ic_lora: true
        max_keyframes: 8
        continuation:           # frontend-only: what the editor seeds its controls with
          source: "tail_frames"
          overlap_frames: 4
          stitch: true
        tips:
          - "Requires the SVI Pro 2.0 LoRA PAIR (high AND low)."
    limits:
      default_duration: 5
      default_fps: 24
      max_duration: 30
      max_frames: 1001
    references: null | "whole" | "per_shot"   # optional, see "references" (segment) above
    reference_fields: ["field_a", "field_b"]  # required alongside references: "per_shot"
    preset_mode_overrides:      # optional, see "Preset mode overlays" below
      <preset_mode>: { ... a partial video_director block ... }
```

- `preset_modes` — which of the preset's own `modes:` (the `preset.yml` list, e.g. `video`)
  this capability block applies to. It's how a preset with multiple modes scopes Video
  Director to just its video-producing one(s). Frontend-only; the normalizer ignores it.
- `references`/`reference_fields` — the whole-film reference pool capability: `null`
  (default, no pool), `"whole"` (every segment uses the same pool implicitly, no
  per-segment selection), or `"per_shot"` (a segment may select a subset — see
  [`references` (segment)](#references-segment) above). `reference_fields` names the
  preset's own form fields that hold the pool (e.g. a set of image/video/audio pickers);
  required whenever `references` is set, ignored otherwise.
- `segment_routing` — the fork described in [The two director styles](#the-two-director-styles).
  Truthy makes `director` a chain-style routed multi-segment mode and turns on the derived
  `sub_type` / `needs_*_set` block; absent makes it a timeline-style single generation.
- `modes` — a dict, not a list: **presence of a key enables that composition mode**, and
  its value carries that mode's per-mode capability keys. An empty object (`{}`) means "this
  mode is enabled with no extra capabilities" — that's the normal shape for `t2v`/`i2v`/`flf`.
  Recognized keys on `director` (a real preset declares only the ones its style uses):
  - `per_segment_loras` (bool) — chain style; admits per-segment `loras`.
  - `keyframes` — `"first_only"` admits `first`/`last`-role media join-aware, on any segment
    that opens/closes fresh (see [`media`](#media)), but no free-floating `keyframe`-role
    entries; `"anywhere"` additionally admits those, across the chain's own timeline.
  - `max_segments` (int, default `8`) and `max_frames_per_segment` (int, no default —
    unbounded if omitted) — chain style.
  - `max_overlap_frames` (int, no default — **unbounded** if omitted) — caps
    `settings.continuation.overlap_frames`.
  - `audio` (bool) — admits an `audio` list, in either style.
  - `ic_lora` (bool) — admits an `ic_lora` list, timeline style only.
  - `max_keyframes` (int, default `8`) — the keyframe count cap, wherever keyframes are legal.
  - `continuation` — an object is frontend-only defaults the editor seeds its continuity
    controls with; the normalizer doesn't read those, the submitted document's own
    `settings.continuation` is what gets validated. **An explicit `null`** (the key
    present, not merely omitted) is different: it structurally disables continuation for
    this mode — see [`settings`](#settings) and [Derived blocks](#derived-blocks) above.
    Omitting the key entirely changes nothing (the normal case for most chain presets).
  - `tips` — a list of strings shown as a dismissable banner in that mode's editor: the
    mechanism for surfacing a preset-specific prerequisite the normalizer itself has no way
    to enforce (e.g. "this preset needs both halves of a two-part LoRA loaded, or motion
    degrades silently").
- `limits` — `default_duration`, `default_fps`, `max_duration`, `max_frames`, consumed
  exactly as described under [`settings`](#settings) above.

### Preset mode overlays

A preset's own `modes:` list (the top-level `preset.yml` field, e.g. `["video", "refs"]`)
can carry more than one Director-capable mode with a *different* capability set per mode —
`preset_modes` names which modes the Director applies to at all, but every one of them
shared the same `vars.video_director` block until this mechanism existed. A preset that
needs the capabilities themselves to differ per mode declares `preset_mode_overrides`, a
dict keyed by preset mode:

```yaml
vars:
  video_director:
    preset_modes: ["video", "refs"]
    segment_routing: true
    modes:
      t2v: {}
      i2v: {}
      flf: {}
      director:
        keyframes: "anywhere"
        audio: true
        max_keyframes: 8
        max_segments: 6
        max_frames_per_segment: 345
        max_overlap_frames: 34
    limits: { default_duration: 5, default_fps: 24, max_duration: 15 }
    preset_mode_overrides:
      refs:
        references: "per_shot"
        reference_fields: ["references", "reference_videos", "reference_audios"]
        modes:
          director:
            keyframes: null
            audio: false
            # Explicit null, not omission -- see "continuation" below and
            # "references" (segment)'s reference_indices paragraph.
            continuation: null
            max_overlap_frames: null
```

The **effective** capability set for a request is the base block above, shallow-merged
with `preset_mode_overrides[<the request's preset mode>]` when one exists — computed by
`apply_preset_mode_overlay(capabilities, preset_mode)`
(`src/features/video_director/normalize.py`), called before `normalize_video_director()`
ever sees the document (`GenerationOrchestrator.start_generation()`) and before either chat
tool reads or edits it (`get_video_director`/`update_video_director`, which read the
request's preset mode off `form_state["mode"]`). `normalize_video_director()` itself always
receives an already-merged `capabilities` — it has no idea `preset_mode_overrides` exists.

Merge rules:

- Every top-level key overlays **shallowly**: the override's value replaces the base's
  outright. A preset mode with no entry in `preset_mode_overrides`, or a `preset_mode` the
  override table doesn't recognize, leaves every key exactly as the base declared it — this
  is why `video` mode above is untouched by the `refs` override existing at all.
- **`modes` is the one exception**: it merges **per composition mode**, not as a whole
  dict. In the example, the `refs` override's `modes.director` block only mentions
  `keyframes`/`audio`/`continuation`/`max_overlap_frames` — those four keys are the only
  ones that change; `max_keyframes`, `max_segments` and `max_frames_per_segment` all
  survive from the base `director` block untouched, and `t2v`/`i2v`/`flf` (which the
  override's `modes` dict doesn't mention at all) are untouched too. This lets an override
  adjust one or a few capability keys on one composition mode without having to repeat
  everything else that mode declares.
- **A key present with value `null` in an override is not the same as an absent key.**
  `dict(**base, **override)`-style unpacking overwrites on key PRESENCE, so
  `continuation: null` in an override replaces a base dict (e.g.
  `{source: "tail_frames", ...}`) with `null` — a real, meaningful value some capability
  consumers key off (see [`continuation`](#settings)/[Derived blocks](#derived-blocks)
  below), not "no override for this key". A capability consumer
  that wants to tell "the override said null" apart from "the override didn't mention
  this" checks key presence (`"continuation" in mode_caps`), not just `.get(...)`.
- The merged result never carries `preset_mode_overrides` itself — every consumer only
  ever wants the effective set, never the raw override table.

This is generic machinery, not specific to the `references` capability — any capability
key can differ per preset mode this way.

### Example: a Wan-like preset (chain-style director)

Wan's director mode is a routed multi-segment chain, so it declares `segment_routing` and
the chain-shaped keys. It has no audio or IC-LoRA support, so it declares neither.

```yaml
vars:
  video_director:
    preset_modes: ["video"]
    segment_routing: true
    modes:
      t2v: {}
      i2v: {}
      flf: {}
      director:
        per_segment_loras: true
        keyframes: "first_only"
        max_segments: 8
        max_frames_per_segment: 81
        continuation:
          source: "tail_frames"
          overlap_frames: 4
          stitch: true
        tips:
          - "Each shot continues from the previous shot's final frames — keep prompts visually consistent across a chain for a clean stitch."
    limits:
      default_duration: 5
      default_fps: 16
      max_duration: 60
```

### Example: an LTX-like preset (timeline-style director with audio + IC-LoRA)

LTX runs `director` as ONE generation with keyframe/reference conditioning, so it omits
`segment_routing` entirely and declares the timeline-shaped keys:

```yaml
vars:
  video_director:
    preset_modes: ["video"]
    modes:
      t2v: {}
      i2v: {}
      flf: {}
      director:
        audio: true
        ic_lora: true
        max_keyframes: 8
    limits:
      default_duration: 5
      default_fps: 25
      max_duration: 40
      max_frames: 1001
```

### Example: a family combining both (windowed chain + keyframes + audio)

Nothing forces a preset to pick one column of the style table. A family whose pipes
continue a chain *and* place keyframes across it *and* take an audio track declares the
chain-shaped keys alongside `keyframes: "anywhere"` and `audio`, plus the `max_overlap_frames`
bound its conditioning window actually supports:

```yaml
vars:
  video_director:
    preset_modes: ["video"]
    segment_routing: true
    modes:
      t2v: {}
      i2v: {}
      flf: {}
      director:
        per_segment_loras: true
        keyframes: "anywhere"
        max_keyframes: 8
        max_segments: 8
        max_frames_per_segment: 97
        max_overlap_frames: 8
        audio: true
    limits:
      default_duration: 5
      default_fps: 25
      max_duration: 60
```

`ic_lora` stays unavailable to a chain-style preset regardless — declaring it there is a
no-op that still rejects any submitted `ic_lora` list.

## Per-family interpretation

The normalizer knows nothing about model families — it validates and canonicalizes the
document, full stop. What each family's pipes *do* with a canonical document is still
landing (several of the pipes below are in progress); this section describes the intended
contract, not internal function names, so it won't go stale as the implementation shifts.

| Family | `t2v`/`i2v`/`flf` | `director` |
|---|---|---|
| **LTX** | Single generation; `first`/`last` media condition via keyframe tokens in the DiT, not a separate concat pass. | Timeline style. Segments condition the same single generation via per-range keyframe tokens; audio is either model-generated alongside the video or an existing user track muxed onto the output (`role: "mux"` — LTX's audio VAE only decodes, so a supplied track never re-enters the diffusion loop). IC-LoRA reference conditioning applies per `ic_lora` entry. |
| **Wan** | `i2v`/`flf` condition via concat (image latents concatenated onto the noise input), not keyframe tokens. | Chain style. Sequential generations, one per segment, stitched with a tail-frame handoff (`settings.continuation`, default 4-frame overlap) into one final video. Each segment can carry its own `high`+`low` LoRA stack when `per_segment_loras` is declared. Segment length is bounded by whatever SVI guidance the checkpoint needs — in practice ≤81 frames per segment. |

## v1 limits

These are current, not architectural — expect some to loosen as the pipes mature.

- **Prompts are literal.** Unlike the main prompt editor's `{a|b}` dynamicprompts grammar
  (see [Prompt Expansion](prompts.md)), a Video Director segment's `prompt`/`negative_prompt`
  is used exactly as written — no per-image variant sampling. The orchestrator skips prompt
  expansion entirely when `form_data.video_director` is present.
- **`quantity` is effectively `1` in a chain-style director** — a chain produces one
  stitched video per generation, not a batch of independent chains.
- **Conditioned LTX runs are euler-only** — keyframe-token conditioning (i2v/flf/director)
  currently requires the `euler` sampler; other schedulers aren't validated against it yet.
- **Wan's chain declares `keyframes: "first_only"`** — every fresh-open segment (segment 0, or
  one an explicit `sub_type: "t2v"` override cuts) may carry its own start image, and a
  fresh-open segment whose outgoing join is ALSO a cut may pair it with an end image (`flf`);
  what it does NOT get is a free-floating `keyframe`-role entry placed anywhere along the
  timeline — that needs `"anywhere"` instead.
- **Video media is images-only for `first`/`last`/`keyframe`** — a video-typed reference is
  rejected; only `ic_lora.reference` accepts a clip.

## See also

- [Preset Authoring Guide](presets.md) — `preset.yml`, `vars:`, forms, pipeline templating.
- [Prompt Expansion](prompts.md) — the `{a|b}` dynamicprompts grammar Video Director
  segments deliberately don't use, and why ComfyUI presets are per-batch.
- [Backends and Engines](backends.md) — Video Director targets the `native` engine; a
  `comfyui` preset is untouched by any of this.
