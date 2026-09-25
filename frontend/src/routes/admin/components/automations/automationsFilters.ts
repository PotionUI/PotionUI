import type { SortOption } from '$lib/components/library/librarySection';
import type { Automation } from '$lib/types/automations';

export type AutomationSortBy = 'created_at' | 'name';

export interface AutomationsFilters {
	q: string;
	sortBy: AutomationSortBy;
}

export const DEFAULT_AUTOMATIONS_FILTERS: AutomationsFilters = {
	q: '',
	sortBy: 'created_at'
};

export const AUTOMATIONS_SORT_OPTIONS: readonly SortOption<AutomationSortBy>[] = [
	{ value: 'created_at', label: 'Newest' },
	{ value: 'name', label: 'Name A–Z' }
];

export function applyAutomationsFilters(
	automations: readonly Automation[],
	filters: AutomationsFilters
): Automation[] {
	const query = filters.q.trim().toLowerCase();
	const rows = automations.filter((automation) => {
		if (!query) return true;
		return (
			automation.name.toLowerCase().includes(query) ||
			(automation.description ?? '').toLowerCase().includes(query)
		);
	});
	if (filters.sortBy === 'name') {
		return rows.sort((a, b) => a.name.localeCompare(b.name));
	}
	return rows.sort((a, b) => b.created_at.localeCompare(a.created_at));
}
