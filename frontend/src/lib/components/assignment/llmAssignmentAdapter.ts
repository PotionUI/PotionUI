import * as adminApi from '$lib/services/admin-api';
import type { AssignmentAdapter, AssignmentState } from './types';

function responseError(response: { message?: string } | null | undefined, fallback: string) {
	return response?.message || fallback;
}

export function createLLMAssignmentAdapter(llmConfigId: string): AssignmentAdapter {
	return {
		resourceLabel: 'LLM configuration',

		async loadState(): Promise<AssignmentState> {
			const [assignmentsResponse, groupsResponse] = await Promise.all([
				adminApi.getLLMConfigAssignments(llmConfigId),
				adminApi.getUserGroups()
			]);
			if (!assignmentsResponse.success) {
				throw new Error(responseError(assignmentsResponse, 'Could not load LLM assignments'));
			}
			if (!groupsResponse.success) {
				throw new Error(responseError(groupsResponse, 'Could not load user groups'));
			}

			const groups = groupsResponse.data || [];
			const groupResponses = await Promise.all(
				groups.map(async (group) => ({
					groupId: group.id,
					response: await adminApi.getGroupLLMs(group.id)
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
							(response.data || []).some((assignment) => assignment.llm_config_id === llmConfigId)
						)
						.map(({ groupId }) => groupId)
				)
			};
		},

		assignUser: (userId) => adminApi.assignLLMToUser(userId, llmConfigId),
		unassignUser: (userId) => adminApi.unassignLLMFromUser(userId, llmConfigId),
		assignGroup: (groupId) => adminApi.assignLLMsToGroup(groupId, [llmConfigId]),
		unassignGroup: (groupId) => adminApi.unassignLLMFromGroup(groupId, llmConfigId)
	};
}
