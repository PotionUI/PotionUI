# Music Director

Music Director is the composition contract for native music/song presets, mirroring
[Video Director](video-director.md)'s shape: one abstract editor surface, one wire
document, one server-side normalizer gating what a submission is allowed to say against
what the preset actually declares. A preset opts in by declaring `vars.music_director`
(see below); nothing changes for a preset that doesn't.

This is the authoritative reference for the wire document and the capability declaration.
Where a detail depends on a source file, that file is named so you can double-check.

**This document covers Phase 1 only: the backend contract.** The frontend editor and the
first preset (YuE) that consumes this contract land separately; `compile_sections_to_lyrics`
(below) is not wired into any pipe yet.

## Composition modes

A preset declares which of these it supports (`vars.music_director.modes`, below). Unlike
Video Director's `director` mode, there is no capability-driven style fork here — every
mode's shape is fixed by its name.

| Mode | What it produces |
|---|---|
| `t2m` | Text-to-music: a free-form `description` only, no lyrics, no sections, no references. Instrumental. |
| `song` | A single full-song generation. Lyrics via `sections` — either a plain single section (the common case) or a short list — plus the global `description` as the style prompt. `references` only when the preset also declares `style` (see below). |
| `style` | Reference-audio-conditioned generation: one or more `references` required, optionally capped by `max_reference_seconds`. |
| `extend` | Continuation of an existing track: `extend_source` is required. |
| `repaint` | Regenerate a time range of an existing track in place: `repaint` (`source` + `start`/`end`) is required. |
| `director` | A structured section timeline (`sections`, non-empty, capped by `max_sections`) that **compiles to a single tagged-lyrics generation** (`compile: "single_shot"`, the only value implemented today — see [Derived: the compiler](#derived-the-compiler)). Per-section `style_hint`s and reference selection are both capability-gated. |

`song` and `director` are the two modes that carry `sections`; every other mode rejects the
key outright if the document sends it.

## The document contract

`form_data.music_director` is validated and canonicalized by
`normalize_music_director(document, capabilities, storage_dir, form_data=None)`
(`src/features/music_director/normalize.py`), called from
`GenerationOrchestrator.start_generation()` before a generation record is created — the same
point in the pipeline Video Director's normalizer runs at, and immediately after it. It
raises `MusicDirectorValidationError` — a `ValueError` subclass carrying **every** problem
found, not just the first (`str(exc)` joins them with `; `) — which the generation
controller turns into a `400 validation_error` response before anything is persisted or
queued. On success it returns a **new, canonical** dict; the input is never mutated, and
`form_data['music_director']` is replaced with the canonical version for everything
downstream. `form_data` itself is accepted for signature parity with
`normalize_video_director` but currently unused — see [`references`](#references) for why.

```jsonc
{
  "schema_version": 1,
  "mode": "t2m" | "song" | "style" | "extend" | "repaint" | "director",
  "description": "warm 90s boom-bap, vinyl crackle, female vocal",
  "sections": [                          // song (optional) / director (required) only
    {
      "id": "sec-1",
      "kind": "intro" | "verse" | "pre_chorus" | "chorus" | "post_chorus" | "bridge" | "instrumental" | "solo" | "outro",
      "lyrics": "...",
      "style_hint": "...",                // director only, gated by per_section_prompts
      "duration_hint": 16.0,               // director only, gated by section_duration_hints; seconds, > 0
      "references": ["ref-1"]              // director only, gated by references: "per_section"
    }
  ],
  "references": [                        // style (required) / song (gated) / director (gated)
    { "id": "ref-1", "media": { "path": "...", "relative_path": "...", "type": "audio" } }
  ],
  "extend_source": { "media": {...} } | null,   // extend only
  "repaint": {                                   // repaint only
    "source": { "media": {...} },
    "start": 12.0, "end": 20.0
  } | null,
  "settings": {
    "duration": 120,
    "seed": -1,
    "bpm": 92,               // only when settings.bpm capability is true
    "key": "C minor",        // only when settings.key capability is true
    "time_signature": "4/4"  // only when settings.time_signature capability is true
  }
}
```

### `schema_version`

Must be exactly `1`. Missing → `"missing schema_version"`. Any other value → an error
telling the caller the document was produced by a client newer than this server
understands, same as Video Director.

### `mode`

Must be a key of `capabilities.modes`. If `capabilities` carries no `modes` map at all, no
mode validates — the preset simply hasn't opted in. Unlike Video Director, an unresolvable
mode raises immediately (nothing else in the document is meaningfully validatable without
it), so `MusicDirectorValidationError` in that case carries exactly one error.

### `description`

Free-form style text, every mode. Defaults to `""`. Must be a string if present. By default an
empty description is a valid (if useless) request — the same way an empty `t2v` prompt is valid
in Video Director — UNLESS the submitting mode's capability block sets `description_required:
true` (see [Preset capability declaration](#preset-capability-declaration)), in which case an
empty/whitespace-only description is rejected: `"description: this mode requires a non-empty
description -- describe the music before generating"`. A family whose generator has no other
conditioning signal to fall back on (no audio input, and — unlike a purely visual `t2v` — lyrics
alone aren't a substitute for the style prompt) declares this so the gap surfaces here rather
than as that pipe's own late `validate_config` failure.

### `sections`

Only `song` and `director` accept this key; every other mode rejects a non-null value
outright (`"sections: mode 'extend' does not accept sections"`, etc).

- **`song`** — `sections` may be a single section **object** (not a list) as shorthand for
  "one section, the whole song"; the normalizer wraps it into a one-item list before
  further validation. A list of several sections is also accepted (a song with an explicit
  verse/chorus structure, still one generation). At least one section — object or list
  entry — is required; an absent/empty `sections` is an error naming the mode.
- **`director`** — `sections` must be a non-empty list, capped by the mode's `max_sections`
  (default `12`).

Each section, either mode:

- `id` — defaults to `section-{index}` when omitted.
- `kind` — one of `intro`/`verse`/`pre_chorus`/`chorus`/`post_chorus`/`bridge`/`instrumental`/`solo`/`outro`;
  defaults to `verse` when omitted, and any other value is an error (silently coerced to
  `verse` in the canonical output, same as an invalid Video Director `sub_type` clears to
  `None` — the error is what tells the caller, not the fallback value).
- `lyrics` — a string, defaults to `""`.
- `style_hint` — a per-section style override. Only accepted when the mode declares
  `per_section_prompts: true`; sending one otherwise is an error, not a silent drop (a
  caller that thinks its per-section hint is taking effect and isn't needs to know — the
  same reasoning Video Director's `per_segment_loras` gate uses).
- `duration_hint` — a per-section duration override. Only accepted when the mode declares
  `section_duration_hints: true`; sending one otherwise is an error, same reasoning as
  `style_hint`'s gate above. Must be `> 0` if given. Advisory only; nothing downstream
  currently derives a real duration from it (unlike Video Director's chain-style segment
  `frames`, there is no "sum the sections" pass here yet) -- the only consumer is the
  frontend arrangement rail's proportional block widths.
- `references` — only accepted when the mode is `director` **and** its `references`
  capability is exactly `"per_section"` (see [`references`](#references) below); the value
  is a list of reference `id`s, each of which must appear in the document's own top-level
  `references` pool. Not a media object, not a `form_media` pointer (unlike Video Director's
  segment `references`) — the pool is inline on this document, so selecting from it is just
  naming an `id` already declared there.

### `references`

Unlike Video Director's whole-film reference pool (which lives on separate form fields and
is addressed by `capabilities.reference_fields`), a Music Director document's `references`
pool is **inline on the document itself** — this is why `normalize_music_director` accepts
`form_data` but doesn't currently read it.

Whether the key is accepted, and whether it's required, depends on `mode`:

| Mode | Accepted? | Required? |
|---|---|---|
| `style` | always | yes — at least one entry |
| `song` | only when the preset's `modes` also declares a `style` mode | no |
| `director` | only when the mode's `references` capability is `"whole"` or `"per_section"` | no |
| `t2m` / `extend` / `repaint` | never | — |

A `song` preset combining lyrics with a reference track (e.g. "sing this melody with these
lyrics") declares both `song` and `style` in its `modes` map — `song`'s own gate checks for
`style`'s *presence*, not any value on it.

Each entry: `{ "id": "...", "media": {...} }`. `media` is resolved to an on-disk path
exactly like Video Director's `media[].media` (see [Path
resolution](#path-resolution-and-traversal)) — `id` is a caller-assigned label a section's
`references` selection (above) or `director`'s "per_section" gate points back at; it is not
derived or validated against anything but uniqueness-by-usage.

`style` mode's `max_reference_seconds` (if the mode capability declares one) is checked only
when a reference's `media` object carries a client-declared `duration_seconds` hint — real
audio duration isn't derivable here without decoding the file, so an entry with no such hint
passes through unchecked. This mirrors Video Director's `audio[].length`, which is likewise
taken on the caller's word rather than probed. Exceeding a *declared* hint is an error naming
both the declared length and the cap.

An empty `references: []` on `style` mode is the same error as an absent one — the mode
"requires at least one reference" either way.

### `extend_source`

Required, `extend` mode only: `{ "media": {...} }`. Any other mode sending a non-null value
is rejected. `media` resolves the same way as every other media reference in this contract.

### `repaint`

Required, `repaint` mode only: `{ "source": { "media": {...} }, "start": 12.0, "end": 20.0 }`.
Validated shape-only: `start`/`end` are required numbers with `0 <= start < end` — there is
no check against the source track's real duration (decoding it is the generator's business,
same reasoning as `references`' `max_reference_seconds` above and Video Director's `audio`
role). Any other mode sending a non-null `repaint` is rejected.

### `settings`

- `duration` — defaults from `capabilities.limits.default_duration` (fallback `120`), must
  be `> 0` and, when `capabilities.limits.max_duration` is set, no greater than it.
- `seed` — `-1` or missing resolves to a freshly rolled seed via `generate_seed()`
  (`src/platform/util/latents.py`, the same helper Video Director and the prompt expander
  use), written back into the canonical document. An explicit non-`-1` seed passes through
  unchanged.
- `bpm` / `key` / `time_signature` — each accepted **only** when the preset's
  `capabilities.settings` block declares that key `true` (see [Preset capability
  declaration](#preset-capability-declaration)). Sending one the preset hasn't declared is a
  *teaching* error: `"settings.bpm is not supported by this preset -- describe it in the
  description text instead"` — the model reads it out of the free-form prompt like every
  other knob this preset doesn't expose structurally. When declared, `bpm` must be a
  positive number and `key`/`time_signature` must be non-empty strings. All three default to
  `null` in the canonical output when absent or rejected.

### Path resolution and traversal

Every `media` object (`references[].media`, `extend_source.media`, `repaint.source.media`)
is a `{path, relative_path, ...}` reference, resolved identically to Video Director's
`media[].media`: if `path` is given, absolute, and exists, it's kept as-is; otherwise the
given `path`/`relative_path` is joined onto `storage_dir` (the per-user file storage root —
`Settings.get_file_storage_directory(user_id)`) and resolved. The resolved path must
land **inside** `storage_dir` — anything that resolves outside it is rejected — and must
exist on disk. The canonical output rewrites `media["path"]` to the resolved absolute path;
every other key on the reference passes through unchanged.

### Unknown-key policy

Unknown **top-level** keys on the document (anything besides `schema_version`, `mode`,
`description`, `sections`, `references`, `extend_source`, `repaint`, `settings`) are
preserved verbatim in the canonical output. Unknown keys **inside** known structures are
dropped; only the keys documented above survive normalization there.

## Derived: the compiler

`compile_sections_to_lyrics(sections)` (`src/features/music_director/normalize.py`) is a
pure function, **not called by `normalize_music_director`** and not wired into any pipe yet
— it exists in this phase purely so the tagged-lyrics format is pinned down and unit-tested
ahead of the preset work that will call it.

It serializes a `director`-mode section list into the single document a
`compile: "single_shot"` mode submits as one generation: each section becomes a lower-case
bracket tag (`[chorus]`) followed by its `lyrics`, sections separated by one blank line —
the tagged-lyrics family both YuE and Music3 accept. `compile: "single_shot"` is the only
value implemented; a future `"windowed"` compile style (per-section generations, stitched)
is out of scope for this phase and unimplemented.

## Preset capability declaration

A preset opts in by declaring `vars.music_director` in `preset.yml` (see [`vars:`](presets.md#presetyml-reference)
in the Preset Authoring Guide). This is the `capabilities` dict `normalize_music_director`
validates the document against — a preset that omits a mode from `modes` simply never
accepts that mode's documents, and a preset that omits `vars.music_director` entirely
accepts none of them.

```yaml
vars:
  music_director:
    preset_modes: ["song"]            # which of this preset's own modes/ this applies to
    modes:                            # dict keyed by composition mode; presence = enabled
      t2m: {}
      song: {}
      style:
        max_reference_seconds: 30
      extend: {}
      repaint: {}
      director:
        max_sections: 12
        per_section_prompts: true
        section_duration_hints: true  # gates the per-section duration_hint (default false)
        references: "whole" | "per_section" | null
        compile: "single_shot"        # the only implemented value today
        description_required: false   # reject an empty description in this mode (default false)
    settings:                         # which musical settings are real fields vs prompt-text-only
      bpm: false
      key: false
      time_signature: false
    limits:
      default_duration: 120
      max_duration: 300
      sample_rate: 32000
      stereo: true
    preset_mode_overrides:            # optional, see "Preset mode overlays" below
      <preset_mode>: { ... a partial music_director block ... }
```

- `preset_modes` — which of the preset's own `modes:` (the `preset.yml` list) this
  capability block applies to. Frontend-only; the normalizer ignores it.
- `modes` — presence of a key enables that composition mode. An empty object (`{}`) means
  "enabled with no extra capabilities" (the normal shape for `t2m`/`extend`/`repaint`).
  Recognized keys on `style`: `max_reference_seconds` (metadata, see
  [`references`](#references)). Recognized keys on `director`: `max_sections` (default
  `12`), `per_section_prompts` (bool), `section_duration_hints` (bool, default `false` —
  gates per-section `duration_hint`, same shape as `per_section_prompts`), `references`
  (`"whole"`/`"per_section"`/`null`, absent means `null`), `compile` (frontend/pipe-facing;
  the normalizer doesn't read it — only `compile_sections_to_lyrics`'s caller will, once
  wired). `description_required` (bool, default `false`) is recognized on every mode — see
  [`description`](#description).
- `settings` — `bpm`/`key`/`time_signature`, each a bool gating whether that field is
  accepted structurally in `settings` (see [`settings`](#settings) above). Absent keys
  default to `false` (not accepted).
- `limits` — `default_duration`, `max_duration` consumed exactly as described under
  [`settings`](#settings) above. `sample_rate`/`stereo` are pipe-facing metadata; the
  normalizer doesn't read them.

### Preset mode overlays

Identical mechanism to Video Director's (`docs/video-director.md#preset-mode-overlays`):
`apply_preset_mode_overlay(capabilities, preset_mode)`
(`src/features/music_director/normalize.py`) computes the effective capability set for a
request — the base block above, shallow-merged with
`preset_mode_overrides[<the request's preset mode>]` when one exists — before
`normalize_music_director()` ever sees the document. `normalize_music_director()` itself
always receives an already-merged `capabilities`; it has no idea `preset_mode_overrides`
exists. See Video Director's writeup for the full merge-rules rationale (shallow per
top-level key, per-composition-mode merge under `modes`, explicit-`null`-vs-absent
semantics) — the two overlay functions are intentionally duplicated (not shared) rather than
having this feature package depend on a sibling one for a five-line generic merge.

### Example: a Music3-like preset (song + director, no settings knobs, no references)

```yaml
vars:
  music_director:
    preset_modes: ["song"]
    modes:
      # `description_required: true` everywhere: the checkpoint has no audio
      # encoder, so `description` is the only conditioning signal the
      # generator ever gets -- lyrics alone never suffice.
      t2m: {description_required: true}
      song: {description_required: true}
      director:
        description_required: true
        max_sections: 12
        per_section_prompts: true
        compile: "single_shot"
        # NO `references` key: MiniMax-Music3's shipped checkpoint has no
        # audio encoder, so reference audio cannot condition anything --
        # declaring `references: "whole"` here would be a capability the
        # normalizer accepts but nothing downstream can honor.
    settings:
      bpm: false
      key: false
      time_signature: false
    limits:
      default_duration: 60
      max_duration: 360
      sample_rate: 44100
      stereo: true
```

### Example: an ACE-like preset (adds style/repaint, structured bpm)

```yaml
vars:
  music_director:
    preset_modes: ["song"]
    modes:
      t2m: {}
      song: {}
      style:
        max_reference_seconds: 30
      repaint: {}
      director:
        max_sections: 12
        per_section_prompts: true
        references: "per_section"
        compile: "single_shot"
    settings:
      bpm: true
      key: false
      time_signature: false
    limits:
      default_duration: 120
      max_duration: 300
      sample_rate: 44100
      stereo: true
```

### Example: a YuE-like preset (song + extend, no director)

```yaml
vars:
  music_director:
    preset_modes: ["song"]
    modes:
      t2m: {}
      song: {}
      extend: {}
    settings:
      bpm: false
      key: false
      time_signature: false
    limits:
      default_duration: 120
      max_duration: 180
      sample_rate: 32000
      stereo: true
```

## v1 limits

These are current, not architectural — expect some to loosen as the pipes mature.

- **`compile_sections_to_lyrics` is unwired.** Phase 1 lands the contract and the compiler
  as a pure, tested function; no preset or pipe consumes it yet.
- **`compile: "windowed"` is undeclared and unimplemented.** Every `director` mode today is
  `compile: "single_shot"` — a whole section timeline compiles to one generation. A future
  per-section-generation style is out of scope for this phase.
- **`duration_hint` is advisory only.** Nothing derives a document-level duration from
  summed section hints (unlike Video Director's chain-style `frames` summation).
- **No sample-rate/channel validation.** `limits.sample_rate`/`stereo` are declared metadata
  a pipe reads directly; the normalizer doesn't enforce either.

## See also

- [Video Director](video-director.md) — the sibling contract this one mirrors in shape and
  idiom; read it for the fuller worked examples of the overlay mechanism and the
  path-resolution discipline both modules share.
- [Preset Authoring Guide](presets.md) — `preset.yml`, `vars:`, forms, pipeline templating.
- [Prompt Expansion](prompts.md) — the `{a|b}` dynamicprompts grammar; `description` and
  section `lyrics`/`style_hint` in this contract are literal, same as Video Director's
  segment prompts, for the same reason (per-image variant sampling doesn't compose with a
  single fixed generation).
