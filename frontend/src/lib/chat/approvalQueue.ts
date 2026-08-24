import type { UnifiedChatMessageData, ToolExecution } from '$lib/types/chat';

/**
 * One pending tool approval, anchored back to the message and execution
 * index the resolve API call needs (`message_id` + `tool_index`).
 */
export interface ApprovalQueueEntry {
	messageId: string;
	messageTimestamp: number | undefined;
	index: number;
	execution: ToolExecution;
}

/**
 * All still-pending tool approvals across a conversation, in message order
 * then execution order — the order the dock resolves them in. A message
 * without a persisted id can't be approved yet (the approve API call needs
 * `message_id`), so it's skipped rather than surfaced as unresolvable.
 */
export function deriveApprovalQueue(messages: UnifiedChatMessageData[]): ApprovalQueueEntry[] {
	const queue: ApprovalQueueEntry[] = [];
	for (const message of messages) {
		if (!message.id || message.role !== 'assistant') continue;
		const executions = message.tool_executions || [];
		executions.forEach((execution, index) => {
			if (execution.pending_approval && !execution.rejected) {
				queue.push({
					messageId: message.id!,
					messageTimestamp: message.timestamp,
					index,
					execution
				});
			}
		});
	}
	return queue;
}
