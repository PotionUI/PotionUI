from src.platform.plugins.hooks import hooks_registry

PLANS_HOOKS = hooks_registry.declare(
    "plans", "backend",
    "limit_exceeded", "assignment_changed",
    specs={
        "limit_exceeded": {
            "description": (
                "Fired when a plan limit refuses a generation submit or an upload, just before the refusal is "
                "returned. Observe only: the refusal cannot be lifted from a handler."
            ),
            "payload": {
                "user_id": {"type": "str", "description": "The refused user"},
                "point": {"type": "str", "description": "'submit' or 'upload'"},
                "kinds": {"type": "list", "description": "Limit kind keys that refused, primary first"},
                "codes": {"type": "list", "description": "Refusal codes, same order as kinds"},
            },
            "use_when": ["Tell an admin or an external system that someone hit a limit"],
            "example": (
                "def on_limit_exceeded(context: HookContext) -> HookContext:\n"
                "    logger.info(f\"{context.data['user_id']} hit {context.data['kinds']}\")\n"
                "    return context\n"
            ),
        },
        "assignment_changed": {
            "description": "Fired after an admin changes which plan a group, the default or a user's override points at.",
            "payload": {
                "target": {"type": "str", "description": "'group' or 'user'"},
                "target_id": {"type": "str", "description": "Group id (all_users for the default) or user id"},
                "plan_id": {"type": "Optional[str]", "description": "The new plan id, null for inherit / no override"},
                "actor_id": {"type": "str", "description": "The admin who made the change"},
            },
            "use_when": ["Audit plan changes outside PotionUI"],
            "example": (
                "def on_assignment_changed(context: HookContext) -> HookContext:\n"
                "    audit(context.data)\n"
                "    return context\n"
            ),
        },
    },
)
