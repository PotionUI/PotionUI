"""
User Group module.

Handles user group management including CRUD operations for groups,
member management, and resource assignments (presets, LLMs, models).

`operations` (module-level functions over `UserGroupRepository` +
`PluginRegistry`) replaces the former `UserGroupManager` class - see that
package's docstring. `UserGroupController` (`src.features.user_groups.routes`)
is the sole caller.
"""
