import type { PresetRequirementsSummary } from '$lib/types/api';

export interface RequirementsBadgeInfo {
	variant: 'success' | 'danger' | 'warning';
	label: string;
}

/** The preset list row's requirements indicator, from the preset's last-evaluated
 * `requirements_summary` (`GET /api/presets/{id}/requirements` - never computed by the
 * list/detail endpoints themselves). `null` when it has never been checked - the row
 * shows nothing rather than implying "no requirements". Priority: a hard miss (danger)
 * beats an unknown/optional-missing requirement (warning, both soft) beats
 * all-satisfied - `summary.missing` already excludes `optional: true` entries (they
 * count under `optional_missing` instead), so this never flags a preset danger-red
 * over a requirement its own author said was optional. */
export function presetRequirementsBadge(
	summary: PresetRequirementsSummary | null | undefined
): RequirementsBadgeInfo | null {
	if (!summary) return null;
	if (summary.missing > 0) return { variant: 'danger', label: `${summary.missing} missing` };

	const unchecked = summary.unknown + summary.optional_missing;
	if (unchecked > 0) {
		if (summary.optional_missing === 0) return { variant: 'warning', label: `${summary.unknown} unknown` };
		if (summary.unknown === 0) return { variant: 'warning', label: `${summary.optional_missing} optional` };
		return { variant: 'warning', label: `${unchecked} unchecked` };
	}

	const total = summary.ok + summary.missing + summary.unknown + summary.optional_missing;
	return { variant: 'success', label: `${summary.ok}/${total}` };
}
