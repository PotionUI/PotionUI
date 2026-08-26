"""Tool-approval dispatch for chat.

Handles the human-in-the-loop confirmation of a tool call a prior assistant turn
left pending: rebuilds the ToolContext from the stored message metadata, runs the
tool's confirmed execution and persists the result back onto the message.

The live tool loop that runs *during* a send is interleaved with token streaming
and persistence, so it stays in ConversationRunner; only this self-contained
approve/reject step is separated here. Dependencies are read through the manager
so the composition root's late binding keeps working.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

from src.features.chat.exceptions import MessageCreationFailedException
from src.features.llm.tools.base import ToolContext

logger = logging.getLogger(__name__)

# Matches a mode prompt's "## Tools" section (tool-calling instructions and the
# {{TOOL_HINTS}} substitution, already resolved to text) up to the next "## "
# heading or end of string — see DEFAULT_TOOLS_SYSTEM_PROMPT_TEMPLATE in
# src.features.chat.modes.builtin.
_TOOL_SECTION_RE = re.compile(r"##\s*Tools\b.*?(?=\n##\s|\Z)", re.DOTALL)

# Appended to every presentation-turn system prompt so a model whose base
# prompt still names tools elsewhere (e.g. a custom session system_message)
# does not attempt one anyway.
_PRESENTATION_ONLY_DIRECTIVE = (
    "\n\n---\n"
    "You are in a tool-free presentation turn: a tool action was already "
    "approved or denied and has already run (or been skipped) outside your "
    "control. No tools are available to you right now. Reply in plain text "
    "only, briefly telling the user what happened — do not emit a "
    "<tool_call> block or any tool-call syntax."
)


class ToolCallDispatcher:
    """Approve or reject a pending tool execution recorded on a message."""

    def __init__(self, manager):
        self._m = manager

    async def approve_tool_execution(
        self,
        session_id: str,
        user_id: str,
        message_id: str,
        tool_index: int,
        approved: bool,
    ) -> Dict[str, Any]:
        """Approve or reject a pending tool execution.

        When approved, calls `tool.execute_confirmed()` and stores the confirmed
        result in the message metadata.  When rejected, simply marks the execution
        as rejected without calling the tool.

        Args:
            session_id: The session that owns the message
            user_id: The requesting user (ownership verification)
            message_id: ID of the assistant message containing the tool execution
            tool_index: Index into the message's tool_executions list
            approved: True to approve and run execute_confirmed, False to reject

        Returns:
            Dict with a "result" key containing the confirmed ToolResult fields

        Raises:
            SessionNotFoundException: If session not found
            AccessDeniedException: If user doesn't own the session
            MessageCreationFailedException: If message/tool state is invalid
        """
        session = self._m._get_session_or_raise(session_id)
        self._m._verify_ownership(session, user_id)

        message = self._m.chat_repository.get_message(message_id)
        if not message:
            raise MessageCreationFailedException(f"Message {message_id} not found")

        metadata = message.metadata or {}
        tool_executions = metadata.get("tool_executions", [])

        if tool_index < 0 or tool_index >= len(tool_executions):
            raise MessageCreationFailedException(
                f"Tool index {tool_index} out of range (message has {len(tool_executions)} tool executions)"
            )

        execution = tool_executions[tool_index]
        if not execution.get("pending_approval"):
            raise MessageCreationFailedException(
                f"Tool execution at index {tool_index} is not pending approval"
            )

        result_data: Dict[str, Any]
        context_metadata = metadata.get("context_metadata", {})

        if approved and self._m.tool_executor:
            tool_name = execution.get("tool_name", "")
            arguments = execution.get("arguments", {})

            context = ToolContext(
                user_id=user_id,
                mode_id=session.mode,
                session_metadata=context_metadata,
                segment_category_repository=self._m.segment_category_repository,
                saved_segment_repository=self._m.saved_segment_repository,
                segment_template_repository=self._m.segment_template_repository,
                model_index_manager=self._m.model_index_manager,
                preset_manager=self._m.preset_manager,
                phrasebook_category_repository=self._m.phrasebook_category_repository,
                phrasebook_value_repository=self._m.phrasebook_value_repository,
                prompt_database=self._m.prompt_database,
                generation_orchestrator=self._m.generation_orchestrator,
                llm_memory_repository=self._m.llm_memory_repository,
                prompt_enhancement_manager=self._m.prompt_enhancement_manager,
                media_index_manager=self._m.media_index_manager,
                settings_manager=self._m.settings_manager,
                collection_repository=self._m.collection_repository,
                tag_repository=self._m.tag_repository,
                plugin_registry=self._m.plugins,
                generation_history_manager=self._m.generation_history_manager,
                llm_id=session.llm_config_id,
            )

            result = await self._m.tool_executor.execute_tool_confirmed(
                tool_name, context, arguments
            )
            result_data = {
                "success": result.success,
                "data": result.data,
                "error": result.error,
            }
            execution["pending_approval"] = False
            execution["rejected"] = False
            execution["result"] = result_data
        else:
            execution["pending_approval"] = False
            execution["rejected"] = not approved
            result_data = execution.get("result", {})

        # Persist updated metadata
        metadata["tool_executions"] = tool_executions
        self._m.chat_repository.update_message_metadata(message_id, metadata)

        logger.debug(
            f"Tool execution {tool_index} on message {message_id} "
            f"{'approved' if approved else 'rejected'}"
        )

        # Continue the conversation: feed the outcome back and let the model say
        # what it did (or acknowledge the rejection) as a new assistant message.
        assistant_message = await self._present_outcome(
            session=session,
            tool_name=execution.get("tool_name", ""),
            arguments=execution.get("arguments", {}),
            approved=approved,
            result_data=result_data,
            form_state=context_metadata.get("form_state"),
        )

        return {
            "result": result_data,
            "assistant_message": assistant_message.model_dump() if assistant_message else None,
        }

    async def _present_outcome(
        self,
        session: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        approved: bool,
        result_data: Dict[str, Any],
        form_state: Optional[Dict[str, Any]] = None,
    ):
        """Run a presentation completion and persist its assistant message.

        Seeds the model with the paused turn's tool call and its now-resolved
        outcome, then asks it (tool-free) to narrate the result to the user. A
        best-effort continuation: if anything here fails the approval itself has
        already been persisted, so we log and return None rather than surface an
        error to the user.
        """
        if not self._m.tool_executor:
            return None
        try:
            system_prompt, _allowed_tools, mode = (
                self._m._context.resolve_session_prompt_and_tools(session, form_state=form_state)
            )
            presentation_prompt = self._build_presentation_system_prompt(system_prompt or "")

            # Trailing empty-content assistant turns are the paused tool-call
            # turn(s), which carry no text — drop them so the model isn't fed a
            # bogus blank assistant message before the reconstructed tool call.
            history = self._m.chat_repository.get_conversation_history(session.id)
            while history and history[-1].get("role") == "assistant" and not (
                history[-1].get("content") or ""
            ).strip():
                history.pop()

            call_id = "approval_call_0"
            history.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    # Canonical object arguments; the client normalizes to its wire
                    # shape at the request boundary (see clients.tool_call_shape).
                    "function": {"name": tool_name, "arguments": arguments or {}},
                }],
            })
            history.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": tool_name,
                "content": self._outcome_tool_message(approved, result_data),
            })

            response = await self._m.tool_executor.present_tool_outcome(
                messages=history,
                llm_id=session.llm_config_id,
                system_message=presentation_prompt,
                mode=mode.id if mode else None,
                llm_options=(mode.llm_options or None) if mode else None,
            )

            cleaned_content, parsed_content = self._m.response_processor.process(
                response.content if response is not None else "", mode=session.mode
            )
            if not (cleaned_content or "").strip():
                # The model (all retries) produced nothing narratable — an
                # approved/denied tool action must never surface as a blank
                # assistant turn, so fall back to deterministic narration
                # derived from the tool execution itself.
                cleaned_content = self._fallback_narration(tool_name, approved, result_data)
                parsed_content = None

            return self._m.chat_repository.add_message(
                session_id=session.id,
                role="assistant",
                content=cleaned_content,
                parsed_content=parsed_content,
                metadata={
                    "model": getattr(response, "model", None) if response else None,
                    "tokens_used": getattr(response, "tokens_used", None) if response else None,
                    "prompt_tokens": getattr(response, "prompt_tokens", None) if response else None,
                    "completion_tokens": getattr(response, "completion_tokens", None) if response else None,
                },
            )
        except Exception:
            logger.exception("Failed to continue conversation after tool approval")
            return None

    @staticmethod
    def _build_presentation_system_prompt(base_prompt: str) -> str:
        """Strip tool-calling instructions from a mode/custom prompt and pin
        the turn to plain-text narration.

        Best-effort strip of the builtin "## Tools" section (name, hints and
        call-me instructions all live there — see `_TOOL_SECTION_RE`), then an
        unconditional directive appended on top so a prompt that still names
        tools elsewhere (e.g. an admin-authored custom system_message) is
        overridden rather than relied upon to already be clean.
        """
        stripped = _TOOL_SECTION_RE.sub("", base_prompt or "").strip()
        return f"{stripped}{_PRESENTATION_ONLY_DIRECTIVE}".strip()

    @staticmethod
    def _fallback_narration(tool_name: str, approved: bool, result_data: Dict[str, Any]) -> str:
        """Deterministic outcome narration used when the LLM presentation
        turn produces no usable text (every retry empty, or the completion
        itself failed). Never returns an empty string.
        """
        label = (tool_name or "").replace("_", " ").strip() or "the requested action"

        if not approved:
            return f"I did not run {label} — you denied it, so nothing changed."

        if not result_data.get("success"):
            error = result_data.get("error") or "an unknown error occurred"
            return f"{label.capitalize()} failed: {error}"

        parsed: Optional[Dict[str, Any]] = None
        data = result_data.get("data")
        if isinstance(data, str) and data.strip():
            try:
                loaded = json.loads(data)
                if isinstance(loaded, dict):
                    parsed = loaded
            except (json.JSONDecodeError, TypeError):
                parsed = None

        if tool_name == "update_phrasebook_values" and parsed is not None:
            count = parsed.get("updated_count")
            if isinstance(count, int):
                noun = "value" if count == 1 else "values"
                return f"Done: updated {count} phrasebook {noun}."

        if tool_name == "create_phrasebook_values" and parsed is not None:
            count = parsed.get("created_count")
            if isinstance(count, int):
                noun = "value" if count == 1 else "values"
                return f"Done: created {count} phrasebook {noun}."

        if tool_name == "remove_phrasebook_values" and parsed is not None:
            count = parsed.get("deleted_count")
            if isinstance(count, int):
                noun = "value" if count == 1 else "values"
                return f"Done: removed {count} phrasebook {noun}."

        if tool_name == "create_phrasebook_category" and parsed is not None:
            category = parsed.get("category") or {}
            name = category.get("path") or category.get("name")
            if name:
                return f"Done: created the '{name}' phrasebook category."

        return f"Done: {label} completed."

    @staticmethod
    def _outcome_tool_message(approved: bool, result_data: Dict[str, Any]) -> str:
        """The tool-result content fed back to the model to narrate."""
        if not approved:
            return (
                "The user REJECTED this action, so it was NOT applied and nothing "
                "changed. Briefly acknowledge that you did not make the change."
            )
        if result_data.get("success"):
            return result_data.get("data") or "The action was applied successfully."
        return f"Error: {result_data.get('error') or 'the action could not be applied.'}"
