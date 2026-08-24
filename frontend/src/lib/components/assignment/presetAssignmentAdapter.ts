import * as adminApi from '$lib/services/admin-api';
import type { AssignmentAdapter, AssignmentState } from './types';

function responseError(response: { message?: string } | null | undefined, fallback: string) {
	return response?.message || fallback;
}

export function createPresetAssignmentAdapter(presetId: string): AssignmentAdapter {
	return {
		resourceLabel: 'preset',

		async loadState(): Promise<AssignmentState> {
			const [assignmentsResponse, groupsResponse] = await Promise.all([
				adminApi.getPresetAssignments(presetId),
				adminApi.getUserGroups()
			]);
			if (!assignmentsResponse.success) {
				throw new Error(responseError(assignmentsResponse, 'Could not load preset assignments'));
			}
			if (!groupsResponse.success) {
				throw new Error(responseError(groupsResponse, 'Could not load user groups'));
			}

			const groups = groupsResponse.data || [];
			const presetDbId = assignmentsResponse.data?.preset_db_id;
			if (groups.length && !presetDbId) {
				throw new Error('The installed preset relationship could not be resolved');
			}

			const groupResponses = await Promise.all(
				groups.map(async (group) => ({
					groupId: group.id,
					response: await adminApi.getGroupPresets(group.id)
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
							(response.data || []).some((assignment) => assignment.preset_id === presetDbId)
						)
						.map(({ groupId }) => groupId)
				)
			};
		},

		assignUser: (userId) => adminApi.assignPresetToUsers(presetId, [userId]),
		unassignUser: (userId) => adminApi.unassignPresetFromUser(presetId, userId),
		assignGroup: (groupId) => adminApi.assignPresetsToGroup(groupId, [presetId]),
		unassignGroup: (groupId) => adminApi.unassignPresetFromGroup(groupId, presetId)
	};
}
