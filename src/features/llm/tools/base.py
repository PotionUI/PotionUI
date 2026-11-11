"""Base classes and data models for the LLM tool system."""

import inspect
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Hashable, List, Optional

logger = logging.getLogger(__name__)

MCP_NEEDS_LIVE_FORM = "Needs the live Generate form — available when called from chat."
MCP_USE_START_GENERATION = "Drives the open Generate form. Use start_generation over MCP."


@dataclass
class ToolSource:
    """A source reference attached to a tool result (e.g. a URL or document)."""
    source_type: str  # e.g. "url", "document", "preset"
    title: str
    subtitle: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    icon: Optional[str] = None


@dataclass
class ToolApprovalPreview:
    """A structured, human-facing preview of an action awaiting approval.

    Any `requires_approval` tool fills this on the ToolResult its `execute()`
    preview returns, so the approval surface can state *what* is about to happen
    in plain terms — the action verb, the thing it acts on, and the concrete
    items involved — instead of dumping raw arguments. All fields are optional;
    a tool that fills none falls back to a generic argument dump.
    """
    action: str  # e.g. "Remove", "Create category", "Add values"
    target: Optional[str] = None  # e.g. "from category camera"
    items: List[str] = field(default_factory=list)  # the concrete items acted on
    note: Optional[str] = None  # a caveat, e.g. "2 values already exist and will be skipped"
    # Full-fidelity per-operation before/after state, for a tool whose `items`
    # prose can't carry enough to review a change (e.g. a Video Director
    # segment/media edit): [{op, summary, before, after}], `before`/`after`
    # None for an add/remove respectively. Absent for tools that only need
    # `items`.
    changes: Optional[List[Dict[str, Any]]] = None
    # Discriminates the richer dock layouts below. None means a tool that
    # only fills the legacy action/target/items/note/changes fields above.
    kind: Optional[str] = None  # "generation" | "timeline" | "text_edit"
    # One-line text for the dock's collapsed row.
    summary: Optional[str] = None
    # Settings-grid rows: {"label", "value", "mono"?, "old"?}. `"old"` present
    # means the tool changed this value from what the user had -> the UI
    # flags it and renders old -> new.
    fields: Optional[List[Dict[str, Any]]] = None
    # Long-form text with an optional before/after diff: {"label", "text",
    # "old_text"?}. `"old_text"` present -> the UI renders a stacked diff.
    text_blocks: Optional[List[Dict[str, Any]]] = None
    # Timeline scenes: {"range", "text", "op"?}.
    rows: Optional[List[Dict[str, Any]]] = None


@dataclass
class ToolResult:
    """Result of a tool execution."""
    success: bool
    data: str  # Serialized result for LLM consumption
    error: Optional[str] = None
    sources: Optional[List[ToolSource]] = None
    image_data: Optional[str] = None  # Base64 image to send via LLM vision on next call
    preview: Optional[ToolApprovalPreview] = None  # Set by requires_approval tools' preview execute()
    retryable: bool = False


def serialize_approval_preview(preview: Optional[ToolApprovalPreview]) -> Optional[Dict[str, Any]]:
    """Flatten a ToolApprovalPreview to the wire/persistence dict, or None."""
    if preview is None:
        return None
    return {
        "action": preview.action,
        "target": preview.target,
        "items": list(preview.items),
        "note": preview.note,
        "changes": preview.changes,
        "kind": preview.kind,
        "summary": preview.summary,
        "fields": preview.fields,
        "text_blocks": preview.text_blocks,
        "rows": preview.rows,
    }


@dataclass(frozen=True)
class ToolServices:
    segment_category_repository: Any = None
    saved_segment_repository: Any = None
    segment_template_repository: Any = None
    model_index_manager: Any = None
    preset_collaborators: Any = None
    phrasebook_category_repository: Any = None
    phrasebook_value_repository: Any = None
    llm_repository: Any = None
    prompt_database: Any = None
    generation_orchestrator: Any = None
    llm_memory_repository: Any = None
    prompt_enhancement_manager: Any = None
    media_indexer: Any = None
    settings: Any = None
    collection_repository: Any = None
    tag_repository: Any = None
    plugin_registry: Any = None
    generation_history_facade: Any = None
    content_safety: Any = None
    library_collaborators: Any = None
    tab_drafts: Any = None
    media_tool_service: Any = None
    elixirs: Any = None


@dataclass
class ToolContext:
    """Context passed to tools during execution.

    Contains user info, session metadata, and service references
    needed by tools to gather application data.
    """
    user_id: str
    mode_id: Optional[str] = None
    is_admin: bool = False
    session_metadata: Dict[str, Any] = field(default_factory=dict)
    # Service references - injected at construction time
    segment_category_repository: Any = None
    saved_segment_repository: Any = None
    segment_template_repository: Any = None
    model_index_manager: Any = None
    preset_collaborators: Any = None
    phrasebook_category_repository: Any = None
    phrasebook_value_repository: Any = None
    llm_repository: Any = None
    # A `PromptDatabaseCollaborators` bundle (see
    # `src.features.prompt_database.collaborators`), or None.
    prompt_database: Any = None
    generation_orchestrator: Any = None
    llm_memory_repository: Any = None
    prompt_enhancement_manager: Any = None
    media_indexer: Any = None
    # Needed to resolve the user's storage root when a tool validates a media
    # value the model proposed (see `src.features.llm.tools.media_values`).
    settings: Any = None
    llm_id: Optional[str] = None
    collection_repository: Any = None
    tag_repository: Any = None
    # Needed by tools that mutate through `operations` module-level functions
    # requiring hooks to fire (e.g. organize_gallery's tag creation) - the
    # collaborator the old tag/collection managers held internally.
    plugin_registry: Any = None
    generation_history_facade: Any = None
    content_safety: Any = None
    library_collaborators: Any = None
    tab_drafts: Any = None
    media_tool_service: Any = None
    elixirs: Any = None
    chat_session: bool = True

    @classmethod
    def for_session(cls, services: ToolServices, user_id: str, **fields_: Any) -> "ToolContext":
        shared = {f.name: getattr(services, f.name) for f in fields(services)}
        return cls(user_id=user_id, **{**shared, **fields_})

    def storage_dir(self) -> Optional[str]:
        """The requesting user's file storage root, or None if unavailable."""
        if self.settings is None:
            return None
        try:
            return self.settings.get_file_storage_directory(self.user_id)
        except Exception:
            logger.warning("could not resolve the storage root for tool validation", exc_info=True)
            return None


@dataclass
class ToolExecution:
    """Record of a single tool execution."""
    tool_name: str
    arguments: Dict[str, Any]
    result: ToolResult
    duration_ms: int
    approval_status: Optional[str] = None
    skipped: bool = False
    tool_call_id: Optional[str] = None


class BaseTool(ABC):
    """Abstract base class for LLM tools.

    Tools provide LLMs with access to application data so they
    can make informed decisions when generating responses.
    """

    # Chat modes this tool belongs to. None means the tool is global and
    # visible in every mode. Set as a class attribute in subclasses (e.g.
    # ``modes = ["generation"]``); plugin loading may override per instance.
    modes: Optional[List[str]] = None

    # Optional lucide icon name for UI display of tool activity.
    icon: Optional[str] = None

    context_group: str = "core"

    name: str
    group: str = "Other"
    user_description: str = ""
    requires_approval: bool = False
    approval_per_call: bool = False
    read_only: bool = False
    spends: bool = False
    mcp: bool = False
    mcp_unavailable: Optional[str] = None

    @property
    def writes(self) -> bool:
        return not self.read_only

    @property
    def label(self) -> str:
        """Human-readable display name for the UI. Defaults to the title-cased name."""
        return self.name.replace('_', ' ').title()

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for the LLM."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema for tool parameters."""
        ...

    @property
    def hint(self) -> str:
        """System prompt hint: tells the LLM when/why to call this tool.

        Unlike `description` (which goes into the tool schema and describes what the tool does),
        `hint` goes into the system message and tells the LLM when to proactively use this tool.
        Override in subclasses. Returns empty string by default (tool won't appear in system prompt hints).
        """
        return ""

    def approval_required(self, arguments: Dict[str, Any]) -> bool:
        return self.requires_approval

    def schema_revision(self) -> Optional[Hashable]:
        return None

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        """Whether this tool should be advertised for the turn's form state.

        Called before schemas/hints are built so a tool that only makes sense
        under a specific form condition (e.g. a document that isn't loaded)
        disappears from the model's tool set entirely rather than being
        advertised and rejected at execute() time. Defaults to always
        available; override in subclasses that need form-state gating.
        """
        return True

    @abstractmethod
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Execute the tool with the given context and arguments.

        For tools with `requires_approval=True` this should return a *preview*
        of the proposed action (not apply it yet).
        """
        ...

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        """Apply the confirmed action after user approval.

        Only called for tools where `requires_approval=True`.  The default
        implementation raises NotImplementedError — override in subclasses that
        need it.
        """
        raise NotImplementedError(
            f"Tool '{self.name}' has requires_approval=True but does not implement execute_confirmed()"
        )

    def to_schema(self) -> Dict:
        """Returns OpenAI/Ollama-compatible tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }


class ApprovalTool(BaseTool):
    requires_approval = True
    repositories: tuple = ()
    unavailable_error = "Manager not available"

    def _unavailable(self, context: ToolContext) -> Optional[ToolResult]:
        if any(getattr(context, attr) is None for attr in self.repositories):
            return ToolResult(success=False, data="", error=self.unavailable_error)
        return None

    async def _run(self, step, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        unavailable = self._unavailable(context)
        if unavailable:
            return unavailable
        try:
            result = step(context, kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        except ValueError as exc:
            return ToolResult(success=False, data="", error=str(exc))

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        return await self._run(self._preview, context, kwargs)

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        return await self._run(self._apply, context, kwargs)

    def _preview(self, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    def _apply(self, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    def _proposal(self, preview: ToolApprovalPreview, payload: Dict[str, Any], message: str) -> ToolResult:
        return ToolResult(
            success=True,
            data=json.dumps({"action": self.name, "proposal": payload, "message": message}),
            preview=preview,
        )
