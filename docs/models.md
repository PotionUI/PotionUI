---
category: Presets / Models
category_order: 70
order: 20
---

# Models and Backend Availability

> Read [`docs/backends.md`](backends.md) first; engine vs. backend is assumed throughout. The
> `strip_model_dir` filter calls in ComfyUI preset templates (a `models/<type>/` prefix strip) remain as
> a compatibility shim; see "The form value" below.

## The problem

A **model** in PotionUI is a row in the `models` table. Rows are created by walking a
directory on this host, and the file's SHA-256 is worked out afterwards, in the background (see
"Scanning and digests"). Originally a row existed only if the file existed locally (`file_path`
was `NOT NULL UNIQUE`); that constraint is gone, and a model is now identified as described under
"The model" and "Identity".

The `model` and `lora_picker` form fields list those rows, and the value they store is the row's
`file_path` for local files.

That works for the `native` engine, whose pipes open the file. It does not work for `comfyui`.
The ComfyUI plugin never reads, uploads, or paths a model: it substitutes a **bare name** into a
workflow node and POSTs the graph to `/prompt`, and the ComfyUI server resolves that name against
its own `folder_paths` configuration.

A model that exists only as a local file has three consequences for a remote engine. The design below
removes them.

**A model had to be downloaded locally to be selected, even when only a remote server would load it.**
The local copy is never read; the remote server loads its own copy. A model present on the ComfyUI server but
absent from this host would otherwise be invisible to the picker, however loadable it may be. See
"Indexing is per backend".

**Preset templates reconstructed the remote name by string surgery.** Because the field value is a local
path, every ComfyUI preset strips the prefix back off with the `strip_model_dir` filter
(`src/platform/templating/dict_utils.py`; see [Pipelines → `pipeline.yml` context](presets/pipelines.md#pipelineyml-context)):

```jinja
lora_name: "{{ item.model | strip_model_dir }}"
```

The filter anchors on the last `models/<known type dir>/` path boundary, so a `models_dir` other than the
default `./models` doesn't corrupt the name: only the type directory is stripped, any subdirectory
underneath it is kept, and a value it doesn't recognize (a bare filename, or an already backend-native
ref) passes through unchanged.

**Generation history missed models whose stored value was not a local path.** `generation_models.model_id`
is a foreign key to `models.id`, and the lookup used to be an exact `file_path` match, so a preset whose
`form_data` held a bare filename recorded no models at all. See "Consequences for history" for how it is
matched now.

## The model

Split identity from location.

A **model** is a logical thing: this LoRA, that checkpoint. It owns the provider metadata, the
tags, the favourite flag, and the generation history.

An **availability** is a claim that a particular backend can load that model, together with the
exact string that backend needs in order to do so.

```
models                              model_availability
------                              ------------------
id                    ◄─────────────  model_id
model_type            (FK)            backend_id  ──────►  backends.id
filename                              ref
sha256      (nullable)                size        (nullable)
file_size   (nullable)                indexed_at
                                      confidence
```

`ref` is the **engine-native identifier** — whatever that backend wants to be handed:

| engine  | example `ref`                   |
|---------|---------------------------------|
| native  | `models/loras/x.safetensors`    |
| comfyui | `style/x.safetensors`             |

Storing the ref per backend is what retires the 134 `replace(...)` calls. The pipeline stops
reconstructing a name and simply asks the resolved backend for this model's ref.

`models.file_path NOT NULL UNIQUE` is gone (migration `037_drop_model_file_path.py`): a model that
exists only on a remote server has no local path at all, and a model with a local copy can have
several — see "Model roots and locations" below.

## How a model's type is decided

Every model has one type (`checkpoint`, `diffusion_model`, `lora`, `vae`, and so on), and pickers
list models by type. PotionUI usually takes the type from the folder a file sits in. For a few
folders it reads the file instead, because a folder such as `Stable-diffusion` holds full
checkpoints and bare diffusion models side by side, and a Flux transformer filed as a checkpoint
would never show up in the Flux presets.

### Who wins

When several sources disagree, the first one in this list decides:

1. **A type set by an admin** (Admin → Models, on the model's page).
2. **A type set by a recipe** that installs the model.
3. **A type recorded when the file was downloaded.** A finished download into a checkpoint,
   diffusion-model or `unet` folder is read in place, whatever the folder's switch says, and its
   type is remembered. The file is never moved.
4. **What the file's own header says**, for a copy that sits in a folder with "Detect type from
   file" on.
5. **The folder's type.**

The first three are remembered by the file's content (its sha256), so they follow the file if it is
moved, copied or renamed.

### What PotionUI reads

Only the header of a `.safetensors`, `.sft` or `.gguf` file is read: the list of tensor names,
shapes and data types. No weights are loaded, and the header is read once per file content, not on
every scan. Pickle files (`.ckpt`, `.pt`, `.pth`, `.bin`) are never opened for this and keep the
folder's type.

From the header PotionUI works out the model family (Flux, Qwen-Image, SDXL, and so on) and which
parts the file contains. The parts decide the type:

| The file contains | Type |
|---|---|
| A known transformer or UNet and nothing else | `diffusion_model` |
| A known transformer or UNet plus a VAE, a text encoder, or both | `checkpoint` |
| A UNet-style denoiser of an unknown family plus a VAE or text encoder | `checkpoint` |
| Only a denoiser of an unknown family | Needs a type |
| No denoiser at all (a VAE, text encoder or LoRA in the wrong folder) | Needs a type |

A part only counts when it has at least two tensors, so one stray tensor cannot change a file's
type. GGUF files that hold only a transformer are `diffusion_model`.

### Needs a type

A file in a header-detected folder that no classifier recognises gets the type **Undefined**,
shown as **Needs a type** in Admin → Models. It is kept out of every picker, and a saved session
that points at it is refused with a message naming the file. Give it a type by hand and it works
straight away.

### Detect type from file (per folder)

Admin → Models → Folders has a **Detect type from file** switch on each checkpoint,
diffusion-model and `unet` folder. It is on by default for folders named `Stable-diffusion` and
`unet`, and off everywhere else, including the standard `checkpoints` and `diffusion_models`
folders, where the folder name already means what it says. Turning it on classifies the folder's
files without rehashing them. Turning it off puts the folder's files back to the folder's type,
unless an admin, a recipe or a download has pinned a type; nothing is read from disk either way.

Files on disk are never moved or renamed by any of this.

### Full checkpoints in diffusion-model pickers

Many downloads of Flux, Qwen-Image, Z-Image and similar models are all-in-one files: the
transformer plus the VAE and text encoders. They are checkpoints by the rule above, but the native
engine loads only the transformer part of such a file and ignores the rest. So on the native
engine, a picker that asks for a diffusion model also lists these full checkpoints, marked
**Full checkpoint** (`packaging: full_checkpoint` in the API). The stored type stays `checkpoint`.

This applies to Flux, Qwen-Image (both versions), Z-Image, Krea-2, Wan, Anima, SeedVR2 and
MiniMax-H3 files. It does not apply to:

- bitsandbytes (nf4 or fp4) files, which the native engine cannot load;
- GGUF files, which the native engine refuses;
- families outside that list, and Wan add-on checkpoints (VACE, camera control, audio and similar);
- ComfyUI, which lists its diffusion models by folder, so a file in `checkpoints` cannot be picked
  there.

### Changing a type by hand

On a model's page in Admin → Models an admin can set the type. It applies to every copy of that
file's content, wins over everything above, and is refused if another model already uses the same
filename under that type. **Reset to automatic** removes it and the type is decided again from the
list above.

### Teaching PotionUI a new family

A plugin can add a classifier for a family core does not know. See
[Contributing a model classifier](plugin-api.md#contributing-a-model-classifier). Files that were
"Needs a type" are looked at again the next time indexing runs after the plugin is enabled.

## Model roots and locations

A **model root** is a folder an admin pointed PotionUI at (Admin → Models → Folders, or the setup
wizard) — the built-in `home` root (the `models_dir` setting) or a **library** root added later
(an existing ComfyUI/A1111 install, a NAS share, a second drive). A root is bound to one or more
model types through `model_root_bindings` (`model_type + subdir`), and per type the bindings are
ordered (`position`); at most one binding per type is the **write root** (`is_write`), where a
download for that type lands. A root can bind several subfolders of one type (StabilityMatrix
keeps `Lora` and `LyCORIS` side by side), but no bound folder may equal or sit inside another
bound folder on the same root. `src/platform/filesystem/model_roots.py` (`ModelRootResolver`) is
the pure mapping from a root/type/relative-path to a filesystem `Path`, with no knowledge of
`models` rows; `src/features/models/roots.py` (`ModelRootsManager`) is the admin logic (detect,
create, relink, reorder, write-probe) behind `/api/models/roots*`.

Editing a root's folders through `PATCH /api/models/roots/{id}`:

- `bindings` adds or updates by `(model_type, subdir)`. A subdir the root does not bind yet is
  **added** next to the existing folders of that type; it never re-points one. A binding may carry
  `write: true` to become the write folder for its type.
- `remove_bindings` (`[{model_type, subdir}]`) removes single folders. If one was the write
  folder, writes move to another folder of the type on the same root, else to another root; if
  no writable root can take over the request is refused before anything changes.
- `remove_types` removes every folder of the type on that root.
- To re-point a type, send the new subdir in `bindings` and the old one in `remove_bindings` in
  the same request.
- `PUT /api/models/roots/write` takes an optional `subdir` to pick the write folder of a type on
  a root.

A **location** (`model_locations`, one row per `(binding, rel_path)`) is one on-disk copy
of a model: `src/features/models/locator.py` (`ModelLocator`) turns a model id or a logical ref
into the winning physical `Path`, and `ModelScanner` (`src/features/models/indexer.py`) is the only
writer that creates `models` rows with a location and `model_locations` rows — it walks every
online root's bound directories, diffs against the last-seen `size`/`mtime_ns` per location, and
never re-reads a file that hasn't changed. A scan creates rows without reading large files; their
content digests arrive later and are written by the digest pool (`DigestManager`,
`src/features/models/digests.py`), the one other writer, which only fills a location's digest and
the model's `sha256` and, in the one case described under "Scanning and digests", folds a fresh
row into the model it duplicates. Both take the scanner's write lock for every write.

### Layout profiles

A **layout profile** describes how one tool lays out its model folders (ComfyUI, A1111/Forge,
SD.Next, StabilityMatrix, Fooocus, SwarmUI, ...), so a folder an admin points at can be mapped to
PotionUI model types without guessing from folder names. A profile is one YAML file. Profiles ship
in `content/model-layouts/marketplace/`, yours go in `content/model-layouts/local/` (gitignored),
and an enabled plugin can add more through a `model_layouts:` root (see
[Plugin API](plugin-api.md)). `GET /api/models/layouts` (admin) lists what is loaded and the files
that failed validation. Validate with `python scripts/model_layout_lint.py`.

```yaml
schema: 1
id: mytool                  # ^[a-z0-9][a-z0-9-]{0,39}$, equal to the file name, "generic" is reserved
label: My Tool              # shown as "Detected: My Tool"
priority: 50                # 0-100, breaks ties only
install_dirs: [".", "MyTool"]
markers:                    # evidence that the tool is installed, relative to the install dir
  - {path: mytool.py, kind: file, weight: 4}
  - {path: core, kind: dir, weight: 2}
min_marker_score: 4         # markers at or above this score = a strong match
min_folder_evidence: 4      # folders alone at or above this score = a weak match
models_root: ["models"]     # candidates, first existing wins
config_readers:             # read the tool's own settings for moved folders
  - {kind: comfyui_extra_model_paths, file: extra_model_paths.yaml}
folders:
  - {path: checkpoints, model_type: checkpoint, write: true, scan_headers: true}
  - {path: loras, model_type: lora, write: true}
  - {path: LyCORIS, model_type: lora}
```

When an admin points Add folder at a path, `POST /api/models/roots/detect` scores every profile against the
typed path, against subfolders named in `install_dirs`, and against the ancestor left after stripping a
`models_root` folder off the end. A profile whose markers reach `min_marker_score` is a strong match and
always beats a match on folder names alone; ties fall to score, then `priority`, then `id`. The tool the admin
pointed at wins, so a ComfyUI inside a SwarmUI install is not considered when the SwarmUI folder is typed.
Profiles with a `delegate` (such as a Pinokio home) look for installed apps and detect each one. The
`profile` request field forces a profile id, or `generic` for plain folder-name matching, which is also the
fallback when no profile matches. Folders a tool keeps outside the typed path are returned as `outside_folders`,
and folders or model roots found through the tool's own settings are returned as `extra_roots`; neither is ever
added without the admin's choice. Counting files stops after 6 seconds in total, and folders past that report
`file_count_truncated`.

Rules the lint enforces:

- Every path is relative and POSIX: no leading `/`, no drive letter, no `\`, no `..`, no empty or
  `.` segments, NFC-normalised, none of `:<>"|?*`, and no Windows reserved names (`CON`, `NUL`, ...).
- `model_type` must be a known model type. `scan_headers: true` is allowed only for the
  header-classified types (`checkpoint`, `diffusion_model`, `unet`).
- Several folders may serve one type, but no two folders may be equal under case folding, none may
  sit inside another, and at most one folder per type may set `write: true`.
- `config_readers[].kind` is one of `comfyui_extra_model_paths`, `fooocus_config_txt`,
  `swarmui_settings_fds`, `stabilitymatrix_settings_json`, `sdnext_config_json`,
  `a1111_commandline_args`. Readers are defensive: a missing, malformed or oversized file yields
  a warning, never an error. A Windows drive path read while running under Linux or WSL is
  translated to the matching `/mnt/<drive>/` path when that mount exists.
- A profile needs `markers` and `folders`, or a `delegate` (a wrapper such as Pinokio that holds
  other installs) with no `folders`.
- Ids are unique across all roots. A marketplace profile wins over a local or plugin profile with
  the same id, and the loser is reported as a load error.

### Choosing a folder by hand

Detection only pre-fills suggestions. In Add folder and on each folder in Admin → Models → Folders, **Add a folder for a type**
binds any subfolder of the root, or the root itself, to a model type. Browse the subfolders (folders with no model files
are marked Empty) or type a path relative to the root. The path must exist, stay inside the root and not overlap a folder
that is already added; the form checks the overlap before sending, and the server checks all three again.
`GET /api/models/roots/browse?path=<root>&sub=<relative path>` (admin) lists the child folders of one level. It refuses
`..` and absolute paths, never follows a link that leaves the root, and stops after 500 folders, 8 levels or 3 seconds.
Without a profile, a folder named `upscale_models` or `latent_upscale_models` is an Upscaler folder; the latent upscalers
of the LTX and MiniMax presets read from the same type.

### The refs table

| Surface | Value | Example |
|---|---|---|
| Picker / persisted `form_data` | `model:<id>` | `model:01J8Z…` |
| Native `model_availability.ref` | logical ref: canonical type directory + relative path, root-independent | `loras/sdxl/detail.safetensors` |
| Pipe config `file_path` (dispatch-resolved) | absolute physical path on the dispatching host, from whichever root won | `D:\ComfyUI\models\loras\sdxl\detail.safetensors` |
| ComfyUI ref | the server's own bare/subdir name (unchanged by roots) | `style/detail.safetensors` |
| UI display (admin) | `location.logical_path` + `location.root_label`, absolute `location.path` in a tooltip | `loras/sdxl/detail.safetensors` on **ComfyUI** |

`NativeBackend.resolve_ref` is the one seam that turns a logical ref into a physical path:
`resolve_form_model_refs(form_data, backend, locator)` calls `backend.resolve_ref(ref)`, which for
the local native driver is `str(locator.path_for_ref(ref))`. Every other backend's `resolve_ref`
is a no-op — a ComfyUI-native ref was never root-relative to begin with. If the winning root is
offline, resolution raises `ModelRefNotAvailableError` naming the file and the root, before the
generation is even persisted.

### Duplicates and conflicts

The same `(model_type, filename)` can exist under more than one root. `models.copies` (surfaced by
the catalog as `copies` on the model DTO) counts how many present locations a model has; the
**winner** — what a generation actually loads — is the present location on an online root with
the lowest binding `position` for its type, ties broken by the shorter `rel_path` then lexical
order. Reordering roots for a type visibly changes which copy wins; nothing is renamed or moved to
make that happen.

The same file (same content) in two folders is one model with two locations, even when the two
folders would give it different types: the type is decided once for the content, using the rules in
[How a model's type is decided](#how-a-models-type-is-decided). When neither folder detects types
from the file but their types disagree, the header is read to settle it.

A location's `status` is `present`, `missing` (its root is online but the file is gone — the row
survives, tags/ratings/assignments are untouched, in case the file reappears), or `conflict` (same
identity, same filename, but this copy's own `sha256` disagrees with the model's canonical
digest — a partially-synced mirror or a quantised copy that kept its name). A `conflict` location
is never the winner and is never routed to.

### Offline roots

A root's `state` (`online` / `offline` / `unreadable`) is written by a cheap, timed, cached probe
(`RootProbe`, 15s TTL, 2s timeout on a worker thread) — the dispatch path never itself waits on a
disk that might be a dead network share. While a root is offline: its locations are left exactly as
they are (no location is marked `missing` just because the root can't be reached right now), the
scanner skips its bound directories entirely, and native availability projection drops that root's
locations, so the picker and routing stop offering those models — and pick them back up, unhashed,
as soon as the root is online again and a probe confirms it.

## Identity

**A model is identified by `(model_type, filename)`.**

Native's `models/loras/x.safetensors` and ComfyUI's `style/x.safetensors` both reduce to
`('lora', 'x.safetensors')` and merge into one row. Only the filename must agree; the directory
part belongs to the ref.

The type in that pair is the model's decided type (see
[How a model's type is decided](#how-a-models-type-is-decided)), which is not always the type of
the folder the file sits in. A Flux transformer in a `Stable-diffusion` folder is a
`diffusion_model` and is identified as such.

A migration enforces `UNIQUE(model_type, filename)` and refuses to apply if the existing index
already contains a collision, rather than silently merging two rows.

### Why not SHA-256

ComfyUI does not expose hashes, and never will through any endpoint it currently has. A hash is
therefore unavailable for exactly the models that need cross-backend matching. `sha256` remains
on the model row when native indexing computed it — it is useful for provider lookup and for
native-side deduplication — but it is **not** required to merge, and must not be assumed present.

### Why not size

File size is a poor identifier and a good witness. LoRA sizes cluster hard, because rank and
dimensions determine the byte count — two unrelated LoRAs trained at the same rank are commonly
byte-for-byte the same length. In a real library a substantial minority of sizes are shared by
more than one file, so matching on size alone is ambiguous for those.

So size is **stored and compared, never keyed on**. When two backends report the same filename at
different sizes, record the availability and raise a warning. The case is real — someone
quantises a checkpoint to fp8 and keeps the name, so `flux.safetensors` is 23 GB on one backend
and 11 GB on another. Merging them silently would generate with different weights depending on
which backend won selection.

When a backend cannot report size, there is no warning and the merge proceeds on name alone.
That degradation is deliberate.

### Renames

Automatic matching requires an identical filename, so a renamed copy will not merge. This is
tolerable because the escape hatch is trivial: identity lives on the model row and the ref lives
on the availability row, so linking a differently-named remote file to an existing model is one
insert.

```
model_availability(model_id=<existing>, backend_id=<comfy>, ref='style/renamed.safetensors')
```

The model detail view offers **link** and **split** actions. Prefer a visible occasional click
over a fuzzy matcher that occasionally selects the wrong weights.

Adding a model by hand (name, optionally size and sha256) creates a model row with no
availability. Indexing attaches availability later. A user-supplied hash is an assertion, not a
verification, and is flagged as such.

## Scanning and digests

Indexing a large library used to mean reading every byte of every file before a model existed.
Two jobs are now separate. A **scan** (a directory walk, a `stat`, and a header read for
header-typed files) creates rows at once, so models are listed and usable in seconds. A **digest**
(the full SHA-256 of one file, stored in `models.sha256` and `model_locations.sha256`) is
background work. A location with no digest is **pending**, one with a digest is **ready**.
Pending locations already project to availability as `reported` and become `verified` when the
digest lands; nothing about generation waits for a digest.

### What the scan does with a file

| File | What happens |
|---|---|
| Up to 1 MiB (embeddings and the like) | Hashed during the scan; nothing bigger is ever read in full by a scan |
| HF-layout directory | Fingerprinted during the scan (config plus shard names and sizes) |
| Digest already in `model_hash_cache` for this size and mtime | Ready at once |
| Anything larger | A row at once with no digest (only its header is read, and only for header-typed files), handed to the background pool per the policy below |
| Same name as an existing model but a different size | A `conflict` location straight away; different size proves different bytes |
| Another file of exactly the same size exists, present or missing with a known digest | The **twin gate**: a quick signature (head, tail and 14 windows, about 3 MiB) is compared; a different signature proves the files differ, so the row is created; an equal signature only makes the file a **candidate**: it is held in memory, hashed together with its twin, and then decided by the same rules as a hashed file (duplicate of an existing model: skipped; same name in another folder: an extra location; otherwise a new model) |

A renamed or moved large file whose size and mtime equal a missing location's, and whose quick
signature equals that location's stored signature, is recognised as that file: it takes the stored
digest and is not read in full or queued. An ambiguous match (several missing locations with
different digests) falls back to the candidate path below.

A held-back candidate is not a row yet; the indexing status reports it in `digests.duplicate_checks`.
A renamed or moved large file whose mtime changed is such a candidate: its old location is already
missing and still holds the digest, so the new file is hashed once and joins the original model with its tags,
collections and provider info. As a safety net, when a background digest lands on a model that
has a single location and another model with that digest has no present copy, the new model is
folded into the original: the original takes the new filename and every row that pointed at the
new model (tags, collections, assignments, provider info, generation links, previews, availability)
is moved to it in the same transaction. The fold is refused when the original's type already
uses that filename.
Locations that were digested before quick signatures existed get theirs filled in by the pool
whenever it is otherwise idle (nothing queued or running, not paused, no generation running); the
reads are paced like any other background read and skipped for files that changed.

Downloads made by the download queue are hashed with SHA-256 while they stream (a resumed download
first hashes the part it already has) and the digest is recorded in `model_hash_cache` under the
final size and mtime, so the scan that indexes the file takes it as ready without reading it again.
The same digest verifies a checksum when the download has one.

Indexing progress is pushed to administrators over `/ws/admin` as `indexing_status` frames carrying
the same payload as `GET /api/models/indexing/status`; the UI no longer polls. A frame is sent only
when the payload changed, about once a second while work runs and nothing while it is idle.

Header-typed files get their type from a header read during the scan, and the stored verdict (family,
whether a transformer can be extracted) is written at the same moment. Verdicts are keyed by a hash of
the header (format, tensor names, dtypes, shapes and metadata), kept on each file's location, so two
files with the same header share one verdict and a file without a digest yet already has its verdict.
Verdicts stored before this change stay keyed by digest and are still read through the model's digest.

### Who asks for a digest

| Consumer | When | Behaviour |
|---|---|---|
| Background pool | After every scan, per the policy below | One file at a time per worker, smaller files first |
| Admin "Pick models" / "Index all" | On request | Queued ahead of the background work |
| `POST /api/models/{id}/digest`, admin set type on a model with no digest | On demand | Runs at once, ignores the rate cap and the generation pause because someone is waiting |
| Provider "Fetch info", remote bundling | On demand | Each model is hashed first, one after another |
| Recipe `artifacts.plan` | A renamed file | Nothing is read: the plan goes by name and stat, queues same-size undigested files at the paced "chosen" priority, and reports them as "checking whether you already have X" |
| Recipe `artifacts.fetch` | No name or digest match, but same-size undigested files exist | At most 2 of them are verified through the paced pool (the rate cap and the generation pause apply; the step reports how many of them are really checked and waits at most 15 minutes, never for a candidate that is backing off after a failure or sits on an offline root); a match means no download, otherwise the file is downloaded |
| `plugin_api.models.model_for_sha256(..., size=...)` | A plugin asking by hash | See [the plugin API](plugin-api.md) |
| A new download without a checksum | On completion | A row at once, queued as picked; the type pinned from its header is made permanent when the digest lands |

### Resource levels, pause, policy

| | Low (default) | Normal | Max |
|---|---|---|---|
| Hash workers | 1 | 2 | 4 |
| CPU and IO priority of those threads | lowest | below normal | unchanged |
| Read-rate cap | 96 MiB/s | 384 MiB/s | none |
| Pauses while a local generation runs | yes | yes | no |

The priority is set per thread (Linux `nice` and `ionice` on the worker thread; Windows background
mode), never for the process, so generation is not slowed. The cap and the pause are the
guarantees; IO priority depends on the disk scheduler. Changing the level replaces the worker
threads; a file in flight restarts. **Pause** is persisted and survives a restart; demand requests
still run while paused.

`model_digest_policy` decides what is queued after a scan:

- `unset`: with nothing pending it stays `unset`, so an empty first boot never makes the choice.
  When at least one file is pending and no more than 20 files and no more than 64 GiB are waiting,
  it becomes `background` silently. Otherwise nothing is queued and `digests.plan.awaiting_choice`
  is `true` until an admin
  answers: **Index all** (`background`), **Pick models** (the picked files are queued, the policy
  becomes `on_demand`) or **Later** (`on_demand`). Models are listed and usable before the answer,
  and nothing is queued or read for them while the choice is open. The only reads that can still
  happen are a pair of same-size files whose quick signatures agree (probable copies, compared at
  the paced twin priority) and the lookups an explicit recipe run asks for.
- `background`: every pending file is queued, smallest first.
- `on_demand`: nothing is queued; digests come from picks, demand and downloads only.

An install that already had digests before this existed starts as `background`.

| Setting | Values | Default |
|---|---|---|
| `model_indexing_level` | `low`, `normal`, `max` | `low` |
| `model_indexing_paused` | boolean | `false` |
| `model_digest_policy` | `unset`, `background`, `on_demand` | `background` if any model already had a digest, else `unset` |

### API (admin only)

| Route | Purpose |
|---|---|
| `GET /api/models/indexing/status` | The scan status plus the `digests` block below |
| `POST /api/models/index` | A manual rescan; a running scan finishes and one more runs after it |
| `PUT /api/models/indexing/settings` | `{level?, policy?}`; answers `{level, policy}`; `422` for any other value |
| `POST /api/models/indexing/pause`, `POST /api/models/indexing/resume` | The persisted user pause; answer with the `digests` block |
| `GET /api/models/indexing/pending?query=&model_type=&root_id=&offset=&limit=` | Files waiting for a digest, by filename (`limit` 1 to 200, default 50): `{items: [{model_id, location_id, filename, model_type, size, root_label, rel_path, queued}], total, total_bytes, by_type}`. `query` is a case-insensitive filename substring. `total` and `total_bytes` cover the whole filtered set, never just the page; `by_type` counts all pending files and ignores `query` and `model_type`, so chip counts stay put while typing |
| `POST /api/models/indexing/digests` | `{mode: "all"}`, `{mode: "selected", model_ids}` or `{mode: "matching", query?, model_type?, exclude_model_ids?}`; answers `{queued, digests}`. Picked files go ahead of background work. The policy is not changed by this call |
| `POST /api/models/{id}/digest` | Idempotent demand: `{state: "ready" \| "queued" \| "failed", eta_seconds}`; `failed` means every copy has failed its attempts and is not read again until the file changes or the request is repeated with `?retry=true`; a model folded into another one answers `ready` with `merged_into`; `404` for an unknown model, `422` when it has no readable file |

The `digests` block: `policy`, `level`, `levels` (`[{id, workers, rate_bps, pauses_for_generation}]`,
the one source of per-level numbers; `rate_bps` is `null` when uncapped), `paused` (the user's pause
only), `pause_reason` (`"generation"` while the generation hold is active and the user has not
paused, else `null`), `busy` (a generation is running on a local native backend), `workers` (of the
current level), `pending_total`, `queued`, `deferred` (pending and not queued), `ready`,
`pending_bytes`, `queued_bytes`, `ready_bytes`, `current` (`[{model_id, filename, size, done}]`),
`throughput_bps` and `eta_seconds` (a 30-second moving average, `null` for the first 10 seconds,
while paused, or with nothing queued), `duplicate_checks`, `failed` (`[{path, error}]`: a file
that failed three times stays listed and is tried again when the file changes or when its digest is requested with `?retry=true`), `plan` (`{awaiting_choice,
files, bytes, min_files, min_bytes}`) and `version` (moves whenever a digest, a row or the policy
changes, so a client refetches lists only then).

The hook `model_index.after_digests` (payload `model_ids`, at most once every 5 seconds) fires when
digests land; the organize engine treats it like the end of a scan and re-evaluates new models.

### Known limits

- A type pin for a download without a checksum is held in memory; a restart between the download
  and its digest loses the pin (the type itself already came from the header).
- A file held back for comparison is not listed until its comparison finishes, and it is found
  again by the next scan after a restart.
- Windows thread priority is untested; the cap and the generation pause do not depend on it.

## Indexing is per backend

Indexing is an action on **each backend** (Admin → Backends), because what a backend can load
is a fact about that backend. The admin Models page has no index action of its own; when files
sit on disk unindexed it shows a count that links to Backends.

The seam is a new method on the backend contract, alongside `prepare_pipes`:

```python
class BaseBackend:
    async def list_models(self, model_type: str) -> list[BackendModel]:
        """Enumerate the models this backend can load.

        Returns entries carrying at minimum `ref` and `filename`; `size` when the
        backend can report it. Part of the plugin-facing API.
        """
```

Indexing a backend calls `list_models` for each model type, resolves each entry to a model row by
`(model_type, filename)` — creating it if absent — and reconciles the availability rows.

The two engines answer with different fidelity through one interface, and the UI should not blend
them:

| engine  | source                     | yields                  | confidence |
|---------|----------------------------|-------------------------|------------|
| native  | filesystem scan; SHA-256 in the background | ref, filename, size, hash once known | verified once hashed, reported before |
| comfyui | HTTP (see below)           | ref, filename, size      | reported |
| comfyui | HTTP, degraded             | ref, filename            | name only |

Native indexing yields verified identity. ComfyUI indexing yields hearsay — a claim by a server
about its own disk, true when it was made.

### Automatic reconcile (local native only)

A few callers write `models` rows a different way — the filesystem `ModelScanner` (recipes'
`models.index` step, the admin reindex action, a provider download-and-index job) and a local
download completing on this host's disk — without going through a backend's own `list_models`.
`src.features.models.native_availability_reconciler.NativeAvailabilityReconciler` re-indexes
every enabled local native backend right after each of those, so `model_availability` never
falls behind `models`. It never runs for `native.remote` or `comfyui` backends — those already
reconcile themselves on their own completion path (a remote-destination download re-indexes its
destination backend; a plugin backend reconciles however it chooses) — and it never raises: the
scan or download that triggered it has already succeeded.

### Digest conflicts (native only)

Remote execution mounts the model depot at the same path on a worker as on the dispatcher, so a
locally-computed path resolves verbatim there too — but that says nothing about whether the bytes
at that path agree. A partially-synced mirror, an interrupted upload, or a worker one rsync behind
can hold a file at exactly the right path and name with different content, and a generation
against it would succeed silently on the wrong weights.

Every `model_availability` row carries a `digest` — the content SHA-256 *that backend's own scan*
computed for its copy, distinct from `models.sha256` (the model's canonical digest, set once by
whichever indexer hashed it first). When a backend re-indexes a model it already has a row for and
its freshly-computed digest disagrees with the canonical one, the row is written with
`confidence = conflict` instead of `verified` — and a conflicted row is excluded from
`backends_holding`/`backend_ids_by_model`, so that backend is never selected to run a generation
needing that model. `resolve_form_model_refs` raises `ModelDigestConflictError` (naming the model,
the backend, and both digests) as a last-resort block if a conflicted row is ever reached anyway.

`model_hash_cache` — a `(path, size, mtime_ns) -> sha256` table — lets the scan and the background
hasher reuse a digest without touching the file. A cache hit means the file hasn't moved since it
was last hashed; a miss (new file, or one whose size/mtime changed) is hashed and cached, by the
scan for files up to 1 MiB and by the digest queue for larger ones. Directory-model fingerprints (`is_directory` rows, HF-layout checkpoints) are never compared as digests: `sha256` there is a cheap
config+shard-list fingerprint, not a content hash.

### Listing a ComfyUI server's models

Use **`GET /models/{folder}`**. It is stable, has one shape, and returns exactly what is on disk:

```
GET /models/loras  →  ["detail.safetensors", "style/foo.safetensors", ...]
```

`GET /models` enumerates the folder names from ComfyUI's `folder_paths` registry — 65 of them on
a typical install, including custom-node directories. So the `model_type → folder` mapping is
**discovered at runtime** rather than hardcoded, and the plugin needs no table of node classes.

Enrich with **`GET /experiment/models/{folder}`**, which adds byte size:

```json
[{"name": "detail.safetensors", "pathIndex": 0, "modified": 1750441529.0, "size": 228458116}]
```

The `/experiment/` prefix means what it says. Feature-detect it, and degrade to `/models/{folder}`
with `confidence = name only`. Names from the two endpoints agree exactly.

Two details that matter. Refs routinely contain subdirectories — entries in a well-organised LoRA
folder look like `style/foo.safetensors`, and that subpath *is* what the workflow needs. And one
file can appear under two refs when ComfyUI has several search roots configured for a folder
(`upscale.pth` and `extra/upscale.pth`, identical size), so deduplicate by `(filename, size)`
while indexing.

ComfyUI reports no hashes, so a file it lists under `checkpoints` cannot be matched to a native
file by content. If a native file with the same name and size sits in a `checkpoints` folder but
PotionUI has decided it is a different type (say `diffusion_model`), the ComfyUI entry is not added
as a second model and gets no availability. It is listed as a **type mismatch** in the indexing
result for that backend, with the model it matches. ComfyUI's legacy `unet` folder is treated as
`diffusion_models`. For a backend that does report hashes (a remote native worker), the type comes
from the same rules as a local file, so the same bytes land on the same model.

### Do not index from `/object_info`

It is the obvious endpoint and it is the wrong one.

It has **two schema shapes in a single ComfyUI version**. `LoraLoader.lora_name` returns
`[[names...], {}]`, while `UpscaleModelLoader.model_name` returns `["COMBO", {...}]`. Code that
takes element `[0]` reads the string `"COMBO"` from the second and reports five models, one per
character.

It reports **entries that are not files**. `VAELoader`, for instance, offers built-in pseudo-VAEs
(`pixel_space`, the taesd family) that exist nowhere on disk. An availability index built from it
contains phantom models.

It answers a different question — what a node accepts, not what the server has — and it requires
a hardcoded `model_type → node class` map that breaks when a custom node replaces a loader. The
full payload is 9.7 MB across 3090 classes.

Keep it as a last-resort fallback for servers too old to expose `/models`, and filter accordingly.

### Seeing where a model lives

`GET /api/models` returns `backend_ids` on each model plus a top-level
`availability_indexed` flag, resolved with one query per page. `GET /api/models/{id}/availability`
returns the detail: for each backend, its `ref`, `size`, `confidence`, `digest` and `indexed_at`,
plus a `size_conflict` flag when backends disagree on the byte count — the same filename holding
different weights — and a `digest_conflict` flag when at least one backend's own copy disagrees
with the canonical digest (native only; see "Digest conflicts" above).

An empty `backend_ids` is ambiguous on its own and must be read together with the flag.
`availability_indexed: false` means *nothing has been indexed yet*, and rendering that as
"available on no backend" would make every model look broken before the first index run. Only
with `availability_indexed: true` does an empty list mean the model is genuinely unloadable.

Each admin model also carries `location` (the winner: `root_id`, `root_label`, `logical_path`,
absolute `path`) and `copies` (how many present locations it has); `null`/`0` means a remote-only
model with nothing on this host. `GET /api/models/{id}` additionally returns `locations`, every
known copy with its `status` and whether it `is_winner` — see "Model roots and locations" above.

### Staleness

`model_availability` is a cache of another machine's filesystem. ComfyUI does not notify anyone
when a model is added or removed, so a model deleted on the remote remains selectable until the
next index, and the generation then fails inside ComfyUI as an `execution_error`.

This cannot be designed away. Show `indexed_at` per backend, offer a re-index action, and fail
with a message that names the model and the backend.

## Backend selection

Selection currently joins `preset.engine == backend.engine`, then pinned `backend_id` →
per-engine default → highest priority (see `docs/backends.md`). With several backends of the same
engine, availability must narrow that set — a preset's ComfyUI backends need not hold the same
models.

The obvious approach is circular: scoping the picker to a backend requires choosing the backend
before choosing models, but choosing the backend from the models requires the reverse.

Invert it.

1. Populate the picker from the **union** of models available on any enabled backend whose engine
   matches the preset. Badge each entry with the backends that hold it.
2. After selection, the candidate set is every enabled backend of that engine holding **an
   availability row for every selected model**.
3. Empty candidate set — fail before dispatch, naming the model and the backends checked:
   *"No ComfyUI backend has both `x.safetensors` and `y.safetensors`."*
4. More than one — apply the existing precedence: pinned `backend_id` → per-engine default →
   priority.

Step 1 also lets the UI grey out combinations no single backend can satisfy, before the user
presses Generate.

## The form value

The picker stores `model:<model_id>` — the literal prefix makes the value self-describing, so
form data can be walked generically. No form schema is needed, and nested shapes like the LoRA
picker's `[{model, strength}, ...]` fall out of the same recursion.

The backend is selected *before* the pipeline is built, so at that point each reference is
rewritten to the selected backend's `ref` (`src/features/models/form_refs.py`). Preset templates
therefore receive an engine-native string.

Anything that is not a `model:` reference passes through untouched. That is what keeps saved
sessions, preset defaults (bare filenames) and legacy path values working — and it is why the
`strip_model_dir` filter calls **cannot yet be deleted**: they remain load-bearing for those
legacy values (a local `models/loras/x.safetensors` path resolved before availability existed),
while being harmless no-ops on a modern ref (already backend-native, with no `models/<type>/`
prefix to strip).

A backend that has never been indexed is a special case. It holds models; it has simply never been
asked, so an absent availability row proves nothing. Resolution then falls back to the model's own
indexed location — `locator.path_for_model(model_id)` — or, when it has none (a remote-only
model), its bare `filename`, reproducing exactly what the picker submitted before availability
existed. Once a backend *has* been indexed, a missing row is a fact rather than ignorance, and
resolution fails loudly.

The same asymmetry governs selection: availability narrows the candidate backends only when at
least one backend of the engine has been indexed. Enforcing it against an empty index would fail
every generation on that engine instead of degrading to the previous behaviour.

## Portable generation bundles

`GenerationHistoryArchive.export_bundle` / `import_bundle`
(`src/features/generation/history_archive.py`) package one generation as a `generation.json`
envelope another instance can reuse. Model ulids are as instance-local as everywhere else in this
document, so `form_data`'s `model:<id>` refs are rewritten to filenames on export and back to
`model:<id>` on import (`GENERATION_BUNDLE_SCHEMA_VERSION = 2`).

A plain filename-for-filename substitution across the whole of `form_data` cannot tell two fields
apart when they happen to hold the same string — a checkpoint and a vae sharing a filename, or an
ordinary text field that just says the filename — and would resolve one of them to the wrong local
model or corrupt the text field outright. The export therefore also walks `form_data` and records
each `model:<id>` occurrence's exact location alongside its `(model_type, filename)` identity, as
`generation.model_refs: [{path, model_type, filename, sha256}, ...]` (`path` is a list of dict keys
/ list indices, so a repeated or nested reference gets its own entry). Import resolves each entry
by `(model_type, filename)` and rewrites only that recorded path — every other string in
`form_data`, model-filename-shaped or not, passes through untouched.

Which branch import takes is decided by whether the `model_refs` **key** is present at all, not by
whether the list is non-empty: a v2 bundle whose export found no `model:<id>` occurrences declares
`model_refs: []`, and that is authoritative — it means "none", so import leaves every string in
`form_data` exactly as exported. Only a bundle with no `model_refs` key at all is genuinely v1.

A v1 bundle predates path-recording and is still accepted, but with nothing to substitute by value
across the whole of `form_data` this time — the field's own type has to stand in for the missing
path. Import only rewrites a filename match inside a field the importing instance's own,
locally-installed copy of the bundle's preset (matched on `preset_id`/`mode`/`form_name`) declares
as a `model`/`models` picker or a `lora_picker` list; every other field, whatever value it holds, is
never a candidate. When that preset can't be resolved locally at all, there is no way to tell a
model reference from an ordinary field by value alone, so nothing is rewritten and every local
match is reported unresolved instead of guessed. Within the fields that do qualify, a filename the
bundle's own `models` list names under more than one model type is also left as the bare filename
with a warning — which field is which still can't be told apart without path data.

## Consequences for history

`param_handler` no longer relies on an exact `file_path` match. It falls back to the identity
`(model_type, filename)`, taking the basename of whatever ref the pipeline emitted, which repairs
any preset whose stored form values never matched a `models.file_path` and therefore recorded no
history at all. When a filename is ambiguous across model types it records nothing rather than
guessing, because attributing a generation to the wrong model is worse than attributing it to none.

`generations` gains a **`backend_id`** column. It has none today, so with two ComfyUI backends
there is no way to know which machine produced an image — which defeats the provenance that
motivates keeping models in the application at all.

The `model` **display parameter** (`generation_parameters`, distinct from the `generation_models`
association above) never records the raw ref a preset emitted. It records the resolved catalog
model's `display_name` — falling back to the leaked value's own basename when nothing resolves —
so generation history reads a name, never a depot path or ComfyUI folder ref.

## Model installation

Out of scope, and deliberately so.

The core download queue writes to this host's models directory. For a remote ComfyUI that is the
wrong disk, and ComfyUI's core API has no model-download endpoint (`/upload/image` handles input
media, not weights). Pushing gigabytes through PotionUI to a server that could fetch them
directly at line rate is not an improvement.

Manage a remote server's models on that server — a persistent volume, a provisioning script,
ComfyUI-Manager. PotionUI lists what is there and does not pretend to install it.

If that changes, the shape is a second optional capability mirroring `list_models`:

```python
async def install_model(self, ...) -> InstallResult:  # default: NotSupported
```

A ComfyUI backend could implement it when ComfyUI-Manager is present. Its API is unprobed; treat
this paragraph as a sketch, not a plan.

## See also

- [`docs/backends.md`](backends.md) — engine vs. backend, selection, contributing an engine
- [`docs/providers.md`](providers.md) — marketplaces, credentials, on-demand downloads
- [`docs/presets.md`](presets.md) — form fields and pipeline templating
