"""
Phrasebook batch-operation registry.

A batch operation is a tool the Phrasebook's Find & replace selection bar can
run over a set of selected values: its id, the label shown in the bar, the
optional plugin frontend component that collects its parameters, and the
backend object that runs it. Core's own tools (replace, activate/deactivate,
move, delete) register here at bootstrap under source "core", so a plugin
operation is a first-class peer of them.

Plugins extend the bar by registering additional
`PhrasebookBatchOperationDefinition`s on the shared
`phrasebook_operation_registry` singleton when they are enabled, and by having
them removed via `unregister_source` when disabled.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass
class BatchOutcome:
    """What a batch run did, returned to the caller and to `phrasebook.batch.after`."""

    updated: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    message: str = ""


@dataclass
class BatchPreview:
    """A dry run: per-field `{id, field, before, after}` items plus counts."""

    items: List[Dict[str, Any]] = field(default_factory=list)
    changed: int = 0
    unchanged: List[str] = field(default_factory=list)


class BatchOperationError(Exception):
    """A batch request the operation refuses; `code` becomes the API error code."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class PhrasebookBatchContext(ABC):
    """User-scoped access to phrasebook values for one batch request. Every
    write is one transaction; ids that aren't the user's raise
    `BatchOperationError('unknown_values')`."""

    user_id: str

    @abstractmethod
    def values(self, value_ids: Sequence[str]) -> List[Dict[str, Any]]:
        """The user's values among `value_ids`, in the given order."""

    @abstractmethod
    def category(self, category_id: str) -> Optional[Dict[str, Any]]:
        """The user's category, or None."""

    @abstractmethod
    def update_value_texts(self, rows: Sequence[Tuple[str, str, str]]) -> None:
        """Set `(id, label, value)` on each row."""

    @abstractmethod
    def set_active(self, value_ids: Sequence[str], is_active: bool) -> None:
        """Set the active flag on each value."""

    @abstractmethod
    def move(self, value_ids: Sequence[str], category_id: str) -> List[Dict[str, Any]]:
        """Re-parent the values under `category_id` (appended after its last
        sort_order) and return them updated."""

    @abstractmethod
    def delete(self, value_ids: Sequence[str]) -> None:
        """Delete each value."""


class PhrasebookBatchOperation(ABC):
    """One batch tool. `run` is required; `preview` is opt-in by setting
    `supports_preview = True` and overriding it."""

    supports_preview: bool = False

    async def preview(
        self, ctx: PhrasebookBatchContext, value_ids: List[str], params: Dict[str, Any]
    ) -> BatchPreview:
        raise BatchOperationError("no_preview", "This operation has no preview")

    @abstractmethod
    async def run(
        self, ctx: PhrasebookBatchContext, value_ids: List[str], params: Dict[str, Any]
    ) -> BatchOutcome:
        raise NotImplementedError


class DuplicatePhrasebookOperationError(ValueError):
    """Raised when registering an operation id that is already registered."""


@dataclass(frozen=True)
class PhrasebookBatchOperationDefinition:
    """Declaration for a single batch operation."""

    op_id: str
    label: str
    # A `PhrasebookBatchOperation` instance.
    backend: Any
    # `plugin:<plugin_id>:<asset>` for a plugin-hosted parameter modal; None
    # runs the operation straight from the bar with empty params.
    frontend_component: Optional[str] = None
    source: str = "core"


class PhrasebookOperationRegistry:
    """Registry mapping operation id -> `PhrasebookBatchOperationDefinition`."""

    def __init__(self):
        self._by_id: Dict[str, PhrasebookBatchOperationDefinition] = {}

    def register(self, definition: PhrasebookBatchOperationDefinition) -> None:
        if definition.op_id in self._by_id:
            raise DuplicatePhrasebookOperationError(
                f"Phrasebook batch operation already registered: '{definition.op_id}'"
            )
        self._by_id[definition.op_id] = definition

    def unregister_source(self, source: str) -> None:
        for op_id in [op_id for op_id, defn in self._by_id.items() if defn.source == source]:
            del self._by_id[op_id]

    def get(self, op_id: str) -> Optional[PhrasebookBatchOperationDefinition]:
        return self._by_id.get(op_id)

    def all(self) -> List[PhrasebookBatchOperationDefinition]:
        return list(self._by_id.values())

    def frontend_manifest(self) -> List[Dict[str, Any]]:
        """Serialize the registry for `GET /api/phrasebook/batch-ops`."""
        return [
            {
                "id": defn.op_id,
                "label": defn.label,
                "component": defn.frontend_component,
                "has_preview": bool(getattr(defn.backend, "supports_preview", False)),
                "source": defn.source,
            }
            for defn in self.all()
        ]


phrasebook_operation_registry = PhrasebookOperationRegistry()
