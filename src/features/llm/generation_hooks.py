from typing import Callable, Optional

from src.features.llm.hooks import LLM_HOOKS
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import await_data_blocking_waits, execute_hook, run_veto_hook


async def fire_before_generate(
    plugins: PluginRegistry,
    user_id: str,
    config_id: str,
    prompt: str,
    has_image: bool,
    error: Callable[[str], BaseException],
) -> str:
    hook_data = run_veto_hook(
        plugins,
        LLM_HOOKS.before_generate,
        {
            "user_id": user_id,
            "config_id": config_id,
            "prompt": prompt,
            "has_image": has_image,
        },
        "Generation blocked",
        error=error,
    )
    await await_data_blocking_waits(hook_data)
    return hook_data.get("prompt", prompt)


def fire_after_generate(
    plugins: PluginRegistry,
    user_id: str,
    config_id: str,
    content: str,
    model: Optional[str],
    tokens_used: int,
) -> None:
    execute_hook(
        plugins,
        LLM_HOOKS.after_generate,
        {
            "user_id": user_id,
            "config_id": config_id,
            "content": content,
            "model": model,
            "tokens_used": tokens_used,
        },
    )
