import type { AssignmentSummary } from '$lib/services/admin-api';

export function assignmentCountLabel(summary: AssignmentSummary, configId: string): string {
	const entry = summary[configId];
	const users = entry?.assignment_count || 0;
	const groups = entry?.group_count || 0;
	if (!users && !groups) return 'Unassigned';
	const parts: string[] = [];
	if (users) parts.push(`${users} user${users === 1 ? '' : 's'}`);
	if (groups) parts.push(`${groups} group${groups === 1 ? '' : 's'}`);
	return parts.join(' · ');
}
