"""
User group administration operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function that takes exactly the collaborators it needs (`UserGroupRepository`,
`PluginRegistry`) as leading arguments, followed by the operation's own
parameters. `UserGroupController` (`routes.py`) holds the collaborators and
passes them in; nothing here is stored across calls.

Shape rule: one module per concern (`groups`, `members`, `resources`), plus
`guards` for the `require_admin`/`require_group_exists` preconditions every
operation opens with - each re-exported here as the public surface. `resources`
covers presets/LLMs/models together (one hook pair keyed by `resource_type`,
shared list/assign/unassign shape - see its docstring) rather than three
near-duplicate modules.
"""
from src.features.user_groups.operations.groups import (
    SystemGroupProtectedError,
    get_all_groups,
    create_group,
    get_group,
    update_group,
    delete_group,
)
from src.features.user_groups.operations.members import (
    get_group_members,
    add_members,
    remove_member,
    get_user_groups,
)
from src.features.user_groups.operations.resources import (
    get_group_presets,
    assign_presets,
    unassign_preset,
    get_group_llms,
    assign_llms,
    unassign_llm,
    get_group_models,
    assign_models,
    unassign_model,
)
from src.features.user_groups.operations.guards import require_admin, require_group_exists

__all__ = [
    "SystemGroupProtectedError",
    "get_all_groups",
    "create_group",
    "get_group",
    "update_group",
    "delete_group",
    "get_group_members",
    "add_members",
    "remove_member",
    "get_user_groups",
    "get_group_presets",
    "assign_presets",
    "unassign_preset",
    "get_group_llms",
    "assign_llms",
    "unassign_llm",
    "get_group_models",
    "assign_models",
    "unassign_model",
    "require_admin",
    "require_group_exists",
]
