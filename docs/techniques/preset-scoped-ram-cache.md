---
type: technique
title: Preset-Scoped Model RAM Cache
category_group: Memory
status: stable
families: [all-native]
authors: []
paper: null
reference_impl: null
knobs: []
related: []
---

# Preset-Scoped Model RAM Cache

Loading a model's weights from disk into host RAM is slow — for a large checkpoint, tens of
seconds. To avoid re-reading a model from disk on every generation, PotionUI keeps recently loaded
models cached in host RAM (not VRAM) between generations, keyed by a fingerprint of everything that
would make the cached copy stale (file paths, LoRA configuration, dtype, and so on). A generation
that reuses the same model with the same configuration gets it back from RAM immediately instead of
reloading from disk; GPU placement of a cached model is decided separately, at the moment it's
actually needed on the GPU.

By default, this cache is scoped to the currently active preset: switching to a different native
preset evicts every RAM-cached model that belonged to the previous preset, so host RAM holds only
the active preset's models rather than accumulating every model you've ever touched in a session.
Models loaded outside a native generation (for example by the ComfyUI backend, or during a warmup)
are never tagged with a preset owner and are never auto-evicted by this policy — only genuine LRU
pressure removes them. Independent of preset switching, the cache also evicts on plain host-RAM
pressure: before a new model load, PotionUI checks live free system RAM and evicts least-recently-used
entries until there's enough headroom, so a single large load can't push the box into a
near-freeze even if nothing has changed presets.

## When to use it

This isn't something you turn on — it runs automatically for every native generation. It matters
most when you're iterating repeatedly on the same preset/model (fast, no reload) versus switching
between different presets in the same session (each switch evicts the previous preset's cached
models from RAM, so the next generation on the old preset reloads from disk).

## How to enable it

There is no preset key or environment variable for this, and no dedicated admin UI control — the
Settings tab (`frontend/src/routes/admin/components/SystemSettingsTab.svelte`) only surfaces a
hand-picked allowlist of settings, which does not include `model_cache_scope`. The code does read a
`model_cache_scope` setting (`"preset"`, the default eviction-on-switch behavior described above,
versus `"global"`, which keeps every preset's models cached until plain RAM pressure forces
eviction) through the standard settings-by-key API, and the row IS seeded — migration
`080_add_model_cache_scope_setting.py` inserts it (default `"preset"`) whenever the `settings` table
exists. That makes it reachable through the generic `PUT /api/settings/model_cache_scope` endpoint
(`{"value": "global"}`), which any admin account can call directly even without a UI control for it —
it's a `SYSTEM`-typed setting, so `src/features/settings/routes.py`'s admin check is the only gate.
Treat this as a setting with no dedicated admin control today, changeable only by calling the
settings API directly as an admin.

## Tradeoffs and limitations

- Switching presets mid-session costs a reload the next time you switch back, since the previous
  preset's models are evicted from RAM (not just VRAM) on the switch.
- The `model_cache_scope: "global"` alternative exists in the code and its settings row is seeded,
  but there is no admin UI control for it — opting into keeping multiple presets' models cached
  simultaneously means calling `PUT /api/settings/model_cache_scope` directly as an admin.
- Eviction under RAM pressure is LRU-based and can still evict a model you're about to reuse if
  enough other large loads intervene first.
- This is host-RAM caching only; it says nothing about whether a model is resident on the GPU at
  any given moment — that's a separate placement decision made at generation time.
