# Remote Native

This document covers both halves of Remote Native: the standalone worker
process (below) and the `native.remote` backend driver that runs inside the
main PotionUI process and talks to it. Skip to "Core side" for the latter.

# Remote Native worker

The Remote Native worker is a standalone FastAPI process that executes an
`ExecutionPackageV1` (`src/platform/worker_protocol/`) off the main PotionUI
process - on rented compute, a second machine, or just a separate process on
the same host. It speaks worker protocol v1 over HTTP + Server-Sent Events,
runs one execution at a time, and decides its own device/dtype/VRAM budget.
It has **no PotionUI database access** - that boundary is what makes it safe
to run on hardware PotionUI's operator does not otherwise control.

## Running it

Bare metal, via the `worker` install preset (checks the GPU, the port,
`POTIONUI_WORKER_DIR`, and `POTIONUI_WORKER_TOKEN`, then execs `python
worker.py`):

```bash
./potionui worker doctor   # environment checks for this preset
./potionui worker start    # create the venv, install the full CUDA stack, launch worker.py
```

Or run `worker.py` directly:

```bash
POTIONUI_WORKER_TOKEN=<shared-secret> python worker.py
```

Containerized, via the reference `docker/worker.Dockerfile` image — the path
a remote-execution provider plugin (e.g. `runpod-provider`) expects; see
`docker/README.md`'s "RunPod worker image" section.

`worker.py` is a thin wrapper (mirrors `api.py`): it loads `.env` and exposes
the `app` object `src.bootstrap.worker_app.create_worker_app()` builds. The
composition root is `src/bootstrap/worker_container.py`
(`build_worker_container()`), which constructs the worker's process
singletons - a `PipeCatalog`, `GpuMonitor`, `SystemMonitor`, a `WorkerJournal`,
and the `WorkerCoordinator` that ties them together - the same one-function,
dependency-ordered pattern `src/bootstrap/container.py` uses for the main app,
scaled down to what a worker actually needs.

### Configuration (environment only - no settings table)

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `POTIONUI_WORKER_TOKEN` | yes | - | Bearer token every route requires. The process refuses to start without it. |
| `POTIONUI_WORKER_DIR` | no | `./worker_data` | Journal + artifacts + staged-assets root. |
| `POTIONUI_WORKER_ID` | no | generated once | Stable id to report in the handshake. |
| `POTIONUI_WORKER_PROVIDER` | no | `manual` | Opaque provider name (core never enumerates legal values). |
| `POTIONUI_WORKER_HOST` | no | `127.0.0.1` | Bind address. Never defaults to `0.0.0.0`. |
| `POTIONUI_WORKER_PORT` | no | `8100` | Bind port. |
| `POTIONUI_WORKER_DEVICE` / `_DTYPE` / `_VRAM_GB` | no | probed | Override what the worker injects into every pipe's config - see "Device injection" below. |
| `POTIONUI_BUILD_ID` | no | none | Fed into `compute_build_fingerprint`; absent degrades to a protocol-version-only fingerprint. |

## Route table

All routes below require `Authorization: Bearer <POTIONUI_WORKER_TOKEN>`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/worker` | Handshake: an enveloped `WorkerInfoV1` with probed capabilities and all three fingerprint domains. |
| `POST` | `/v1/executions` | Submit an enveloped `ExecutionPackageV1`. Idempotent on `execution_id` (same `request_digest` → current status/200; a different digest → 409). A per-pipe contract mismatch (or, for a package without `pipe_contracts`, a whole-catalog mismatch) or an expired package → `REJECTED`, never executed. A second submit while one is running → 429 (single-slot). Otherwise → 202, runs on a background thread. |
| `POST` | `/v1/executions/{id}/assets/{logical_id}` | Stream-upload one input asset ahead of execution; verified against the package's `input_assets` manifest before it is accepted. See "Input assets" below. |
| `POST` | `/v1/models/inventory` | Enveloped `ModelBundleManifestV1` in, enveloped `ModelInventoryResponseV1` out - which entries this worker's model depot already has (`present`), is missing, or has a size/digest `mismatch` for. Registers the manifest so a following staging upload can look an entry up by `(bundle_id, logical_id)`. 503 if this worker has no configured model depot. See "Models" below. |
| `POST` | `/v1/models/{bundle_id}/{logical_id}` | Stream-upload one model file into the depot, verified by size and digest against the entry registered via the inventory call above. `404` if that bundle/logical_id was never inventoried first. A re-upload of already-correct bytes is a safe no-op. |
| `GET` | `/v1/executions/{id}/events?after=N` | SSE stream of enveloped `JobEventV1`s: journaled events with `cursor > N` first, then live events, until a terminal event closes the stream. |
| `POST` | `/v1/executions/{id}/cancel` | `{"result": "accepted" \| "already_terminal" \| "not_found"}`. Cooperative - a pipe mid-`process()` finishes its current step first. |
| `GET` | `/v1/artifacts/{artifact_id}` | Streams a produced file from the worker's scoped artifacts directory. `artifact_id` is validated as a bare 32-hex-char id before it ever reaches a path. |

## How a pipeline is reconstructed and run

`src/features/remote_execution/worker/executor.py` (`WorkerPipelineExecutor`)
turns a `ProcessedPipelineV1` back into running pipe instances. The
instantiation algorithm is not invented for the worker - it is the same one
`PipelineExecutor` uses locally (`src/features/generation/generation.py:
601-624`): a pipe's class defaults (`get_default_config()`), deep-merged under
its shipped config (`deep_update`), then `validate_pipe_configuration` fills
any spec default still missing. Both functions are imported directly from
`src.features.generation.generation` rather than reimplemented.

What the worker does **not** reconstruct: `GenerationEngine`'s SERVICE-input
injection (GPU/SYSTEM/MEMORY/LLM/MODELS/ASSETS/SETTINGS) and its plugin
before/after-execute hooks. A worker has no PotionUI database, settings table,
or model-lifecycle cache to back MEMORY/MODELS/LLM/ASSETS/SETTINGS with, so
only `GPU` (a bare `GpuMonitor()`) and `SYSTEM` (a bare `SystemMonitor()`) -
the two that need no such state - are wired; a pipe requesting anything else
gets `None` injected, with a warning logged. This is a real gap for a pipe
that needs a real model-lifecycle cache to run for real, tracked here rather
than faked.

### Device injection

A package's pipe config already carries every pipe-class default merged in
(`src.features.generation.effective_config.merge_pipe_defaults`, run on the
dispatching side) *except* `device`/`dtype`/`vram_limit_gb` - core
deliberately leaves those three for whichever host actually executes the
pipeline. `device_injection.py` mirrors
`NativeBackend.prepare_pipes`'s `setdefault` injection exactly: the worker's
own device/dtype/vram wins only where the preset didn't set one explicitly.
`tests/features/remote_execution/worker/test_device_injection.py` proves this
against the real `NativeBackend` class, not a re-description of it.

One known, narrow gap inherited from core (not fixed here - out of this
card's owned files): a pipe whose own `configuration()` declares
`device`/`dtype`/`vram_limit_gb` as a real spec parameter with a literal
default (`tiled_refiner` is the one shipped example) gets that literal baked
into its config by `merge_pipe_defaults` before the worker ever sees it, which
makes the worker's `setdefault` a no-op for that one pipe. This is the "36-key
trap" `effective_config.py`'s docstring warns about; it lives in
`src/features/generation/effective_config.py` /
`src/features/generation/package_assembly.py`, both outside this worker's
owned files.

## Journal

`src/features/remote_execution/worker/journal.py` (`WorkerJournal`) keeps one
append-only JSONL file per execution under
`POTIONUI_WORKER_DIR/executions/<execution_id>.jsonl`: line 0 is
`{"request_digest": ...}`, every following line is a `JobEventV1`. This is the
worker's own state - it has no PotionUI database and never reads or writes
`src/features/remote_execution/{records,repository,policy}.py`, which is
core's row for the same execution. A new `WorkerJournal` instance over the
same directory (a process restart) recovers every execution's request digest
and full event history, which is what makes idempotency survive a restart.

## Input assets

`ExecutionPackageV1.input_assets` carries an
`src.platform.worker_protocol.input_asset.InputAssetManifestV1` when the
dispatching side collected any (`src.features.generation.input_assets`,
outside this worker's owned files) - `None` for a package with nothing to
stage. A pipe's `config`/`inputs` then carries `asset://<logical_id>` tokens
in place of the real host path.

`src/features/remote_execution/worker/assets.py` (`AssetStager`) is created
per execution (`WorkerCoordinator.submit`) and staged against that manifest:
`entry_for(package, logical_id)` looks the entry up via
`InputAssetManifestV1.asset()`, and `stage(entry, chunks)` writes the upload to
`execution_dir / entry.relative_path` under a temp name, hashing and
size-checking as bytes arrive, and only renaming into place once both match.
The upload route (`POST /v1/executions/{id}/assets/{logical_id}`) enforces the
declared size while still streaming the body in, so an oversized or runaway
upload is rejected (422) before it is fully buffered. A re-upload of an
already-staged asset is safe - the destination is content-addressed by the
entry, so re-staging identical bytes is a no-op overwrite.

Before running, `WorkerCoordinator._run` emits a `staging` event and blocks
(`_wait_for_assets`) until every logical id the manifest names has been
staged (or the package carries no manifest, in which case this is immediate).
A client polling `/v1/executions/{id}/events` sees `staging` as the current
status for as long as any asset remains unstaged, and only then `running`.
`WorkerPipelineExecutor` resolves `asset://<logical_id>` tokens found anywhere
in a pipe's *config* (recursively, through nested dicts/lists) to the staged
path at execution time, via the same `AssetStager.resolve`.

Unknown `execution_id` or `logical_id` on upload is a 404; a digest or size
mismatch is a 422 carrying the stager's reason.

**Across a process restart**, only the journal survives - `WorkerCoordinator`
keeps in-flight packages and their `AssetStager`s in process memory only (like
`_running_execution_id` and `_cancel_flags`). `submit()` answers *any*
execution id already present in the journal with `duplicate` (same digest) or
`digest_conflict` (different digest) without re-entering the run path, whether
or not that execution had reached a terminal state before the restart - so an
execution that restarted mid-staging is not resumed or restaged automatically.
Its `package_for`/`stager_for` are gone with the old process, so the upload
route 404s ("unknown execution") for it from then on: previously staged files
are not rediscovered from disk on the new process, they are honestly
unreachable rather than silently trusted. This mirrors the coordinator's
existing terminal-only restart contract
(`test_a_new_app_instance_over_the_same_work_dir_is_idempotent`); making a
restart mid-flight resumable is a larger change than this asset-staging fix.

## Core side

`src/features/backends/native_remote_backend.py` (`RemoteNativeBackend`) is
the `native.remote` driver: a configured instance of `NativeRemoteBackendConfig`
(`src/features/backends/backend_config.py` - `base_url`, a secret
`worker_token`, connect/request timeouts). Registered as a core builtin
alongside `native.local` in `BackendRegistry._register_builtin_backends`, so
it needs no plugin to exist. Unlike `NativeBackend`/`ComfyUIBackend` (both
`InProcessBackend`s executing pipes through a local `PipelineExecutor`), it
extends `BaseBackend` directly and never touches a `PipelineExecutor` - the
whole pipeline runs on the worker's hardware, not this host's.

### Collaborators

`RemoteNativeBackend` needs the process's `PipeCatalog` and `PluginRegistry`
to compute its own compatibility fingerprints. These are late-bound via
`bind_remote_context(pipe_catalog, plugin_registry)` rather than constructor
injection: `BackendRegistry._create_backend_instance` builds every backend the
same uniform way (`backend_class(backend_config=config)`), then duck-type
checks `hasattr(backend, "bind_remote_context")` the same way it already
duck-type checks `isinstance(backend, InProcessBackend)` for
`set_generation_manager`. `BackendRegistry` itself gained an optional
`pipe_catalog` constructor parameter for this (`src/bootstrap/container.py`
passes the container's one `PipeCatalog` instance).

Fingerprints (`pipe_catalog`/`plugin_bundle`/`build` - see
`src/pipelines/remote_fingerprint.py`) are computed once at bind time and
cached, per that module's own guidance (`compute_pipe_catalog_fingerprint`
forces eager pipe discovery the first time it runs).

### Dispatch flow (`start_generation` -> `_dispatch`)

1. Build a `ProcessedPipelineV1` from the orchestrator's raw pipe list
   (`build_processed_pipeline`, `src/features/generation/package_assembly.py`
   - the same merge-defaults logic the local executor and the worker both
   use, made public for this caller) and call `collect_input_assets` on it to
   get the `logical_id -> local source Path` map the transport needs later.
   `collect_input_assets` (`src/features/generation/input_assets.py`) now
   returns that map as a third element alongside the rewritten pipes and the
   manifest - it always resolved these paths internally, this only surfaces
   them.
2. `assemble_execution_package` builds the real `ExecutionPackageV1` (this
   re-runs `collect_input_assets` internally, on the same deterministic
   input, to produce the actual token-rewritten pipes and digest - a small,
   accepted duplication of file digesting rather than threading a
   precomputed manifest through the assembly API).
3. Create the `RemoteExecution` row (`RemoteExecutionRepository.create`,
   `id == generation_id`, idempotent on that key).
4. **Handshake + build pre-gate.** `GET /v1/worker`, compare the `build`
   domain against this backend's cached value only - `pipe_catalog`/
   `plugin_bundle` are whole-catalog and no longer gate here, since a worker
   missing a host-only plugin's pipe should not block a pipeline that never
   uses it. A build mismatch or an unreachable worker fails the row
   immediately (`error_code` `fingerprint_mismatch` / `worker_unreachable`)
   and returns *without ever calling submit*. The real pipe-catalog check is
   the worker's own per-package gate (`WorkerCoordinator._fingerprint_mismatch`):
   the package carries `pipe_contracts` (pipe_type -> contract fingerprint,
   one entry per pipe the pipeline actually uses); the worker rejects only if
   one of those types is missing from its catalog or its contract differs -
   never for a pipe the pipeline doesn't touch. A package built before this
   field existed (`pipe_contracts` empty) falls back to the old whole-catalog
   comparison across all three domains.
5. `RemoteExecutionRepository.claim_specific` leases the row directly by id
   (PENDING -> DISPATCHING), a single-row analogue of `claim_for_dispatch`
   added because the single-slot dispatch path (an execution is claimed the
   instant it's created, never queued behind another) has no pool to select
   from - reusing `claim_for_dispatch`'s oldest-first query would risk
   claiming a *different* row. A background task renews this lease every
   `lease_seconds / 2` for the life of the dispatch.
6. `POST /v1/executions`, then stream every declared input asset's real bytes
   up via `POST /v1/executions/{id}/assets/{logical_id}` (source path from
   step 1's map).
7. Consume `GET /v1/executions/{id}/events` to completion. Every event goes
   through `RemoteExecutionRepository.apply_job_event` (persist +, if the
   kind implies a state, transition the row) - the one place a worker event
   becomes row state, shared with the reconciler (below). An event carrying
   `payload["output"]` is a pipe's own `GenerationOutput`, encoded generically
   by `src/features/remote_execution/output_codec.py` (`encode_output` on the
   worker, walking the output's dataclass fields via
   `dataclasses.fields`/`typing.get_type_hints` rather than a per-type
   whitelist, so nothing a pipe emits - core's output types or a plugin's -
   needs separate wiring to cross the wire); its `event.artifacts` are
   downloaded first (`WorkerTransport.download_artifact`, sha256 + size
   verified against the `ArtifactRefV1`, written under
   `<storage>/remote_imports/<generation_id>/`), then `decode_output`
   reconstructs the identical dataclass instance from the payload plus the
   `{artifact_id: local_path}` map those downloads produced, and it is handed
   to the same `emit` callback a local pipe would call - so the normal
   handler pipeline (gallery/video/audio/param/models/...) persists it
   exactly as it would a local generation's output. `pipe_progress` events are
   always one of these (a pipe's own `ProgressGenerationOutput`, icon/title
   included); the worker-lifecycle kinds without a payload
   (`staging`/`running`/`pipe_started`) are instead synthesized into a
   `ProgressGenerationOutput` here with the `<<PIPE:name>>` title convention.
   A failed/rejected event's `JobErrorV1` becomes an `ErrorGenerationOutput`.
8. Any exception between claiming the row and a terminal event (submit
   failing, an upload failing, an artifact failing digest verification, the
   connection dying) marks the row FAILED (`error_code: dispatch_error`)
   before re-raising, so nothing is left stuck DISPATCHING/STAGING/RUNNING
   forever - this single-slot MVP has no separate loop that would otherwise
   retry and eventually notice.

**`cancel_generation`** calls `POST .../cancel` and, on `"accepted"`, also
moves the row PENDING-adjacent state to `CANCELLING` itself
(`RemoteExecutionRepository.apply_state`) - required because the state
machine only allows a worker's eventual `cancelled` event to land the row
*from* `CANCELLING`, never straight from `RUNNING`/`STAGING`/`DISPATCHING`
(`src/features/remote_execution/records.py`'s `LEGAL_TRANSITIONS`). One
consequence: the worker keeps emitting its own ordinary progress events right
up until it notices the cancel, so one of those can legitimately be
journaled *after* core has already moved the row to `CANCELLING`.
`apply_job_event` treats that as a stale, dropped state implication (logged,
not raised) rather than an `IllegalStateTransition` - the event is still
persisted, only the (now-superseded) state transition is skipped.

**Models.** `RemoteNativeBackend` builds a real `ModelBundleManifestV1`
(`src/features/remote_execution/model_bundle_builder.py`, `build_model_bundle`)
from the processed pipeline's own model references before dispatch: it walks
every pipe's `config` for the `{"file_path": ..., "name": ...}` shape every
model-picker field writes (diffusion model, text encoder, VAE, LoRA stack,
...), resolves each file against the models feature's own index
(`ModelRepository.get_by_file_path`), and turns it into one
`ModelBundleEntryV1` - `logical_id`/`role` from the model's own identity
(`model_type/filename`, the same identity `get_by_identity` matches models by
across backends), `relative_path` from `MODEL_TYPE_TO_DIRECTORY`, and
`digest`/`size_bytes` from the model's already-recorded `sha256`/`file_size`.
Identical files referenced from more than one pipe collapse to one entry;
entries are sorted by `logical_id` before the bundle digest is computed, so
the same pipeline always produces the same manifest bytes.

**Digests are never computed at dispatch time.** A model with no recorded
digest (never indexed, or an HF-layout directory model - digesting a
directory model isn't implemented, since it needs per-shard entries rather
than a single file digest) fails the dispatch immediately with an actionable
`ModelBundleResolutionError` naming the file and telling the operator to
re-index it, rather than hashing a multi-gigabyte checkpoint on the hot
dispatch path or silently shipping a bundle nothing can verify.

**Model sync is admin configuration, never a dispatch side effect.** Before
`POST /v1/executions`, `RemoteNativeBackend._dispatch` only *checks* worker
inventory - `model_bundle_staging.find_unstaged_entries` posts the bundle
manifest to `POST /v1/models/inventory` and returns whatever the worker
reports missing or mismatched. If anything comes back, dispatch fails
immediately (`error_code="models_not_staged"`, no submit) naming the missing
filenames and pointing at Admin -> Backends -> `<name>` -> Models; nothing is
pushed. Pushing/fetching bytes onto a worker's depot happens only through
that admin surface (`src/features/remote_execution/ops.py`,
`POST /api/admin/remote-models/{backend_id}/push` and `.../fetch`), which
reuses the same bundle-entry resolution `build_model_bundle` uses
(`resolve_bundle_entry` - `ModelRepository.get_by_id`, hashing on demand) and
streams to `POST /v1/models/{bundle_id}/{logical_id}`
(`WorkerTransport.upload_model`) or hands the worker a provider-resolved URL
to pull directly (`POST /v1/models/fetch`). `stage_model_bundle` (the old
inventory-then-push routine) still exists and backs the push op's upload
loop; it is simply never called from dispatch anymore. A pipeline referencing
an HF-layout directory model still cannot dispatch (see below) -
`build_model_bundle`/`resolve_bundle_entry` refuse it before a manifest is
ever built, and `RemoteNativeBackend` turns that refusal into a
`generation_error` naming the preset and the offending model file rather than
a raw exception/stack trace.

A pipe's config still carries the *dispatching host's* absolute path
verbatim (`package_assembly`'s "model paths stay verbatim" design) - staging
does not rewrite it. It is `remap_model_paths` (worker-side,
`src/features/remote_execution/worker/path_remap.py`), run by
`WorkerCoordinator._run` immediately before execution, that resolves each
`file_path` to its depot location by matching the model bundle's entries on
filename, so the pipe actually reads the just-staged depot copy, never the
unreachable original path.

### Transport (`src/features/remote_execution/transport.py`)

`WorkerTransport` wraps `httpx.AsyncClient` per call (mirrors
`ComfyUIBackend`'s per-call `aiohttp.ClientSession`), one method per worker
route, translating HTTP outcomes into typed results
(`SubmitResponse`/`WorkerInfoV1`/`JobEventV1`) or one of three exceptions
(`WorkerUnreachableError`, `WorkerProtocolError`,
`ArtifactVerificationError`). Accepts an optional `transport=` override
(`httpx.AsyncBaseTransport`) purely for tests - real usage never sets it.
Note for anyone testing against it: `httpx.ASGITransport` fully drains an ASGI
response before handing bytes back to the client rather than truly streaming
it as the app produces it, so a genuinely still-in-flight SSE connection
can't be exercised through it the way a real socket allows - tests that need
that either target a pipe that finishes quickly (the response completes and
the whole buffered history is delivered at once) or a cancel-aware pipe that
notices promptly. That draining is not perfectly reliable for a very fast,
fully-synchronous burst of events (observed under `ASGITransport` specifically:
the response can close one event short of the worker's actual terminal event) -
`RemoteNativeBackend._consume_events` treats a stream that closes without ever
delivering a terminal event as a dropped connection, not a finished execution,
and reconnects with `GET .../events?after=<last cursor seen>` (bounded retries)
rather than leaving the row stuck non-terminal. This is a real production
concern too, not just a test artifact - the same thing can happen to a real
SSE connection over a flaky link to rented compute.

### Reconciliation

`src/features/remote_execution/reconciler.py` (`RemoteExecutionReconciler`)
makes the `remote_executions` table honest again after a restart:
`requeue_expired_leases` + `expire_overdue` + `fail_exhausted` (pure
repository sweeps, no network), then a bounded, best-effort resume of the
event stream for any row still non-terminal whose `backend_id` resolves to a
`native.remote` config - each row gets `event_pull_timeout_seconds`
(default 5s) via `asyncio.wait_for`; an unreachable or slow worker is counted
and logged, never allowed to block the sweep or (see below) app startup.
Deliberately does **not** re-wire an `emit` callback into a resumed row: the
generation-side WebSocket bridge that owned one lived in the process that
died, and nothing here can hand a resumed output to it - this only keeps the
row and its persisted event history correct. A generation left non-terminal
by the restart is reconciled the same way a local one is
(`GenerationRepository.reconcile_interrupted_generations`, called alongside
this), not resurrected here.

Wired at `src/bootstrap/app.py`'s lifespan startup, right after the existing
`reconcile_interrupted_generations()` call, wrapped in its own try/except so
a reconciliation failure never prevents the app from starting.

### Known gaps

- **An admin push does not survive a worker restart mid-upload.**
  `ModelDepot` keeps a registered bundle manifest (`bundle_id -> entries`) in
  process memory only - if the worker restarts between an inventory call and
  the staging uploads that follow it, that in-flight push's uploads 404
  ("no such model entry ... call /v1/models/inventory with this bundle
  first"). Already-staged files on disk are untouched (digest sidecars
  persist); the fix is re-running the push from Admin -> Backends ->
  `<name>` -> Models, which re-registers via a fresh inventory call and
  resumes correctly (an entry already `present` is never re-sent).
- **Ephemeral/serverless compute defeats the depot's whole premise.** Staging
  is a real optimization only because a worker's depot persists across
  dispatches (an entry already `present` is never re-sent). A worker whose
  disk doesn't survive between invocations (a serverless/per-job container)
  would re-stage its full model set on every single dispatch - staging would
  still be correct, just no better than re-uploading from scratch each time.
- **HF-layout directory models are not bundled.** `build_model_bundle`
  refuses to build an entry for a directory-layout model (`Model.is_directory`)
  rather than emit a wrong one - a pipeline that references one cannot
  dispatch to `native.remote` today.
- **Reconciliation resume fidelity.** A resumed row's persisted event history
  is correct, but no live output is re-emitted to any UI - by design (see
  above), but worth knowing if a "what happened while I was down" view is
  ever built on top of this.
- **`list_models`/model listing** is not implemented for `native.remote`
  (`supports_model_listing()` stays at `BaseBackend`'s default `False`) -
  model availability routing does not yet consider a remote worker's models.

## Liveness

Provisioned compute (a `native.remote` backend filled in through a
`ComputeProvisioner` plugin) is kept honest by two things, both generic across
providers:

- **Bring-up feedback.** `POST /api/admin/provisioning` returns at once with the
  row in `provisioning`; the provisioner runs in a background task and reports
  each phase it can see (`preparing`, `creating`, `starting`, `waiting_worker`,
  `ready`). Every report lands on the row's `progress` timeline and goes out
  on `/ws/admin` as `{"type": "compute_status", "row": {...}}` — the whole row,
  every time. The Infrastructure tab renders the timeline live; a failure lands
  as `failed` with the provider's message, and can be provisioned again or
  terminated.
- **Heartbeat.** `ComputeStatusMonitor` (started in the app lifespan) asks each
  provisioner for `status(handle)` every `provisioning.status_interval_seconds`
  (settings table; default 15, minimum 5). A pod paused or deleted in the
  provider's own console shows up within one interval as `stopped`/`missing`,
  its backend is disabled so no generation routes to it, and every open admin
  page gets the `compute_status` push. When the pod comes back the row returns
  to `running` but the backend stays disabled — the tab offers "Enable
  backend" rather than guessing. `GET /api/admin/provisioning/{row_id}` runs
  the same reconcile on demand. A row left in `provisioning` by a server
  restart is marked `failed` on the first tick, since nothing will finish it.
- **Start again.** A `stopped` (or `unreachable`/`unknown`) row has a "Start"
  action: `POST /api/admin/provisioning/{row_id}/start` sets it to `starting`
  and runs the provisioner's `start(handle)` in the background with the same
  live timeline (`starting`, `waiting_worker`, `ready`). When the worker
  answers, the backend gets the (possibly new) connection details and is
  enabled — this is an explicit operator start, so unlike the heartbeat it
  does re-enable. A failure lands as `failed` with the provider's message and
  the backend stays disabled.

State vocabulary: `provisioning | starting | running | stopped | missing |
unreachable | failed | unknown` — see the "Contributing a compute provisioner"
section of `docs/plugin-api.md` for what a provisioner returns and when.
