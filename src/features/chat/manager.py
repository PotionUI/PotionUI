"""
Chat session and message operations coordinator.

This module provides the ChatManager class, the single entry point onto the chat
feature. It owns no orchestration of its own: it composes the focused role
classes that do (session store, access policy, context builder, tool dispatcher,
conversation runner) and delegates each public operation to the
right one.

ChatManager stays a single injectable object because its collaborators depend on
it as one handle. The ChatController reaches through it for several attributes
(``tool_executor``, ``llm_memory_repository``, ``pre_chat_action_manager``,
``chat_mode_registry``), and the composition root late-binds most of the tool /
resource managers onto it *after* construction. The role classes therefore read
their dependencies back through this manager, so that late binding keeps working
without re-wiring seven separate objects.
"""

import logging
from typing import AsyncGenerator, Dict, List, Optional, Any, Tuple

from src.features.chat.dto import SessionResponse, SendMessageResponse
from src.features.chat.modes import ChatModeRegistry
from src.features.chat.response_processor import ResponseProcessor
from src.features.chat.title_generator import ChatTitleGenerator
from src.features.chat.reflection import ChatReflectionGenerator
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import HookContext
from src.platform.resources import ResourceSuggestion
from src.features.chat.repository import ChatRepository

from src.features.chat.policy import ChatPolicy
from src.features.chat.session_store import ChatSessionStore
from src.features.chat.context_builder import ChatContextBuilder
from src.features.chat.tool_dispatcher import ToolCallDispatcher
from src.features.chat.conversation import ConversationRunner

logger = logging.getLogger(__name__)


class ChatManager:
    """
    Orchestrates chat session and message operations.

    Combines repository access, LLM service calls, response processing,
    and plugin hook execution into cohesive chat workflows.
    """

    def __init__(
        self,
        chat_repository: ChatRepository,
        llm_service: 'LLMGateway',
        response_processor: ResponseProcessor,
        plugin_registry: PluginRegistry,
        chat_mode_registry: ChatModeRegistry,
        tool_executor: Optional[Any] = None,
        segment_manager: Optional[Any] = None,
        model_index_manager: Optional[Any] = None,
        preset_manager: Optional[Any] = None,
        phrasebook_manager: Optional[Any] = None,
        prompt_database_manager: Optional[Any] = None,
        generation_orchestrator: Optional[Any] = None,
        pre_chat_action_manager: Optional[Any] = None,
        llm_memory_repository: Optional[Any] = None,
        prompt_enhancement_manager: Optional[Any] = None,
        media_index_manager: Optional[Any] = None,
        resource_registry: Optional[Any] = None,
        settings_manager: Optional[Any] = None,
        collection_repository: Optional[Any] = None,
        tag_repository: Optional[Any] = None,
        generation_history_manager: Optional[Any] = None,
    ):
        """Initialize ChatManager.

        Args:
            chat_repository: Repository for chat data access
            llm_service: Service for LLM API calls
            response_processor: Processor for LLM response transformations
            plugin_registry: Plugin registry for hook execution
            chat_mode_registry: Registry of available chat modes
            tool_executor: Optional tool executor for LLM tool calling
            segment_manager: Optional segment manager for tool context
            model_index_manager: Optional model index manager for tool context
            preset_manager: Optional preset manager for tool context
            phrasebook_manager: Optional phrasebook manager for tool context
            prompt_database_manager: Optional prompt database manager for tool context
            generation_orchestrator: Optional generation orchestrator for tool context
            pre_chat_action_manager: Optional pre-chat action manager for executing actions before LLM calls
            llm_memory_repository: Optional LLM memory repository for tool context
            prompt_enhancement_manager: Optional manager backing the enhance_prompt tool
            media_index_manager: Optional media index manager backing the
                search_gallery tool
            resource_registry: Optional registry resolving @resource mentions
            settings_manager: Optional settings manager, read for the admin
                session-debug viewer's ``chat_llm_call_tracing`` flag
            collection_repository: Optional collection repository for tool context
            tag_repository: Optional tag repository for tool context
            generation_history_manager: Optional generation history manager
                for tool context
        """
        self.chat_repository = chat_repository
        self.llm_service = llm_service
        self.response_processor = response_processor
        self.plugins = plugin_registry
        self.chat_mode_registry = chat_mode_registry
        self.tool_executor = tool_executor
        self.segment_manager = segment_manager
        self.model_index_manager = model_index_manager
        self.preset_manager = preset_manager
        self.phrasebook_manager = phrasebook_manager
        self.prompt_database_manager = prompt_database_manager
        self.generation_orchestrator = generation_orchestrator
        self.pre_chat_action_manager = pre_chat_action_manager
        self.llm_memory_repository = llm_memory_repository
        self.prompt_enhancement_manager = prompt_enhancement_manager
        self.media_index_manager = media_index_manager
        self.resource_registry = resource_registry
        self.settings_manager = settings_manager
        self.collection_repository = collection_repository
        self.tag_repository = tag_repository
        self.generation_history_manager = generation_history_manager
        # Generation repositories for the @generations resource provider;
        # late-assigned in the composition root like model_index_manager/preset_manager.
        self.generation_repository: Optional[Any] = None
        self.generation_parameter_repository: Optional[Any] = None
        self.generation_model_repository: Optional[Any] = None
        self.title_generator = ChatTitleGenerator(llm_service, chat_repository)
        # Fire-and-forget title tasks; referenced so they aren't garbage-collected mid-run.
        self._title_tasks: set = set()
        # Takes `self`, not its collaborators directly — llm_memory_repository is
        # late-bound onto this manager after construction (see class docstring).
        self.reflection_generator = ChatReflectionGenerator(self)
        # Fire-and-forget reflection tasks; same GC-safety reason as _title_tasks.
        self._reflection_tasks: set = set()

        # Role classes. Each reads its dependencies back through this manager so
        # the composition root's post-construction late binding keeps working.
        self._policy = ChatPolicy(self)
        self._sessions = ChatSessionStore(self)
        self._context = ChatContextBuilder(self)
        self._tools = ToolCallDispatcher(self)
        self._conversation = ConversationRunner(self)

    # --- Hook Execution Helper (shared primitive) ---

    def _execute_hook(self, hook: str, data: dict) -> Tuple[dict, bool, HookContext]:
        """Execute a hook and return the context data, whether it was blocked,
        and the context itself (async call sites drain its blocking waits via
        `await_hook_blocking_waits` — see generation's orchestrator).

        Returns:
            Tuple of (context_data, blocked, context)
        """
        context, results = self.plugins.execute_hook(
            hook,
            initial_data=data
        )
        blocked = context.data.get("blocked", False)
        return context.data, blocked, context

    # --- Validation delegators (policy + session store) ---

    def _get_session_or_raise(self, session_id: str) -> SessionResponse:
        return self._sessions.get_or_raise(session_id)

    def _get_session_with_messages_or_raise(self, session_id: str) -> SessionResponse:
        return self._sessions.get_with_messages_or_raise(session_id)

    def _verify_ownership(self, session: SessionResponse, user_id: str) -> None:
        return self._policy.verify_ownership(session, user_id)

    def _verify_active(self, session: SessionResponse) -> None:
        return self._policy.verify_active(session)

    def _inject_memory_block(
        self,
        conversation_history: List[Dict[str, Any]],
        context_metadata: Optional[Dict[str, Any]],
        user_id: str,
    ) -> Dict[str, Any]:
        return self._context.inject_memory_block(conversation_history, context_metadata, user_id)

    # --- Session Operations ---

    def create_session(
        self,
        user_id: str,
        original_text: Optional[str] = None,
        llm_config_id: Optional[str] = None,
        mode: str = 'generation',
        name: Optional[str] = None,
        system_message: Optional[str] = None,
        enabled_tools: Optional[List[str]] = None,
    ) -> SessionResponse:
        return self._sessions.create_session(
            user_id=user_id,
            original_text=original_text,
            llm_config_id=llm_config_id,
            mode=mode,
            name=name,
            system_message=system_message,
            enabled_tools=enabled_tools,
        )

    def list_sessions(
        self,
        user_id: str,
        mode: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> Tuple[List[SessionResponse], int]:
        return self._sessions.list_sessions(
            user_id=user_id, mode=mode, search=search, limit=limit, offset=offset,
        )

    def get_session(self, session_id: str, user_id: str) -> SessionResponse:
        return self._sessions.get_session(session_id, user_id)

    def update_session(
        self,
        session_id: str,
        user_id: str,
        name: Optional[str] = None,
        llm_config_id: Optional[str] = None,
    ) -> SessionResponse:
        return self._sessions.update_session(session_id, user_id, name=name, llm_config_id=llm_config_id)

    def delete_session(self, session_id: str, user_id: str) -> bool:
        return self._sessions.delete_session(session_id, user_id)

    def accept_session(self, session_id: str, user_id: str) -> bool:
        return self._sessions.accept_session(session_id, user_id)

    def reject_session(self, session_id: str, user_id: str) -> bool:
        return self._sessions.reject_session(session_id, user_id)

    # --- Context / system prompt ---

    async def suggest_resources(
        self,
        query: str,
        mode_id: Optional[str],
        user_id: str,
        limit: int = 15,
    ) -> List[ResourceSuggestion]:
        return await self._context.suggest_resources(query, mode_id, user_id, limit=limit)

    # --- Message Operations ---

    async def send_message(
        self,
        session_id: str,
        user_id: str,
        content: str,
        image_data: Optional[str] = None,
        context_metadata: Optional[Dict[str, Any]] = None,
        resources: Optional[List[str]] = None,
    ) -> SendMessageResponse:
        return await self._conversation.send_message(
            session_id=session_id,
            user_id=user_id,
            content=content,
            image_data=image_data,
            context_metadata=context_metadata,
            resources=resources,
        )

    def send_message_stream(
        self,
        session_id: str,
        user_id: str,
        content: str,
        image_data: Optional[str] = None,
        context_metadata: Optional[Dict[str, Any]] = None,
        resources: Optional[List[str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        return self._conversation.send_message_stream(
            session_id=session_id,
            user_id=user_id,
            content=content,
            image_data=image_data,
            context_metadata=context_metadata,
            resources=resources,
        )

    async def record_prompt_feedback(
        self,
        session_id: str,
        user_id: str,
        message_id: str,
        action_index: int,
        verdict: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._conversation.record_prompt_feedback(
            session_id=session_id,
            user_id=user_id,
            message_id=message_id,
            action_index=action_index,
            verdict=verdict,
            reason=reason,
        )

    # --- Tool Approval Operations ---

    async def approve_tool_execution(
        self,
        session_id: str,
        user_id: str,
        message_id: str,
        tool_index: int,
        approved: bool,
    ) -> Dict[str, Any]:
        return await self._tools.approve_tool_execution(
            session_id=session_id,
            user_id=user_id,
            message_id=message_id,
            tool_index=tool_index,
            approved=approved,
        )

    # --- Admin session-debug viewer ---
    # Cross-user session listing and LLM call-trace inspection, gated at the
    # route level with get_current_admin_user. Reads chat_call_trace_repository
    # directly (module-level singleton, same pattern as llm_repository) rather
    # than threading it through construction, since it is only ever used here.

    def list_admin_sessions(
        self,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List chat sessions across ALL users for the admin debug viewer."""
        sessions, total = self.chat_repository.list_sessions_admin(
            search=search, limit=limit, offset=offset,
        )
        tracing_enabled = True
        if self.settings_manager is not None:
            tracing_enabled = bool(self.settings_manager.get_setting("chat_llm_call_tracing", True))
        return {"sessions": sessions, "total": total, "tracing_enabled": tracing_enabled}

    def get_admin_session_detail(self, session_id: str) -> Dict[str, Any]:
        """A session's messages plus every LLM call trace, for the debug viewer.

        Traces are grouped onto their message via ``trace["message_id"]``
        (None for calls not yet attributed to a message, e.g. an in-flight or
        failed turn, or a title-generation call).
        """
        from src.features.llm.trace_repository import chat_call_trace_repository

        session = self._get_session_with_messages_or_raise(session_id)
        traces = chat_call_trace_repository.list_for_session(session_id)
        return {
            "session": session.model_dump(),
            "traces": traces,
        }

    def clear_traces(self, session_id: Optional[str] = None) -> int:
        """Delete call traces for one session, or every session when omitted."""
        from src.features.llm.trace_repository import chat_call_trace_repository

        if session_id:
            return chat_call_trace_repository.delete_for_session(session_id)
        return chat_call_trace_repository.delete_all()
