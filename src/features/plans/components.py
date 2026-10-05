from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from src.features.plans.guard import LimitGuard, PlanSettingsStore
from src.features.plans.kinds import register_core_kinds
from src.features.plans.manager import PlansManager
from src.features.plans.repository import LimitEventRepository, PlanRepository, UsageRepository
from src.features.plans.routes import PlansController
from src.platform.database.rows import now_utc
from src.platform.plugins.hooks import HookContext
from src.platform.plugins.limit_kinds import LimitKindRegistry


@dataclass
class PlansComponents:
    guard: LimitGuard
    manager: PlansManager
    controller: PlansController
    events: LimitEventRepository


def hook_runner_for(plugin_registry: Any) -> Optional[Callable[[str, Dict[str, Any]], None]]:
    if plugin_registry is None:
        return None
    def run(hook_name: str, data: Dict[str, Any]) -> None:
        plugin_registry.execute_hook(hook_name, HookContext(hook_name=hook_name, plugin_id="system", data=data))

    return run


def build_plans(registry: LimitKindRegistry, settings: Any, plugin_registry: Any = None,
                clock: Callable[[], datetime] = now_utc) -> PlansComponents:
    usage = UsageRepository()
    events = LimitEventRepository()
    register_core_kinds(registry, usage)
    runner = hook_runner_for(plugin_registry)
    guard = LimitGuard(
        registry=registry,
        plans=PlanRepository(),
        events=events,
        usage=usage,
        settings=PlanSettingsStore(settings),
        clock=clock,
        hook_runner=runner,
    )
    manager = PlansManager(guard, hook_runner=runner)
    return PlansComponents(guard=guard, manager=manager, controller=PlansController(manager), events=events)
