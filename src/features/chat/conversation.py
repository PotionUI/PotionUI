import asyncio
import json
import logging
import re
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from src.platform.security.failure_text import failure_detail
from src.features.chat import projection
from src.features.chat.apply_tags import ApplyTargets, normalize_apply_tags
from src.features.chat.context_builder import empty_memory_result
from src.features.chat.decisions import rejections_for_reply
from src.features.chat.dto import MessageResponse, SessionResponse, SendMessageResponse
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
from src.features.chat.reply_contract import approval_continuation_message, continuation_nudge
from src.features.chat.history import replayed_history
from src.features.chat.thinking import ReasoningCapture, config_shows_thinking
from src.features.llm import context_budget, trace_collector
from src.features.llm.harness.adapter import Settings
from src.features.llm.harness.build import new_id, user_message
from src.features.llm.harness.engine import BudgetExhausted, TurnInput, TurnOutcome
from src.features.llm.harness.events import ReasoningDelta, TextDelta
from src.features.llm.harness.messages import ContextPart, Message, Origin, Role
from src.features.llm.harness.profile import CapabilityProfile
from src.features.llm.harness.turn_state import NoticeRaised, StepCommitted, StepDiscarded, ToolFinished, ToolStarted, TurnState
from src.features.llm.trace_repository import chat_call_trace_repository
from src.features.llm.tools.base import ToolExecution
from src.features.llm.tools.executor import ToolExecutor
from src.features.llm.tools.use_tools import use_tools_schema
from src.features.llm.tools.builtin.utils import resolve_active_preset_id
from src.platform.plugins.hooks import await_hook_blocking_waits
from src.platform.util.imaging import convert_image_to_base64

logger = logging.getLogger(__name__)

_DEFAULT_HISTORY_TOKEN_BUDGET = 8000
_HISTORY_BUDGET_CHARS_PER_TOKEN = 4
_MAX_TOOL_ROUNDS = 20
_TITLE_WAIT_SECONDS = 15


class _AssistantNotSaved(MessageCreationFailedException):
    pass


@dataclass
class _TurnRun:
    title_task: Optional[asyncio.Task] = None
    done: Optional[Dict[str, Any]] = None
    step_records: List[Dict[str, Any]] = field(default_factory=list)


def _usage_data(usage: Any) -> Optional[Dict[str, Any]]:
    if usage is None:
        return None
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "cost": usage.cost,
    }


class ConversationRunner:
    _TOOL_ACTION_CONTENT_RE = re.compile(
        r'<tool_action\s+type="(?:update_(?:director_)?|add_|delete_)segment"[^>]*>([\s\S]*?)</tool_action>'
    )

    def __init__(self, manager):
        self._m = manager

    def _verify_mode_access(self, session: SessionResponse, is_admin: bool) -> None:
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
        run = _TurnRun()
        turn = self._turn(
            run, session_id, user_id, content, image_data, context_metadata, resources, is_admin, None,
        )
        async with aclosing(turn) as events:
            async for _ in events:
                pass
        done = run.done or {}
        return SendMessageResponse(
            user_message=MessageResponse(**done["user_message"]),
            assistant_message=MessageResponse(**done["assistant_message"]),
            resolved_message=done.get("resolved_message"),
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
        continue_from: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        run = _TurnRun()
        turn = self._turn(
            run, session_id, user_id, content, image_data, context_metadata, resources, is_admin, continue_from,
        )
        started = False
        try:
            async with aclosing(turn) as events:
                async for event in events:
                    started = True
                    yield event
        except _AssistantNotSaved as e:
            yield {"event": "error", "data": {"error": "message_creation_failed", "message": str(e)}}
            return
        except Exception as e:
            if not started:
                raise
            logger.exception(f"Error during streaming in session {session_id}: {e}")
            yield {"event": "error", "data": {"error": "stream_error", "message": failure_detail(e, is_admin)}}
            return
        if run.title_task is not None:
            try:
                title = await asyncio.wait_for(run.title_task, timeout=_TITLE_WAIT_SECONDS)
                if title:
                    yield {"event": "title", "data": {"session_id": session_id, "name": title}}
            except Exception as e:
                logger.debug(f"Title generation did not complete for session {session_id}: {e}")

    async def _turn(
        self,
        run: _TurnRun,
        session_id: str,
        user_id: str,
        content: str,
        image_data: Optional[str],
        context_metadata: Optional[Dict[str, Any]],
        resources: Optional[List[str]],
        is_admin: bool,
        continue_from: Optional[Dict[str, Any]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        session = self._m._get_session_or_raise(session_id)
        self._m._verify_ownership(session, user_id)
        self._m._verify_active(session)
        self._verify_mode_access(session, is_admin)

        if not session.llm_config_id:
            raise InvalidLLMConfigException("Session has no LLM configuration")

        continuing = bool(continue_from)
        paused: Optional[TurnState] = None
        approval_execution: Dict[str, Any] = {}
        if continuing:
            paused, approval_execution, continued_context = self._m._tools.continuation(session_id, continue_from)

        if not continuing:
            hook_data, blocked, hook_ctx = self._m._execute_hook(
                CHAT_MESSAGE_HOOKS.before_send,
                {"session_id": session_id, "user_id": user_id, "content": content, "image_data": image_data},
            )
            await await_hook_blocking_waits(hook_ctx)
            if blocked:
                reason = hook_data.get("block_reason", "Message blocked")
                logger.warning(f"Message send blocked by plugin: {reason}")
                raise MessageCreationFailedException(reason)
            content = hook_data.get("content", content)
            image_data = hook_data.get("image_data", image_data)
        else:
            image_data = None
            resources = None
            if context_metadata is None:
                context_metadata = continued_context

        if not self._mode_reads_app_context(session.mode):
            context_metadata = self._attachments_only(context_metadata)
            resources = None

        step_records = run.step_records

        if resources:
            resource_start = time.monotonic()
            yield {"event": "status", "data": {"step": "resolving_resources", "state": "started"}}
            resolved_resources = await self._m._context.resolve_message_resources(
                resources, user_id, session.mode, context_metadata, is_admin=is_admin
            )
            step_records.append({
                "step": "resolving_resources",
                "duration_ms": int((time.monotonic() - resource_start) * 1000),
            })
            yield {
                "event": "status",
                "data": {
                    "step": "resolving_resources",
                    "state": "completed",
                    "detail": {"count": len(resolved_resources), "uris": [r.uri for r in resolved_resources]},
                },
            }
        else:
            resolved_resources = await self._m._context.resolve_message_resources(
                resources, user_id, session.mode, context_metadata, is_admin=is_admin
            )

        user_row = None
        resolved_message = None
        rejections: List[Any] = []
        if not continuing:
            paused = self._m._tools.open_turn(session_id)
            rejections = rejections_for_reply(paused, content)
            if rejections:
                resolved_message = await self._m._tools.mark_rejected_for_reply(session_id, paused, rejections)
            else:
                paused = None
        turn_id = paused.turn_id if paused is not None else new_id("turn")
        if not continuing:
            user_metadata: Dict[str, Any] = {}
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
            user_row = self._m.chat_repository.add_message(
                session_id=session_id,
                role='user',
                content=content,
                metadata=user_metadata if user_metadata else None,
                turn_id=turn_id,
            )
            if not user_row:
                raise MessageCreationFailedException("Failed to save user message")

        await self._prime_context_window(session)
        tool_tier = self._tool_tier(session)
        form_state = (context_metadata or {}).get("form_state")
        tool_scope = self._m._context.resolve_tool_scope(session, form_state)
        system_prompt, allowed_tools, mode, deferred_tools = self._m._context.resolve_session_prompt_and_tools(
            session, form_state=form_state, tool_tier=tool_tier, scope=tool_scope,
            loaded_groups=paused.loaded_tool_groups if paused is not None else (),
        )
        tools_enabled = self._m.tool_executor is not None and bool(allowed_tools or deferred_tools)

        replay_target, replay_profile = self._replay_target(session)
        conversation_history = replayed_history(
            self._m.turn_engine.store, self._m.replay_selector, session_id, turn_id,
            replay_target, replay_profile, tools_enabled=tools_enabled,
        )
        image_base64 = convert_image_to_base64(image_data)
        if not continuing:
            conversation_history.append(user_message(
                content, images=(image_base64,) if image_base64 else (), message_id=user_row.id,
            ))

        gen_hook_data, _, _ctx = self._m._execute_hook(
            CHAT_RESPONSE_HOOKS.before_generate,
            {
                "session_id": session_id,
                "conversation_history": conversation_history,
                "llm_config_id": session.llm_config_id,
            },
        )
        conversation_history = list(gen_hook_data.get("conversation_history", conversation_history))
        injected = list(conversation_history) if continuing else conversation_history

        withheld_tools = self._m._context.withheld_tools_for_session(
            session, form_state=form_state, scope=tool_scope,
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

        memory_result = empty_memory_result()
        if mode.app_context:
            memory_start = time.monotonic()
            yield {"event": "status", "data": {"step": "loading_memory", "state": "started"}}
            memory_result = self._m._context.inject_memory_block(
                injected, context_metadata, user_id,
                write_memory_available="write_memory" in (allowed_tools or []),
                mode_id=session.mode,
            )
            step_records.append({"step": "loading_memory", "duration_ms": int((time.monotonic() - memory_start) * 1000)})
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

        history_info = self._apply_history_budget(injected)
        if continuing:
            self._apply_history_budget(conversation_history)

        await self._m._context.inject_contributor_block(injected, session, context_metadata, user_id)
        self._m._context.inject_resource_block(injected, resolved_resources)
        offered_tool_names = set(allowed_tools or ()).union(*(deferred_tools or {}).values())
        workspace_result = self._m._context.inject_workspace_block(
            injected, context_metadata, offered_tools=offered_tool_names,
        )
        self._m._context.inject_prompt_state_block(
            injected, context_metadata, offered_tools=offered_tool_names,
            variable_reader=self._m._context.prompt_state_variable_reader(user_id, context_metadata),
        )
        self._m._context.inject_reply_contract_reminder_block(injected, mode, tool_tier)
        self._m._context.inject_tool_availability_block(injected, allowed_tools, withheld_tools)

        if not continuing:
            history, new_messages = self._split_current_user(injected)
            ledger_messages = [*history, *new_messages]
        else:
            history = tuple(conversation_history)
            note = approval_continuation_message(
                approval_execution.get("tool_name") or "", approval_execution.get("tool_call_id") or "",
            )
            new_messages = (Message(
                id=f"approval_{approval_execution.get('tool_call_id')}",
                role=Role.USER,
                parts=(ContextPart(kind="approval", text=note["content"]),),
            ),)
            ledger_messages = [*injected, *new_messages]

        tool_schemas = self._resolve_tool_schemas_for_ledger(allowed_tools, deferred_tools)
        settings = Settings.from_options(mode.llm_options)
        budget_accounting = self._resolve_budget_inputs(session, settings)
        try:
            context_ledger = self._build_context_ledger(
                system_prompt, tool_schemas, memory_result, ledger_messages,
                accounting=budget_accounting,
            )
        except context_budget.ContextBudgetExceededError as e:
            raise ContextBudgetExceededException(str(e)) from e

        enabled_pre_chat_actions = self._enabled_pre_chat_actions(session.llm_config_id)
        if enabled_pre_chat_actions:
            pre_chat_start = time.monotonic()
            yield {"event": "status", "data": {"step": "running_pre_chat", "state": "started"}}
            pre_chat_results = await self._run_pre_chat_actions(session.llm_config_id)
            step_records.append({
                "step": "running_pre_chat",
                "duration_ms": int((time.monotonic() - pre_chat_start) * 1000),
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

        yield {"event": "message_created", "data": {
            "user_message_id": user_row.id if user_row else None, "assistant_message_id": "",
        }}

        show_thinking = config_shows_thinking(self._m.llm_service, session.llm_config_id)
        reasoning = ReasoningCapture(show_thinking)
        thinking_start = time.monotonic()
        yield {
            "event": "status",
            "data": {
                "step": "thinking", "state": "started",
                **({"detail": {"thinking_visible": True}} if show_thinking else {}),
            },
        }
        answering_start: Optional[float] = None
        outcome: Optional[TurnOutcome] = None

        turn_input = TurnInput(
            session_id=session_id,
            llm_id=session.llm_config_id,
            system=system_prompt or None,
            history=history,
            new_messages=new_messages,
            settings=settings,
            tool_context=(
                self._m._tools.tool_context(session, user_id, is_admin, context_metadata, mode.id)
                if tools_enabled else None
            ),
            allowed_tools=tuple(allowed_tools) if allowed_tools is not None else None,
            deferred_tools={group: tuple(names) for group, names in (deferred_tools or {}).items()},
            max_rounds=_MAX_TOOL_ROUNDS,
            iteration_nudge=continuation_nudge(mode.structured_reply),
            mode=mode.id,
            turn_id=turn_id,
            tools_in_use=paused.snapshot.tools if paused is not None else self._m.turn_engine.previous_tools(session_id),
            resume=(tuple(rejections) if rejections else ()) if paused is not None else None,
        )

        with trace_collector.activate(session_id, user_id, purpose="chat_tools" if tools_enabled else "chat"):
            async with aclosing(self._m.turn_engine.run(turn_input)) as events:
                async for event in events:
                    if isinstance(event, ReasoningDelta):
                        for released in reasoning.on_reasoning(event):
                            yield released
                        continue
                    for released in reasoning.close():
                        yield released
                    if isinstance(event, TextDelta):
                        if answering_start is None:
                            answering_start = time.monotonic()
                            step_records.append({
                                "step": "thinking",
                                "duration_ms": int((answering_start - thinking_start) * 1000),
                            })
                            yield {"event": "status", "data": {"step": "answering", "state": "started"}}
                        yield {"event": "token", "data": {"content": event.text}}
                    elif isinstance(event, ToolStarted):
                        yield {"event": "tool_start", "data": {
                            "tool_name": event.call.name, "arguments": dict(event.call.args or {}),
                        }}
                    elif isinstance(event, ToolFinished):
                        if not getattr(event.record, "skipped", False):
                            yield {"event": "tool_end", "data": ToolExecutor.tool_end_payload(event.record)}
                    elif isinstance(event, BudgetExhausted):
                        yield {"event": "status", "data": {
                            "step": "tool_budget_exhausted", "state": "completed",
                            "detail": {"max_iterations": event.max_rounds},
                        }}
                    elif isinstance(event, NoticeRaised):
                        yield {"event": "notice", "data": {
                            "kind": event.notice.kind.value, "message": event.notice.message,
                            "detail": dict(event.notice.detail or {}),
                        }}
                    elif isinstance(event, StepDiscarded):
                        yield {"event": "step_discarded", "data": {"seq": event.seq}}
                    elif isinstance(event, StepCommitted):
                        step = event.step
                        if step.message.role is Role.ASSISTANT and step.finish is not None:
                            yield {"event": "step", "data": {
                                "seq": step.seq,
                                "finish": step.finish.value,
                                "tool_calls": len(step.message.tool_calls()),
                                "usage": _usage_data(step.usage),
                            }}
                    elif isinstance(event, TurnOutcome):
                        outcome = event
            for released in reasoning.close():
                yield released

        if answering_start is None:
            answering_start = time.monotonic()
            step_records.append({
                "step": "thinking",
                "duration_ms": int((answering_start - thinking_start) * 1000),
            })
            yield {"event": "status", "data": {"step": "answering", "state": "started"}}
        step_records.append({
            "step": "answering",
            "duration_ms": int((time.monotonic() - answering_start) * 1000),
        })

        if outcome is None:
            raise _AssistantNotSaved("The turn produced no result")
        usage = outcome.usage if outcome.model_generated else None
        steps = list(outcome.steps)
        cleaned_content, parsed_content = self._m.response_processor.process(
            normalize_apply_tags(projection.segment_text(steps), ApplyTargets.from_context(context_metadata)),
            mode=session.mode,
        )

        extra: Dict[str, Any] = {
            'behavior_trace': self._build_behavior_trace(
                mode=mode,
                session=session,
                resolved_resources=resolved_resources,
                memory_result=memory_result,
                workspace_result=workspace_result,
                pre_chat_results=pre_chat_results,
                tool_executions=list(outcome.executions),
                rescues=outcome.rescues,
                prompt_tokens=usage.input_tokens if usage else None,
                completion_tokens=usage.output_tokens if usage else None,
                steps=step_records,
                tools_offered=allowed_tools,
                tools_withheld=withheld_tools,
                history_info=history_info,
                image_base64=image_base64,
                context_ledger=context_ledger,
                tool_failures=outcome.tool_failures,
                thinking_mode=outcome.thinking_mode,
                completion=outcome.completion,
                tool_tier=tool_tier,
                tools_deferred=deferred_tools,
                thinking_visible=show_thinking,
            ),
        }
        if continuing:
            extra['continued_from'] = {
                "message_id": continue_from["message_id"],
                "tool_index": continue_from["tool_index"],
            }
        assistant_metadata = projection.assistant_metadata(
            steps,
            outcome.state,
            records={
                execution.tool_call_id: projection.execution_record(execution)
                for execution in outcome.executions
                if execution.tool_call_id
            },
            context_metadata=context_metadata,
            extra=extra,
        )

        assistant_row = self._m.chat_repository.add_message(
            session_id=session_id,
            role='assistant',
            content=cleaned_content,
            parsed_content=parsed_content,
            metadata=assistant_metadata,
            turn_id=turn_id,
        )
        if not assistant_row:
            raise _AssistantNotSaved("Failed to save assistant message")

        self._backfill_trace_message_id(session_id, assistant_row.id)
        self._m._execute_hook(
            CHAT_RESPONSE_HOOKS.after_save,
            {"message_id": assistant_row.id, "session_id": session_id, "content": cleaned_content},
        )
        if user_row is not None:
            self._m._execute_hook(
                CHAT_MESSAGE_HOOKS.after_send,
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "user_message_id": user_row.id,
                    "assistant_message_id": assistant_row.id,
                },
            )

        logger.debug(f"Message exchange complete in session {session_id}")
        run.title_task = self._start_title_task(session, session_id)
        if mode.app_context:
            self._start_reflection_task(session_id, form_state)

        run.done = {
            "assistant_message": self._presented(assistant_row, turn_id).model_dump(),
            "user_message": user_row.model_dump() if user_row else None,
            "resolved_message": resolved_message,
        }
        yield {"event": "done", "data": run.done}

    def _mode_reads_app_context(self, mode_id: str) -> bool:
        mode = self._m.chat_mode_registry.get(mode_id)
        return mode is None or mode.app_context

    @staticmethod
    def _attachments_only(context_metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        image_url = (context_metadata or {}).get("image_url")
        return {"image_url": image_url} if image_url else None

    def _presented(self, row: MessageResponse, turn_id: str) -> MessageResponse:
        store = self._m.turn_engine.store
        [presented] = projection.present(
            [row], store.steps_by_turn([turn_id]), store.states([turn_id]), show_thinking=False,
        )
        return presented

    @staticmethod
    def _split_current_user(history: List[Message]) -> Tuple[Tuple[Message, ...], Tuple[Message, ...]]:
        if history and history[-1].role is Role.USER:
            return tuple(history[:-1]), (history[-1],)
        return tuple(history), ()

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
            value = self._m.tool_services.settings.get_setting(
                'chat_history_token_budget', _DEFAULT_HISTORY_TOKEN_BUDGET
            )
            if value is not None:
                return int(value)
        except Exception:
            logger.debug("Could not read chat_history_token_budget setting", exc_info=True)
        return _DEFAULT_HISTORY_TOKEN_BUDGET

    async def _prime_context_window(self, session: SessionResponse) -> None:
        llm_config_id = getattr(session, "llm_config_id", None)
        if not llm_config_id:
            return
        try:
            config = self._m.llm_service.repository.get_configuration(llm_config_id)
            if config is not None:
                await self._m.llm_service.profiles.ensure(config)
        except Exception:
            logger.debug("Could not prime LLM context window", exc_info=True)

    def _replay_target(self, session: SessionResponse) -> Tuple[Origin, CapabilityProfile]:
        return self._m.llm_service.replay_target(session.llm_config_id)

    def _tool_tier(self, session: SessionResponse) -> str:
        llm_config_id = getattr(session, "llm_config_id", None)
        if not llm_config_id:
            return context_budget.TOOL_TIER_STANDARD
        try:
            config = self._m.llm_service.repository.get_configuration(llm_config_id)
            if config is None:
                return context_budget.TOOL_TIER_STANDARD
            capacity = self._m.llm_service.context_capacity_for(config)
            return context_budget.tool_tier(capacity)
        except Exception:
            logger.debug("Could not resolve the tool tier for the context window", exc_info=True)
            return context_budget.TOOL_TIER_STANDARD

    def _resolve_budget_inputs(
        self,
        session: SessionResponse,
        settings: Settings,
    ) -> Optional[context_budget.ContextProfile]:
        llm_config_id = getattr(session, "llm_config_id", None)
        if not llm_config_id:
            return None
        try:
            config = self._m.llm_service.repository.get_configuration(llm_config_id)
            if config is None:
                return None
            inputs = self._m.llm_service.context_profile_for(config, settings)
            int(inputs.reserve_tokens)
            return inputs
        except Exception:
            logger.debug("Could not resolve LLM config for context budget", exc_info=True)
            return None

    def _apply_history_budget(self, conversation_history: List[Message], min_protected: int = 1) -> Dict[str, Any]:
        total = len(conversation_history)
        if total == 0:
            return {"messages_sent": 0, "messages_total": 0, "truncated": False}

        budget_tokens = self._history_token_budget()
        if budget_tokens <= 0:
            return {"messages_sent": total, "messages_total": total, "truncated": False}

        budget_chars = budget_tokens * _HISTORY_BUDGET_CHARS_PER_TOKEN
        units = context_budget.atomic_units(conversation_history)
        anchor = context_budget.current_turn_anchor(units)
        protected_count = max(1, min(min_protected, len(units)))
        if anchor is not None:
            protected_count = max(protected_count, len(units) - anchor)

        protected = units[-protected_count:]
        kept_reversed = list(reversed(protected))
        used_chars = sum(self._unit_chars(unit) for unit in protected)
        for unit in reversed(units[:-protected_count]):
            unit_chars = self._unit_chars(unit)
            if used_chars + unit_chars > budget_chars:
                break
            kept_reversed.append(unit)
            used_chars += unit_chars

        kept = [message for unit in reversed(kept_reversed) for message in unit]
        truncated = len(kept) < total
        if truncated:
            conversation_history[:] = kept

        return {"messages_sent": len(kept), "messages_total": total, "truncated": truncated}

    @staticmethod
    def _unit_chars(unit: List[Message]) -> int:
        return sum(
            len(fragment)
            for message in unit
            for fragments in context_budget.wire_fragments(message)
            for fragment in fragments
        )

    def _resolve_tool_schemas_for_ledger(
        self, allowed_tools: Optional[List[str]], deferred_tools: Optional[Dict[str, List[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch the resolved tool schemas the executor would actually send this turn.

        Best-effort like the rest of the trace plumbing — a registry lookup
        failure (or a test double that doesn't model ``get_schemas``) yields
        an empty list rather than breaking the send.
        """
        if not allowed_tools and not deferred_tools:
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
        if not isinstance(schemas, list):
            return []
        return [*schemas, use_tools_schema(deferred_tools)] if deferred_tools else schemas

    @staticmethod
    def _build_context_ledger(
        system_prompt: Optional[str],
        tool_schemas: List[Dict[str, Any]],
        memory_result: Dict[str, Any],
        conversation_history: List[Message],
        *,
        accounting: Optional[context_budget.ContextProfile] = None,
    ) -> Dict[str, Any]:
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

        history_chars = sum(len(fragments[0]) for message in conversation_history for fragments in context_budget.wire_fragments(message))
        history_size = {
            "chars": history_chars,
            "est_tokens": history_chars // 4,
            "message_count": len(conversation_history),
        }

        total_chars = system_prompt_size["chars"] + tool_schemas_size["chars"] + history_size["chars"]

        outcome = context_budget.budget_with_profile(
            accounting, system_message=system_prompt, messages=conversation_history, tool_schemas=tool_schemas,
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
        tool_tier: Optional[str] = None,
        tools_deferred: Optional[Dict[str, List[str]]] = None,
        thinking_visible: bool = False,
    ) -> Dict[str, Any]:
        """Assemble the persisted per-message behavior-trace manifest.

        Shared by the streaming and buffered send paths so both persist the same
        shape onto the assistant message metadata.

        ``image_base64`` is the (already-resolved) payload actually sent to the
        LLM — never persisted here, only its presence and size, so the admin
        trace viewer can answer "was an image attached to this turn" without
        anyone having to read raw request JSON.

        ``tool_failures``, ``thinking_mode`` and ``completion`` come from the
        turn engine's ``TurnOutcome``: ``completion`` is ``{"reason": ...,
        "raw": ...}`` for a model-written answer and ``None`` when the turn
        ended on a rescue's fallback message or the provider reported nothing;
        ``thinking_mode`` is ``None`` for any provider that doesn't report it.

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
            "tool_tier": tool_tier,
            "tools_deferred": {group: list(names) for group, names in (tools_deferred or {}).items()},
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
        if thinking_visible:
            trace["thinking_visible"] = True
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

        if not self._m.tool_services.prompt_enhancement_manager:
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
            self._m.tool_services.prompt_enhancement_manager,
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
