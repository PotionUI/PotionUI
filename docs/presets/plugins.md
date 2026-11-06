---
title: Plugin-Contributed Presets
category: Presets / Models
category_order: 70
order: 20
---

# Plugin-Contributed Presets

Three ways a plugin extends the preset system: shipping a whole new preset (a `presets:`
manifest root, scanned exactly like the core tree — see
[Canonical layout](../presets.md#canonical-layout)), contributing one or more MODES onto a preset
it doesn't own (`preset_modes:`), and attaching fields, pipes and prompt resources to modes that
already exist (`preset_extensions:`). The last two are described on this page.

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

**Prompting declared by the mode itself**: a contributed mode cannot edit the target's `preset.yml`,
so three optional keys in the mode's own default `modes/<name>/form.yml` (not a variant's) carry what
a core mode puts in the target's `vars:` and `llm:` blocks. They are read only for a contributed mode
and merged into the target preset when the contribution is accepted:

```yaml
name: "custom"
mode_label: "Restyle"
prompt: "optional"
llm_guide: |
  The prompt is optional plain English, one sentence added to the request.
segment_templates: []
```

- `prompt:` is `required` (the default), `optional` or `none`. `optional` adds the mode to
  [`vars.optional_prompt_modes`](manifest.md#varsoptional_prompt_modes-modes-where-the-prompt-is-optional),
  `none` adds it to [`vars.promptless_modes`](manifest.md#varspromptless_modes-modes-that-need-no-prompt).
- `prompt_label:` and `prompt_placeholder:` name what the prompt box is for and give an example, see
  [What the prompt is for](manifest.md#what-the-prompt-is-for-prompt_label-prompt_placeholder).
- `llm_guide:` becomes `llm.modes.<name>.guide`, the prompting guide that replaces the preset-level guide
  while this mode is active.
- `segment_templates:` becomes `vars.prompt.modes.<name>.segment_templates`, the same flat replace as for a
  core mode; an empty list means "no preset templates for this mode".

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

A contributed mode's fields may keep the `@config:checkpoint_tags`/`text_encoder_tags`/`vae_tags`/`lora_tags` indirection, which resolves against the target preset's `configuration:`.

## Extending existing modes

`preset_extensions:` attaches pieces to modes a preset already has, without replacing them: form
fields go into an existing tab or section, pipes are added to or patched in the mode's pipeline,
and prompt resources and Director reference fields are appended. Use it when a plugin adds one
capability to a mode (an extra reference input, a post-processing pass) rather than a whole new
mode.

```yaml
# manifest.yml
preset_extensions:
  - target: "4TK1KBQZ2XMB8ME0PTMXS1YJQP"   # the target preset's id
    modes: ["txt2img"]                     # mode names on that preset, core or plugin-contributed
    file: "extensions/krea2.yml"           # relative to the plugin root
```

### Anchors

Fields are placed by a tab's or section's `anchor:`, a stable key that never changes with the
copy. Every shipped preset tab carries one (the name of its `tabs/<anchor>.yml` file, or its label
in lowercase when the tab is inline); a section is targetable once its preset gives it one:

```yaml
- type: "tab"
  anchor: "advanced"
  label: "Advanced"
  children: "{{ paths.preset }}/modes/txt2img/tabs/advanced.yml"

- type: "section"
  anchor: "sampling"
  label: "Sampling"
```

`anchor:` is valid only on `tab` and `section`, must be lowercase letters, digits, `_` or `-`, and
must be unique per type within one form. It is not sent to the frontend and is not a form value,
so adding one never changes a form's `form_data`.

### The extension file

```yaml
fields:
  - into: {tab: "advanced"}        # or {section: "<anchor>"}
    after: "cfg"                   # or before: "<field name>"; neither appends to the end
    fields:                        # or file: "fields/grain.yml" (a file with a `fields:` list)
      - name: "grain_intensity"
        type: "slider"
        label: "Grain"
        description: "Film grain amount."
        default: 0.3
        configuration: {min: 0.0, max: 1.0, step: 0.05}

pipes:
  - add:                           # a full pipe node, with its own id
      name: "film_grain"
      id: "grain"
      enabled: "{{ form.grain_intensity > 0 }}"
      input:
        - ["image", "generator", "image"]
      configuration:
        intensity: "{{ form.grain_intensity }}"
    after: "generator"             # or before: "<pipe id>"; neither appends to the end
  - patch: "generator"             # a pipe id, or a name used by only one pipe
    input_add:
      - ["mask", "loader", "mask"]
    configuration_add:
      detail_strength: 0.0

prompt_resources:                  # appended to each named mode's prompt_resources
  - {field: "grain_refs", kind: "image", token: "<grain @>"}

director_reference_fields: ["grain_refs"]
```

- **Fields** follow the normal schema rules of [Form Fields](forms.md): they bring their own
  `when:`, `reactions:`, `audience:`, `hidden_when_video_director:` and so on. `after`/`before`
  names a field anywhere inside the target tab or section, including inside one of its rows or
  sections; the new fields go next to it in the same container. `{{ paths.preset }}` inside an
  extension's `children:` resolves to the folder holding the extension file, `{{ paths._shared }}`
  to the core shared tree. Each field is applied to every form variant of each named mode.
- **Pipes** are add-only. An added pipe needs an `id` no pipe in the mode uses as its id or name,
  and may share a `name` with an existing pipe only if nothing in the mode wires to that pipe by
  name. A patch may only add input params and configuration keys the pipe does not set yet; a key
  set by core or by an earlier extension is a conflict.
- **`prompt_resources`** use the shape of `preset.yml`'s
  [prompt resources](manifest.md#prompt-resources), and every `field` must exist in the mode.
- **`director_reference_fields`** are appended to the Director's `reference_fields` for each named
  mode (written to `vars.video_director.preset_mode_overrides.<mode>.reference_fields`, starting
  from what that mode has today). The mode must be a Video Director mode, and each name must be a
  field of the mode not already listed.

### Order, conflicts and provenance

- Extensions apply after `preset_modes:` contributions, so a plugin-contributed mode can be
  extended too. They run in the same order: by plugin id, then declaration order.
- Each extension is all-or-nothing. A missing tab, section, field or pipe, a mode the preset does
  not have, a field name or anchor the form already has, a conflicting pipe key or prompt
  resource, or a field that fails schema validation rejects the whole extension. The preset loads
  unchanged, and the error is recorded in `load_errors` under
  `plugin '<id>' preset_extensions (target '<preset>', file '<file>')`.
- A target preset that is not installed means the extension is simply absent, not an error.
- An extension only applies while its plugin is enabled; disabling the plugin reloads presets
  without it.
- Every field an extension adds carries `source_plugin: <plugin id>` in the form schema the
  frontend receives; added pipes carry it on their `PipeTemplate`.

`scripts/preset_lint.py` and `GET /api/developer/presets/lint` re-run the same merge and report a
rejected extension as an error. They also check every added pipe's name against the pipe catalog
(an error) and warn about configuration keys a pipe does not declare. To see the merged pipeline,
render the mode with the plugin enabled:

```bash
python scripts/preset_render.py <preset-id> <mode> --plugin-dir <path/to/plugin>
```
