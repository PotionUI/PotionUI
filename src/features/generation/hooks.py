"""Hook points owned by the generation domain."""

from src.platform.plugins.hooks import hooks_registry

GENERATION_HOOKS = hooks_registry.declare(
    "generation", "backend",
    "before_start", "after_complete",
    "before_delete", "after_delete",
    "before_bulk_delete", "after_bulk_delete",
    "before_upload", "after_upload",
    "before_update_tags", "after_update_tags",
    specs={
        "before_start": {
            "description": (
                "Fired just before a generation record is created, after backend "
                "selection. If a handler replaces form_data, it is re-validated "
                "against the preset's form schema (the same bind_form check the "
                "original submission went through) before anything is persisted - "
                "a hook-modified form_data that no longer satisfies required "
                "fields/ranges/options fails the whole generation with a "
                "FormBindingError, it does not proceed with invalid values. "
                "Carries a VRAM snapshot (free/total plus a best-effort estimate "
                "of what this request needs) so a handler can decide whether to "
                "free room before the pipeline runs."
            ),
            "payload": {
                "generation_id": {"type": "str", "description": "ULID assigned to the new generation"},
                "preset_id": {"type": "str", "description": "Preset the generation was requested against"},
                "form_data": {"type": "dict", "description": "Submitted form fields for the generation request"},
                "backend_id": {"type": "str", "description": "ID of the backend selected to run the generation"},
                "user_id": {"type": "Optional[str]", "description": "Owning user, if authenticated"},
                "vram_free_gb": {"type": "Optional[float]", "description": "Free VRAM in GB (NVML), or null if no GPU monitor is wired or the read failed"},
                "vram_total_gb": {"type": "Optional[float]", "description": "Total VRAM in GB, or null on the same conditions as vram_free_gb"},
                "vram_estimate_gb": {"type": "Optional[float]", "description": "Best-effort LOWER-BOUND estimate of the VRAM this request needs, in GB: summed on-disk size of the model:<id> references the form carries (weight-load margin) plus a resolution/frames-scaled activation term. Covers every model-picker reference - for native presets the diffusion model, text encoder, VAE and LoRAs; a component a preset pins in config (not a picker) is outside form refs and is not counted. Null ONLY when nothing is resolvable (no model references, or none of their sizes are indexed); the automatic pattern treats null as 'evict, when in doubt'"},
            },
            "mutable": ["form_data"],
            "use_when": [
                "Rewrite or inject form_data before the pipeline is built (e.g. apply a default LoRA, inject credentials)",
                "Record/audit a generation request before it starts",
                "Free VRAM before the pipeline runs (e.g. evict an external model) by comparing vram_free_gb against vram_estimate_gb",
            ],
            "example": (
                "# manifest.yml\n"
                "hooks:\n"
                "  backend:\n"
                "    - hook: \"generation.before_start\"\n"
                "      handler: \"hooks.generation_hooks.on_before_start\"\n\n"
                "# hooks/generation_hooks.py\n"
                "def on_before_start(context: HookContext) -> HookContext:\n"
                "    context.data[\"form_data\"][\"steps\"] = 30  # force a default\n"
                "    return context\n"
            ),
        },
        "after_complete": {
            "description": "Fired when a generation reaches a terminal state (completed/failed/cancelled), before the WebSocket callback notifies clients.",
            "payload": {
                "generation_id": {"type": "str", "description": "ID of the generation that finished"},
                "status": {"type": "str", "description": "Terminal GenerationState value (e.g. 'completed', 'failed', 'cancelled')"},
                "duration": {"type": "float", "description": "Wall-clock seconds from creation to completion"},
                "outputs": {"type": "list", "description": "Always empty at this call site; not populated from the database"},
                "preset_id": {"type": "Optional[str]", "description": "Preset the generation used"},
                "user_id": {"type": "Optional[str]", "description": "Owning user, if found"},
            },
            "use_when": [
                "Send a notification (webhook, email) when a generation finishes",
                "Record metrics/analytics keyed by preset or duration",
            ],
            "example": (
                "# hooks/generation_hooks.py\n"
                "def on_after_complete(context: HookContext) -> HookContext:\n"
                "    logger.info(f\"generation {context.data['generation_id']} -> {context.data['status']}\")\n"
                "    return context\n"
            ),
        },
        "before_delete": {
            "description": "Fired before a single generation is deleted; can block the deletion.",
            "payload": {
                "generation_id": {"type": "str", "description": "Generation to be deleted"},
                "user_id": {"type": "str", "description": "User requesting the deletion (ownership already verified)"},
                "preset_id": {"type": "Optional[str]", "description": "Preset the generation used"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": [
                "Veto deletion of a generation that is referenced elsewhere (e.g. pinned in a gallery)",
            ],
        },
        "after_delete": {
            "description": "Fired after a single generation and its files have been deleted.",
            "payload": {
                "generation_id": {"type": "str", "description": "Generation that was deleted"},
                "user_id": {"type": "str", "description": "User who requested the deletion"},
                "files_deleted_fs": {"type": "int", "description": "Number of files removed from disk"},
                "files_deleted_db": {"type": "int", "description": "Number of file rows removed from the database"},
            },
            "use_when": ["React to cleanup, e.g. invalidate a CDN cache entry for the deleted files"],
        },
        "before_bulk_delete": {
            "description": "Fired before a bulk-delete operation runs; can block the entire operation.",
            "payload": {
                "generation_ids": {"type": "List[str]", "description": "IDs targeted for deletion"},
                "user_id": {"type": "str", "description": "User requesting the deletion"},
                "count": {"type": "int", "description": "Number of generations targeted"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Block a bulk delete above a size threshold, or one touching protected generations"],
        },
        "after_bulk_delete": {
            "description": "Fired after a bulk-delete operation completes.",
            "payload": {
                "generation_ids": {"type": "List[str]", "description": "IDs that were targeted"},
                "user_id": {"type": "str", "description": "User who requested the deletion"},
                "deleted_count": {"type": "int", "description": "Number successfully deleted"},
                "failed_count": {"type": "int", "description": "Number that failed to delete"},
                "total_files_deleted": {"type": "int", "description": "Total files removed from disk across all deletions"},
            },
            "use_when": ["Log/report the outcome of a bulk cleanup job"],
        },
        "before_upload": {
            "description": "Fired before uploaded files are turned into a completed generation record; can block the upload.",
            "payload": {
                "user_id": {"type": "str", "description": "Uploading user"},
                "file_count": {"type": "int", "description": "Number of files submitted"},
                "tag_ids": {"type": "List[str]", "description": "Tag IDs requested to be applied"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Enforce upload quotas or file-count limits per user"],
        },
        "after_upload": {
            "description": "Fired after uploaded files have been saved and associated with a new generation record.",
            "payload": {
                "generation_id": {"type": "str", "description": "ID of the generation created for the upload"},
                "user_id": {"type": "str", "description": "Uploading user"},
                "file_count": {"type": "int", "description": "Number of files actually saved (may be less than requested if some were skipped)"},
                "tag_ids": {"type": "List[str]", "description": "Tag IDs applied, if any"},
            },
            "use_when": ["Kick off post-processing (e.g. thumbnailing, virus scan) for uploaded media"],
        },
        "before_update_tags": {
            "description": "Fired before a generation's tag set is replaced; can block the update.",
            "payload": {
                "generation_id": {"type": "str", "description": "Generation being retagged"},
                "user_id": {"type": "str", "description": "User requesting the update"},
                "tag_ids": {"type": "List[str]", "description": "New full set of tag IDs to apply"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Enforce tag policy (e.g. require at least one content-rating tag)"],
        },
        "after_update_tags": {
            "description": "Fired after a generation's tags have been replaced.",
            "payload": {
                "generation_id": {"type": "str", "description": "Generation that was retagged"},
                "user_id": {"type": "str", "description": "User who made the update"},
                "tag_ids": {"type": "List[str]", "description": "Tag IDs now applied"},
            },
            "use_when": ["Sync tag changes to an external index/search system"],
        },
    },
)

PIPE_HOOKS = hooks_registry.declare(
    "pipe", "backend",
    "before_execute", "after_execute",
    specs={
        "before_execute": {
            "description": "Fired immediately before each pipe in the pipeline processes its input.",
            "payload": {
                "pipe_id": {"type": "int", "description": "Index of the pipe within the preset's pipeline list"},
                "pipe_name": {"type": "str", "description": "Name of the pipe (e.g. 'generator', 'checkpoint_loader')"},
                "pipe_config": {"type": "dict", "description": "The pipe's resolved configuration dict"},
                "inputs": {"type": "dict", "description": "Input parameters about to be passed to the pipe"},
                "generation_id": {"type": "str", "description": "ID of the generation being processed"},
            },
            "mutable": ["inputs", "pipe_config"],
            "use_when": [
                "Inject or rewrite inputs for a specific pipe (e.g. inject an API credential into a remote pipe's config)",
                "Log/trace per-pipe inputs for debugging",
            ],
            "example": (
                "# hooks/pipe_hooks.py\n"
                "def on_before_execute(context: HookContext) -> HookContext:\n"
                "    if context.data[\"pipe_name\"] == \"generator\":\n"
                "        context.data[\"inputs\"][\"seed\"] = 42\n"
                "    return context\n"
            ),
        },
        "after_execute": {
            "description": "Fired immediately after each pipe finishes processing, only if the pipe returned a result.",
            "payload": {
                "pipe_id": {"type": "int", "description": "Index of the pipe within the preset's pipeline list"},
                "pipe_name": {"type": "str", "description": "Name of the pipe"},
                "outputs": {"type": "Any", "description": "The pipe's PipeResult.output value"},
                "duration": {"type": "float", "description": "Seconds spent executing the pipe"},
            },
            "mutable": ["outputs"],
            "use_when": [
                "Post-process or annotate a pipe's outputs before they're passed to the next pipe",
                "Record per-pipe timing metrics",
            ],
        },
    },
)

# Note: "output.transform" existed in the previous hook enum with zero call
# sites and no manifest references - dropped rather than re-declared.

OUTPUT_TYPE_HOOKS = hooks_registry.declare(
    "output_type", "backend",
    "register",  # Plugins register OutputTypeSpec entries
    specs={
        "register": {
            "description": "Fired once at app startup to let plugins register additional GenerationOutput types on the shared output_type_registry.",
            "payload": {
                "registry": {"type": "OutputTypeRegistry", "description": "Shared registry; call registry.register(OutputTypeSpec(...)) to add a type"},
            },
            "mutable": ["registry"],
            "use_when": [
                "Add a new kind of generation output (custom handler, WebSocket message type, and serializer) so the pipeline and frontend can recognize it",
            ],
            "example": (
                "# manifest.yml\n"
                "hooks:\n"
                "  backend:\n"
                "    - hook: \"output_type.register\"\n"
                "      handler: \"hooks.output_hooks.register_output_type\"\n\n"
                "# hooks/output_hooks.py\n"
                "def register_output_type(context: HookContext) -> HookContext:\n"
                "    registry = context.data[\"registry\"]\n"
                "    registry.register(OutputTypeSpec(output_cls=MyOutput, key=\"my_output\",\n"
                "        message_type=\"my_output_update\", serializer=my_serializer))\n"
                "    return context\n"
            ),
        },
    },
)
