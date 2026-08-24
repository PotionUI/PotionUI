"""Hook points owned by the preset domain (install/uninstall, assignment)."""

from src.platform.plugins.hooks import hooks_registry

PRESET_HOOKS = hooks_registry.declare(
    "preset", "backend",
    "before_install", "after_install",
    "before_uninstall", "after_uninstall",
    "before_assign", "after_assign",
    "before_unassign", "after_unassign",
    specs={
        "before_install": {
            "description": "Fired before an admin-selected preset is installed from the filesystem into the database. Can block installation.",
            "payload": {
                "preset_id": {"type": "str", "description": "ID of the preset to install"},
                "user_id": {"type": "str", "description": "Admin performing the installation"},
                "preset_name": {"type": "str", "description": "Preset's display name, resolved from the filesystem preset files"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Restrict which presets can be installed based on custom policy or licensing checks"],
        },
        "after_install": {
            "description": "Fired after a preset has been installed into the database.",
            "payload": {
                "preset_id": {"type": "str", "description": "ID of the installed preset"},
                "user_id": {"type": "str", "description": "Admin who performed the installation"},
                "preset_name": {"type": "str", "description": "Preset's display name"},
                "installed_preset_id": {"type": "str", "description": "ID of the created installed-preset database record"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging or provisioning follow-up actions after install"],
        },
        "before_uninstall": {
            "description": "Fired before an installed preset is removed, along with all its user assignments. Can block uninstallation.",
            "payload": {
                "preset_id": {"type": "str", "description": "ID of the preset to uninstall"},
                "user_id": {"type": "str", "description": "Admin performing the uninstallation"},
                "assignment_count": {"type": "int", "description": "Number of user assignments that will be removed"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Block uninstallation of a preset that is still actively required by other plugin-managed data"],
        },
        "after_uninstall": {
            "description": "Fired after a preset and its user assignments have been removed.",
            "payload": {
                "preset_id": {"type": "str", "description": "ID of the uninstalled preset"},
                "user_id": {"type": "str", "description": "Admin who performed the uninstallation"},
                "removed_assignments": {"type": "int", "description": "Number of user assignments that were removed"},
            },
            "mutable": [],
            "use_when": ["Notification-only: cleanup of plugin-owned data tied to the uninstalled preset"],
        },
        "before_assign": {
            "description": "Fired before a preset is assigned to a batch of users. Can rewrite the user_ids list or block the assignment.",
            "payload": {
                "preset_id": {"type": "str", "description": "Preset being assigned"},
                "user_ids": {"type": "List[str]", "description": "User IDs the preset will be assigned to"},
                "admin_id": {"type": "str", "description": "Admin performing the assignment"},
            },
            "mutable": ["user_ids", "blocked", "block_reason"],
            "use_when": ["Filter or restrict which users a preset can be assigned to (e.g. license seat limits)"],
        },
        "after_assign": {
            "description": "Fired after a preset has been assigned to users.",
            "payload": {
                "preset_id": {"type": "str", "description": "Preset that was assigned"},
                "user_ids": {"type": "List[str]", "description": "User IDs the preset was assigned to (post-hook value)"},
                "admin_id": {"type": "str", "description": "Admin who performed the assignment"},
                "assigned_count": {"type": "int", "description": "Number of assignment records actually created"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging or notifying assigned users"],
        },
        "before_unassign": {
            "description": "Fired before a preset assignment is removed from a single user. Can block the unassignment.",
            "payload": {
                "preset_id": {"type": "str", "description": "Preset being unassigned"},
                "user_id": {"type": "str", "description": "User the preset will be unassigned from"},
                "admin_id": {"type": "str", "description": "Admin performing the unassignment"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Prevent removing a preset assignment a user's active session still depends on"],
        },
        "after_unassign": {
            "description": "Fired after a preset assignment has been removed from a user.",
            "payload": {
                "preset_id": {"type": "str", "description": "Preset that was unassigned"},
                "user_id": {"type": "str", "description": "User the preset was unassigned from"},
                "admin_id": {"type": "str", "description": "Admin who performed the unassignment"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging after unassignment"],
        },
    },
)
