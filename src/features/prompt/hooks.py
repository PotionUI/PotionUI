"""Hook points owned by the prompt domain."""

from src.platform.plugins.hooks import hooks_registry

PROMPT_HOOKS = hooks_registry.declare(
    "prompt", "backend",
    "transform",
    specs={
        "transform": {
            "description": (
                "Fired around per-image prompt expansion. Runs twice per image: once with "
                "phase='pre' on the authored template (before dynamicprompts samples it), and "
                "once with phase='post' on the expanded text."
            ),
            "payload": {
                "generation_id": {"type": "Optional[str]", "description": "Generation being processed"},
                "image_index": {"type": "int", "description": "0-based index of the image within the batch"},
                "phase": {
                    "type": "str",
                    "description": "'pre' (template, before expansion) or 'post' (expanded text)",
                },
                "seed": {"type": "int", "description": "Seed used to expand this image's prompt"},
                "positive": {"type": "str", "description": "Positive prompt text for this phase/image"},
                "negative": {"type": "str", "description": "Negative prompt text for this phase/image"},
            },
            "mutable": ["positive", "negative"],
            "use_when": [
                "Inject a house style or safety rewrite into the template before it is expanded (phase='pre')",
                "Post-process the finalized per-image prompt, e.g. rewrite attention weights (phase='post')",
            ],
            "example": (
                "# manifest.yml\n"
                "hooks:\n"
                "  backend:\n"
                "    - hook: \"prompt.transform\"\n"
                "      handler: \"hooks.prompt_hooks.on_transform\"\n\n"
                "# hooks/prompt_hooks.py\n"
                "def on_transform(context: HookContext) -> HookContext:\n"
                "    if context.data[\"phase\"] == \"post\":\n"
                "        context.data[\"positive\"] += \", masterpiece\"\n"
                "    return context\n"
            ),
        },
    },
)
