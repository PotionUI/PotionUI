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
| **Identity** — who is calling | `.identity` | `User`, `AccountType`, `get_current_active_user` |
| **Hooks and runtime** — reacting to the app, reaching its managers | `.hooks` | `HookContext`, `HookResult`, `HookSpec`, `hooks_registry`, `PluginRegistry`, `get_container`, `get_global_plugin_registry`, `get_global_tool_registry`, `ModelLifecycle` |
| **Providers** — talking to a model marketplace | `.providers` | `MarketplaceProviderBase`, `ProviderCapability`, `ProviderMetadata`, `ProviderModelInfo`, `ProviderSearchResult`, `ProviderPromptItem`, `ProviderError`, `ProviderConnectionError`, `ProviderRateLimitError`, `ProviderNotFoundError`, `get_provider_registry`, `ModelInfo` |
| **Chat** — extending the assistant | `.chat` | `BaseTool`, `ToolContext`, `ToolResult`, `ToolSource`, `PreChatAction` |
| **Backends** — contributing an engine | `.backends` | `InProcessBackend`, `BaseBackendConfig`, `BackendStatus`, `BackendHealth`, `BackendModel`, `ModelListingNotSupported`, `deduplicate` |
| **Pipes** — contributing a pipeline step | `.pipes` | `BasePipe`, `PipeInput`, `PipeOutput`, `PipeInputSpec`, `PipeOutputSpec`, `PipeConfigSpec`, `IOType`, `GenerationOutput`, `ImageGenerationOutput`, `VideoGenerationOutput`, `MeshGenerationOutput`, `GalleryGenerationOutput`, `ProgressGenerationOutput`, `ComfyUIWorkflowGenerationOutput`, `GenerationExecutionError`, `Icon`, `Progress`, `logger`, `OutputTypeSpec`, `SerializeContext`, `output_type_registry`, `DuplicateOutputTypeError` |
| **Native engine** — driving generation through the in-process engine directly | `.native` | `Conditioning`, `GeneratorContext`, `GeneratorKrea2Pipe`, `NativeGeneratorHandle`, `ProgressEmitter`, `native_step_hooks` |
| **Presets** — finding a preset, starting a generation | `.presets` | `PresetCollaborators`, `preset_operations`, `FilePresetRepository`, `GenerationRequest`, `PromptPair` |
| **Storage** — keeping data | `.storage` | `db`, `generate_ulid`, `Settings`, `SettingRepository`, `PluginRepository` |
| **Media** | `.media` | `convert_image_to_base64`, `BackgroundMattingModel` |

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


async def on_generation_complete(context: HookContext) -> HookContext:
    ...
    return context
```

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

Three lifecycle hooks fire around a plugin's own life. They are ordinary manifest hooks —
declare a handler the same way you would for any other hook point:

```yaml
hooks:
  backend:
    - hook: "plugin.lifecycle.boot"
      handler: "backend.hooks.lifecycle_hooks.on_boot"
    - hook: "plugin.lifecycle.enable"
      handler: "backend.hooks.lifecycle_hooks.on_enable"
    - hook: "plugin.lifecycle.disable"
      handler: "backend.hooks.lifecycle_hooks.on_disable"
```

| Hook | Fires |
|---|---|
| `plugin.lifecycle.boot` | Once per process, for every enabled plugin — at startup, and right after `enable` when a plugin is enabled at runtime |
| `plugin.lifecycle.enable` | Only on the disabled → enabled transition, **never** at boot |
| `plugin.lifecycle.disable` | On the enabled → disabled transition, before the plugin's hooks are unregistered |

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
three can block the transition they report.

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

## Contributing a setup recipe

An enabled plugin can ship its own setup-wizard recipe, the same way it can ship its own
preset (see `presets:` below and [Presets](presets.md)). Declare a `recipes:` root in
`manifest.yml`; no Python import or handler is required:

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

## Contributing a prompt importer

Core owns file-based import: `POST /api/prompts/import` accepts `styles.csv`
(A1111/Forge/SD.Next/InvokeAI), Fooocus style JSON, dynamicprompts wildcard YAML, one
prompt per line, and images carrying A1111, ComfyUI, InvokeAI or NovelAI metadata, with
format auto-detection (`src/features/prompt_database/importing/`), and
`GET /api/prompts/export?format=styles-csv` writes the library back out. A plugin importer
is for a source that is not a file — a marketplace or a remote service. It appears in the
Prompt Library's Import menu below the core entry. Declare each importer in `manifest.yml`:

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

A provider-backed importer (one that calls out to a marketplace provider plugin, e.g. the
`civitai` provider's `fetch_image_prompts`) registers exactly the same way — `run()` is
free to call whatever else it needs, including another plugin's provider, before handing
prompts to `create_prompt_for_user`.

Disabling the plugin removes its importer(s) from `GET /api/prompts/importers`
immediately; prompts already imported are unaffected.

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

Name collisions are resolved deterministically, never silently: a core mode of the target preset
always wins over a contribution; between two contributions for the same target+mode name, the
first one (contributions sorted by plugin id, then declaration order) wins and the rest are
rejected with a load error attributed to the plugin. A contributed mode inherits the target's
`speed_profiles:` and admin `configuration:` (its fields' `@config:<key>` filter_tags resolve
against the target, same as a core mode's) — there is no separate per-contribution mechanism for
either. See [Presets](presets.md) "Plugin-contributed modes" for the full contract, including how
provenance (`source_plugin`) surfaces in `GET /api/presets/{id}/modes` and the mode picker.
`content/plugins/marketplace/krea2-edit/` is the first shipped example: an `edit` mode contributed onto
the native Krea-2 preset.

## Driving native-engine generation directly

Most pipes just assemble existing pipes into a pipeline. A pipe that needs to run inference
against the native (in-process) engine itself — VAE-encode a reference image, sample, decode —
rather than only consuming other pipes' outputs, does that through `src.plugin_api.native`
(`content/plugins/marketplace/krea2-edit/` is the shipped example — a generator that in-context
edits a source image rather than generating from scratch).

`GeneratorKrea2Pipe` is the concrete core Krea-2 generator pipe. Subclass it (as krea2-edit does)
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

## Related

- [Providers](providers.md) — writing a marketplace provider.
- [Backends and Engines](backends.md) — contributing an engine.
- [Presets](presets.md) — what a preset is and how pipes are wired into one.
