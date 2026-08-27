---
category: Presets / Models
category_order: 70
order: 25
---

# Backends and Engines

PotionUI runs a generation by handing a processed pipeline to a **backend**. Which backend it picks
is decided by the preset. This document explains the two concepts that decision rests on, how an
admin configures backends, and how a plugin contributes a new one.

This is the authoritative reference for the subsystem. Where a detail depends on a source file, that
file is named so you can double-check.

The same rule governs [providers](providers.md): core names no marketplace, and each
provider owns its own credentials.

## Engine vs backend

These two words used to be conflated. They are now strictly separate.

- An **engine** is a *protocol* — the language a pipeline speaks. `native` means the pipes are
  in-process diffusers code from `src/pipelines/pipes/`. `comfyui` means the pipeline is a ComfyUI workflow
  that gets submitted to a ComfyUI server. A preset declares its engine, because a preset's pipes
  are engine-specific: a preset built on the `comfyui` pipe can only ever speak ComfyUI; one built
  on `generator/sdxl` can only ever speak diffusers. You cannot run one on the other.

- A **backend** is a configured *instance* of an engine. An admin creates it. It carries the
  connection details (host, port, api key), plus `enabled`, `priority`, and `is_default`.

The join between them is a single field comparison: `preset.engine == backend.engine`.

| Backend (an instance) | Engine (its protocol) | Where it runs |
|---|---|---|
| Local GPU | `native` | in-process, this Python process |
| ComfyUI Desktop | `comfyui` | `127.0.0.1:8188` |
| ComfyUI RunPod | `comfyui` | a pod's public host |

The last two rows are the point. Both are the *same engine* — the same wire protocol, the same
pipes, the same workflow JSON. They differ only in where the socket points. That is a backend
concern, not an engine concern.

### Why `runpod` and `remote_http` are gone

The old `BackendType` enum contained `local`, `runpod`, and `remote_http`. Two of those were never
engines. "RunPod" is not a protocol — it is a hosting provider. "Remote HTTP" is not a protocol
either — it is a transport. Neither described what a pipeline says on the wire, and no preset ever
selected them; they were dead code and have been deleted.

If you want to run generation on a rented GPU box, you do not need a new engine. You install ComfyUI
on the box and add a `comfyui` backend whose `host` points at it. The engine stays `comfyui`; only
the connection config changes. Any preset that already declares `engine: comfyui` runs there
unmodified.

Engine is an **open string set**, not a closed enum. `native` is built in; `comfyui` is contributed
by the `comfyui-backend` marketplace plugin; anything else arrives the same way (see
"Contributing an engine from a plugin"). Disabling a plugin removes its engine, and with it every
preset that declares that engine.

## The backend object

A backend, as it appears in the database and over REST:

```json
{
  "id": "comfyui-1",
  "name": "ComfyUI Desktop",
  "engine": "comfyui",
  "enabled": true,
  "is_default": true,
  "priority": 1,
  "timeout_seconds": 300,
  "host": "127.0.0.1",
  "port": 8188,
  "secure": false,
  "api_key": null,
  "client_id": null
}
```

`id`, `name`, `engine`, `enabled`, `priority`, `timeout_seconds` are the base fields
(`BaseBackendConfig` in `src/features/backends/backend_config.py`). Everything from `host` down is
ComfyUI-specific and comes from that plugin's config subclass.

A `native` backend has no connection fields — it *is* this process — but it does carry
`device`, `dtype`, and `gpu_max_vram`.

### Defaults are hardware-derived, not hardcoded

`device`, `dtype`, and `gpu_max_vram` used to default to `cuda`/`float16`/`8` unconditionally — a
GPU-less host silently got a `cuda` config that only failed once a generation was attempted, and a
90GB-class card was capped at 8GB. `NativeBackendConfig` (`src/features/backends/backend_config.py`)
now derives these via `detect_native_hardware_defaults()`
(`src/features/backends/native_hardware.py`), which probes the host once per process (no
allocation): no CUDA device → `cpu`/`float32`; CUDA present → `cuda`, `bfloat16` on Ampere-or-newer
(SM80+, else `float16`), and `gpu_max_vram` = total VRAM minus the same reserve the native engine
already keeps free at inference time (`minimum_inference_memory_gb()`). An explicit admin-set value
in a backend's persisted config always wins — the hardware probe only fills in what's missing, so the
example `"default": "cuda"` below is illustrative, not a guaranteed constant across hosts.

### Why GPU settings live on the native backend

These three used to be global `SYSTEM` settings. They never were global: only `content/presets/marketplace/**`
pipelines and `GpuMonitor`'s budget ever read them, and no ComfyUI preset could — a ComfyUI server
picks its own device and manages its own VRAM. They configure *one engine*, so they belong to that
engine's backend. (`file_storage_directory` genuinely is global — both engines write files to this
host — and stays a setting. `attention_mechanism` was dead and was deleted.)

`NativeBackend.prepare_pipes` injects them into every pipe's config as `device`, `dtype`, and
`vram_limit_gb`, exactly as `ComfyUIBackend.prepare_pipes` injects `host`/`port`. Both answer the
same question: how should this engine instance be driven? Injection uses `setdefault`, so a preset
that pins `device` on a particular pipe still wins.

`GpuMonitor` remains a host-level service and holds no budget of its own. The backend that owns the
GPU calls `set_vram_cap_gb()` before each run, so services that consult the GPU manager directly
(`MemoryAdvisor`, `ModelLifecycle`) see the same cap the pipes do. Budget precedence is:
an explicit `vram_limit_gb` argument, then the owner's cap, then available hardware — always bounded
by what is actually free.

Current native-v2 boundary: the cap reaches model loaders and can influence FP8/load decisions,
but the standard flow generator does not yet pass it into `NativeGenerator`. Runtime placement for
those generators therefore falls back to physical device total memory; `gpu_max_vram` is not yet a
hard native-v2 runtime cap. See [Model Inference Path](/admin?tab=docs&doc=dev/model-inference-path).

Consequence: with more than one GPU, `device` is per-backend rather than per-installation. The
native engine is a singleton today, so that is latent, not useful — but it is the right shape.

`is_default` is scoped **per engine**: at most one default `native` backend and at most one default
`comfyui` backend, not one default overall. It is **only** mutated through
`POST /api/backends/{id}/set-default` — a plain `PUT /api/backends/{id}` cannot flip it, because
clearing the flag on the backend's siblings has to happen in the same transaction.

> `max_concurrent_generations` no longer exists. It was a config field that nothing ever enforced —
> not the registry, not the backends, not the pipeline. It has been removed from the config model,
> the persisted config blob, the admin form, and the API. Do not reintroduce it.

## Declaring the engine on a preset

`preset.yml` carries a scalar `engine:` field:

```yaml
schema: 1
id: "01K0W24A3RADXXABH16YQ7KF00"
name: "[Native/QwenImage] T2I"
version: "1.0.0"
category: "image"
engine: "native"
modes:
  - txt2img
```

It replaces the old list-valued `supported_backends:`. A preset has exactly one engine — the list
was always a lie, because a preset's pipe list cannot be executed by two different protocols. There
is no backward compatibility with `supported_backends:`; a preset that still uses it fails manifest
validation.

`engine` is a required, non-empty string in the manifest (`PresetManifest` in
`src/features/presets/schema.py`). `PresetTemplate.engine` (`src/features/presets/templates.py`) defaults to `"native"`
when unset, and `PresetInfo.engine` (`src/features/presets/dto.py`) exposes it over the preset API.

### The linter checks the engine against the pipes

The engine must agree with the pipes in `modes/<mode>/pipeline.yml`. `engine: comfyui` means the
pipeline drives the `comfyui` pipe (registered by the `comfyui-backend` plugin); `engine: native`
means it drives pipes from `src/pipelines/pipes/` such as `checkpoint_loader/sdxl`, `generator/sdxl`,
`model_loader/qwen_image`.

`PresetLinter._lint_engine_matches_pipes` (`src/features/presets/linter.py`) enforces the half of that
rule which is structurally detectable, since `comfyui` is the one engine identifiable from the
pipeline alone — it is the only one that requires a pipe literally named `comfyui`. Two errors:

- `engine: comfyui` but no mode's `pipeline.yml` declares a `comfyui` pipe.
- a `comfyui` pipe is present but the preset declares some other engine.

A plugin engine whose pipes are named arbitrarily cannot be checked this way; for those, the engine
declaration is on the author.

```bash
python scripts/preset_lint.py content/plugins/marketplace/comfyui-backend/presets/QwenImage
```

The same check is exposed at `GET /api/developer/presets/lint`. See
[Preset Authoring Guide](presets.md) for everything else in `preset.yml`.

### The directory name is convention only

Presets live under `content/presets/marketplace/<Model>/<variant>/` (shipped, tracked) or
`content/presets/local/<Model>/<variant>/` (yours, `.gitignored`) — e.g. `content/presets/marketplace/SDXL/realistic/`.
**Nothing parses the directory at all.** The loader reads `engine:` out of `preset.yml` and ignores
where the preset lives — a preset under `content/presets/marketplace/` that declares `engine: comfyui` will
happily run on ComfyUI. A plugin's own `presets:` root (e.g. `comfyui-backend`'s) works the same way.

## Configuring backends (admin)

Admin → **Backends** (`frontend/src/routes/admin/components/BackendsTab.svelte`). Backends are
grouped by engine. For each one you set:

- **enabled** — a disabled backend is never a selection candidate and is not even instantiated.
- **priority** — an integer; higher wins. Only consulted when no default is set for the engine.
- **default** — the preferred backend for its engine. `POST /api/backends/{id}/set-default` makes a
  backend the default for its engine, clearing the flag on its siblings.
- **timeout_seconds** — per-generation request timeout.
- connection fields, if the engine has any.

Two backends of the same engine are a normal setup: a ComfyUI Desktop instance for local work and a
ComfyUI pod for heavy jobs, one of them flagged default. Two backends of *different* engines never
compete — a preset picks its engine, and only that engine's backends are considered.

### REST API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/backends` | all backends |
| `GET` | `/api/backends/enabled` | enabled only, priority descending |
| `GET` | `/api/backends/default?engine=native` | default for an engine; `engine` is required |
| `GET` | `/api/backends/engines` | one descriptor per registered engine (see below) |
| `GET` | `/api/backends/health` | health of every backend |
| `POST` | `/api/backends` | create; the body carries `engine` |
| `GET` `PUT` `DELETE` | `/api/backends/{id}` | read / update / delete |
| `POST` | `/api/backends/{id}/test` | test the connection |
| `GET` | `/api/backends/{id}/health` | health of one backend |
| `GET` | `/api/backends/{id}/system-info` | GPU / memory / disk info |
| `POST` | `/api/backends/{id}/set-default` | make this the default for its engine |

Router: `src/features/backends/routes.py` (prefix `/api/backends`). Health payloads carry
`backend_engine`.

### Engines describe themselves

`GET /api/backends/engines` returns a descriptor per engine, not a bare name:

```json
[
  {"engine": "native",  "label": "Native",  "singleton": true,  "fields": [
    {"name": "device", "label": "Device", "type": "string", "required": false,
     "default": "cuda", "description": "Torch device used to load and run models",
     "secret": false, "options": ["cpu", "cuda:0", "cuda"]}
  ]},
  {"engine": "comfyui", "label": "ComfyUI", "singleton": false, "fields": [
    {"name": "host", "label": "Host", "type": "string", "required": false,
     "default": "127.0.0.1", "description": "Hostname or IP of the ComfyUI server", "secret": false},
    {"name": "api_key", "label": "API Key", "type": "string", "required": false,
     "default": null, "description": "Optional. For authenticated instances", "secret": true}
  ]}
]
```

`fields` comes from `BaseBackendConfig.engine_fields()`, which reports every field a config class
declares beyond the base ones (`BASE_CONFIG_FIELDS`). `type` is `string`, `number`, or `boolean`;
`secret: true` (set via `Field(json_schema_extra={"secret": True})`) means render a password input;
`options` (a fixed list via `Field(json_schema_extra={"options": [...]})`, or a host-dependent one
via `engine_field_options()`) means render a `<select>`; `label` comes from the field's `title`. A config class sets `engine_label` and `engine_singleton`
as `ClassVar`s.

This exists so that **no frontend or core code hardcodes an engine's settings**. `comfyui` is
supplied by a plugin and may be absent entirely; the admin UI builds its create/edit form purely
from these descriptors, and a `singleton` engine is never offered in the create form.

Engine-specific settings (ComfyUI's `host`, `port`, `secure`, `api_key`, `client_id`) are **flat
top-level fields** on the request and response bodies, not nested under a `config` key — the request
models allow extra fields and hand them to the engine's registered config class, which validates
them. `PUT /api/backends/{id}` is a partial update: it is merged onto the stored config, so omitted
fields keep their values. `engine` is **immutable** — it decides which config class validates the
backend, so changing it means deleting and recreating. `is_default` is likewise not writable here;
only `POST /api/backends/{id}/set-default` moves it.

## Backend selection

When a generation starts, `src/features/generation/orchestrator.py` calls

```python
BackendRegistry.select_backend_for_generation(engine: str, backend_id: str | None = None) -> BaseBackend
```

(`src/features/backends/backend_registry.py`). The algorithm, in order:

1. **Candidates** — every backend whose `engine` equals the preset's `engine` *and* is `enabled`,
   sorted by descending `priority`. If the list is empty, raise.
2. If the request supplied a **`backend_id`**, use that backend if and only if it is among the
   candidates. Otherwise raise — a pin to an incompatible or disabled backend is an error, never a
   silent fallback.
3. Otherwise, the candidate flagged **`is_default`** for that engine.
4. Otherwise, the highest-`priority` candidate.

It returns a `BaseBackend` and never `None`. Both failure paths raise **`NoBackendForEngineError`**
(defined in `backend_registry.py`, a `RuntimeError` subclass):

```
No enabled backend provides engine 'comfyui'. Available engines: ['native'].
Is the plugin providing 'comfyui' enabled, and a backend configured for it?

Requested backend 'comfyui-2' is not an enabled backend for engine 'comfyui'
```

There is no cross-engine fallback. A `comfyui` preset never quietly runs on the native GPU when the
ComfyUI server is down — the pipes would not exist. The failure is loud by design.

`backend_id` on the generation request is unchanged and remains optional. It exists so a user with
two ComfyUI backends can pin a run to a specific one.

## Runtime classes

```
BaseBackend                     src/features/backends/base_backend.py
└── InProcessBackend            src/features/backends/in_process_backend.py
    ├── NativeBackend           src/features/backends/native_backend.py
    └── ComfyUIBackend          content/plugins/marketplace/comfyui-backend/backend/comfyui_backend.py
```

`BaseBackend` is the abstract contract: `start_generation(pipeline_data, emit)`,
`cancel_generation(generation_id)`, `health_check()`, `get_system_info()`. Backends are **stateless
executors** — they start and cancel work and report health. Generation state (status, progress,
listing, subscription) is owned by `GenerationStatusTracker` on the orchestrator side, never by a
backend.

`InProcessBackend` factors out what every backend so far actually does: it owns the `_active` set of
in-flight generation ids, the `_run` coroutine that drives `GenerationEngine` on a worker thread and
emits completion, and `cancel_generation`. Before executing it calls `self.prepare_pipes(pipes)`, the
one hook subclasses override. **`InProcessBackend` is plugin-facing API** — its constructor and
`prepare_pipes` signature are a contract that plugin engines depend on.

(`LocalBackend` / `local_backend.py` no longer exist; `NativeBackend` is the rename.)

- `NativeBackend.prepare_pipes` fills missing `device`, `dtype`, and `vram_limit_gb` values in
  every pipe config. It uses `setdefault`, so a preset that deliberately pins one of those values
  still wins.
- `NativeBackend.health_check` reports on the *configured* device, not just whether the host has any
  GPU: `device: cpu` is always `"healthy"` (no CUDA dependency); `device: cuda`/`cuda:N` is
  `"healthy"` only when `torch.cuda.is_available()` is true and the requested index exists on this
  host, otherwise it reports `"degraded"` with a `reason` string explaining what's wrong and what to
  do, since every generation on that backend would otherwise fail at load time. This reuses the
  existing three-tier `healthy`/`degraded`/`error` status vocabulary the admin UI
  (`BackendsTab.svelte::getHealthVariant`) already renders, rather than inventing a new status the
  frontend doesn't recognize. A raised exception during the check itself still reports `"error"`.
- `ComfyUIBackend.prepare_pipes` writes the connection config into each pipe's
  `config['backend_config']`, which is how `ComfyUIPipe`
  (`content/plugins/marketplace/comfyui-backend/backend/pipes/comfyui/main.py`) learns which server to talk
  to. It also overrides `health_check`, `get_system_info`, and cancellation (ComfyUI cancels via its
  own interrupt endpoint, not by flipping a local flag).

### The `native` engine has exactly one backend

There is one GPU and one `GenerationEngine` in this process, and `GenerationEngine` supports one
in-flight generation at a time (`cancel()` flips a single `_cancelled` flag). A second `native`
backend would be a second name for the same hardware, competing for the same lock — pure confusion
with no capability gained.

So the `native` backend is a singleton: auto-provisioned on first boot, not deletable, and with no
connection config to edit. `BackendConfigStore` rejects a second one, and the admin UI must not
offer to create it. If you want a second GPU box, that is a different machine running its own
PotionUI or ComfyUI — reach it as a `comfyui` backend, not as a second `native` one.

**Never key off the native backend's row `id`.** Migration `069_backends_type_to_engine.py` renames
the *engine* (`local` → `native`), not the row id, so that existing generation history keeps
resolving its backend. A database upgraded through 069 therefore has the native backend at
`id = "local"`; a fresh install auto-provisions it at `id = "native"`. Both are correct. The
invariant to test is `backend.engine == "native"`.

## Contributing an engine from a plugin

Everything a plugin imports comes from `src.plugin_api` — see [the Plugin API](plugin-api.md).

Engines are registered through the `backend.register` hook. It fires once at `BackendRegistry`
init with two empty, mutable dicts; you add your classes to both.

`manifest.yml`:

```yaml
hooks:
  backend:
    - hook: "backend.register"
      handler: "hooks.backend_hooks.register_backend"
```

`hooks/backend_hooks.py`:

```python
from src.plugin_api import HookContext

def register_backend(context: HookContext) -> HookContext:
    from ..backend.comfyui_backend import ComfyUIBackend
    from ..backend.comfyui_config import ComfyUIBackendConfig

    context.data["backend_types"]["comfyui"] = ComfyUIBackend
    context.data["config_types"]["comfyui"] = ComfyUIBackendConfig
    return context
```

The dict key is the engine name. That string is what presets put in `engine:`, what
`GET /api/backends/engines` returns, and what the admin UI offers when creating a backend.

Your config class **must subclass `BaseBackendConfig`** rather than re-declaring `id`, `name`,
`engine`, `enabled`, `priority`, `timeout_seconds` by hand — hand-copied base fields drift the moment
a base field is added or removed. Add only what your engine actually needs:

```python
from src.plugin_api import BaseBackendConfig
from pydantic import Field

class ComfyUIBackendConfig(BaseBackendConfig):
    engine: str = Field(default="comfyui")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8188)
    secure: bool = Field(default=False)
    api_key: str | None = None
    client_id: str | None = None
```

Your backend class should subclass `InProcessBackend` and override `prepare_pipes` (plus
`health_check` / `get_system_info`, which have no sensible default for a remote service). If it
needs the `GenerationEngine`, expose a `set_generation_engine(manager)` method — the registry
calls it after construction when present.

A plugin that provides an engine will normally also provide the pipes that speak it (manifest
`pipes:` section) and ship the presets that declare it (manifest `presets:` section — a directory,
relative to the plugin, scanned recursively for `preset.yml` like the core tree when the plugin is
enabled). The `comfyui-backend` plugin is the reference implementation of all three: its `comfyui`
presets live under `content/plugins/marketplace/comfyui-backend/presets/`, useless without the plugin
installed. Presets keep the `id:` from their own `preset.yml`, so a preset is identified the same
way wherever it lives.

### Other backend hooks

`src/features/backends/hooks.py` also declares `before_create` / `after_create`, `before_update` /
`after_update`, and `before_delete` / `after_delete`, fired around admin CRUD on backend
configurations. The `before_*` variants can rewrite the submitted `backend_data`; note that
`before_delete` is advisory — the controller does not currently honour a `blocked` flag from it. The
live catalog of every hook, including payload shapes, is served at
`GET /api/plugins/hooks/catalog`.

## Troubleshooting

**`NoBackendForEngineError: No enabled backend provides engine 'comfyui'.`**
Selection found zero enabled backends whose `engine` matches the preset. Either no backend of that
engine exists (admin → Backends → create one), every one of them is disabled, or — most often — the
plugin that registers the engine is disabled, in which case the engine is not registered at all and
`GET /api/backends/engines` will not list it. Enable the plugin and restart; engines are collected
once at `BackendRegistry` init. The error message itself prints the engines that *are* available.

**Generation fails immediately with a connection error on a ComfyUI backend.**
Use `POST /api/backends/{id}/test` (the "Test" button in the admin tab). Check that the ComfyUI
server is actually listening on the configured `host:port`, that `secure` matches how it is served
(`http` vs `https`), and that an `api_key` is set if the server requires one. `host` must be
reachable *from the PotionUI process* — `127.0.0.1` means the PotionUI machine, which is not the
pod.

**`NoBackendForEngineError: Requested backend '<id>' is not an enabled backend for engine '<engine>'`**
Selection step 2: the pinned `backend_id` must be enabled and its `engine` must equal the preset's
`engine`. Pinning a `native` backend on a `comfyui` preset is an error, not a fallback.

**A preset runs on the wrong backend.**
Selection ignores directory names entirely. Read `engine:` in the preset's `preset.yml`, then check
which candidate won: the `is_default` backend for that engine beats priority, and priority only
breaks ties among the rest. `GET /api/backends/enabled` shows the candidate list in priority order.

**An engine disappeared after a plugin update.**
Backends persist in the database even when their engine is no longer registered. Such a backend is
skipped at init and never becomes a candidate, with a warning naming it:
`Backend '<id>' declares unknown engine '<engine>' (is its plugin enabled?)`. Re-enable or reinstall
the plugin, or delete the orphaned backend.

## See also

- [Preset Authoring Guide](presets.md) — `preset.yml`, pipelines, forms.
- Hooks Catalog (developer docs, live) — every hook and its payload, including `backend.*`.
