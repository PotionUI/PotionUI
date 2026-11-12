"""
Preset operations - business logic for preset queries, installation,
assignment, and admin-set configuration/form-overrides.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function that takes a `PresetCollaborators` bundle (`src.features.presets.
collaborators`) as its leading argument, followed by the operation's own
parameters. Callers (`PresetController`, generation's pipeline preview, the
setup executors, chat/LLM tool context) hold the bundle and pass it in;
nothing here is stored across calls.

Shape rule: one module per concern (`query`, `installation`, `assignment`,
`configuration`, `form_overrides`), each re-exported here as the public
surface - split a module before it outgrows ~200 lines rather than let it
absorb an unrelated concern. Callers import from the package
(`from src.features.presets import operations`), never from a submodule
directly.
"""
from src.features.presets.operations.access import (
    is_admin_user,
    preset_available_to_user,
    require_preset_access,
)
from src.features.presets.operations.query import (
    list_presets,
    get_preset,
    get_available_modes,
    apply_user_model_scope,
    get_form_schema,
    get_pipeline,
    reload_preset,
)
from src.features.presets.operations.installation import (
    install_preset,
    uninstall_preset,
)
from src.features.presets.operations.assignment import (
    assign_preset_to_users,
    unassign_preset_from_user,
    get_preset_assignments,
)
from src.features.presets.operations.configuration import (
    get_preset_configuration,
    set_preset_configuration,
)
from src.features.presets.operations.form_overrides import (
    get_form_overrides_inventory,
    set_form_overrides,
)
from src.features.presets.operations.requirements import (
    get_preset_requirements,
    refresh_mode_requirements,
)
from src.features.presets.operations.display import (
    get_admin_detail,
    set_display,
    upload_display_cover,
)
from src.features.presets.operations.mirrors import (
    create_mirror,
    set_mirror_modes,
)
from src.features.presets.operations.picker_state import (
    get_picker_state,
    pin_preset,
    unpin_preset,
    push_recent_preset,
)

__all__ = [
    "is_admin_user",
    "preset_available_to_user",
    "require_preset_access",
    "list_presets",
    "get_preset",
    "get_available_modes",
    "apply_user_model_scope",
    "get_form_schema",
    "get_pipeline",
    "reload_preset",
    "install_preset",
    "uninstall_preset",
    "assign_preset_to_users",
    "unassign_preset_from_user",
    "get_preset_assignments",
    "get_preset_configuration",
    "set_preset_configuration",
    "get_form_overrides_inventory",
    "set_form_overrides",
    "get_preset_requirements",
    "refresh_mode_requirements",
    "get_picker_state",
    "pin_preset",
    "unpin_preset",
    "push_recent_preset",
    "get_admin_detail",
    "set_display",
    "upload_display_cover",
    "create_mirror",
    "set_mirror_modes",
]
