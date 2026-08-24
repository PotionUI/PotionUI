// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from 'vitest';

const { approveToolExecution } = vi.hoisted(() => ({ approveToolExecution: vi.fn() }));
vi.mock('$lib/services/api/index', () => ({
	api: { approveToolExecution }
}));

import { resolveApproval } from './approvalResolve';
import type { ToolExecution } from '$lib/types/chat';

function execution(): ToolExecution {
	return {
		tool_name: 'create_phrasebook_category',
		arguments: { path: 'camera.angles' },
		result: { success: false, data: '' },
		duration_ms: 0,
		pending_approval: true
	};
}

describe('resolveApproval', () => {
	beforeEach(() => {
		approveToolExecution.mockReset();
		vi.spyOn(window, 'dispatchEvent');
	});

	it('marks the execution resolved and returns the continuation message on approve', async () => {
		approveToolExecution.mockResolvedValue({
			success: true,
			data: {
				result: { success: true, data: '{"action":"ok"}' },
				assistant_message: { id: 'am1', content: 'Done.' }
			}
		});

		const resolution = await resolveApproval('s1', 'm1', 0, execution(), true);

		expect(approveToolExecution).toHaveBeenCalledWith('s1', {
			message_id: 'm1',
			tool_index: 0,
			approved: true
		});
		expect(resolution.updatedExecution.pending_approval).toBe(false);
		expect(resolution.updatedExecution.rejected).toBe(false);
		expect(resolution.updatedExecution.result.data).toBe('{"action":"ok"}');
		expect(resolution.assistantMessage).toEqual({ id: 'am1', content: 'Done.' });
		expect(window.dispatchEvent).toHaveBeenCalledWith(
			expect.objectContaining({ type: 'potionui:tool-approved' })
		);
	});

	it('marks the execution rejected without firing the approved event on deny', async () => {
		approveToolExecution.mockResolvedValue({
			success: true,
			data: { result: null, assistant_message: { id: 'am2', content: 'Declined.' } }
		});

		const resolution = await resolveApproval('s1', 'm1', 0, execution(), false);

		expect(resolution.updatedExecution.pending_approval).toBe(false);
		expect(resolution.updatedExecution.rejected).toBe(true);
		expect(window.dispatchEvent).not.toHaveBeenCalled();
	});

	it('throws when the API call fails, instead of silently resolving', async () => {
		approveToolExecution.mockResolvedValue({ success: false, error: 'boom' });
		await expect(resolveApproval('s1', 'm1', 0, execution(), true)).rejects.toThrow('boom');
	});
});
