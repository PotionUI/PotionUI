---
category: Presets / Models
category_order: 70
order: 20
---

# Models and Backend Availability

> **Status: implemented** (2026-07-09), except where noted. The one deliberate exception is the
> 134 `replace('models/…/', '')` calls in ComfyUI preset templates, which remain as a
> compatibility shim — see "The form value" below. Read [`docs/backends.md`](backends.md) first;
> engine vs. backend is assumed throughout.

## The problem

A **model** in PotionUI is a row in the `models` table. That row is created by walking a
directory on this host and computing a SHA-256 of each file's bytes, so a row exists only if the
file exists locally. The schema enforces it: `file_path` is `NOT NULL UNIQUE`.

The `model` and `lora_picker` form fields list those rows, and the value they store is the row's
`file_path`.

That works for the `native` engine, whose pipes open the file. It does not work for `comfyui`.
The ComfyUI plugin never reads, uploads, or paths a model: it substitutes a **bare name** into a
workflow node and POSTs the graph to `/prompt`, and the ComfyUI server resolves that name against
its own `folder_paths` configuration.

Three consequences follow, and all three are live today.

**You must download a model locally to select it, even when only a remote server will load it.**
The local copy is never read. The remote server loads its own copy. Any model present on the
ComfyUI server but absent from this host is simply invisible to the picker, however loadable it
may be.

**Preset templates reconstruct the remote name by string surgery.** Because the field value is a
local path, every ComfyUI preset strips the prefix back off:

```jinja
lora_name: "{{ item['model'] | replace('models/loras/', '') }}"
```

There are 134 such calls. They only work while `models_dir` is exactly `./models`; point it
elsewhere and `replace` matches the middle of an absolute path, silently corrupting the name:

```
/srv/weights/models/loras/x.safetensors  →  /srv/weights/x.safetensors
```

**Generation history breaks whenever the string doesn't match.** `generation_models.model_id` is
a foreign key to `models.id` — the history is correctly keyed on the logical model. But
`src/features/generation/handlers/param_handler.py:102` populates it with
`model_repo.get_by_file_path(model_path)`, an exact string match. Any preset whose `form_data`
holds a bare filename rather than a local path therefore records **no models at all** for its
generations: the lookup misses, `models_not_found` increments, and nothing surfaces it.

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

`models.file_path NOT NULL UNIQUE` must be relaxed: a model that exists only on a remote server
has no local path. Local paths become native availability rows.

## Identity

**A model is identified by `(model_type, filename)`.**

Native's `models/loras/x.safetensors` and ComfyUI's `style/x.safetensors` both reduce to
`('lora', 'x.safetensors')` and merge into one row. Only the filename must agree; the directory
part belongs to the ref.

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

## Indexing is per backend

Today "Index models" is a global action on the admin `/models` page that scans one directory.
It becomes an action on **each backend**, because what a backend can load is a fact about that
backend.

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
| native  | filesystem scan + SHA-256  | ref, filename, size, hash | verified |
| comfyui | HTTP (see below)           | ref, filename, size      | reported |
| comfyui | HTTP, degraded             | ref, filename            | name only |

Native indexing yields verified identity. ComfyUI indexing yields hearsay — a claim by a server
about its own disk, true when it was made.

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

Hashing on every scan would make indexing a large depot unusable, so the native scan
(`scan_native_models`) goes through `model_hash_cache` — a `(path, size, mtime_ns) -> sha256`
table. A cache hit means the file hasn't moved since it was last hashed and the digest is reused
without touching the file; a miss (new file, or one whose size/mtime changed) hashes it and caches
the result. Directory-model fingerprints (`is_directory` rows, HF-layout checkpoints — see
`101_add_model_is_directory.py`) are never compared as digests: `sha256` there is a cheap
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
134 `replace('models/loras/', '')` calls **cannot yet be deleted**: they remain load-bearing for
those legacy values, while being harmless no-ops on a modern ref.

A backend that has never been indexed is a special case. It holds models; it has simply never been
asked, so an absent availability row proves nothing. Resolution then falls back to the model's own
`file_path` (or `filename`, for a remote-only model), reproducing exactly what the picker
submitted before availability existed. Once a backend *has* been indexed, a missing row is a fact
rather than ignorance, and resolution fails loudly.

The same asymmetry governs selection: availability narrows the candidate backends only when at
least one backend of the engine has been indexed. Enforcing it against an empty index would fail
every generation on that engine instead of degrading to the previous behaviour.

## Consequences for history

`param_handler` no longer relies on an exact `file_path` match. It falls back to the identity
`(model_type, filename)`, taking the basename of whatever ref the pipeline emitted, which repairs
any preset whose stored form values never matched a `models.file_path` and therefore recorded no
history at all. When a filename is ambiguous across model types it records nothing rather than
guessing, because attributing a generation to the wrong model is worse than attributing it to none.

`generations` gains a **`backend_id`** column. It has none today, so with two ComfyUI backends
there is no way to know which machine produced an image — which defeats the provenance that
motivates keeping models in the application at all.

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

## Prerequisites

Two independent fixes should land before the schema work.

**The `models_dir` setting is ignored by three of its five readers.** The registered key is
`models_dir`, but `src/features/models/indexer.py:45`, `src/features/models/manager.py:946`,
and the then-plugin download manager all read `model_dir` — a key that has
never existed — and silently fall back to the literal `"models"`. The bug is invisible only
because the default value normalises to the same path.

**`generations` has no `backend_id`.** Required by the provenance goal above.

## See also

- [`docs/backends.md`](backends.md) — engine vs. backend, selection, contributing an engine
- [`docs/providers.md`](providers.md) — marketplaces, credentials, on-demand downloads
- [`docs/presets.md`](presets.md) — form fields and pipeline templating
