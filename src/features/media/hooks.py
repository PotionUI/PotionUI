"""Hook points owned by the media domain."""

from src.platform.plugins.hooks import hooks_registry

MEDIA_HOOKS = hooks_registry.declare(
    "media", "backend",
    "before_upload", "after_upload",
    "before_delete", "after_delete",
    "before_serve",
    specs={
        "before_upload": {
            "description": "Fired before an uploaded media file (not part of a generation) is written to disk; can block the upload.",
            "payload": {
                "filename": {"type": "str", "description": "Original filename from the client"},
                "content_type": {"type": "Optional[str]", "description": "MIME type of the uploaded file"},
                "size": {"type": "int", "description": "Size of the uploaded file in bytes"},
                "user_id": {"type": "Optional[str]", "description": "Uploading user, if authenticated"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Enforce file-size or content-type policy on standalone media uploads"],
        },
        "after_upload": {
            "description": "Fired after a media file has been saved to the uploads directory.",
            "payload": {
                "filename": {"type": "str", "description": "Generated unique filename on disk"},
                "path": {"type": "str", "description": "Absolute filesystem path where the file was saved, or its storage key when the configured storage backend has no local path (e.g. S3)"},
                "relative_path": {"type": "str", "description": "Path relative to the user's storage directory"},
                "size": {"type": "int", "description": "File size in bytes"},
                "user_id": {"type": "Optional[str]", "description": "Uploading user, if authenticated"},
            },
            "use_when": ["Kick off post-processing on a standalone upload, e.g. virus scanning or thumbnailing"],
        },
        "before_delete": {
            "description": "Fired before all media files for a generation are deleted; can block the deletion.",
            "payload": {
                "generation_id": {"type": "str", "description": "Generation whose media is being deleted"},
                "user_id": {"type": "str", "description": "User requesting the deletion"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Veto media deletion for a generation still referenced elsewhere"],
        },
        "after_delete": {
            "description": "Fired after a generation's media files have been deleted from disk and the database.",
            "payload": {
                "generation_id": {"type": "str", "description": "Generation whose media was deleted"},
                "deleted_files": {"type": "int", "description": "Number of files successfully deleted from disk"},
                "failed_files": {"type": "int", "description": "Number of files that failed to delete"},
                "user_id": {"type": "str", "description": "User who requested the deletion"},
            },
            "use_when": ["Invalidate a CDN cache or external index after media is removed"],
        },
        "before_serve": {
            "description": "Fired before a generation's media file (or thumbnail) is served to a client; can block serving.",
            "payload": {
                "generation_id": {"type": "str", "description": "Generation the file belongs to"},
                "filename": {"type": "str", "description": "Requested filename"},
                "size": {"type": "Optional[str]", "description": "Requested thumbnail size ('small'/'medium'/'large'), or None for original"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Enforce access control or watermarking policy before serving a media file"],
            "example": (
                "# hooks/media_hooks.py\n"
                "def on_before_serve(context: HookContext) -> HookContext:\n"
                "    if is_flagged(context.data[\"generation_id\"]):\n"
                "        context.data[\"blocked\"] = True\n"
                "        context.data[\"block_reason\"] = \"Content under review\"\n"
                "    return context\n"
            ),
        },
    },
)
