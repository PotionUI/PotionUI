"""
Tests for the `pipe.before_execute` hook contract used by the OpenRouter plugin:
  1. Hook context must expose `generation_id` so hooks can correlate with
     `generation.before_start` (which carries user_id).
  2. If a hook modifies `pipe_config`, the changes must reach the executed
     pipe's `self.config`.
"""

from typing import Any, Dict

from src.features.generation.engine import GenerationEngine
from src.platform.plugins.hooks import HookContext
from src.platform.plugins.registry import PluginRegistry
from src.features.generation.hooks import PIPE_HOOKS
from src.pipelines.contracts import IOType, PipeInput, PipeOutput, PipeOutputSpec
from unittest.mock import Mock


class _ObservingPipe:
    """Records the config it sees inside process()."""

    name = "observer_pipe"
    seen_config: Dict[str, Any] = {}

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @staticmethod
    def get_default_config():
        return {"secret": None, "other": "default"}

    @staticmethod
    def inputs():
        return []

    @staticmethod
    def outputs():
        return [PipeOutputSpec(name="output", io_type=IOType.TEXT, is_array=False)]

    @staticmethod
    def configuration():
        return []

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
        # Record at execution time so we see what the hook produced.
        _ObservingPipe.seen_config = dict(self.config)
        return PipeOutput(output={"output": "ok"})


def _mock_deps():
    return {
        "gpu": Mock(),
        "model_directories": Mock(),
        "pipe_catalog": Mock(),
        "settings": Mock(),
        "system_monitor": Mock(),
        "memory_advisor": Mock(),
        "llm_service": Mock(),
    }


def _plugin_registry() -> PluginRegistry:
    return PluginRegistry(marketplace_dir="test_plugins_mp", local_dir="test_plugins_local")


def test_before_execute_hook_receives_generation_id():
    """pipe.before_execute context should include generation_id for plugin correlation."""
    deps = _mock_deps()
    registry = _plugin_registry()
    manager = GenerationEngine(**deps, plugin_registry=registry)

    seen = {}

    def handler(ctx: HookContext) -> HookContext:
        seen["generation_id"] = ctx.data.get("generation_id")
        seen["pipe_name"] = ctx.data.get("pipe_name")
        return ctx

    registry.hook_chain.register(
        PIPE_HOOKS.before_execute, "test_plugin", handler
    )
    deps["pipe_catalog"].get_pipe.return_value = _ObservingPipe

    manager.generate(
        [{"name": "observer_pipe", "enabled": True, "input": [], "cache": [], "config": {}}],
        lambda _o: None,
        "gen-expected",
    )

    assert seen["generation_id"] == "gen-expected"
    assert seen["pipe_name"] == "observer_pipe"


def test_before_execute_hook_pipe_config_mutation_propagates():
    """Hook mutating pipe_config in-place must land in the executed pipe's self.config."""
    deps = _mock_deps()
    registry = _plugin_registry()
    manager = GenerationEngine(**deps, plugin_registry=registry)

    def handler(ctx: HookContext) -> HookContext:
        # In-place mutation, like the real openrouter hook does.
        ctx.data["pipe_config"]["secret"] = "sk-injected-by-hook"
        return ctx

    registry.hook_chain.register(
        PIPE_HOOKS.before_execute, "test_plugin", handler
    )
    deps["pipe_catalog"].get_pipe.return_value = _ObservingPipe

    _ObservingPipe.seen_config = {}
    manager.generate(
        [{"name": "observer_pipe", "enabled": True, "input": [], "cache": [], "config": {}}],
        lambda _o: None,
        "gen-mutation",
    )

    assert _ObservingPipe.seen_config.get("secret") == "sk-injected-by-hook"


def test_before_execute_hook_pipe_config_replacement_propagates():
    """Hook replacing pipe_config with a new dict must also reach the pipe."""
    deps = _mock_deps()
    registry = _plugin_registry()
    manager = GenerationEngine(**deps, plugin_registry=registry)

    def handler(ctx: HookContext) -> HookContext:
        ctx.data["pipe_config"] = {"secret": "sk-replaced", "other": "replaced-value"}
        return ctx

    registry.hook_chain.register(
        PIPE_HOOKS.before_execute, "test_plugin", handler
    )
    deps["pipe_catalog"].get_pipe.return_value = _ObservingPipe

    _ObservingPipe.seen_config = {}
    manager.generate(
        [{"name": "observer_pipe", "enabled": True, "input": [], "cache": [], "config": {}}],
        lambda _o: None,
        "gen-replace",
    )

    assert _ObservingPipe.seen_config.get("secret") == "sk-replaced"
    assert _ObservingPipe.seen_config.get("other") == "replaced-value"
