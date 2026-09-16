import * as adminApi from '$lib/services/admin-api';
import type { APIResponse } from '$lib/types/api';
import type { AssignmentAdapter, AssignmentState } from './types';

function responseError(response: { message?: string } | null | undefined, fallback: string) {
	return response?.message || fallback;
}

export function createModelAssignmentAdapter(modelId: string): AssignmentAdapter {
	return {
		resourceLabel: 'model',

		async loadState(): Promise<AssignmentState> {
			const [assignmentsResponse, groupsResponse] = await Promise.all([
				adminApi.getModelAssignments(modelId),
				adminApi.getUserGroups()
			]);
			if (!assignmentsResponse.success) {
				throw new Error(responseError(assignmentsResponse, 'Could not load model assignments'));
			}
			if (!groupsResponse.success) {
				throw new Error(responseError(groupsResponse, 'Could not load user groups'));
			}

			const groups = groupsResponse.data || [];
			const groupResponses = await Promise.all(
				groups.map(async (group) => ({
					groupId: group.id,
					response: await adminApi.getGroupModels(group.id)
				}))
			);
			const failed = groupResponses.find(({ response }) => !response.success);
			if (failed) {
				throw new Error(responseError(failed.response, 'Could not load user group assignments'));
			}

			return {
				userIds: new Set((assignmentsResponse.data?.assignments || []).map((a) => a.user_id)),
				groupIds: new Set(
					groupResponses
						.filter(({ response }) =>
							(response.data || []).some((assignment) => assignment.model_id === modelId)
						)
						.map(({ groupId }) => groupId)
				)
			};
		},

		assignUser: (userId) => adminApi.assignModelToUser(userId, modelId),
		unassignUser: (userId) => adminApi.unassignModelFromUser(userId, modelId),
		assignGroup: (groupId) => adminApi.assignModelsToGroup(groupId, [modelId]),
		unassignGroup: (groupId) => adminApi.unassignModelFromGroup(groupId, modelId)
	};
}

export function createBulkModelAssignmentAdapter(
	modelIds: string[],
	onProgress: (done: number, total: number) => void
): AssignmentAdapter {
	async function loopAssign(call: (modelId: string) => Promise<APIResponse>): Promise<APIResponse> {
		onProgress(0, modelIds.length);
		for (let i = 0; i < modelIds.length; i++) {
			const response = await call(modelIds[i]);
			if (!response.success) return response;
			onProgress(i + 1, modelIds.length);
		}
		return { success: true };
	}

	return {
		resourceLabel: 'model',

		async loadState(): Promise<AssignmentState> {
			return { userIds: new Set(), groupIds: new Set() };
		},

		assignUser: (userId) => loopAssign((modelId) => adminApi.assignModelToUser(userId, modelId)),
		unassignUser: (userId) => loopAssign((modelId) => adminApi.unassignModelFromUser(userId, modelId)),
		assignGroup: (groupId) => loopAssign((modelId) => adminApi.assignModelsToGroup(groupId, [modelId])),
		unassignGroup: (groupId) => loopAssign((modelId) => adminApi.unassignModelFromGroup(groupId, modelId))
	};
}
