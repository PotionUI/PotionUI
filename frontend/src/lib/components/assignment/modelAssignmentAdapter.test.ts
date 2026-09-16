import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/services/admin-api', () => ({
	assignModelToUser: vi.fn(),
	unassignModelFromUser: vi.fn(),
	assignModelsToGroup: vi.fn(),
	unassignModelFromGroup: vi.fn(),
	getModelAssignments: vi.fn(),
	getUserGroups: vi.fn(),
	getGroupModels: vi.fn()
}));

import * as adminApi from '$lib/services/admin-api';
import { createBulkModelAssignmentAdapter } from './modelAssignmentAdapter';

describe('createBulkModelAssignmentAdapter', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('starts every model unassigned regardless of prior state', async () => {
		const adapter = createBulkModelAssignmentAdapter(['m1', 'm2'], () => {});
		const state = await adapter.loadState();
		expect(state.userIds.size).toBe(0);
		expect(state.groupIds.size).toBe(0);
	});

	it('assigns the user to every selected model, reporting progress as it goes', async () => {
		(adminApi.assignModelToUser as any).mockResolvedValue({ success: true });
		const progress: Array<[number, number]> = [];

		const adapter = createBulkModelAssignmentAdapter(['m1', 'm2', 'm3'], (done, total) =>
			progress.push([done, total])
		);
		const response = await adapter.assignUser('user-1');

		expect(response.success).toBe(true);
		expect(adminApi.assignModelToUser).toHaveBeenCalledTimes(3);
		expect(adminApi.assignModelToUser).toHaveBeenNthCalledWith(1, 'user-1', 'm1');
		expect(adminApi.assignModelToUser).toHaveBeenNthCalledWith(2, 'user-1', 'm2');
		expect(adminApi.assignModelToUser).toHaveBeenNthCalledWith(3, 'user-1', 'm3');
		expect(progress).toEqual([[0, 3], [1, 3], [2, 3], [3, 3]]);
	});

	it('assigns a group to every selected model one at a time', async () => {
		(adminApi.assignModelsToGroup as any).mockResolvedValue({ success: true });
		const adapter = createBulkModelAssignmentAdapter(['m1', 'm2'], () => {});
		await adapter.assignGroup('group-1');
		expect(adminApi.assignModelsToGroup).toHaveBeenCalledTimes(2);
		expect(adminApi.assignModelsToGroup).toHaveBeenNthCalledWith(1, 'group-1', ['m1']);
		expect(adminApi.assignModelsToGroup).toHaveBeenNthCalledWith(2, 'group-1', ['m2']);
	});

	it('stops on the first failure and reports it without touching the rest', async () => {
		(adminApi.assignModelToUser as any)
			.mockResolvedValueOnce({ success: true })
			.mockResolvedValueOnce({ success: false, message: 'boom' });

		const adapter = createBulkModelAssignmentAdapter(['m1', 'm2', 'm3'], () => {});
		const response = await adapter.assignUser('user-1');

		expect(response.success).toBe(false);
		expect(response.message).toBe('boom');
		expect(adminApi.assignModelToUser).toHaveBeenCalledTimes(2);
	});
});
