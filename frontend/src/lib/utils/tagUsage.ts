import type { TagUsageRef } from '$lib/types/api';

/** Formats the 409 `used_by` payload from `DELETE /api/tags/{id}` into a specific,
 *  actionable message rather than a generic "failed to delete" toast. */
export function formatTagUsageError(tagName: string, usedBy: TagUsageRef[] | null | undefined): string {
	if (!usedBy || usedBy.length === 0) {
		return `"${tagName}" is still in use and can't be deleted.`;
	}
	const list = usedBy.map((ref) => `${ref.preset_name} (${ref.key})`).join(', ');
	return `"${tagName}" is used by ${list} and can't be deleted.`;
}
