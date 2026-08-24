"""Hook points owned by the tag domain."""

from src.platform.plugins.hooks import hooks_registry

TAG_HOOKS = hooks_registry.declare(
    "tag", "backend",
    "before_create", "after_create",
    "before_update", "after_update",
    "before_delete", "after_delete",
    specs={
        "before_create": {
            "description": "Fired before a tag row is created. Can rewrite `name` or block creation.",
            "payload": {
                "name": {"type": "str", "description": "Requested tag name"},
                "type": {"type": "str", "description": "'MODEL' or 'GENERATION' - see TagType in src/features/tags/dto.py"},
                "user_id": {"type": "str", "description": "Effective owning user id (GENERATION tags are per-user, MODEL tags are shared)"},
            },
            "mutable": ["name", "blocked", "block_reason"],
            "use_when": [
                "Normalizing tag names (casing, trimming, slugification) before creation",
                "Blocking creation of reserved/profane tag names",
            ],
        },
        "after_create": {
            "description": "Fired after the tag has been persisted.",
            "payload": {
                "tag_id": {"type": "str", "description": "Newly created tag's id"},
                "name": {"type": "str", "description": "Tag name"},
                "type": {"type": "str", "description": "'MODEL' or 'GENERATION'"},
                "user_id": {"type": "str", "description": "Owning user id"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging after tag creation"],
        },
        "before_update": {
            "description": "Fired before a tag rename is applied. Can rewrite `new_name` or block the update.",
            "payload": {
                "tag_id": {"type": "str", "description": "Tag being updated"},
                "old_name": {"type": "str", "description": "Current tag name"},
                "new_name": {"type": "str", "description": "Requested new tag name"},
                "type": {"type": "str", "description": "'MODEL' or 'GENERATION'"},
                "user_id": {"type": "str", "description": "Owning user id"},
            },
            "mutable": ["new_name", "blocked", "block_reason"],
            "use_when": ["Validating or normalizing the renamed value before it's persisted"],
        },
        "after_update": {
            "description": "Fired after the tag rename has been persisted.",
            "payload": {
                "tag_id": {"type": "str", "description": "Updated tag's id"},
                "name": {"type": "str", "description": "Tag's current name"},
                "type": {"type": "str", "description": "'MODEL' or 'GENERATION'"},
                "user_id": {"type": "str", "description": "Owning user id"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging after tag rename"],
        },
        "before_delete": {
            "description": "Fired before a tag is deleted. Can block the deletion.",
            "payload": {
                "tag_id": {"type": "str", "description": "Tag to be deleted"},
                "name": {"type": "str", "description": "Tag name"},
                "type": {"type": "str", "description": "'MODEL' or 'GENERATION'"},
                "user_id": {"type": "str", "description": "Owning user id"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Blocking deletion of tags still referenced by other plugin-managed data"],
        },
        "after_delete": {
            "description": "Fired after the tag has been removed from the repository.",
            "payload": {
                "tag_id": {"type": "str", "description": "Deleted tag's id"},
                "name": {"type": "str", "description": "Tag's name at time of deletion"},
                "type": {"type": "str", "description": "'MODEL' or 'GENERATION'"},
                "user_id": {"type": "str", "description": "Owning user id"},
            },
            "mutable": [],
            "use_when": ["Notification-only: cleanup of references to the deleted tag in plugin-managed data"],
        },
    },
)
