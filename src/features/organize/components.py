from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from src.features.notifications.types import NotificationTypeRegistry, NotificationTypeSpec
from src.features.organize.admin import OrganizeAdmin
from src.features.organize.backfill import BackfillManager
from src.features.organize.core_actions import register_core_actions
from src.features.organize.core_facts import register_core_facts
from src.features.organize.instance_ops import InstanceModelOps
from src.features.organize.item_repository import OrganizeItemRepository
from src.features.organize.outcome_repository import OrganizeOutcomeRepository
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
from src.features.users.repository import UserRepository
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
                   executor: Optional[Any] = None, groups: Optional[Any] = None, plugins: Optional[Any] = None,
                   types: Optional[Any] = None, busy: Optional[Callable[[], bool]] = None) -> OrganizeComponents:
    items = OrganizeItemRepository()
    writer = OrganizeWriteRepository()
    users = UserRepository()
    ops = InstanceModelOps(groups, plugins, types, items) if None not in (groups, plugins, types) else None
    register_core_facts(registry, items, users, presets)
    register_core_actions(registry, writer, ops)
    if notification_types is not None:
        register_notification_types(notification_types)
    manager = OrganizeManager(
        OrganizeCollaborators(
            rules=OrganizeRuleRepository(),
            runs=OrganizeRunRepository(),
            items=items,
            writer=writer,
            users=users,
            registry=registry,
            visibility=ContentSafetyVisibility(content_safety),
            notify=notify,
            outcomes=OrganizeOutcomeRepository(),
            ops=ops,
        ),
        executor=executor,
    )
    backfill = BackfillManager(manager)
    worker = OrganizeWorker(manager.handle_event, prune=manager.prune, sweep=manager.retry_waiting, busy=busy)
    return OrganizeComponents(manager=manager, controller=OrganizeController(manager, backfill, OrganizeAdmin(manager)), worker=worker)
