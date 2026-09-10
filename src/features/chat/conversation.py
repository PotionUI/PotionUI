"""Chat message send/stream orchestration.

The heart of a chat turn: validate the session, persist the user message,
assemble context, call the LLM (with or without tools), stream or collect the
response, persist the assistant message, fire hooks and kick off title
generation. Both the buffered (`send_message`) and streaming
(`send_message_stream`) paths live here, plus the learning-loop feedback
recorder and the title/pre-chat helpers they share.

Extracted from the ChatRuntime coordinator; it drives the other chat
collaborators (context builder) and reads its dependencies through the
manager so the composition root's late binding keeps working.
"""

import asyncio
import json
import logging
import re
import time
from contextlib import aclosing
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.features.chat.dto import SessionResponse, SendMessageResponse
from src.features.chat.exceptions import (
    AdminOnlyModeException,
    ContextBudgetExceededException,
    InvalidLLMConfigException,
    MessageCreationFailedException,
    PreChatActionError,
)
from src.features.chat.hooks import CHAT_MESSAGE_HOOKS, CHAT_RESPONSE_HOOKS
from src.features.chat.modes import GENERATION_MODE_ID, ChatMode
from src.features.chat.pre_chat_actions import PreChatActionResult
from src.features.chat.reply_contract import TOOL_LOOP_CONTINUATION_NUDGE
from src.features.llm import context_budget, trace_collector
from src.features.llm.trace_repository import chat_call_trace_repository
from src.features.llm.tools.base import ToolContext, ToolExecution, serialize_approval_preview
from src.features.llm.tools.builtin.utils import resolve_active_preset_id
from src.platform.plugins.hooks import await_hook_blocking_waits
from src.platform.util.imaging import convert_image_to_base64

logger = logging.getLogger(__name__)

# Read-with-default (no migration seed): 0 means unlimited, matching the
# "0 = unlimited" convention documented for the setting.
_DEFAULT_HISTORY_TOKEN_BUDGET = 8000
_HISTORY_BUDGET_CHARS_PER_TOKEN = 4


class ConversationRunner:
    """Orchestrates a chat turn end-to-end for both send and stream paths."""

    _TOOL_ACTION_CONTENT_RE = re.compile(
        r'<tool_action\s+type="update_(?:director_)?segment"[^>]*>([\s\S]*?)</tool_action>'
    )

    def __init__(self, manager):
        self._m = manager

    def _verify_mode_access(self, session: SessionResponse, is_admin: bool) -> None:
        """Reject a non-admin using a session whose mode is admin_only.

        Shared by both the buffered and streaming send paths, run right after
        ownership/active checks and before anything is persisted.
        """
        mode = self._m.chat_mode_registry.get(session.mode)
        if mode is not None and mode.admin_only and not is_admin:
            raise AdminOnlyModeException(f"Chat mode '{session.mode}' is restricted to administrators")

    async def send_message(
        self,
        session_id: str,
        user_id: str,
        content: str,
        image_data: Optional[str] = None,
        context_metadata: Optional[Dict[str, Any]] = None,
        resources: Optional[List[str]] = None,
        is_admin: bool = False,
    ) -> SendMessageResponse:
        """Send a message and get AI response.

        Executes hooks:
        - chat.message.before_send: Can modify content or block
        - chat.response.before_generate: Can modify history/config
        - chat.response.transform: Transform LLM output (via ResponseProcessor)
        - chat.response.after_save: Notification of saved response
        - chat.message.after_send: Notification of message exchange complete

        Args:
            session_id: The session ID
            user_id: The user ID (for ownership verification)
            content: The message content
            image_data: Optional image data (path or base64)

        Returns:
            SendMessageResponse with user_message and assistant_message

        Raises:
            SessionNotFoundException: If session not found
            AccessDeniedException: If user doesn't own the session
            SessionClosedException: If session is closed
            InvalidLLMConfigException: If no LLM config set
            MessageCreationFailedException: If message creation fails
        """
        # Get session (without messages for validation)
        session = self._m._get_session_or_raise(session_id)
        self._m._verify_ownership(session, user_id)
        self._m._verify_active(session)
        self._verify_mode_access(session, is_admin)

        if not session.llm_config_id:
            raise InvalidLLMConfigException("Session has no LLM configuration")

        # Execute before_send hook; drain blocking waits so a wait_for_completion
        # automation (e.g. clear VRAM) finishes before the message proceeds.
        hook_data, blocked, hook_ctx = self._m._execute_hook(
            CHAT_MESSAGE_HOOKS.before_send,
            {
                "session_id": session_id,
                "user_id": user_id,
                "content": content,
                "image_data": image_data
            }
        )
        await await_hook_blocking_waits(hook_ctx)

        if blocked:
            reason = hook_data.get("block_reason", "Message blocked")
            logger.warning(f"Message send blocked by plugin: {reason}")
            raise MessageCreationFailedException(reason)

        # Allow hooks to modify content
        content = hook_data.get("content", content)
        image_data = hook_data.get("image_data", image_data)

        # Behavior-trace bookkeeping.
        step_records: List[Dict[str, Any]] = []

        # Snapshot-resolve @resource mentions (never raises; stale refs become error notes)
        _resource_start = time.monotonic()
        resolved_resources = await self._m._context.resolve_message_resources(
            resources, user_id, session.mode, context_metadata
        )
        if resources:
            step_records.append({
                "step": "resolving_resources",
                "duration_ms": int((time.monotonic() - _resource_start) * 1000),
            })

        # Add user message to session (store image reference in metadata for chat display)
        user_metadata = {}
        if image_data:
            user_metadata["image_path"] = image_data
        if context_metadata and context_metadata.get("image_url"):
            user_metadata["image_url"] = context_metadata["image_url"]
        if resolved_resources:
            user_metadata["resources"] = self._m._context.resources_metadata(resolved_resources)
        if session.mode == GENERATION_MODE_ID:
            preset_id = resolve_active_preset_id((context_metadata or {}).get("form_state"))
            if preset_id:
                user_metadata["preset_id"] = preset_id
        user_message = self._m.chat_repository.add_message(
            session_id=session_id,
            role='user',
            content=content,
            metadata=user_metadata if user_metadata else None,
        )

        if not user_message:
            raise MessageCreationFailedException("Failed to save user message")

        # Build conversation history
        conversation_history = self._m.chat_repository.get_conversation_history(session_id)

        # Execute before_generate hook
        gen_hook_data, _, _ctx = self._m._execute_hook(
            CHAT_RESPONSE_HOOKS.before_generate,
            {
                "session_id": session_id,
                "conversation_history": conversation_history,
                "llm_config_id": session.llm_config_id
            }
        )
        # Allow hooks to modify generation parameters
        conversation_history = gen_hook_data.get("conversation_history", conversation_history)

        # Resolve the session's mode into prompt + allowed tools up front: the
        # injected blocks below must reference only tools the session can call
        # (e.g. the memory block's write_memory nudge). Cached, side-effect-free.
        system_prompt, allowed_tools, mode = self._m._context.resolve_session_prompt_and_tools(
            session, form_state=(context_metadata or {}).get("form_state"),
        )
        tools_enabled = self._m.tool_executor is not None and bool(allowed_tools)

        # Tools the mode's prompt describes but this session can't actually call
        # right now (toggled off, governed off, or unavailable) - without this
        # the model narrates using a tool it was never given. Computed even when
        # some tools ARE allowed so a partial set doesn't get promised either.
        withheld_tools = self._m._context.withheld_tools_for_session(
            session, form_state=(context_metadata or {}).get("form_state"),
        )
        if withheld_tools:
            step_records.append({"step": "tools", "duration_ms": 0})

        # Recalled memory is injected before the history budget runs so it is
        # counted against the budget instead of escaping it unbounded; the
        # budget call below protects it explicitly (min_protected=2) rather
        # than relying on the trim walk happening to keep it.
        _memory_start = time.monotonic()
        memory_result = self._m._context.inject_memory_block(
            conversation_history, context_metadata, user_id,
            write_memory_available="write_memory" in (allowed_tools or []),
            mode_id=session.mode,
        )
        step_records.append({"step": "loading_memory", "duration_ms": int((time.monotonic() - _memory_start) * 1000)})

        # Cap what actually goes to the LLM to a token budget (repo/UI keep the
        # full history regardless — see get_conversation_history's docstring).
        history_info = self._apply_history_budget(
            conversation_history, min_protected=2 if memory_result.get("injected_chars") else 1,
        )

        # Inject mode context + @resource snapshot right before the last user
        # message (contributor, resource, workspace, prompt state, then the
        # reply-contract reminder last so it lands closest to the user turn —
        # recency matters for a rule the model must not lose to a long system
        # prompt) — these run after the budget, same as before, so only the
        # memory block's size is guaranteed to count against it.
        await self._m._context.inject_contributor_block(conversation_history, session, context_metadata, user_id)
        self._m._context.inject_resource_block(conversation_history, resolved_resources)

        workspace_result = self._m._context.inject_workspace_block(conversation_history, context_metadata)
        self._m._context.inject_prompt_state_block(conversation_history, context_metadata)
        self._m._context.inject_reply_contract_reminder_block(conversation_history, mode)
        self._m._context.inject_tool_availability_block(conversation_history, allowed_tools, withheld_tools)

        tool_schemas = self._resolve_tool_schemas_for_ledger(allowed_tools)
        budget_accounting = self._resolve_budget_inputs(session, mode)
        try:
            context_ledger = self._build_context_ledger(
                system_prompt, tool_schemas, memory_result, conversation_history,
                accounting=budget_accounting, image_data=image_data,
            )
        except context_budget.ContextBudgetExceededError as e:
            raise ContextBudgetExceededException(str(e)) from e

        # Execute pre-chat actions (e.g., clear ComfyUI VRAM)
        enabled_pre_chat_actions = self._enabled_pre_chat_actions(session.llm_config_id)
        _pre_chat_start = time.monotonic()
        pre_chat_results = await self._run_pre_chat_actions(session.llm_config_id)
        if enabled_pre_chat_actions:
            step_records.append({
                "step": "running_pre_chat",
                "duration_ms": int((time.monotonic() - _pre_chat_start) * 1000),
            })

        # Convert image path to base64 if needed
        image_base64 = convert_image_to_base64(image_data)

        tool_executions: List[ToolExecution] = []

        _thinking_start = time.monotonic()
        if tools_enabled:
            # Build tool context with service references
            tool_context = ToolContext(
                user_id=user_id,
                mode_id=mode.id,
                is_admin=is_admin,
                session_metadata=context_metadata or {},
                segment_category_repository=self._m.segment_category_repository,
                saved_segment_repository=self._m.saved_segment_repository,
                segment_template_repository=self._m.segment_template_repository,
                model_index_manager=self._m.model_index_manager,
                preset_collaborators=self._m.preset_collaborators,
                phrasebook_category_repository=self._m.phrasebook_category_repository,
                phrasebook_value_repository=self._m.phrasebook_value_repository,
                llm_repository=self._m.llm_service.repository,
                prompt_database=self._m.prompt_database,
                generation_orchestrator=self._m.generation_orchestrator,
                llm_memory_repository=self._m.llm_memory_repository,
                prompt_enhancement_manager=self._m.prompt_enhancement_manager,
                media_indexer=self._m.media_indexer,
                settings=self._m.settings,
                collection_repository=self._m.collection_repository,
                tag_repository=self._m.tag_repository,
                plugin_registry=self._m.plugins,
                generation_history_facade=self._m.generation_history_facade,
                llm_id=session.llm_config_id,
            )

            # Route through tool executor
            with trace_collector.activate(session_id, user_id, purpose="chat_tools"):
                llm_response, tool_executions = await self._m.tool_executor.execute_with_tools(
                    messages=conversation_history,
                    llm_id=session.llm_config_id,
                    system_message=system_prompt or "",
                    tool_context=tool_context,
                    mode=mode.id,
                    image_data=image_base64,
                    allowed_tools=allowed_tools,
                    llm_options=mode.llm_options or None,
                    iteration_nudge=TOOL_LOOP_CONTINUATION_NUDGE if mode.structured_reply else None,
                )
        else:
            # Standard path - direct LLM call without tools
            with trace_collector.activate(session_id, user_id, purpose="chat"):
                llm_response = await self._m.llm_service.generate_with_history(
                    messages=conversation_history,
                    llm_id=session.llm_config_id,
                    image_data=image_base64,
                    custom_system_message=system_prompt or None,
                    mode=mode.id,
                    options_override=mode.llm_options or None,
                )
        # No distinct "answering" phase to time on the buffered path — the whole
        # LLM round-trip (including any tool loop) is one blocking call, so it is
        # all recorded as a single "thinking" step.
        step_records.append({"step": "thinking", "duration_ms": int((time.monotonic() - _thinking_start) * 1000)})

        # Process response through ResponseProcessor (includes CHAT_RESPONSE_TRANSFORM hook)
        cleaned_content, parsed_content = self._m.response_processor.process(
            llm_response.content,
            mode=session.mode
        )

        # Build assistant message metadata
        assistant_metadata: Dict[str, Any] = {
            'model': llm_response.model,
            'tokens_used': llm_response.tokens_used,
            'prompt_tokens': llm_response.prompt_tokens,
            'completion_tokens': llm_response.completion_tokens,
            'behavior_trace': self._build_behavior_trace(
                mode=mode,
                session=session,
                resolved_resources=resolved_resources,
                memory_result=memory_result,
                workspace_result=workspace_result,
                pre_chat_results=pre_chat_results,
                tool_executions=tool_executions,
                rescues=getattr(llm_response, "rescues", None),
                prompt_tokens=llm_response.prompt_tokens,
                completion_tokens=llm_response.completion_tokens,
                steps=step_records,
                tools_offered=allowed_tools,
                tools_withheld=withheld_tools,
                history_info=history_info,
                image_base64=image_base64,
                context_ledger=context_ledger,
                tool_failures=getattr(llm_response, "tool_failures", None),
                thinking_mode=getattr(llm_response, "thinking_mode", None),
                completion=getattr(llm_response, "completion", None),
            ),
        }

        # Include tool execution records if any
        if tool_executions:
            assistant_metadata['tool_executions'] = [
                {
                    'tool_name': te.tool_name,
                    'arguments': te.arguments,
                    'result': {
                        'success': te.result.success,
                        'data': te.result.data,
                        'error': te.result.error,
                    },
                    'duration_ms': te.duration_ms,
                    'pending_approval': te.pending_approval,
                    'preview': serialize_approval_preview(te.result.preview),
                }
                for te in tool_executions
            ]
            # Store context_metadata so approve_tool_execution can rebuild ToolContext
            if context_metadata:
                assistant_metadata['context_metadata'] = context_metadata

        # Add assistant message to session
        assistant_message = self._m.chat_repository.add_message(
            session_id=session_id,
            role='assistant',
            content=cleaned_content,
            parsed_content=parsed_content,
            metadata=assistant_metadata,
        )

        if not assistant_message:
            raise MessageCreationFailedException("Failed to save assistant message")

        self._backfill_trace_message_id(session_id, assistant_message.id)

        # Execute after_save hook
        self._m._execute_hook(
            CHAT_RESPONSE_HOOKS.after_save,
            {
                "message_id": assistant_message.id,
                "session_id": session_id,
                "content": cleaned_content
            }
        )

        # Execute after_send hook
        self._m._execute_hook(
            CHAT_MESSAGE_HOOKS.after_send,
            {
                "session_id": session_id,
                "user_id": user_id,
                "user_message_id": user_message.id,
                "assistant_message_id": assistant_message.id
            }
        )

        logger.debug(f"Message exchange complete in session {session_id}")

        # Fire-and-forget: the frontend picks the new name up on session refetch
        self._start_title_task(session, session_id)
        self._start_reflection_task(session_id, (context_metadata or {}).get("form_state"))

        return SendMessageResponse(
            user_message=user_message,
            assistant_message=assistant_message
        )

    async def send_message_stream(
        self,
        session_id: str,
        user_id: str,
        content: str,
        image_data: Optional[str] = None,
        context_metadata: Optional[Dict[str, Any]] = None,
        resources: Optional[List[str]] = None,
        is_admin: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Send a message and stream the AI response as an async generator.

        Validation errors (session not found, access denied, etc.) are raised
        immediately before any yielding. Errors during streaming are yielded as
        error events.

        Args:
            session_id: The session ID
            user_id: The user ID (for ownership verification)
            content: The message content
            image_data: Optional image data (path or base64)
            context_metadata: Optional context metadata

        Yields:
            Dicts with 'event' and 'data' keys describing streaming events.

        Raises:
            SessionNotFoundException: If session not found (before first yield)
            AccessDeniedException: If user doesn't own the session (before first yield)
            SessionClosedException: If session is closed (before first yield)
            InvalidLLMConfigException: If no LLM config set (before first yield)
            MessageCreationFailedException: If user message creation fails (before first yield)
        """
        # --- Validation (raises before any yield) ---
        session = self._m._get_session_or_raise(session_id)
        self._m._verify_ownership(session, user_id)
        self._m._verify_active(session)
        self._verify_mode_access(session, is_admin)

        if not session.llm_config_id:
            raise InvalidLLMConfigException("Session has no LLM configuration")

        # Execute before_send hook; drain blocking waits so a wait_for_completion
        # automation (e.g. clear VRAM) finishes before the message proceeds.
        hook_data, blocked, hook_ctx = self._m._execute_hook(
            CHAT_MESSAGE_HOOKS.before_send,
            {
                "session_id": session_id,
                "user_id": user_id,
                "content": content,
                "image_data": image_data
            }
        )
        await await_hook_blocking_waits(hook_ctx)

        if blocked:
            reason = hook_data.get("block_reason", "Message blocked")
            logger.warning(f"Message send blocked by plugin: {reason}")
            raise MessageCreationFailedException(reason)

        content = hook_data.get("content", content)
        image_data = hook_data.get("image_data", image_data)

        # Behavior-trace bookkeeping.
        step_records: List[Dict[str, Any]] = []

        # Snapshot-resolve @resource mentions (never raises; stale refs become error notes)
        if resources:
            _resource_start = time.monotonic()
            yield {"event": "status", "data": {"step": "resolving_resources", "state": "started"}}
            resolved_resources = await self._m._context.resolve_message_resources(
                resources, user_id, session.mode, context_metadata
            )
            step_records.append({
                "step": "resolving_resources",
                "duration_ms": int((time.monotonic() - _resource_start) * 1000),
            })
            yield {
                "event": "status",
                "data": {
                    "step": "resolving_resources",
                    "state": "completed",
                    "detail": {
                        "count": len(resolved_resources),
                        "uris": [r.uri for r in resolved_resources],
                    },
                },
            }
        else:
            resolved_resources = await self._m._context.resolve_message_resources(
                resources, user_id, session.mode, context_metadata
            )

        # Save user message (store image reference in metadata for chat display)
        user_metadata = {}
        if image_data:
            user_metadata["image_path"] = image_data
        if context_metadata and context_metadata.get("image_url"):
            user_metadata["image_url"] = context_metadata["image_url"]
        if resolved_resources:
            user_metadata["resources"] = self._m._context.resources_metadata(resolved_resources)
        if session.mode == GENERATION_MODE_ID:
            preset_id = resolve_active_preset_id((context_metadata or {}).get("form_state"))
            if preset_id:
                user_metadata["preset_id"] = preset_id
        user_message = self._m.chat_repository.add_message(
            session_id=session_id,
            role='user',
            content=content,
            metadata=user_metadata if user_metadata else None,
        )

        if not user_message:
            raise MessageCreationFailedException("Failed to save user message")

        # Build conversation history
        conversation_history = self._m.chat_repository.get_conversation_history(session_id)

        # Execute before_generate hook
        gen_hook_data, _, _ctx = self._m._execute_hook(
            CHAT_RESPONSE_HOOKS.before_generate,
            {
                "session_id": session_id,
                "conversation_history": conversation_history,
                "llm_config_id": session.llm_config_id
            }
        )
        conversation_history = gen_hook_data.get("conversation_history", conversation_history)

        # Resolve the session's mode into prompt + allowed tools up front: the
        # injected blocks below must reference only tools the session can call
        # (e.g. the memory block's write_memory nudge). Cached, side-effect-free.
        system_prompt, allowed_tools, mode = self._m._context.resolve_session_prompt_and_tools(
            session, form_state=(context_metadata or {}).get("form_state"),
        )
        tools_enabled = self._m.tool_executor is not None and bool(allowed_tools)

        # Tools the mode's prompt describes but this session can't actually call
        # right now (toggled off, governed off, or unavailable) - without this
        # the model narrates using a tool it was never given. Computed even when
        # some tools ARE allowed so a partial set doesn't get promised either.
        withheld_tools = self._m._context.withheld_tools_for_session(
            session, form_state=(context_metadata or {}).get("form_state"),
        )
        if withheld_tools:
            step_records.append({"step": "tools", "duration_ms": 0})
            yield {
                "event": "status",
                "data": {
                    "step": "tools",
                    "state": "completed",
                    "detail": {"offered": list(allowed_tools or []), "withheld": withheld_tools},
                },
            }

        # Recalled memory is injected before the history budget runs so it is
        # counted against the budget instead of escaping it unbounded; the
        # budget call below protects it explicitly (min_protected=2) rather
        # than relying on the trim walk happening to keep it.
        _memory_start = time.monotonic()
        yield {"event": "status", "data": {"step": "loading_memory", "state": "started"}}
        memory_result = self._m._context.inject_memory_block(
            conversation_history, context_metadata, user_id,
            write_memory_available="write_memory" in (allowed_tools or []),
            mode_id=session.mode,
        )
        step_records.append({
            "step": "loading_memory",
            "duration_ms": int((time.monotonic() - _memory_start) * 1000),
        })
        yield {
            "event": "status",
            "data": {
                "step": "loading_memory",
                "state": "completed",
                "detail": {
                    "note_count": len(memory_result["note_ids"]),
                    "by_scope": memory_result["by_scope"],
                    "by_scope_dropped": memory_result.get("by_scope_dropped"),
                    "injected_chars": memory_result.get("injected_chars"),
                },
            },
        }

        # Cap what actually goes to the LLM to a token budget (repo/UI keep the
        # full history regardless — see get_conversation_history's docstring).
        history_info = self._apply_history_budget(
            conversation_history, min_protected=2 if memory_result.get("injected_chars") else 1,
        )

        # Inject mode context + @resource snapshot right before the last user
        # message (contributor, resource, workspace, prompt state, then the
        # reply-contract reminder last so it lands closest to the user turn —
        # recency matters for a rule the model must not lose to a long system
        # prompt) — these run after the budget, same as before, so only the
        # memory block's size is guaranteed to count against it.
        await self._m._context.inject_contributor_block(conversation_history, session, context_metadata, user_id)
        self._m._context.inject_resource_block(conversation_history, resolved_resources)

        workspace_result = self._m._context.inject_workspace_block(conversation_history, context_metadata)
        self._m._context.inject_prompt_state_block(conversation_history, context_metadata)
        self._m._context.inject_reply_contract_reminder_block(conversation_history, mode)
        self._m._context.inject_tool_availability_block(conversation_history, allowed_tools, withheld_tools)

        tool_schemas = self._resolve_tool_schemas_for_ledger(allowed_tools)
        budget_accounting = self._resolve_budget_inputs(session, mode)
        try:
            context_ledger = self._build_context_ledger(
                system_prompt, tool_schemas, memory_result, conversation_history,
                accounting=budget_accounting, image_data=image_data,
            )
        except context_budget.ContextBudgetExceededError as e:
            raise ContextBudgetExceededException(str(e)) from e

        # Execute pre-chat actions (e.g., clear ComfyUI VRAM)
        enabled_pre_chat_actions = self._enabled_pre_chat_actions(session.llm_config_id)
        if enabled_pre_chat_actions:
            _pre_chat_start = time.monotonic()
            yield {"event": "status", "data": {"step": "running_pre_chat", "state": "started"}}
            pre_chat_results = await self._run_pre_chat_actions(session.llm_config_id)
            step_records.append({
                "step": "running_pre_chat",
                "duration_ms": int((time.monotonic() - _pre_chat_start) * 1000),
            })
            yield {
                "event": "status",
                "data": {
                    "step": "running_pre_chat",
                    "state": "completed",
                    "detail": {"actions": [r.action_id for r in pre_chat_results]},
                },
            }
        else:
            pre_chat_results = await self._run_pre_chat_actions(session.llm_config_id)

        # Convert image path to base64 if needed
        image_base64 = convert_image_to_base64(image_data)

        # --- Streaming starts here ---
        yield {"event": "message_created", "data": {"user_message_id": user_message.id, "assistant_message_id": ""}}

        full_content = ""
        tool_executions: List[ToolExecution] = []
        rescues: Optional[List[Dict[str, Any]]] = None
        tool_failures: Optional[Any] = None
        thinking_mode: Optional[Dict[str, Any]] = None
        turn_completion: Optional[Dict[str, Any]] = None
        usage_data: Dict[str, Any] = {}

        _thinking_start = time.monotonic()
        yield {"event": "status", "data": {"step": "thinking", "state": "started"}}
        _answering_started = False

        try:
            if tools_enabled:
                tool_context = ToolContext(
                    user_id=user_id,
                    mode_id=mode.id,
                    is_admin=is_admin,
                    session_metadata=context_metadata or {},
                    segment_category_repository=self._m.segment_category_repository,
                saved_segment_repository=self._m.saved_segment_repository,
                segment_template_repository=self._m.segment_template_repository,
                    model_index_manager=self._m.model_index_manager,
                    preset_collaborators=self._m.preset_collaborators,
                    phrasebook_category_repository=self._m.phrasebook_category_repository,
                phrasebook_value_repository=self._m.phrasebook_value_repository,
                    llm_repository=self._m.llm_service.repository,
                    prompt_database=self._m.prompt_database,
                    generation_orchestrator=self._m.generation_orchestrator,
                    llm_memory_repository=self._m.llm_memory_repository,
                    prompt_enhancement_manager=self._m.prompt_enhancement_manager,
                    media_indexer=self._m.media_indexer,
                    settings=self._m.settings,
                    collection_repository=self._m.collection_repository,
                    tag_repository=self._m.tag_repository,
                    plugin_registry=self._m.plugins,
                    generation_history_facade=self._m.generation_history_facade,
                    llm_id=session.llm_config_id,
                )

                with trace_collector.activate(session_id, user_id, purpose="chat_tools"):
                    # Owns the executor's stream: this generator being closed
                    # (the caller's subscriber disconnecting, or an exception
                    # unwinding through it) must close the tool executor's
                    # stream — which, through its own equivalent scopes,
                    # reaches the workflow, the turn source and the provider
                    # — rather than abandon a still-open provider connection.
                    async with aclosing(self._m.tool_executor.execute_with_tools_stream(
                        messages=conversation_history,
                        llm_id=session.llm_config_id,
                        system_message=system_prompt or "",
                        tool_context=tool_context,
                        mode=mode.id,
                        image_data=image_base64,
                        allowed_tools=allowed_tools,
                        llm_options=mode.llm_options or None,
                        iteration_nudge=TOOL_LOOP_CONTINUATION_NUDGE if mode.structured_reply else None,
                    )) as agen:
                        async for event in agen:
                            if event["type"] in ("tool_start", "tool_end"):
                                logger.debug(f"[ChatRuntime] Yielding SSE event: {event['type']} - {event['data'].get('tool_name', '')}")
                                yield {"event": event["type"], "data": event["data"]}
                            elif event["type"] == "status":
                                yield {"event": "status", "data": event["data"]}
                            elif event["type"] == "token":
                                if not _answering_started:
                                    _answering_start = time.monotonic()
                                    step_records.append({
                                        "step": "thinking",
                                        "duration_ms": int((_answering_start - _thinking_start) * 1000),
                                    })
                                    yield {"event": "status", "data": {"step": "answering", "state": "started"}}
                                    _answering_started = True
                                full_content += event["data"]["content"]
                                yield {"event": "token", "data": event["data"]}
                            elif event["type"] == "done":
                                tool_executions = event["data"]["tool_executions"]
                                rescues = event["data"].get("rescues")
                                tool_failures = event["data"].get("tool_failures")
                                thinking_mode = event["data"].get("thinking_mode")
                                turn_completion = event["data"].get("completion")
                                if not full_content:
                                    full_content = event["data"].get("full_content", "")
                                # Extract token usage from done event
                                usage_data = {
                                    "tokens_used": event["data"].get("tokens_used"),
                                    "prompt_tokens": event["data"].get("prompt_tokens"),
                                    "completion_tokens": event["data"].get("completion_tokens"),
                                }
                if not _answering_started:
                    # No token was ever emitted (e.g. empty tool-path response) — still
                    # close out "thinking" and mark "answering" for a consistent trace.
                    _answering_start = time.monotonic()
                    step_records.append({
                        "step": "thinking",
                        "duration_ms": int((_answering_start - _thinking_start) * 1000),
                    })
                    yield {"event": "status", "data": {"step": "answering", "state": "started"}}
                    _answering_started = True
            else:
                # "answering" starts immediately before the token loop — the no-tools
                # path has no separate tool phase, so "thinking" is effectively instant.
                _answering_start = time.monotonic()
                step_records.append({
                    "step": "thinking",
                    "duration_ms": int((_answering_start - _thinking_start) * 1000),
                })
                yield {"event": "status", "data": {"step": "answering", "state": "started"}}
                _answering_started = True

                # Stream tokens from LLM (yields dicts with type "token" or "usage")
                with trace_collector.activate(session_id, user_id, purpose="chat"):
                    # Owns the gateway's stream — same reasoning as the
                    # tools-path scope above: this generator closing must
                    # close the gateway's, reaching the provider's own
                    # stream rather than abandoning it.
                    async with aclosing(self._m.llm_service.stream_with_history(
                        messages=conversation_history,
                        llm_id=session.llm_config_id,
                        image_data=image_base64,
                        custom_system_message=system_prompt or None,
                        mode=mode.id,
                        options_override=mode.llm_options or None,
                    )) as agen:
                        async for event in agen:
                            if event["type"] == "token":
                                full_content += event["content"]
                                yield {"event": "token", "data": {"content": event["content"]}}
                            elif event["type"] == "usage":
                                usage_data = {
                                    "tokens_used": event.get("tokens_used"),
                                    "prompt_tokens": event.get("prompt_tokens"),
                                    "completion_tokens": event.get("completion_tokens"),
                                }
                                thinking_mode = event.get("thinking_mode")
                                turn_completion = event.get("completion")

            step_records.append({
                "step": "answering",
                "duration_ms": int((time.monotonic() - _answering_start) * 1000),
            })

            # Post-stream: process, save, and notify
            cleaned_content, parsed_content = self._m.response_processor.process(
                full_content,
                mode=session.mode
            )

            assistant_metadata: Dict[str, Any] = {
                'behavior_trace': self._build_behavior_trace(
                    mode=mode,
                    session=session,
                    resolved_resources=resolved_resources,
                    memory_result=memory_result,
                    workspace_result=workspace_result,
                    pre_chat_results=pre_chat_results,
                    tool_executions=tool_executions,
                    rescues=rescues,
                    prompt_tokens=usage_data.get('prompt_tokens'),
                    completion_tokens=usage_data.get('completion_tokens'),
                    steps=step_records,
                    tools_offered=allowed_tools,
                    tools_withheld=withheld_tools,
                    history_info=history_info,
                    image_base64=image_base64,
                    context_ledger=context_ledger,
                    tool_failures=tool_failures,
                    thinking_mode=thinking_mode,
                    completion=turn_completion,
                ),
            }

            # Include token usage data in metadata
            if usage_data:
                assistant_metadata['tokens_used'] = usage_data.get('tokens_used')
                assistant_metadata['prompt_tokens'] = usage_data.get('prompt_tokens')
                assistant_metadata['completion_tokens'] = usage_data.get('completion_tokens')

            if tool_executions:
                assistant_metadata['tool_executions'] = [
                    {
                        'tool_name': te.tool_name,
                        'arguments': te.arguments,
                        'result': {
                            'success': te.result.success,
                            'data': te.result.data,
                            'error': te.result.error,
                        },
                        'duration_ms': te.duration_ms,
                        'pending_approval': te.pending_approval,
                        'preview': serialize_approval_preview(te.result.preview),
                    }
                    for te in tool_executions
                ]
                # Store context_metadata so approve_tool_execution can rebuild ToolContext
                if context_metadata:
                    assistant_metadata['context_metadata'] = context_metadata

            assistant_message = self._m.chat_repository.add_message(
                session_id=session_id,
                role='assistant',
                content=cleaned_content,
                parsed_content=parsed_content,
                metadata=assistant_metadata if assistant_metadata else None,
            )

            if not assistant_message:
                yield {"event": "error", "data": {"error": "message_creation_failed", "message": "Failed to save assistant message"}}
                return

            self._backfill_trace_message_id(session_id, assistant_message.id)

            # Execute after_save hook
            self._m._execute_hook(
                CHAT_RESPONSE_HOOKS.after_save,
                {
                    "message_id": assistant_message.id,
                    "session_id": session_id,
                    "content": cleaned_content
                }
            )

            # Execute after_send hook
            self._m._execute_hook(
                CHAT_MESSAGE_HOOKS.after_send,
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "user_message_id": user_message.id,
                    "assistant_message_id": assistant_message.id
                }
            )

            logger.debug(f"Streaming message exchange complete in session {session_id}")

            title_task = self._start_title_task(session, session_id)
            self._start_reflection_task(session_id, (context_metadata or {}).get("form_state"))

            yield {
                "event": "done",
                "data": {
                    "assistant_message": assistant_message.model_dump(),
                    "user_message": user_message.model_dump()
                }
            }

            if title_task is not None:
                try:
                    title = await asyncio.wait_for(title_task, timeout=15)
                    if title:
                        yield {"event": "title", "data": {"session_id": session_id, "name": title}}
                except Exception as e:
                    logger.debug(f"Title generation did not complete for session {session_id}: {e}")

        except Exception as e:
            logger.exception(f"Error during streaming in session {session_id}: {e}")
            yield {"event": "error", "data": {"error": "stream_error", "message": str(e)}}

    def _start_title_task(self, session: SessionResponse, session_id: str) -> Optional[asyncio.Task]:
        """Start async title generation if the session needs one.

        Returns the task (streaming path awaits it to emit a `title` event)
        or None when no generation is warranted. Never raises.
        """
        try:
            message_count = self._m.chat_repository.count_messages(session_id)
            if not self._m.title_generator.should_generate(session, message_count):
                return None
            task = asyncio.create_task(self._m.title_generator.generate(session_id))
            self._m._title_tasks.add(task)
            task.add_done_callback(self._m._title_tasks.discard)
            return task
        except Exception as e:
            logger.warning(f"Could not start title generation for session {session_id}: {e}")
            return None

    def _start_reflection_task(self, session_id: str, form_state: Optional[Dict[str, Any]] = None) -> None:
        """Fire-and-forget memory reflection after a turn.

        Never awaited by the response path — unlike the title task, nothing
        downstream needs its result this turn, so a slow or failed reflection
        call can never delay or break what the user sees. Delegates entirely
        to ``ChatReflectionGenerator.trigger``, which owns the per-session
        single-flight/coalescing bookkeeping (at most one pass in flight per
        session) and its own trigger-condition gating — this call site does
        not need to know whether a pass is already running.

        ``form_state`` is this turn's active preset/model context — it only
        exists on the live turn, not the persisted session, so it must be
        passed in here rather than re-resolved inside the task.
        """
        self._m.reflection_generator.trigger(session_id, form_state)

    def _history_token_budget(self) -> int:
        """Read the `chat_history_token_budget` setting; 0 or unset/unparsable = unlimited."""
        try:
            value = self._m.settings.get_setting(
                'chat_history_token_budget', _DEFAULT_HISTORY_TOKEN_BUDGET
            )
            if value is not None:
                return int(value)
        except Exception:
            logger.debug("Could not read chat_history_token_budget setting", exc_info=True)
        return _DEFAULT_HISTORY_TOKEN_BUDGET

    def _resolve_budget_inputs(
        self, session: SessionResponse, mode: Optional[ChatMode],
    ) -> Optional[context_budget.AccountingInputs]:
        """This turn's accounting inputs (capacity, reserve, tokenizer(s),
        per-image override) for the context-ledger pre-flight check in
        ``_build_context_ledger`` — built via ``LLMGateway.accounting_inputs_for``,
        the SAME builder ``estimate_context_budget`` uses for every real send,
        so the pre-flight number can never diverge from what actually goes
        out on the wire. Degrades to ``None`` — the ledger then falls back to
        unknown capacity, the module's default reserve, chars-per-token
        estimate — when the config can't be resolved; the LLM call itself
        raises its own clear error on a genuinely missing configuration.
        """
        llm_config_id = getattr(session, "llm_config_id", None)
        if not llm_config_id:
            return None
        # Best-effort like every other per-turn lookup in this module (memory,
        # workspace, contributor block): a test double or a not-yet-composed
        # collaborator standing in for the real LLMGateway/LLMConfig must
        # degrade to the unknown-capacity/default-reserve/estimate fallback,
        # never break the send.
        try:
            config = self._m.llm_service.repository.get_configuration(llm_config_id)
            if config is None:
                return None
            mode_options = (mode.llm_options or {}) if mode else {}
            inputs = self._m.llm_service.accounting_inputs_for(config, mode_options or None)
            int(inputs.reserve_tokens)  # validate now — a non-numeric test double degrades below, not mid-ledger
            return inputs
        except Exception:
            logger.debug("Could not resolve LLM config for context budget", exc_info=True)
            return None

    def _apply_history_budget(
        self, conversation_history: List[Dict[str, Any]], min_protected: int = 1,
    ) -> Dict[str, Any]:
        """Trim `conversation_history` in place to a token budget before it reaches the LLM.

        Uses a chars/4 heuristic per message. Whole messages only, oldest
        dropped first. The last ``min_protected`` messages are always kept
        regardless of their combined size — normally just the current (last)
        message, but a caller that has already inserted a system block right
        before it (e.g. the recalled-memory block) passes ``min_protected=2``
        so that block can never be a trim casualty even if it alone would
        blow the budget, instead of relying on the backward walk happening to
        keep it. This only affects what's sent to the LLM for this turn — the
        stored conversation in the repository/UI is untouched.

        Returns the manifest entry recorded into the behavior trace:
        ``{"messages_sent": n, "messages_total": n, "truncated": bool}``.
        """
        total = len(conversation_history)
        if total == 0:
            return {"messages_sent": 0, "messages_total": 0, "truncated": False}

        budget_tokens = self._history_token_budget()
        if budget_tokens <= 0:
            return {"messages_sent": total, "messages_total": total, "truncated": False}

        budget_chars = budget_tokens * _HISTORY_BUDGET_CHARS_PER_TOKEN
        min_protected = max(1, min(min_protected, total))

        # Always keep the protected tail regardless of its size, then walk
        # backwards keeping whole messages while they still fit.
        protected = conversation_history[-min_protected:]
        kept_reversed = list(reversed(protected))
        used_chars = sum(len(msg.get("content", "") or "") for msg in protected)
        for msg in reversed(conversation_history[:-min_protected]):
            content_len = len(msg.get("content", "") or "")
            if used_chars + content_len > budget_chars:
                break
            kept_reversed.append(msg)
            used_chars += content_len

        kept = list(reversed(kept_reversed))
        truncated = len(kept) < total
        if truncated:
            conversation_history[:] = kept

        return {"messages_sent": len(kept), "messages_total": total, "truncated": truncated}

    def _resolve_tool_schemas_for_ledger(self, allowed_tools: Optional[List[str]]) -> List[Dict[str, Any]]:
        """Fetch the resolved tool schemas the executor would actually send this turn.

        Best-effort like the rest of the trace plumbing — a registry lookup
        failure (or a test double that doesn't model ``get_schemas``) yields
        an empty list rather than breaking the send.
        """
        if not allowed_tools:
            return []
        try:
            registry = self._m.tool_executor.tool_registry if self._m.tool_executor else None
        except Exception:
            return []
        if not registry:
            return []
        try:
            schemas = registry.get_schemas(allowed_tools)
        except Exception:
            return []
        return schemas if isinstance(schemas, list) else []

    @staticmethod
    def _build_context_ledger(
        system_prompt: Optional[str],
        tool_schemas: List[Dict[str, Any]],
        memory_result: Dict[str, Any],
        conversation_history: List[Dict[str, Any]],
        *,
        accounting: Optional[context_budget.AccountingInputs] = None,
        image_data: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Per-turn size accounting for what actually reached the LLM.

        The top-level fields (``system_prompt``/``tool_schemas``/``memory``/
        ``history``/``total_est_tokens``) are the original chars/4 display
        heuristic, unchanged, and size ``conversation_history`` as passed in —
        the messages actually sent this turn, after budgeting and after every
        context block (contributor, resource, memory, workspace, prompt
        state, reply-contract reminder) has been injected; it therefore
        already includes the memory block's chars, which ``memory`` also
        reports on its own so memory's share of the total is visible at a
        glance.

        ``budget`` is the model-aware accounting from
        ``context_budget.enforce_budget`` — the SAME
        ``context_budget.AccountingInputs`` bundle (capacity/reserve/
        tokenizer(s)/per-image override) ``LLMGateway`` builds for the actual
        wire call (see ``LLMGateway.accounting_inputs_for`` and
        ``ConversationRunner._resolve_budget_inputs``), not a second
        heuristic — passing ``accounting=None`` (every pre-existing caller of
        this method) degrades to unknown capacity, the module's default
        reserve, and the chars-per-token estimate, the same fallback
        ``_resolve_budget_inputs`` uses when the session's LLM config can't
        be resolved.

        Raises ``context_budget.ContextBudgetExceededError`` when even the
        protected tail of ``conversation_history`` doesn't fit — the caller
        must convert this into an actionable chat-domain error (see
        ``ContextBudgetExceededException``) and must not submit the request.
        """
        def _size(text: str) -> Dict[str, int]:
            chars = len(text or "")
            return {"chars": chars, "est_tokens": chars // 4}

        system_prompt_size = _size(system_prompt or "")

        try:
            schemas_text = json.dumps(tool_schemas)
        except (TypeError, ValueError):
            tool_schemas = []
            schemas_text = "[]"
        tool_schemas_size = _size(schemas_text)
        tool_schemas_size["tool_count"] = len(tool_schemas)

        memory_chars = (memory_result or {}).get("injected_chars") or 0
        memory_size = {"chars": memory_chars, "est_tokens": memory_chars // 4}

        history_chars = sum(len(m.get("content", "") or "") for m in conversation_history)
        history_size = {
            "chars": history_chars,
            "est_tokens": history_chars // 4,
            "message_count": len(conversation_history),
        }

        total_chars = system_prompt_size["chars"] + tool_schemas_size["chars"] + history_size["chars"]

        cap = accounting.capacity if accounting else context_budget.CapacityInfo(
            context_budget.UNKNOWN_CAPACITY_TOKENS, "unknown"
        )
        reserve = accounting.reserve_tokens if accounting else context_budget.DEFAULT_RESERVE_TOKENS
        counter = accounting.counter if accounting else None
        messages_counter = accounting.messages_counter if accounting else None
        image_tokens_override = accounting.image_tokens_override if accounting else None
        outcome = context_budget.enforce_budget(
            capacity_tokens=cap.capacity_tokens,
            capacity_source=cap.source,
            reserve_tokens=reserve,
            system_message=system_prompt,
            messages=conversation_history,
            tool_schemas=tool_schemas,
            image_data=image_data,
            image_tokens=image_tokens_override,
            counter=counter,
            messages_counter=messages_counter,
        )

        return {
            "system_prompt": system_prompt_size,
            "tool_schemas": tool_schemas_size,
            "memory": memory_size,
            "history": history_size,
            "total_est_tokens": total_chars // 4,
            "budget": outcome.ledger,
        }

    @staticmethod
    def _backfill_trace_message_id(session_id: str, message_id: str) -> None:
        """Stamp this turn's LLM call traces with the assistant message id.

        Best-effort like the rest of the trace plumbing: a failure here (e.g.
        the table not existing in a lightweight test double) must not break
        message persistence, which has already succeeded by this point.
        """
        try:
            chat_call_trace_repository.backfill_message_id(session_id, message_id)
        except Exception:
            logger.debug("Could not backfill chat_llm_call_traces.message_id", exc_info=True)

    async def _run_pre_chat_actions(self, llm_config_id: str) -> List[PreChatActionResult]:
        """Execute registered pre-chat actions (e.g., clear ComfyUI VRAM) before LLM calls.

        Returns the executed action results (empty when unconfigured or none enabled),
        so callers can record what ran in the behavior-trace manifest.
        """
        if not self._m.pre_chat_action_registry:
            return []
        try:
            return await self._m.pre_chat_action_registry.execute_actions(llm_config_id)
        except PreChatActionError as e:
            raise MessageCreationFailedException(str(e))

    def _enabled_pre_chat_actions(self, llm_config_id: str) -> List[Any]:
        """Peek the pre-chat actions that would run, without running them.

        Used to decide whether to emit a `running_pre_chat` status event before
        actually executing them. Never raises.
        """
        if not self._m.pre_chat_action_registry:
            return []
        try:
            return self._m.pre_chat_action_registry.get_enabled_actions(llm_config_id)
        except Exception:
            logger.debug("Could not pre-check enabled pre-chat actions", exc_info=True)
            return []

    @staticmethod
    def _build_behavior_trace(
        mode: ChatMode,
        session: SessionResponse,
        resolved_resources: List[Any],
        memory_result: Dict[str, Any],
        workspace_result: Optional[Dict[str, Any]],
        pre_chat_results: List[PreChatActionResult],
        tool_executions: List[ToolExecution],
        rescues: Optional[List[Dict[str, Any]]],
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
        steps: List[Dict[str, Any]],
        tools_offered: Optional[List[str]] = None,
        tools_withheld: Optional[Dict[str, str]] = None,
        history_info: Optional[Dict[str, Any]] = None,
        image_base64: Optional[str] = None,
        context_ledger: Optional[Dict[str, Any]] = None,
        tool_failures: Optional[Any] = None,
        thinking_mode: Optional[Dict[str, Any]] = None,
        completion: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Assemble the persisted per-message behavior-trace manifest.

        Shared by the streaming and buffered send paths so both persist the same
        shape onto the assistant message metadata.

        ``image_base64`` is the (already-resolved) payload actually sent to the
        LLM — never persisted here, only its presence and size, so the admin
        trace viewer can answer "was an image attached to this turn" without
        anyone having to read raw request JSON.

        ``tool_failures`` is pass-through from the executor response (see
        ``execute_with_tools``/``execute_with_tools_stream``), read via
        ``getattr``/``.get`` with a ``None`` default so a response object that
        doesn't carry the attribute yet degrades cleanly rather than raising.

        ``thinking_mode`` is the same pass-through for the native provider's
        explicit thinking-mode outcome (``LLMResponse.thinking_mode`` — see
        ``NativeLLMClient._chat_template_kwargs``): ``None`` for any provider
        that doesn't report it.

        ``completion`` is the normalized completion outcome from
        ``clients.completion`` (``{"reason": ..., "raw": ...}``) — ``None``
        when the terminal round was a rescue's fallback message rather than a
        genuine model completion (see ``ToolExecutor``'s "answer"/"budget"
        guards) or when a provider reported nothing.

        ``tools_withheld`` is the reason each of the mode's tools missing from
        ``tools_offered`` was withheld (see
        ``ChatContextBuilder.withheld_tools_for_session``) — the frontend's
        "tools" step reads both off this trace, not off ``steps``, the same way
        it already reads ``memory``/``pre_chat_actions`` for those steps.
        """
        session_metadata = getattr(session, 'metadata', None) or {}
        system_prompt_source = (
            "custom" if session_metadata.get("system_message") else f"mode:{mode.id}"
        )
        trace = {
            "version": 1,
            "mode": mode.id if mode else None,
            "system_prompt_source": system_prompt_source,
            "resources": [{"uri": r.uri, "type": r.kind} for r in resolved_resources],
            "memory": memory_result,
            "workspace": workspace_result,
            "pre_chat_actions": [r.action_id for r in pre_chat_results],
            "tools_used": [te.tool_name for te in tool_executions],
            "tools_offered": list(tools_offered or []),
            "tools_withheld": tools_withheld or {},
            "rescues": rescues,
            "tool_failures": tool_failures,
            "thinking_mode": thinking_mode,
            "completion": completion,
            "token_counts": {"prompt": prompt_tokens, "completion": completion_tokens},
            "steps": steps,
            "image_attached": {
                "attached": bool(image_base64),
                "base64_size_kb": round(len(image_base64) / 1024, 1) if image_base64 else None,
            },
        }
        if history_info is not None:
            trace["history"] = history_info
        if context_ledger is not None:
            trace["context_ledger"] = context_ledger
        return trace

    async def record_prompt_feedback(
        self,
        session_id: str,
        user_id: str,
        message_id: str,
        action_index: int,
        verdict: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an approve/reject verdict on a proposed prompt.

        Approved prompts are saved into the prompt library (source
        'chat_approved') so future enhancements can retrieve them as exemplars.
        The verdict is persisted into the message metadata so the UI state
        survives reload.

        Raises:
            SessionNotFoundException: If session not found
            AccessDeniedException: If user doesn't own the session
            MessageCreationFailedException: If the message or prompt index is invalid
        """
        session = self._m._get_session_or_raise(session_id)
        self._m._verify_ownership(session, user_id)

        if verdict not in ("approved", "rejected"):
            raise MessageCreationFailedException(f"Invalid verdict '{verdict}'")

        if not self._m.prompt_enhancement_manager:
            raise MessageCreationFailedException("Prompt enhancement not available")

        message = self._m.chat_repository.get_message(message_id)
        if not message or message.session_id != session_id:
            raise MessageCreationFailedException(f"Message {message_id} not found")

        metadata = message.metadata or {}
        enhancement = metadata.get("enhancement") or {}
        candidates = enhancement.get("candidates")
        if not candidates:
            # Tool-path proposals: fall back to parsing tags from the content
            candidates = [
                m.strip() for m in self._TOOL_ACTION_CONTENT_RE.findall(message.content or "")
            ]

        if action_index < 0 or action_index >= len(candidates):
            raise MessageCreationFailedException(
                f"Prompt index {action_index} out of range (message has {len(candidates)} proposals)"
            )

        from src.features.prompt_enhancement import operations as prompt_enhancement_operations

        prompt_text = candidates[action_index]
        result = await prompt_enhancement_operations.record_feedback(
            self._m.prompt_enhancement_manager,
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            prompt_text=prompt_text,
            verdict=verdict,
            model_id=enhancement.get("model_id"),
            reason=reason,
            mode=session.mode,
        )

        # Persist the verdict so the UI can restore thumb state after reload
        feedback_map = metadata.get("prompt_feedback") or {}
        feedback_map[str(action_index)] = {"verdict": verdict, "reason": reason}
        metadata["prompt_feedback"] = feedback_map
        self._m.chat_repository.update_message_metadata(message_id, metadata)

        return {
            "action_index": action_index,
            "verdict": verdict,
            **result,
        }
