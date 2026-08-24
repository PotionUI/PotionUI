"""
Execution context objects passed through the automation engine.

`AutomationServices` bundles the manager/repository dependencies node
`execute()` implementations call into (see `src/features/automation/nodes/actions.py`).
It's constructed once in `src/bootstrap/container.py` and injected into `AutomationEngine`;
every field is optional so the engine, and tests, can run with a partial
bundle. `RunContext` is per-run state; `NodeExecutionContext` is the narrower
view a single node's `execute()` sees.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class AutomationServices:
    """Injectable dependency bundle for node `execute()` implementations."""
    model_index_manager: Optional[Any] = None
    model_indexer: Optional[Any] = None
    tag_repository: Optional[Any] = None
    notification_manager: Optional[Any] = None
    gpu_manager: Optional[Any] = None
    # Backs the trigger.filesystem "app directory" picker (models root + its
    # subdirectories, storage dir, outputs dir) and effective-directory
    # resolution - see src/features/automation/triggers/filesystem.py.
    settings_manager: Optional[Any] = None
    # Backs action.backend_action: config manager resolves the backend and its
    # quick actions; lifecycle manager backs the native "Clear VRAM" operation.
    backend_config_manager: Optional[Any] = None
    model_lifecycle_manager: Optional[Any] = None
    # Backs action.index_models: only active backends can be asked what they can
    # load, so the registry resolves selected ids to LIVE backend instances.
    backend_registry: Optional[Any] = None
    backend_model_indexer: Optional[Any] = None
    # Backs action.assign_user_to_group: goes straight to the repository, not
    # UserGroupManager - automation runs have no admin HTTP-context for its
    # `_require_admin` gate to check.
    user_group_repository: Optional[Any] = None
    # Backs action.index_media_queue: drains the media-index queue (system
    # tags + gallery search embeddings).
    media_index_manager: Optional[Any] = None
    # Backs trigger.gpu_threshold's `require_generation_idle` option - read
    # only, never a dependency the trigger requires (it must run fine without it).
    generation_status_tracker: Optional[Any] = None
    # Backs action.scan_files' model resolution (file path -> indexed Model).
    model_repository: Optional[Any] = None
    # Backs action.add_to_collection.
    model_collection_repository: Optional[Any] = None


@dataclass
class RunContext:
    """Per-run state shared across every node visited during one automation run."""
    automation_id: str
    run_id: str
    event: Dict[str, Any]
    services: AutomationServices = field(default_factory=AutomationServices)
    upstream: Dict[str, Any] = field(default_factory=dict)  # node_id -> NodeResult.output


@dataclass
class NodeExecutionContext:
    """View of the run passed into a single node's `spec.execute(ctx)`."""
    run: RunContext
    node_id: str
    node_type: str
    config: Dict[str, Any]
    # Set by the engine before calling execute(); nodes call this to report
    # an intermediate "waiting" state (e.g. action.wait_for_gpu polling) so
    # the UI reflects it before the node finishes. Optional no-op by default.
    set_status: Callable[[str], None] = lambda status: None

    @property
    def event(self) -> Dict[str, Any]:
        return self.run.event

    @property
    def upstream(self) -> Dict[str, Any]:
        return self.run.upstream

    @property
    def services(self) -> AutomationServices:
        return self.run.services

    def template_context(self) -> Dict[str, Any]:
        """Context dict for Jinja-templated config values / expressions: {event, upstream}."""
        return {"event": self.event, "upstream": self.upstream}
