"""Hook points owned by the download queue manager."""

from src.platform.plugins.hooks import hooks_registry

DOWNLOAD_HOOKS = hooks_registry.declare(
    "download", "backend",
    "before_queue", "after_queue",
    "before_pause", "after_pause",
    "before_resume", "after_resume",
    "before_cancel", "after_cancel",
    "before_delete", "after_delete",
    "before_clear", "after_clear",
    specs={
        "before_queue": {
            "description": "Fired before a download record is created - shared by queue_model_download, queue_media_download and queue_hf_repo_download (distinguished by `type`). Can rewrite url/destination_dir/filename/tags or block queueing.",
            "payload": {
                "url": {"type": "str", "description": "Source URL to download from (for hf_repo: the repo's canonical URL)"},
                "type": {"type": "str", "description": "'model', 'media' or 'hf_repo'"},
                "destination_dir": {"type": "Optional[str]", "description": "Requested destination directory, falls back to a type-specific default if empty"},
                "filename": {"type": "Optional[str]", "description": "Requested filename override, derived from the URL if empty"},
                "tags": {"type": "Optional[List[str]]", "description": "Tags to apply (model downloads only - absent for media)"},
                "checksum_sha256": {"type": "Optional[str]", "description": "Expected checksum (model downloads only)"},
                "provider_id": {"type": "Optional[str]", "description": "Provider id for authenticated downloads (model downloads only)"},
                "created_by": {"type": "Optional[str]", "description": "User id that queued the download"},
            },
            "mutable": ["url", "destination_dir", "filename", "tags", "blocked", "block_reason"],
            "use_when": [
                "Rewriting the destination path/filename per a custom naming convention",
                "Blocking downloads from disallowed hosts",
            ],
            "example": (
                "def handler(ctx):\n"
                "    if not ctx.get('url', '').startswith('https://'):\n"
                "        ctx.set('blocked', True)\n"
                "        ctx.set('block_reason', 'Only HTTPS sources are allowed')\n"
                "    return ctx"
            ),
        },
        "after_queue": {
            "description": "Fired after the download record has been created and enqueued to the worker.",
            "payload": {
                "download_id": {"type": "str", "description": "Newly created download's id"},
                "url": {"type": "str", "description": "Source URL (post-hook value)"},
                "type": {"type": "str", "description": "'model', 'media' or 'hf_repo'"},
                "filename": {"type": "str", "description": "Resolved filename"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging or external tracking of queued downloads"],
        },
        "before_pause": {
            "description": "Fired before an active download is paused. Can block the pause.",
            "payload": {
                "download_id": {"type": "str", "description": "Download to be paused"},
                "filename": {"type": "str", "description": "Download's filename"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Blocking pause of downloads that must run to completion uninterrupted"],
        },
        "after_pause": {
            "description": "Fired after the download has been paused.",
            "payload": {
                "download_id": {"type": "str", "description": "Paused download's id"},
                "filename": {"type": "str", "description": "Download's filename"},
            },
            "mutable": [],
            "use_when": ["Notification-only"],
        },
        "before_resume": {
            "description": "Fired before a paused/failed download is resumed. Can block the resume.",
            "payload": {
                "download_id": {"type": "str", "description": "Download to be resumed"},
                "filename": {"type": "str", "description": "Download's filename"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Blocking resume based on custom retry policy (e.g. max retry count)"],
        },
        "after_resume": {
            "description": "Fired after the download has been resumed.",
            "payload": {
                "download_id": {"type": "str", "description": "Resumed download's id"},
                "filename": {"type": "str", "description": "Download's filename"},
            },
            "mutable": [],
            "use_when": ["Notification-only"],
        },
        "before_cancel": {
            "description": "Fired before an active/pending download is cancelled. Can block the cancel.",
            "payload": {
                "download_id": {"type": "str", "description": "Download to be cancelled"},
                "filename": {"type": "str", "description": "Download's filename"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Blocking cancellation of downloads a plugin considers critical"],
        },
        "after_cancel": {
            "description": "Fired after the download has been cancelled.",
            "payload": {
                "download_id": {"type": "str", "description": "Cancelled download's id"},
                "filename": {"type": "str", "description": "Download's filename"},
            },
            "mutable": [],
            "use_when": ["Notification-only"],
        },
        "before_delete": {
            "description": "Fired before a download record is deleted (active downloads are cancelled first). Can block the deletion.",
            "payload": {
                "download_id": {"type": "str", "description": "Download to be deleted"},
                "filename": {"type": "str", "description": "Download's filename"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Blocking deletion of a download record still referenced elsewhere"],
        },
        "after_delete": {
            "description": "Fired after the download record has been removed from the repository.",
            "payload": {
                "download_id": {"type": "str", "description": "Deleted download's id"},
                "filename": {"type": "str", "description": "Download's filename at time of deletion"},
            },
            "mutable": [],
            "use_when": ["Notification-only: cleanup of files/references tied to the deleted download record"],
        },
        "before_clear": {
            "description": "Fired before a bulk history clear - shared by clear_completed and clear_cancelled (distinguished by `clear_type`). Can block the clear.",
            "payload": {
                "clear_type": {"type": "str", "description": "'completed' or 'cancelled'"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Blocking bulk history clears, e.g. to preserve records for audit"],
        },
        "after_clear": {
            "description": "Fired after the matching downloads have been removed from history.",
            "payload": {
                "clear_type": {"type": "str", "description": "'completed' or 'cancelled'"},
                "count": {"type": "int", "description": "Number of download records removed"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging of bulk clears"],
        },
    },
)
