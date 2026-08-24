"""Hook points owned by the model index domain."""

from src.platform.plugins.hooks import hooks_registry

MODEL_INDEX_HOOKS = hooks_registry.declare(
    "model_index", "backend",
    "before_index", "after_index",
    "before_delete", "after_delete",
    "before_download", "after_download",
    "before_assign", "after_assign",
    "before_unassign", "after_unassign",
    "before_update_tags", "after_update_tags",
    "before_fetch_info", "after_fetch_info",
    specs={
        "before_index": {
            "description": "Fired before a background model-indexing scan starts; can block it.",
            "payload": {
                "action": {"type": "str", "description": "Always the literal string 'start_indexing'"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Prevent indexing while another maintenance operation is in progress"],
        },
        "after_index": {
            "description": "Fired after a background model-indexing scan completes.",
            "payload": {
                "result": {"type": "Any", "description": "Return value of ModelIndexer.index_models(), shape depends on the indexer implementation"},
            },
            "use_when": ["Log or report indexing results"],
        },
        "before_delete": {
            "description": "Fired before a model is removed from the index (file on disk is untouched); can block the deletion.",
            "payload": {
                "model_id": {"type": "str", "description": "ID of the model to remove from the index"},
                "filename": {"type": "str", "description": "Filename of the model"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Veto removing a model still assigned to users or referenced by presets"],
        },
        "after_delete": {
            "description": "Fired after a model has been removed from the index.",
            "payload": {
                "model_id": {"type": "str", "description": "ID of the model that was removed"},
                "filename": {"type": "str", "description": "Filename of the model"},
            },
            "use_when": ["Sync an external catalog after a model is removed from the index"],
        },
        "before_download": {
            "description": "Fired before a model download-and-index job starts; can block it.",
            "payload": {
                "name": {"type": "str", "description": "Model name"},
                "link": {"type": "str", "description": "Download URL"},
                "size": {"type": "str", "description": "Human-readable size string from the request"},
                "sha256": {"type": "str", "description": "Expected SHA256 hash for verification"},
                "model_type": {"type": "str", "description": "Model type, e.g. 'checkpoint'"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Enforce a disk-quota or source-URL allowlist policy before downloading a model"],
        },
        "after_download": {
            "description": "Fired after a model has been downloaded, verified, and indexed. Only fires on success - not called if the download/hash-verification/indexing fails.",
            "payload": {
                "name": {"type": "str", "description": "Model name"},
                "model_id": {"type": "str", "description": "ID assigned to the newly indexed model"},
                "file_path": {"type": "str", "description": "Absolute path where the model file was saved"},
            },
            "use_when": ["Notify when a new model finishes downloading and becomes available"],
        },
        "before_assign": {
            "description": "Fired before a model is assigned to a user; can block the assignment.",
            "payload": {
                "model_id": {"type": "str", "description": "Model to assign"},
                "user_id": {"type": "str", "description": "User to assign the model to"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Enforce per-user model-access limits or licensing checks"],
        },
        "after_assign": {
            "description": "Fired after a model has been assigned to a user.",
            "payload": {
                "model_id": {"type": "str", "description": "Model that was assigned"},
                "user_id": {"type": "str", "description": "User the model was assigned to"},
                "assignment_id": {"type": "str", "description": "ID of the created assignment record"},
            },
            "use_when": ["Notify a user their model access changed"],
        },
        "before_unassign": {
            "description": "Fired before a model assignment is removed from a user; can block it.",
            "payload": {
                "model_id": {"type": "str", "description": "Model to unassign"},
                "user_id": {"type": "str", "description": "User to remove the assignment from"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Veto revoking access to a model still in active use"],
        },
        "after_unassign": {
            "description": "Fired after a model assignment has been removed from a user.",
            "payload": {
                "model_id": {"type": "str", "description": "Model that was unassigned"},
                "user_id": {"type": "str", "description": "User the assignment was removed from"},
            },
            "use_when": ["Sync external entitlement systems when access is revoked"],
        },
        "before_update_tags": {
            "description": "Fired before a model's tag set is replaced; can block the update.",
            "payload": {
                "model_id": {"type": "str", "description": "Model being retagged"},
                "tag_ids": {"type": "List[str]", "description": "New full set of tag IDs (all must be MODEL-type tags)"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Enforce a tagging policy for models (e.g. require a base-model tag)"],
        },
        "after_update_tags": {
            "description": "Fired after a model's tags have been replaced.",
            "payload": {
                "model_id": {"type": "str", "description": "Model that was retagged"},
                "tag_ids": {"type": "List[str]", "description": "Tag IDs now applied"},
            },
            "use_when": ["Sync tag changes to an external search index"],
        },
        "before_fetch_info": {
            "description": "Fired before a background job fetches provider metadata (e.g. CivitAI info) for models; can block it.",
            "payload": {
                "provider": {"type": "str", "description": "Provider name to fetch from"},
                "model_ids": {"type": "Optional[List[str]]", "description": "Specific model IDs to process, or None for all eligible models"},
                "force_refresh": {"type": "bool", "description": "If True, refresh all models even if they already have provider info"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Rate-limit or gate provider metadata fetches"],
        },
        "after_fetch_info": {
            "description": "Fired after a provider metadata fetch job completes.",
            "payload": {
                "provider": {"type": "str", "description": "Provider that was queried"},
                "successful": {"type": "int", "description": "Number of models successfully updated with provider info"},
                "failed": {"type": "int", "description": "Number of models that failed or had no match"},
            },
            "use_when": ["Report the outcome of a bulk metadata-enrichment job"],
        },
    },
)
