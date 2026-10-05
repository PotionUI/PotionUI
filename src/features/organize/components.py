from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from src.features.notifications.types import NotificationTypeRegistry, NotificationTypeSpec
from src.features.organize.core_actions import register_core_actions
from src.features.organize.core_facts import register_core_facts
from src.features.organize.item_repository import OrganizeItemRepository
from src.features.organize.manager import (
    NOTICE_JOB_FINISHED,
    NOTICE_RULE_PAUSED,
    OrganizeCollaborators,
    OrganizeManager,
)
from src.features.organize.routes import OrganizeController
from src.features.organize.rule_repository import OrganizeRuleRepository
from src.features.organize.run_repository import OrganizeRunRepository
from src.features.organize.visibility import ContentSafetyVisibility
from src.features.organize.worker import OrganizeWorker
from src.features.organize.write_repository import OrganizeWriteRepository
from src.platform.plugins.organize import OrganizeRegistry


@dataclass
class OrganizeComponents:
    manager: OrganizeManager
    controller: OrganizeController
    worker: OrganizeWorker


def register_notification_types(registry: NotificationTypeRegistry) -> None:
    specs = (
        NotificationTypeSpec(
            key=NOTICE_RULE_PAUSED, label="Auto-organize paused a rule",
            description="One of your rules was paused and needs a look.", category="organize",
        ),
        NotificationTypeSpec(
            key=NOTICE_JOB_FINISHED, label="Auto-organize finished applying a rule",
            description="A rule finished filing your existing items.", category="organize",
        ),
    )
    for spec in specs:
        if not registry.has(spec.key):
            registry.register(spec)


def build_organize(registry: OrganizeRegistry, content_safety: Any, notify: Optional[Callable[..., Any]],
                   presets: Callable[[], List[Tuple[str, str]]],
                   notification_types: Optional[NotificationTypeRegistry] = None,
                   executor: Optional[Any] = None) -> OrganizeComponents:
    items = OrganizeItemRepository()
    writer = OrganizeWriteRepository()
    register_core_facts(registry, items, presets)
    register_core_actions(registry, writer)
    if notification_types is not None:
        register_notification_types(notification_types)
    manager = OrganizeManager(
        OrganizeCollaborators(
            rules=OrganizeRuleRepository(),
            runs=OrganizeRunRepository(),
            items=items,
            writer=writer,
            registry=registry,
            visibility=ContentSafetyVisibility(content_safety),
            notify=notify,
        ),
        executor=executor,
    )
    worker = OrganizeWorker(manager.handle_event, prune=manager.prune)
    return OrganizeComponents(manager=manager, controller=OrganizeController(manager), worker=worker)
