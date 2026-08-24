import * as adminApi from '$lib/services/admin-api';
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
