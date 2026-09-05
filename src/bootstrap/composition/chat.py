"""Chat feature wiring.

`ChatRuntime` reaches almost every other domain through its tool context, so it
used to be constructed early with ``None`` placeholders and completed by
seventeen attribute assignments at the bottom of `build_container`. Nothing
built between those two points consumes a chat component, so the placeholders
were an ordering artefact, not a cycle: `build_chat` is called once every
collaborator exists and passes them all to the constructor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, TYPE_CHECKING

from src.features.chat import ChatRuntime, ResponseProcessor
from src.features.chat.pre_chat_actions import PreChatActionRegistry
from src.features.chat.repository import ChatRepository
from src.features.chat.routes import ChatController
from src.features.chat.turns import ChatTurnRegistry
from src.features.llm.gateway import LLMGateway
from src.features.llm.repository import LLMRepository
from src.platform.plugins import PluginRegistry
from src.platform.settings.settings import Settings

if TYPE_CHECKING:
    from src.features.chat.modes import ChatModeRegistry
    from src.features.generation.history_facade import GenerationHistoryFacade
    from src.features.generation.model_repository import GenerationModelRepository
    from src.features.generation.orchestrator import GenerationOrchestrator
    from src.features.generation.parameter_repository import GenerationParameterRepository
    from src.features.generation.repository import GenerationRepository
    from src.features.llm.tools.executor import ToolExecutor
    from src.features.phrasebook.repository import (
        PhrasebookCategoryRepository,
        PhrasebookValueRepository,
    )
    from src.features.segments.repository import (
        SavedSegmentRepository,
        SegmentCategoryRepository,
        SegmentTemplateRepository,
    )
    from src.platform.resources import ResourceRegistry


DEFAULT_TURN_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class ChatDeps:
    """Everything chat needs from the rest of the process."""

    llm_service: LLMGateway
    llm_repository: LLMRepository
    plugin_registry: PluginRegistry
    settings: Settings
    chat_mode_registry: "ChatModeRegistry"
    resource_registry: "ResourceRegistry"
    tool_executor: "ToolExecutor"
    tool_governance_repository: Any
    phrasebook_category_repository: "PhrasebookCategoryRepository"
    phrasebook_value_repository: "PhrasebookValueRepository"
    phrasebook_search: Callable[..., Any]
    segment_category_repository: "SegmentCategoryRepository"
    saved_segment_repository: "SavedSegmentRepository"
    segment_template_repository: "SegmentTemplateRepository"
    model_index_manager: Any
    preset_manager: Any
    prompt_database: Any
    prompt_enhancement_manager: Any
    generation_orchestrator: "GenerationOrchestrator"
    generation_repository: "GenerationRepository"
    generation_parameter_repository: "GenerationParameterRepository"
    generation_model_repository: "GenerationModelRepository"
    generation_history_facade: "GenerationHistoryFacade"
    llm_memory_repository: Any
    media_indexer: Any
    collection_repository: Any
    tag_repository: Any


@dataclass(frozen=True)
class ChatComponents:
    """What chat contributes back to the container."""

    chat_repository: ChatRepository
    response_processor: ResponseProcessor
    pre_chat_action_registry: PreChatActionRegistry
    chat_runtime: ChatRuntime
    chat_turn_registry: ChatTurnRegistry
    chat_controller: ChatController


def _turn_timeout_seconds(settings: Settings) -> int:
    try:
        return int(settings.get_setting("chat_turn_timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_TURN_TIMEOUT_SECONDS


def build_chat(deps: ChatDeps) -> ChatComponents:
    chat_repository = ChatRepository()
    response_processor = ResponseProcessor(plugin_registry=deps.plugin_registry)
    pre_chat_action_registry = PreChatActionRegistry(
        plugin_registry=deps.plugin_registry,
        llm_repository=deps.llm_repository,
    )
    chat_runtime = ChatRuntime(
        chat_repository=chat_repository,
        llm_service=deps.llm_service,
        response_processor=response_processor,
        plugin_registry=deps.plugin_registry,
        chat_mode_registry=deps.chat_mode_registry,
        tool_executor=deps.tool_executor,
        segment_category_repository=deps.segment_category_repository,
        saved_segment_repository=deps.saved_segment_repository,
        segment_template_repository=deps.segment_template_repository,
        model_index_manager=deps.model_index_manager,
        preset_manager=deps.preset_manager,
        phrasebook_category_repository=deps.phrasebook_category_repository,
        phrasebook_value_repository=deps.phrasebook_value_repository,
        phrasebook_search=deps.phrasebook_search,
        prompt_database=deps.prompt_database,
        generation_orchestrator=deps.generation_orchestrator,
        pre_chat_action_registry=pre_chat_action_registry,
        llm_memory_repository=deps.llm_memory_repository,
        prompt_enhancement_manager=deps.prompt_enhancement_manager,
        media_indexer=deps.media_indexer,
        resource_registry=deps.resource_registry,
        settings=deps.settings,
        collection_repository=deps.collection_repository,
        tag_repository=deps.tag_repository,
        generation_history_facade=deps.generation_history_facade,
        tool_governance_repository=deps.tool_governance_repository,
        generation_repository=deps.generation_repository,
        generation_parameter_repository=deps.generation_parameter_repository,
        generation_model_repository=deps.generation_model_repository,
    )

    # Turns are owned by a per-process registry so a client disconnect (page
    # reload) can't kill an in-flight response.
    chat_turn_registry = ChatTurnRegistry(turn_timeout_seconds=_turn_timeout_seconds(deps.settings))
    return ChatComponents(
        chat_repository=chat_repository,
        response_processor=response_processor,
        pre_chat_action_registry=pre_chat_action_registry,
        chat_runtime=chat_runtime,
        chat_turn_registry=chat_turn_registry,
        chat_controller=ChatController(chat_runtime, chat_turn_registry),
    )
