import asyncio
import json
import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple
from weakref import WeakValueDictionary

from src.features.chat import decisions as decision_rules
from src.features.chat.decisions import DecisionRefusal, DecisionRefused
from src.features.chat.exceptions import (
    ContinuationConflictException,
    MessageCreationFailedException,
    ToolExecutionPendingException,
)
from src.features.llm.harness.messages import TextPart, ToolCallPart, ToolResultPart
from src.features.llm.harness.tool_runner import rejection_text
from src.features.llm.harness.tool_runner import APPROVED, FAILED, PENDING, REJECTED
from src.features.llm.harness.turn_state import Decision, DecisionKind, PendingCall, TurnState, TurnStatus
from src.features.llm.tools.base import ToolContext, serialize_approval_preview
from src.features.llm.tools.workflow import ToolCallGuard

logger = logging.getLogger(__name__)


class ToolCallDispatcher:
    def __init__(self, manager):
        self._m = manager
        self._locks: "WeakValueDictionary[str, asyncio.Lock]" = WeakValueDictionary()

    def _lock(self, message_id: str) -> asyncio.Lock:
        lock = self._locks.get(message_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[message_id] = lock
        return lock

    def tool_context(
        self,
        session: Any,
        user_id: str,
        is_admin: bool,
        context_metadata: Optional[Dict[str, Any]],
        mode_id: Optional[str] = None,
    ) -> ToolContext:
        return ToolContext.for_session(
            self._m.tool_services,
            user_id,
            mode_id=mode_id or session.mode,
            is_admin=is_admin,
            acts_as_admin=is_admin,
            session_metadata=context_metadata or {},
            llm_repository=self._m.llm_service.repository,
            plugin_registry=self._m.plugins,
            llm_id=session.llm_config_id,
        )

    def _load_execution(self, session_id: str, message_id: str, tool_index: int):
        message = self._m.chat_repository.get_message(message_id)
        if not message or message.session_id != session_id:
            raise MessageCreationFailedException(f"Message {message_id} not found")
        metadata = dict(message.metadata or {})
        executions = metadata.get("tool_executions") or []
        if tool_index < 0 or tool_index >= len(executions):
            raise MessageCreationFailedException(
                f"Tool index {tool_index} out of range (message has {len(executions)} tool executions)"
            )
        return message, metadata, executions

    @staticmethod
    def _resolution(status: str, message_id: str, tool_index: int, execution: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": status, "execution": execution, "message_id": message_id, "tool_index": tool_index}

    @property
    def _store(self):
        return self._m.turn_engine.store

    def _turn_state(self, message: Any) -> Optional[TurnState]:
        return self._store.load_state(message.turn_id) if message.turn_id else None

    @staticmethod
    def _reopened(state: Optional[TurnState], call_id: Optional[str]) -> Optional[TurnState]:
        if state is None or state.status is not TurnStatus.AWAITING_DECISION or not call_id:
            return state
        failed = next((part for part in state.gathered if part.call_id == call_id and part.is_error), None)
        if failed is None:
            return state
        return replace(
            state,
            pending=(*state.pending, PendingCall(call_id=call_id, name=failed.name, decision=DecisionKind.APPROVAL)),
            gathered=tuple(part for part in state.gathered if part.call_id != call_id),
        )

    def _reconcile(self, state: Optional[TurnState], execution: Dict[str, Any]) -> None:
        call_id = execution.get("tool_call_id")
        if state is None or state.status is not TurnStatus.AWAITING_DECISION:
            return
        if not any(item.call_id == call_id for item in state.pending):
            return
        record = execution.get("result") or {}
        if execution.get("approval_status") == REJECTED:
            text = rejection_text(execution.get("approval_reason"))
        elif record.get("success"):
            text = record.get("data") or ""
        else:
            text = f"Error: {record.get('error') or 'unknown error'}"
        waiting = next(item for item in state.pending if item.call_id == call_id)
        part = ToolResultPart(
            call_id=call_id, name=waiting.name, content=(TextPart(text=text),),
            is_error=execution.get("approval_status") != APPROVED,
        )
        applied = state.applied_calls
        if execution.get("approval_status") == APPROVED:
            applied = applied | {ToolCallGuard.call_key(waiting.name, dict(execution.get("arguments") or {}))}
        self._store.save_state(replace(
            state,
            pending=tuple(item for item in state.pending if item.call_id != call_id),
            gathered=(*(item for item in state.gathered if item.call_id != call_id), part),
            applied_calls=applied,
        ))

    async def resolve_tool_execution(
        self,
        session_id: str,
        user_id: str,
        message_id: str,
        tool_index: int,
        approved: bool,
        reason: Optional[str] = None,
        context_metadata: Optional[Dict[str, Any]] = None,
        force: bool = False,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        session = self._m._get_session_or_raise(session_id)
        self._m._verify_ownership(session, user_id)

        async with self._lock(message_id):
            message, metadata, executions = self._load_execution(session_id, message_id, tool_index)
            execution = executions[tool_index]
            status = execution.get("approval_status")
            state = self._turn_state(message)
            settled = status in (APPROVED, REJECTED) or (status == FAILED and not approved)
            if settled:
                self._reconcile(state, execution)
                return self._resolution("already_resolved", message_id, tool_index, execution)
            if approved and status == FAILED:
                state = self._reopened(state, execution.get("tool_call_id"))
            try:
                decision = decision_rules.decision_for(
                    state, decision_rules.execution_call_ids(metadata), tool_index, approved, reason,
                )
            except DecisionRefused as refusal:
                if refusal.refusal in (DecisionRefusal.NOT_AWAITING, DecisionRefusal.NOT_PENDING):
                    return self._resolution("already_resolved", message_id, tool_index, execution)
                raise MessageCreationFailedException(str(refusal)) from refusal

            stored_context = metadata.get("context_metadata") or {}
            if (
                approved
                and status == PENDING
                and not force
                and context_metadata is not None
                and await self._is_stale(execution, self.tool_context(session, user_id, is_admin, context_metadata))
            ):
                return self._resolution("stale", message_id, tool_index, execution)

            call = ToolCallPart(
                call_id=decision.call_id,
                name=execution.get("tool_name") or "",
                args=dict(execution.get("arguments") or {}),
            )
            finished = await self._m.turn_engine.settle(
                state, call, decision, self.tool_context(session, user_id, is_admin, stored_context),
            )
            result = finished.record.result
            applied = state.applied_calls
            if not approved:
                execution["approval_status"] = REJECTED
                execution["approval_reason"] = decision.reason
            else:
                result_data = {"success": result.success, "data": result.data, "error": result.error}
                execution["result"] = result_data
                execution["approval_status"] = APPROVED if result.success else FAILED
                execution["approval_reason"] = None
                self._apply_action_to_context_metadata(result_data, stored_context)
                if stored_context:
                    metadata["context_metadata"] = stored_context
                if result.success:
                    applied = applied | {ToolCallGuard.call_key(call.name, dict(call.args))}

            metadata["tool_executions"] = executions
            if not self._m.chat_repository.transition_tool_execution(message_id, tool_index, status, metadata):
                _message, _metadata, current = self._load_execution(session_id, message_id, tool_index)
                return self._resolution("already_resolved", message_id, tool_index, current[tool_index])
            self._store.save_state(replace(
                state,
                pending=tuple(item for item in state.pending if item.call_id != decision.call_id),
                gathered=(*(part for part in state.gathered if part.call_id != decision.call_id), finished.result),
                applied_calls=applied,
            ))

        return self._resolution(execution["approval_status"], message_id, tool_index, execution)

    @staticmethod
    def _before_values(preview: Optional[Dict[str, Any]]) -> List[Any]:
        if not preview:
            return []
        values: List[Any] = []
        for row in preview.get("fields") or []:
            if isinstance(row, dict) and "old" in row:
                values.append(("field", row.get("label"), row.get("old")))
        for block in preview.get("text_blocks") or []:
            if isinstance(block, dict) and "old_text" in block:
                values.append(("text", block.get("label"), block.get("old_text")))
        for index, change in enumerate(preview.get("changes") or []):
            if isinstance(change, dict) and change.get("before") is not None:
                values.append(("change", index, change.get("before")))
        return values

    async def _is_stale(self, execution: Dict[str, Any], context: ToolContext) -> bool:
        before = self._before_values(execution.get("preview"))
        if not before or not self._m.tool_executor:
            return False
        tool = self._m.tool_executor.tool_registry.get(execution.get("tool_name", ""))
        if tool is None:
            return False
        try:
            result = await tool.execute(context, **(execution.get("arguments") or {}))
        except Exception:
            logger.debug("Stale check preview failed for %s", execution.get("tool_name"), exc_info=True)
            return False
        if not result.success or result.preview is None:
            return False
        current = self._before_values(serialize_approval_preview(result.preview))
        return json.dumps(current, default=str, sort_keys=True) != json.dumps(before, default=str, sort_keys=True)

    def open_turn(self, session_id: str) -> Optional[TurnState]:
        return self._store.open_turn(session_id)

    async def mark_rejected_for_reply(
        self, session_id: str, state: TurnState, rejections: List[Decision],
    ) -> Optional[Dict[str, Any]]:
        latest = self._m.chat_repository.get_latest_message(session_id)
        if latest is None or latest.role != "assistant" or latest.turn_id != state.turn_id:
            return None
        async with self._lock(latest.id):
            message = self._m.chat_repository.get_message(latest.id)
            metadata = dict(message.metadata or {})
            executions = metadata.get("tool_executions") or []
            reasons = {item.call_id: item.reason for item in rejections}
            changed = [
                index for index, record in enumerate(executions)
                if record.get("approval_status") == PENDING and record.get("tool_call_id") in reasons
            ]
            if not changed:
                return None
            for index in changed:
                executions[index]["approval_status"] = REJECTED
                executions[index]["approval_reason"] = reasons[executions[index]["tool_call_id"]]
            metadata["tool_executions"] = executions
            if not self._m.chat_repository.transition_tool_execution(latest.id, changed[0], PENDING, metadata):
                return None
        return {"id": latest.id, "tool_executions": executions}

    def continuation(
        self, session_id: str, continue_from: Dict[str, Any],
    ) -> Tuple[TurnState, Dict[str, Any], Optional[Dict[str, Any]]]:
        message_id = continue_from.get("message_id") or ""
        tool_index = int(continue_from.get("tool_index", 0))
        message, metadata, executions = self._load_execution(session_id, message_id, tool_index)
        execution = executions[tool_index]
        status = execution.get("approval_status")
        if status == PENDING:
            raise ToolExecutionPendingException(f"Tool execution {tool_index} on message {message_id} is still pending")
        if status not in (APPROVED, REJECTED, FAILED):
            raise MessageCreationFailedException(
                f"Tool execution {tool_index} on message {message_id} did not need approval"
            )
        state = self._turn_state(message)
        if state is None or state.status is not TurnStatus.AWAITING_DECISION:
            raise ContinuationConflictException(f"Message {message_id} was already continued")
        if state.pending:
            raise ToolExecutionPendingException(f"Turn {state.turn_id} still has calls waiting for a decision")
        return state, execution, metadata.get("context_metadata")

    @staticmethod
    def _apply_action_to_context_metadata(
        result_data: Dict[str, Any],
        context_metadata: Dict[str, Any],
    ) -> None:
        if not result_data.get("success") or not isinstance(context_metadata, dict):
            return
        data = result_data.get("data")
        if not isinstance(data, str) or not data.strip():
            return
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return

        form_state = context_metadata.get("form_state")
        if not isinstance(form_state, dict):
            return

        action = payload.get("action")
        if action == "apply_form_changes":
            form_data = dict(form_state.get("form_data") or {})
            for change in payload.get("applied_changes") or []:
                field_name = change.get("field_name") if isinstance(change, dict) else None
                if isinstance(field_name, str) and field_name:
                    form_data[field_name] = change.get("new_value")
            form_state["form_data"] = form_data
