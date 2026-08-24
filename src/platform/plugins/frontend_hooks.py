"""Hook points registered for reference only - executed in JavaScript on the frontend."""

from src.platform.plugins.hooks import hooks_registry

WORKBENCH_HOOKS = hooks_registry.declare(
    "workbench", "frontend",
    "actions", "tools",
    "image.click",  # attr: image_click
    specs={
        "actions": {
            "description": "Mounted via `<PluginSlot hookName=\"workbench.actions\" position=\"top-right\">` in Workbench.svelte, overlaid on completed image/video/audio output. Populated by a manifest's `hooks.frontend: [{hook: 'workbench.actions', component, position}]` entries.",
            "payload": {
                "imageUrl": {"type": "string | null", "description": "Display URL of the current image, if the workbench item is an image"},
                "videoUrl": {"type": "string | null", "description": "Display URL of the current video, if the workbench item is a video"},
                "audioData": {"type": "unknown", "description": "Current audio track data, if the workbench item is audio"},
                "generationId": {"type": "string | undefined", "description": "ID of the generation being displayed"},
                "workbenchIndex": {"type": "number", "description": "Index of the currently displayed item within the workbench"},
                "metadata": {"type": "object", "description": "Image metadata (width/height/fileSize/format), see imageMetadata in Workbench.svelte"},
                "fileType": {"type": "string", "description": "'image' | 'video' | 'audio'"},
            },
            "use_when": ["Adding a custom action button (e.g. send-to-external-tool) alongside the built-in download/zoom/modal buttons"],
        },
        "tools": {
            "description": "Mounted via `<PluginSlot hookName=\"workbench.tools\" position=\"bottom-left\">` in Workbench.svelte, shown in gallery mode or for completed videos. Populated the same way as `workbench.actions`.",
            "payload": {
                "imageUrl": {"type": "string | null", "description": "Display URL of the current image"},
                "generationId": {"type": "string | undefined", "description": "ID of the generation being displayed"},
                "workbenchIndex": {"type": "number", "description": "Index of the currently displayed item within the workbench"},
                "metadata": {"type": "object", "description": "Image metadata (width/height/fileSize/format)"},
                "fileType": {"type": "string", "description": "'image' | 'video' | 'audio'"},
            },
            "use_when": ["Adding a custom tool button alongside the built-in compare/zoom buttons"],
        },
        "image.click": {
            "description": "Not mounted via PluginSlot - Workbench.svelte's handleImageClick instead POSTs this context to `/api/plugins/{plugin_id}/execute` for every plugin registered against this hook (via `pluginStore.getPluginsByHook`), on every click of the displayed image.",
            "payload": {
                "imageUrl": {"type": "string", "description": "Display URL of the clicked image"},
                "clickX": {"type": "number", "description": "Click X coordinate (clientX)"},
                "clickY": {"type": "number", "description": "Click Y coordinate (clientY)"},
                "generationId": {"type": "string | undefined", "description": "ID of the generation being displayed"},
                "workbenchIndex": {"type": "number", "description": "Index of the currently displayed item"},
                "metadata": {"type": "object", "description": "Image metadata (width/height/fileSize/format)"},
            },
            "use_when": ["Backend-executed reactions to a user clicking an image, e.g. point-based masking/inpainting triggers"],
        },
    },
)

IMAGE_HOOKS = hooks_registry.declare(
    "image", "frontend",
    "actions",
    specs={
        "actions": {
            "description": "Mounted via `<PluginSlot hookName=\"image.actions\">` in GenerationDetailsModal.svelte, alongside the built-in favorite/tag/reuse/download/open-in-new-tab buttons for the currently displayed file. Populated by a manifest's `hooks.frontend: [{hook: 'image.actions', component, position}]` entries.",
            "payload": {
                "generationId": {"type": "string", "description": "ID of the generation being displayed"},
                "fileIndex": {"type": "number", "description": "Index of the currently displayed file within the generation"},
                "filename": {"type": "string", "description": "Basename of the currently displayed file, as served under /api/media/generations/{generationId}/{filename}"},
                "fileUrl": {"type": "string", "description": "Display URL of the currently displayed file"},
                "fileType": {"type": "string", "description": "'image' | 'video' | the raw file_type value on the generation's file record"},
            },
            "use_when": ["Adding a custom per-image action button, e.g. exporting a file to an external tool"],
        },
    },
)

# Plugin-facing renderer kinds (`renderers: [{kind, key, component}]`) and
# extension slot names (`contributions: [{slot, ...}]`), served via
# GET /api/plugins/frontend-extensions and consumed by the frontend renderer
# registries / extensionSlots (roadmap phase A5).
RENDERER_HOOKS = hooks_registry.declare(
    "renderer", "frontend",
    "history.artifact", "workbench.file", "generation.output", "model.view",
    specs={
        "history.artifact": {
            "description": "Registered via manifest `renderers: [{kind: 'history.artifact', key: <artifact_type>, component}]`. Dispatched by `artifactRendererRegistry` and mounted by `GenerationPanelHistory.svelte` for the matching `artifact_type` (one component per type, not additive).",
            "payload": {
                "artifact": {"type": "object", "description": "The pipe artifact object for this history entry (artifact_type-specific shape, e.g. seed/compare_images/models - see the pipe_artifact WebSocket message in CLAUDE.md)"},
                "onSeedClick": {"type": "function | undefined", "description": "Optional callback, only meaningful for the built-in 'seed' artifact type"},
            },
            "use_when": ["Rendering a custom artifact type produced by a plugin's own pipe (a pipe_artifact WebSocket message with a plugin-defined artifact_type)"],
        },
        "workbench.file": {
            "description": "Registered via manifest `renderers: [{kind: 'workbench.file', key: <file_type>, component}]`. Resolved by `workbenchFileRendererRegistry` and mounted by `Workbench.svelte` as a fallback for a `file_type` it has no dedicated branch for (falls back to the 'image' entry if unregistered).",
            "payload": {
                "file": {"type": "object", "description": "The current gallery item or generation object (currentGalleryItem ?? currentGeneration in Workbench.svelte) - shape varies by generation output type"},
            },
            "use_when": ["Adding a preview renderer for a custom media/file type your plugin's pipeline produces"],
        },
        "generation.output": {
            "description": "Registered via manifest `renderers: [{kind: 'generation.output', key: <message_type>, component}]`. `registerPluginOutputHandler` (frontend/src/lib/generation/messages/pluginOutput.ts) stores the latest WebSocket message of that type per-tab; `GenerationPanelHistory.svelte` mounts it via `PluginMessageRenderer.svelte`.",
            "payload": {
                "msg": {"type": "unknown", "description": "The raw WebSocket message of the plugin-declared message_type (see WebSocket Message Structure in CLAUDE.md - this is a custom message type, not one of the built-in ones)"},
            },
            "use_when": ["Rendering a plugin-specific WebSocket message type emitted during generation (e.g. a custom progress/artifact message a plugin's backend pipe sends)"],
        },
        "model.view": {
            "description": "Registered via manifest `renderers: [{kind: 'model.view', key, component}]`. Unlike the other renderer kinds, these are additive - every registered section renders alongside the core model detail page (frontend/src/routes/models/[model_id]/+page.svelte), keyed by `pluginId:key`.",
            "payload": {
                "model": {"type": "object", "description": "The model detail object for the current /models/[model_id] page"},
            },
            "use_when": ["Adding an extra section to a model's detail page, e.g. plugin-specific metadata or actions for that model"],
        },
    },
)

# "admin.tabs" and "generation.panel.modes" existed in the previous hook enum
# with zero references anywhere in frontend/ or plugin manifests and were
# dropped in A2 - re-declared here now that A5 brings them back as live
# extension slots.
EXTENSION_SLOT_HOOKS = hooks_registry.declare(
    "slot", "frontend",
    "admin.tabs", "nav.primary", "generation.panel.modes",
    specs={
        "admin.tabs": {
            "description": "Populated by a manifest's `contributions: [{slot: 'admin.tabs', component, label, order, require_role}]` entries. Read via `contributionsForSlot('admin.tabs')` and rendered as extra tabs in frontend/src/routes/admin/+page.svelte, alongside the core admin tabs.",
            "payload": {
                "plugin_id": {"type": "string", "description": "Contributing plugin's id"},
                "component": {"type": "string", "description": "Component asset path to mount for the tab"},
                "label": {"type": "string | undefined", "description": "Tab label"},
                "order": {"type": "number", "description": "Sort order among all admin.tabs contributions"},
                "require_role": {"type": "string | undefined", "description": "'ADMIN' to restrict visibility - filtered client-side against authStore's account_type"},
            },
            "use_when": ["Adding a plugin-specific settings/management tab to the admin page"],
        },
        "nav.primary": {
            "description": "Populated by a manifest's `contributions: [{slot: 'nav.primary', component, label, order, icon, route, require_role}]` entries. Read via `contributionsForSlot('nav.primary')` and merged into Sidebar.svelte's core nav list by `order` (core items use implicit orders 10, 20, 30... leaving room to slot in between).",
            "payload": {
                "plugin_id": {"type": "string", "description": "Contributing plugin's id"},
                "component": {"type": "string", "description": "Component name, used to build the default route (`/plugins/{component}`) if `route` isn't set"},
                "label": {"type": "string | undefined", "description": "Nav item label, falls back to `component`"},
                "order": {"type": "number", "description": "Sort order among all nav items (core + contributed)"},
                "icon": {"type": "string | undefined", "description": "Icon name, falls back to 'puzzle'"},
                "route": {"type": "string | undefined", "description": "Explicit route path override"},
                "require_role": {"type": "string | undefined", "description": "Role gate, same semantics as admin.tabs"},
            },
            "use_when": ["Adding a top-level navigation entry for a plugin's own page"],
        },
        "generation.panel.modes": {
            "description": "Populated by a manifest's `contributions: [{slot: 'generation.panel.modes', component, ...}]` entries. Read via `contributionsForSlot('generation.panel.modes')` in GenerationPanel.svelte and rendered as an extra drawer alongside the core history/chat/settings drawers, identified by a synthetic `plugin:<pluginId>:<component>` drawer id.",
            "payload": {
                "plugin_id": {"type": "string", "description": "Contributing plugin's id"},
                "component": {"type": "string", "description": "Component asset path mounted in the drawer"},
            },
            "use_when": ["Adding a custom drawer/mode to the generation panel, alongside history/chat/settings"],
        },
    },
)
