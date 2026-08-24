import { describe, expect, it } from 'vitest';
import { deriveApprovalQueue } from './approvalQueue';
import type { UnifiedChatMessageData, ToolExecution } from '$lib/types/chat';

function execution(overrides: Partial<ToolExecution> = {}): ToolExecution {
	return {
		tool_name: 'update_form_settings',
		arguments: {},
		result: { success: false, data: '' },
		duration_ms: 0,
		...overrides
	};
}

function message(overrides: Partial<UnifiedChatMessageData> = {}): UnifiedChatMessageData {
	return {
		role: 'assistant',
		content: '',
		timestamp: 1000,
		...overrides
	};
}

describe('deriveApprovalQueue', () => {
	it('returns an empty queue when nothing is pending', () => {
		expect(deriveApprovalQueue([])).toEqual([]);
		expect(
			deriveApprovalQueue([message({ id: 'm1', tool_executions: [execution()] })])
		).toEqual([]);
	});

	it('orders entries by message order then execution order', () => {
		const queue = deriveApprovalQueue([
			message({
				id: 'm1',
				timestamp: 1000,
				tool_executions: [
					execution({ tool_name: 'a', pending_approval: true }),
					execution({ tool_name: 'b', pending_approval: true })
				]
			}),
			message({
				id: 'm2',
				timestamp: 2000,
				tool_executions: [execution({ tool_name: 'c', pending_approval: true })]
			})
		]);
		expect(queue.map((e) => e.execution.tool_name)).toEqual(['a', 'b', 'c']);
		expect(queue.map((e) => e.messageId)).toEqual(['m1', 'm1', 'm2']);
		expect(queue.map((e) => e.index)).toEqual([0, 1, 0]);
		expect(queue[2].messageTimestamp).toBe(2000);
	});

	it('excludes rejected and already-resolved executions', () => {
		const queue = deriveApprovalQueue([
			message({
				id: 'm1',
				tool_executions: [
					execution({ tool_name: 'rejected', pending_approval: true, rejected: true }),
					execution({ tool_name: 'resolved', pending_approval: false }),
					execution({ tool_name: 'pending', pending_approval: true })
				]
			})
		]);
		expect(queue.map((e) => e.execution.tool_name)).toEqual(['pending']);
	});

	it('excludes a message that has no persisted id yet', () => {
		const queue = deriveApprovalQueue([
			message({ tool_executions: [execution({ pending_approval: true })] })
		]);
		expect(queue).toEqual([]);
	});

	it('ignores user messages even if they carried tool_executions', () => {
		const queue = deriveApprovalQueue([
			message({
				id: 'm1',
				role: 'user',
				tool_executions: [execution({ pending_approval: true })]
			})
		]);
		expect(queue).toEqual([]);
	});
});
