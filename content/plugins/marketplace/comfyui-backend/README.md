# ComfyUI Backend

Connects PotionUI to a running ComfyUI server: a `comfyui` engine, the `comfyui` pipe that
executes a raw workflow graph, requirement checkers (`comfyui_node`/`comfyui_model`), and the
workflow-import wizard. For **authoring** a `comfyui`-engine preset (the two-minute import
path, the manual path, troubleshooting), see
[docs/presets/comfyui.md](../../../../docs/presets/comfyui.md) — this file covers the
importer's own implementation internals: GGUF compatibility, crash-safe publication, exact-
integer transport for large literals, schema-consistency re-validation, and the node catalog
CLI used to extend node coverage.

## GGUF model folders

 This is compatibility with an already-configured ComfyUI server running
the [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) custom node — it does not add native
GGUF loading, model conversion or download, a format-filter UI redesign, benchmarking, or
automatic custom-node installation of any kind. ComfyUI-GGUF registers `unet_gguf`/`clip_gguf`
as separate folder names for the *same* on-disk directory as `diffusion_models`/`text_encoders`,
filtered to the `.gguf` files ComfyUI's own ordinary listing excludes — a UNET/CLIP loader's
`.gguf` selection (`UnetLoaderGGUF`/`UnetLoaderGGUFAdvanced`, or a GGUF CLIP loader's own
`clip_name`/`clip_name1..4` input, which can independently hold an ordinary encoder or a GGUF
one) is recognized by its file extension and its `comfyui_model` requirement — in analyze
suggestions, the Requirements-preview step, and the emitted/reloaded preset — names whichever
listing actually carries it, never the ordinary one a `.gguf` file is invisible under. The
Requirements panel's own live check also tries the other listing before reporting a file
missing, so a preset saved before this existed still resolves correctly against a real server;
its missing-model guidance always names the physical `models/diffusion_models` or
`models/text_encoders` directory, never `unet_gguf`/`clip_gguf` as if they were real
subdirectories to create.

`GET /presets/imported/{id}/source` returns the stored `form`/`history` (from the
preset's `import.json` sidecar, or `default_form`/`default_history` for a preset imported before
the sidecar carried them) so the wizard can reopen an imported preset exactly as it was built.
The workflow it returns, and the one reload re-emits from, is `import-source.json` - the
workflow exactly as imported, every node included - never the emitted `modes/<mode>/files/
workflows/<mode>.json`, which the `lora_picker` rewrite has already stripped the replaced LoRA
chain nodes from (so its re-analysis would find no chain for `form.lora_chain` to refer to). A
preset stored before `import-source.json` existed re-opens the emitted workflow instead, with
the selection's `replaced_node_ids` dropped: those nodes are gone from that graph, and the kept
ones are still wired as imported.

## Failure-safe publication

 `import`/reload never write into the live preset directory
directly: the replacement preset is fully rendered and written into a staging directory first,
then published with a rename-based swap - the existing directory (if any) is renamed aside to a
backup, the staging directory is renamed into place, and the backup is dropped. Both the staging
directory and the backup live outside every scanned preset root (one level above
`content/presets/local`, so the swap is still a same-filesystem rename), which is what makes
them undiscoverable: a hidden name inside the root would not do it, because the catalogue's
`preset.yml` scan descends into dot directories. A serialization, write, or rename failure at any point before the swap
completes leaves the existing preset exactly as it was and discards the staging directory; on a
brand-new import (nothing existing to preserve) the same failure leaves no preset directory
behind at all, rather than a partial one a later scan could pick up. This is a two-rename swap,
not a single power-loss-atomic transaction - a crash between the two renames could still leave
the target briefly absent with only the backup present, needing manual reconciliation on
restart; what it guards against is an ordinary exception during staging, writing, or renaming
(a full disk, a permission error, a bug), not a mid-swap power loss. In the (pathological)
case where even restoring the backup fails, the backup directory is never deleted - the error
names its path so the previous preset's content can be moved back to the target directory by
hand. A backup preserved that way sits outside the preset roots too, so it is never listed as a
preset in the meantime.

## Exact large integers

 A browser's own `JSON.parse`/`JSON.stringify` silently rounds an
integer literal outside JavaScript's safe range (`Number.MAX_SAFE_INTEGER`, `+/-(2**53-1)`) to
the nearest representable double - a large seed or other oversized literal in a pasted/uploaded
workflow is exactly the kind of value that hits this the moment client code touches it as a
parsed object, well before any network request. `analyze`/`requirements`/`import` accept an
additional `workflow_text` request field alongside `workflow` - the exact source text, never
parsed into a JS object - which is authoritative over `workflow` for every literal value when
given (re-decoded server-side with the stdlib `json` module, whose default integer parsing is
already exact at any size); `workflow` alone still works exactly as before for a caller that
never sends `workflow_text`. Symmetrically, every response that might echo a literal value
pulled from a parsed workflow (a candidate's `current_value`, a generated field's `default`,
the stored `workflow` dict itself) replaces any integer outside that same safe range with
`{"__exact_int__": "<digits>"}` - a plain string a browser's `JSON.parse` reproduces exactly,
never converts to a number and re-rounds. `GET .../presets/imported/{id}/source` additionally
returns `workflow_text` - the stored workflow file's exact text, never round-tripped through
`json.loads`/`json.dumps` at all - for the wizard's edit/modify flow to keep and resubmit
unchanged instead of re-serializing the (now correctly tagged, but still JS-object) `workflow`
field. `POST .../presets/imported/{id}/reload` needs none of this: it always re-reads its own
stored file server-side and never receives a workflow from the client at all, so it was already
exact. None of this repairs a preset already saved with a rounded value from before this existed
- only the transport for a workflow still in flight through the wizard.

## Schema consistency

 `analyze`/`source` resolve a reachable ComfyUI backend's `object_info`
once and classify every candidate from it (which inputs are prompts, which are model files and
under which `models/` folder); the response carries that classification's own
`schema_fingerprint` and `object_info_used`. `import`/reload re-resolve `object_info` fresh and
re-run the same classification before writing anything - `import` only if the caller echoes back
the `schema_fingerprint`/`object_info_used` it was given (both optional; omit them to skip the
check entirely, e.g. a script driving `import` without ever calling `analyze`), reload against
its own `import.json` sidecar's stored fingerprint. A resulting classification that disagrees
with what was shown refuses the save with a 400 naming the drift, rather than silently
re-wiring a mapping or dropping a `comfyui_model` requirement; a backend that answered at
analyze/source time but not at save/reload time always refuses too, since there's no way to
confirm the classification is still the same one shown. The one exception is proceeding on a
*richer* schema than what analyze saw (no backend was reachable then, one is now) - allowed
only when the newly available `object_info` classifies the workflow exactly the same way.
The no-backend-reachable path itself (`object_info_used: false` both times) is unaffected: an
unmodified workflow always reclassifies identically, so it's never refused.


## The node catalog

Steps 2–4 read a class's role from a declarative catalog —
`content/plugins/marketplace/comfyui-backend/backend/preset_import/node_catalog.yml` — instead of
naming node classes in code. Each entry is `category` (`loader`/`sampler`/`sampling`/`latent`/
`image_input`/`modifier`/`lora`/`output`/`ignore`), `links` (which connected inputs carry a prompt,
a model/clip chain, the sampler's latent, or the sampler's own noise/guider/sampler/sigmas graph),
and `inputs` (one `InputSpec` per literal input: `role`, the core `field` type, `name`/`label`,
optional `config`/`transform`/`section`/`history`, and — for a model-file role — the `models/`
`folder` it lives under). A class the catalog doesn't name still imports fine as generic literal
fields; it just won't get an "obvious" default-form placement.

A `passthrough`-tagged link forwards whatever kind of connection reached the node (a model/clip
chain, a prompt walk) onward unchanged; an entry's `branch: {input, on_true, on_false}` picks which
of two `passthrough` inputs a walk follows by that literal's own value at import time — how a
runtime switch node (`ComfySwitchNode`) is caught by the model-chain and prompt walks without either
one naming it directly.

The plugin ships a CLI over this catalog, `content/plugins/marketplace/comfyui-backend/scripts/comfyui_nodes.py`
(run with `PYTHONPATH=./venv/lib/python3.12/site-packages:.` from the repo root — importing the
plugin's `backend` package needs `aiohttp`):

- `coverage <workflow.json>` — mode, sampler, sampling cluster, a per-class coverage table, the
  default form outline, and an "Uncatalogued classes: ..." line (`--fail-on-uncatalogued` exits 1
  when that list isn't empty, for CI use).
- `scaffold <ClassType> [--object-info FILE | --comfyui-src DIR]` — a paste-ready catalog entry,
  read from a saved `/object_info` dump or ComfyUI's own source (`nodes.py`/`comfy_extras/*.py`,
  parsed with `ast`, never imported or run), with `# TODO` markers wherever a human must confirm a
  guess (e.g. `python content/plugins/marketplace/comfyui-backend/scripts/comfyui_nodes.py scaffold BasicScheduler`).
- `list [--category CAT]` — one line per catalogued class.
- `check` — runs `validate_catalog`; exits 1 on any problem.

Adding a node the catalog doesn't know is then: run `coverage` to find it, `scaffold` to draft its
entry, paste and resolve the `# TODO`s, `check` to confirm — one YAML entry, no code change.

