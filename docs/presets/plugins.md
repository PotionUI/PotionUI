---
title: Plugin-Contributed Presets
category: Presets / Models
category_order: 70
order: 20
---

# Plugin-Contributed Presets

Two distinct ways a plugin extends the preset system: shipping a whole new preset (a
`presets:` manifest root, scanned exactly like the core tree — see
[Canonical layout](../presets.md#canonical-layout)), and contributing one or more MODES onto
a preset it doesn't own (`preset_modes:`, this page).

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

