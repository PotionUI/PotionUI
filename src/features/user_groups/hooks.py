"""Hook points owned by the user group domain."""

from src.platform.plugins.hooks import hooks_registry

USER_GROUP_HOOKS = hooks_registry.declare(
    "user_group", "backend",
    "before_create", "after_create",
    "before_update", "after_update",
    "before_delete", "after_delete",
    "before_add_member", "after_add_member",
    "before_remove_member", "after_remove_member",
    "before_assign_resource", "after_assign_resource",
    "before_unassign_resource", "after_unassign_resource",
    specs={
        "before_create": {
            "description": "Fired before a user group is created. Can rewrite name/description or block creation.",
            "payload": {
                "name": {"type": "str", "description": "Requested group name"},
                "description": {"type": "Optional[str]", "description": "Requested group description"},
                "user_id": {"type": "str", "description": "Admin performing the creation"},
            },
            "mutable": ["name", "description", "blocked", "block_reason"],
            "use_when": ["Enforce a group naming policy or limit the number of groups before creation"],
        },
        "after_create": {
            "description": "Fired after a user group has been created.",
            "payload": {
                "group_id": {"type": "str", "description": "Newly created group's ID"},
                "name": {"type": "str", "description": "Group name"},
                "description": {"type": "Optional[str]", "description": "Group description"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging after group creation"],
        },
        "before_update": {
            "description": "Fired before a user group's name/description is updated. Can rewrite the new values or block the update.",
            "payload": {
                "group_id": {"type": "str", "description": "Group being updated"},
                "old_name": {"type": "str", "description": "Current group name"},
                "new_name": {"type": "Optional[str]", "description": "Requested new group name, if changing"},
                "old_description": {"type": "Optional[str]", "description": "Current group description"},
                "new_description": {"type": "Optional[str]", "description": "Requested new description, if changing"},
                "user_id": {"type": "str", "description": "Admin performing the update"},
            },
            "mutable": ["new_name", "new_description", "blocked", "block_reason"],
            "use_when": ["Validate or block renames/description changes on protected groups"],
        },
        "after_update": {
            "description": "Fired after a user group has been updated.",
            "payload": {
                "group_id": {"type": "str", "description": "Updated group's ID"},
                "name": {"type": "str", "description": "Group's current name"},
                "description": {"type": "Optional[str]", "description": "Group's current description"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging after group update"],
        },
        "before_delete": {
            "description": "Fired before a user group is deleted. Can block deletion.",
            "payload": {
                "group_id": {"type": "str", "description": "Group to be deleted"},
                "name": {"type": "str", "description": "Group name"},
                "user_id": {"type": "str", "description": "Admin performing the deletion"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Prevent deletion of groups a plugin considers protected (e.g. a default group it manages)"],
        },
        "after_delete": {
            "description": "Fired after a user group has been deleted.",
            "payload": {
                "group_id": {"type": "str", "description": "Deleted group's ID"},
                "name": {"type": "str", "description": "Deleted group's name"},
            },
            "mutable": [],
            "use_when": ["Notification-only: cleanup of plugin-owned data tied to a deleted group"],
        },
        "before_add_member": {
            "description": "Fired once per user ID when adding members to a group (inside a loop - one call per user_id). Can block that single member's addition; other members in the same batch are unaffected.",
            "payload": {
                "group_id": {"type": "str", "description": "Group members are being added to"},
                "user_id": {"type": "str", "description": "User being added to the group"},
                "admin_id": {"type": "str", "description": "Admin performing the addition"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Block adding a specific user to a group based on custom eligibility rules"],
        },
        "after_add_member": {
            "description": "Fired once per user ID after that member has been added to the group. Only fires for members that were actually added (skips ones already in the group).",
            "payload": {
                "group_id": {"type": "str", "description": "Group the member was added to"},
                "user_id": {"type": "str", "description": "User that was added"},
                "member_id": {"type": "str", "description": "ID of the created membership record"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging or provisioning resources when a member joins a group"],
        },
        "before_remove_member": {
            "description": "Fired before a member is removed from a group. Can block the removal.",
            "payload": {
                "group_id": {"type": "str", "description": "Group the member would be removed from"},
                "user_id": {"type": "str", "description": "User being removed"},
                "admin_id": {"type": "str", "description": "Admin performing the removal"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Prevent removal of a group's last/protected member"],
        },
        "after_remove_member": {
            "description": "Fired after a member has been removed from a group.",
            "payload": {
                "group_id": {"type": "str", "description": "Group the member was removed from"},
                "user_id": {"type": "str", "description": "User that was removed"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging after member removal"],
        },
        "before_assign_resource": {
            "description": (
                "Fired once per resource ID when assigning presets, LLM configs, or models to a group "
                "(inside a loop - one call per resource_id, shared across three call sites keyed by resource_type). "
                "Can block that single resource's assignment."
            ),
            "payload": {
                "group_id": {"type": "str", "description": "Group the resource is being assigned to"},
                "resource_type": {"type": "str", "description": "'preset', 'llm', or 'model'"},
                "resource_id": {"type": "str", "description": "ID of the preset/LLM config/model being assigned"},
                "admin_id": {"type": "str", "description": "Admin performing the assignment"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Restrict which presets/LLMs/models can be assigned to a group based on custom policy"],
        },
        "after_assign_resource": {
            "description": "Fired once per resource ID after that resource has been assigned to the group. Only fires for resources actually assigned (skips duplicates).",
            "payload": {
                "group_id": {"type": "str", "description": "Group the resource was assigned to"},
                "resource_type": {"type": "str", "description": "'preset', 'llm', or 'model'"},
                "resource_id": {"type": "str", "description": "ID of the assigned resource"},
                "assignment_id": {"type": "str", "description": "ID of the created assignment record"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging after a resource is assigned to a group"],
        },
        "before_unassign_resource": {
            "description": "Fired before a preset/LLM/model is unassigned from a group. Can block the unassignment.",
            "payload": {
                "group_id": {"type": "str", "description": "Group the resource would be unassigned from"},
                "resource_type": {"type": "str", "description": "'preset', 'llm', or 'model'"},
                "resource_id": {"type": "str", "description": "ID of the resource being unassigned"},
                "admin_id": {"type": "str", "description": "Admin performing the unassignment"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Prevent unassigning a resource a group still depends on"],
        },
        "after_unassign_resource": {
            "description": "Fired after a preset/LLM/model has been unassigned from a group.",
            "payload": {
                "group_id": {"type": "str", "description": "Group the resource was unassigned from"},
                "resource_type": {"type": "str", "description": "'preset', 'llm', or 'model'"},
                "resource_id": {"type": "str", "description": "ID of the unassigned resource"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging after resource unassignment"],
        },
    },
)
