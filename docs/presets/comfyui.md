---
title: ComfyUI Presets
category: Presets / Models
category_order: 70
order: 19
---

# ComfyUI Presets

Everything in [preset.yml](manifest.md), [Forms](forms.md), and [Pipelines](pipelines.md)
applies to `engine: comfyui` presets too — same `preset.yml`, same form/tab files, same lint
and render tooling. What's different is `modes/<mode>/pipeline.yml`: instead of native pipes
it drives one `comfyui` pipe that executes a raw ComfyUI workflow graph, with form values
patched into that graph at generation time. The worked example for everything in this section
is `content/plugins/marketplace/comfyui-backend/presets/SDXL/` — read it alongside this
chapter. The importer's own implementation internals (GGUF compatibility, failure-safe
publication, exact-integer transport, schema-consistency checks, and the node catalog CLI)
live in `content/plugins/marketplace/comfyui-backend/README.md` — this page covers what a
preset author needs, not how the plugin is built.


Everything above applies to `engine: comfyui` presets too — same `preset.yml`, same form/tab
files, same lint and render tooling. What's different is `modes/<mode>/pipeline.yml`: instead of
native pipes it drives one `comfyui` pipe that executes a raw ComfyUI workflow graph, with form
values patched into that graph at generation time. The worked example for everything in this
section is `content/plugins/marketplace/comfyui-backend/presets/SDXL/` — read it alongside this
chapter. `content/plugins/marketplace/comfyui-backend/presets/QwenImage/` adds an `img2img` mode
(`LoadImage`) and a subgraph workflow; `FluxKlein9b` shows subgraph node ids.

### The two-minute path: import a workflow

If you already have a working ComfyUI graph, don't hand-write the preset — import it.

1. **Export the workflow.** In the ComfyUI UI, use **Workflow → Export (API)** — if you don't see
   it, turn on **Dev mode options** in ComfyUI's settings first. This is the graph the ComfyUI
   server actually executes: a flat `{"<node_id>": {"class_type": ..., "inputs": {...}}}` mapping
   keyed by node id, values addressed by name (`4.inputs.ckpt_name`) — exactly what `workflow_file`
   and `field_mappings` below expect, and what the SDXL preset's
   `modes/txt2img/files/workflows/txt2img.json` is, unedited. The plain **Export** / **Save**
   option saves the *UI* graph instead (node positions, a `links` array of
   `[id, from, from_slot, to, to_slot, type]` tuples, `widgets_values` arrays with no input names) —
   this importer only ever accepts the API shape, and rejects a UI export immediately with
   "This is the ComfyUI UI export. Enable Dev mode options in ComfyUI settings and use Workflow →
   Export (API), then import that file." A workflow that uses ComfyUI **subgraphs** works fine —
   subgraph node ids (`<instance_id>:<inner_id>`) come through as-is in the API export.

2. **Analyze it.** The `comfyui-backend` plugin exposes two admin-only import endpoints (also
   accessible via the **Import workflow** tab in Administration → Plugins → ComfyUI Backend — see
   "Importing it as a preset" in the user-facing [Bringing Your Own ComfyUI Workflows](../user/comfyui-presets.md) page).
   `POST /api/plugins/comfyui-backend/presets/import/analyze` takes the exported (API-format) JSON
   and returns a list of `candidates` — one per detected input, each with its `node_id`,
   `class_type`, `node_title`, `input_name`, `current_value`, `value_type`, a
   `suggested_field_type` / `suggested_field_name` / `suggested_label` / `suggested_config`, a
   `role` (e.g. `prompt`, `sampler_param`, `model`), and whether it's `obvious` enough to
   pre-select — plus the detected `mode`, `node_count`, `sampler_node_id`, any detected
   `lora_chain`, and `format` (always `"api"`).

   The response also carries `default_form`/`default_history` — a ready-to-submit `form`/`history`
   (see step 3) built from the `obvious` candidates: resolution as one field with two mappings, each
   model loader field grouped under a "Models" group, and a detected LoRA chain collapsed into one
   `lora_picker` field. This is the wizard's starting point, not a requirement: submit it back
   verbatim for the importer's best guess, or design a different `form`/`history` entirely.

   **Combo (dropdown) inputs.** With a reachable backend's `object_info` available (see "Schema
   consistency" in the `comfyui-backend` plugin's own README), a node input backed by a fixed
   choice list — ComfyUI's own combo widget —
   is recognized under either of its two equivalent `/object_info` spellings: the inline
   `[[opt1, opt2, ...], {...}]` form, and the named `["COMBO", {"options": [...]}]` form used by,
   among others, an API-node's model picker. Either spelling suggests a `select` field carrying
   the live option list (or, when the input is a recognized model-file name or its options mostly
   look like model filenames, a `model` field instead — never both). The workflow's own current
   value is always kept verbatim, even when it doesn't case-match any option. A combo this can't
   safely resolve to a complete, static option list — `options` missing, not a list, empty, or
   containing anything but a plain string/number/boolean, or a `remote`-marked (dynamically
   populated) combo — is left as its plain literal-field guess instead of guessing at values or
   fetching them live; a connected input never becomes a field candidate at all, combo or not.

   ```bash
   curl -s -X POST http://localhost:7680/api/plugins/comfyui-backend/presets/import/analyze \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d "{\"workflow\": $(cat txt2img.json)}"
   ```

3. **Import it.** Call `POST /api/plugins/comfyui-backend/presets/import` with the original
   `workflow`, a `form`, a `history`, and `model_family`/`variant`/`display_name`. `form` is
   `{tabs: [{id, label, icon?, items: [Item, ...]}, ...]}` — the preset's tab layout, exactly as an
   admin arranges it. An `Item` is one of:

   - `{kind: "field", field_name, field_type, label, default?, config?, mappings}` — a real form
     field. `mappings` is a list of `{node_id, input_name, transform}` (a field can map several
     inputs — resolution maps one field to both a width and a height input); `transform` is one of
     `"none"`, `"strip_model_prefix"` (strips the model picker's depot type directory via the
     `strip_model_dir` filter), `"split_wh_width"` / `"split_wh_height"` (the
     `.split('x')[0|1]` idiom below), or `"seed"` (wires the literal `"@seed"` sentinel regardless
     of the field's own value). A field needs at least one mapping, with one exception:
     `field_type: "lora_picker"` is graph-wired (see the `@loop` recipe below), never a single
     mapped value.
   - `{kind: "row", columns: 2|3|4, items}`, `{kind: "group", title, items}`,
     `{kind: "section", title, collapsed, items}` (a collapsible group), `{kind: "header", text}` —
     layout containers, nestable, mapping onto the real `row`/`group`/`accordion`/`header` field
     types this chapter documents elsewhere.

   `history` is `[{field, label, format, template?}, ...]`, in display order — which fields get
   recorded to a generation's `param_emitter` (see "Configure the `comfyui` pipe" below) and how:
   `format` is `"as_is"`/`"number"`/`"wxh"` (the field's own value, verbatim), `"model_name"` (the
   bare filename, path stripped), `"list"` (a LoRA-list field's active count and names), or
   `"jinja"` (a `template` you write yourself, used verbatim as the emitted value).

   Prompt (positive and negative, found by following the sampler node's own conditioning links),
   seed, and batch size are never `Item`s — the wizard shows them locked, and they wire in
   automatically (bound to `@seed`/`form.quantity`) regardless of what `form` contains. Any input
   left out of every field's `mappings` keeps its literal baked-in value from the copied workflow
   JSON; a `LoadImage` node needing a real field (not a baked-in placeholder) is enforced for you —
   a workflow with an image input but no `image`-typed field in `form` is rejected with a clear 400.
   The call writes `content/presets/local/<model_family>/<variant>/` — never into a plugin
   directory, and never overwriting an existing preset — lints it immediately, and reloads the
   running preset catalogue, so the result shows up without a restart.

   ```bash
   curl -s -X POST http://localhost:7680/api/plugins/comfyui-backend/presets/import \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{
       "workflow": '"$(cat txt2img.json)"',
       "form": {
         "tabs": [
           {"id": "generation", "label": "Generation", "items": [
             {"kind": "field", "field_name": "checkpoint", "field_type": "model", "label": "Checkpoint",
              "config": {"model_type": "checkpoint"},
              "mappings": [{"node_id": "4", "input_name": "ckpt_name", "transform": "strip_model_prefix"}]},
             {"kind": "field", "field_name": "steps", "field_type": "slider", "label": "Steps", "default": 20,
              "mappings": [{"node_id": "3", "input_name": "steps"}]}
           ]}
         ]
       },
       "history": [
         {"field": "checkpoint", "label": "Checkpoint", "format": "model_name"},
         {"field": "steps", "label": "Steps", "format": "number"}
       ],
       "model_family": "MyModel",
       "variant": "standard",
       "display_name": "MyModel Standard"
     }'
   ```

   The response is `{preset_id, path, mode, lint: {errors, warnings}}` — check `lint` before
   assuming the import is clean.

   The written `preset.yml` also gets a `requirements:` block (see [Requirements](requirements.md)),
   inferred from the whole workflow graph: one `comfyui_node` entry for every node class outside a
   small built-in allowlist (a custom node pack the target server may not have installed), and one
   `comfyui_model` entry for every checkpoint/UNET/CLIP/VAE/LoRA file a loader node references -
   except a loader input a `model` field's picker drives, whose file is chosen per generation and
   whose workflow literal is therefore not required (the wizard's Requirements step previews the
   same set, from the form as designed so far). Both types are registered by this plugin
   (`backend/requirements.py`) — they check the resolved backend's `GET /object_info` and
   `GET /models/{folder}` live, so a missing custom node or model file surfaces on the preset's
   Requirements panel instead of as a mid-generation pipeline error.

4. **Tweak the generated YAML.** The importer gets you a working skeleton, not a finished preset:
   check which fields you picked up (a "Power Lora Loader"-style single multi-LoRA node, or a
   pass-through node sitting between a LoRA chain and the sampler, aren't detected yet), rename
   fields, move things between tabs, and add anything structural the importer can't infer —
   conditional reference images, subgraph rewrites. The rest of this chapter is the reference for
   doing that by hand.


### The manual path: build a preset by hand

**Scaffold**, choosing `content/presets/local` so nothing lands in a plugin directory:

```bash
python scripts/preset_new.py MyModel/standard --engine comfyui --category image --modes txt2img \
    --root content/presets/local
```

**Drop the exported API JSON** under `files/workflows/` — either preset-level
(`files/workflows/txt2img.json`, referenced as `{{ paths.preset }}/files/workflows/txt2img.json`,
the pattern QwenImage uses when one file is shared across modes) or mode-level
(`modes/<mode>/files/workflows/txt2img.json`, referenced as
`{{ paths.preset }}/modes/<mode>/files/workflows/txt2img.json`, what SDXL and FluxKlein9b do).
Neither path is parsed for meaning — `workflow_file` is a plain string, so pick whichever avoids
duplicating the file across modes.

**Configure the `comfyui` pipe** in `modes/<mode>/pipeline.yml`. The standard chain every shipped
ComfyUI preset uses is:

```
dynamic_prompts_renderer → seed_generator → from_iotype → param_emitter → comfyui → output_skipper → gallery
```

`dynamic_prompts_renderer`, `seed_generator`, `from_iotype`, and `param_emitter` are the same
provenance/seed/parameter-tracking pipes a native preset uses — copy that block from SDXL's
`pipeline.yml` verbatim, keyed off `form.seed`/`form.quantity` as usual. `output_skipper` with empty
`rules: []` is a required pass-through in the standard chain (only ComfyUI presets that actually
skip frames on a condition give it rules). The pipe doing the real work is `comfyui`:

```yaml
- name: "comfyui"
  enabled: true
  input:
    - ["seed", "seed_generator", "seed"]
  configuration:
    host: "127.0.0.1"
    port: 8188
    workflow_file: "{{ paths.preset }}/modes/txt2img/files/workflows/txt2img.json"
    timeout: 300
    secure: false
    node_manipulations: [...]   # optional, see below
    field_mappings: [...]       # required, see below
```

`host`/`port`/`secure` here are placeholders — at generation time the pipe actually talks to
whichever backend the user or preset selected for the `comfyui` engine (see
[Backends and Engines](../backends.md)); they only matter if the pipe is ever invoked with no backend
resolved. Node ids (`"4"`, `"6"`) are the top-level keys of the exported JSON; a subgraph node from
ComfyUI's node-group feature is addressed as `"<group_id>:<node_id>"` (FluxKlein9b's KSampler is
`"75:63"`). A node's `_meta.title` can also be used instead of an id, as long as it's unique in the
graph.

**`field_mappings`** — a flat list of `[source, "<node_id>.inputs.<input_name>", type]` triples,
applied in order after the workflow JSON loads:

- `source` is a Jinja2 template evaluated with the same `form`/`generation`/`preset`/`paths`
  context as everywhere else in `pipeline.yml` (see [Pipelines → Template contexts](pipelines.md#template-contexts)), or the literal
  string `"@seed"` — shorthand for the seed value the `comfyui` pipe already received over its
  `input:` wiring (`input: - ["seed", "seed_generator", "seed"]`), not a template.
- `type` casts the rendered value before it's written into the node: `"str"`, `"int"`, `"float"`,
  `"bool"`, or `"image"`. `"image"` is special-cased: the pipe loads the file (a path, a `PIL.Image`,
  or an in-memory upload) and uploads it to the ComfyUI server first, then writes the server-side
  filename ComfyUI returned into `inputs.image` — you never write a path or filename yourself.
- Split a combined form value where the graph wants two inputs, the way SDXL splits its
  `"832x1216"` resolution string:
  ```yaml
  - ["{{ (form.resolution | default('832x1216')).split('x')[0] }}", "5.inputs.width", "int"]
  - ["{{ (form.resolution | default('832x1216')).split('x')[1] }}", "5.inputs.height", "int"]
  ```
- Model-picker values carry their depot type directory (`models/checkpoints/foo.safetensors`);
  strip it with the `strip_model_dir` filter — it keeps any subdirectory underneath and passes a
  bare filename or already-native ComfyUI ref through unchanged:
  ```yaml
  - ["{{ form.checkpoint | strip_model_dir }}", "4.inputs.ckpt_name", "str"]
  ```

**`node_manipulations`** — a list applied to the loaded workflow *before* `field_mappings`, each
entry a `type:` plus an optional `condition:` (a Jinja2 expression or bare `true`/`false`; skipped
manipulations are simply not applied, they don't error). Supported types:

| `type` | Purpose | Required keys |
|---|---|---|
| `add_node` | Insert a new node into the graph | `node_id`, `node_config` (`class_type`, `inputs`, optional `_meta.title`) |
| `update_node_input` | Rewrite one input on an existing node (rewire a connection or change a literal) | `node_id`, `input_key`, `input_value`, optional `type_cast` |
| `remove_node_input` | Delete one input key from a node | `node_id`, `input_key` |
| `remove_node` | Delete a node and every connection into or out of it | `node_id` |
| `bypass_node` | Rewire the node's inputs straight to its consumers, dropping the node itself | `node_id` |
| `reroute_connection` | Point one node's output at a different downstream target | `from_node`, `to_node`, `new_target`, optional `output_index` |

`condition: "{{ form.ref_image_2 | default('') == '' }}"` is how QwenImage's img2img preset drops
an optional reference-image branch (`LoadImage` node plus its two `image2` connections) when the
user didn't provide one — always **remove before rewire**: removing a node nulls out any dangling
references to it, so an `update_node_input` that restores a valid connection must come after the
matching `remove_node` calls, not before.

**The LoRA `@loop` chain** — every shipped ComfyUI preset builds its LoRA stack the same way,
because ComfyUI has no "list of LoRAs" node: it chains one `LoraLoaderModelOnly` node per selected
LoRA between the checkpoint/diffusion-model loader and the sampler. Copy this block (SDXL's
version; see [Pipelines → The `@loop` recipe](pipelines.md#the-loop-recipe-repeated-lora--controlnet-slots) for `@loop` mechanics in general) and change only the
anchor node ids (`4` = checkpoint loader, `3` = KSampler):

```yaml
node_manipulations:
  # One LoraLoaderModelOnly per selected LoRA, chaining each to the previous
  # (the first connects to the checkpoint loader, node 4)
  - "@loop":
      items: "{{ form.loras | default([]) }}"
      template:
        type: "add_node"
        node_id: "lora_{{ loop.index }}"
        node_config:
          inputs:
            lora_name: "{{ item.model | strip_model_dir }}"
            strength_model: "{{ item.strength }}"
            model: ["{% if loop.first %}4{% else %}lora_{{ loop.index0 }}{% endif %}", 0]
          class_type: "LoraLoaderModelOnly"
          _meta:
            title: "LoRA {{ loop.index }}"

  # Point the sampler (node 3) at the end of the chain: the last generated
  # LoRA node, or the checkpoint loader itself when no LoRAs were selected
  - type: "update_node_input"
    node_id: "3"
    input_key: "model"
    input_value: ["{% set loras = form.loras | default([]) %}{% if loras %}lora_{{ loras | length }}{% else %}4{% endif %}", 0]
```

Line by line: the `@loop` iterates the `lora_picker` field's list value (see "`items:` + `lora_picker`"
above), producing one `add_node` manipulation per selected LoRA. `node_id: "lora_{{ loop.index }}"`
names each new node `lora_1`, `lora_2`, ... so later loop iterations (and the second manipulation)
can reference them by that id. `model: [..., 0]` is a connection array (`[node_id, output_slot]`);
the Jinja conditional inside it wires the first LoRA's `model` input to the original loader (node
`4`) and every later one to the previous LoRA node. The second manipulation runs once, unconditionally,
and rewires the sampler's `model` input from the loader straight to the *last* LoRA node in the
chain — or leaves it on the loader when the list is empty (`{% if loras %}...{% else %}4{% endif %}`).
The pair together makes an empty LoRA list a no-op: no nodes are added, and the sampler still points
at node `4`. For a subgraph workflow the same recipe applies with subgraph-qualified ids (FluxKlein9b
anchors on `"75:70"`/`"75:63"` instead of `"4"`/`"3"`).

**`configuration.model_tags`** restricts which models show up in a checkpoint/LoRA picker (see
[Configuration (admin-set)](manifest.md#configuration-admin-set)) — SDXL declares `checkpoint_tags` and `lora_tags`, then each
model field's `filter_tags: "@config:checkpoint_tags"` picks it up. Declare this whenever a preset
targets a specific model family (SDXL/Illustrious checkpoints, not any SDXL checkpoint) so admins
can scope the picker without editing YAML.

**Lint, render, and place it.**

```bash
python scripts/preset_lint.py content/presets/local/MyModel/standard
python scripts/preset_render.py content/presets/local/MyModel/standard txt2img
```

`preset_render.py` runs the real template-rendering path with fixture form data and prints every
resulting pipe config value — the fastest way to see whether a `field_mappings` template actually
renders what you expect, without touching a live ComfyUI server. A ComfyUI preset that's yours
belongs under `content/presets/local/<Model>/<variant>/`, never inside
`content/plugins/marketplace/comfyui-backend/` — that tree is the plugin's shipped presets, not a
place to drop personal ones (and it's re-scanned/managed as part of the plugin, not your working
tree). To share a preset with someone else, they only need the directory: zip
`content/presets/local/<Model>/<variant>/` and have them drop it in the same place, or under
`content/presets/marketplace/` if it should be always-on for them. The directory is self-contained —
`preset.yml`, `modes/`, and `files/workflows/*.json` travel together, and the preset keeps its `id`
(so it stays the same preset in History/generations) regardless of which root it lands under.

### Troubleshooting

**Lint errors:**

- *"references node id not found in workflow"* — a `field_mappings`/`node_manipulations` entry
  names a node id (or `_meta.title`) that isn't a key in the loaded `workflow_file`. Usually a typo,
  or the JSON was re-exported after the graph changed and ids shifted — ComfyUI does not guarantee
  stable ids across edits.
- *type mismatch on a cast* — a `field_mappings` entry's `type` doesn't match what the node input
  actually wants (e.g. casting a resolution split to `"str"` when the node wants `"int"`); ComfyUI
  itself will usually reject the queued job with a clearer error naming the input.
- *missing default* — a form field referenced from `pipeline.yml` (`form.steps`, `form.cfg`, ...)
  has no `default:` in its tab file and no `| default(...)` in the template, so a `preset_render.py`
  run — or a real generation — can hit an undefined value.
- *`engine: comfyui` but no mode declares a `comfyui` pipe* / *a `comfyui` pipe present but the
  engine isn't `comfyui`* — see "The linter checks the engine against the pipes" in
  [Backends and Engines](../backends.md).

**"The workflow runs fine in ComfyUI but fails here":**

- **Models are named differently.** `field_mappings` writes the *bare filename* your form's model
  picker resolves to (after stripping `models/checkpoints/`, `models/loras/`, etc.) into the
  workflow's loader node. If the ComfyUI server doesn't have a file by that exact name in the
  matching folder, the job fails there, not in PotionUI — install the model under the name the
  preset expects, or edit the field to filter on a tag you actually have.
- **Custom nodes aren't installed on the server.** A workflow built in a ComfyUI instance with
  extra custom nodes (community `class_type`s) will queue and immediately fail on a server that
  doesn't have those nodes installed. Check the ComfyUI server's own log for the missing
  `class_type` name and install the matching custom-node pack there.
- **The graph queues in ComfyUI's own UI but the preset never even gets that far.** That's a
  `workflow_file` load problem, not a server problem — confirm the JSON is the **API** export, not
  a UI export, and that the path in `workflow_file` actually resolves (relative to the preset
  directory via `paths.preset`).

