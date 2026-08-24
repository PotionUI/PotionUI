"""
Automation template registry.

Mirrors the sibling `automation_nodes.py` extension point: an immutable
catalog entry (`AutomationTemplate`) is a portable automation envelope plus
namespaced metadata, registered onto the shared `AutomationTemplateRegistry`
by `src.features.automation.templates.register_builtin_templates` (core
catalog, at import time) and by `PluginRegistry` (`plugin:<id>:<template_id>`,
on enable, removed again via `unregister_source` on disable).

Also home to `validate_automation_envelope`, the one structural-shape check
for a portable automation document shared with `AutomationManager.
import_automation` (`src/features/automation/manager.py`), so the two paths
- registering a template file and importing an exported automation - can't
drift on what counts as a well-formed envelope.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class AutomationTemplateRegistrationError(ValueError):
    """Raised when an immutable template contribution cannot be registered."""


class AutomationEnvelopeError(ValueError):
    """
    Raised by `validate_automation_envelope` on the first structural problem
    found in a candidate portable automation document.

    Carries a `code` identifying which check failed, plus any detail the
    caller needs to compose its own message (e.g. `wrong_version` carries
    `found`/`expected`). Callers catch this and raise their own error type
    with their own wording - it is deliberately not user-facing itself.
    """

    def __init__(self, code: str, **detail: Any):
        self.code = code
        self.detail = detail
        super().__init__(code)


def validate_automation_envelope(document: Any, *, schema: str, schema_version: int) -> None:
    """
    Structural validation for a portable automation envelope, parameterized
    on the expected `schema`/`schema_version` so neither caller hardcodes the
    other's constants. Raises `AutomationEnvelopeError` on the first violated
    check (never returns a list - both current callers only need "is this
    valid", not an exhaustive issue list):

    - `not_dict` - document isn't a JSON object
    - `wrong_schema` - `schema`/`kind` don't match
    - `wrong_version` - `schema_version` doesn't match (detail: found, expected)
    - `missing_graph` - no `automation.graph` object with a `nodes` list
    - `missing_edges` - `automation.graph` has no `edges` list
    - `invalid_node` - a graph node isn't `{id: str, type: str, ...}`
    - `invalid_edge` - a graph edge isn't a JSON object
    - `invalid_node_types_meta` - top-level `node_types` isn't a list
    """
    if not isinstance(document, dict):
        raise AutomationEnvelopeError("not_dict")
    if document.get("schema") != schema or document.get("kind") != "automation":
        raise AutomationEnvelopeError("wrong_schema")
    if document.get("schema_version") != schema_version:
        raise AutomationEnvelopeError(
            "wrong_version", found=document.get("schema_version"), expected=schema_version,
        )

    automation = document.get("automation")
    graph = automation.get("graph") if isinstance(automation, dict) else None
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise AutomationEnvelopeError("missing_graph")
    if not isinstance(graph.get("edges"), list):
        raise AutomationEnvelopeError("missing_edges")

    for node in graph["nodes"]:
        if (
            not isinstance(node, dict)
            or not isinstance(node.get("id"), str)
            or not node["id"]
            or not isinstance(node.get("type"), str)
            or not node["type"]
        ):
            raise AutomationEnvelopeError("invalid_node")
    if not all(isinstance(edge, dict) for edge in graph["edges"]):
        raise AutomationEnvelopeError("invalid_edge")

    if "node_types" in document and not isinstance(document["node_types"], list):
        raise AutomationEnvelopeError("invalid_node_types_meta")


def plugin_template_source(plugin_id: str) -> str:
    """The `AutomationTemplateRegistry` source key for a plugin's own templates."""
    return f"plugin:{plugin_id}"


@dataclass(frozen=True)
class AutomationTemplate:
    """One immutable catalog entry backed by a portable automation envelope."""

    key: str
    template_id: str
    source: str
    source_name: str
    title: str
    description: str
    category: str
    icon: str
    tags: Tuple[str, ...]
    document_json: str
    node_types: Tuple[str, ...]

    def clone_document(self) -> Dict[str, Any]:
        """Return a mutable copy; callers must never mutate the catalog copy."""
        return json.loads(self.document_json)

    def summary(self, missing_node_types: List[str]) -> Dict[str, Any]:
        return {
            "key": self.key,
            "id": self.template_id,
            "source": self.source,
            "source_name": self.source_name,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "icon": self.icon,
            "tags": list(self.tags),
            "node_types": list(self.node_types),
            "missing_node_types": missing_node_types,
            "available": not missing_node_types,
        }


class AutomationTemplateRegistry:
    """Process-local catalog. Plugin entries are keyed and removed by source."""

    def __init__(self):
        self._by_key: Dict[str, AutomationTemplate] = {}

    def register_from_file(
        self,
        *,
        source: str,
        source_name: str,
        template_id: str,
        title: str,
        description: str = "",
        category: str = "general",
        icon: str = "bolt",
        tags: Optional[List[str]] = None,
        path: Path,
        root: Path,
    ) -> AutomationTemplate:
        resolved_root = root.resolve()
        resolved_path = path.resolve()
        if (
            resolved_path != resolved_root
            and resolved_root not in resolved_path.parents
        ):
            raise AutomationTemplateRegistrationError(
                f"Automation template path escapes its source directory: {path}"
            )
        if not resolved_path.is_file():
            raise AutomationTemplateRegistrationError(
                f"Automation template file does not exist: {path}"
            )

        try:
            document = json.loads(resolved_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AutomationTemplateRegistrationError(
                f"Cannot read automation template '{template_id}': {exc}"
            ) from exc

        self._validate_document(document, template_id)
        key = f"{source}:{template_id}"
        if key in self._by_key:
            raise AutomationTemplateRegistrationError(
                f"Automation template already registered: '{key}'"
            )

        template = AutomationTemplate(
            key=key,
            template_id=template_id,
            source=source,
            source_name=source_name,
            title=title,
            description=description,
            category=category,
            icon=icon,
            tags=tuple(tags or []),
            document_json=json.dumps(document, sort_keys=True, separators=(",", ":")),
            node_types=self._node_types(document),
        )
        self._by_key[key] = template
        return template

    @staticmethod
    def _node_types(document: Dict[str, Any]) -> Tuple[str, ...]:
        keys = set()
        declared = document.get("node_types")
        if isinstance(declared, list):
            keys.update(str(key) for key in declared if key)

        graph = (document.get("automation") or {}).get("graph") or {}
        nodes = graph.get("nodes") or []
        keys.update(str(node.get("type")) for node in nodes if node.get("type"))
        return tuple(sorted(keys))

    @staticmethod
    def _validate_document(document: Any, template_id: str) -> None:
        try:
            validate_automation_envelope(
                document, schema="potionui.automation", schema_version=1,
            )
        except AutomationEnvelopeError as exc:
            raise AutomationTemplateRegistrationError(
                _template_envelope_message(exc, template_id)
            ) from exc

    def get(self, key: str) -> Optional[AutomationTemplate]:
        return self._by_key.get(key)

    def all(self) -> List[AutomationTemplate]:
        return sorted(
            self._by_key.values(),
            key=lambda item: (item.category, item.title.lower(), item.key),
        )

    def unregister_source(self, source: str) -> None:
        for key in [
            key for key, template in self._by_key.items() if template.source == source
        ]:
            del self._by_key[key]


def _template_envelope_message(exc: AutomationEnvelopeError, template_id: str) -> str:
    if exc.code == "not_dict":
        return f"Automation template '{template_id}' must contain a JSON object"
    if exc.code == "wrong_schema":
        return f"Automation template '{template_id}' is not a PotionUI automation envelope"
    if exc.code == "wrong_version":
        return (
            f"Automation template '{template_id}' uses unsupported schema_version "
            f"{exc.detail['found']!r}"
        )
    if exc.code == "missing_graph":
        return f"Automation template '{template_id}' is missing automation.graph.nodes"
    if exc.code == "missing_edges":
        return f"Automation template '{template_id}' is missing automation.graph.edges"
    if exc.code == "invalid_node":
        return f"Automation template '{template_id}' contains an invalid graph node"
    if exc.code == "invalid_edge":
        return f"Automation template '{template_id}' contains an invalid graph edge"
    if exc.code == "invalid_node_types_meta":
        return f"Automation template '{template_id}' has invalid node_types metadata"
    return f"Automation template '{template_id}' is invalid ({exc.code})"
