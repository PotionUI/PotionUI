import { api } from '$lib/services/api/index';
import type { ToolExecution } from '$lib/types/chat';

export interface ApprovalResolution {
	index: number;
	approved: boolean;
	updatedExecution: ToolExecution;
	assistantMessage: any | null;
}

/**
 * Resolve one pending tool approval against the backend, returning the
 * updated execution and any continuation assistant message. Fires
 * `potionui:tool-approved` on approval — plugins and other panels listen
 * for it (e.g. to refresh state the approved tool changed).
 */
export async function resolveApproval(
	sessionId: string,
	messageId: string,
	index: number,
	execution: ToolExecution,
	approved: boolean
): Promise<ApprovalResolution> {
	const response = await api.approveToolExecution(sessionId, {
		message_id: messageId,
		tool_index: index,
		approved
	});

	if (!response.success || !response.data) {
		throw new Error(response.error || 'Failed to resolve approval');
	}

	const updatedExecution: ToolExecution = {
		...execution,
		pending_approval: false,
		rejected: !approved,
		result: approved && response.data.result ? response.data.result : execution.result
	};

	if (approved) {
		window.dispatchEvent(
			new CustomEvent('potionui:tool-approved', {
				detail: { tool_name: execution.tool_name, result: response.data.result }
			})
		);
	}

	return {
		index,
		approved,
		updatedExecution,
		assistantMessage: response.data.assistant_message ?? null
	};
}
