"""
Well-known ids of the built-in user groups.

Seeded by `src.platform.database.migrations.095_seed_default_user_groups`
with these exact ids - stable strings rather than `generate_ulid()` output so
code can reference "the everyone group" without a lookup, the same way
`src.features.automation.triggers.hook_bridge.DISPATCHER_PLUGIN_ID` or a
keybinding default's fixed id (e.g. `'quick_search'`, migration 046) is a
well-known constant rather than a generated one.

Referenced directly by `UserRepository.create_claiming_instance`
(src/features/users/repository.py - a feature importing another feature's
constants, same as its existing `from src.features.segments.repository import
DEFAULT_SEGMENT_CATEGORIES`) to join the claiming owner to both groups on the
same cursor/transaction as the user row and the `instance_claim` sentinel.

The migration keeps its own copy of these literals rather than importing this
module - migrations in this codebase are self-contained/import-free by
convention - so if these ids ever change, update both places.
"""

ALL_USERS_GROUP_ID = "all_users"
ALL_ADMINS_GROUP_ID = "all_admins"
