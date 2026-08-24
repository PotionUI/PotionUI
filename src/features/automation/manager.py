"""
`AutomationManager` - CRUD, graph validation, and trigger lifecycle for the
automation module (Manager pattern, no "Service" classes per repo convention).

Owns the concrete `TriggerSource` instances for every trigger node in every
enabled automation, and the shared resources they need (the hook-event
bridge, the filesystem watch manager). The engine only knows how to *walk* a
graph once triggered - this is what decides *when* to walk it.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.features.automation.engine import AutomationEngine
from src.platform.plugins.automation_nodes import NodeTypeRegistry, node_type_registry, resolve_dynamic_ports
from src.platform.plugins.automation_templates import (
    AutomationEnvelopeError,
    AutomationTemplateRegistry,
    validate_automation_envelope,
)
from src.features.automation.triggers.base import TriggerSource
from src.features.automation.triggers.filesystem import FilesystemTrigger, FilesystemWatchManager, resolve_effective_directory
from src.features.automation.triggers.hook_bridge import HookEventBridge, HookEventTrigger
from src.features.automation.triggers.manual import ManualTrigger
from src.features.automation.triggers.resource import ResourceTrigger
from src.features.automation.triggers.schedule import ScheduleTrigger
from src.features.automation.records import Automation

logger = logging.getLogger(__name__)


class GraphValidationError(ValueError):
    """Raised by `create`/`update` when a graph fails `validate_graph`."""

    def __init__(self, issues: List[Dict[str, Any]]):
        self.issues = issues
        super().__init__(f"Automation graph is invalid: {issues}")


class AutomationImportError(ValueError):
    """Raised by `import_automation` when the document isn't an importable export."""


class AutomationTemplateNotFoundError(LookupError):
    """Raised when a template key is not present in the runtime catalog."""


class AutomationTemplateUnavailableError(ValueError):
    """Raised when this installation is missing node types required by a template."""


# Bumped only on a breaking change to the exported envelope. `import_automation`
# refuses anything it doesn't recognise rather than guessing.
EXPORT_SCHEMA = "potionui.automation"
EXPORT_SCHEMA_VERSION = 1


def _import_envelope_message(exc: AutomationEnvelopeError) -> str:
    """Translate a shared `AutomationEnvelopeError` code into `import_automation`'s wording."""
    if exc.code == "not_dict":
        return "Import document must be a JSON object"
    if exc.code == "wrong_schema":
        return "Not a PotionUI automation export"
    if exc.code == "wrong_version":
        return (
            f"Unsupported export schema_version: {exc.detail['found']!r} "
            f"(this build reads version {exc.detail['expected']})"
        )
    if exc.code == "missing_graph":
        return "Export is missing 'automation.graph' with a 'nodes' list"
    if exc.code == "missing_edges":
        return "Export is missing 'automation.graph' with an 'edges' list"
    if exc.code == "invalid_node":
        return "Export contains an invalid graph node"
    if exc.code == "invalid_edge":
        return "Export contains an invalid graph edge"
    if exc.code == "invalid_node_types_meta":
        return "Export has invalid node_types metadata"
    return f"Export is invalid ({exc.code})"


class _PluginTriggerAdapter(TriggerSource):
    """Wraps a plugin-provided custom trigger's `spec.start`/`spec.stop` callables."""

    def __init__(self, automation_id: str, node_id: str, config: Dict[str, Any], enqueue, spec):
        super().__init__(automation_id, node_id, config, enqueue)
        self._spec = spec

    async def start(self) -> None:
        if self._spec.start is not None:
            await self._spec.start(self)

    async def stop(self) -> None:
        if self._spec.stop is not None:
            await self._spec.stop(self)


class AutomationManager:
    def __init__(
        self,
        repository,
        engine: AutomationEngine,
        plugin_registry: Optional[Any] = None,
        registry: NodeTypeRegistry = node_type_registry,
        template_registry: Optional[AutomationTemplateRegistry] = None,
    ):
        self.repository = repository
        self.engine = engine
        self.plugin_registry = plugin_registry
        self.registry = registry
        self.template_registry = template_registry

        self._hook_bridge: Optional[HookEventBridge] = (
            HookEventBridge(plugin_registry.hook_chain) if plugin_registry is not None else None
        )
        self._watch_manager = FilesystemWatchManager()
        # (automation_id, node_id) -> running TriggerSource
        self._active_triggers: Dict[tuple, TriggerSource] = {}

    # -- CRUD --------------------------------------------------------------

    async def create(self, name: str, graph: Dict[str, Any], description: Optional[str] = None,
                      user_id: Optional[str] = None, enabled: bool = False) -> Automation:
        issues = self.validate_graph(graph)
        if any(i["severity"] == "error" for i in issues):
            raise GraphValidationError(issues)

        automation = self.repository.create(Automation(
            id="", name=name, graph=graph, description=description, user_id=user_id, enabled=enabled,
        ))

        if enabled:
            await self._start_triggers(automation)

        return automation

    def get(self, automation_id: str, user_id: Optional[str] = None) -> Optional[Automation]:
        return self.repository.get_by_id(automation_id, user_id=user_id)

    def list(self, user_id: Optional[str] = None) -> List[Automation]:
        return self.repository.get_all(user_id=user_id)

    async def update(self, automation_id: str, name: Optional[str] = None, graph: Optional[Dict[str, Any]] = None,
                      description: Optional[str] = None, enabled: Optional[bool] = None) -> Optional[Automation]:
        existing = self.repository.get_by_id(automation_id)
        if existing is None:
            return None

        graph_changed = graph is not None and graph != existing.graph
        if graph_changed:
            issues = self.validate_graph(graph)
            if any(i["severity"] == "error" for i in issues):
                raise GraphValidationError(issues)
            existing.graph = graph

        if name is not None:
            existing.name = name
        if description is not None:
            existing.description = description
        if enabled is not None:
            existing.enabled = enabled

        updated = self.repository.update(existing, bump_version=graph_changed)
        if updated is None:
            return None

        if graph_changed or enabled is not None:
            await self._stop_triggers(automation_id)
            if updated.enabled:
                await self._start_triggers(updated)

        return updated

    async def delete(self, automation_id: str) -> bool:
        await self._stop_triggers(automation_id)
        return self.repository.delete(automation_id)

    async def set_enabled(self, automation_id: str, enabled: bool) -> Optional[Automation]:
        if not self.repository.set_enabled(automation_id, enabled):
            return None

        automation = self.repository.get_by_id(automation_id)
        await self._stop_triggers(automation_id)
        if enabled and automation is not None:
            await self._start_triggers(automation)

        return automation

    # -- portability ---------------------------------------------------------

    def list_templates(self) -> List[Dict[str, Any]]:
        """Return catalog metadata plus requirement availability for this runtime."""
        if self.template_registry is None:
            return []

        result = []
        for template in self.template_registry.all():
            missing = [key for key in template.node_types if self.registry.get(key) is None]
            result.append(template.summary(missing))
        return result

    async def instantiate_template(
        self,
        template_key: str,
        *,
        user_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> tuple[Automation, List[Dict[str, Any]]]:
        """Create a fresh disabled automation from one immutable catalog entry."""
        template = self.template_registry.get(template_key) if self.template_registry is not None else None
        if template is None:
            raise AutomationTemplateNotFoundError(template_key)

        missing = [key for key in template.node_types if self.registry.get(key) is None]
        if missing:
            raise AutomationTemplateUnavailableError(
                "Template requires node type(s) not installed on this system: " + ", ".join(missing)
            )

        document = template.clone_document()
        if name:
            automation_payload = dict(document.get("automation") or {})
            automation_payload["name"] = name
            document["automation"] = automation_payload
        return await self.import_automation(document, user_id=user_id)

    def export_automation(self, automation_id: str) -> Optional[Dict[str, Any]]:
        """
        Serialize one automation to a portable envelope, or None if unknown.

        Deliberately omits `id`, `user_id`, `enabled`, `version` and timestamps:
        those describe this installation's copy, not the workflow. The `graph`
        is copied verbatim - in particular graph-internal node ids are preserved,
        because config values reference them as `upstream.<node_id>.<field>` in
        Jinja templates. Rewriting node ids would silently break every such
        reference; they're scoped to the graph, so keeping them is safe.
        """
        automation = self.repository.get_by_id(automation_id)
        if automation is None:
            return None

        node_types = sorted({node.get("type") for node in automation.graph.get("nodes", []) if node.get("type")})

        return {
            "schema": EXPORT_SCHEMA,
            "schema_version": EXPORT_SCHEMA_VERSION,
            "kind": "automation",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "automation": {
                "name": automation.name,
                "description": automation.description,
                "graph": automation.graph,
            },
            # Lets the importing side name the node types it's missing before it
            # touches the graph.
            "node_types": node_types,
        }

    async def import_automation(self, document: Dict[str, Any],
                                 user_id: Optional[str] = None) -> tuple:
        """
        Create a new automation from an exported envelope.

        Returns `(automation, warnings)`. The automation is always created
        **disabled** with a fresh id, so no trigger is started and a watch
        directory that doesn't exist here cannot blow up the import - the user
        fixes the path, then enables (at which point `FilesystemTrigger.start()`
        re-checks it anyway).

        Structural problems block; environment problems come back as warnings.
        This is why `import_automation` exists rather than a `strict=False` flag
        on `create` - `create`'s "refuse any error" contract stays untouched.
        """
        try:
            validate_automation_envelope(
                document, schema=EXPORT_SCHEMA, schema_version=EXPORT_SCHEMA_VERSION,
            )
        except AutomationEnvelopeError as exc:
            raise AutomationImportError(_import_envelope_message(exc)) from exc

        payload = document["automation"]
        graph = payload["graph"]

        # Named up front so a missing plugin reads as "install X", not as a wall
        # of per-node "Unknown node type" errors.
        missing = sorted({
            node.get("type") for node in graph["nodes"]
            if self.registry.get(node.get("type", "")) is None
        })
        if missing:
            raise AutomationImportError(
                "Cannot import: node type(s) not installed on this system: "
                + ", ".join(str(key) for key in missing)
            )

        issues = self.validate_graph(graph)
        structural = [i for i in issues if i["severity"] == "error" and i.get("category") == "structural"]
        if structural:
            raise GraphValidationError(structural)

        warnings = [i for i in issues if i.get("category") == "environment"]

        automation = self.repository.create(Automation(
            id="",
            name=payload.get("name") or "Imported automation",
            description=payload.get("description"),
            graph=graph,
            user_id=user_id,
            enabled=False,
        ))

        return automation, warnings

    # -- validation ----------------------------------------------------------

    def validate_graph(self, graph: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Returns `[{node_id, message, severity, category}]` - unknown types, cycles,
        dangling edges, missing required config.

        `category` splits issues by *what would fix them*:

        - `"structural"` - the graph itself is wrong (unknown node type, cycle,
          dangling edge, bad output port, missing required config). Wrong
          everywhere, on every machine.
        - `"environment"` - the graph is fine but this machine can't satisfy it
          (a watch directory that doesn't exist here).

        `create`/`update` ignore the distinction and still refuse any
        `severity == "error"`. `import_automation` uses it to accept a graph
        exported from another machine, downgrading environment errors to
        warnings - otherwise a workflow watching `/home/alice/models` could
        never be imported by Bob.
        """
        issues: List[Dict[str, Any]] = []
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        node_ids = {n["id"] for n in nodes}

        for node in nodes:
            spec = self.registry.get(node.get("type", ""))
            if spec is None:
                issues.append({"node_id": node.get("id"), "message": f"Unknown node type: '{node.get('type')}'",
                               "severity": "error", "category": "structural"})
                continue

            config = node.get("config", {}) or {}
            for field_def in spec.config_schema:
                if field_def.get("required") and not config.get(field_def["name"]):
                    issues.append({
                        "node_id": node["id"],
                        "message": f"Missing required config field: '{field_def['name']}'",
                        "severity": "error",
                        "category": "structural",
                    })

            if node.get("type") == "trigger.filesystem":
                issues.extend(self._validate_filesystem_directory(node["id"], config))

            issues.extend(self._validate_output_ports(node, spec, config, edges))

        for edge in edges:
            if edge.get("source") not in node_ids:
                issues.append({"node_id": edge.get("id"), "message": f"Dangling edge: unknown source '{edge.get('source')}'",
                               "severity": "error", "category": "structural"})
            if edge.get("target") not in node_ids:
                issues.append({"node_id": edge.get("id"), "message": f"Dangling edge: unknown target '{edge.get('target')}'",
                               "severity": "error", "category": "structural"})

        if self._has_cycle(node_ids, edges):
            issues.append({"node_id": None, "message": "Graph contains a cycle",
                           "severity": "error", "category": "structural"})

        return issues

    @staticmethod
    def _validate_filesystem_directory(node_id: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        `trigger.filesystem` directory checks, run at graph-save time: custom
        absolute paths are allowed (explicit user requirement, no allow-list),
        but the directory must be non-empty, exist, and actually be a
        directory. `FilesystemTrigger.start()` re-checks this defensively
        since a directory can disappear between save and trigger start.
        """
        directory = resolve_effective_directory(config)

        # No directory configured at all: the graph is incomplete, on any machine.
        if not directory:
            return [{"node_id": node_id, "message": "Watch directory is required",
                     "severity": "error", "category": "structural"}]

        # Configured but absent: the graph is fine, *this machine* can't satisfy it.
        # Import downgrades this to a warning (see `import_automation`).
        if not os.path.isdir(directory):
            return [{
                "node_id": node_id,
                "message": f"Watch directory '{directory}' does not exist or is not a directory",
                "severity": "error",
                "category": "environment",
            }]

        return []

    @staticmethod
    def _validate_output_ports(node: Dict[str, Any], spec, config: Dict[str, Any],
                                edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Every outgoing edge's `source_handle` must be one of the node's
        actual output ports. For dynamic-port nodes (`condition.switch`) that
        means the currently-configured case labels + "default" - so removing
        a case from `cases` and leaving a dangling edge on it is caught here
        ("edge leaves removed case '<handle>'"). For everything else
        (including plain true/false conditions) it's just `spec.output_ports`.
        """
        dynamic_ports = resolve_dynamic_ports(spec, config)
        valid_handles = set(dynamic_ports) if dynamic_ports is not None else set(spec.output_ports)

        issues = []
        for edge in edges:
            if edge.get("source") != node["id"]:
                continue
            handle = edge.get("source_handle", "out")
            if handle in valid_handles:
                continue
            if dynamic_ports is not None:
                message = f"Edge leaves removed case '{handle}'"
            else:
                message = f"Edge uses unknown output port '{handle}' for node type '{node.get('type')}'"
            issues.append({"node_id": node["id"], "message": message,
                           "severity": "error", "category": "structural"})

        return issues

    @staticmethod
    def _has_cycle(node_ids: set, edges: List[Dict[str, Any]]) -> bool:
        """Kahn's algorithm topo-sort; any leftover nodes after processing indicate a cycle."""
        in_degree = {node_id: 0 for node_id in node_ids}
        adjacency: Dict[str, List[str]] = {node_id: [] for node_id in node_ids}

        for edge in edges:
            source, target = edge.get("source"), edge.get("target")
            if source not in node_ids or target not in node_ids:
                continue
            adjacency[source].append(target)
            in_degree[target] += 1

        queue = [node_id for node_id, degree in in_degree.items() if degree == 0]
        visited = 0

        while queue:
            current = queue.pop()
            visited += 1
            for neighbor in adjacency[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return visited != len(node_ids)

    # -- trigger lifecycle -----------------------------------------------------

    async def start_all_enabled(self) -> None:
        """Start triggers for every enabled automation. Call once at app startup, after `engine.set_loop`."""
        for automation in self.repository.get_all(enabled_only=True):
            await self._start_triggers(automation)

    async def stop_all(self) -> None:
        """Stop every running trigger. Call on app shutdown."""
        for key in list(self._active_triggers.keys()):
            trigger = self._active_triggers.pop(key)
            try:
                await trigger.stop()
            except Exception:
                logger.error(f"[AUTOMATION_MANAGER] Error stopping trigger {key}", exc_info=True)
        self._watch_manager.stop()

    async def run_now(self, automation_id: str, node_id: Optional[str] = None,
                       payload: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Manually fire an automation run, bypassing any trigger."""
        automation = self.repository.get_by_id(automation_id)
        if automation is None:
            return None

        trigger_node_id = node_id
        if trigger_node_id is None:
            trigger_nodes = [n for n in automation.graph.get("nodes", []) if self._is_trigger_node(n)]
            if not trigger_nodes:
                logger.error(f"[AUTOMATION_MANAGER] run_now: automation {automation_id} has no trigger node")
                return None
            trigger_node_id = trigger_nodes[0]["id"]

        return await self.engine.run(automation_id, trigger_node_id, payload or {})

    def _is_trigger_node(self, node: Dict[str, Any]) -> bool:
        spec = self.registry.get(node.get("type", ""))
        return spec is not None and spec.kind == "trigger"

    async def _start_triggers(self, automation: Automation) -> None:
        for node in automation.graph.get("nodes", []):
            if not self._is_trigger_node(node):
                continue

            key = (automation.id, node["id"])
            if key in self._active_triggers:
                continue

            trigger = self._build_trigger(automation.id, node)
            if trigger is None:
                continue

            try:
                await trigger.start()
                self._active_triggers[key] = trigger
            except Exception:
                logger.error(f"[AUTOMATION_MANAGER] Failed to start trigger {key}", exc_info=True)

    async def _stop_triggers(self, automation_id: str) -> None:
        for key in [k for k in self._active_triggers if k[0] == automation_id]:
            trigger = self._active_triggers.pop(key)
            try:
                await trigger.stop()
            except Exception:
                logger.error(f"[AUTOMATION_MANAGER] Error stopping trigger {key}", exc_info=True)

    def _build_trigger(self, automation_id: str, node: Dict[str, Any]) -> Optional[TriggerSource]:
        node_type = node["type"]
        node_id = node["id"]
        config = node.get("config", {}) or {}
        enqueue = self.engine.enqueue_trigger

        if node_type == "trigger.filesystem":
            return FilesystemTrigger(
                automation_id, node_id, config, enqueue, self._watch_manager,
                notification_manager=self.engine.services.notification_manager,
            )

        if node_type == "trigger.schedule":
            return ScheduleTrigger(automation_id, node_id, config, enqueue)

        if node_type == "trigger.gpu_threshold":
            gpu_manager = self.engine.services.gpu_manager
            if gpu_manager is None:
                logger.error(f"[AUTOMATION_MANAGER] trigger.gpu_threshold node {node_id}: no GpuManager configured")
                return None
            return ResourceTrigger(
                automation_id, node_id, config, enqueue, gpu_manager,
                generation_status_tracker=self.engine.services.generation_status_tracker,
            )

        if node_type == "trigger.manual":
            return ManualTrigger(automation_id, node_id, config, enqueue)

        if node_type == "trigger.hook_event":
            if self._hook_bridge is None:
                logger.error(f"[AUTOMATION_MANAGER] trigger.hook_event node {node_id}: no PluginRegistry configured")
                return None
            return HookEventTrigger(automation_id, node_id, config, enqueue, self._hook_bridge,
                                    schedule_run=self.engine.schedule_run)

        spec = self.registry.get(node_type)
        if spec is not None and spec.start is not None and spec.stop is not None:
            return _PluginTriggerAdapter(automation_id, node_id, config, enqueue, spec)

        logger.error(f"[AUTOMATION_MANAGER] No trigger factory for node type '{node_type}' (node {node_id})")
        return None
