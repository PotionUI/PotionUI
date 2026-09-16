import type { AssignmentSummary, AssignmentSummaryEntry } from '$lib/services/admin-api';

export function isModelUnassigned(entry: AssignmentSummaryEntry | undefined): boolean {
	return !(entry?.assignment_count || 0) && !(entry?.group_count || 0);
}

export function applyAssignmentSummaryChange(
	summary: AssignmentSummary,
	resourceId: string,
	change: { userCount: number; groupCount: number }
): AssignmentSummary {
	return {
		...summary,
		[resourceId]: { assignment_count: change.userCount, group_count: change.groupCount }
	};
}
