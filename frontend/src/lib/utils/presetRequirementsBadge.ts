import type { PresetRequirementsSummary } from '$lib/types/api';

export interface RequirementsBadgeInfo {
	variant: 'success' | 'danger' | 'warning';
	label: string;
}

/** The preset list row's requirements indicator, from the preset's last-evaluated
 * `requirements_summary` (`GET /api/presets/{id}/requirements` - never computed by the
 * list/detail endpoints themselves). `null` when it has never been checked - the row
 * shows nothing rather than implying "no requirements". Missing beats unknown beats
 * all-satisfied, so a preset with both a missing and an unknown requirement reads as
 * "missing" first (the harder blocker). */
export function presetRequirementsBadge(
	summary: PresetRequirementsSummary | null | undefined
): RequirementsBadgeInfo | null {
	if (!summary) return null;
	if (summary.missing > 0) return { variant: 'danger', label: `${summary.missing} missing` };
	if (summary.unknown > 0) return { variant: 'warning', label: `${summary.unknown} unknown` };
	const total = summary.ok + summary.missing + summary.unknown;
	return { variant: 'success', label: `${summary.ok}/${total}` };
}
