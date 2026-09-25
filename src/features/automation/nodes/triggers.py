"""
Catalog metadata for the 5 built-in trigger node types.

These specs have no `execute`/`start`/`stop` - the concrete `TriggerSource`
subclasses in `src.features.automation.triggers` are instantiated directly by
`AutomationRuntime`'s trigger factory (each needs different runtime deps:
the hook bridge, the filesystem watch manager, the GPU manager). Registering
them here just makes them visible to the node-type catalog / canvas palette
and reserves their `config_schema` as the single source of truth for both
ends (see plan A7 - `/api/automations/node-types`).

Field-def key names are dictated by the frontend field components, NOT by
`configuration:`-nesting convention preset `form.yml` fields use elsewhere:
`FormField.svelte` passes each field def straight through as `config` to
e.g. `SelectField.svelte`/`TextInput.svelte`/`NumberInput.svelte`/
`SliderField.svelte`, and those read `config.title` (not `config.label`),
`config.options`/`config.min`/`config.max`/`config.step` at the TOP LEVEL
(not nested under `configuration`), and `config.visible`/`config.reactions`
at the top level. Every field def below matches that exactly - verified
against the four components in `frontend/src/lib/components/form-fields/`.
"""

from typing import Dict, List

from src.platform.plugins.automation_nodes import NodeField, NodeTypeSpec, node_type_registry
from src.features.automation.triggers.filesystem import CUSTOM_PATH_VALUE, list_app_directories
from src.features.generation.error_classification import ERROR_CATEGORIES


def _hook_name_options() -> List[Dict[str, str]]:
    """
    Resolved lazily (per `/api/automations/node-types` request, via
    `registry.resolved_config_schema`), not snapshotted at import time -
    so hooks a plugin declares after this module loads still show up.
    """
    from src.platform.plugins.hooks import hooks_registry
    return [
        {"value": spec.name, "label": spec.name}
        for spec in sorted(hooks_registry.all(), key=lambda s: s.name)
        if spec.type == "backend"
    ]


def _error_category_options() -> List[Dict[str, str]]:
    return [{"value": category, "label": category.replace("_", " ").capitalize()} for category in ERROR_CATEGORIES]


def _user_filter_options() -> List[Dict[str, str]]:
    from src.features.users.repository import user_repo
    return [{"value": user.id, "label": user.username} for user in user_repo.get_all()]


def register(registry=node_type_registry) -> None:
    registry.register(NodeTypeSpec(
        key="trigger.hook_event",
        kind="trigger",
        title="Backend Event",
        description="Fires when a backend hook event occurs (e.g. generation completed, model indexed).",
        icon="zap",
        category="events",
        config_schema=[
            {"name": "hook_name", "type": "select", "title": "Event", "options_provider": _hook_name_options},
            {"name": "wait_for_completion", "type": "checkbox", "title": "Wait for this run to finish",
             "default": False,
             "description": "Make the event that fired this trigger wait for the run to complete before it "
                            "proceeds (e.g. hold a generation at generation.before_start while a model is "
                            "evicted). The event is never vetoed - on timeout or failure it proceeds anyway."},
            {"name": "wait_timeout_s", "type": "number", "title": "Wait Timeout (seconds)", "default": 30,
             "description": "Only used when 'Wait for this run to finish' is on."},
        ],
        # Payload is the hook's own `context.data` (see triggers/hook_bridge.py) -
        # its shape is whatever the selected hook publishes, so it can't be declared here.
        dynamic_outputs=True,
    ))

    registry.register(NodeTypeSpec(
        key="trigger.generation_failed",
        kind="trigger",
        title="Generation Failed",
        description="Fires once when a generation fails (never on cancel). Empty filters match every failure.",
        icon="warning",
        category="events",
        config_schema=[
            {"name": "category", "type": "select", "title": "Error Category (optional)", "default": "",
             "allow_empty": True, "options_provider": _error_category_options},
            {"name": "preset_id", "type": "string", "title": "Preset ID (optional)", "default": ""},
            {"name": "user_id", "type": "select", "title": "User (optional)", "default": "",
             "allow_empty": True, "options_provider": _user_filter_options},
        ],
        outputs=(
            NodeField("generation_id", "string", "Generation ID", "The failed generation, also its error ID.",
                      "01J9ZX3Q4K8M2N6P7R5T0V1W2Y"),
            NodeField("user_id", "string", "User ID", "Owning user, or null.", "01J9ZX0A1B2C3D4E5F6G7H8J9K"),
            NodeField("preset_id", "string", "Preset ID", "Preset the generation used.", "sdxl/juggernaut"),
            NodeField("error_code", "string", "Error Code", "Error category code.", "cuda_oom"),
            NodeField("category", "string", "Category", "Same as error_code.", "cuda_oom"),
            NodeField("message", "string", "Message", "Safe user-facing failure summary.",
                      "Ran out of GPU memory (VRAM) during generation."),
            NodeField("failed_pipe", "string", "Failed Pipe", "Pipe that failed, or null.", "generator"),
        ),
    ))

    registry.register(NodeTypeSpec(
        key="trigger.filesystem",
        kind="trigger",
        title="File Watcher",
        description="Fires when a file is created, modified, or deleted under a watched directory.",
        icon="folder-open",
        category="events",
        config_schema=[
            {"name": "directory", "type": "select", "title": "Directory", "options_provider": list_app_directories},
            {"name": "custom_path", "type": "textbox", "title": "Custom Path", "visible": False,
             "reactions": [
                 {"when": {"field": "directory", "equals": CUSTOM_PATH_VALUE}, "then": {"set_visibility": True}},
                 {"when": {"field": "directory", "not_equals": CUSTOM_PATH_VALUE}, "then": {"set_visibility": False}},
             ]},
            {"name": "recursive", "type": "checkbox", "title": "Include subdirectories", "default": True,
             "description": "Also fire for files created in subdirectories of the watched directory."},
            {"name": "event", "type": "select", "title": "Event", "default": "created",
             "options": [
                 {"label": "Created", "value": "created"},
                 {"label": "Modified", "value": "modified"},
                 {"label": "Deleted", "value": "deleted"},
                 {"label": "Any", "value": "any"},
             ]},
            {"name": "pattern", "type": "string", "title": "Filename Pattern", "default": "*"},
            {"name": "debounce_ms", "type": "number", "title": "Debounce (ms)", "default": 2000},
        ],
        # Mirrors `build_event_payload` (triggers/filesystem.py) - asserted by
        # test_outputs_contract.py so the two can't drift.
        outputs=(
            NodeField("path", "string", "Path", "Absolute path of the file that changed.",
                      "/home/u/models/loras/krea2/style.safetensors"),
            NodeField("event", "string", "Event", "created | modified | deleted.", "created"),
            NodeField("dir", "string", "Watched Directory", "The directory being watched.",
                      "/home/u/models/loras"),
            NodeField("parts", "array", "Parts",
                      "Deprecated alias of rel_parts - kept for graphs that already reference it.",
                      ["krea2", "style.safetensors"]),
            NodeField("rel_parts", "array", "Relative Parts",
                      "Path components relative to the watched directory. rel_parts.0 is the "
                      "immediate subdirectory - what a Switch usually keys off.",
                      ["krea2", "style.safetensors"]),
            NodeField("rel_path", "string", "Relative Path", "rel_parts joined with '/'.",
                      "krea2/style.safetensors"),
            NodeField("ext", "string", "Extension", "File suffix, including the dot.", ".safetensors"),
            NodeField("size", "number", "Size", "Size in bytes; null for deleted files.", 1234567),
        ),
    ))

    registry.register(NodeTypeSpec(
        key="trigger.schedule",
        kind="trigger",
        title="Schedule",
        description="Fires on a cron schedule or fixed interval.",
        icon="clock",
        category="events",
        config_schema=[
            {"name": "mode", "type": "select", "title": "Mode", "default": "interval",
             "options": [
                 {"label": "Interval", "value": "interval"},
                 {"label": "Cron", "value": "cron"},
             ]},
            {"name": "interval_s", "type": "number", "title": "Interval (seconds)", "default": 3600},
            {"name": "cron", "type": "string", "title": "Cron Expression", "default": ""},
        ],
        outputs=(
            NodeField("fired_at", "string", "Fired At", "ISO-8601 timestamp of the tick.",
                      "2026-07-09T12:00:00"),
            NodeField("mode", "string", "Mode", "interval | cron.", "interval"),
        ),
    ))

    registry.register(NodeTypeSpec(
        key="trigger.gpu_threshold",
        kind="trigger",
        title="GPU VRAM Threshold",
        description=(
            "Fires on a free-VRAM threshold. The 'drops below'/'rises above' directions "
            "fire once per crossing and re-arm only after VRAM swings back past the margin; "
            "the 'is below'/'is above' directions fire on every poll the condition holds."
        ),
        icon="cpu",
        category="events",
        config_schema=[
            {"name": "threshold_pct", "type": "slider", "title": "Threshold (% free)", "default": 20,
             "min": 0, "max": 100, "step": 1},
            {"name": "direction", "type": "select", "title": "Direction", "default": "below",
             "options": [
                 {"label": "Free VRAM drops below", "value": "below"},
                 {"label": "Free VRAM rises above", "value": "above"},
                 {"label": "Free VRAM is below", "value": "is_below"},
                 {"label": "Free VRAM is above", "value": "is_above"},
             ]},
            {"name": "margin_pct", "type": "number", "title": "Re-arm Margin (pp)", "default": 5,
             "description": "Only used by the crossing directions; ignored by 'is below'/'is above'."},
            {"name": "poll_interval_s", "type": "number", "title": "Poll Interval (seconds)", "default": 10},
            {"name": "hold_s", "type": "number", "title": "Hold Duration (seconds)", "default": 0,
             "description": "The condition must stay true for this long before firing. 0 fires immediately."},
            {"name": "require_generation_idle", "type": "checkbox", "title": "Only when no generation is running",
             "default": False},
        ],
        outputs=(
            NodeField("free_vram_mb", "number", "Free VRAM (MB)", "Free VRAM when the threshold tripped.", 1800),
            NodeField("total_vram_mb", "number", "Total VRAM (MB)", "Total VRAM on the device.", 24000),
            NodeField("free_vram_pct", "number", "Free VRAM (%)", "Free VRAM as a percentage.", 7.5),
            NodeField("threshold_pct", "number", "Threshold (%)", "The configured threshold.", 20),
            NodeField("direction", "string", "Direction", "below | above.", "below"),
        ),
    ))

    registry.register(NodeTypeSpec(
        key="trigger.manual",
        kind="trigger",
        title="Manual",
        description="Fires only when triggered explicitly (Run Now).",
        icon="play",
        category="events",
        config_schema=[],
        # Fires whatever payload the Run Now caller supplied.
        dynamic_outputs=True,
    ))
