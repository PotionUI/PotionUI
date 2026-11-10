---
category: Presets / Models
category_order: 70
order: 45
---

# The Plugin API

`src.plugin_api` is the surface a plugin imports from. It is the **only** part of the
backend a plugin may import.

```python
from src.plugin_api import User, get_current_active_user, generate_ulid
```

`db` is the one export to import differently — inside the function that uses it,
never at your module's top level:

```python
def list_rows():
    from src.plugin_api import db
    with db.get_cursor() as cursor:
        ...
```

A module-level `from src.plugin_api import db` copies the database handle into your
module's namespace the first time your plugin is imported, and keeps that copy for the
life of the process. Anything that later swaps the application's database — a test
harness, most commonly — swaps the handle your module is no longer reading. Importing
inside the function resolves it fresh on every call.

Everything else under `src/` is internal. It is refactored, renamed and moved as the
application changes, and none of those changes are announced. A plugin that imports an
internal path will keep working right up until it doesn't — and it will fail at load
time, in someone else's installation.

## What it guarantees

- **Stability.** A name exported here will not be removed or have its meaning changed
  without a deprecation first. Where the class actually lives is free to change; your
  import is not affected.
- **Completeness.** If a plugin needs it, it belongs here. A gap is a bug in the API, not
  an invitation to import around it — ask for the name to be exported.
- **Nothing else.** Anything you can reach that is not exported here is internal, no
  matter how convenient it looks or how well it works today.

## What's importable

Every name below can be imported directly from `src.plugin_api`. They are also grouped
into modules (`src.plugin_api.providers`, `.chat`, `.backends`, …) if you prefer to
import from those — the names are identical, so it is purely a matter of taste.

| Group | Module | Exports |
|---|---|---|
| **Identity** — who is calling, and signing them in | `.identity` | `User`, `AccountType`, `get_current_active_user`, `get_user` (a user by id, or `None`, for background work that acts as a known user), `register_login_provider`, `unregister_login_provider`, `sign_in_external`, `ExternalSession`, `ExternalLoginError` |
| **Hooks and runtime** — reacting to the app, reaching its managers | `.hooks` | `HookContext`, `HookResult`, `HookSpec`, `hooks_registry`, `PluginRegistry`, `get_container`, `get_app_loop`, `run_on_app_loop`, `get_global_plugin_registry`, `get_global_tool_registry`, `ModelLifecycle` |
| **Providers** — talking to a model marketplace | `.providers` | `MarketplaceProviderBase`, `ProviderCapability`, `ProviderMetadata`, `ProviderModelInfo`, `ProviderSearchResult`, `ProviderPromptItem`, `ProviderError`, `ProviderConnectionError`, `ProviderRateLimitError`, `ProviderNotFoundError`, `get_provider_registry`, `ModelInfo`, `aiohttp_connector`, `client_ssl_context`, `is_certificate_error`, `certificate_failure_message`, `HttpMarketplaceProvider`, `register_provider`, `retry_after_from_headers` |
| **Chat** — extending the assistant | `.chat` | `BaseTool`, `ToolContext`, `ToolResult`, `ToolSource`, `PreChatAction` |
| **LLM** — contributing an LLM configuration type | `.llm` | `Adapter`, `HttpAdapter`, `OpenAIChatAdapter`, `RenderRequest`, `WireRequest`, `CapabilityProfile`, `ProfileLayer`, `Settings`, `ToolSpec`, the message parts, stream events and provider errors, `LLMConfig`, `DiscoveredModel`, `DiscoveredWindow` |
| **Backends** — contributing an engine, and reading the enabled ones | `.backends` | `InProcessBackend`, `BaseBackendConfig`, `BackendStatus`, `BackendHealth`, `BackendModel`, `ModelListingNotSupported`, `deduplicate`, `list_backends()` (every enabled backend, highest priority first: what `GET /api/backends/enabled` returns for it, credentials redacted, plus `execution_device` (`"this_host_gpu"`, `"no_gpu"`, `"remote"` or `"unestablished"`), `max_concurrent_runs` and `vram_total_gb` / `vram_free_gb`; the VRAM is known only for a local native backend on the GPU this process monitors and is `None` otherwise; a backend that is enabled but not running reports `"unestablished"` and a `None` capacity; not user-scoped: it returns what `GET /api/backends/enabled` returns to any signed-in user) |
| **Cloud** — contributing a hosted-model provider, reading the enabled models and pricing a run | `.cloud`, `.cloud_testing` | `estimate_cloud_cost(user, model_id, shots, driver=None)` (what a run of shots on an enabled cloud model would cost, the answer of `POST /api/cloud/models/{model_id}/estimate`; each shot is the route's `{"task", "params"}`; administrators only, `PermissionError` for anyone else; `ModelNotFoundException` for a model that is not an enabled cloud model), `DirectorEstimateError` (a `ValueError`: the shots it cannot price), `enabled_cloud_models(backend_id)` (the `CloudCatalogEntry` of each model turned on in a cloud backend's catalog, by slug: `model_id`, `label`, `provider_model_id`, `tasks`, `outputs` and its `spec`), `resolve_cloud_model(model_id)` (a `ResolvedCloudModel`: the `backend_id`, `driver` and catalog `entry` of an enabled cloud model, with its `spec`; `None` when no enabled cloud backend offers it), `CloudCatalogEntry`, `ResolvedCloudModel` (none of these reads carries a backend credential; the two catalog reads are unscoped: they do not check that a given user may reach a model, which `submit_generation` does and `visible_model_ids(user_id)` lists), and the provider kit: `register_cloud_provider`, `CloudProvider`, `CloudBackendConfig`, `CloudHttp`, `CloudModelSpec`, `ParamSpec`, `MediaInputSpec`, `PriceLine`, `CloudRequest`, `CloudJob`, `CloudStatus`, `CloudResult`, `CloudArtifact`, `CloudCost`, `CloudHealth`, `CloudError`, `LocalMedia`, `CANONICAL_PARAMS`, `MEDIA_ROLES`, `TASK_KINDS`, `CLOUD_BLOCKS`, `spec_problems`, `valid_specs`, `load_catalog_file`, `encode_media`, `decode_inline`, `default_error_mapper`, and (from `.cloud_testing`) `FakeCloudProvider`, `FakeCloudConfig`, `FakeBehaviour`, `FakeClock`, `ContractCase`, `run_contract`, `CONTRACT_CHECKS`, `fake_director_specs`, `fake_video_bytes`, `TINY_PNG`, `bind_plugin_backend` |
| **Pipes** — contributing a pipeline step | `.pipes` | `BasePipe`, `PipeInput`, `PipeOutput`, `PipeInputSpec`, `PipeOutputSpec`, `PipeConfigSpec`, `IOType`, `GenerationOutput`, `ImageGenerationOutput`, `VideoGenerationOutput`, `MeshGenerationOutput`, `GalleryGenerationOutput`, `ProgressGenerationOutput`, `TextGenerationOutput`, `TextArtifactAction`, `ComfyUIWorkflowGenerationOutput`, `GenerationExecutionError`, `Icon`, `Progress`, `logger`, `OutputTypeSpec`, `SerializeContext`, `output_type_registry`, `DuplicateOutputTypeError`, `PreEncodedReference` (an already VAE-encoded reference a pipe hands to a `reference_latents` input), `MAX_BUNDLE_COPIES` (the most copies a prompt resource bundle row may ask for) |
| **Native engine** — driving generation through the in-process engine directly | `.native` | `Conditioning`, `GeneratorContext`, `GeneratorKrea2Pipe`, `NativeGeneratorHandle`, `ProgressEmitter`, `native_step_hooks`, `load_native_component(path, kind)` (one component file, such as a `vae`, loaded on the CPU by the engine's own loader and returned as its wrapper: `.module`, `move_to(device)`, `unload()`) |
| **Presets** — finding a preset, starting a generation | `.presets` | `PresetCollaborators`, `preset_operations`, `FilePresetRepository`, `GenerationRequest`, `PromptPair`, `PresetMedia`, `GalleryItem`, `lint_preset_dir`, `PRESET_MEDIA_MAX_BYTES` and `PRESET_COVER_VIDEO_MAX_BYTES` (the byte budgets the preset linter warns above for any preset media file and for a video cover), `downscale_and_save_webp`, `preset_template(preset_id)` (a deep copy of a loaded preset as its loader built it, a `PresetTemplate` whose fields follow the loader and are not yet a stable contract, or `None`), `resolve_preset_path(preset_id, reference)` (a path the preset names, with its `{{ paths.preset }}` and `{{ paths._shared }}` variables filled and resolved; `None` unless it stays inside the preset's folder or the shared presets folder), `preset_install_state(preset_id)` (a `PresetInstallState`: whether the preset is `installed` and `assigned` to anyone), `describe_preset` (a loaded preset's name, `vars` and modes, each with its default form and every form's fields as `type` + `required` + `multi`, and its `engine`, `category` and `requires`; `None` when no preset with that id is loaded, installed or not), `list_presets(user)` (a `PresetDescription` of each installed preset the user may run, the only one of these preset reads scoped to a user: `describe_preset`, `preset_template`, `resolve_preset_path` and `preset_install_state` take no user; see [Reading presets](#reading-presets)), `PresetDescription`, `ModeDescription`, `FieldDescription`, `bind_director_media(request, role, media, segment_id=None, promote=False, *, at=None, capabilities=None, field=None)` (puts a resolved image, from `resolve_media_ref` or `extract_frame`, into the request's Video Director document, keeping the rest of it. A single-shot document takes it as its `first` or `last` frame; with `promote=True` a first frame turns a text-only shot into a start-image shot and a last frame turns a start-image shot into a first-and-last shot. A `director` document takes `first` or `last` on `segment_id`, or a `keyframe` at `at` seconds from the start of that segment, as the preset's director mode allows: see [`media`](video-director.md#media) in docs/video-director.md. Role `reference` adds one media or a list of them (images, videos, audio tracks) to the reference pool of the preset mode, the form field `field` or the first of its `reference_fields`, and with `segment_id` cites them in that segment's prompt: see [`references`](video-director.md#references-segment). `capabilities` defaults to the preset's `vars.video_director` for the request's mode, read from the running application: a `director` document or a `reference` bound before the application has started, such as in a plugin's own unit tests, raises the container's `RuntimeError` unless `capabilities` is given. Raises `DirectorBindingError` with a `code`), `RequirementBackendInfo` (the resolved backend a `RequirementContext` carries: `id`, `engine`, `driver`, `config`) |
| **Forms** — the field types core ships | `.forms` | `core_field_type_names()` (the names of every core non-container field type, e.g. to validate a `field_type` a form-building tool is handed) |
| **Generation** — submitting, previewing, following and reading generations | `.generation` | `submit_generation`, `preview_memory`, `cancel_generation`, `generation_state`, `generation_record`, `generation_failure` (administrators only), `generation_active`, `list_generation_files`, `resolve_media_ref`, `get_session`, `get_session_snapshot`, `GenerationRequest`, `MediaRefError`, `SessionNotFoundError`, `IdempotencyKeyConflict`, and the errors `submit_generation` and `preview_memory` let through, to catch by type: `FormBindingError` (a `ValueError`: the form does not bind), `FormNotFoundException` (no form of that name), `NoBackendForEngineError` and `NoEligibleBackendError` (both `RuntimeError`: no backend can run the request) |
| **Compute** — renting GPU compute for a Remote Native worker | `.compute` | `ComputeProvisioner`, `ComputeProvisionerError`, `ComputeStatus`, `ProvisionRequest`, `ProvisionResult`, `ProvisionProgress`, `ProgressReporter`, `ComputeFieldDescriptorV1`, `ComputeFieldOptionV1`, `COMPUTE_STATES`, `STATE_*`, `STAGE_*`, `COMPUTE_HOOKS` |
| **Storage** — keeping data | `.storage` | `db`, `generate_ulid`, `plugin_settings`, `Settings`, `SettingRepository`, `PluginRepository` |
| **Media** | `.media` | `convert_image_to_base64`, `BackgroundMattingModel`, `probe_video` (a `VideoProbe` from ffprobe, or `None`), `transcode_video(src, dest, TranscodeSpec(...), max_bytes=None)` (ffmpeg re-encode to webm VP9 or mp4 H.264: constant frame rate, even dimensions, no audio unless `keep_audio`; steps down a quality ladder to fit `max_bytes`; raises `VideoTranscodeError`, or `FfmpegUnavailableError` before writing anything when ffmpeg is missing), `find_ffmpeg`, `TranscodeSpec`, `VideoProbe`, `StreamingMp4Writer(out_path, width, height, fps)` (pipes RGB uint8 frames to ffmpeg into a video-only MP4; `write`, `close`, `abort`, or a `with` block; raises `FFmpegNotFoundError` when ffmpeg is missing), `has_audio_stream(path)`, `mux_audio_from_source(video_only, source, out_path)` (video of one file plus audio of another, stream-copied or AAC; returns `False` and leaves no partial file when there is no audio or ffmpeg fails), the frame schedule of frame interpolation, the one `interpolator/rife` uses: `source_per_output(src_fps, factor=2, target_fps=0.0)` (source frames per output frame, an exact `Fraction`: `1/factor`, or `src_fps/target_fps` with both rates as fractions), `output_fps(src_fps, factor=2, target_fps=0.0)`, `output_count(n_source, ratio)` (output frames for `n_source` source frames, the duration kept) and `slot(index, ratio)` (a `Slot`: output frame `index` lies between source frames `left` and `left + 1` at `t` in [0, 1), `t` snapped to 0 within 1e-3), so two interpolators given the same clip place the same frames, NTSC rates on long clips included; `extract_frame(user, ref, at="last", purpose="user_upload")` and `save_user_media(user, path, purpose="user_upload")` (async; one frame of a video the user may see, or a file the plugin wrote, stored as the user's upload and returned as a `StoredMedia`; see [Working with the user's media](#working-with-the-users-media)), `media_local_path(user, ref)` (the path on this server of the file of a media the user may see, or `None` when the storage keeps it elsewhere), `copy_media_file(user, ref, target)` (streams that file into a file of the plugin), `upload_formats()` (the file extensions the Library takes, by `media_type`), `StoredMedia`, and the media reference names of `.generation`: `resolve_media_ref`, `MediaRefError`, `HistoryMediaRef`, `LibraryMediaRef`, `ResolvedMedia` |
| **Models** — model metadata fields, provider links, resolving a model's file/folder across roots, and acting on catalog models | `.models` | `WellKnownModelMetadataField`, `get_model_provider_info`, `ModelFileUnavailable` (raised by `resolve_model_file` when the model's file is not reachable), `model_type_dirs`, `model_write_dir`, `resolve_model_file`, `model_for_path(path, admin=False)`, `model_for_sha256(sha256, *, size=None, hash_candidates="background", admin=False)` (the indexed model with that hash, or `None`; pass the file's `size` in bytes and a local copy that has not been hashed yet but has exactly that size can match as well: `"background"` queues at most eight of those candidates per call at the normal, rate-capped priority and answers `None` until they are done, `"paced"` verifies at most two of them through the rate-capped pool, waiting up to 15 minutes, `"inline"` hashes up to eight of them before answering at full speed, `"never"` only looks; `ValueError` for any other mode; unscoped; both leave out the operational fields - location, file size, hash - unless `admin=True`, for a caller that has checked the user is an admin), the hook `model_index.after_digests` (payload `model_ids`, at most once every 5 seconds: files finished being hashed, so a model's `sha256` is now known), `visible_model_ids(user_id)` (the models assigned to a user, directly or through their groups), `assign_model_to_user(model_id, user_id)` (`True` when newly assigned; fires `model_index.before_assign` / `after_assign`; raises `ModelAssignmentException`), `update_model_metadata(model_id, values)` (sets some shared attribute values, validated against the type's definitions; raises `InvalidModelMetadataException`, `ModelNotFoundException`), `run_provider_fetch(provider, model_ids)` (async; hashes any model that has no digest yet, one after another, then fills empty description, triggers, tags and previews from a provider plugin; `{"successful", "failed", "errors"}`; raises `ProviderFetchException` for an unknown or uninitialised provider, or a `model_index.before_fetch_info` veto), `ProviderFetchException`, `directory_aliases()`, `type_for_folder_name`, `model_types()` (the registered model types, plugin types included, read at call time), `HeaderView`, `TensorInfo`, `FamilyMatch`, `model_classifier_registry` (read-only) |
| **Downloads** — queueing a download and following it | `.downloads` | `queue_model_download(url, *, filename=None, model_type=None, subdir=None, tags=None, checksum_sha256=None, provider_id=None, created_by=None, destination_backend_id=None)` (async; the queued download as a dict; raises `DownloadQueueException`, or its `UnsafeFilenameException` and `DownloadDestinationInvalid` subtypes - the latter when the subfolder, directory or remote backend cannot be used, as opposed to a queue that is blocked), `get_download(download_id)` (the download's current state, or `None`), `DownloadNotFoundException`. Declare the `download.after_complete` hook to be told when a download has landed (`download_id`, `created_by` ...; `model_id` or `duplicate_of` only for a plain local model download, the one kind indexed on completion) |
| **User groups** — groups, membership, and the NSFW policy a user gets | `.groups` | `list_groups()`, `get_user_groups(user_id)`, `group_member_ids(group_id)` (groups are `{"id", "name", "is_system", "content_policy"}` dicts), `effective_content_policy(user_id)` (`"allowed"`, `"blur"` or `"blocked"`: the instance setting made stricter by the user's groups), `assign_models_to_group(group_id, model_ids, acting_user)` (the ids newly assigned, through the group operation and its hooks in that admin's name; `ValueError` for a non-admin actor or an unknown group) |
| **Notifications** — telling users something happened | `.notifications` | `notify(*, source, title, message="", user_id=None, type="", level="info", category="plugin", metadata=None, transient=False, show_toast=True)` (how many users received it; `user_id=None` broadcasts; a registered `type` respects each user's preferences), `register_notification_types(registry, specs)` (from a `notification_type.register` handler; returns the keys it added), `NotificationTypeSpec`, `NotificationTypeRegistry`, `DuplicateNotificationTypeError`. The `notification_type.register` hook runs at startup for the enabled plugins and, for a plugin an admin enables while the app runs, at that enable, so its types (and their `admin_only`) apply without a restart. It runs once per plugin and process: types are never unregistered, so a disabled plugin's types stay declared, and a re-enable does not run it again; types a plugin update adds are declared at the next restart |
| **Auto-organize** — contributing rule conditions and actions | `.organize` | `OrganizeItem`, `OrganizeChange`, `OrganizeActionBlocked`, `OrganizeActionSkipped`, `OrganizeActionRetry`, `compare`, `FACT_KINDS`, `OPERATOR_LABELS`, `CONFIG_FIELD_KINDS`, `FACT_TRIGGERS`, `SQL_ALIASES`, `SUBJECTS` |
| **Limits** — contributing a plan limit kind, and reading where a user stands | `.limits` | `LimitKind`, `Usage`, `Refusal`, `MeasureContext`, `RefusalContext`, `AdmissionRequest`, `LimitEvents` (`record(user_id, kind, units=1, ref_id=None)`, `refund(ref_id)`), `LimitEventsView`, `LimitExceeded`, `limit_status(user_id, kind)` (a `LimitStatus`: `limit` (None when nothing limits the user for that kind), `used` in the current window, `exempt`, `resets_at`, `contact_line`, plus `limited` and `remaining`; for a plugin that admits its own operations against a kind it measures itself) |
| **Phrasebook** — contributing a batch tool to Find & replace | `.phrasebook` | `PhrasebookBatchOperation`, `PhrasebookBatchContext`, `BatchOutcome`, `BatchPreview`, `BatchOperationError` |
| **Sampling** — contributing a step algorithm or a sigma schedule | `.sampling` | `SamplerDefinition`, `ScheduleDefinition`, `ScheduleContext`, `OptionSpec`, `GuidanceStrategy`, `SamplingCancelled`, `run_hooks`, `sample_euler`, `sampler_registry`, `schedule_registry` |
| **Filters** — contributing a photo filter op with a Python counterpart | `.filters` | `ColourOp`, `SpatialOp`, `apply_filter`, `compile_lut`, `parse_cube`, `validate_steps`, `Cube`, `CubeError` |
| **Setup** — telling admins what to do after enabling the plugin | `.setup` | `SetupCheck`, `SetupCheckContext`, `SetupCheckResult` |

Each module's docstring explains what its exports are for; this table is the index.

## Examples

An admin-only route:

```python
from fastapi import APIRouter, Depends, HTTPException

from src.plugin_api import AccountType, User, get_current_active_user

router = APIRouter(prefix="/api/plugins/my-plugin")


@router.post("/reindex")
async def reindex(user: User = Depends(get_current_active_user)):
    if user.account_type != AccountType.ADMIN:
        raise HTTPException(status_code=403, detail="Admins only")
    ...
```

A chat tool:

```python
from src.plugin_api import BaseTool, ToolContext, ToolResult


class CountDatasetsTool(BaseTool):
    name = "count_datasets"

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        ...
```

Declare it in `manifest.yml`:

```yaml
tools:
  - class: "backend.tools.count_datasets_tool:CountDatasetsTool"
    modes: ["my-plugin"]
```

**A tool without `modes` is global — it appears in every chat mode, including the
built-in generation chat.** That is rarely what you want for a tool that only makes
sense on your plugin's own page. Give it a `modes` list naming the chat mode(s) it
belongs to (see "Contributing a chat mode" below); the host applies it as an
instance-level override after constructing your tool, so the class itself stays free
of any registry wiring.

Say what the tool does and when to call it in `description`. That text is what the model
sees with native tool calling and with prompt-injected tools alike, and what MCP clients
see. `hint` is optional and only fills your own mode's `{{TOOL_HINTS}}` placeholder; keep
it to what the description cannot say. The builtin tools set no hint.

A tool is sent to the model up front unless the context window is small. Set the class
attribute `context_group` to something other than `"core"` for a tool the model only
needs now and then; on small windows it is then held back until the model asks for it
(see "Tools by context window" in `docs/chat-modes.md`). Tools your mode lists under
`tools:` are always sent.

A pipe that finishes a mesh pipeline:

```python
from src.plugin_api import GalleryGenerationOutput, MeshGenerationOutput


def process(self, pipe_input, generation_outputs):
    generation_outputs(GalleryGenerationOutput(
        images=[],
        meshes=[MeshGenerationOutput(mesh_path=path, temporary=False, seed=seed)],
    ))
```

The core `gallery` pipe takes only `IOType.IMAGE` and `IOType.VIDEO`, so a mesh
pipeline builds its own `GalleryGenerationOutput` in its terminal pipe rather than
routing through it. `temporary=False` is what persists the file and writes its `files`
row; `temporary=True` saves to `tmp/` and records nothing. Only `.glb` is accepted, and
the file is validated as glTF-binary before it is stored — an emitted file that is not
one fails the save rather than being served to a viewer as a broken mesh.

A hook handler:

```python
from src.plugin_api import HookContext


def on_generation_complete(context: HookContext) -> HookContext:
    context.data["my-plugin.notified"] = True
    context.metadata["my-plugin.seen"] = context.data["generation_id"]
    return context
```

**Handlers are synchronous.** The hook chain calls your handler and reads the
returned `HookContext` immediately, so an `async def` handler is rejected at
registration and a handler that returns a coroutine is recorded as a failed hook
result. Return the context you were given (data changes and metadata annotations
both propagate to the next handler and to the caller); returning anything else is
a contract error.

To hand long-running work back to the caller, schedule it and leave the
awaitables under `HookContext.data[HOOK_BLOCKING_WAITS_KEY]` (the constant lives in
`src/platform/plugins/hooks.py`; its value is the literal key
`"__hook_blocking_waits__"`). Only call sites that drain them wait: today
`generation.before_start`
(`src/features/generation/orchestrator.py`) and `chat.message.before_send`
(`src/features/chat/conversation.py`). Every other hook point ignores the key, so a
handler for one of them must not rely on the work being finished when the hook
returns.

Reaching the application's managers — always inside the function that needs them, never
at import time, because the container does not exist yet while your module is being
imported:

```python
from src.plugin_api import get_container


async def start_something():
    orchestrator = get_container().generation_orchestrator
    ...
```

## Initializing your plugin: `boot` vs `enable`

Four lifecycle hooks fire around a plugin's own life. They are ordinary manifest hooks —
declare a handler the same way you would for any other hook point:

```yaml
hooks:
  backend:
    - hook: "plugin.lifecycle.boot"
      handler: "backend.hooks.lifecycle_hooks.on_boot"
    - hook: "plugin.lifecycle.ready"
      handler: "backend.hooks.lifecycle_hooks.on_ready"
    - hook: "plugin.lifecycle.enable"
      handler: "backend.hooks.lifecycle_hooks.on_enable"
    - hook: "plugin.lifecycle.disable"
      handler: "backend.hooks.lifecycle_hooks.on_disable"
```

| Hook | Fires |
|---|---|
| `plugin.lifecycle.boot` | Once per process, for every enabled plugin — at startup, and right after `enable` when a plugin is enabled at runtime |
| `plugin.lifecycle.ready` | Once per process, for every enabled plugin, once the event loop is running and generations a previous process left unfinished have been marked failed — and right after `boot` when a plugin is enabled at runtime |
| `plugin.lifecycle.enable` | Only on the disabled → enabled transition, **never** at boot |
| `plugin.lifecycle.disable` | Exactly once whenever an enabled plugin is torn down: admin disable, uninstall, reload, a rescan that replaces its changed manifest or finds its folder gone, or a mount failure. Fires before the plugin's hooks are unregistered, and never for a plugin that never reached enabled |

A backend hook entry also accepts `remote: true` (default `false`). Set it on a
hook whose handler must run inside a Remote Native worker's execution of the
pipeline (for example a `prompt.transform` handler), as opposed to a hook that
only runs core-side before dispatch. Plugins that contribute no pipe but do
declare a `remote: true` hook are included in the worker compatibility
handshake the same way a pipe-contributing plugin is
(`compute_remote_plugin_bundle_fingerprint`,
`src/pipelines/remote_fingerprint.py`); hooks left at the default are core-only
and invisible to that handshake.

**Per-process initialization belongs in `boot`.** `enable` fires exactly once in the
lifetime of an installation: the moment an admin turns the plugin on. It does not fire
again when the server restarts — the startup resync re-registers an already-enabled
plugin's handlers without replaying its enable transition, because a restart is not a
transition. A plugin that creates its tables or warms a cache from `enable` alone works
until the first restart and then silently stops being initialized.

```python
def on_boot(context):
    """Runs on every process start; make it idempotent."""
    MyPluginTables().create_all()
    return context
```

`boot` is dispatched only to the subject plugin's own handler, so `context.get("plugin_id")`
is always your plugin. `enable` and `disable`, by contrast, broadcast to every enabled
plugin's handler — those are for reacting to *another* plugin's state changing, so a
handler there must check `plugin_id` before assuming the event is about itself.

A handler that raises is logged and skipped: one plugin's failing `boot` cannot abort
startup, block another plugin's `boot`, or fail the enable that triggered it. None of the
hooks can block the transition they report.

Because the registry owns the teardown signal, a plugin that starts background work in `ready`
can cancel it in `disable` and rely on it: the same teardown fires `disable` on a reload or a
rescan that swaps in a changed manifest, and the replacement copy receives `boot` and `ready`
again. The handler runs while the registry lock is held (reentrant), so it may call back
into the registry.

`ready` is for work that needs the running application rather than just the process:
reconciling the plugin's own records against generation state after a restart, or starting
a background coroutine. Like `boot` it is dispatched only to the plugin's own handler. Handlers
are synchronous; `src.plugin_api.hooks.run_on_app_loop(coro)` schedules a coroutine on the
application's event loop from any thread and returns a `concurrent.futures.Future`
(`get_app_loop()` returns the loop itself). Both raise `RuntimeError` before the application has
started, and `ready` is the first point at which they work. Do not block a `ready` handler on that
future: the handler runs on the loop it is waiting for.

```python
from src.plugin_api.hooks import run_on_app_loop

def on_ready(context):
    run_on_app_loop(reconcile_my_records())
    return context
```

## Choosing a plugin category

`category:` in `manifest.yml` decides which group the plugin appears under in Admin → Plugins.
It describes what the plugin adds, not how it is built.

| Value | Label | What belongs here |
|---|---|---|
| `backends` | Backends & compute | Engines, cloud providers and compute hosts that run generations |
| `sources` | Model sources | Marketplaces and hubs models are browsed and downloaded from |
| `steps` | Generation steps | Pipes and processing steps that run inside a generation |
| `tools` | Tools & pages | Editors, exporters, viewers and importers the user works with |
| `security` | Sign-in & security | Login providers and access control |
| `monitoring` | Monitoring | Resource monitors and cleanup helpers |
| `developer` | Developer | Examples and reference extensions |
| `other` | Other | Anything that fits none of the above (the default) |

The old values `generation`, `models`, `media`, `workflow` and `system` still load: they map to
`backends`, `sources`, `tools`, `tools` and `other`. Any unknown value loads as `other`. Both
cases log a warning that names the plugin.

The plugin `type`, its `capabilities` and its registered hooks are not shown on plugin cards; they
sit in a collapsed "Technical details" section on the plugin's page.

## Settings form

A manifest's `settings:` list renders as the plugin's form in **Admin → Plugins → Settings**.
Each entry has `name`, `type` (`string`, `number`, `boolean`), `label`, and optionally
`description`, `required`, `default` and `is_secret` (encrypted at rest, masked in every
response, rendered as a password field). A `select` entry adds `options` (`{value, label}`) and a
`number` entry may add `min` and `max`.

A plugin reads its own settings with `plugin_settings(plugin_id)` from `src.plugin_api`: one
dict with the stored value (credentials decrypted) or the manifest `default` for every
non-`info` entry. A `number` is an `int` when its `default` is an int, otherwise a `float`; a
`boolean` is a `bool`; a `select` is one of its `options`; a value outside `min`/`max` is
clamped to the bound with a logged warning; a value that does not parse falls back to the
default. A `string` is returned as stored, except that an empty value reads as the `default`
when the entry has one; set `allow_empty: true` on the entry to keep an empty string (for "none
selected"), and an entry with no `default` always keeps it. Keep only
what the manifest cannot express - trimming, URL validation, splitting - in the plugin.

An entry with `type: info` renders as a notice instead of an input and is never stored.
Use it when the plugin depends on something configured elsewhere:

```yaml
settings:
  - name: "account_creation_notice"
    type: "info"
    label: "First-time sign-ins need account creation"
    description: "Someone signing in for the first time is turned away until \"Create accounts on first external login\" is on."
    href: "/admin?tab=settings&view=external_login"
    link_label: "Open External Login settings"
```

`href` and `link_label` are optional. `/admin?tab=settings&view=<group>` opens Admin →
Settings on that group.

## Login providers

A login provider signs users in through an external identity source — an OIDC issuer, a
company SSO, anything with a redirect and a verifiable answer. Core carries none of that
protocol and takes no dependency for it. Your plugin owns the protocol and the two routes
that drive it; core owns the mapping onto a real PotionUI user, so groups, model access,
preset access and per-user resources all keep working exactly as they do for a local
account.

Two calls make it work:

```python
from src.plugin_api import register_login_provider, sign_in_external
```

### Putting the button on the login page

`register_login_provider(id, label, start_path, auto_redirect=None)` adds one entry to the list the login page
renders under the local username/password form, as a button reading `Continue with {label}`.

```python
PLUGIN_ID = "acme-sso"

def enable():
    register_login_provider(PLUGIN_ID, "Acme SSO", f"/api/plugins/{PLUGIN_ID}/start")
```

`start_path` is a GET route on your own plugin router. It must live under
`/api/plugins/<your plugin id>/`, because that prefix is what ties the entry to your
plugin: disabling the plugin removes every provider registered under it, the same way it
removes your field types and chat modes. A `start_path` outside that prefix still works,
but core cannot tell whose it is and will not clean it up for you — call
`unregister_login_provider(id)` yourself.

Registering the same provider id twice raises `DuplicateLoginProviderError`; a missing
label or a relative `start_path` raises `InvalidLoginProviderError`.

`auto_redirect` is an optional zero-argument callable returning a bool, called every time the
list is served (so an admin toggle applies without a restart). While it returns true the login
page sends signed-out visitors straight to that provider's `start_path` and hides the local
form; `/login?local=1` always brings the form back. A callable that raises counts as false.
Only the first provider whose callable returns true is auto-redirected to.

`GET /api/auth/providers` serves the list, unauthenticated, as
`[{"id": ..., "label": ..., "start_path": ..., "auto_redirect": false}]` in registration order. The login page
renders nothing at all when it is empty.

### Signing the user in

`sign_in_external(issuer, sub, claims)` is the other half. **Core never verifies an
external identity — you do.** Validate the provider's response (signature, audience,
nonce, expiry, whatever your protocol requires) in your callback route, and only then hand
core the result:

| Argument | What it is |
|---|---|
| `issuer` | Stable identifier of the identity source, e.g. the OIDC `iss` value. |
| `sub` | The provider's stable, non-reassignable subject id for this person. Never the email. |
| `claims` | Dict carrying `email`, `email_verified`, `preferred_username` and `name`. All optional. |

What core does with it:

- An existing `(issuer, sub)` mapping signs that user in and stamps `last_login_at`.
- No mapping, and the admin setting **Link by verified email** is on: a local user whose
  email matches `claims["email"]` is adopted and the mapping recorded — but only when
  `claims["email_verified"]` is true. An unverified email is ignored, because trusting one
  would let anyone who can claim an address take over the account behind it.
- No mapping and no link: a local user is created when the admin setting **Create accounts
  on first external login** is on, and the sign-in is refused with `ExternalLoginError`
  when it is off (the default). A created account is always an ordinary `USER`, never an
  admin, gets the configured default group, and takes its username from
  `preferred_username`, then the email's local part, then `sub` — normalized to the
  username policy and de-duplicated against existing accounts. The account keeps
  `claims["email"]` only when `claims["email_verified"]` is true; otherwise it gets a
  placeholder address, so an unverified address can never be squatted and later matched by
  **Link by verified email**.

The session that comes back is the same one the password login issues, minted by the same
code path.

### The callback contract

`sign_in_external` returns an `ExternalSession`:

| Field | What it is |
|---|---|
| `user` | The `User` that was signed in. |
| `access_token` | The bearer token. Never put this in a URL. |
| `handoff_code` | Single-use code, valid for two minutes, that the browser trades for the token. |
| `created` | True when this sign-in created the local account. |

The browser must end up holding the access token the same way a password login leaves it
holding one, and a backend route cannot write the frontend's storage. So your callback
redirects to the login callback page carrying `handoff_code`, and the page exchanges it:

```python
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from src.plugin_api import ExternalLoginError, sign_in_external

router = APIRouter(prefix=f"/api/plugins/{PLUGIN_ID}")

@router.get("/callback")
async def callback(code: str, state: str):
    claims = verify_with_your_provider(code, state)
    try:
        session = sign_in_external(ISSUER, claims["sub"], claims)
    except ExternalLoginError:
        return RedirectResponse("/login?error=external_login", status_code=302)
    return RedirectResponse(
        f"/login/callback?code={session.handoff_code}", status_code=302
    )
```

`/login/callback` POSTs the code to `POST /api/auth/external/exchange`, receives
`{"access_token": ..., "token_type": "bearer"}` — byte-for-byte the shape
`POST /api/auth/login` returns — establishes the session through the same store the
password login uses, and routes to `/`.

Send `handoff_code`, never `access_token`. A URL lands in browser history, in the
`Referer` header of the next request, and in any proxy log on the way; the code is
single-use and short-lived, so a copy of it is worthless by the time anyone reads it back.
The exchange endpoint answers 401 for a code that is unknown, already spent, or expired,
and the landing page shows a "this sign-in link has expired" message with a way back to
the login page.

### What an admin controls

Three SYSTEM settings, in Administration → System Settings → External login:

| Setting | Default | Effect |
|---|---|---|
| `external_login_auto_create` | off | Whether an unmapped external identity may create a local user. |
| `external_login_default_group` | none | Group a user created this way is added to. |
| `external_login_link_by_email` | off | Whether an external identity may attach to an existing local user by verified email. |

Local username/password login is untouched by all of this, and the owner account can
always sign in locally — an identity provider outage never locks an instance out.

The mappings live in the `external_identities` table (`issuer`, `subject` → `user_id`,
`created_at`, `last_login_at`), unique on `(issuer, subject)`, and cascade away with the
user.

## Contributing a cloud provider

A provider plugin gives PotionUI one hosted-model service (images, video or audio generated on the
provider's servers). It imports only from `src.plugin_api.cloud` and, in its tests,
`src.plugin_api.cloud_testing`. Its `backend.register` handler registers the provider, and the driver
`cloud.<key>` becomes a creatable backend type:

```python
from src.plugin_api import HookContext
from src.plugin_api.cloud import register_cloud_provider

def register_backend(context: HookContext) -> HookContext:
    from ..provider.acme import AcmeProvider
    register_cloud_provider(context, AcmeProvider)
    return context
```

The provider subclasses `CloudProvider` (`discover`, `submit`, `poll`, optionally `fetch`, `cancel`,
`check`, `map_error`) with a `CloudBackendConfig` subclass for its settings, and does all network I/O
through `self.http` (`CloudHttp`), which sends credentials only to the API's own origin, refuses
private download targets, redacts secrets and maps HTTP errors to the nine `CloudError` kinds. The
plugin ships its own presets through the manifest `presets:` root, assembled from the shared blocks
listed in `CLOUD_BLOCKS`. `cloud_testing` provides a scripted `FakeCloudProvider` and a contract
kit (`ContractCase`, `run_contract`) to run against recorded fixtures. The full contract, the test
kit and a step-by-step example are in [Cloud Models](cloud-models.md#writing-a-provider-plugin).

## Showing admins the next setup step

A plugin that needs more than the enable switch (a backend with an API key, models turned on,
presets installed and assigned) lists those steps under `setup:` in `manifest.yml`. Once the plugin
is enabled, Admin → Plugins shows a **Setup needed** badge on its card, and its Overview tab opens
with a **Setup** section: the next step and its button first, then every step with its state. Each
step is checked against the instance as it is now, so the list updates as the admin works through it.

```yaml
setup:
  - kind: "backend.added"
    driver: "cloud.acme"
    label: "Add an Acme backend with your API key"
  - kind: "cloud.models_enabled"
    driver: "cloud.acme"
  - kind: "presets.installed"
  - kind: "presets.assigned"
```

| `kind` | Done when | The button opens |
|---|---|---|
| `plugin.settings` | every `settings:` entry marked `required: true` has a value | the plugin's Settings tab |
| `backend.added` (needs `driver`) | an enabled backend uses `driver` | Add backend with that driver picked, or the backend that is turned off |
| `cloud.models_enabled` (needs `driver`) | one of those backends has at least one catalog model turned on | that backend's Catalog tab |
| `presets.installed` | at least one preset from the plugin's own `presets:` roots is installed | the first preset |
| `presets.assigned` | an installed preset of the plugin is assigned to a user or a group | the first installed preset |
| `check` (needs `check` and `label`) | your `SetupCheck` says so | the `action_href` you return |

`label` and `description` are optional on the built-in kinds and replace the default wording; the
description shows only while the step is not done. A step that depends on an earlier one (models
before any backend exists, assignments before anything is installed) reads *waiting* and has no
button. When a step names a `driver` that is not loaded yet, which is the case right after enabling
a plugin that registers a backend, a **Restart PotionUI** step with a restart button comes first.
When the manifest has `docs:`, the section also links to the first page.

A plugin-specific check is a class implementing `SetupCheck`:

```python
from src.plugin_api import SetupCheck, SetupCheckContext, SetupCheckResult

class AccountLinked(SetupCheck):
    def evaluate(self, context: SetupCheckContext) -> SetupCheckResult:
        if context.settings.get("account_id"):
            return SetupCheckResult(done=True, description="Account linked.")
        return SetupCheckResult(
            done=False,
            description="Link the account this plugin bills to.",
            action_label="Open settings",
            action_href="/admin?tab=plugins&id=acme",
        )
```

```yaml
setup:
  - kind: "check"
    check: "setup_checks:AccountLinked"
    label: "Link your Acme account"
```

`context.settings` holds the plugin's saved global settings. `evaluate` runs on every visit to
Admin → Plugins, so keep it to local reads: no network calls. A check that cannot be loaded or
raises shows as a step still to do. The report is served admin-only at `GET /api/plugins/setup`
and never carries prices or costs.

## Contributing a chat mode

A plugin with its own page (a `pages:` entry) can give that page a dedicated chat
assistant instead of sharing the generic one. Declare it in `manifest.yml`; no Python
class is needed:

```yaml
chat_modes:
  - id: "my-plugin"
    name: "My Plugin Assistant"
    description: "Helps with things specific to this plugin"
    icon: "photo"
    default_route_prefixes:
      - "/plugins/my-plugin"
    tools:
      - "count_datasets"
    system_prompt: |
      You are an assistant for My Plugin. Use the tools below to ground your answers.

      Available tools:
      {{TOOL_HINTS}}
```

The frontend resolves which mode a chat panel opens in by matching the current route
against every mode's `default_route_prefixes` (longest prefix wins); visiting your
plugin's page auto-selects this mode, no navigation wiring required on your part.
`tools:` lists tool names the mode's system prompt always mentions
(`{{TOOL_HINTS}}` expands to their hints); a tool becomes usable in the mode either by
being named there or by declaring the mode in its own `modes:` list (see "A chat tool"
above) — declaring it in both places is redundant but harmless. Disabling the plugin
removes the mode and its tools from every registry automatically.

### Driving the chat panel from your plugin's page

`window.__potionui.chat` is the frontend host API (`$lib/plugin-api/host.ts`) a
plugin-hosted page uses to work with the global chat assistant without the assistant
knowing your plugin exists:

- `provideContext(key, provider)` — registers a function called on every chat send;
  its return value is merged into the outgoing `context_metadata` under `key` (a `null`
  return is skipped, not sent). Call the returned unregister function when your page
  unmounts.
- `declareMode(modeId)` — makes `modeId` the assistant's active mode for as long as the
  declaration is active, overriding the route-prefix match (useful for a mode that
  isn't reachable through any route prefix, e.g. an admin-only setup wizard). The most
  recently declared mode wins if more than one is active; unregistering restores
  route-based resolution.
- `onToolApplied(toolName, handler)` — runs `handler` with the result of each call to
  `toolName` in the conversation (its `data`, decoded from JSON): a tool that needs approval
  once the user approves it, any other tool once the assistant's reply that ran it has
  arrived. For an approved tool it runs before (and instead of) any core hardcoded handling
  for that tool name. See "When `onToolApplied` runs" below.
- `open()`, `close()`, `toggle()` — open, close or flip the global chat panel, the same
  panel the sidebar button and the keyboard shortcut drive. Pair them with
  `declareMode`/`provideContext` to open the assistant already focused on your page, for
  example from an "Ask the assistant" button.
- `isOpen()` — whether the panel is open right now.
- `prefill(text, {replace?})` — puts `text` in the chat composer without sending it, opens
  the panel if it is closed and focuses the composer, so the user reads and edits the message
  and sends it, or not, as with one they typed. A draft the user has already typed is kept;
  pass `{replace: true}` to overwrite it. Called while the panel is still loading, the text
  lands as soon as the chat is ready; it is dropped if the panel is closed first or the chat
  is not ready within 15 seconds.
- `send(text)` — sends `text` as the user's message, as if they had typed it, in the mode
  the page resolves (the route-prefix match, or your `declareMode`), opening the panel if it
  is closed. The user does not confirm it: the message appears in the conversation as theirs
  and the assistant answers it; a tool declared with `requires_approval` still waits for their
  approval as usual. When the open conversation is in another mode, a new conversation is
  started in the page's mode; the previous one stays in the chat history. Your
  `provideContext` providers run for this message like for any other, and the draft in the
  composer is left alone. Returns a promise that resolves once the message is on its way (not
  when the assistant has answered) and rejects with an `Error` when the chat is unavailable:
  no chat model configured, the assistant still answering the previous message, the
  conversation could not be created, the panel closed before the chat was ready, or the chat
  not ready within 15 seconds. An empty `text` rejects with a `TypeError`. Prefer `prefill`
  when the user should read the message before it goes.

`open`, `close`, `toggle`, `isOpen`, `prefill` and `send` are absent on hosts that predate
them: feature-detect them before offering a button that relies on them, or call them as
`window.__potionui.chat.open?.()`.

```js
const chat = window.__potionui.chat;
draftButton.hidden = !chat.prefill;
draftButton.onclick = () => chat.prefill('Plan the shots for this storyboard');
sendButton.hidden = !chat.send;
sendButton.onclick = async () => {
  try {
    await chat.send('Add a closing shot to the storyboard');
  } catch (error) {
    window.__potionui.notifications.toast('error', error.message);
  }
};
```

```js
const unregisterContext = window.__potionui.chat.provideContext('comfyui_import_wizard', () => ({
  step: currentStep,
  workflow_id: workflowId
}));
const unregisterMode = window.__potionui.chat.declareMode('comfyui-import-wizard');
const unregisterTool = window.__potionui.chat.onToolApplied('import_comfyui_workflow', (result) => {
  applyImportedWorkflow(result.workflow);
});
askButton.onclick = () => window.__potionui.chat.open();

// on unmount:
unregisterContext();
unregisterMode();
unregisterTool();
```

**When `onToolApplied` runs.** A tool declared with `requires_approval` reaches the handler
when the user approves it; a rejected call never does. Any other tool reaches it when the
assistant's reply that ran it is final: at the end of the streamed answer (including one the
app reattaches to after a reload), when the app recovers a reply whose stream was cut, in the
non-streaming fallback, and for the tools the assistant runs while it carries on after an
approval. Such a call reaches the handler only when it succeeded and its `data` is JSON: a
tool that returns plain text, a failed or skipped call, and a call that waits for approval or
whose approval expired are not passed on. Nothing reaches the handler while the user has
switched the assistant's tools off (Tools OFF in the composer).

Each call reaches the handler at most once. A call is identified by its assistant message and
its position in that message, so a reply that arrives twice (live, then again when the app
recovers it after a dropped connection) does not run the handler again, and an approved call
is never run a second time as an unapproved one. Opening an earlier conversation from the
chat history does not replay its tool results, and a handler registered after a result
arrived does not receive it.

The handler may return `{status, message?}` with `status` one of `'applied'`, `'stale'`,
`'partial'` or `'noop'`. When it reports `'stale'` or `'partial'` with a `message`, the chat
shows that message in place of the assistant's narration for that reply, the same way it
does for an approved tool; when one reply ran several such calls, the last message wins. It
changes what is shown, not the stored conversation. A handler that throws is logged, and the
reply's other calls still reach their handlers.

```js
const unregisterShot = window.__potionui.chat.onToolApplied('my_plugin_add_shot', (result) => {
  if (result.project_id !== currentProjectId) {
    return { status: 'stale', message: 'That shot belongs to another project; it was not added here.' };
  }
  timeline.insertShot(result.shot);
  return { status: 'applied' };
});
```

On a host that predates this, only approved calls reach the handler, and nothing on
`window.__potionui` tells the two apart.

**Following a turn live.** A tool that runs without approval reaches `onToolApplied` only
when the whole reply is final, which can be many calls later. A page whose tools save its
document on the server can follow each call as it ends: the host dispatches a
`potionui:tool-end` event on `window` for every tool call of a streamed turn, with
`detail = {tool_name, success, pending}` (`pending` is true for a call that now waits for the
user's approval). The event carries no result, so read your own state again, and keep
`onToolApplied` for the result itself: a page that misses the event (a host that predates it,
a reply recovered after a dropped connection) still gets the result there.

```js
const onToolEnd = (event) => {
  const { tool_name, success, pending } = event.detail;
  if (success && !pending && myTools.includes(tool_name)) reloadDocumentIfNewer();
};
window.addEventListener('potionui:tool-end', onToolEnd);
// on unmount:
window.removeEventListener('potionui:tool-end', onToolEnd);
```

## Contributing an LLM configuration type

A plugin can add a provider to the Type list of Admin → LLM / Assistant → Add configuration.
Declare it under `llm_providers:`:

```yaml
llm_providers:
  - type: acme
    label: Acme
    backend: backend.adapter:AcmeAdapter   # "module.path:ClassName"
    default_base_url: https://api.acme.example/v1
```

`backend` is loaded and instantiated once when the plugin enables, like a
`recipe_steps[].backend`, and every configuration whose `type` is `acme` is then sent through
that object. The object is an `Adapter`: it renders a `RenderRequest` (the typed messages,
tools and settings of one model call) into a `WireRequest`, sends it, and decodes the reply
into stream events. For a service that speaks the OpenAI chat-completions schema, subclass
`OpenAIChatAdapter` from `src.plugin_api.llm` and adjust the request body in `build_body`,
which every call (streamed, buffered and tool-calling) goes through before it is sent:

```python
from src.plugin_api.llm import CapabilityProfile, OpenAIChatAdapter, RenderRequest

class AcmeAdapter(OpenAIChatAdapter):
    display_name = "Acme"

    def build_body(self, request: RenderRequest, profile: CapabilityProfile) -> dict:
        body = super().build_body(request, profile)
        body["max_tokens"] = body.pop("max_completion_tokens")
        return body
```

The inherited `list_models` and `discover` read the service's `/models` listing, so the
model picker and the model's context window work without more code; override them (they
return `DiscoveredModel` entries and a `ProfileLayer`) when the service lists models another
way. A service with its own wire format subclasses `HttpAdapter` and implements `render`,
`decoder`, `discover` and `list_models`; override `classify_error` when its error replies
need a different mapping to the typed provider errors. The form lists `label` after the built-in types. When the admin picks a type, it puts
that type's `default_base_url` in the Base URL if the field is empty or still holds another
type's default, and leaves a URL the admin typed alone. A `type` named like a built-in one
(`openai`, `ollama`, `native`) is ignored, and a type another enabled plugin already owns
fails the enable. Disabling the plugin takes the type away: configurations saved with it
stay, and fail with "Unsupported LLM type" until the plugin is enabled again.

## Contributing automation templates

An enabled plugin can add immutable starter workflows to the Automation Templates
catalog. Declare each portable automation envelope in `manifest.yml`; no Python import
or handler is required:

```yaml
automation_templates:
  - id: clear-runtime-memory
    title: Clear runtime memory
    description: Release memory held by this runtime after a generation finishes.
    category: system
    icon: trash
    tags: [memory, gpu]
    path: automations/clear-runtime-memory.json
```

`id` must contain lowercase letters, numbers, underscores, or hyphens and is namespaced
at runtime as `plugin:<plugin-id>:<id>` (core templates use `core:<id>`). `path` is
relative to the plugin directory and cannot escape it. The JSON file uses the same
`potionui.automation` schema produced by the automation export endpoint.

Templates are catalog entries, not live automations. Choosing one creates a new,
user-owned, disabled automation through the normal import validation path. If its node
types are unavailable, the catalog explains which requirements are missing and prevents
instantiation. Disabling the plugin removes its templates from the catalog; automations
users already created from them remain theirs.

## Contributing Auto-organize facts and actions

Auto-organize lets each user write rules that file their own generations, Library
uploads and models: "when a new image is made with Krea-2 at 1344 x 768, add it to
Landscapes". A rule has one subject, a list of conditions (a *fact*, an operator and a
value) matched with all or any, and a list of actions. A plugin adds facts and actions
of its own; they appear in the rule builder with no frontend work, because the builder
picks its control from the fact's `kind`.

```yaml
organize_facts:
  - key: tagger.labels            # must be namespaced: <plugin>.<name>
    label: Image contains
    subjects: [generation, upload]
    kind: tag_list                # model_ref | enum | size | number | text | tag_list | bool | attribute
    operators: [has, has_not]     # optional, a subset of the kind's operators
    handler: organize.labels      # module.function, like hook handlers
    sql_handler: organize.labels_sql          # optional, makes previews exact and fast
    options_handler: organize.label_options   # optional, feeds the picker's autocomplete
    options: []                   # optional static choices: [{value, label}]
    picker: {}                    # optional hints for the control (see below)
    triggers: [item_created, tags_changed]

organize_actions:
  - key: tagger.notify
    label: Tell my webhook
    subjects: [generation]
    requires_admin: true          # anything that reaches outside the app
    handler: organize.notify
    undo_handler: organize.undo_notify       # optional
    config_schema:
      - {key: channel, kind: text, label: Channel, required: true}
    scope: user
    notes: []
    needs: []
    done_label: ""
    paced: false
```

`scope` is `user` (the default) or `instance`, `notes` are lines the editor shows in one
info box, `needs` is `[digest]` to wait for a model's file hash, `done_label` is the
summary of a finished run and `paced` marks an action that reaches an outside service;
the last four are explained under "Instance actions".

Import only from `src.plugin_api.organize`. The reference implementation used by the
tests lives at `tests/fixtures/organize_example_plugin/`.

### Kinds, operators and values

| kind | operators | value the rule stores | control the builder shows | `picker` keys |
|---|---|---|---|---|
| `model_ref` | `is`, `is_any_of`, `is_not` | a model id, or a list for `is_any_of` | the model picker | `model_types` |
| `enum` | `is`, `is_any_of`, `is_not` | a string, or a list for `is_any_of` | chips or a select; autocomplete when there is an `options_handler` | `multi` |
| `size` | `is`, `at_least`, `at_most` | `{"width": 1344, "height": 768}` | resolution presets + custom | `presets` |
| `number` | `is`, `at_least`, `at_most` | a number | stepper | `min`, `max`, `step`, `unit` |
| `text` | `contains`, `not_contains`, `starts_with`, `ends_with`, `is`, `is_not` | a string, matched case-insensitively; a core fact such as a model's name can also store `ignore_separators: true` on the condition to compare without `-` and `_` | text input | `placeholder` |
| `tag_list` | `has` (every listed value), `has_not` (none of them) | a list of strings | tag autocomplete | - |
| `bool` | `is` | `true` / `false` | toggle | - |
| `attribute` | per attribute type, see below | `{"key", "type", "label", "value"}` | attribute picker, then the type's operators and control | `operators_by_type` |

`compare(kind, operator, actual, expected)` is the exact function the evaluator uses, so
a plugin can test its handler against it. When a fact returns a list (one value per
output of a multi-file generation), a condition matches when **any** value matches.
`not_contains` and `is_not` match when **no** value matches, so an item without the
value matches them.

### The `attribute` kind

An `attribute` fact is a set of named, typed values on each item, such as the custom
attributes admins define for models. The user picks one attribute, then compares its
value with the operators of the attribute's type:

| type | operators | value | matching |
|---|---|---|---|
| `number` | `is`, `at_least`, `at_most` | a number | a `[low, high]` range counts as an interval: `is` means inside it, `at_least` compares `high`, `at_most` compares `low` |
| `text` | the `text` operators | a string | a list of strings (tags) matches like a multi-value text fact |
| `bool` | `is` | `true` / `false` | a missing value counts as `false` |
| `enum` | `is`, `is_any_of`, `is_not` | a string, or a list for `is_any_of` | case-insensitive |

`ATTRIBUTE_VALUE_OPERATORS` in `src.plugin_api.organize` holds this table. An
`attribute` fact must have an `options_handler`, and it lists the attributes rather than
values: each entry is `{"value": key, "label": label, "meta": {...}}`, where `meta` has
`type` (one of the four above), `type_label` (shown next to the name in the picker),
`model_types` (the model types it applies to, empty for every type), `choices`
(`[{value, label}]`, for `enum`) and optional `min`, `max`, `step` (for `number`). The
host calls it when a rule is saved or previewed: the condition's key must be listed,
and the stored `type` and `label` come from the listing, not from the client. The
handler gets the caller's `user_id`, so leave out attributes the caller may not see.

The fact `handler` returns a dict of attribute key to that item's value. The
`sql_handler` receives the whole `{"key", "type", "label", "value"}` dict as `value`.
Set `picker: {operators_by_type: {...}}` only to narrow the operators per type.

### The handlers

- **Fact `handler(item) -> value`** receives an `OrganizeItem` (`subject`, `item_id`,
  `user_id`, read-only `data`) and returns the value for its kind. It must be pure and
  fast: no network, no writes. `item.data` holds, for generations: `preset_id`, `mode`,
  `backend_id`, `form_data`, `model_ids`, `files` (`file_type`, `width`, `height`,
  `duration_seconds`, `mime_type`, `is_final`) and `tags`; for uploads: `media_type`,
  `mime_type`, `width`, `height`, `duration_seconds`, `original_filename` and `tags`; for
  models: `filename`, `model_type`, `family`, `sha256`, `tags`, `file_size` (bytes),
  `description`, `custom_name` (the rule owner's own name for it), `provider_names`,
  `provider_descriptions`, `sources` (`local`, `cloud` or provider ids such as
  `civitai`) and `attributes` (each attribute's value for the rule owner: their own
  value for a per-user attribute, else the shared value, else the default). Reading
  your own plugin tables with `from src.plugin_api import db` inside the handler is fine.
- **Fact `sql_handler(operator, value, alias) -> (clause, params) | None`** returns a SQL
  predicate over the subject's table, aliased per `SQL_ALIASES` (`g` = `generations`,
  `u` = `uploads`, `m` = `models`). `viewer.user_id` is the rule owner's id, for
  per-user data. It turns the preview count into one query. Without it, previews
  evaluate items one by one and say "about" past 5,000 items.
- **Fact `options_handler(user_id, subject, query) -> list`** returns strings or
  `{value, label}` dicts, plus an optional `meta` dict that reaches the client as is.
  It is called with a 2 second budget.
- **Action `handler(item, config, user_id) -> list[OrganizeChange]`** does the work for
  one matching item and returns what it changed (`target_type`, `target_id`,
  `target_name`, `data`). Return only what is new, and make it idempotent: the same item
  can be offered again after a restart. The list is recorded so the Activity tab can
  show it and undo it. Raise `OrganizeActionBlocked(code, message)` to stop the rule and
  pause it with a notice.
- **Action `undo_handler(item, change, user_id) -> bool`** reverses one recorded change.
  Without it, undoing a run leaves that plugin's changes in place and counts them as
  skipped.
- **Outcomes.** A handler that returns normally has done its job (an empty list means
  there was nothing new). It can say more by raising:
  `OrganizeActionBlocked(code, message)` stops the rule and pauses it with a notice;
  `OrganizeActionSkipped(code, message)` is permanent for this item: the item counts as
  done, the other actions of the rule still run, and the run lists the item as
  *skipped* with the message; `OrganizeActionRetry(code, message, after_s=None)` is
  transient: the item is **not** done, the rule's remaining actions are not run for it,
  and the run lists it as *waiting*. The host tries a waiting item again after 5, 15,
  60 and 240 minutes (or `after_s` seconds), and after five attempts turns it into a
  skip with the code `gave_up`. Any other exception counts as a retry with the code
  `error`; it never marks the item done.

### Instance actions

`scope: instance` makes an action part of the instance-wide rules admins keep under
Admin, Models, Auto-organize (`/api/admin/organize/instance/...`). Such a rule belongs to
nobody: the handler gets `user_id=None`, runs as the system, and the run records which
admin started it. An instance action is never offered to users, and a user action is
never offered to instance rules. Instance actions are for the `model` subject only.

- `needs: [digest]` makes the host call the handler only once the model's `sha256` is
  known. Until then the model is recorded as waiting (`unverified`) and the host runs the
  action when `model_index.after_digests` reports the file; the host never hashes a file
  to satisfy it.
- `paced: true` marks an action that calls an outside service. Such a rule is exempt
  from the hourly "filed N items" pause, and a run applies it in small steps so progress
  moves per item. Keep the handler idempotent and raise `OrganizeActionRetry` for outages.
- `notes` and `done_label` are text for the editor and the run summary.

`triggers` says when the fact's value can first be known: `item_created`, or also
`tags_changed` for a value that arrives later (an auto-tagger), or `metadata_changed`
for a model value that changes when its description, attributes, provider info or a
user's own name for it is edited (`model_index.after_update_metadata`), or
`detected_tags_changed` for a generation value written by Media Tagging
(`media_index.after_tags_written`). Rules using such a fact are evaluated again on those
changes, for items the rule has not filed yet. A fact whose `triggers` leave out
`item_created`, like the built-in **Detected tags**, is not evaluated when the item is
created: a rule using it first runs when its event arrives, so a "does not have" test
cannot file an item before its tags exist.

### What the host guarantees

- A rule only ever reads its owner's items and writes to its owner's collections and
  tags. Plugin actions receive the owner's `user_id` and must hold to the same rule.
- An action with `requires_admin: true` is hidden from non-admins and refused when a
  non-admin saves a rule with it.
- Disabling the plugin removes its facts and actions. Rules that use them stay, marked
  "needs attention", and run again when the plugin is back.
- A rule files each item at most once, and a live rule that files more than the hourly
  limit (200 by default) is paused with a notice.

## Contributing a plan limit kind

A plan is a named list of limits, and each limit is one *kind*: storage space,
generations per day, cloud spend per month and the largest single upload ship with core. A plugin adds kinds of its
own with a `limit_kinds:` section; the plan editor's "Add limit" picker, the users list
columns, the user's usage view and the refusal all render from the kind, so a kind needs
no frontend code.

```yaml
limit_kinds:
  - key: credits.monthly          # must be namespaced: <plugin>.<name>
    label: Credits per month
    short_label: Credits          # optional, for usage rows and columns
    description: Each submit spends one credit
    value_type: count             # bytes | count | usd
    unit: per month               # optional editor suffix
    window: month                 # none (a level) | day | month
    enforce_at: [submit]          # submit and/or upload
    ledger: true                  # count one event per accepted submit
    measure_handler: limits.measure     # optional when ledger: true
    applies_handler: limits.applies     # optional, narrow where the kind is asked
    incoming_handler: limits.incoming   # optional, what one request adds
    refusal_code: credits_exhausted     # optional, default <key>_exceeded
    refusal_message: "You have used {used} of {limit} credits. {contact}"
    admin_only_values: false      # optional; usd kinds hide numbers from users by default
    warn_at: 0.8
```

- **`measure_handler(context: MeasureContext) -> Usage | number`** returns the user's usage
  for `context.user_id` only. `context.window_start` and `context.window_end` bound the
  current day or month (midnight in the instance timezone, an admin setting, UTC by
  default); `context.events` reads and writes this kind's ledger for the user
  (`total()`, `record(units, ref_id)`, `refund(ref_id)`). Keep it fast: it runs on every
  submit or upload the kind applies to.
- **`ledger: true`** makes core record one event per accepted submit (`incoming_handler`
  can change the units) and refund it when the run is cancelled before it started or fails
  with no output. Without a `measure_handler` the usage is the ledger total in the window.
  A ledger kind needs a `day` or `month` window; events are kept 40 days. To count
  something else, call `LimitEvents.record(user_id, kind, units, ref_id)` yourself.
- **`applies_handler(request: AdmissionRequest) -> bool`** sees `point`, `user_id`,
  `incoming_bytes` (uploads; the total of every file in the request), `item_bytes` (uploads; the size
  of each file, or `None` when the caller passed only a total), `engine`, `backend_id` and
  `preset_id` (submits).
- **`incoming_handler(request) -> number | None`**: `None` means unknown, and the kind
  refuses when `used >= limit`; a number refuses when `used + incoming > limit`.
- `refusal_message` may use `{label}`, `{used}`, `{limit}`, `{resets}` and `{contact}`
  (the admin's contact line).

### What the host guarantees

- A refusal is one HTTP 403 `limit_exceeded` carrying the kind, its code, usage, limit
  and reset time. Running work is never stopped and nothing is ever deleted for the user.
- Admins are exempt while "Admins are exempt" is on (the default); their usage is still
  measured.
- Disabling the plugin removes its kinds. Plans keep the limits, shown as inactive and
  never enforced, and they apply again when the plugin is back.
- A kind whose measure raises is skipped for that request (logged), never enforced as zero.

## Contributing a recipe (and recipe step kinds)

A recipe is an ordered list of steps that takes an instance from "nothing installed" to
"this preset generates". The first-run wizard runs one, and so does Admin → Recipes.

### Shipping a recipe

An enabled plugin can ship its own recipe, the same way it can ship its own preset (see
`presets:` below and [Presets](presets.md)). Declare a `recipes:` root in `manifest.yml`;
no Python import or handler is required:

```yaml
recipes:
  - path: "recipes"   # a dir in the plugin, relative to the plugin root
```

The dir is scanned for `*.yml` recipe files exactly like the core `content/recipes/`
tree, and only while the plugin is enabled. A recipe id colliding with a core recipe id
is reported as a load error and the core recipe wins — same precedence a `local` recipe
colliding with a `marketplace` one gets. `content/plugins/marketplace/comfyui-backend/`
ships `comfyui-detect` this way: the recipe is useless without the plugin installed, so
it lives with it, alongside the `comfyui` presets it installs.

A step that only makes sense during the first-run wizard is marked `onboarding_only:
true` and is skipped by a run started from Admin → Recipes. Core's `workspace.activate`
(which closes onboarding out) is the one built-in step that carries the flag.

An `artifacts[]` entry can mark itself `gated: true` with a `license_url:` when the
source requires an accepted licence or access token before it will serve the file (a
gated Hugging Face repo, say). `artifacts.plan` then adds a non-fatal warning to the
run's consent request when that artifact's resolved provider has no credential
configured yet, pointing the owner at the licence and Admin → Plugins.

An `artifacts[]` entry can instead declare `variants:`, the same model slot in several
precisions. The file fields (`filename`, `size_bytes`, `checksum`, `provider_hint`,
`gated`, `license_url`) then move onto each variant, next to `id`, `label` (plain words
such as "Balanced"), `precision` (`bf16`, `fp16`, `fp8`, `int8` or `nvfp4`), `uploader`,
`source_url` and optional `recommended_for` rules (`min_vram_gb` and/or `generations`
from `ampere`, `ada`, `hopper`, `blackwell`, `other`). Exactly one variant is marked
`default: true`; it is used when no GPU is detected. `artifacts.plan` picks the first
variant (in file order, so list the best quality first) whose rules match the detected
GPU and whose precision that GPU can run, and falls back to the smallest one the recipe
recommends for that GPU generation at any VRAM (the smallest one it can run when none
targets the generation). A variant already on disk always wins. The owner can override the pick per
slot when approving the download. `python scripts/hf_variants.py <repo> <glob>...
--default <id>` prints ready-to-paste `variants:` YAML for a Hugging Face repo.

### Contributing a step kind

Every step names a `kind:`, and a plugin can add kinds of its own. Declare them under
`recipe_steps:`:

```yaml
recipe_steps:
  - kind: collections.ensure
    backend: steps:EnsureCollections   # "module.path:ClassName" - a src/plugin_api/recipes.py StepExecutor
```

`backend` is loaded the same way a `field_types[].schema_class` is, and instantiated once
when the plugin enables. A kind that collides with a core kind, or with one another
enabled plugin already owns, fails the enable. Implement it against
`src.plugin_api.recipes`:

```python
from src.plugin_api.recipes import StepContext, StepExecutor, StepResult

class EnsureCollections(StepExecutor):
    def execute(self, context: StepContext) -> StepResult:
        wanted = context.step.params.get("collections", [])
        if not wanted:
            return StepResult.fail(
                "NO_COLLECTIONS",
                "This step needs at least one collection to create.",
                suggested_repair="Add a `collections:` list to the step's params.",
            )
        return StepResult.ok({"created": len(wanted)})
```

`StepContext` carries the run, the parsed recipe, the step being run, and a
`report_progress(current, total, unit)` callback a long-running step should call from its
own poll loop so the UI can render progress before the step finishes. Besides `ok` and
`fail`, a step can return `StepResult.awaiting(consent_request)` to park the run until an
admin explicitly approves — that is how a multi-GB download gets a go-ahead. Failure
messages are read by a non-technical owner mid-setup, so write them as a plain sentence
and put any admin-shaped fix in `suggested_repair`.

Registration makes the kind both runnable and lintable: `scripts/recipe_lint.py` and the
running catalog accept recipes using it, while an unregistered kind stays an unknown-kind
error. Disabling the plugin takes the kind away again.

A step kind may implement three optional methods next to `execute`:

- `validate_params(params, recipe, lint: LintContext) -> list[str]` — param checks. The running
  catalog rejects a recipe whose steps fail them, and `scripts/recipe_lint.py` reports them (rule
  R7). `lint.preset_index` maps preset ids to loaded presets.
- `preview(context) -> dict` — a side-effect-free summary for the dry run
  (`POST /api/recipes/{id}/plan`). Return `{"summary": "...", "consent_request": {...}?}`.
- `grant(context, consent_request, grant: GrantRequest) -> dict` — called when an admin approves a
  step parked on `awaiting(...)`. `grant` carries `selections`, `modes`, `extras` and `target`
  from the approval body; raise `ValueError` with a plain sentence to reject it. The returned dict is
  stored on the approving attempt, where later steps read it through
  `context.step_outputs[step_key]` (or `context.plans(...)` for a `plan` key).

`context.is_cancelled()` turns true when an admin cancels the run while the step is still running;
a long poll loop should check it, stop its own work and return. `report_progress(...)` also takes a
`safe_output=` dict to show richer interim state (the download step uses it for per-file rows).

A `consent_request` passed to `StepResult.awaiting(...)` is rendered by the same consent sheet as
core's own steps. These keys are understood, all optional: `artifacts` (files with `id`,
`display_name`, `size_bytes`, `kind`, `gated`, `license_url`), `total_bytes`, `slots` (per-slot
variant pickers), `modes` (a mode checklist), `manifest`, `disk`, `estimate`, `providers` (missing
credential prompts), `warnings`, `checking` and `target`. See
`src/features/recipes/mode_plans.py` for the full `modes.plan` payload.

### Installing preset modes

Core's `modes.plan` step takes its files from a preset's own model-field `recommendations:`
instead of a hand-written `artifacts:` list. It offers every mode (`modes: "all"`), pre-ticks
`selected`, keeps `locked` modes ticked (the smoke mode), offers optional files as extras
(`extras: offer`, pre-ticked by `preselect_extras`), and parks for consent with the mode
checklist above. Follow it with `artifacts.fetch {plan: <step key>}` (downloads in parallel,
re-checks free disk, writes to a remote worker when that is the target), `preset.defaults
{plan}` (fills empty or broken form-override defaults with the downloaded files) and
`pipeline.render {plan}` (renders every chosen mode). Every native preset with downloadable
recommendations also gets a derived `modes-<preset>` recipe with exactly these steps, and
`python scripts/recipe_new.py <preset>` scaffolds a starter that uses them;
`python scripts/recipe_lint.py --explain <recipe>` prints what each mode would download.

## Contributing a model layout profile

A model layout profile teaches Admin → Models → Folders how one tool arranges its model
folders (see [Models](models.md#layout-profiles) for the file format). An enabled plugin can
ship profiles the same way it ships recipes. Declare a `model_layouts:` root in `manifest.yml`;
no Python import or handler is required:

```yaml
model_layouts:
  - path: "model-layouts"   # a dir in the plugin, relative to the plugin root
```

The dir is scanned for `*.yml` profile files exactly like the core
`content/model-layouts/` tree, and only while the plugin is enabled; enabling or disabling the
plugin reloads the catalog. A file name must equal the profile `id`. An id colliding with a
core profile id is reported as a load error and the core profile wins, the same precedence a
`local` profile colliding with a `marketplace` one gets. Plugins ship YAML only: the config
readers a profile can name are a closed set implemented in core. Check a profile with
`python scripts/model_layout_lint.py`.

## Contributing LLM profile rows

A profile row tells the harness what a model can do (reasoning control, tool-choice limits,
rejected sampling, prices). A provider plugin can ship the rows for its own models so core names
none of them. Declare an `llm_profiles:` root in `manifest.yml`; no Python import is required:

```yaml
llm_profiles:
  - path: "profiles"   # a dir in the plugin, relative to the plugin root
```

The dir is scanned for `*.yml` files in the format described in [LLM Capability
Profiles](llm-profiles.md), only while the plugin is enabled. Plugin rows apply after the
marketplace rows and before the admin's `content/llm-profiles/local/` rows; a row with the same
`id` replaces the earlier one. A bad file or row is skipped and logged with its path.

## Contributing photo filters and filter ops

A **filter** is a recipe of adjustment steps (plus an optional `.cube` LUT) the image editor and the server
apply the same way; [Photo Filters](filters.md) is the reference for the file format, the ops and the
engine. An enabled plugin can ship ready-made filters and add new ops. Both are declared in `manifest.yml`;
disabling the plugin takes them away again.

### Shipping filters

```yaml
filters:
  - path: filters          # <plugin>/filters/<name>/filter.yml
```

The root is scanned exactly like `content/filters/marketplace/`: each `<name>/filter.yml` (plus an optional
`lut.cube`) is one filter, and the directory name must equal the `id:`. Public ids are
`<plugin_id>:<name>`, so a plugin filter never collides with a core, local or user filter. A filter that ships
a `.cube` must declare `license:`. A filter that fails validation is reported under `load_errors` in
`GET /api/filters` and the rest still load. Check yours with `python scripts/filter_lint.py`.

### Adding an op

```yaml
filter_ops:
  - id: retro-tape.scanlines        # must be "<plugin-id>.<op>"; core ops never contain a dot
    label: Scanlines
    kind: spatial                   # colour | spatial
    params:
      - { id: strength, label: Strength, type: int, min: 0, max: 100, default: 30 }
      - { id: gap, label: Gap, type: int, min: 2, max: 12, default: 4 }
    python: ops.py:Scanlines        # optional; absent means editor only
```

The declaration is what the backend and the linter validate a filter's params against, without importing
JavaScript. A param is `type: int` or `float` with a `min`, a `max` and a `default` inside that range.

An op is **colour** only if each output pixel depends on that pixel's RGB alone; then it is folded into the
filter's LUT like a core colour op. Anything that needs position or neighbours is **spatial** and runs after
the LUT. A filter lists its colour steps before its spatial steps.

The browser half is registered through the image-editor host (`window.__potionui.imageEditor`), next to the
declaration above: call `registerAdjustment` with an adjustment whose `id` is the op id. A colour op adds
`kind: 'colour'` and `map(rgb, values)`, a pointwise function over `[r, g, b]` floats in 0..1, so the editor
folds it into the LUT. A spatial op uses its `apply(image, values)`, which must return synchronously; the
editor blends its result by the filter's intensity. An op with no browser half leaves its filters unusable in
the editor. The Python half is optional and is a class named by `python:` (`file.py:ClassName`, a path
inside the plugin directory), imported only while the plugin is enabled:

```python
from src.plugin_api.filters import ColourOp, SpatialOp


class Warmth(ColourOp):
    def map(self, rgb, params):
        return rgb


class Scanlines(SpatialOp):
    def apply(self, image, params, amount):
        return image
```

- `ColourOp.map(rgb, params)`: `rgb` is a float32 array of shape `(N, 3)` in 0..1, vectorised over lattice
  nodes; return the same shape. The result is clamped to 0..1.
- `SpatialOp.apply(image, params, amount)`: `image` is a float32 array of shape `(H, W, 3)` in 0..1 and
  `amount` is the intensity as 0..1. A spatial op **must scale its effect by `amount`**: at 0 it returns the
  image unchanged.
- Without a `python:` entry the filter still works in the editor, `GET /api/filters` reports
  `backend_ok: false` for any filter using it, and server-side consumers refuse it with a message naming
  the op.

`apply_filter`, `compile_lut`, `parse_cube`, `validate_steps`, `Cube` and `CubeError` are exported from the
same module so a plugin's own tests can check its op against the engine.

## Samplers and schedules

The native engine's step algorithms (samplers) and sigma schedules are both registries. A
plugin adds its own with two manifest roots and two plain functions; the entry then shows
up everywhere a core one does — the `sampler` / `schedule` form fields, the pipe config
surface, `GET /api/sampling/catalog`.

### The manifest

```yaml
samplers:
  - key: euler_jitter                    # what a preset selects; must not collide with a core key
    handler: "sampling:sample_euler_jitter"   # "module.path:func" in your plugin
    label: "Euler (jittered)"
    stochastic: true                     # needs the request's seeded generator
    description: "Euler with a small per-step noise kick."
    families: ["*"]                      # or e.g. ["ltx", "wan"] - ModelSpec.family values
    options:
      - name: jitter
        type: float
        default: 0.05
        description: "Fraction of the step size added as fresh noise."
        min_value: 0.0
        max_value: 1.0

schedules:
  - key: even
    handler: "sampling:build_even"
    label: "Even spacing"
    description: "An unshifted 1 -> 0 ramp."
    families: ["*"]
    owns_steps: false                    # true when your list's own length is the step count
    requires_image_seq_len: false        # true when you read ctx.image_seq_len
```

### The handlers

```python
# sampling.py, in your plugin directory
import torch

from src.plugin_api.sampling import SamplingCancelled, run_hooks, sample_euler


def sample_euler_jitter(model_fn, x, sigmas, guidance, cond, uncond, hooks=(),
                        is_cancelled=None, sampler_options=None):
    """The uniform sampler signature. Delegates to core's Euler, then adds a
    small noise kick drawn from the request's own seeded generator."""
    opts = sampler_options or {}
    x = sample_euler(model_fn, x, sigmas, guidance, cond, uncond,
                     hooks=hooks, is_cancelled=is_cancelled, sampler_options=opts)
    jitter = float(opts.get("jitter", 0.05))
    if jitter:
        noise = torch.randn(x.shape, generator=opts.get("generator"),
                            device=x.device, dtype=x.dtype)
        x = x + jitter * float(sigmas[-2]) * noise
    return x


def build_even(ctx):
    """A schedule builder: ctx.steps + 1 descending sigmas, float32 on CPU."""
    return torch.linspace(1.0, 0.0, ctx.steps + 1, dtype=torch.float32)
```

**The sampler contract.** `model_fn(x, sigma, conditioning) -> velocity` arrives already
wrapped (expert routing, fp32 trajectory, step cache). `guidance` is a `GuidanceStrategy`
that combines the cond/uncond predictions. `hooks` are fired once per step through
`run_hooks(hooks, "on_step", ...)` — that is what drives the progress bar and the live
preview, so call it. `is_cancelled()` is polled per step and a True answer must raise
`SamplingCancelled`. `sampler_options` carries your declared options plus two
engine-supplied keys: `generator` (the request's seeded `torch.Generator` — draw every
stochastic sample from it, never from the global RNG, or the same seed stops reproducing
the same image) and `discontinuity_steps` (step indices at which a multistep solver must
clear its history, set when a dual-expert model switches networks mid-schedule). Read
`sample_euler` before writing one; it is the reference implementation.

**The schedule contract.** `build(ctx: ScheduleContext) -> Tensor` returns `ctx.steps + 1`
descending sigmas. `build_sigmas` owns everything around that call — the `denoise < 1`
truncation, the detail-daemon warp, the exact-zero terminal — so a builder only produces
the curve. `ctx` carries `steps`, your `options` dict, and the shift-family inputs
(`shift`, `base_shift`, `max_shift`, `dynamic_shift`, `fixed_mu`, `image_seq_len`). Set
`owns_steps: true` when your own list length dictates the step count (core's `manual`
sigma list is the one example) and you will be handed the raw `steps` with no truncation;
set `requires_image_seq_len: true` and a caller with no packed token count is rejected
before your builder runs.

**Flow-matching conventions** every native target shares: sigmas descend from `1.0` (pure
noise) to `0.0` (clean latent), the model returns a VELOCITY, and the denoised estimate is
`x0 = x - sigma * v`.

### Where the entries surface

Both registrations happen when the plugin enables and are removed when it disables. A key
that collides with a core algorithm, or with one another enabled plugin already owns,
fails the enable with a message naming the incumbent.

`GET /api/sampling/catalog` lists every registered sampler and schedule with its label,
description, families and options — that is what the form fields read. A preset picks
them with the `sampler` and `schedule` field types, whose option lists come from the
registry rather than a hardcoded enum:

```yaml
- name: sampler
  type: sampler
  label: Sampler
  configuration:
    family: ltx            # only entries that declare this family (or "*")
    include: [euler, euler_ancestral, euler_jitter]   # optional, kept in this order
    exclude: [lcm]                                     # optional
```

A `schedule` field takes the same three configuration keys against the schedule registry.
Leaving `family`, `include` and `exclude` off shows everything registered.

## Contributing a model classifier

PotionUI reads the header of every safetensors or GGUF file in a folder that has header scanning
on, and decides the model type from what it finds. A classifier teaches it a new model family:
it looks at the tensor names and shapes and says which family the file belongs to. Core then
works out the type (`checkpoint` when the file also carries a VAE or text encoder, otherwise
`diffusion_model`). Files nothing recognises are listed under "Needs a type" in Admin -> Models.

```yaml
model_classifiers:
  - key: hidream                  # unique across every classifier, core included
    handler: "classifiers.classify_hidream"   # "module.function" in your plugin
    label: "HiDream"
    version: 1                    # bump when the logic changes
    formats: [safetensors, gguf]  # header formats the classifier is offered
    priority: 0                   # optional; higher runs first (core native families run at 100)
```

```python
# classifiers.py, in your plugin directory
from src.plugin_api.models import FamilyMatch, HeaderView


def classify_hidream(view: HeaderView) -> FamilyMatch | None:
    if "double_stream_blocks.0.block.attn1.to_q.weight" not in view.denoiser_keys:
        return None
    return FamilyMatch("hidream")
```

The handler gets a `HeaderView` (parsed data only: `format`, `keys`, `denoiser_keys`, `shape(key)`,
`dtype(key)`, `denoiser_shape(key)`, `metadata`, `file_size`, `filename`; no path and no file handle)
and returns a `FamilyMatch(family, variant=None, model_type=None)` or `None`. The first match
wins, highest priority first. Set `model_type` only when the content rule is wrong for your
family. An exception in a handler is logged once and treated as `None`. `transformer_extractable`
is reserved for core families the native loader is known to handle; leave it at its default.

Enabling the plugin registers its classifiers and changes the registry fingerprint, so files
that no classifier recognised before are evaluated again at the next indexing run. Files already
decided keep their verdict; an admin can retype them. Disabling the plugin removes its
classifiers. `src.plugin_api.models.model_classifier_registry` is a read-only view for listing
and looking up classifiers; there is no register call, plugins register through the manifest.

## Contributing a model type

A model type is a folder of model files PotionUI scans, indexes, shows in Admin -> Models and
offers to model fields. Core registers its own types (`checkpoint`, `lora`, `vae`, ...) through
the same registry a plugin uses. Declare yours under `model_types:`:

```yaml
model_types:
  - id: pose_rig                  # lowercase, digits and underscores; unique across core and plugins
    label: "Pose rig"             # shown wherever the type is named
    purpose: "Skeleton for pose transfer"   # optional one-line description
    folder: pose_rigs             # one folder name under each models root; downloads land here
    aliases: [PoseRigs]           # optional other folder names that mean this type
    extensions: [.onnx, .safetensors]       # optional; defaults to the core model file extensions
    layout: file                  # optional; `directory` treats each subfolder with a config.json as one model
    header_classified: false      # optional; files are typed from their headers, like checkpoints
    assignable: true              # optional; whether an admin may move a model to this type
```

Enabling the plugin registers the type and binds `folder` under the PotionUI models root, so the
next indexing run (a restart, or Rescan in Admin -> Models) catalogues the files in it. A model
field with `model_type: pose_rig` lists them. An `id`, `folder` or alias that is already taken,
by core or another plugin, fails the enable with an error naming both owners.

Disabling the plugin removes the type. Its folder is no longer scanned and its files are no
longer resolvable, but the models already indexed keep their rows: Admin -> Models shows them
under the type id, marked as no longer available, until the plugin is enabled again.

Python code reads the live set through `src.plugin_api.models.model_types()`; a frontend reads
`window.__potionui.modelTypes` (`list()`, `get(id)`, `presentation(id)`), which follows
`GET /api/models/type-definitions`. Both include plugin types while their plugin is enabled.

## Contributing a prompt resource source

A prompt resource source lets a preset's prompt cite rows of a picker field whose items are
bundles of several references (an image and an audio member, say) rather than single media.
A preset mode, or a [preset extension](presets/plugins.md#extending-existing-modes), maps such a field with
`source: <id>` in `prompt_resources`; see [Prompt resources](presets/manifest.md#prompt-resources).
Declare the source in `manifest.yml`:

```yaml
prompt_resource_sources:
  - id: stack                 # the value prompt_resources entries put in `source`
    field_type: lora_picker   # the field type it reads; lint rejects any other
    label: Stack              # names an item in messages and chip tooltips
    badge: Stack              # the @ menu badge while an item's members are unknown
    members: sources.members  # "module.function" in your plugin
```

`members(item_key, caller)` returns the item's members as `[{"kind", "name"}]`, where `kind`
is `image`, `video` or `audio`. `item_key` is the row's `model` value (`model:<id>`), and
`caller` carries `user_id`, `is_admin` and `model_file(model_id)`, which returns the model's
file path. Raise `ValueError` with a short reason when the item cannot be read; any other
exception is reported as unreadable without its text. Core calls it at submit time for every
cited row and from `GET /api/prompt-resources/sources/{id}/members?item=<item key>`, which first
checks that the caller can see the model (404 otherwise, before your handler runs). Two
plugins cannot register the same id, and disabling the plugin removes the source.

## Contributing a prompt importer

Core owns file-based import: `POST /api/prompts/import` accepts `styles.csv`
(A1111/Forge/SD.Next/InvokeAI), Fooocus style JSON, dynamicprompts wildcard YAML, one
prompt per line, and images carrying A1111, ComfyUI, InvokeAI or NovelAI metadata, with
format auto-detection (`src/features/prompt_database/importing/`), and
`GET /api/prompts/export?format=styles-csv` writes the library back out. A plugin importer
is for a source that is not a file — a marketplace or a remote service. It appears in the
Prompt Library's header overflow menu below the core "From file or text" entry. Declare each importer in `manifest.yml`:

```yaml
prompt_importers:
  - id: civitai
    label: CivitAI import
    component: ImportModal.svelte   # your plugin frontend asset, same build as any other
    backend: importers:CivitaiPromptImporter   # "module.path:ClassName" - a src/plugin_api/prompts.py PromptImporter
```

`backend` is loaded the same way a `field_types[].schema_class` is, and instantiated once
when the plugin enables. Implement it against `src.plugin_api.prompts`:

```python
from src.plugin_api.prompts import PromptImporter, PromptImportOutcome, create_prompt_for_user

class CivitaiPromptImporter(PromptImporter):
    async def run(self, payload: dict, user_id: str) -> PromptImportOutcome:
        # `payload` is whatever your `component` modal posted, as-is.
        prompt = await create_prompt_for_user(
            user_id, payload["content"], source_provider="civitai",
        )
        return PromptImportOutcome(imported=1, skipped=0, total=1, items=[prompt])
```

`create_prompt_for_user` delegates to the same path manual and chat-created prompts use,
and always requires `source_provider` — an imported prompt is never filed under the
manual bucket. Your `component` is mounted as a modal with props `{onClose, onImported}`
(call `onImported()` once you're done so the library list refreshes and the modal closes);
it owns its own success/error UI, exactly like any other plugin-hosted component.

To file imported prompts under a catalog model, pass `model_id` to `create_prompt_for_user`
(an unknown id raises and the route answers 400 `invalid_model`). Your modal should collect
it with core's picker rather than a free-text input: mount
`window.__potionui.components.PromptModelField` (`mount(el, {modelId, modelLabel, onChange,
onPickerOpenChange})`, then `update`/`unmount`) — `onChange` receives `{id, label}` or
`null`, and `onPickerOpenChange(true)` tells your modal to leave Escape to the picker.

For a searchable admin/user multi-select (e.g. "who to run this fetch for"), mount
`window.__potionui.components.UserPicker` the same way: `mount(el, {users, value, onChange,
disabled})`, then `update`/`unmount` as `users`/`value`/`disabled` change. `users` is
`{id, username, email?}[]`; `value` is `string[]` (the selected user ids) or the string
`'all'`; `onChange` receives the next `value`. The component owns its own search box,
keyboard navigation, and an "All users" toggle that disables the list — you never render
your own user checkboxes.

For a plugin dialog, mount `window.__potionui.components.PluginModal` instead of rolling
your own overlay — it wraps the same `BaseModal` + `ConfirmFooter` every core modal uses, so
Cancel/Esc/backdrop-click and Confirm/Enter behave identically everywhere. Props: `isOpen`
(default `true`), `title`, `subtitle?`, `size?` (`'sm' | 'md' | 'lg' | 'xl' | 'full'`,
default `'md'`), `confirmLabel` (default `'Confirm'`), `cancelLabel?`, `confirmVariant?`
(`'primary' | 'danger'`, default `'primary'`), `busy?`, `confirmDisabled?`, `summary?`,
`hideCancel?` (omits the Cancel button, e.g. for a "Close" footer on a result screen),
`onConfirm`, `onCancel` (Cancel/Esc/backdrop/close-X all call this; every dismiss path is
blocked while `busy`), and `mountBody(el: HTMLElement) => void | (() => void)` — called once
the body container is in the DOM so you can append your own DOM node into it; a returned
function runs as cleanup when the modal is destroyed. Enter confirms only when
`!busy && !confirmDisabled`, and is ignored inside a `<textarea>`.

```js
const api = window.__potionui.components.PluginModal;
const el = document.createElement('div');
document.body.appendChild(el);
const instance = api.mount(el, {
  title: 'Import from Civitai',
  confirmLabel: 'Import',
  mountBody: (target) => target.appendChild(bodyEl),
  onConfirm: () => runImport(),
  onCancel: () => api.unmount(instance)
});
api.update(instance, { busy: true });
api.unmount(instance);
```

A provider-backed importer (one that calls out to a marketplace provider plugin, e.g. the
`civitai` provider's `fetch_image_prompts`) registers exactly the same way — `run()` is
free to call whatever else it needs, including another plugin's provider, before handing
prompts to `create_prompt_for_user`.

A bulk fetch — many prompts, fanned out to many users, from one provider call — skips
`create_prompt_for_user`'s one-at-a-time loop for `import_prompts_for_user(user_id, entries,
*, source_provider)` instead. Each entry is the `ProviderPromptItem` shape (`prompt`, and
optionally `negative_prompt`, `source_id`, `source_url`, `model_id`, `model_name`,
`base_model`, `cfg_scale`, `steps`, `sampler`, `width`, `height`, `nsfw`, `tags`,
`metadata`); a `negative_prompt` becomes its own row, paired with the positive one under a
fresh `source_group_id`. It dedupes against what `user_id` already has on file under
`source_provider` by `source_id` — call `find_prompt_source_ids(user_id, source_provider,
model_id=None)` yourself if you need that set for a dedupe of your own — and returns
`{"created": <rows written>, "skipped_duplicates": <entries skipped>}`.

To resolve which marketplace id a catalog model is linked to (so you know what to fetch),
read `src.plugin_api.models.get_model_provider_info(model_id, provider=None)` — the
model's `providers` table row (`{"provider", "provider_model_id", "provider_version_id",
"model_name"}`), or `None` when it has no link to that provider.
`src.plugin_api.identity.list_user_ids()` lists every user id in the instance — the "all
users" default for a fetch route that doesn't take an explicit user list. From an async
route handler, `await src.plugin_api.providers.ensure_providers_discovered()` guarantees
the provider registry is discovered and initialized before you call
`registry.get_provider(provider_id)`; `get_provider_registry()` alone only kicks discovery
off in the background and can hand you a registry that isn't ready yet.

Disabling the plugin removes its importer(s) from `GET /api/prompts/importers`
immediately; prompts already imported are unaffected.

## Writing a prompt for a preset

`src.plugin_api.prompts.write_prompt_for_preset(user_id, instruction, *, preset_id=None,
mode=None, form_data=None, media=(), llm_id=None)` asks a language model to write one
prompt that follows `instruction`, in the style of the preset it is for. It is the same
grounding the chat's prompt enhancement uses: the preset's `llm.guide` (or the mode's
guide), its name and description, the model selected in `form_data` with its prompting
guidance, and the user's approved and community prompts for that model. `media` is a list
of `{"value", "media_type"}` entries as `src.plugin_api.generation.resolve_media_ref`
returns them; images are sent as they are and each video as a few frames. Without an
`llm_id` it uses a language model the user can use, the way chat does: the server default
when one is set, otherwise the user's assigned models, and when media is given the first
of those with vision support. It returns `{"text", "model_id", "llm_id"}`.

It raises `PromptWriteRefused` (with a `code` and a message you can show) when the user
has no language model (`no_llm`), the requested model is turned off (`llm_disabled`), media
is given and no usable model has vision (`vision_not_supported`, raised before any file is read),
the user cannot see the preset under the same rule as the presets routes (admins see every
preset, others their directly or group-assigned ones; `preset_not_available`, raised before
any grounding or file is read), a file path is absolute, climbs with `..` or resolves outside
the storage root (`media_outside_storage`), an image is over 5 MB (`media_too_large`), more
than 4 files are given in one call (`too_much_media`), a file cannot be read
(`media_unreadable`, `media_unsupported`), the instruction is blank (`no_instruction`) or the
reply is empty (`empty_reply`). The instruction is cut to 4000 characters, the preset guide
to 3000, and each video gives at most 3 frames. Transport failures of the
language model propagate as they are. `prompt_writer_status(user_id, llm_id=None)`
reports `{"available", "vision", "name", "code", "message"}` for that user without calling
the model, so a plugin can warn before it runs anything.

## Contributing a phrasebook batch tool

The Phrasebook's Find & replace view lets a user select values and run a tool over them.
Core's tools — replace, activate/deactivate, move, delete — are registered on the same
registry a plugin uses, so a plugin tool is a first-class peer: it lists in
`GET /api/phrasebook/batch-ops`, runs through the same endpoint and the same hooks, and
appears in the selection bar's **More** menu. Declare each tool in `manifest.yml`:

```yaml
phrasebook_ops:
  - id: titlecase
    label: Title-case labels
    component: TitlecaseModal.svelte   # optional - omit for a no-UI op
    backend: ops:TitlecaseOperation     # "module.path:ClassName" - a src/plugin_api/phrasebook.py PhrasebookBatchOperation
```

`backend` is loaded like a `prompt_importers[].backend` and instantiated once when the
plugin enables. Implement it against `src.plugin_api.phrasebook`:

```python
from src.plugin_api.phrasebook import (
    PhrasebookBatchOperation, PhrasebookBatchContext, BatchOutcome, BatchPreview, BatchOperationError,
)

class TitlecaseOperation(PhrasebookBatchOperation):
    supports_preview = True

    def _rows(self, ctx: PhrasebookBatchContext, value_ids: list[str]):
        # ctx.values() returns only the caller's values and raises
        # BatchOperationError("unknown_values") for any id that isn't theirs.
        return [(v, v["label"].title()) for v in ctx.values(value_ids)]

    async def preview(self, ctx, value_ids, params) -> BatchPreview:
        items = [
            {"id": v["id"], "field": "label", "before": v["label"], "after": after}
            for v, after in self._rows(ctx, value_ids) if after != v["label"]
        ]
        changed = {item["id"] for item in items}
        return BatchPreview(items=items, changed=len(changed),
                            unchanged=[i for i in value_ids if i not in changed])

    async def run(self, ctx, value_ids, params) -> BatchOutcome:
        rows = [(v["id"], after, v["value"]) for v, after in self._rows(ctx, value_ids) if after != v["label"]]
        ctx.update_value_texts(rows)          # one transaction, all or nothing
        return BatchOutcome(updated=ctx.values([r[0] for r in rows]) if rows else [],
                            skipped=[i for i in value_ids if i not in {r[0] for r in rows}],
                            message=f"Title-cased {len(rows)} labels")
```

`ctx` is the only way a tool touches the phrasebook: `values(ids)`, `category(id)`,
`update_value_texts(rows)`, `set_active(ids, is_active)`, `move(ids, category_id)` and
`delete(ids)` are all scoped to the calling user and each write is one transaction. Raise
`BatchOperationError(code, message, status=400)` to refuse a request; `code` becomes the
API error code. `params` is whatever your modal posted, as-is — validate it yourself
(core's tools raise `invalid_params`).

The endpoints, shared by core and plugin tools:

- `GET /api/phrasebook/batch-ops` → `[{id, label, component, has_preview, source}]`
- `POST /api/phrasebook/values/batch` with `{op, value_ids, params}` → the `BatchOutcome`
  as `{updated, skipped, deleted, message}`; 404 `unknown_op`, 400 `empty_selection`,
  `blocked`, or whatever code the tool raised
- `POST /api/phrasebook/values/batch/preview` with the same body → the `BatchPreview` as
  `{items: [{id, field, before, after}], changed, unchanged}`; 400 `no_preview` for a tool
  without one

Your `component` is mounted in a modal with props `{ valueIds: string[], onClose(),
onDone(outcome?) }`: it owns its parameter UI, posts to the batch endpoint itself, and calls
`onDone(outcome)` when finished — the host toasts `outcome.message`, re-runs the search and
refreshes the tree. A tool declared without a `component` runs straight from the More menu
with empty `params`. A plugin that only wants a button in the selection bar (no registered
operation) can contribute to the `phrasebook.selection.actions` extension slot instead —
a `contributions:` entry with that slot is mounted inside the bar with
`context = { selectedIds: string[] }`.

Three hooks bracket the path (full payloads in `GET /api/plugins/hooks/catalog`):

- `phrasebook.batch.before` — `{op, value_ids, params, user_id}`; a handler may rewrite
  `params` or `value_ids`, or set `blocked` / `block_reason` to veto the run (400 `blocked`).
- `phrasebook.batch.after` — the same plus `outcome`; observe-only.
- `phrasebook.find.results` — the find parameters plus `categories` / `values` (each value
  hit carries its `matches` spans); a handler may annotate, drop or append hits.

Disabling the plugin removes its tools from `GET /api/phrasebook/batch-ops` immediately.

## Media tools

A **media tool** is one declaration of something that edits media: trim a clip, resize an image, loop
a video. Core ships nineteen; a plugin adds its own. The Tools menu on the Library and History pages, a
media field's toolbar, presets, PotionAI chat and automation all read the same catalog, so a plugin tool
shows up in every one of them as soon as the plugin is enabled and disappears when it is disabled.
Tools change shape, time, sound or pixels (trim, crop, resize, rotate, flip, speed, split, frame, concat, fit canvas,
crop to subject, colour key, fix colours, film grain, mute, volume, replace audio, paint layer) and run
deterministically with no model. Looks on
still images (grain, VHS, grades) are photo filters, not tools: see
[Contributing photo filters and filter ops](#contributing-photo-filters-and-filter-ops).

### Core tools

| id | accepts | produces | params |
|---|---|---|---|
| `trim` | video, audio | same | `start`, `end` (seconds) |
| `crop` | image, video | same | `x`, `y`, `width`, `height` (px) |
| `resize` | image, video | same | `width`, `height` (one side keeps the aspect ratio), `quality` high / balanced / small |
| `rotate` | image, video | same | `degrees` 90 / 180 / 270, clockwise |
| `flip` | image, video | same | `axis` horizontal / vertical |
| `speed` | video, audio | same | `speed` 0.25 to 4, `keep_pitch` |
| `fit_canvas` | image | same | `width`, `height`, `scale_percent`, `anchor`, `fill` |
| `color_key` | image | same | `key_mode`, `key_color`, `tolerance`, `softness`, `despill`, `feather` |
| `crop_subject` | image with transparency | same | `margin`, `alpha_threshold` |
| `frame` | video | image | `time` (seconds) |
| `split` | audio, video | same, many | `mode` every / at, `part_seconds`, `at` (seconds) |
| `concat` | video, 2 to 8 selected | video, one | `overlap`, `overlap_frames`, `fit`, `keep_audio` |
| `cut_finder` | video | video, many | `sensitivity` 0 to 100, `min_shot_seconds` |
| `mute` | video with sound | same | none |
| `volume` | video, audio with sound | same | `unit` percent / db, `percent` 0 to 400, `db` -60 to 20, `fade_in`, `fade_out` (seconds) |
| `replace_audio` | video (editor only) | same | `audio` (media), `start` (seconds into the track), `loop` |
| `paint_layer` | image (editor only) | same | `layer` (media, a transparent PNG) |

Core ids never contain a dot. Video output keeps even dimensions and the sound; images keep their
transparency (a JPEG that gains an alpha channel is saved as PNG).

`mute`, `volume` and `replace_audio` copy the video stream when they run alone; next to a picture or timing edit they
share that edit's single encode. `replace_audio` keeps the video's length: the track starts `start` seconds in and, with
`loop`, repeats until the picture ends. `split` with `mode: at` cuts at one time point into two items; video parts are
re-encoded so the cut lands on the exact frame. Tools whose `scopes` are only `editor` (`replace_audio`, `paint_layer`)
are left out of the Library and History menus because they need a file picked in the editor.

A `media` parameter names another file: `{"item_id": "..."}` (a Library item or a helper upload) or
`{"generation_file_id": "..."}`; a bare string is read as an `item_id`. The run checks that the caller owns it and that
its kind is one of the parameter's `accepts`, then hands the tool a staged `ToolMedia` in its place.

The Effects menu (`group: effects`) lists **effects**: tools defined by an `effect.yml` folder, with a live preview
shader and ffmpeg, torch or numpy renders. Film grain (`film_grain`) and Fix colours (`fix_colors`) are the core ones.
Their contract, the catalog `effect` block and the shader and analysis endpoints are in [Video Effects](effects.md).

### The editor API

The Image and Video editor tabs keep a stack of steps, `{tool, params, hidden?}`, and talk to these endpoints. Every
one needs a signed-in user, answers 404 for a source, helper file, run or preview owned by someone else, and never puts
a file path in a response.

| Method and path | Body | Answer |
|---|---|---|
| `POST /api/media-tools/runs` | `{steps, inputs: [ref], output: {mode, target_item_id?}}` | 202 `{run_id}` |
| `GET /api/media-tools/runs` | | `{runs: [run]}`, my runs still held (queued, running, or finished in the last 10 minutes) |
| `GET /api/media-tools/runs/{id}` | | `run` |
| `DELETE /api/media-tools/runs/{id}` | | `run`, cancelled |
| `POST /api/media-tools/preview/frame` | `{input: ref, steps, time_seconds?, max_side? 1280, format? webp / png}` | the image; header `X-Preview-Cache: hit / miss` |
| `POST /api/media-tools/preview/clip` | `{input: ref, steps, start_seconds? 0, duration_seconds? 3, height? 480}` | 202 `{run_id, preview}` |
| `GET /api/media-tools/previews/{id}` | | the preview clip (cookie or bearer, so a `<video>` can play it) |
| `POST /api/media-tools/artifacts` | multipart `file` | `{id, media_type, mime_type, url, width, height, duration_seconds, fps, size}` |
| `GET /api/media-tools/effects/{tool_id}/shader` | | the effect's preview shader as `text/plain`, with an `ETag` |
| `POST /api/media-tools/effects/analysis` | `{input: ref, steps, step_index}` | `{values, cached}`, an effect's measured values |

- A `run` is `{run_id, kind: run / preview, status, progress 0..1, label, items, replaced, preview, error, summary}`. Poll it by
  id for progress; a reload reattaches through the stored id or the run list.
- **Saving a stack** runs it as one background run. Hidden steps are skipped and never validated; up to 64 visible steps
  run in order. `mode: new` makes Library items, `mode: update` with `target_item_id` replaces the file of the copy a
  first save made (one input, no split, same kind), and `mode: replace` replaces a Library source; a History source or a
  helper upload is refused with `replace_not_allowed`. A stack with `split` saves its parts as separate items named
  "part 1 of 2".
- **Frame preview** renders one frame of a video, or the image, through the stack with each tool's frame path, the same
  code the full render uses: `film_grain` seeds each frame's grain by a hash of its frame number, so the paused frame shows
  the grain the render will put there, and `fix_colors` measures the trimmed range of the source. `time_seconds` is on
  the source clip; trim and speed steps map it to the frame the render would show. Sound and timing steps leave the
  picture alone. A tool with no frame path (`frame`, `concat`, most plugin tools) is refused with its `step_index`.
  Answers are cached per user, source, time, stack and size.
- **Clip preview** cuts `duration_seconds` from `start_seconds` (source time, clamped inside the trim), runs the stack on
  that piece and scales it to `height` as an MP4. A split step is left out. When the same request is cached the answer
  is `{run_id: null, preview}` at once; otherwise poll the run and read `preview` when it completes, or cancel it.
  Fades and the replacement track's offset count from the start of the piece, not of the whole clip.
- **Helper uploads** (`derived_artifact`) hold what an editor makes or receives: a paint layer, a mask, a dropped file,
  a replacement track. Use the returned `id` as a run input or in a `media` parameter. They stay out of the Library but
  count toward the plan's storage and per-file limits. They must be an image, audio or video file of at most 512 MB.
  The announced size is checked before the body is read and the bytes are checked again after. The same rules apply
  to `POST /api/media/upload` with `purpose=derived_artifact`.

### From chat, MCP and automation

PotionAI's `run_media_tool` chat tool, the same tool over MCP and the `action.media_tool` automation node run
the catalog tools that have a Python implementation (`python:`). Component-only tools have no run API and are
not offered. The chat tool builds its description and `tool` enum from the live catalog each turn, so a
plugin's tools appear in the model's schema while the plugin is enabled, with each parameter written as
`name type[min..max]unit =default`. Keep parameter names short and give a `min`, `max` and `default` where
they make sense, because the model reads them verbatim. Every run goes through the same service as the API:
the same ownership checks, parameter validation and Library quota. `mode: replace` is the one call that waits
for the user's approval in chat. See [Chat Modes](chat-modes.md#tool-approval) and
[Automation Nodes](automation-nodes.md#media-tool).

### From presets

A preset step runs any registered tool, a plugin's included, through the `media_tool` pipe. `tool` is the catalog id,
`params` are the tool's parameters (the same names and the same validation as the API), and `skip_unfit: true` passes
media the tool cannot work on, such as an image without transparency for `crop_subject`, through unchanged instead of
failing.

```yaml
- name: "media_tool"
  id: "crop_subject"
  input:
    - ["image", "matting", "image"]
  configuration:
    display_title: "Cropping subject"
    tool: "crop_subject"
    skip_unfit: true
    params:
      margin: "{{ form.crop_margin }}"
```

Images, videos and audio files go in and come out as arrays on `image`, `video` and `audio`; a tool that makes a
different kind (`frame` turns a video into an image) fills the output of that kind. A tool that joins (`concat`) takes
every input of one kind and makes one output. Image tools run in memory without writing the picture to disk. The pipe
reads the plugin catalog from the `MEDIA_TOOLS` service the generation engine injects, so a plugin's tool runs while the
plugin is enabled; a Remote Native worker has no such service and runs the core tools only. See the
[Pipes Reference](pipes.md#pipe-media-tool).

### Declaring a tool

A plugin declares tools in `manifest.yml` under `media_tools`. Disabling the plugin removes them from
every surface.

```yaml
media_tools:
  - id: boomerang.loop              # must be "<plugin-id>.<tool>"
    label: Boomerang
    icon: repeat                    # optional, an Icon name; default "extension"
    description: Play the clip forward, then backward   # optional
    accepts: [video]                # image | video | audio, at least one
    produces: same                  # same | image | video | audio
    cardinality: one_to_one         # one_to_one | one_to_many | many_to_one
    params:
      - { name: style, type: select, options: [forward, pingpong], default: pingpong }
      - { name: repeats, type: integer, min: 1, max: 8, default: 2 }
    requires: { has_audio: false }  # optional: has_audio, has_alpha, min_duration (seconds)
    selection: { min: 1, max: 4 }   # optional: how many items the tool takes
    scopes: [library, history]      # optional: library | history | field; default all three
    preview: false                  # true needs python; the dialog then shows a before/after pane
    python: tools/loop.py:LoopTool  # optional: a path inside the plugin dir, then :ClassName
    component: loop-preview         # optional: a plugin frontend asset
```

At least one of `python` and `component` is required. The id must start with your plugin id and a dot,
and a plugin cannot take an id another plugin or core already registered; an entry that breaks either
rule keeps the plugin from enabling.

`params` are form fields. `type` is `integer`, `float`, `select`, `checkbox`, `color`, `string` or
`anchor`, with these keys: `name`, `label`, `default`, `min`, `max`, `step`, `unit`, `options` (a list of
values or of `{label, value}`), `allow_transparent` (a `color` that may also be `transparent`), `when`
and `description`. `when` uses the preset form's `when:` grammar (`{ field: key_mode, equals: color }`),
and a parameter whose condition is false is dropped from the validated params. A parameter without a
`default` is required; `default: null` makes it optional. The dialog builds its controls from this
list: a select with up to four options becomes a segmented control, a number with `min` and `max` a
slider, a checkbox a switch.

### The Python half

```python
from src.plugin_api.media_tools import FilterFragment, InvalidToolParams, MediaTool, ToolContext, ToolMedia


class LoopTool(MediaTool):
    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        source = inputs[0]
        out = ctx.new_path(".mp4")
        ctx.ffmpeg(["-i", str(source.path), "-filter_complex", "...", str(out)], duration=source.duration)
        return [ctx.probe(out)]
```

`run(inputs, params, ctx)` receives the already validated `params` and returns the produced files, which
must live under `ctx.scratch_dir`: the host publishes them to the Library and deletes the scratch
directory afterwards, whatever happens.

- **`ToolMedia`** is a frozen dataclass: `path`, `kind` (`image` | `video` | `audio`), `width`, `height`,
  `duration`, `fps`, `has_audio`, `has_alpha`. Everything except `path` and `kind` may be `None`.
- **`ToolContext`** gives you `scratch_dir`, `user_id` (the user the run belongs to, `None` outside a
  Library or History run), `new_path(suffix)` for a fresh output path,
  `progress(fraction, label="")`, `is_cancelled()`, `probe(path)` and `ffmpeg(args, duration=None)`.
  `ffmpeg` is the one cancellable runner: it adds `-y`, parses `-progress` into `ctx.progress` when you
  pass the expected output `duration`, kills the process when the run is cancelled and raises on a failure
  without ever exposing stderr or server paths. Use it instead of `subprocess` so cancel works.
- **`ctx.summarize(message, links=())`** lets the last step of a run finish with no media: return `[]` after
  calling it and the run completes with nothing added to the Library, showing `message` (and the first
  `(label, href)` link: a path inside the app such as `/models`, with no backslash, whitespace or control
  character) in place of the Library notice. The run's
  `summary` is `{message, links: [{label, href}]}`.
- **`ToolFailed(message)`** ends the run as failed with `message` shown to the user, and **`ToolCancelled()`**
  ends it as cancelled; raise it when `ctx.is_cancelled()` turns true during a long loop.
- **`check_params(params)`** is an optional override for rules that span parameters (`end` must be greater
  than `start`); raise `InvalidToolParams(field, message)`.
- **`validate_params(spec, params)`** is what the API, presets and chat all call before running; it applies
  defaults, coerces, checks ranges and options, and raises `InvalidToolParams`.

#### Chains and the fast paths

A request is a list of steps, and a single tool is a chain of one. The runner fuses neighbouring steps
that offer a fast path so the media is decoded and encoded once:

```python
class Mirror(MediaTool):
    def video_filter(self, media: ToolMedia, params: dict) -> FilterFragment | None:
        return FilterFragment(vf=["hflip"])

    def image(self, image, params):
        return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
```

- `video_filter(media, params)` returns a `FilterFragment`: `vf` and `af` are lists of ffmpeg filters,
  `start` and `end` a time window, `drop_audio` removes the sound. A fragment that changes the picture size,
  the length or the quality reports it in the optional `width`, `height`, `duration` and `crf` so the next
  step is validated against the right media. Consecutive fragments merge into one ffmpeg pass; a leading
  `start`/`end` becomes an input seek. It is also asked about audio files, where only `af` and the window
  apply. Return `None` to say "no fast path for this input".
- `image(image, params)` receives and returns a PIL image. Consecutive image steps run in memory and the
  result is saved once, with the source's EXIF orientation already applied.
- A step with neither fast path runs `run()` on the previous step's output file.

### Component-only tools

An entry with a `component` and no `python` is a tool the browser runs itself, the case the old
`history_tools` key covered. It gets no run endpoint: the catalog lists it so the Tools menu can offer it
for the selected media, and the host mounts the component in a modal.

```yaml
media_tools:
  - id: my-plugin.contact-sheet
    label: Contact sheet
    icon: grid
    accepts: [image]
    selection: { min: 2, max: 12 }
    scopes: [history]
    component: ContactSheetModal.svelte
```

The catalog serves its `component` as `plugin:<plugin-id>:<asset>`, the same reference string a
`phrasebook_ops[].component` resolves to. The component is mounted with the props `{ items,
generationIds, generations, onClose(), onDone() }`: `items` is the selection as
`{ id, kind, url, filename, width, height }`, `generationIds` and `generations` are filled on the History
page, and `onDone()` clears the selection once the tool finished. If it needs the backend it calls its own
plugin API route.

### Shipping effects

An effect is a media tool defined by one folder (see [Video effects](effects.md)). Declare one or more roots and put
`<name>/effect.yml` folders in them:

```yaml
effects:
  - path: effects
```

- Each effect registers as `<plugin-id>.<id>` under the plugin's source, in the Video editor's Effects panel, the
  Library Effects menu, presets, chat, MCP and automation. Nothing in the UI names the plugin.
- Python renders and analyses (`render.py:fn`) may import only `numpy`, `torch` and `src.plugin_api.*`.
- A path outside the plugin folder, a missing folder or any broken `effect.yml` fails the enable with the error, and
  nothing of the plugin registers. Disabling the plugin removes its effects; the open editor's catalog refreshes.
- `python scripts/effect_parity.py <plugin folder> --golden` writes the parity goldens to
  `<plugin>/tests/effects/golden/`, and the same command without `--golden` checks every render against them.
- `tests/e2e/effect_plugins/e2e-effects/` is a complete example (an Invert effect with a shader and a numpy render).

### The API

Successful JSON responses use the usual `{success, data}` envelope. A refusal answers with `detail: {success: false, error, message, ...}`; the codes are listed per route.

- `GET /api/media-tools?kind=&scope=` returns `{tools: [...]}`: every tool for the kind and scope, with the
  declaration (`id`, `label`, `icon`, `accepts`, `produces`, `cardinality`, `params`, `requires`,
  `selection`, `scopes`, `editor`, `preview`, `component`, `source`) plus `runnable` (`true` when the tool has
  a Python implementation, `false` for a component-only tool that has no run API) and `last_params`, the
  values this user ran the tool with last (or `null`). `source` is `core` or the plugin id. A param's `max`
  is enforced before a run starts, so a value above it is a 400.
- `POST /api/media-tools/runs` takes `{steps: [{tool, params}], inputs: [{item_id} | {generation_file_id}],
  output: {mode: "new" | "replace"}}` and answers 202 `{run_id}`. A generation file is read straight from
  storage and the result is always a new Library item; `replace` keeps the row, its tags and its
  collections, and is only allowed for one Library item, tools that return one file per input, and a result
  of the same kind. A run takes 1 to 8 steps and 1 to 50 inputs. A 400 before anything runs carries one of
  `unknown_tool`, `unsupported_media`, `invalid_params` (with `field` and `step_index`), `invalid_selection`
  or `replace_not_allowed`; an item that is missing or not the caller's answers 404 `not_found`. A user may
  have 8 runs waiting at once (429 `too_many_queued`) and nothing starts while the server shuts down (503
  `shutting_down`). The same upload quota the editor used applies (403 `limit_exceeded`), and a refusal
  leaves no file behind.
- Results look like normal uploads: each carries its content hash, images get their thumbnails before the run
  completes, and a video's thumbnails are rendered right after (the Library card fills in a moment later). A
  run item lists `thumbnail_small`, `thumbnail_medium` and `thumbnail_large` URLs for the sizes that exist.
  `replace` swaps the thumbnails together with the file and deletes the old ones. If another run replaced the
  same item first, the later run fails with `conflict` and publishes nothing. A run that would create more
  than 500 files fails with `too_many_outputs` before anything is published.
- `GET /api/media-tools/runs/{run_id}` returns `{run_id, status, progress, label, items, replaced, error, summary}`.
  `status` is `queued`, `running`, `completed`, `failed` or `cancelled`; `items` are the resulting Library
  items; a failed run carries `error: {code, message}` (a tool's `requires` check ends the run with
  `unsupported_media`). An unknown run id, or another user's, is a 404.
- `DELETE /api/media-tools/runs/{run_id}` cancels: the encoder is killed and nothing is published.
- `POST /api/media-tools/preview` takes `{tool, params, input, time_seconds?}` for a tool with
  `preview: true` and returns `image/webp` with a long side of at most 512. Pixel params (`unit: px`) are
  scaled down together when the largest exceeds 512, so a huge canvas renders as a small one. For a video,
  `time_seconds` must be finite, not negative and not past the end, otherwise 400 `invalid_params` on
  `time_seconds`. Only two previews render at once; one that cannot get a slot within a few seconds answers
  429 `preview_busy`.

Runs are in-process: at most two execute at once, the rest wait their turn, and a finished run is kept for
ten minutes. A user only ever sees, polls and cancels their own runs. After a single-step run completes, its
parameters are stored as that user's `last_params` for the tool.

## Reading model files and folders

A model's bytes can sit on any admin-configured root, not just the `models_dir` setting, so a
plugin that needs a path never joins one by hand — it goes through `src.plugin_api.models`:

```python
from src.plugin_api.models import (
    model_type_dirs, model_write_dir, resolve_model_file, model_for_path,
    model_types, directory_aliases, type_for_folder_name,
)

model_type_dirs("lora")          # -> every online root's bound directory for this type, in order
model_write_dir("lora")          # -> the directory a new download for this type lands in
resolve_model_file(model_id)     # -> the winning present copy's absolute Path, or raises
model_for_path(path)             # -> that model's catalog dict (or None), from a filesystem path
index_model_file(path, user_id=None)  # -> {model_id, indexed, duplicate_of, warning}; catalogues a file the plugin wrote
```

`resolve_model_file` raises `src.features.models.locator.ModelFileUnavailable` (not importable
from `plugin_api` — catch it by class name, or by string match on `reason`/`root_label`) when the
model has no present copy on any online root; the error names the root when the file is known to
exist but its root is offline. `model_type_dirs`/`model_write_dir` can raise
`src.platform.filesystem.model_roots.ModelRootError` subclasses when no root is bound for that
type, is read-only, or is offline — same caveat.

`index_model_file` takes only an existing file inside a folder some model type writes to (a `ValueError` otherwise),
and with `user_id` assigns the new model, or the catalog model it duplicates, to that user.

`directory_aliases()` and `type_for_folder_name(name)` are for a plugin that speaks another
tool's folder vocabulary (a ComfyUI-style `loras`/`checkpoints`/… folder name) and needs the
PotionUI model type it corresponds to, case-insensitively and including aliases.

## Contributing a model card action

Admin -> Models cards can carry a plugin-contributed icon button, alongside the built-in
assign/remove actions. Declare it under `contributions:` in `manifest.yml`:

```yaml
contributions:
  - slot: "admin.models.card.actions"
    component: "MyModelAction.svelte"
    order: 100
```

It's mounted via `<PluginSlot hookName="admin.models.card.actions" context={{ model }}>` in
`ModelCard.svelte`'s management-actions row, so your component receives the card's `model`
object (as returned by `GET /api/models`) through its `context` prop - full payload in
`GET /api/plugins/hooks/catalog`. The slot only renders in the admin management-actions
context (`showManagementActions`), never on the Library/Generate model pickers.

Give the contribution a `label`: the host wraps your component in the app's tooltip with that
text, so your button must not set a native `title`.

`context.refresh()` reloads the list in place after your action changed the model, so the
card re-renders with the new previews, tags or description; it's awaitable, so `await
context.refresh()` resolves once the reload has landed. For provider metadata,
`POST /api/models/info/fetch` with `wait: true` and explicit `model_ids` runs the fetch before
responding and returns `{successful, failed}`; without `wait` it runs in the background.

`context.track(promiseOrFn)` drives the card's "working" state - a moving border around the
card - while your action is in flight. Pass it a function or a promise; it increments the
card's busy counter, awaits the result, decrements the counter in a `finally`, and returns
whatever you passed in resolved. Wrap the network call and the awaited `context.refresh()`
together so the border keeps animating until the refreshed data has actually landed:

```js
await context.track(async () => {
  await fetch(/* ... */);
  await context.refresh();
});
```

## Contributing a Generation Details action

The Generation Details modal's header (`GenerationDetailsModal.svelte`) can carry a
plugin-contributed icon button, alongside the generation id/status/navigation controls.
Declare it under `contributions:` in `manifest.yml`:

```yaml
contributions:
  - slot: "generation.detail.actions"
    component: "MyDetailAction.svelte"
    label: "My action"
    order: 100
    require_role: "ADMIN"   # optional
```

It's mounted via `<PluginSlot hookName="generation.detail.actions" tooltips context={{...}}>`,
so your component receives `{generationId, presetId, mode, fileIndex, filename, fileUrl,
fileType}` for the currently displayed file through its `context` prop - full payload in
`GET /api/plugins/hooks/catalog`. Give the contribution a `label`: the host wraps your
component in the app's tooltip with that text, so your button must not set a native `title`.

## Contributing an Admin tab with its own left menu

An `admin.tabs` contribution adds a tab to the admin top navigation. By default the host wraps the
component in the page header (title only), which suits a single view. A tab that has several tools
can set `frame: "bare"` so the host mounts the component with the full tab area and draws nothing
around it; the component then renders its own page with a left menu by mounting the host's
`LibraryShell` (see "A list page like History and Models"):

```yaml
contributions:
  - slot: "admin.tabs"
    component: "MyAdminPage.svelte"
    label: "My tools"
    frame: "bare"      # "shell" (default) or "bare"
```

Keep the selected tool in the URL by editing the query string with `history.replaceState`, so the
admin page's own `tab` parameter stays untouched.

## Contributing modes to an existing preset

An enabled plugin can add one or more generation MODES to a preset it doesn't own — e.g. an
`img2img` mode for a preset that only shipped `txt2img` — without forking or editing that
preset. This is distinct from `presets:` (a plugin shipping a whole new preset of its own; see
[Presets](presets.md)). Declare it in `manifest.yml`:

```yaml
preset_modes:
  - target: "01KX46YCC5RB5EGYY38SBMVKR5"   # the target preset's id
    modes_root: "contributed"              # a dir in the plugin, relative to the plugin root
```

`modes_root` is laid out like a preset root minus `preset.yml`: a `modes/<name>/` subtree, each
with the same `pipeline.yml`/`form.yml`(`/variants`) shape a core mode has. Every mode dir found
there is contributed to `target` — there's no separate per-mode enable list. A contributed mode
is schema-validated through the exact same loader path a core mode is; a target that isn't
installed, or a disabled owning plugin, means the contribution is simply absent, not an error.

A contributed mode declares its own prompting in its default `form.yml`, since it cannot edit the
target's `vars:` and `llm:`: `prompt: required | optional | none` (adds the mode to the target's
`optional_prompt_modes` or `promptless_modes`), `prompt_label:` / `prompt_placeholder:` (what the prompt box is for, and an example), `llm_guide:` (the mode's `llm.modes.<name>.guide`) and
`segment_templates:` (the mode's `vars.prompt.modes.<name>.segment_templates`). They are merged into the
target preset when the contribution is accepted; see [Plugin-Contributed Presets](presets/plugins.md).

Name collisions are resolved deterministically, never silently: a core mode of the target preset
always wins over a contribution; between two contributions for the same target+mode name, the
first one (contributions sorted by plugin id, then declaration order) wins and the rest are
rejected with a load error attributed to the plugin. A contributed mode inherits the target's
`speed_profiles:` and admin `configuration:` (its fields' `@config:<key>` filter_tags resolve
against the target, same as a core mode's) — there is no separate per-contribution mechanism for
either. See [Plugin-Contributed Presets](presets/plugins.md) for the full contract, including how
provenance (`source_plugin`) surfaces in `GET /api/presets/{id}/modes` and the mode picker.

## Extending existing preset modes

A plugin can attach fields, pipes and prompt resources to modes a preset already has, instead of
contributing a whole mode:

```yaml
preset_extensions:
  - target: "4TK1KBQZ2XMB8ME0PTMXS1YJQP"   # the target preset's id
    modes: ["txt2img"]                     # core or plugin-contributed modes of that preset
    file: "extensions/krea2.yml"           # relative to the plugin root
```

The extension file holds four optional lists: `fields` (placed `into` a tab or section by its
`anchor:`, optionally `after`/`before` a named field), `pipes` (`add` a pipe node with its own
`id`, `after`/`before` a pipe, or `patch` a pipe with `input_add`/`configuration_add`),
`prompt_resources` (appended per mode) and `director_reference_fields` (appended to the mode's
Director `reference_fields`). Everything is add-only: a field name, anchor, pipe input or
configuration key that already exists is a conflict, and any conflict or missing anchor rejects
the whole extension with a load error naming the plugin. Extensions apply after `preset_modes:`,
by plugin id then declaration order, only while the plugin is enabled. Added fields reach the
frontend with `source_plugin` set. See
[Plugin-Contributed Presets](presets/plugins.md#extending-existing-modes) for the full contract.

## Contributing a compute provisioner

A compute provisioner teaches core how to rent a GPU on one hosting provider
(RunPod, Vast.ai, ...) and run the Remote Native worker on it. Core owns the
`native.remote` backend row, the `provisioned_compute` row, the heartbeat
that keeps that row honest, and the admin UI; the plugin owns nothing but its
provider. Register a subclass of `ComputeProvisioner` by class through the
`compute.register` hook:

```python
# manifest.yml
hooks:
  backend:
    - hook: "compute.register"
      handler: "backend.hooks.compute_hooks.register"

# backend/hooks/compute_hooks.py
def register(context):
    context.data["provisioners"]["vastai"] = VastAiComputeProvisioner
    return context
```

```python
from src.plugin_api.compute import (
    ComputeProvisioner, ComputeProvisionerError, ComputeStatus,
    ProvisionProgress, ProvisionRequest, ProvisionResult,
    STAGE_CREATING, STAGE_PREPARING, STAGE_READY, STAGE_STARTING, STAGE_WAITING_WORKER,
    STATE_MISSING, STATE_RUNNING, STATE_STOPPED, STATE_UNREACHABLE,
)

class VastAiComputeProvisioner(ComputeProvisioner):
    provider_id = "vastai"
    label = "Vast.ai"

    async def describe_fields(self, values=None): ...

    async def provision(self, request: ProvisionRequest, report) -> ProvisionResult:
        await report(ProvisionProgress(STAGE_PREPARING, "Picking an offer"))
        offer = await self._pick_offer(request.values)
        await report(ProvisionProgress(STAGE_CREATING, f"Renting instance on offer {offer.id}", 30))
        instance = await self._client.create_instance(offer)
        while not instance.running:
            await report(ProvisionProgress(STAGE_STARTING, f"Waiting for {instance.id} ({elapsed}s)", 50))
            ...
        for attempt in range(1, 61):
            await report(ProvisionProgress(STAGE_WAITING_WORKER, f"Waiting for the worker (attempt {attempt}/60)", 70))
            if await self._handshake(instance):
                await report(ProvisionProgress(STAGE_READY, "Worker is up", 100))
                return ProvisionResult(handle=request.profile_name, base_url=..., worker_token=..., ready=True,
                                       resource_ref=instance.id)
            ...
        return ProvisionResult(..., ready=False)

    async def status(self, handle: str) -> ComputeStatus:
        instance = await self._client.get_instance(self._lookup(handle))
        if instance is None:
            return ComputeStatus(STATE_MISSING, "Instance no longer exists on Vast.ai")
        if instance.state == "stopped":
            return ComputeStatus(STATE_STOPPED, f"Instance {instance.id} is stopped")
        if not await self._handshake(instance):
            return ComputeStatus(STATE_UNREACHABLE, f"Instance {instance.id} running but the worker did not answer")
        return ComputeStatus(STATE_RUNNING, f"Instance {instance.id} running, worker answered")

    async def start(self, handle: str, report) -> ProvisionResult:
        instance = await self._client.get_instance(self._lookup(handle))
        if not instance.running:
            await report(ProvisionProgress(STAGE_STARTING, f"Resuming {instance.id}", 30))
            await self._client.start_instance(instance.id)
            while not instance.running:
                await report(ProvisionProgress(STAGE_STARTING, f"Waiting for {instance.id} ({elapsed}s)", 50))
                ...
        for attempt in range(1, 61):
            await report(ProvisionProgress(STAGE_WAITING_WORKER, f"Waiting for the worker (attempt {attempt}/60)", 70))
            if await self._handshake(instance):
                await report(ProvisionProgress(STAGE_READY, "Worker is up", 100))
                # base_url re-read: Vast.ai may map a different port after a restart
                return ProvisionResult(handle=handle, base_url=..., worker_token=..., ready=True,
                                       resource_ref=instance.id)
            ...
        return ProvisionResult(..., ready=False)

    async def stop(self, handle: str) -> None: ...
    async def terminate(self, handle: str) -> None: ...
```

### The lifecycle core drives

`POST /api/admin/provisioning` validates the form against `describe_fields()`,
creates the row as `provisioning`, links it to the target backend and returns
at once. The bring-up runs in a background task:

1. `provision(request, report)` is called. Every `await report(...)` appends a
   `{stage, message, percent, at}` entry to the row's `progress` timeline
   (newest 50 kept), mirrors the message into `status_detail`, and broadcasts
   the whole row as a `compute_status` message on `/ws/admin`. `report` never
   raises into the plugin — call it at every phase you can observe, including
   each poll while waiting on the provider; that is the only feedback the
   operator gets while the pod comes up.
2. On return, core writes `base_url`/`worker_token` onto the backend, enables
   it, records `handle`/`resource_ref`, and sets the row to `running`
   (`ready=True`) or `unreachable` (`ready=False`).
3. On `ComputeProvisionerError` (or any exception) the row becomes `failed`
   with the message as `status_detail`; the backend stays disabled and
   unconfigured. A `failed` row can be terminated (cleanup) or provisioned
   again — a new `POST` for the same backend replaces it.
4. Terminating a row still `provisioning` cancels the task. Your `provision()`
   sees `asyncio.CancelledError` at whatever `await` it is on: tear down what
   you already created (core has no `handle` yet, so it cannot) and re-raise.

Starting again works the same way. `POST /api/admin/provisioning/{row_id}/start`
takes a `stopped`, `unreachable` or `unknown` row (anything else is a 409),
resets its timeline, sets it to `starting` and runs `start(handle, report)` in
the background. Report `starting` while the provider brings the resource back,
then `waiting_worker`/`ready` exactly as in `provision()`. On return core writes
the result's `base_url`/`worker_token` onto the backend again — return them
fresh, since a provider may hand a restarted resource a different port — and
**enables** the backend (an explicit operator action, unlike the heartbeat's
rule below). `start()` must be idempotent for a resource that is already
running: skip the provider's start call and go straight to the worker
handshake, never raise. Failure lands the row on `failed` with the backend
left disabled; the heartbeat then moves it on to whatever the provider
reports.

Stages are free strings; the admin UI has labels for `preparing`, `creating`,
`starting`, `waiting_worker` and `ready`, and humanizes anything else.

### The heartbeat

Once a row is out of `provisioning`/`starting`, the status monitor calls `status(handle)`
every `provisioning.status_interval_seconds` (settings table, default 15,
minimum 5) with a per-call timeout, and writes `status`, `status_detail` and
`status_checked_at`. Return one of:

| state | meaning |
|---|---|
| `running` | the provider says up AND the worker handshake answers |
| `stopped` | paused/exited, by the operator or by the provider |
| `missing` | the provider no longer knows the handle |
| `unreachable` | the provider says running but the worker handshake fails |
| `failed` | the resource is in an error state the provider reports as such |
| `unknown` | you could not ask (raise `ComputeProvisionerError`; core stores the message as detail) |

`detail` is shown verbatim to the admin — make it the provider's own reason
("Pod abc123 is EXITED (stopped)"). `provisioning` and `starting` are never
returned by a provisioner: core sets them while one of its own background
jobs owns the row, and the heartbeat skips such rows.

On a change core broadcasts the row; when the new state is `stopped`,
`missing` or `failed` it also disables the linked backend so a paused pod
stops being selected for generations. It never re-enables on a return to
`running` — that is operator intent, and the UI offers an "Enable backend"
action instead. `GET /api/admin/provisioning/{row_id}` is the same reconcile
on demand.

## Reading presets

`src.plugin_api.presets` describes the presets of this instance, so a plugin can offer a user a
choice of presets and fill a request for the one they pick.

| Function | Does |
|---|---|
| `list_presets(user)` | A `PresetDescription` for each installed preset `user` may run, in the order `GET /api/presets` lists presets: the presets assigned to them directly or through one of their groups, the rule `submit_generation` checks, and every installed preset for an administrator. An installed preset whose files are not loaded is left out. Without a user it raises `PermissionError` |
| `describe_preset(preset_id)` | The `PresetDescription` of a loaded preset, installed or not, or `None` when no preset with that id is loaded: its `id` and `name`, a copy of its `vars`, its `modes` (each with its `default_form` and every form's fields as `type`, `required` and `multi`), its `engine` and `category`, and `requires`, a copy of the `requires:` block of its `preset.yml` (such as `{"min_vram_gb": 12, "recommended_vram_gb": 16}`) or `None` |
| `preset_template(preset_id)` | The `PresetTemplate` of a loaded preset as its loader built it, installed or not, or `None`: its `path` on this server, its `driver`, and its modes down to each form field and its `configuration`, recommended model files included. It is a deep copy: changing it changes nothing in the loaded preset. Its fields are those the loader builds (`src/features/presets/templates.py`) and follow it; they are not a stable contract until the plugin API's stability question (issue #36) is answered, so read the fields you need and expect them to move |
| `resolve_preset_path(preset_id, reference)` | `reference`, a path a preset names (such as the `path` of a field's option file), with its `{{ paths.preset }}` and `{{ paths._shared }}` variables replaced by the preset's folder and the shared presets folder, then resolved, links followed. `None` when the preset is not loaded, or when the resolved path is not inside the preset's folder or the shared presets folder: a `..` that climbs out, an absolute path elsewhere, a drive letter, a UNC path, or a symbolic link or junction that leads out. On Windows the check ignores case, as the file system does. A path without either variable is resolved against the server's working directory, as the loader reads it, so it is `None` unless it lands in one of the two folders |
| `preset_install_state(preset_id)` | A `PresetInstallState`: `installed`, whether an administrator installed the preset, and `assigned`, whether it is then assigned to at least one user or group. A preset that is not installed is never `assigned` |

Only `list_presets(user)` is scoped to a user: it lists what they may run and says nothing of the
other presets. `describe_preset`, `preset_template`, `resolve_preset_path` and
`preset_install_state` are not scoped to a user. They take no user and read the presets of the
instance, whoever asks: the first three read any loaded preset, installed, assigned or not, and
`preset_install_state` tells about any preset id. Before you show a user what one of them reads,
check that `list_presets(user)` lists that preset. `preset_install_state` is for what administrators see,
such as a [setup check](#showing-admins-the-next-setup-step), and a `PresetTemplate` holds paths on
this server.

## Running generations from a plugin

`src.plugin_api.generation` is the supported way to start, follow and read generations from
outside the Generate page. Each function takes the acting user and enforces the same rules the
HTTP API enforces for that user: anything that is not theirs is reported as not found rather than
forbidden (administrators may reach anything), and a content-restricted user only sees what the
history API would show them.

| Function | Does |
|---|---|
| `await submit_generation(user, request)` | Same path as `POST /api/generations/start`: routing, form binding, cloud and content policy. Returns `{generation_id, status, queue_position}` and raises what the route turns into HTTP errors. With `request.idempotency_key` set, submitting the same key again returns the first submission instead of starting another (see below). The preset must be one the user is offered: installed and assigned to them, directly or through one of their groups, as `GET /api/presets` lists them and as the assistant's tools check (administrators may use any preset). Any other preset id, unknown ones included, raises `PermissionError` with the same message, so the answer does not tell which presets exist. This is checked first, before an idempotency key is looked up: a retry after the preset was unassigned is refused too |
| `await preview_memory(user, request)` | Same path as `POST /api/generations/memory-preview`: binds the form and picks the backend as a submit would, then returns `{estimate, coverage, device, budget, backend}` without queueing anything, running a hook or loading a model. A preset the user is not offered raises the same `PermissionError` as `submit_generation`. Otherwise it raises what the route turns into HTTP errors: `ValueError` for an unknown preset, `FormBindingError` (a `ValueError`) for a form that does not bind, `FormNotFoundException` for an unknown `form_name`, `NoBackendForEngineError` or `NoEligibleBackendError` (both `RuntimeError`) when no backend can run it |
| `await cancel_generation(user, generation_id)` | Cancels one generation; returns whether anything was cancelled |
| `generation_state(user, generation_id)` | The live status record, or the stored row once the process no longer tracks it, scoped as `GET /api/generations/{id}/status` scopes it: for anyone but an administrator, a failure's `message`, `error_message` and `error_user_message` are the generic user copy for its `error_code`, with `error_contact_admin` set |
| `generation_record(user, generation_id)` | The stored row, as `generation_state` returns it once the generation has left the queue, even while the live record still exists; `None` while the generation is not stored yet. Its failure text is scoped as `GET /api/generations/history/{id}` scopes it: the generic user copy for anyone but an administrator. Never carries the raw detail of a failure |
| `generation_failure(user, generation_id)` | The failure of a failed generation with its raw `detail`, as `GET /api/generations/{id}/failure` answers it, or `None` when the generation is unknown or did not fail. Administrators only: anyone else, the generation's owner included, gets `PermissionError` |
| `generation_active()` | Whether the application's generation queue holds a generation, pending or running, of any user on any backend: a plugin waits for `False` before a heavy background job. It says nothing about whose generations they are |
| `list_generation_files(user, generation_id, final_only=True)` | The generation's files in canonical order, each with its `index` in the unfiltered list (the `file_index` of a `<field>__origin` link). `final_only` leaves out intermediate and derived files |
| `resolve_media_ref(user, ref)` | Turns `{"kind": "history", "generation_id", "file_id"}` or `{"kind": "library", "item_id"}` into `{value, media_type, original_filename, origin}`; `value` is what a media field accepts. Paths and URLs are never accepted; a bad, foreign, hidden or deleted reference raises `MediaRefError` with a `code` of `invalid_ref` or `not_found` |
| `get_session(user, session_id, mode=None)` | The owner's session as it is now: `{session_id, preset_id, name, version, updated_at, mode, data}`. `version` is the write counter every save bumps (layout-only saves included), so compare payloads rather than versions to tell whether settings changed. With `mode`, `data` is that mode's payload or `None`; raises `SessionNotFoundError` for anyone else |
| `get_session_snapshot(user, session_id, version=None)` | The owner's session payload, or one of its saved versions; raises `SessionNotFoundError` for anyone else. Its `version` echoes the history version asked for, not the write counter |

**Idempotent submits.** `GenerationRequest.idempotency_key` (1 to 200 characters, also accepted as the
`idempotency_key` field of the `POST /api/generations/start` body) makes a submit safe to retry. Keys are
scoped to the submitting user, so two users may use the same key. A second submit with a key the user
already used starts nothing and returns the original `{generation_id, status, queue_position}`; it is
resolved before the form is bound or any backend is chosen, and concurrent submits with one key produce
exactly one generation. The key is stored with a fingerprint of the request (preset, mode, form name, form data and prompts); reusing a key for a different request raises `IdempotencyKeyConflict` (HTTP 409 on the route). If the original row exists but this process is no longer tracking it (for example
the process stopped between recording and queueing it), the retry reports it as `failed` rather than
leaving it pending; use a new key to run it again. Idempotency is settled inside one backend process: concurrent submits are serialised by an in-process lock, so it does not hold across several API processes sharing a database. A submit with no key behaves as it always did.

Generations started this way stream progress to websocket subscribers and write a run report,
exactly like ones started from the page.

```python
from src.plugin_api.generation import (
    GenerationRequest, list_generation_files, resolve_media_ref, submit_generation,
)

async def upscale_last_result(user, preset_id, generation_id):
    first = list_generation_files(user, generation_id)[0]
    media = resolve_media_ref(
        user, {"kind": "history", "generation_id": generation_id, "file_id": first["id"]}
    )
    form_data = {"image": media["value"], "image__origin": media["origin"]}
    return await submit_generation(
        user, GenerationRequest(preset_id=preset_id, mode="img2img", form_data=form_data)
    )
```

### Preparing a request from a saved session

A plugin frontend can turn a saved session into the same `GenerationRequest` the Generate page
would send, without a Generate tab: `window.__potionui.generation.prepareRequest(input)`.

| Input | Meaning |
| --- | --- |
| `presetId`, `mode` | The preset and the mode to run. |
| `session` | One mode's saved settings, as a session stores them (`prompt`, `promptSegments`, `promptTabs`, `promptRelay`, `videoDirector`, `variables`, `formData`, `selectedVariant`, ...). |
| `overrides` | Optional changes on top of the session: `formData` is merged field by field, every other key replaces the session's value. |
| `presetVars` | The preset's `vars` (from `GET /api/presets/{id}`), which decide multi-prompt, Prompt Relay, promptless and optional-prompt modes and the Video or Music Director. |
| `tabId` | Optional; becomes the request's `tab_id`. |
| `random`, `now` | Optional sources for chip shuffles and shuffled variables, for repeatable results. |

It returns `{ok: true, request, variableRolls, submittedPromptTemplate, shuffled, director}`, or
`{ok: false, code, reason}` where `code` is `no_preset`, `no_prompt` or `director` and `reason`
is a message when there is one. Chip shuffles and shuffled variables are resolved in the call, so
each call is a fresh roll. Submit the request with `submit_generation` (or
`POST /api/generations/start`); the server still binds and checks it like any other request.

### Following a generation's progress

A plugin frontend follows a running generation with
`window.__potionui.generation.subscribe(generationId, handler)`. It returns an unsubscribe
function; call it when the generation is over or when your page unmounts.

Read the generation's current state with `GET /api/generations/{id}/status` before you
subscribe: events start when you subscribe and what happened before is not replayed. An
unknown id, or one that belongs to another user, produces no event at all, not even an error.

```js
const unsubscribe = window.__potionui.generation.subscribe(generationId, (event) => {
  if (event.type === 'generation_status') {
    showProgress(event.message, event.progress);
    return;
  }
  unsubscribe();
  if (event.type === 'generation_complete') loadResults(generationId);
  else if (event.type === 'generation_error') showError(event.data?.message ?? event.message);
  else showCancelled();
});
```

`handler` receives only these four events, and only for `generationId`:

| `event.type` | What it carries |
| --- | --- |
| `generation_status` | Progress while it runs: `generation_id`, `status` (`'running'`), `progress` (a number, or `null` while the stage has no fraction), `message`, `current_step`. |
| `generation_complete` | `data`: the generation's status record (`data.id`, `data.status`, ...). Read the files with `list_generation_files` or the history API. |
| `generation_error` | The failure: `data.generation_id` and `data.message`, or the same fields at the top level, depending on where it failed. |
| `generation_cancelled` | `data`: the generation's status record. |

Other messages of the generation socket (previews, artifacts, timers, the subscription
acknowledgement or a subscription error) are not passed on. The server only streams a
user's own generations; administrators may follow any.

All plugin subscriptions share one connection to the app's generation websocket
(`/ws/generation`), opened on the first `subscribe` and closed when the last subscriber
unsubscribes, so subscribing to many generations does not open many sockets. The core pages
(Generate, History) keep their own connections. The socket reconnects on its own after a drop
and subscribes to every followed generation again. The generation protocol has no unsubscribe
message: `unsubscribe()` stops calling your handler, and the server stops sending once the
shared connection closes. A handler that throws is logged and does not stop the other
handlers.

`subscribe` is absent on hosts that predate it; feature-detect it with
`window.__potionui.generation.subscribe?.(...)` and fall back to polling
`GET /api/generations/{id}/status` if you need to.

## Working with the user's media

`src.plugin_api.media` lets a plugin read the file of a media the user picked, take a frame out of
a video, store a file it made in the user's Library and tell which file types the Library takes.
Each function that names a media takes the acting user and reaches only what the history and
Library APIs show that user: a media that is not theirs, that their content policy hides or whose
file is gone is reported as not found, never as forbidden.

A media is named by the reference `resolve_media_ref` takes (see
[Running generations from a plugin](#running-generations-from-a-plugin)):
`{"kind": "history", "generation_id", "file_id"}` for a file of a generation, `{"kind": "library",
"item_id"}` for a Library item. Paths and storage keys are never accepted. `resolve_media_ref`,
`MediaRefError` and the reference types import from `src.plugin_api.media` and from
`src.plugin_api` as well.

| Function | Does |
|---|---|
| `await extract_frame(user, ref, at="last", purpose="user_upload")` | Takes one frame of a video the user may see and stores it as their PNG upload, named after the video: `clip-frame-last.png`, `clip-frame-2.5s.png`. `at` is `"last"` or a number of seconds from the start, turned into a frame with the frame rate `probe_video` reads, so `at` in seconds needs `ffprobe` and `"last"` does not |
| `await save_user_media(user, path, purpose="user_upload")` | Stores a file the plugin wrote, at an absolute path on this server, as the user's upload, the way `POST /api/media/upload` stores one: the `media.before_upload` hooks, the same file stored once per user and purpose, thumbnails, and the user's plan. Its extension decides what it is: an image, a video, an audio file or a 3D model |
| `media_local_path(user, ref)` | The path on this server of the file of a media the user may see, or `None` when the storage keeps it elsewhere, such as object storage. The path is the stored file itself: read it, never write, move or delete it |
| `copy_media_file(user, ref, target)` | Streams the file of a media the user may see into `target`, a file of the plugin, wherever the storage keeps it. A copy that fails leaves no file at `target` |
| `upload_formats()` | The file extensions the Library takes, keyed by the `media_type` a stored file of that extension gets: `{"image": [".bmp", ".gif", ...], "video": [...], "audio": [...], "model": [".glb"]}`, each list sorted and read when called. `save_user_media` stores exactly these. A plugin can hand them to its page for a file picker, leaving out any its own code cannot read |

`extract_frame` and `save_user_media` return a `StoredMedia`: the keys of a `ResolvedMedia`, which
a media field and `bind_director_media` take (`value`, `uploads/<file>`; `media_type`;
`original_filename`; `origin`, `None`), plus `ref`, the `{"kind": "library", "item_id"}`
reference of the stored item, and `width` and `height` in pixels when they could be read. `ref` is
`None` only when the store could not write the Library row; the file is stored all the same, as
with the upload route.

`purpose="user_upload"` makes the file a Library item the user browses. `purpose="derived_artifact"`
keeps it servable and referenceable but out of the Library, for a helper file such as a mask; a
helper file is an image, a video or an audio file of at most 512 MB.

Refusals raise `MediaRefError` with a `code`:

| `code` | When |
|---|---|
| `invalid_ref` | The reference is not a reference object, is malformed or has other keys; `at` is not `"last"` or a finite number of seconds, 0 or more; `path` is relative |
| `not_found` | The media is not one the user may see, or its file is gone; `path` is not a file |
| `not_a_video` | `extract_frame` was given an image or an audio file |
| `extract_failed` | The frame could not be read, for example at a time past the end of the video, or the frame rate could not be read for `at` in seconds |
| `unsupported_media` | `save_user_media` was given a file whose extension is not one of an image, a video, an audio file or a 3D model |
| `invalid_purpose` | `purpose` is neither `user_upload` nor `derived_artifact` |
| `upload_rejected` | The store refused the file: a `media.before_upload` hook blocked it (the message is its reason), or a helper file is too large |

A full storage plan raises `LimitExceeded` (`src.plugin_api.limits`), as the upload route does.
`save_user_media` checks the plan and the helper file cap against the size of the file before it
reads it. Without a user, `extract_frame` and `save_user_media` raise `PermissionError`.
`media_local_path` and `copy_media_file` raise `invalid_ref` and `not_found` only, and a copy lets
the error of the storage through when it fails.

`save_user_media` stores whatever file it is given. Give it files your plugin wrote, never a path
taken from a request.

```python
from src.plugin_api.media import extract_frame
from src.plugin_api.presets import bind_director_media

async def continue_from(user, request, generation_id, file_id):
    frame = await extract_frame(user, {"kind": "history", "generation_id": generation_id, "file_id": file_id})
    return bind_director_media(request, "first", frame, promote=True)
```

## Driving native-engine generation directly

Most pipes just assemble existing pipes into a pipeline. A pipe that needs to run inference
against the native (in-process) engine itself — VAE-encode a reference image, sample, decode —
rather than only consuming other pipes' outputs, does that through `src.plugin_api.native`
(`src/pipelines/pipes/generator/krea2_edit/` is the in-tree example — a generator that in-context
edits a source image rather than generating from scratch).

`GeneratorKrea2Pipe` is the concrete core Krea-2 generator pipe. Subclass it (as `generator/krea2_edit` does)
to reuse its config schema and the shared seed-loop/model-acquisition machinery
(`build_context`/`process`) instead of re-deriving Krea-2's own defaults — override
`generate_one` for whatever your pipe does differently:

```python
from src.plugin_api import (
    Conditioning, GeneratorContext, GeneratorKrea2Pipe,
    NativeGeneratorHandle, ProgressEmitter, native_step_hooks,
)

class MyEditPipe(GeneratorKrea2Pipe):
    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter):
        gen: NativeGeneratorHandle = ctx.extra["generator"]  # already constructed for you
        latent = gen.sample(Conditioning(cond=..., uncond=...), gen.latent_shape_for(w, h),
                             steps=..., seed=seed, cfg_scale=..., hooks=native_step_hooks(gen, progress, ...))
        pixels = gen.decode(latent)
        ...
```

`NativeGeneratorHandle` is a **narrow, structural** view of the real (large, fast-moving,
mostly-private) `NativeGenerator` class — only `encode_image`, `sample`, `decode` and
`latent_shape_for`, the operations a generator pipe's `generate_one` actually calls. A real
generator instance (handed to you pre-built via `ctx.extra["generator"]`) satisfies it
structurally; there is no wrapper object and no runtime cost. You never construct a generator
yourself, and you should never call anything on `gen` beyond this surface — everything else
(device placement, VRAM streaming, quantization, ...) is exactly the kind of internal that
`src.plugin_api` exists to keep you out of. If your pipe genuinely needs another `NativeGenerator`
operation, that is a gap in this surface — ask for it, the same as any other plugin_api gap.

## Contributing a tool tab

A tool tab is a whole Generate tab owned by one tool, next to the core Image editor and Video
editor tabs. It has no form pane, Workbench or deck: the tab area is your component. Declare it
in `manifest.yml`:

```yaml
tool_tabs:
  - id: stamp
    label: Stamp tool
    icon: brush
    accepts: [image]
    handles: []
    component: StampToolTab.svelte
```

| Key | Meaning |
|---|---|
| `id` | Lowercase slug, unique within the plugin. The tab's tool kind is `<plugin id>.<id>`. |
| `label` | Shown in the "+" menu after the Image and Video editors, in the phone Tabs sheet and as the default tab name. |
| `icon` | An app icon name (`brush`, `sliders`, `film`, ...). Defaults to `extension`. |
| `accepts` | Media kinds (`image`, `video`, `audio`) the tool can open. |
| `handles` | Edit actions the tool claims, for the History and Library shortcuts. |
| `component` | The compiled component under `frontend/dist/` (a `frontend/dist/` prefix is accepted). |

The entries are served with the other manifest-declared extensions
(`GET /api/plugins/frontend-extensions`, key `tool_tabs`) for enabled plugins only, and the
frontend registers them through the same refresh that loads renderers and field types. A tab whose
plugin is later disabled stays open and says the tool is not available.

The component receives one prop, `host`:

| Member | What it is |
|---|---|
| `tabId`, `tool` | The tab's id and its tool kind. |
| `source` | The media the tab was opened on (`{ path, relative_path?, url?, name?, type? }`), or `null`. |
| `state` | Your persisted JSON state, or `null` for a fresh tab. Keep it small: params and ids, never pixels. |
| `activeTool` | The sub-tool the tab should open on, when it was opened from a shortcut. |
| `hint` | A one-shot hint from the shortcut that opened the tab, or `null`. Call `clearHint()` once you have used it. |
| `run` | The background run the tab is tracking (`{ runId, progress }`), so a reload can reattach. |
| `phone` | `true` when the tab renders in the phone layout. |
| `setState(next)` | Persist new state. It is saved with the tab and in Workspaces. |
| `setDirty(edits)` | The number of unsaved edits. It drives the tab's warning dot and the close prompt. |
| `setTitle(title)`, `setThumbnail(url)` | Name the tab (unless the user renamed it) and set its 20px thumbnail. |
| `setSource(ref)`, `setActiveTool(id)`, `setRun(run)`, `clearHint()` | Update what the tab is open on and what it tracks. |
| `setActions({ undo, redo, save })` | Fill the phone top bar's Undo and Save buttons (`{ label?, disabled?, run }`; `save` also takes `progress`). |
| `openToolTab(tool, source, { activeTool, hint })` | Open another tool tab, following the same rules as the History and Library shortcuts. |

The core editors use these `activeTool` ids, and the History and Library shortcuts open them with
one of these set:

| Tab (`tool`) | `activeTool` ids |
|---|---|
| Image editor (`image`) | `crop`, `resize`, `canvas`, `paint`, `mask`, `effects`, `colorKey`, `subject` |
| Video editor (`video`) | `trim`, `split`, `crop`, `resize`, `speed`, `mute`, `volume`, `replace`, `effects`, `frame` |

A `hint` is an object with any of `look` (an Effects look to preview), `effect` (a video effect id such
as `film_grain` to preview), `orientation` (`rotate` or `flip`, for Crop) and `sendTo` (`{ tabId, field }`:
the generation tab and media field the tab was opened from, so Send to a generation lists that field first).
A hint is not persisted with the tab.

Opening a tab focuses one that already holds the same item, fills an empty tab of the same kind
next, and otherwise adds a new tab. Bitmaps your tool produces are uploaded and referenced by id;
the tab never stores them inline.

## Extending the image editor

The media loader's image editor (**Edit** on a loaded image, **Draw a new image** on an empty
field) is built on registries a plugin frontend can
extend through `window.__potionui.imageEditor`. Every `register*` call returns an unregister
function. Registrations are global and survive editor sessions, so register once when your
plugin's frontend loads.

```js
const editor = window.__potionui.imageEditor;

const offTool = editor.registerTool({
  id: 'line',
  label: 'Line',
  icon: 'M5 19L19 5',
  key: 'N',
  group: 'shapes',
  options: ['size', 'color'],
  hint: 'Drag to draw a straight line.',
  cursor: 'crosshair',
  pointerDown(host, point) { start = point; },
  pointerUp(host, point) {
    const layer = host.activeLayer;
    const before = host.snapshotLayer(layer);
    const context = layer.canvas.getContext('2d');
    context.strokeStyle = host.settings.color;
    context.lineWidth = host.settings.size;
    context.beginPath();
    context.moveTo(start.x - layer.x, start.y - layer.y);
    context.lineTo(point.x - layer.x, point.y - layer.y);
    context.stroke();
    host.commitPixels('Line', layer, { x: 0, y: 0, width: layer.canvas.width, height: layer.canvas.height }, before);
  }
});
```

- **Tools** (`registerTool`): a `PaintTool` with `id`, `label`, `icon` (an SVG path `d` for a
  24x24 viewBox), optional `key` shortcut, `group` (tools in the same group sit together),
  `options` (any of `size`, `opacity`, `color`, `tolerance`) and pointer handlers
  `pointerDown`, `pointerMove`, `pointerUp`, `pointerCancel` receiving the `host` and a
  document-space point. `overlay(host, context)` draws guides above the canvas. Edit pixels
  on `host.activeLayer`, then call `host.commitPixels(label, layer, dirtyRect, before)` with a
  copy from `host.snapshotLayer(layer)` taken first; that single call is what makes the edit
  undoable. Pointer handlers run only for the primary button.
- **Adjustments** (`registerAdjustment`; `registerFilter` stays as a deprecated alias for one release): a `PaintFilter` with `id`, `label`, numeric `params`
  (`{id, label, min, max, value}`) or `toggle: true`, `active(values)` and
  `apply(image, values)` returning the new `{width, height, data}` RGBA buffer (it may be a
  promise). Filters appear in the Adjust panel, preview live, and honour the selection
  automatically: the editor blends the result back through the selection mask, so a filter
  never needs to know about it.
- **Image sources** (`registerImageSource`): `{id, label, pick()}`; `pick()` resolves
  `{url | blob | canvas, name}` or `null`. The source is listed in the Open and Add image menus.
- **Actions** (`registerAction`): `{id, label, run(editor)}` shown in the Canvas panel; `editor`
  offers `flatten()`, `addImage(canvas, name)` and `openImage(canvas, name)`.
- `addImage(canvas, name)` / `openImage(canvas, name)` / `isOpen()` act on the editor that is
  currently open. `addImage` returns `false` when none is. `openImage` replaces the open
  editor's image, and with none open it uploads the canvas as a PNG and opens an Image editor
  tab on it (History and Library **Edit** actions open that tab too), so it always returns `true`.

The editor saves a flattened PNG as a new Library upload through the normal upload API, so a
plugin never writes files itself.

## Overlays and z-index layering

Every core modal, popover, dropdown and menu shares one open-order z-index stack
(`$lib/actions/overlayLayer.ts` + `$lib/actions/layerStack.ts`): whichever one opened last
renders on top, whether that's a dropdown opened from inside a modal or a modal opened from
inside a popover. A plugin overlay that hardcodes its own `z-index` sits outside that stack
and will render under (or, worse, unpredictably over) core overlays depending on the
literal number picked.

For a plugin dialog, mount `window.__potionui.components.PluginModal` (see above) rather
than rolling your own overlay — it wraps `BaseModal`, which is already layered, so your
dialog always stacks correctly relative to everything else without any extra work.

If you do need a custom overlay (a popover, a context menu, anything you position and
z-index yourself), join the same stack instead of hardcoding a number:

- From a Svelte component: `use:window.__potionui.layers.overlayLayer` on the positioned
  element (the one carrying `position: fixed`/`absolute` and the z-index — not a
  `display: contents` wrapper, which has no box for a z-index to apply to). Pass a tier as
  the action's argument for a toast- or tooltip-like overlay that must always render above
  the modal/popover band (`use:window.__potionui.layers.overlayLayer={'tooltip'}`); the
  default tier is `'overlay'`, the one every core modal and popover uses.
- From plain DOM code (no Svelte template involved, e.g. an `innerHTML`-built overlay): call
  `window.__potionui.layers.acquire('overlay')` for the z-index to apply yourself, and
  `window.__potionui.layers.release('overlay', thatValue)` once the overlay closes — every
  acquire needs a matching release, or the stack never lets that band's baseline settle back
  down.

Feature-detect the same way as any other host API addition (`window.__potionui.layers?.…`)
so your plugin degrades instead of throwing on an older core build.

A core component that opens further overlays of its own — `PromptModelField`'s model
picker, for instance — only stacks correctly if the container you mounted it into is
itself part of this same layering system: inside a `PluginModal`, or inside an element
using `layers.overlayLayer` as described above. Mounting it into a plain, unlayered
`<div>` risks its own picker rendering under whatever else happens to be open.

## Calling your plugin's backend from its page

A plugin page (a `pages:` entry) is mounted by the host with `mountPlugin(el, props)`, or, when
the module has no `mountPlugin`, as a Svelte component with the same props except
`hostComponents`. The props are:

- `pluginId`, `route` — your plugin's id and the page's route.
- `api` — the HTTP client described below.
- `user` — the signed-in user.
- `navigate(path)` — moves the app to `path` without reloading it; `replaceUrl(path)` changes
  the address without adding a history entry.
- `hostComponents` — the same registry as `window.__potionui.components`.
- `headerActions`, `setHeaderTitleHidden`, `setHeaderHidden` — see "Using the page header from
  a plugin page" below.

`api` is bound to your plugin. Paths are relative to your plugin's routes, which live under
`/api/plugins/<plugin id>` (so `api.get('/projects')` calls `GET /api/plugins/<plugin id>/projects`).
Every method goes through the host's authenticated client, the one behind `window.__potionui.api`
(see "Using the app's UI components and Tailwind in a plugin frontend" below): it sends the
current session token and the app's cookies, adopts a refreshed token, and ends the session on a
401 as core pages do. Your routes can therefore depend on the usual authenticated user, and a
long-lived page keeps working after the token is refreshed. `fetchFromApp` and `fetchRawFromApp`
take a path on the app, from the site root; a URL of another host is refused with a `TypeError`
before anything is sent.

| Method | Sends | Resolves with |
| --- | --- | --- |
| `get(path)` | `GET` | the parsed JSON body, whatever the status |
| `post(path, data)`, `put(path, data)` | `POST`/`PUT` with `data` as JSON | the parsed JSON body, whatever the status |
| `delete(path)` | `DELETE` | the parsed JSON body, whatever the status |
| `uploadFile(path, formData)` | `POST` with a `FormData` body (multipart) | the parsed JSON body, whatever the status |
| `fetchFromApp(path)` | `GET` on an app route, `path` taken from the site root (`/api/backends`) | the parsed JSON body, whatever the status |
| `request(method, path, data?)` | any method; `data`, when given, as JSON | the parsed body: JSON, or the text when it is not JSON, or `null` for a `204` or an empty body. Throws on an error status |
| `patch(path, data)` | `request('PATCH', path, data)` | as `request` |
| `fetchRaw(path, init?)` | your `init` (`method`, `headers`, `body`, `signal`, plus `timeout` and `onUploadProgress`) | the `Response` itself, for binary bodies and ranges. Never throws on a status |
| `fetchRawFromApp(path, init?)` | as `fetchRaw`, on an app route taken from the site root (`/api/media/upload`) | as `fetchRaw` |
| `download(path, filename?)` | `GET` through `fetchRaw`, then saves the body as a file | once the browser has been asked to save it. Throws on an error status |
| `getToken()` | — | the current token, or `null`. HTTP calls never need it; it is only for a WebSocket you open yourself (a browser WebSocket cannot send headers, so pass it as `?token=`) |

**Errors.** `get`, `post`, `put`, `delete`, `uploadFile` and `fetchFromApp` do not look at the
HTTP status: they resolve with the parsed JSON of any response, error responses included, or
with `null` when the body is not JSON (an HTML error page from a proxy, for example). They reject
only when the network fails (a `TypeError`). They keep that behaviour because existing plugins
read errors out of the body. For new code, prefer `request` and `patch`, which throw an `Error`
(named `PluginApiError`) for any status outside 200–299 with:

- `status` — the HTTP status;
- `body` — the parsed body (JSON, text, or `null`);
- `detail` — `body.detail` when the body has one (what FastAPI's `HTTPException` sends),
  otherwise `undefined`;
- `message` — `detail` when it is a string, `detail.message` when it is an object with a
  message, otherwise `Request failed with status <status>`.

A network failure rejects with a `TypeError`, and an aborted `signal` with an `AbortError`, as
with the other methods.

**Timeouts and progress.** `get`, `post`, `put`, `delete` and `fetchFromApp` time out after 30
seconds, like `window.__potionui.api`. `uploadFile`, `request`, `patch`, `fetchRaw`,
`fetchRawFromApp` and `download` have no timeout unless you pass `timeout` (in milliseconds) to
`fetchRaw` or `fetchRawFromApp`; abort them with a `signal`. `onUploadProgress(loaded, total)` is
called as the body of a `fetchRaw` or `fetchRawFromApp` goes up, which a progress bar for a
`FormData` upload needs; `total` is `0` when the size is unknown. A `Blob` body without a
`Content-Type` header is sent with the blob's own type. The response body is read in full before
the `Response` resolves, so `fetchRaw` suits files and ranges, not a stream that never ends.

```js
export function mountPlugin(el, { api }) {
  async function rename(id, name) {
    try {
      await api.patch(`/projects/${id}`, { name });
    } catch (err) {
      if (err.status === 409) showConflict(err.message);
      else throw err;
    }
  }

  async function exportProject(id, name) {
    await api.download(`/projects/${id}/export.zip`, `${name}.zip`);
  }

  async function showThumbnail(img, renderId) {
    const res = await api.fetchRaw(`/renders/${renderId}/thumbnail`);
    if (res.ok) img.src = URL.createObjectURL(await res.blob());
  }

  async function uploadToLibrary(file, onPercent, signal) {
    const form = new FormData();
    form.append('file', file, file.name);
    const res = await api.fetchRawFromApp('/api/media/upload', {
      method: 'POST',
      body: form,
      signal,
      onUploadProgress: (loaded, total) => total && onPercent((loaded / total) * 100)
    });
    return res.json();
  }
  // ...
}
```

**Downloads.** `download` fetches the file with the token, turns it into a `Blob`, clicks a
hidden `<a download>` and removes it; the object URL is revoked a minute later. The file is
named after `filename`, else after the response's `Content-Disposition` (`filename*=UTF-8''…`
or `filename="…"`), else after the last segment of `path`. The whole body is held in memory
before it is saved, which suits exports and documents rather than very large media files. A
plain `<a href>` to your route would not work, since a link cannot carry the `Authorization`
header.

`request`, `patch`, `fetchRaw`, `fetchRawFromApp` and `download` are absent on hosts that
predate them; check `if (api.request)` before relying on them.

## Keeping window shortcuts out of text fields

A plugin page or modal that listens for keys on the window (Space to play, arrows to step,
Ctrl+Z to undo) fires while the user types into any field that happens to sit under that
window, unless the handler stands down. The host's own shortcuts share one guard, and it is
exposed so a plugin behaves the same way:

- `window.__potionui.input.isTypingInField(event)` takes a `keydown`/`keyup` and is true when
  the key came from a text field (`input`, `textarea`, `select`, `contenteditable`, or a
  `textbox`/`searchbox`/`combobox` role, including inside an open shadow root), during an IME
  composition, or from a slider or radio control that owns the arrow keys. It leaves
  checkboxes, buttons and range sliders alone for keys they do not use.
- `window.__potionui.input.isEditableTarget(target)` is the underlying target check, for events
  that are not keyboard events, such as `paste`. Pass `event.composedPath()[0] ?? event.target`.

```js
function onKey(event) {
  if (window.__potionui?.input?.isTypingInField(event)) return;
}

function onPaste(event) {
  const origin = event.composedPath()[0] ?? event.target;
  if (window.__potionui?.input?.isEditableTarget(origin)) return;
}
```

Feature-detect as with any other host API addition (`window.__potionui.input?.…`) and keep
your own fallback for an older core build.

## Using the page header from a plugin page

A plugin page is mounted under the app's page header (the plugin's icon and label). The
props passed to `mountPlugin` include three header hooks:

- `headerActions` — an empty element inside the header row, to the right of the title. Move
  or render your own controls into it; it takes the remaining width and may shrink
  (`min-width: 0`), so your controls should size themselves and collapse their own overflow.
- `setHeaderTitleHidden(hidden)` — hides the host title (icon and label) so `headerActions`
  can own the whole row, for instance with a back button and an editable name while a record
  is open. Call `setHeaderTitleHidden(false)` to bring the title back.
- `setHeaderHidden(hidden)` — hides the whole host header row, for a page that renders its own
  header (a library page with its own title, search and actions). The `headerActions` element
  stays in the page, so showing the row again later is safe.

Both setters reset whenever the page is loaded again, and are absent on hosts that predate
them, so call them as `setHeaderTitleHidden?.(true)`.

## Keyboard shortcuts on a plugin page

The app's global shortcuts (listed in the Keyboard Shortcuts dialog) listen on the whole
document. They already ignore unmodified keys typed into an input, a textarea, a select or a
contenteditable element, so an ordinary plugin form needs nothing. A plugin page that
handles plain keys itself, such as a timeline that uses Space, the arrow keys or letters,
turns the global shortcuts off while it is on screen (and lets its own handlers stand down
while the user types, with `input.isTypingInField` from "Keeping window shortcuts out of text
fields" above):

```js
const releaseKeyboard = window.__potionui.keyboard.suppress();

// on unmount:
releaseKeyboard(); // or: window.__potionui.keyboard.resume();
```

- `suppress()` turns the global shortcuts off and returns a function that undoes this one
  call. That function works once; calling it again does nothing.
- `resume()` undoes one earlier `suppress()`. A `resume()` with nothing to undo does nothing.

Suppressions are counted: the shortcuts come back when every `suppress()` has been matched by
a `resume()` or by its own returned function, so two components of a page can each suppress
and resume without switching the other's suppression off. Plugin suppressions are kept apart
from the app's own (the paint editor, the Keyboard Shortcuts dialog): the app resuming its
shortcuts does not cancel yours, and yours do not cancel the app's.

When a plugin page is left, or replaced by another plugin's page, the host calls
`unmountPlugin` and then drops every plugin suppression still held, including one held by a
plugin component mounted elsewhere at that moment. A page that throws while unmounting, that
has no `unmountPlugin`, or that forgets to resume therefore cannot leave the app without
shortcuts. A function returned by `suppress()` before that point does nothing afterwards, so a
late call from the old page cannot cancel the next page's suppression. Navigating between
sub-paths of the same plugin page keeps the plugin mounted and keeps its suppressions. This
cleanup is done by the plugin page host only: a component mounted through a plugin slot on
another page must resume on its own unmount.

The keyboard API is absent on hosts that predate it; call it as
`window.__potionui.keyboard?.suppress()`.

## Using the app's UI components and Tailwind in a plugin frontend

A plugin frontend is bundled on its own and cannot import `$lib`, but every shared UI
component of the app is available through the host: `window.__potionui.components.<Name>`
exposes `mount(el, props)`, `update(instance, props)` and `unmount(instance)`. Components
load on first use, so `mount` returns immediately and the component appears once its code
has arrived; `update` calls made in between are merged.

Registered names: every component of `$lib/components/ui/` (`Button`, `IconButton`,
`CopyButton`, `Card`, `PageHeader`, `PageTitle`, `Badge`, `Kbd`, `Input`, `InputGroup`,
`Spinner`, `PageContainer`, `EmptyState`, `LoadErrorState`, `SegmentedControl`, `Pagination`,
`Switch`, `Alert`, `UserPicker`, `AdminOnlyMark`), every component of `$lib/components/detail/`
(`DetailLayout`, `DetailHeader`, `DetailBody`, `DetailSection`, `DetailFooter`, `DetailTabs`,
`DetailField`, `KVGrid`, `KVItem`), every component of `$lib/components/library/`
(`LibraryPage`, `LibraryShell`, `LibraryFilterBar`, `LibraryEntryCard`, `LibraryDensityToggle`,
`LibraryFilterChipRow`, `FilterPopoverFrame`), plus `Tooltip` and `Icon`. A new component
dropped into one of those folders is registered automatically. `DynamicForm` renders a preset's
form outside the Generate page: pass `presetId`, `mode`, `variant`, `initialData` and
`onFormDataChange(data)`, and optionally `audience` (`'simple'` or `'advanced'`) to show that
audience's fields without changing the user's own Simple/Advanced choice. `window.__potionui.confirm({title?,
message, variant?, confirmLabel?, cancelLabel?})` opens the app's confirm dialog and resolves to a
boolean: `true` from the confirm button, `false` from the cancel button, Escape or the backdrop.
`confirmLabel` and `cancelLabel` rename the two buttons (`'Confirm'` and `'Cancel'` by default).
`window.__potionui.notifications.toast(level, message)` shows a toast.

`window.__potionui.api` makes authenticated requests to the app, so a plugin never reads the
token from `localStorage` or builds an `Authorization` header. `api.fetch(path, init?)` takes an
absolute app path and an init of `{method, headers, body, signal, timeout, onUploadProgress}` and resolves to a
standard `Response`, so `ok`, `status`, `json()` and `blob()` work as with `fetch`;
`api.json(path, init?)` resolves to the parsed body, or `null` when the body is not JSON. A 4xx
or 5xx answer resolves with its body rather than throwing; only a network failure rejects (a
`TypeError`, or an `AbortError` when `signal` aborts). A refreshed session token is adopted, a
401 ends the session as it does for core pages (except on `/api/auth/login` and
`/api/auth/register`), and cookies are sent. Calls time out after 30 seconds; pass `timeout: 0`
for a long request. A `FormData` body is sent as multipart with no timeout, and a `Blob` body
or a file download works through `api.fetch` the same way. `onUploadProgress(loaded, total)` is
called as the body goes up (`total` is `0` when the browser does not know the size). The
response body is read in full before the `Response` resolves: there is no streaming.

Because registration is automatic, the props of every component in `ui/`, `detail/` and
`library/` are a public contract: a plugin may pass any of them, so renaming or removing one
breaks plugins that use it. Treat such a change as an API change and note it in the changelog;
a component that must stay private belongs outside those folders.

`Button` no longer takes a `title` prop and `IconButton` sets no native `title`: an `IconButton`
shows its `label` in the host tooltip by itself (see "Icon buttons with a tooltip" below), and any
other control is wrapped in the host `Tooltip` (`props={{ text }}`) to show a hover hint. `IconButton`
accepts `variant: 'overlay'` for controls that sit over media and forwards extra attributes
such as `data-*` to its button. `Tooltip` also opens on keyboard focus, closes on Escape,
describes its trigger with `aria-describedby` while open, and takes `truncateOnly: true` to
show only when its content is cut off.

Snippet props cannot cross the bundle boundary, so pass `children` as a string or as
`(el) => cleanup?` that fills the slot element. Any other snippet prop takes `{ slot: string |
(el) => cleanup? }`.

In a Svelte plugin component, `content/plugins/sdk/HostUi.svelte` does the mounting,
updating and cleanup for you, and its markup children are projected into the host component:

```svelte
<script>
  import HostUi from '../../../../sdk/HostUi.svelte';
  let clicks = $state(0);
</script>

<HostUi name="Button" props={{ variant: 'primary', onclick: () => (clicks += 1) }}>
  Clicked {clicks} times
</HostUi>
<HostUi name="Tooltip" props={{ text: 'Rendered by the host' }}>
  <span class="text-fg-muted">Hover me</span>
</HostUi>
```

Tailwind classes written in a plugin's `frontend/src/**/*.{svelte,js,ts}` (marketplace and
local) and in `content/plugins/sdk/` are scanned by the app's Tailwind build, so the semantic
token classes (`bg-surface-2`, `text-fg-muted`, `border-line`, ...) and arbitrary values work
in plugin markup. Rebuild the app frontend after adding new classes. Use the token classes only,
as in the app.

### Shipping a stylesheet with a plugin page

The app's Tailwind build only sees the plugins that are on disk when it runs. A plugin added
later, to `content/plugins/local/` of a Docker image or of an install whose frontend was built
before, finds only the classes the app happens to use itself, and its layout breaks wherever it
uses others. Such a plugin ships its own stylesheet with its page.

A page plugin whose `frontend/src/` holds `<Component>.css`, next to the `<Component>.svelte`
that `index.js` imports, gets `frontend/dist/<Component>.css` from `scripts/build-plugins.mjs`.
The build compiles that file with Tailwind, using the app's `frontend/tailwind.config.js` as a
preset (same theme, tokens, type scale and `focus-visible` variants), over the plugin's own
`frontend/src/**/*.{svelte,js,ts}` only, with Tailwind's base styles off. `@tailwind utilities;`
is usually the whole file; plain CSS rules may follow it. A release build minifies the sheet as
it minifies the bundle. Compiling it needs the app frontend's packages (`npm ci` in
`frontend/`); a plugin without the file does not.

The build puts every rule under the page of the plugin: `.p-4` becomes
`:where([data-potionui-plugin="video-producer"]) .p-4`, `:root`, `html` and `body` at the start
of a selector become the page element itself, and keyframes keep their names. The sheet holds no
token values: its rules read the app's CSS variables (`--surface-2`, `--fg-muted`, ...), so it
follows the light and dark themes like the app does.

The scope sits in `:where()`, so it adds no specificity: a rule of the sheet weighs what the same
utility weighs in the app's own CSS, and the sheet, adopted after the app's, wins only ties. A
host component rendered in the page (a `Button` of `hostComponents`) keeps its `hover:`,
`focus-visible:` and `disabled:` styles when the plugin uses the same base utility, since those
variants weigh more than a bare class. One tie remains: a responsive variant weighs what a bare
class weighs, so a host component whose element carries `px-3 md:px-4` keeps `px-3` at every
width once the plugin uses `px-3` anywhere, because the sheet's `px-3` comes after the app's
`md:px-4`. `:where()` needs Chrome or Edge 88, Firefox 78 or Safari 14; the app's own
`focus-visible:` variant already uses it, and in Firefox and Safari adopting a constructed
stylesheet needs more (Firefox 101, Safari 16.4).

`GET /api/plugins/pages/{route}` answers `stylesheet`, the path of `<Component>.css` in
`frontend/dist/` when the file is there, `null` otherwise. The plugin page route fetches the
sheet with the bundle, keeps only the rules that stay inside the page element, adopts the sheet
before it mounts the page, and removes it when the page unmounts. A kept style rule has
selectors that each start with the plugin's `:where([data-potionui-plugin="<id>"])`, the scope
alone inside `:where()`, followed by nothing, a descendant or a child combinator, and holds no
nested rules; `@media`, `@supports`, `@container` and `@layer` blocks are looked into,
`@keyframes` and `@font-face` are kept, and everything else (`@import`, `@page`, a selector that
starts elsewhere or reaches a sibling of the page, a style rule with nested rules) is dropped,
with a console warning in development. The sheet therefore applies to the plugin page only, and
to nothing once the user leaves it.

Limits of the page stylesheet:

- **Content outside the page element.** Content the plugin moves out of its page element, such
  as its own dropdown or modal portalled to `document.body`, gets nothing from the sheet. Render
  it with the host components, which carry the app's own classes, or give its root the attribute
  `data-potionui-plugin` with the plugin id, which brings the sheet to the root's descendants but
  not to the root itself. The plugins of this repository do not show the problem because the
  app's Tailwind build still scans them.
- **Selectors on the document root.** Only `:root`, `html` and `body` written alone at the start
  of a selector become the page element. `:root[data-theme='light'] .x`, `html.dark .x`,
  `body:hover .x`, and any selector that depends on an ancestor of the page element such as
  `[data-theme='light'] .x`, stay in the sheet under the page, where they never match, and no
  warning says so. They are not supported: the utilities read the app's tokens and already
  follow the theme.
- **CSS nesting.** Nesting in the plain CSS written after `@tailwind utilities;` is not
  supported: the scope prefix breaks `&` rules, and the page drops any style rule that holds
  nested rules.
- **Global names.** `@keyframes` and `@font-face` names are global while the page is open: a
  plugin's `@keyframes spin` replaces the app's `spin`, and a `font-family` it declares applies
  wherever the app uses that name. Prefix your own names with the plugin id
  (`@keyframes video-producer-pulse`).
- **Responsive variants of host components.** As said above, a host component's `md:px-4` loses
  to the sheet's `px-3` when the component's element carries both and the plugin uses `px-3`.
  The host components that change a utility at a breakpoint are the page layout ones
  (`PageHeader`, `PageContainer`, `PageTitle`) and several of `detail/` and `library/`;
  `Button` and the other controls of `ui/` do not.

A plugin without the file, which is every plugin in this repository, gets `stylesheet: null`
and loads as before.

### Adding your own icons

Every host component that takes an icon name (`Icon`, `Button`, `IconButton`, the items of
`SegmentedControl`, ...) looks the name up in the app's icon library. A plugin adds its own
icons with `window.__potionui.registerIcon(name, paths)`:

```js
window.__potionui.registerIcon('my-plugin-clapperboard', [
  'M4 11v8a1 1 0 001 1h14a1 1 0 001-1v-8H4z',
  'M4 11l1.5-6 15 3.5-.9 2.5'
]);
```

`paths` is the `d` attribute of one `<path>` (a string) or of several (an array), drawn
like the app's own icons: a 24×24 view box, stroked with `currentColor`, no fill.

`registerIcon` returns `true` when the icon was added. It returns `false` and changes nothing
when the name already exists, whether it is one of the app's icons or one a plugin
registered earlier, or when the name or one of the paths is empty: a plugin can never replace
an icon the app or another plugin relies on. Prefix your names with your plugin's id to stay
clear of names the app may add later.

An icon can be registered at any time: an `Icon` already on screen under that name (showing
the first letter of the name as its fallback) redraws itself with the new paths. The
registration lasts until the page is reloaded, so register your icons when your plugin
starts, for example at the top of `mountPlugin`.

`registerIcon` is absent on hosts that predate it; call it as
`window.__potionui.registerIcon?.(...)`.

### Icon buttons with a tooltip

Each `HostUi` is its own Svelte root, so a host `IconButton` mounted inside a host `Tooltip`
through two `HostUi` does not know it is inside one: it shows its own `label` tooltip and the
user gets two. Give the tooltip to the `IconButton` itself instead:

```svelte
<HostUi
  name="IconButton"
  props={{ icon: 'film', label: 'Render', tooltip: 'Render the selected take', kbd: ['Shift', 'R'], onclick: render }}
/>
```

- `tooltip` — the tooltip text. Without it the tooltip shows `label`.
- `kbd` — a keyboard shortcut shown in the tooltip, as one string (`'Ctrl+K'`) or one string per
  key (`['Shift', 'R']`). It is only displayed: handling the keys is up to your page (see
  "Keyboard shortcuts on a plugin page"). A screen reader reads it after the tooltip text, the
  keys joined with `+`.

Both props are consumed by the button: unlike other extra props (`data-*`), they never reach the
DOM as attributes. The tooltip attaches to the button itself, with no wrapper element, so `class`
and layout apply to the button as usual, and `label` stays its accessible name (`aria-label`).
Inside a `Tooltip` of the same Svelte tree, an `IconButton` shows no tooltip of its own, so
`tooltip` and `kbd` are not shown there either.

### A list page like History and Models

`LibraryPage` is the app's list-page frame with props only, so a plugin page looks and behaves
like the other list pages: the page title with its count, a left rail of sections with counts
(use it for status filters), the search box, an optional sort select, a primary action button, and
your content below. Pass the content as `children`.

| Prop | Meaning |
| --- | --- |
| `title`, `titleLabel` | `title` heads the section rail; `titleLabel` is the page title (default: the active section's label). |
| `persistKey` | Remembers whether the rail is collapsed. |
| `sections`, `section`, `onSelectSection(id)`, `sectionCounts` | The rail: `[{id, label, icon}]`, the active id, a callback, and `{id: count}`. One section hides the rail. |
| `count` | The count shown next to the page title. |
| `q`, `onQueryChange(value)`, `searchPlaceholder`, `searchHint` | The search box; `/` focuses it. |
| `sortBy`, `sortOptions`, `onSortChange(value)` | `sortOptions` is `[{value, label}]`; omit it to hide the select. |
| `primaryLabel`, `primaryIcon`, `primaryLoading`, `primaryDisabled`, `onPrimary` | The primary action; no label, no button. |
| `heightClass` | Defaults to `h-full`, so the page fills its container. |

```svelte
<HostUi
  name="LibraryPage"
  props={{
    title: 'Widgets',
    titleLabel: 'Widgets',
    persistKey: 'my-plugin:list',
    sections, section, onSelectSection, sectionCounts,
    q, onQueryChange, searchPlaceholder: 'Search widgets',
    primaryLabel: 'New widget', primaryIcon: 'plus', onPrimary: create
  }}
>
  <MyCards />
</HostUi>
```

`LibraryFilterBar` is also mountable on its own for a custom frame; it renders the search box,
the Filters popover (pass `popover` as `{ slot }`) and the sort select without a wrapper.

### Showing a generation in the details modal

`GenerationDetailsModal` is the History details view. Mount it with `generationId` (plus
`initialFileIndex`, and `isOpen`, `onClose`) to show any generation a plugin knows the id of.
Two optional props let a plugin walk a list and add its own controls, both as plain data:

| Prop | Meaning |
| --- | --- |
| `onNavigate(direction)`, `hasPrevious`, `hasNext`, `position: {index, total}` | Shows the previous/next generation controls and `Shift+Left/Right`. On navigation the plugin updates `generationId` and `initialFileIndex` through `update`. |
| `onFileChange({generationId, fileIndex, fileId})` | Called when the file on screen changes, including the first time it is known (and when the user steps between a generation's files). |
| `extraSections` | Sections added to the side panel under Information: `[{id, title, icon?, rows?: [{id, label, checked, disabled?, onToggle(checked)}], actions?: [{id, label, icon?, onClick}]}]`. Pass a new array through `update` to change them. |

```js
const modal = window.__potionui.components.GenerationDetailsModal;
const handle = modal.mount(el, {
  isOpen: true,
  generationId,
  initialFileIndex: 0,
  onClose: () => modal.unmount(handle),
  extraSections: [{ id: 'next', title: 'Send to', rows: [{ id: 'a', label: 'Upscale', checked: false, onToggle: (on) => pick('a', on) }] }]
});
```

### Media pickers with multi-select

`GenerationHistoryModal` and `UploadLibraryModal` are mountable from a plugin
(`window.__potionui.components.<Name>`, props below; `UploadLibraryModal` loads on first use).
Both keep their single-select behaviour unless `multiple` is set:

| Prop | Meaning |
| --- | --- |
| `isOpen`, `onClose`, `title`, `mediaType` | As for single-select. `mediaType` filters what is listed. |
| `multiple` | Switches to multi-select: every file gets its own checkbox, a separate preview button, a selected count and Cancel / Use selected buttons. `onSelect` is not called. |
| `selectedKeys` | The selection, as a string array. Optional; when given the picker follows it, so a plugin can keep the selection across closing and reopening, and across switching between the two pickers. |
| `onSelectionChange(keys, items)` | Called on every toggle with the new keys and the picked items the picker has seen. |
| `onConfirm(keys, items)` | Called by Use selected, which is disabled while nothing is selected; the picker then calls `onClose`. |
| `selectableMediaTypes` | `['image']`, `['video']` or both. Other files stay listed but dimmed, with the reason under them. |

Keys are `generation_id:file_id` in History (every file of a generation is selectable on its own)
and the item id in the Library. Each item is `{key, mediaType, filename, origin, generation?, file?,
item?}`, where `origin` is `{kind: 'history', generationId, fileId}` or `{kind: 'library', itemId}`.
Cancel, the close button, Escape and the backdrop all restore the selection the picker was opened
with before calling `onClose`. Selected keys survive paging, searching and filtering. `items` always
lines up with `keys`: for a file the picker has not loaded since it was mounted, the item carries
only `key` and `origin`, and `mediaType` and `filename` are `null`.

```js
const picker = window.__potionui.components.UploadLibraryModal;
const el = document.body.appendChild(document.createElement('div'));
let keys = [];
const handle = picker.mount(el, {
  isOpen: true,
  multiple: true,
  selectedKeys: keys,
  selectableMediaTypes: ['image', 'video'],
  onSelectionChange: (next) => { keys = next; },
  onConfirm: (next, items) => useMedia(items),
  onClose: () => picker.unmount(handle)
});
```

### Graph canvases with `@xyflow/svelte`

`@xyflow/svelte` is part of the plugin toolchain (`content/plugins/package.json`), at the same
version as the app (pinned exactly in both). A plugin imports it like any other dependency
(`import { SvelteFlow, Background } from '@xyflow/svelte'`) and it is bundled into the plugin's
own dist (about 250 kB minified). The build writes only `.js` there, apart from the page
stylesheet described above, so the library's stylesheet is not shipped as a file: copy what you
need from `@xyflow/svelte/dist/style.css` into the component's `<style>` block, using
`:global(...)` selectors.

## Building your plugin's frontend

Every plugin frontend (a `pages[].component`, a `field_types[].component`, an
`admin_tabs[].component`, a `tool_tabs[].component`, ...) compiles through the single shared toolchain in
`content/plugins/package.json` (esbuild + esbuild-svelte, pinned to the frontend's Svelte
major), driven by `scripts/build-plugins.mjs`:

```bash
cd content/plugins && npm install    # once
node ../../scripts/build-plugins.mjs [pluginId ...]   # or: npm run build:plugins from frontend/
```

The script auto-detects each plugin's build shape from `frontend/src/` (a page with
`index.js`, or standalone components referenced from `manifest.yml`) and writes to
`frontend/dist/`, which you commit alongside your source change.

Two build modes:

- **release (default)** — minified, no source maps, deterministic (identical inputs produce
  byte-identical output). This is what belongs in a committed `frontend/dist/`.
- **debug** — pass `--debug` or set `PLUGIN_BUILD=debug` for unminified output with source
  maps, to step through your plugin dist in the browser devtools. Never commit a debug build.

Both modes preserve upstream license notices (`legalComments: 'eof'`) from the bundled
Svelte runtime and any other bundled dependency.

## Publishing a plugin in a catalog

A plugin that lives outside this repository installs in two ways: cloned or unpacked into
`content/plugins/local/<id>/` by hand, then **Scan for plugins**; or from a catalog, with
**Install from catalog** in Admin → Plugins. A catalog is a JSON file served over https:

```json
{
  "schema": 1,
  "plugins": [
    {
      "id": "video-producer",
      "name": "Video Producer",
      "version": "0.1.0",
      "description": "Storyboards, takes, editing and delivery for AI video.",
      "license": "GPL-3.0",
      "requires": { "core": "0.2.0" },
      "release": {
        "archive": "https://example.com/releases/video-producer-0.1.0.zip",
        "sha256": "<64 lowercase hex digits>"
      }
    }
  ]
}
```

- `id` is the plugin folder name and the manifest `id`: lowercase letters, digits, `.`,
  `_` and `-`, at most 64 characters, not ending with `.`, never a Windows device name
  (`con`, `nul`, `com1`, ...).
- `requires.core` is the PotionUI version the release needs: a bare version is a minimum
  (`"0.2.0"` means `>=0.2.0`), and a range such as `">=0.2,<0.3"` is read as a Python
  version specifier. An entry whose requirement this PotionUI does not meet, or that is not
  a version at all, is listed as incompatible and cannot be installed. Other keys of
  `requires`, and keys PotionUI does not know anywhere in an entry, are ignored.
- `release` names the archive and its sha256; `null`, or both values `null`, lists the
  plugin without a release. An entry that breaks these rules, or an id listed twice, is set
  aside and shown with its reason; the others stay installable.

The archive is a zip holding a single folder named after the id, with `manifest.yml` at
its root and the built `frontend/dist/`: what `content/plugins/local/<id>/` should hold,
without tests. The `__MACOSX/` folder the macOS Finder adds next to it is skipped, never
unpacked. PotionUI refuses the whole archive if any other entry is outside that folder, has
an absolute path, a `..`, `.` or empty part, a backslash or a `:`, a name Windows cannot
hold (a device name, a trailing dot or space, a control character), is a link or a special
file, is encrypted, differs from another only by letter case, or uses a file as a folder,
and when the archive holds more than 10,000 entries or unpacks to more than 1 GiB. The
manifest must be valid and carry the `id` and `version` of the catalog entry.

What an install does, in order: it reads the catalog again on the server (the browser
sends the catalog URL, the plugin id, and the version and sha256 the admin confirmed),
refuses with nothing written when the entry it reads now lists another version or
sha256, downloads the archive (at most 128 MiB, within 300 seconds for the archive and 30
for the catalog, redirects included, and dropped when the host sends nothing for 30
seconds),
compares its sha256 with the catalog's before anything is written, unpacks it into a
staging folder under `content/plugins/local/.potionui-install/`, checks the manifest,
writes `.potionui-catalog.json` (the catalog URL as given, query included, since it binds
the plugin to that catalog; the version, archive URL and sha256) into the plugin folder,
moves the folder into `content/plugins/local/<id>/`, and scans for
plugins. A new plugin is discovered disabled, like one placed by hand, and stays so across
a restart even when the database still holds an enabled row for its id from a copy that
was removed while the server was stopped.

The same id is never installed twice. A plugin that ships with PotionUI
(`content/plugins/marketplace/`) is listed as bundled; a folder of that id that holds no
`.potionui-catalog.json` (a clone, a plugin placed by hand), or the id found in another
folder or in `POTIONUI_EXTRA_PLUGIN_DIRS`, is a conflict; neither is replaced. So is an id
that differs from an installed plugin's only by `-` and `_` (`foo-bar` and `foo_bar`), since
both would load as the same Python package. A plugin
belongs to the catalog it was installed from, the URL kept in its `.potionui-catalog.json`
(scheme and host compared without case, a default port the same as none, path and query
exactly): any other catalog that lists its id shows it as a conflict, installed from
another catalog, and cannot replace it. A plugin installed from this catalog is updated
only to a strictly newer version, both versions read as Python package versions (PEP 440)
like `requires.core`: the same version is installed, an older one is listed as older
than the installed one, and a version either side that is not a PEP 440 version is
listed as not comparable; none of these offers an action. PEP 440 rules apply as they are:
`1.0.0+build` counts as newer than `1.0.0`, and after a version with an epoch (`1!0.1`)
every version without one is older, so a catalog should use plain release numbers. On an
update, the
new folder replaces the old one whole, the old one comes back if the move fails, and an
enabled plugin is enabled again on its new manifest, with presets, pipes and recipes
rescanned. The old folder waits in the staging folder until the scan that follows the
move has the plugin back up, and is never deleted before: when that scan fails (a page
route another plugin already owns, for example), when the new version of an enabled plugin
does not start (a missing Python package, a hook that fails to register), or when the new
manifest of any plugin leaves it in error, the old folder goes back in place, an enabled
plugin is enabled again on it, and the install answers with the reason. A disabled plugin
takes the new version as it is, since nothing runs it until it is enabled. If the old
folder cannot be moved back, or the server stops before the check, it stays in
`content/plugins/local/.potionui-install/`, named in the answer when there is one.

PotionUI installs no Python package. `dependencies.python` stays in the manifest; the
enable step checks it, and the install answer lists it so the admin knows what to
install.

Catalog and archive URLs must be https, to a host that resolves to public addresses
only; every redirect is checked the same way, and the connection goes to the addresses
that were checked. A host written as a number that is not a full IP address
(`2130706433`, `127.1`, `0x7f.1`) is refused in every mode, and so is a URL with
credentials in it. Refusals, statuses and logs name the host, never the whole URL, so a
token in a query or a signed redirect does not end up in them. For a catalog on a local network or a test server, start PotionUI
with `POTIONUI_PLUGIN_CATALOG_ALLOW_LOCAL=1`: catalog and archive URLs may then point to
local and private addresses, and use plain http, but only to such an address. A plain http
URL to a public address, written as one or a name with any public answer, is refused
before connecting, a redirect from https to http included; https to public hosts is
unchanged. Local and private addresses include link-local ones, such as the cloud metadata
address `169.254.169.254`, so set the variable only on a server where that is acceptable.
Installing,
updating and reading a catalog are admin-only (`POST /api/plugins/catalog` and
`POST /api/plugins/catalog/install`). The sha256 proves the archive is the one the
catalog names; it says nothing about the catalog itself, so install only from catalogs
you trust: a plugin runs with the same rights as PotionUI.

## Related

- [Providers](providers.md) — writing a marketplace provider.
- [Backends and Engines](backends.md) — contributing an engine.
- [Presets](presets.md) — what a preset is and how pipes are wired into one.
